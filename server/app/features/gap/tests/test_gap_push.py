"""048 §4 — 게시: 매 틱 PUBLISH gap + SET gap:latest EX 10, Redis 불달 경고 1줄, spreads 와 같은 회차 (fakeredis)."""

import json
import logging
from datetime import UTC, datetime

import fakeredis
import httpx
import pytest

from app.core.live_store import LiveStore
from app.core.models import Tick
from app.core.redis_bus import GAP_CHANNEL, LATEST_KEY, RedisBus, latest_key
from app.core.ticks import TickLoop
from app.features.gap.push import GapPublisher
from app.features.gap.tests.helpers import make_bus, perp, spot


def seed(store: LiveStore) -> None:
    now = datetime.now(UTC)
    spot(store, "binance", "BTC", now=now)
    perp(store, "bybit_perp", "BTC", now=now)


async def test_publisher_sets_latest_with_ttl_and_publishes_on_channel() -> None:
    bus, server = make_bus()
    listener = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    sub = await listener.subscribe(GAP_CHANNEL)
    store = LiveStore()
    seed(store)
    publisher = GapPublisher(store=store, bus=bus)
    publisher.observe(Tick(ts=1, rows=(), dw_failed=()))
    assert publisher.pending == 1
    assert await publisher.drain() == 1
    text = await sub.get(timeout=1.0)
    assert text is not None
    [row] = json.loads(text)["rows"]
    assert (row["sym"], row["spot"], row["perp"]) == ("BTC", "binance", "bybit_perp")
    assert await bus.latest(GAP_CHANNEL) == text
    assert await bus._client.ttl(latest_key(GAP_CHANNEL)) == 10
    assert await bus.latest() is None  # spreads 키는 건드리지 않는다
    await sub.aclose()


async def test_redis_down_warns_once_per_minute_and_tick_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, server = make_bus()
    server.connected = False
    store = LiveStore()
    seed(store)
    publisher = GapPublisher(store=store, bus=bus)
    handed: list[int] = []
    loop = TickLoop(
        store=store,
        streams=[],
        client=httpx.AsyncClient(),
        handoff=lambda tick: handed.append(tick.ts),
        gap=publisher,
    )
    caplog.set_level(logging.WARNING, logger="marketlens.gap_push")
    loop.tick(1_787_000_000)
    loop.tick(1_787_000_001)
    assert handed == [1_787_000_000]  # 직전 틱 인계는 그대로
    assert await publisher.drain() == 0
    assert len([r for r in caplog.records if "갭표 게시 실패" in r.getMessage()]) == 1


async def test_spreads_and_gap_are_published_in_the_same_round() -> None:
    from app.features.spreads.push import SpreadsPublisher

    bus, _ = make_bus()
    store = LiveStore()
    now = datetime.now(UTC)
    spot(
        store,
        "upbit",
        "BTC",
        quote="KRW",
        ask=[100_100_000.0, 0.4],
        bid=[100_000_000.0, 0.5],
        now=now,
    )
    spot(store, "binance", "BTC", ask=[71_500.0, 2.0], bid=[71_450.0, 1.5], now=now)
    perp(store, "bybit_perp", "BTC", bid=71_480.0, ask=71_490.0, now=now)
    store.set_rate("upbit", 1400.0, 1390.0, now)
    spreads = SpreadsPublisher(store=store, bus=bus)
    gap = GapPublisher(store=store, bus=bus)
    loop = TickLoop(
        store=store,
        streams=[],
        client=httpx.AsyncClient(),
        spreads=spreads,
        gap=gap,
    )
    loop.tick(1_787_000_000)
    assert spreads.pending == 1 and gap.pending == 1
    assert await spreads.drain() == 1 and await gap.drain() == 1
    assert await bus._client.ttl(LATEST_KEY) == 10
    assert await bus._client.ttl(latest_key(GAP_CHANNEL)) == 10
    assert json.loads(await bus.latest())["rows"][0]["dom"] == "upbit"
    assert json.loads(await bus.latest(GAP_CHANNEL))["rows"][0]["perp"] == "bybit_perp"
