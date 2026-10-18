"""Redis 저장 — 키 모양·만료·`pages` 차례·`clicks`·같은 seq·그날 8MB·조각 1MB·묶음 3,600 (스펙 073 §3.5·§4)."""

import zlib

import pytest

from app.features.replay.tests.conftest import (
    IP,
    KEEP_SEC,
    NOON,
    SID,
    Rig,
    body,
    kst,
)

DAY_CAP = 8 * 1024 * 1024
SID_CAP = 1024 * 1024
NOON_MS = int(NOON * 1000)


async def test_one_batch_writes_five_keys_and_only_the_two_sets_never_expire(
    rig: Rig,
) -> None:
    ev = [[0, "m", 10, 20], [150, "c", 11, 21, "table"], [900, "s", 300]]
    assert (await rig.post(body(ev=ev))).status_code == 204
    assert rig.keys() == [
        "rp:b:20261019",
        f"rp:e:{SID}",
        f"rp:ip:{IP}",
        f"rp:m:{SID}",
        "rp:recent",
    ]
    # 078 §3.1 — keep 이 아닌 조각(v8 전)의 머리·묶음·그날 키는 14일, 두 집합은 만료 없음(keep 조각이 IP 로 찾혀야 한다)
    for key in rig.keys():
        want = -1 if key in ("rp:recent", f"rp:ip:{IP}") else KEEP_SEC
        assert rig.redis.ttl(key) == want, key
    (blob,) = rig.redis.lrange(f"rp:e:{SID}", 0, -1)
    assert rig.batches() == [{"at": NOON_MS, "seq": 0, "ev": ev}]
    assert zlib.decompress(blob) == (
        b'{"at":%d,"seq":0,"ev":[[0,"m",10,20],[150,"c",11,21,"table"],[900,"s",300]]}'
        % NOON_MS
    )
    assert rig.meta() == {
        "ip": IP,
        "dev": "pc",
        "w": "1280",
        "h": "800",
        "page": "app-spread",
        "pages": "app-spread",
        "now": "app-spread",
        "first": str(NOON_MS),
        "last": str(NOON_MS),
        "seq": "0",
        "n": "1",
        "bytes": str(len(blob)),
        "clicks": "1",
        # c 가 없는 몸통(동의자만 기록하던 예전 replay.js)은 지금 판의 동의로 본다
        "cs0": "granted",
        "cs": "granted",
    }
    assert rig.redis.zrange(f"rp:ip:{IP}", 0, -1, withscores=True) == [
        (SID.encode(), NOON_MS)
    ]
    assert rig.redis.zrange("rp:recent", 0, -1, withscores=True) == [
        (SID.encode(), NOON_MS)
    ]
    assert int(rig.redis.get("rp:b:20261019")) == len(blob)


async def test_later_batches_keep_the_head_and_move_last_n_bytes_clicks(
    rig: Rig,
) -> None:
    await rig.post(body(ev=[[0, "c", 1, 1, ""], [5, "p", "app-gap"]]))
    rig.clock.now = NOON + 2
    # 뒤 묶음의 화면·기기·크기·IP 가 달라도 머리는 첫 묶음 그대로다(조각 시작 때 값)
    later = body(
        seq=1,
        page="landing",
        dev="mobile",
        w=375,
        h=700,
        ev=[[2000, "c", 2, 2, "kpi"], [2100, "c", 3, 3, ""]],
    )
    assert (await rig.post(later, ip="198.51.100.9")).status_code == 204
    meta = rig.meta()
    sizes = [len(b) for b in rig.redis.lrange(f"rp:e:{SID}", 0, -1)]
    assert (meta["ip"], meta["dev"], meta["w"], meta["h"], meta["page"]) == (
        IP,
        "pc",
        "1280",
        "800",
        "app-spread",
    )
    assert (meta["first"], meta["last"]) == (str(NOON_MS), str(NOON_MS + 2000))
    assert (meta["n"], meta["seq"], meta["clicks"]) == ("2", "1", "3")
    assert meta["bytes"] == str(sum(sizes))
    assert int(rig.redis.get("rp:b:20261019")) == sum(sizes)
    assert [b["at"] for b in rig.batches()] == [NOON_MS, NOON_MS + 2000]
    # 조각은 첫 IP 의 집합에만, 최근 집합의 점수는 last
    assert rig.redis.keys("rp:ip:198.51.100.9") == []
    assert rig.redis.zscore("rp:recent", SID) == NOON_MS + 2000


async def test_pages_are_in_order_without_duplicates_and_now_is_the_last_p(
    rig: Rig,
) -> None:
    first = [[0, "p", "app-gap"], [1, "p", "app-spread"], [2, "p", "app-history"]]
    await rig.post(body(ev=first))
    assert (rig.meta()["pages"], rig.meta()["now"]) == (
        "app-spread,app-gap,app-history",
        "app-history",
    )
    await rig.post(body(seq=1, ev=[[3, "p", "app-gap"], [4, "p", "app-flow"]]))
    await rig.post(body(seq=2, ev=[[5, "p", "app-gap"], [6, "m", 1, 1]]))
    meta = rig.meta()
    assert meta["pages"] == "app-spread,app-gap,app-history,app-flow"
    assert meta["now"] == "app-gap"
    # p 가 없는 묶음은 지금 화면을 바꾸지 않는다
    await rig.post(body(seq=3, ev=[[7, "a", "table"]]))
    assert rig.meta()["now"] == "app-gap"


