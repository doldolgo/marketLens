"""core `RedisBus` 의 Clarity 기록 — `admin:clarity`(035 §3.3)·`admin:clarity:pages`(040 §3.2) 문자열, 만료 없음,
`admin:clarity:dims`(068 §3.1) 해시 칸 둘, 만료 없음, `admin:clarity:day:<YYYYMMDD>`(068 §3.2) 문자열, EXPIREAT."""

import time

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


async def test_clarity_dims_record_is_one_hash_whose_slots_never_overwrite_each_other() -> (
    None
):
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    assert await bus.clarity_dims_load("device") is None
    await bus.clarity_dims_save("device", '{"attemptAt":3}')
    await bus.clarity_dims_save("channel", '{"attemptAt":4}')
    await bus.clarity_dims_save("device", '{"attemptAt":5}')
    assert await bus.clarity_dims_load("device") == '{"attemptAt":5}'
    assert await bus.clarity_dims_load("channel") == '{"attemptAt":4}'
    sync = fakeredis.FakeRedis(server=server)
    assert sync.keys() == [b"admin:clarity:dims"]
    assert sync.type("admin:clarity:dims") == b"hash"
    assert sync.ttl("admin:clarity:dims") == -1
    sync.delete("admin:clarity:dims")  # 런북 — 바로 부르기는 두 칸을 같이 지운다
    assert await bus.clarity_dims_load("channel") is None


async def test_clarity_day_records_expire_at_the_given_time_and_load_in_order() -> None:
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    assert await bus.clarity_days_load([]) == []
    expire_at = int(time.time()) + 91 * 86_400
    await bus.clarity_day_save("20261014", '{"fetchedAt":1}', expire_at)
    await bus.clarity_day_save("20261014", '{"fetchedAt":2}', expire_at)  # 덮어쓴다
    await bus.clarity_day_save("20261012", '{"fetchedAt":0}', expire_at)
    days = ["20261012", "20261013", "20261014"]
    assert await bus.clarity_days_load(days) == [
        '{"fetchedAt":0}',
        None,
        '{"fetchedAt":2}',
    ]
    sync = fakeredis.FakeRedis(server=server)
    assert sync.expiretime("admin:clarity:day:20261014") == expire_at
    assert await bus.clarity_load() is None  # 기본 기록과 섞이지 않는다
