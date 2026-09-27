"""`GET /landing` 의 서빙 계약 — 부분별 null, 캐시·한 번만 갱신, HTTP 모양, 두 역할 (스펙 022 §3.2·§4)."""

import asyncio

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.redis_bus import RedisBus
from app.features.landing.service import LandingService
from app.features.landing.tests.helpers import (
    NOW,
    Clock,
    FakeBus,
    FakeInflux,
    candle,
    event,
    make_app,
    make_client,
    row,
    table,
)
from app.main import create_app

ROWS = [row("VERONA", fwd=2.26, rev=-2.3), row("HNT", fwd=1.24, rev=-1.3)]


def _influx() -> FakeInflux:
    influx = FakeInflux()
    for ts in (NOW - 120, NOW - 60):
        influx.candles.append(candle(ts, base="VERONA", fwd_c=2.4))
        influx.candles.append(candle(ts, base="HNT", fwd_c=1.3))
    influx.events.append(event("CUDIS", NOW - 3_600, max_percent=5.0))
    return influx


# ---- 부분별 null: 한 저장소가 안 되면 그 부분만 ----


def _redis_bus(server: fakeredis.FakeServer) -> RedisBus:
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server))


def test_redis_down_nulls_live_and_trail_but_events_still_served() -> None:
    server = fakeredis.FakeServer()
    server.connected = False  # 연결 실패 — 실제 RedisBus 가 예외를 낸다
    resp = make_client(_redis_bus(server), _influx()).get("/landing")
    assert resp.status_code == 200
    body = resp.json()
    assert body["live"] is None and body["trail"] is None
    assert body["events"]["count"] == 1


def test_missing_key_nulls_live_and_trail_and_never_writes_want() -> None:
    server = fakeredis.FakeServer()
    body = make_client(_redis_bus(server), _influx()).get("/landing").json()
    assert body["live"] is None and body["trail"] is None
    assert body["events"]["count"] == 1
    # 랜딩은 읽기만 한다 — spreads:want 를 쓰면 방문자가 수집을 깨우는 셈이다
    assert fakeredis.FakeRedis(server=server).exists("spreads:want") == 0


def test_real_redis_key_is_read_verbatim_into_live() -> None:
    server = fakeredis.FakeServer()
    fakeredis.FakeRedis(server=server).set("spreads:latest", table(ROWS), ex=10)
    body = make_client(_redis_bus(server), None).get("/landing").json()
    assert [t["sym"] for t in body["live"]["top"]] == ["VERONA", "HNT"]


@pytest.mark.parametrize(
    "text", ["not json", "[1, 2]", '{"rate": 1360.0}', '{"rows": {"a": 1}}']
)
def test_value_not_json_or_rows_not_array_nulls_live(text: str) -> None:
    body = make_client(FakeBus(text), _influx()).get("/landing").json()
    assert body["live"] is None and body["trail"] is None
    assert body["events"] is not None


def test_no_influx_nulls_events_and_trail_but_live_served() -> None:
    body = make_client(FakeBus(table(ROWS)), None).get("/landing").json()
    assert body["live"]["top"][0]["sym"] == "VERONA"
    assert body["trail"] is None and body["events"] is None


def test_influx_failure_nulls_events_and_trail_but_live_served() -> None:
    influx = _influx()
    influx.fail = True
    body = make_client(FakeBus(table(ROWS)), influx).get("/landing").json()
    assert body["live"]["top"][0]["sym"] == "VERONA"
    assert body["trail"] is None and body["events"] is None


def test_both_down_is_still_200_with_all_three_null() -> None:
    bus = FakeBus(table(ROWS))
    bus.fail = True
    influx = _influx()
    influx.fail = True
    resp = make_client(bus, influx).get("/landing")
    assert resp.status_code == 200
    assert resp.json() == {
        "servedAt": NOW * 1000,
        "live": None,
        "trail": None,
        "events": None,
    }


# ---- 캐시: live 5초, events·trail 60초, 경로가 바뀌면 trail 새로, null 도 캐시 ----


def test_live_is_cached_for_five_seconds() -> None:
    clock = Clock()
    bus = FakeBus(table(ROWS))
    client = make_client(bus, None, clock)
    client.get("/landing")
    clock.advance(4.9)
    client.get("/landing")
    assert bus.reads == 1
    clock.advance(0.1)  # 5초 — 만료
    client.get("/landing")
    assert bus.reads == 2


