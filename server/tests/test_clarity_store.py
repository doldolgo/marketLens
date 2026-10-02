"""core `RedisBus` 의 Clarity 기록 키 둘 — `admin:clarity`(035 §3.3)·`admin:clarity:pages`(040 §3.2) 문자열, 만료 없음."""

import fakeredis

from app.core.redis_bus import RedisBus


async def test_clarity_record_round_trips_without_expiry() -> None:
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    assert await bus.clarity_load() is None
    await bus.clarity_save('{"attemptAt":1}')
    assert await bus.clarity_load() == '{"attemptAt":1}'
    assert fakeredis.FakeRedis(server=server).ttl("admin:clarity") == -1


async def test_clarity_pages_record_is_its_own_key_without_expiry() -> None:
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    assert await bus.clarity_pages_load() is None
    await bus.clarity_pages_save('{"attemptAt":2}')
    assert await bus.clarity_pages_load() == '{"attemptAt":2}'
    assert await bus.clarity_load() is None  # 기본 기록과 섞이지 않는다
    sync = fakeredis.FakeRedis(server=server)
    assert sync.keys() == [b"admin:clarity:pages"]
    assert sync.ttl("admin:clarity:pages") == -1
