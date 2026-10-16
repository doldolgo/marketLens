"""화면 이동 기록 S3 옮기기와 옮긴 조각 피드 — 수집기 (스펙 078 §3.2·§3.4·§4 'server(수집기)'·'server(archive 피드)').

S3 는 같은 시그니처(`put`·`get`)의 메모리 가짜로, Redis 는 fakeredis 로 본다.
"""

import gzip
import json
import logging
import zlib
from typing import Any

import fakeredis
import pytest

from app.core.redis_bus import RedisBus
from app.features.replay.archive import (
    PICK,
    QUIET_MS,
    STALE_MS,
    ArchiveFeed,
    Archiver,
    object_key,
)
from app.features.replay.tests.conftest import IP, NOON, SID

NOW_MS = int(NOON * 1000)
OLD = (
    NOW_MS - QUIET_MS - 10_000
)  # 3시간 넘게 조용한 first(묶음마다 last 가 1ms 씩 늦다)


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.gets: list[str] = []
        self.fail = False

    def put(self, key: str, body: bytes) -> None:
        if self.fail:
            raise ConnectionError("s3 down")
        self.puts.append(key)
        self.objects[key] = body

    def get(self, key: str) -> bytes | None:
        if self.fail:
            raise ConnectionError("s3 down")
        self.gets.append(key)
        return self.objects.get(key)


class World:
    def __init__(self) -> None:
        self.server = fakeredis.FakeServer()
        self.bus = RedisBus(fakeredis.aioredis.FakeRedis(server=self.server))
        self.redis = fakeredis.FakeRedis(server=self.server)
        self.store = FakeStore()
        self.now = NOON
        self.mono = 1000.0
        self.archiver = Archiver(
            bus=self.bus,
            store=self.store,
            clock=lambda: self.now,
            mono=lambda: self.mono,
        )
        self.feed = ArchiveFeed(store=self.store, mono=lambda: self.mono)

    async def session(
        self, sid: str = SID, first: int = OLD, n: int = 3
    ) -> list[bytes]:
        """keep 조각 하나를 api 와 같은 경로(replay_append)로 쓴다 → 묶음 JSON 줄들."""
        lines = []
        for seq in range(n):
            raw = json.dumps(
                {"at": first + seq, "seq": seq, "ev": [[seq, "c", 1, 2, "kpi"]]},
                separators=(",", ":"),
            ).encode()
            lines.append(raw)
            meta = {
                "ip": IP,
                "dev": "pc",
                "w": "1280",
                "h": "800",
                "page": "landing",
                "pages": "landing",
                "now": "landing",
                "first": str(first),
                "last": str(first + seq),
                "seq": str(seq),
                "n": str(seq + 1),
                "bytes": "10",
                "clicks": str(seq + 1),
                "keep": "1",
            }
            await self.bus.replay_append(
                sid, meta, zlib.compress(raw), "20261019", 14 * 86_400
            )
        return lines

    def meta(self, sid: str = SID) -> dict[str, str]:
        return {
            k.decode(): v.decode() for k, v in self.redis.hgetall(f"rp:m:{sid}").items()
        }

    def todo(self) -> list[str]:
        return [v.decode() for v in self.redis.zrange("rp:todo", 0, -1)]

    async def page(self, sid: str = SID, after: str | None = None) -> dict[str, Any]:
        return json.loads(await self.feed.session(sid, after, self.bus))


def sid(i: int) -> str:
    return f"{i:016x}"


# --- 옮기기 ---


async def test_moves_a_quiet_session_as_one_gzip_jsonl_object() -> None:
    w = World()
    lines = await w.session(n=3)
    assert await w.archiver.run_once() == 1
    key = object_key(SID, OLD)
    assert key == f"replay/2026/10/19/{SID}.ndjson.gz"  # first 의 KST 날
    raw = gzip.decompress(w.store.objects[key]).split(b"\n")
    assert raw[-1] == b"" and raw[1:-1] == lines  # 묶음 JSON 그대로
    head = json.loads(raw[0])
    assert (head["ip"], head["keep"], head["n"]) == (IP, "1", "3")
    assert w.meta()["arch"] == "3" and w.todo() == []
    # 다음 차례에는 옮길 것이 없다
    assert await w.archiver.run_once() == 0 and w.store.puts == [key]