async def test_same_or_older_seq_is_dropped(rig: Rig) -> None:
    await rig.post(body(seq=0))
    assert (await rig.post(body(seq=0, ev=[[1, "v", 0]]))).status_code == 204
    assert (rig.meta()["n"], len(rig.batches())) == ("1", 1)
    await rig.post(body(seq=2))
    assert (await rig.post(body(seq=1))).status_code == 204
    await rig.post(body(seq=3))
    assert [b["seq"] for b in rig.batches()] == [0, 2, 3]
    assert (rig.meta()["seq"], rig.meta()["n"]) == ("3", "3")
    assert "cut" not in rig.meta()


async def test_day_cap_8mb_drops_the_rest_of_that_kst_day(rig: Rig) -> None:
    rig.redis.set("rp:b:20261019", DAY_CAP - 10)
    assert (await rig.post(body())).status_code == 204
    assert rig.redis.keys("rp:m:*") == []
    assert int(rig.redis.get("rp:b:20261019")) == DAY_CAP - 10
    # 들어가는 만큼 남았으면 받는다 — 상한을 넘지 않는다
    rig.redis.set("rp:b:20261019", DAY_CAP - 1000)
    assert (await rig.post(body())).status_code == 204
    used = int(rig.redis.get("rp:b:20261019"))
    assert DAY_CAP - 1000 < used <= DAY_CAP
    # 날이 바뀌면(KST 00:00) 새 키
    rig.redis.set("rp:b:20261019", DAY_CAP)
    rig.clock.now = kst(2026, 10, 19, 23, 59, 59)
    await rig.post(body(seq=1))
    assert rig.meta()["n"] == "1"
    rig.clock.now = kst(2026, 10, 20, 0, 0, 0)
    await rig.post(body(seq=2))
    assert rig.meta()["n"] == "2"
    assert int(rig.redis.get("rp:b:20261020")) == len(
        rig.redis.lrange(f"rp:e:{SID}", -1, -1)[0]
    )
    assert int(rig.redis.get("rp:b:20261019")) == DAY_CAP


async def test_sid_cap_1mb_drops_and_marks_the_session_cut(rig: Rig) -> None:
    await rig.post(body(seq=0))
    rig.redis.hset(f"rp:m:{SID}", "bytes", SID_CAP - 5)
    assert (await rig.post(body(seq=1))).status_code == 204
    meta = rig.meta()
    assert (meta["n"], meta["seq"], meta["cut"]) == ("1", "0", "1")
    assert len(rig.batches()) == 1
    assert rig.redis.ttl(f"rp:m:{SID}") == KEEP_SEC
    # 들어가는 만큼 남았으면 받는다(표시는 남는다)
    rig.redis.hset(f"rp:m:{SID}", "bytes", SID_CAP - 1000)
    await rig.post(body(seq=2))
    meta = rig.meta()
    assert (meta["n"], meta["seq"], meta["cut"]) == ("2", "2", "1")
    assert int(meta["bytes"]) <= SID_CAP


async def test_sid_cap_3600_batches_drops_and_marks_the_session_cut(
    rig: Rig,
) -> None:
    await rig.post(body(seq=0))
    rig.redis.hset(f"rp:m:{SID}", "n", 3_599)
    assert (await rig.post(body(seq=1))).status_code == 204
    assert rig.meta()["n"] == "3600" and "cut" not in rig.meta()
    assert (await rig.post(body(seq=2))).status_code == 204
    meta = rig.meta()
    assert (meta["n"], meta["seq"], meta["cut"]) == ("3600", "1", "1")
    assert len(rig.batches()) == 2


async def test_writing_never_trims_or_expires_the_two_sets(rig: Rig) -> None:
    """078 §3.1 — 14일 넘은 점수도 그대로(keep 조각), 앞 코드가 단 만료는 걷는다(PERSIST)."""
    cutoff = NOON_MS - KEEP_SEC * 1000
    for key in ("rp:recent", f"rp:ip:{IP}"):
        rig.redis.zadd(key, {"old": cutoff - 1})
        rig.redis.expire(key, 60)
    await rig.post(body())
    for key in ("rp:recent", f"rp:ip:{IP}"):
        assert rig.redis.zrange(key, 0, -1) == [b"old", SID.encode()], key
        assert rig.redis.ttl(key) == -1, key


# --- 동의 상태 (073 §3.4·§3.5 — 동의와 관계없이 모든 방문을 기록하고 묶음마다 그 방문의 동의 상태를 싣는다) ---------------


@pytest.mark.parametrize("consent", ["granted", "denied", "later", "unset", "gpc"])
async def test_every_consent_state_is_stored(rig: Rig, consent: str) -> None:
    assert (await rig.post(body(c=consent))).status_code == 204
    meta = rig.meta()
    assert (meta["cs0"], meta["cs"]) == (consent, consent)
    assert len(rig.batches()) == 1


async def test_first_consent_stays_and_the_last_batch_sets_the_current_one(
    rig: Rig,
) -> None:
    # 동의 창에 답하지 않던 방문이 중간에 [모두 동의] — 첫 상태는 그대로, 지금 상태는 마지막 묶음의 것
    await rig.post(body(c="unset"))
    rig.clock.now = NOON + 2
    await rig.post(body(seq=1, c="granted", ev=[[2000, "v", 1]]))
    meta = rig.meta()
    assert (meta["cs0"], meta["cs"]) == ("unset", "granted")
    rig.clock.now = NOON + 4
    # c 없는 묶음(예전 replay.js)은 granted
    await rig.post(body(seq=2, ev=[[4000, "v", 1]]))
    assert (rig.meta()["cs0"], rig.meta()["cs"]) == ("unset", "granted")
