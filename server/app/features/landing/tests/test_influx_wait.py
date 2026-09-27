"""`GET /landing` 의 Influx 3초 규칙 (스펙 022 §3.2·§4).

trail·events 는 갱신이 대기 상한 안에 안 끝나면 직전 값(처음이면 null)을 싣고, 조회는 뒤에서 끝까지 돌아
끝나는 순간 캐시를 채운다. 실제 3초를 기다리지 않게 상한을 0.2초로 줄이고, 느린 Influx 는 fake 의
`gate` 로 흉내 낸다. 응답 뒤에도 도는 조회를 보려고 한 루프에서 여러 요청을 보낸다(async_client).
"""

import threading
import time

from app.features.landing.tests.helpers import (
    NOW,
    Clock,
    FakeBus,
    FakeInflux,
    async_client,
    candle,
    event,
    make_app,
    row,
    settle,
    table,
)

WAIT = 0.2  # 3초 대신
ROWS = [row("VERONA", fwd=2.26, rev=-2.3), row("HNT", fwd=1.24, rev=-1.3)]


def _seeded() -> FakeInflux:
    influx = FakeInflux()
    for ts in (NOW - 120, NOW - 60):
        influx.candles.append(candle(ts, base="VERONA", fwd_c=2.4))
        influx.candles.append(candle(ts, base="HNT", fwd_c=1.3))
    influx.events.append(event("CUDIS", NOW - 3_600, end_ts=NOW - 3_000))
    influx.gate = threading.Event()  # 닫힘 — 열 때까지 조회가 멈춘다
    return influx


async def _timed_get(client) -> tuple[float, dict]:  # noqa: ANN001 — httpx.AsyncClient
    t0 = time.perf_counter()
    resp = await client.get("/landing")
    assert resp.status_code == 200
    return time.perf_counter() - t0, resp.json()


async def test_slow_influx_answers_after_the_wait_with_null_first_and_live_intact() -> (
    None
):
    influx = _seeded()
    app = make_app(FakeBus(table(ROWS)), influx, influx_wait_sec=WAIT)
    async with async_client(app) as client:
        try:
            elapsed, body = await _timed_get(client)
        finally:
            influx.gate.set()
        await settle(influx, 2)
    assert WAIT <= elapsed < WAIT + 1.0  # 조회 끝까지가 아니라 상한만큼
    assert body["live"]["top"][0]["sym"] == "VERONA"  # live 는 이 규칙과 무관하다
    assert body["trail"] is None and body["events"] is None  # 처음이라 직전 값이 없다


async def test_slow_refresh_serves_the_previous_value_even_after_expiry() -> None:
    clock = Clock()
    influx = _seeded()
    influx.gate.set()
    app = make_app(FakeBus(table(ROWS)), influx, clock, influx_wait_sec=WAIT)
    async with async_client(app) as client:
        _, first = await _timed_get(client)
        assert first["trail"]["sym"] == "VERONA" and first["events"]["count"] == 1
        clock.advance(61)  # trail·events 둘 다 만료
        influx.gate.clear()  # 이번 갱신은 느리다
        try:
            elapsed, body = await _timed_get(client)
        finally:
            influx.gate.set()
        await settle(influx, 4)
    assert WAIT <= elapsed < WAIT + 1.0
    assert body["trail"] == first["trail"]
    assert body["events"] == first["events"]  # stop 도 예전 창 그대로 — 직전 값이다


async def test_query_finishing_in_background_fills_the_cache_and_ttl_starts_then() -> (
    None
):
    clock = Clock()
    influx = _seeded()
    app = make_app(FakeBus(table(ROWS)), influx, clock, influx_wait_sec=WAIT)
    async with async_client(app) as client:
        try:
            _, first = await _timed_get(client)
            assert first["events"] is None and first["trail"] is None
            clock.advance(50)  # 조회가 도는 동안 50초가 흐른다
        finally:
            influx.gate.set()
        await settle(influx, 2)
        clock.advance(50)  # 시작부터 100초, 끝난 뒤 50초 — TTL 은 끝난 순간부터 센다
        elapsed, body = await _timed_get(client)
    assert elapsed < WAIT  # 기다리지 않는다 — 뒤에서 채운 캐시를 준다
    assert body["events"]["count"] == 1 and body["trail"]["sym"] == "VERONA"
    assert body["events"]["stop"] == NOW  # 조회를 시작한 시각의 창
    assert len(influx.event_calls) == 1 and len(influx.candle_calls) == 1


async def test_requests_during_a_running_query_do_not_start_another() -> None:
    clock = Clock()
    influx = _seeded()
    app = make_app(FakeBus(table(ROWS)), influx, clock, influx_wait_sec=WAIT)
    async with async_client(app) as client:
        try:
            for _ in range(3):
                elapsed, body = await _timed_get(client)
                assert (
                    WAIT <= elapsed < WAIT + 1.0
                )  # 같은 조회를 같은 상한으로 기다린다
                assert body["events"] is None and body["trail"] is None
                clock.advance(1)
            assert len(influx.event_calls) == 1 and len(influx.candle_calls) == 1
        finally:
            influx.gate.set()
        await settle(influx, 2)


async def test_slow_trail_for_a_new_route_is_null_not_the_old_routes_trail() -> None:
    clock = Clock()
    influx = _seeded()
    influx.gate.set()
    bus = FakeBus(table(ROWS))
    app = make_app(bus, influx, clock, influx_wait_sec=WAIT)
    async with async_client(app) as client:
        _, first = await _timed_get(client)
        assert first["trail"]["sym"] == "VERONA"
        # 1위가 HNT 로 바뀌고, HNT 의 봉 조회는 느리다
        bus.text = table([row("VERONA", fwd=0.5, rev=-0.6), ROWS[1]])
        clock.advance(6)
        influx.gate.clear()
        try:
            elapsed, body = await _timed_get(client)
        finally:
            influx.gate.set()
        await settle(influx, 3)
        _, after = await _timed_get(client)
    assert WAIT <= elapsed < WAIT + 1.0
    assert body["live"]["top"][0]["sym"] == "HNT"
    # 직전 값은 같은 경로의 것만 — VERONA 의 추이를 HNT 카드에 싣지 않는다
    assert body["trail"] is None
    assert after["trail"]["sym"] == "HNT"  # 뒤에서 끝난 조회가 채웠다
