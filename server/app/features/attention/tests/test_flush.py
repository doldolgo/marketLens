"""10초 묶음 쓰기 — 보냄·실패 뒤 다시·2만 필드 넘으면 버림·끌 때 보냄 (스펙 052 §3.5·§4)."""

import asyncio
import logging
from collections.abc import Mapping

import fakeredis
import pytest

from app.core.redis_bus import RedisBus
from app.features.attention.service import AttentionService
from app.features.attention.tests.conftest import (
    EFFECTIVE,
    Clock,
    Rig,
    Ticks,
    beacon,
)


class FlakyBus(RedisBus):
    """`attention_add` 를 정한 횟수만큼 실패시키는 버스 — 나머지는 fakeredis 그대로."""

    def __init__(self, server: fakeredis.FakeServer, fail: int) -> None:
        super().__init__(fakeredis.aioredis.FakeRedis(server=server))
        self.fail = fail
        self.calls = 0

    async def attention_add(
        self, day: str, counts: Mapping[str, int], expire_at: int
    ) -> None:
        self.calls += 1
        if self.fail > 0:
            self.fail -= 1
            raise ConnectionError("redis down")
        await super().attention_add(day, counts, expire_at)


def _wide(n: int) -> bytes:
    """영역 40개 × 지표 3 = 필드 120 — n 으로 비콘마다 다른 id."""
    return beacon(pv=0, a={f"a{n}-{i}": [1, 1, 1] for i in range(40)})


async def _rig(
    fail: int,
) -> tuple[AttentionService, Ticks, FlakyBus, fakeredis.FakeRedis]:
    server = fakeredis.FakeServer()
    clock, ticks = Clock(), Ticks()
    service = AttentionService(
        clock=clock.time,
        mono=clock.monotonic,
        sleep=ticks.sleep,
        privacy_effective=EFFECTIVE,
    )
    bus = FlakyBus(server, fail)
    service.start(bus)
    await asyncio.sleep(0)
    return service, ticks, bus, fakeredis.FakeRedis(server=server)


async def test_nothing_pending_means_no_redis_call() -> None:
    service, ticks, bus, _ = await _rig(fail=0)
    await ticks.release()
    await service.aclose()
    assert bus.calls == 0


async def test_failed_batch_stays_in_memory_and_goes_with_the_next_one() -> None:
    service, ticks, bus, redis = await _rig(fail=1)
    assert service.accept(beacon(), None, None) == "added"
    await ticks.release()  # 실패 — 메모리에 남는다
    assert bus.calls == 1 and redis.keys("attn:*") == []
    assert service.accept(beacon(), None, None) == "added"
    await ticks.release()
    assert redis.hgetall("attn:d:20261019") == {b"landing|pc||pv": b"2"}
    await service.aclose()


async def test_over_20000_pending_fields_are_dropped_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="marketlens.attention")
    service, ticks, bus, redis = await _rig(fail=1)
    for n in range(167):  # 167 × 120 = 20,040 필드
        assert service.accept(_wide(n), None, None) == "added"
    await ticks.release()
    warnings = [r for r in caplog.records if r.name == "marketlens.attention"]
    assert len(warnings) == 1 and "20040" in warnings[0].getMessage()
    await ticks.release()  # 버렸으니 보낼 것이 없다
    assert bus.calls == 1 and redis.keys("attn:*") == []
    await service.aclose()


async def test_just_under_the_cap_is_kept_and_retried() -> None:
    service, ticks, bus, redis = await _rig(fail=1)
    for n in range(166):  # 19,920 필드
        service.accept(_wide(n), None, None)
    await ticks.release()
    await ticks.release()
    assert redis.hlen("attn:d:20261019") == 166 * 120
    await service.aclose()


async def test_shutdown_sends_what_is_left(rig: Rig) -> None:
    assert (await rig.post(beacon(page="privacy", device="mobile"))).status_code == 204
    assert rig.redis.keys("attn:*") == []
    await rig.service.aclose()
    assert rig.day("20261019") == {"privacy|mobile||pv": 1}
