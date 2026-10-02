"""core `RedisBus` 의 Clarity 기록 키 — `admin:clarity` 문자열, 만료 없음 (스펙 035 §3.3)."""

import fakeredis

from app.core.redis_bus import RedisBus


async def test_clarity_record_round_trips_without_expiry() -> None:
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    assert await bus.clarity_load() is None
    await bus.clarity_save('{"attemptAt":1}')
    assert await bus.clarity_load() == '{"attemptAt":1}'
    assert fakeredis.FakeRedis(server=server).ttl("admin:clarity") == -1
