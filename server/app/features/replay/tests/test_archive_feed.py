"""옮긴 화면 이동 기록 피드 `GET /admin/replay/archive` — 수집기 (스펙 078 §3.4·§4 'server(archive 피드)').

옮기기 시험(test_archive.py)의 가짜 S3·fakeredis 세계를 그대로 쓴다.
"""

import json

import pytest

from app.features.replay.archive import ArchiveFeed
from app.features.replay.tests.conftest import SID
from app.features.replay.tests.test_archive import OLD, World, sid


async def _moved(w: World, n: int) -> list[bytes]:
    lines = await w.session(first=OLD - n, n=n)
    await w.archiver.run_once()
    w.redis.delete(f"rp:e:{SID}")  # 14일이 지나 Redis 묶음은 사라졌다
    return lines


async def test_feed_pages_600_with_after_next_more() -> None:
    w = World()
    lines = await _moved(w, 1300)
    got = await w.page()
    assert list(got) == ["state", "code", "meta", "batches", "next", "more", "archived"]
    assert (got["state"], got["code"], got["archived"]) == ("ok", None, True)
    assert got["meta"]["sid"] == SID and got["meta"]["n"] == 1300
    assert len(got["batches"]) == 600 and (got["next"], got["more"]) == (600, True)
    assert got["batches"][0] == json.loads(lines[0])
    got = await w.page(after="1200")
    assert len(got["batches"]) == 100 and (got["next"], got["more"]) == (1300, False)
    assert got["batches"][-1] == json.loads(lines[-1])
    got = await w.page(after="x")  # 틀린 after 는 0
    assert got["batches"][0]["seq"] == 0


@pytest.mark.parametrize("case", ["no_head", "not_moved", "no_object"])
async def test_feed_has_null_meta_without_head_arch_or_object(case: str) -> None:
    w = World()
    if case == "not_moved":
        await w.session(n=1)
    elif case == "no_object":
        await _moved(w, 1)
        w.store.objects.clear()
    got = await w.page(after="5")
    assert got == {
        "state": "ok",
        "code": None,
        "meta": None,
        "batches": [],
        "next": 5,
        "more": False,
        "archived": True,
    }


async def test_feed_errors_are_states() -> None:
    w = World()
    await _moved(w, 1)
    w.store.fail = True
    w.feed = ArchiveFeed(store=w.store, mono=lambda: w.mono)  # 캐시 없이
    assert (await w.page())["code"] == "s3"
    got = json.loads(await w.feed.session(SID, None, None))
    assert (got["state"], got["code"], got["archived"]) == ("error", "redis", True)
    got = json.loads(await ArchiveFeed(store=None).session(SID, "2", w.bus))
    assert (got["code"], got["next"]) == ("s3_off", 2)


async def test_feed_keeps_the_last_two_objects_for_10_minutes() -> None:
    w = World()
    for i in (1, 2, 3):
        await w.session(sid(i), n=1)
    await w.archiver.run_once()
    for i in (1, 2, 1, 2):
        await w.page(sid(i))
    assert len(w.store.gets) == 2
    await w.page(sid(3))  # 셋째 — 가장 오래 안 쓴 1 이 나간다
    await w.page(sid(2))
    await w.page(sid(1))
    assert len(w.store.gets) == 4
    w.mono += 600
    await w.page(sid(1))
    assert len(w.store.gets) == 5