def test_events_query_is_not_repeated_within_sixty_seconds() -> None:
    clock = Clock()
    influx = _influx()
    client = make_client(FakeBus(None), influx, clock)
    client.get("/landing")
    clock.advance(59)
    client.get("/landing")
    assert len(influx.event_calls) == 1
    clock.advance(1)
    body = client.get("/landing").json()
    assert len(influx.event_calls) == 2
    # 새로 읽은 창은 그 시각 기준이다
    assert body["events"]["stop"] == NOW + 60


def test_null_results_are_cached_too_so_outages_are_not_hammered() -> None:
    clock = Clock()
    bus = FakeBus(table(ROWS))
    bus.fail = True
    influx = _influx()
    influx.fail = True
    client = make_client(bus, influx, clock)
    client.get("/landing")
    clock.advance(4)
    client.get("/landing")
    assert bus.reads == 1  # 5초 안엔 불달인 Redis 를 다시 두드리지 않는다
    assert len(influx.event_calls) == 1
    bus.fail = False
    clock.advance(1)
    body = client.get("/landing").json()
    assert body["live"] is not None  # 만료 뒤 복구
    assert body["events"] is None  # events 는 60초 동안 null 그대로
    assert len(influx.event_calls) == 1


def test_trail_is_cached_per_route_and_reread_when_route_changes() -> None:
    clock = Clock()
    bus = FakeBus(table(ROWS))
    influx = _influx()
    client = make_client(bus, influx, clock)
    first = client.get("/landing").json()
    assert first["trail"]["sym"] == "VERONA"
    clock.advance(6)  # live 만 만료 — 같은 경로면 trail 은 그대로
    client.get("/landing")
    assert bus.reads == 2 and len(influx.candle_calls) == 1
    # 1위가 바뀌면 60초 전이라도 trail 을 새로 읽는다
    bus.text = table([row("VERONA", fwd=0.5, rev=-0.6), row("HNT", fwd=1.24, rev=-1.3)])
    clock.advance(6)
    body = client.get("/landing").json()
    assert body["trail"]["sym"] == "HNT"
    assert len(influx.candle_calls) == 2
    assert influx.candle_calls[-1]["base"] == "HNT"


async def test_ten_concurrent_requests_read_redis_and_influx_once() -> None:
    bus = FakeBus(table(ROWS), delay=0.05)  # 첫 읽기가 도는 동안 나머지가 몰린다
    influx = _influx()
    app = make_app(bus, influx)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        responses = await asyncio.gather(*(client.get("/landing") for _ in range(10)))
    assert all(r.status_code == 200 for r in responses)
    assert bus.reads == 1
    assert len(influx.event_calls) == 1
    assert len(influx.candle_calls) == 1
    assert len({r.json()["live"]["top"][0]["sym"] for r in responses}) == 1


# ---- HTTP 모양 ----


def test_response_is_200_camel_case_no_store_and_ignores_query() -> None:
    resp = make_client(FakeBus(table(ROWS)), _influx()).get("/landing?foo=1")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert list(body) == ["servedAt", "live", "trail", "events"]
    assert body["servedAt"] == NOW * 1000
    assert list(body["live"]) == [
        "dataReceivedAt",
        "rate",
        "coins",
        "pairs",
        "over1",
        "over1Movable",
        "top",
    ]
    assert list(body["live"]["top"][0]) == [
        "sym",
        "dom",
        "fx",
        "dir",
        "pct",
        "slip",
        "krw",
        "usd",
        "netDom",
        "netFx",
    ]
    assert list(body["trail"]) == ["sym", "dom", "fx", "dir", "points"]
    assert list(body["events"]) == [
        "start",
        "stop",
        "count",
        "kimp",
        "reverse",
        "open",
        "top",
    ]


@pytest.mark.parametrize("role", ["api", "collector"])
def test_both_roles_serve_landing(role: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROLE", role)
    monkeypatch.setenv("INFLUX_TOKEN", "")  # lifespan 없이 — 저장소 자리가 비어 있다
    get_settings.cache_clear()
    try:
        app = create_app()
        assert isinstance(app.state.landing, LandingService)
        resp = TestClient(app).get("/landing")
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 200
    body = resp.json()
    assert (body["live"], body["trail"], body["events"]) == (None, None, None)