async def test_kst_day_of_first_names_the_key() -> None:
    # 2026-10-18 15:30 UTC = 10-19 00:30 KST
    assert object_key(SID, 1_792_337_400_000) == f"replay/2026/10/19/{SID}.ndjson.gz"


async def test_only_sessions_quiet_for_3_hours_oldest_first_20_at_a_time() -> None:
    w = World()
    await w.session(sid(1), first=NOW_MS - QUIET_MS + 10, n=1)  # 아직 3시간이 안 됐다
    for i in range(30):
        await w.session(sid(100 + i), first=OLD - 1000 * (30 - i), n=1)
    assert await w.archiver.run_once() == PICK == 20
    left = w.todo()
    assert sid(1) in left and len(left) == 11
    # 오래된 순 — 앞 20개가 옮겨졌다
    assert {sid(100 + i) for i in range(20)}.isdisjoint(left)


async def test_a_new_batch_after_reading_keeps_it_in_the_queue() -> None:
    w = World()
    await w.session(n=1)
    real = w.bus.replay_dump

    async def dump_then_batch(s: str) -> Any:
        got = await real(s)
        w.redis.zadd("rp:todo", {s: OLD + 99})  # 그 사이 묶음이 왔다
        return got

    w.bus.replay_dump = dump_then_batch  # type: ignore[method-assign]
    assert await w.archiver.run_once() == 0
    assert "arch" not in w.meta() and w.todo() == [SID]


async def test_missing_head_only_leaves_the_queue() -> None:
    w = World()
    w.redis.zadd("rp:todo", {SID: OLD})
    assert await w.archiver.run_once() == 0
    assert w.todo() == [] and w.store.puts == []


async def test_lost_batches_move_what_is_left_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w = World()
    lines = await w.session(n=3)
    w.redis.lpop(f"rp:e:{SID}")
    with caplog.at_level(logging.WARNING):
        assert await w.archiver.run_once() == 1
    assert w.meta()["arch"] == "2"
    body = gzip.decompress(w.store.objects[object_key(SID, OLD)]).split(b"\n")
    assert body[1:-1] == lines[1:]
    assert "일부가 이미 없다" in caplog.text


async def test_s3_failure_keeps_it_and_warns_once_per_10_minutes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w = World()
    await w.session(n=1)
    w.store.fail = True
    with caplog.at_level(logging.WARNING):
        await w.archiver.run_once()
        await w.archiver.run_once()
        w.mono += 600
        await w.archiver.run_once()
    assert w.todo() == [SID] and "arch" not in w.meta()
    lines = [r for r in caplog.records if "S3 쓰기 실패" in r.getMessage()]
    assert len(lines) == 2 and "ConnectionError" in lines[0].getMessage()
    w.store.fail = False
    assert await w.archiver.run_once() == 1 and w.todo() == []


async def test_waiting_over_7_days_is_an_error_once_per_hour(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w = World()
    await w.session(first=NOW_MS - STALE_MS - 1, n=1)
    w.store.fail = True
    with caplog.at_level(logging.WARNING):
        await w.archiver.run_once()
        w.mono += 1800
        await w.archiver.run_once()
        w.mono += 1800
        await w.archiver.run_once()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2 and "7일" in errors[0].getMessage()
    assert IP not in caplog.text and SID not in caplog.text


async def test_no_bucket_moves_nothing_and_says_so_at_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w = World()
    await w.session(n=1)
    archiver = Archiver(bus=w.bus, store=None, clock=lambda: w.now)
    with caplog.at_level(logging.WARNING):
        archiver.start()
    assert await archiver.run_once() == 0 and w.todo() == [SID]
    assert "S3_BUCKET" in caplog.text
    await archiver.aclose()
