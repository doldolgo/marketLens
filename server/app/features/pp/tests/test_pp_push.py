"""049 §4 — 게시: 보는 사람(`pp:want`)이 있는 틱마다 PUBLISH pp + SET pp:latest EX 10, 없으면 표를 만들지도 보내지도
않음, Redis 불달 경고 1줄, gap 표 다음 같은 회차 (fakeredis)."""

import json
import logging
from datetime import UTC, datetime

import fakeredis
import httpx
import pytest

from app.core.live_store import LiveStore
from app.core.models import Tick
from app.core.redis_bus import GAP_CHANNEL, PP_CHANNEL, RedisBus, latest_key, want_key
from app.core.ticks import TickLoop
from app.features.pp.push import PpPublisher
from app.features.pp.tests.helpers import make_bus, perp


def seed(store: LiveStore) -> None:
    now = datetime.now(UTC)
    perp(store, "binance_perp", "BTC", now=now)
    perp(store, "hyperliquid_perp", "BTC", now=now)


async def test_publisher_sets_latest_with_ttl_and_publishes_on_channel() -> None:
    bus, server = make_bus()
    listener = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    sub = await listener.subscribe(PP_CHANNEL)
    store = LiveStore()
    seed(store)
    publisher = PpPublisher(store=store, bus=bus)
    await bus.want(PP_CHANNEL)  # pp 허브에 접속자가 있다
    await publisher.refresh_want()
    publisher.observe(Tick(ts=1, rows=(), dw_failed=()))
    assert publisher.pending == 1
    assert await publisher.drain() == 1
    text = await sub.get(timeout=1.0)
    assert text is not None
    [row] = json.loads(text)["rows"]
    assert (row["sym"], row["a"], row["b"]) == (
        "BTC",
        "binance_perp",
        "hyperliquid_perp",
    )
    assert await bus.latest(PP_CHANNEL) == text
    assert await bus._client.ttl(latest_key(PP_CHANNEL)) == 10
    assert await bus.latest() is None  # spreads 키는 건드리지 않는다
    assert await bus.latest(GAP_CHANNEL) is None  # gap 키도
    await sub.aclose()


async def test_no_viewer_builds_and_sends_nothing() -> None:
    """`pp:want` 가 없으면 표를 만들지도(틱 시간) 보내지도(송신) 않는다 — gap 의 흔적도 pp 를 켜지 않는다."""
    bus, server = make_bus()
    listener = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    sub = await listener.subscribe(PP_CHANNEL)
    store = LiveStore()
    seed(store)
    publisher = PpPublisher(store=store, bus=bus)
    await bus.want(GAP_CHANNEL)
    await bus.want()
    await publisher.refresh_want()
    assert publisher.wanted is False
    publisher.observe(Tick(ts=1, rows=(), dw_failed=()))
    assert publisher.pending == 0
    assert await publisher.drain() == 0
    assert await sub.get(timeout=0.2) is None
    assert await bus.latest(PP_CHANNEL) is None
    await sub.aclose()


async def test_want_key_turns_building_on_and_its_expiry_turns_it_off() -> None:
    bus, _ = make_bus()
    store = LiveStore()
    seed(store)
    publisher = PpPublisher(store=store, bus=bus)
    await bus.want(PP_CHANNEL)
    assert await bus._client.ttl(want_key(PP_CHANNEL)) == 15
    await publisher.refresh_want()
    publisher.observe(Tick(ts=1, rows=(), dw_failed=()))
    assert publisher.pending == 1
    await bus._client.delete(
        want_key(PP_CHANNEL)
    )  # 마지막 접속자가 떠나고 15초가 지났다
    await publisher.refresh_want()
    publisher.observe(Tick(ts=2, rows=(), dw_failed=()))
    assert publisher.pending == 1  # 새 표는 없다


async def test_redis_down_warns_once_per_minute_and_tick_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, server = make_bus()
    server.connected = False
    store = LiveStore()
    seed(store)
    publisher = PpPublisher(store=store, bus=bus)
    publisher.wanted = True  # 보는 사람이 있던 중 Redis 가 끊겼다
    handed: list[int] = []
    loop = TickLoop(
        store=store,
        streams=[],
        client=httpx.AsyncClient(),
        handoff=lambda tick: handed.append(tick.ts),
        pp=publisher,
    )
    caplog.set_level(logging.WARNING, logger="marketlens.pp_push")
    loop.tick(1_787_000_000)
    loop.tick(1_787_000_001)
    assert handed == [1_787_000_000]  # 직전 틱 인계는 그대로
    assert await publisher.drain() == 0
    assert len([r for r in caplog.records if "선선표 게시 실패" in r.getMessage()]) == 1


async def test_want_read_failure_turns_building_off_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, server = make_bus()
    store = LiveStore()
    seed(store)
    publisher = PpPublisher(store=store, bus=bus)
    publisher.wanted = True
    server.connected = False
    caplog.set_level(logging.WARNING, logger="marketlens.pp_push")
    await publisher.refresh_want()
    await publisher.refresh_want()
    assert publisher.wanted is False
    assert (
        len([r for r in caplog.records if "pp:want 읽기 실패" in r.getMessage()]) == 1
    )


async def test_gap_and_pp_are_published_in_the_same_round_after_gap() -> None:
    from app.features.gap.push import GapPublisher

    bus, _ = make_bus()
    store = LiveStore()
    seed(store)
    gap = GapPublisher(store=store, bus=bus)
    pp = PpPublisher(store=store, bus=bus)
    await bus.want(GAP_CHANNEL)
    await bus.want(PP_CHANNEL)
    await gap.refresh_want()
    await pp.refresh_want()
    loop = TickLoop(store=store, streams=[], client=httpx.AsyncClient(), gap=gap, pp=pp)
    loop.tick(1_787_000_000)
    assert pp.pending == 1  # 현물 행이 없어 gap 은 빈 표, pp 는 BTC 1행
    assert await pp.drain() == 1
    assert len(json.loads(await bus.latest(PP_CHANNEL))["rows"]) == 1
    # 틱 단계 로그에 `선선표` 가 `갭표` 바로 뒤로 잡힌다
    stages = list(loop._times._samples)
    assert stages.index("선선표") == stages.index("갭표") + 1
