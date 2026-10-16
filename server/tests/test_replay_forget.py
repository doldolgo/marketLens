"""IP 하나의 화면 이동 기록 지우기 스크립트 (스펙 078 §3.6·§4 '지우기 스크립트') — fakeredis 와 메모리 S3 가짜."""

import fakeredis
import pytest

from app.core.redis_bus import RedisBus
from app.features.replay.archive import object_key
from scripts.replay_forget import forget

IP = "203.0.113.7"
OTHER = "198.51.100.1"
FIRST = 1_792_000_000_000


class Store:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted.append(key)


def seed(redis: fakeredis.FakeRedis, sid: str, ip: str, **extra: str) -> None:
    redis.hset(
        f"rp:m:{sid}", mapping={"ip": ip, "first": str(FIRST), "keep": "1", **extra}
    )
    redis.rpush(f"rp:e:{sid}", b"x")
    redis.zadd(f"rp:ip:{ip}", {sid: FIRST})
    redis.zadd("rp:recent", {sid: FIRST})
    redis.zadd("rp:todo", {sid: FIRST})


@pytest.fixture
def world() -> tuple[RedisBus, fakeredis.FakeRedis]:
    server = fakeredis.FakeServer()
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server)), fakeredis.FakeRedis(
        server=server
    )


async def test_deletes_only_that_ips_keys_and_objects(world) -> None:  # noqa: ANN001
    bus, redis = world
    seed(redis, "a" * 16, IP, arch="3")
    seed(redis, "b" * 16, IP)
    seed(redis, "c" * 16, OTHER, arch="1")
    store = Store()
    assert await forget(bus, store, IP, dry_run=False) == (2, 2)
    assert sorted(store.deleted) == sorted(
        [object_key("a" * 16, FIRST), object_key("b" * 16, FIRST)]
    )
    left = sorted(k.decode() for k in redis.keys("rp:*"))
    assert left == [
        f"rp:e:{'c' * 16}",
        f"rp:ip:{OTHER}",
        f"rp:m:{'c' * 16}",
        "rp:recent",
        "rp:todo",
    ]
    assert redis.zrange("rp:recent", 0, -1) == [b"c" * 16]
    assert redis.zrange("rp:todo", 0, -1) == [b"c" * 16]


async def test_dry_run_counts_without_deleting(world) -> None:  # noqa: ANN001
    bus, redis = world
    seed(redis, "a" * 16, IP, arch="3")
    store = Store()
    assert await forget(bus, store, IP, dry_run=True) == (1, 1)
    assert store.deleted == [] and redis.exists(f"rp:m:{'a' * 16}")


async def test_archived_without_bucket_deletes_nothing(world) -> None:  # noqa: ANN001
    bus, redis = world
    seed(redis, "a" * 16, IP, arch="3")
    with pytest.raises(RuntimeError):
        await forget(bus, None, IP, dry_run=False)
    assert redis.exists(f"rp:m:{'a' * 16}")
    # 옮긴 것이 없으면 버킷 없이도 Redis 만 지운다
    redis.hdel(f"rp:m:{'a' * 16}", "arch")
    assert await forget(bus, None, IP, dry_run=False) == (1, 0)
    assert not redis.keys("rp:*")
