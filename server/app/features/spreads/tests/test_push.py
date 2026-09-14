"""017 §4 — 수집 쪽 게시와 diff 규칙 (fakeredis, 네트워크 없음). 허브는 test_hub.py, 계약은 test_ws.py."""

import json
import logging
from datetime import UTC, datetime

import fakeredis
import httpx
import pytest

from app.core.live_store import LiveStore
from app.core.models import Tick
from app.core.redis_bus import LATEST_KEY, RedisBus
from app.core.ticks import TickLoop
from app.features.spreads.hub import make_delta
from app.features.spreads.push import SpreadsPublisher
from app.features.spreads.tests.helpers import make_client, make_row, seed_rows


def make_bus() -> tuple[RedisBus, fakeredis.FakeServer]:
    server = fakeredis.FakeServer()
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server)), server


def seed(store: LiveStore, *, now: datetime | None = None) -> None:
    now = now if now is not None else datetime.now(UTC)
    seed_rows(
        store,
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
            )
        ],
        now,
    )
    seed_rows(
        store,
        [
            make_row(
                "binance",
                "BTC",
                price=71_480.0,
                bids=[[71_450.0, 1.5]],
                asks=[[71_500.0, 2.0]],
            )
        ],
        now,
    )
    store.set_rate("upbit", 1400.0, 1390.0, now)


def make_loop(store: LiveStore, publisher: SpreadsPublisher, handoff) -> TickLoop:  # noqa: ANN001
    return TickLoop(
        store=store,
        streams=[],
        client=httpx.AsyncClient(),
        handoff=handoff,
        spreads=publisher,
    )


# --- 수집: want 없으면 안 만들고, 있으면 GET /spreads 와 같은 JSON 을 채널·키에 ---


async def test_publisher_builds_nothing_without_want_and_same_json_as_get_with_want() -> (
    None
):
    bus, _ = make_bus()
    store = LiveStore()
    seed(store)
    publisher = SpreadsPublisher(store=store, bus=bus)

    await publisher.refresh_wanted()  # 키 없음 → 원함 아님
    loop = make_loop(store, publisher, lambda tick: None)
    loop.tick(1_787_000_000)
    assert publisher.pending == 0 and not publisher.wanted

    await bus.want()
    await publisher.refresh_wanted()
    assert publisher.wanted
    loop.tick(1_787_000_001)
    assert publisher.pending == 1
    # 같은 틱 안의 GET /spreads 와 비교 — 시각 필드만 빼고 같다 (§4)
    http = make_client(store).get("/spreads").json()
    assert await publisher.drain() == 1
    published = json.loads(await bus.latest())
    assert published["notional"] == 1000.0 == http["notional"]
    for body in (published, http):
        body.pop("fetchedAt")
        for row in body["rows"]:
            row.pop("age")
    assert published == http
    assert (await bus._client.ttl(LATEST_KEY)) == 10


async def test_publisher_publishes_on_channel_and_only_when_wanted() -> None:
    bus, server = make_bus()
    listener = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    sub = await listener.subscribe()
    store = LiveStore()
    seed(store)
    publisher = SpreadsPublisher(store=store, bus=bus)
    publisher.set_wanted(True)
    publisher.observe(Tick(ts=1, rows=(), dw_failed=()))
    await publisher.drain()
    text = await sub.get(timeout=1.0)
    assert text is not None and json.loads(text)["rows"][0]["sym"] == "BTC"
    await sub.aclose()


async def test_redis_down_skips_publish_but_tick_handoff_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, server = make_bus()
    server.connected = False
    store = LiveStore()
    seed(store)
    publisher = SpreadsPublisher(store=store, bus=bus)
    publisher.set_wanted(True)
    handed: list[int] = []
    loop = make_loop(store, publisher, lambda tick: handed.append(tick.ts))
    caplog.set_level(logging.WARNING, logger="marketlens.spreads_push")
    loop.tick(1_787_000_000)
    loop.tick(1_787_000_001)
    assert handed == [1_787_000_000]  # 직전 틱 인계는 그대로
    assert await publisher.drain() == 0
    # 같은 원인은 60초에 1줄
    warnings = [r for r in caplog.records if "표 게시 실패" in r.getMessage()]
    assert len(warnings) == 1
    # want 를 못 읽으면 직전 값 유지
    await publisher.refresh_wanted()
    assert publisher.wanted


# --- diff 규칙 ---


def table(rows: list[dict], *, rate: float = 1400.0) -> dict:
    return {
        "notional": 1000.0,
        "rate": rate,
        "rows": rows,
        "warnings": [],
        "dataReceivedAt": 1,
        "fetchedAt": 2,
    }


def row(sym: str, **over: object) -> dict:
    base = {
        "sym": sym,
        "dom": "upbit",
        "fx": "binance",
        "fwd": 1.0,
        "rev": -1.0,
        "usd": 1.0,
        "spark": [0.1],
        "status": "ok",
        "age": 0.5,
        "slipFwd": 0.0,
        "slipRev": 0.0,
        "krw": 1.0,
        "netDom": None,
        "depDom": None,
        "wdDom": None,
        "depFx": None,
        "wdFx": None,
    }
    return {**base, **over}


def test_delta_skips_age_only_rows_and_carries_status_changes_and_removed() -> None:
    _, prev = make_delta({}, table([row("BTC"), row("ETH"), row("XRP")]))
    text, _ = make_delta(
        prev,
        table(
            [
                row("BTC", age=3.5),  # age 만 바뀜 → 안 실린다
                row("ETH", status="stale", age=9.0),  # status 바뀜 → 현재 age 와 함께
                # XRP 사라짐 → removed
            ],
            rate=1401.0,
        ),
    )
    delta = json.loads(text)
    assert delta["type"] == "delta" and delta["rate"] == 1401.0
    assert [r["sym"] for r in delta["rows"]] == ["ETH"]
    assert delta["rows"][0]["age"] == 9.0
    assert delta["removed"] == ["XRP|upbit|binance"]


def test_delta_with_no_changed_rows_is_still_sent_with_meta() -> None:
    _, prev = make_delta({}, table([row("BTC")]))
    text, _ = make_delta(prev, table([row("BTC", age=99.0)], rate=1402.0))
    delta = json.loads(text)
    assert delta["rows"] == [] and delta["removed"] == [] and delta["rate"] == 1402.0
    assert set(delta) == {
        "type",
        "notional",
        "rate",
        "rows",
        "removed",
        "warnings",
        "dataReceivedAt",
        "fetchedAt",
    }


def test_spark_change_counts_as_changed_row() -> None:
    _, prev = make_delta({}, table([row("BTC")]))
    text, _ = make_delta(prev, table([row("BTC", spark=[0.1, 0.2])]))
    assert [r["sym"] for r in json.loads(text)["rows"]] == ["BTC"]
