"""화면 이동 기록 보관 표시 — v8 시행일 뒤 처음 만든 조각은 서비스를 끝낼 때까지 (스펙 078 §3.1·§3.3·§4 'server(api)')."""

import zlib

from app.features.replay.tests.conftest import (
    IP,
    KEEP_SEC,
    NOON,
    SID,
    Rig,
    body,
    kst,
)

NOON_MS = int(NOON * 1000)
# conftest 의 KEEP_FROM(kept 짝) 00:00 KST
FROM = kst(2026, 10, 19, 0, 0)


async def test_kept_head_never_expires_events_keep_14_days_and_it_waits_for_s3(
    kept: Rig,
) -> None:
    assert (await kept.post(body())).status_code == 204
    assert kept.meta()["keep"] == "1"
    assert kept.redis.ttl(f"rp:m:{SID}") == -1
    assert kept.redis.ttl(f"rp:e:{SID}") == KEEP_SEC
    assert kept.redis.ttl("rp:b:20261019") == KEEP_SEC
    for key in ("rp:recent", f"rp:ip:{IP}", "rp:todo"):
        assert kept.redis.ttl(key) == -1, key
    assert kept.redis.zrange("rp:todo", 0, -1, withscores=True) == [
        (SID.encode(), NOON_MS)
    ]
    # 다음 묶음 — 머리는 그대로 keep, 옮길 차례의 점수는 새 last
    kept.clock.now = NOON + 5
    await kept.post(body(seq=1))
    assert kept.meta()["keep"] == "1" and kept.redis.ttl(f"rp:m:{SID}") == -1
    assert kept.redis.zscore("rp:todo", SID) == NOON_MS + 5000


async def test_keep_starts_at_midnight_kst_of_the_effective_day(kept: Rig) -> None:
    kept.clock.now = FROM - 0.001
    await kept.post(body(sid="a" * 16))
    kept.clock.now = FROM
    await kept.post(body(sid="b" * 16))
    assert "keep" not in kept.meta("a" * 16)
    assert kept.redis.ttl(f"rp:m:{'a' * 16}") == KEEP_SEC
    assert kept.meta("b" * 16)["keep"] == "1"
    assert kept.redis.zrange("rp:todo", 0, -1) == [b"b" * 16]


async def test_a_head_made_before_keep_stays_14_days(kept: Rig) -> None:
    """머리를 만든 뒤에는 keep 을 바꾸지 않는다 — v8 전 코드·판으로 시작한 조각은 끝까지 14일."""
    kept.redis.hset(
        f"rp:m:{SID}",
        mapping={
            "ip": IP,
            "dev": "pc",
            "w": "1280",
            "h": "800",
            "page": "app-spread",
            "pages": "app-spread",
            "now": "app-spread",
            "first": str(NOON_MS - 1000),
            "last": str(NOON_MS - 1000),
            "seq": "0",
            "n": "1",
            "bytes": "10",
            "clicks": "0",
        },
    )
    await kept.post(body(seq=1))
    assert "keep" not in kept.meta() and kept.meta()["n"] == "2"
    assert kept.redis.ttl(f"rp:m:{SID}") == KEEP_SEC
    assert kept.redis.zcard("rp:todo") == 0


async def test_cut_mark_does_not_expire_a_kept_head(kept: Rig) -> None:
    await kept.post(body(seq=0))
    kept.redis.hset(f"rp:m:{SID}", "bytes", 1024 * 1024 - 5)
    await kept.post(body(seq=1))
    assert kept.meta()["cut"] == "1"
    assert kept.redis.ttl(f"rp:m:{SID}") == -1


async def test_session_says_archived_only_when_moved_and_gone_from_redis(
    kept: Rig,
) -> None:
    await kept.post(body())
    got = (await kept.get("session", sid=SID)).json()
    assert got["archived"] is False and len(got["batches"]) == 1
    # 옮겼어도 Redis 에 묶음이 있으면 Redis 에서
    kept.redis.hset(f"rp:m:{SID}", "arch", "1")
    got = (await kept.get("session", sid=SID)).json()
    assert got["archived"] is False and len(got["batches"]) == 1
    # 14일이 지나 묶음이 사라지면 archived — 묶음 [] · next = after · more false
    kept.redis.delete(f"rp:e:{SID}")
    got = (await kept.get("session", sid=SID, after="4")).json()
    assert (got["archived"], got["batches"], got["next"], got["more"]) == (
        True,
        [],
        4,
        False,
    )
    assert got["meta"]["sid"] == SID
    # 옮기지 않았는데 묶음이 없으면(잃음) archived 가 아니다
    kept.redis.hdel(f"rp:m:{SID}", "arch")
    got = (await kept.get("session", sid=SID)).json()
    assert got["archived"] is False


async def test_stored_batch_bytes_are_unchanged(kept: Rig) -> None:
    await kept.post(body(ev=[[0, "c", 1, 2, "kpi"]]))
    (blob,) = kept.redis.lrange(f"rp:e:{SID}", 0, -1)
    assert zlib.decompress(blob) == (
        b'{"at":%d,"seq":0,"ev":[[0,"c",1,2,"kpi"]]}' % NOON_MS
    )
