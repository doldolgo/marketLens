"""화면 이동 기록 S3 옮기기와 옮긴 조각 읽기 — 수집기 역할 (스펙 078 §3.2·§3.4).

serve 박스(api)에는 AWS 자격이 없고 collect 박스 역할만 버킷(010 과 같은 버킷)에 닿는다 — 그래서 옮기기와 읽기를 수집기가 한다.
옮기기는 10분마다 `rp:todo` 에서 3시간 조용한 keep 조각을 오래된 순 20개씩 골라 머리와 묶음 전부를 gzip JSON Lines 한 객체로
쓴다(첫 줄 머리, 다음 줄부터 073 이 저장한 묶음 JSON 그대로). 읽기는 같은 객체를 줄로 나눠 073 `session` 과 같은 꼴로 600개씩
돌려준다 — 다시 파싱·직렬화하지 않는다. S3 호출(boto3 동기)과 gzip 은 스레드에서 한다.

업로더는 `put(key, body)`·`get(key) → bytes | None` 만 쓴다(테스트는 같은 시그니처의 가짜). 로그에는 종류 이름만 — 키·IP 없음.
"""

import asyncio
import contextlib
import gzip
import json
import logging
import re
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.core.redis_bus import RedisBus
from app.features.replay.feed import page_body, row

logger = logging.getLogger("marketlens.replay")

KST = ZoneInfo("Asia/Seoul")
EVERY_SEC = 600
QUIET_MS = 3 * 3_600_000  # replay.js 조각 상한 2시간 + 여유
PICK = 20
STALE_MS = (
    7 * 86_400_000
)  # 이보다 오래 기다리면 ERROR — 14일이 지나면 Redis 묶음이 사라진다
WARN_EVERY_SEC = 600.0
ERROR_EVERY_SEC = 3_600.0
GZIP_LEVEL = 6  # HTTP·인계 gzip 과 같은 레벨(architecture.md 성능 원칙)
PAGE_BATCHES = 600
CACHE_ITEMS = 2
CACHE_SEC = 600.0
_AFTER = re.compile(r"[0-9]{1,9}")


class Store(Protocol):
    def put(self, key: str, body: bytes) -> None: ...

    def get(self, key: str) -> bytes | None: ...


def object_key(sid: str, first_ms: int) -> str:
    """`replay/<YYYY>/<MM>/<DD>/<sid>.ndjson.gz` — 날은 first 의 KST 날."""
    day = datetime.fromtimestamp(first_ms / 1000, KST)
    return f"replay/{day:%Y/%m/%d}/{sid}.ndjson.gz"


def build_object(meta: dict[str, str], blobs: Sequence[bytes]) -> bytes:
    """첫 줄 머리(해시 그대로 — 글자 값), 다음 줄부터 묶음마다 푼 JSON 한 줄, gzip."""
    head = json.dumps(meta, separators=(",", ":"), ensure_ascii=False).encode()
    lines = [head, *(zlib.decompress(blob) for blob in blobs)]
    return gzip.compress(b"\n".join(lines) + b"\n", GZIP_LEVEL)


def split_object(body: bytes) -> list[bytes]:
    """객체 → 묶음 줄들(첫 줄 머리는 뺀다). 머리는 Redis 의 지금 값을 쓴다(arch 가 붙은 것)."""
    lines = gzip.decompress(body).split(b"\n")
    return [line for line in lines[1:] if line]


class Archiver:
    """옮기기 한 차례(`run_once`)와 10분 고리(`run`). 시계·업로더·버스는 주입한다."""

    def __init__(
        self,
        *,
        bus: RedisBus,
        store: Store | None,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bus = bus
        self._store = store
        self._clock = clock
        self._mono = mono
        self._warned: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None

    def _say(self, kind: str, every: float, level: int, text: str) -> None:
        now = self._mono()
        last = self._warned.get(kind)
        if last is not None and now - last < every:
            return
        self._warned[kind] = now
        logger.log(level, text)

    async def run_once(self) -> int:
        """3시간 조용한 조각을 오래된 순 20개까지 옮긴다 → 옮긴 조각 수. 실패한 조각은 그대로 두고 다음 차례에."""
        if self._store is None:
            return 0
        now_ms = int(self._clock() * 1000)
        moved = 0
        try:
            oldest = await self._bus.replay_todo_oldest()
            if oldest is not None and now_ms - oldest > STALE_MS:
                self._say(
                    "stale",
                    ERROR_EVERY_SEC,
                    logging.ERROR,
                    "화면 이동 기록을 S3 로 7일 넘게 옮기지 못했다 — 14일이 지나면 Redis 묶음이 사라진다",
                )
            todo = await self._bus.replay_todo(now_ms - QUIET_MS, PICK)
        except Exception as exc:
            self._say(
                "redis",
                WARN_EVERY_SEC,
                logging.WARNING,
                f"화면 이동 기록 옮기기 — Redis 읽기 실패: {type(exc).__name__}",
            )
            return 0
        for sid, last in todo:
            with contextlib.suppress(_Skip):
                moved += await self._move(self._store, sid, last)
        return moved

    async def _move(self, store: Store, sid: str, last: int) -> int:
        try:
            meta, blobs = await self._bus.replay_dump(sid)
            if "first" not in meta:
                await self._bus.replay_drop_todo(sid)
                return 0
        except Exception as exc:
            self._say(
                "redis",
                WARN_EVERY_SEC,
                logging.WARNING,
                f"화면 이동 기록 옮기기 — Redis 실패: {type(exc).__name__}",
            )
            raise _Skip from exc
        if len(blobs) < int(meta.get("n", "0")):
            self._say(
                "lost",
                WARN_EVERY_SEC,
                logging.WARNING,
                "화면 이동 기록 옮기기 — Redis 묶음 일부가 이미 없다(있는 만큼 옮긴다)",
            )
        key = object_key(sid, int(meta["first"]))
        try:
            body = await asyncio.to_thread(build_object, meta, blobs)
            await asyncio.to_thread(store.put, key, body)
        except Exception as exc:
            self._say(
                "s3",
                WARN_EVERY_SEC,
                logging.WARNING,
                f"화면 이동 기록 옮기기 — S3 쓰기 실패: {type(exc).__name__}",
            )
            raise _Skip from exc
        try:
            done = await self._bus.replay_archived(sid, last, len(blobs))
        except Exception as exc:
            self._say(
                "redis",
                WARN_EVERY_SEC,
                logging.WARNING,
                f"화면 이동 기록 옮기기 — Redis 쓰기 실패: {type(exc).__name__}",
            )
            raise _Skip from exc
        return 1 if done else 0

    async def run(self) -> None:
        """10분마다 한 차례 — 처음 차례는 기동 10분 뒤(기동 직후의 부하와 겹치지 않게)."""
        while True:
            await asyncio.sleep(EVERY_SEC)
            try:
                await self.run_once()
            except Exception as exc:  # 고리는 죽지 않는다
                self._say(
                    "loop",
                    WARN_EVERY_SEC,
                    logging.WARNING,
                    f"화면 이동 기록 옮기기 실패: {type(exc).__name__}",
                )

    def start(self) -> None:
        if self._store is None:
            logger.warning(
                "S3_BUCKET 이 없어 화면 이동 기록을 S3 로 옮기지 않는다 — 묶음은 14일 뒤 사라지고 머리·목록만 남는다"
            )
            return
        self._task = asyncio.create_task(self.run())

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


class _Skip(Exception):
    """그 조각만 다음 차례로."""


def _failed(code: str, start: int) -> bytes:
    body: dict[str, Any] = {
        "state": "error",
        "code": code,
        "meta": None,
        "batches": [],
        "next": start,
        "more": False,
        "archived": True,
    }
    return json.dumps(body, separators=(",", ":")).encode()


class ArchiveFeed:
    """`GET /admin/replay/archive` — 옮긴 조각을 073 `session` 과 같은 꼴로. 받은 객체는 최근 2개를 10분 둔다."""

    def __init__(
        self,
        *,
        store: Store | None,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._mono = mono
        self._cache: OrderedDict[str, tuple[float, list[bytes]]] = OrderedDict()

    async def _lines(self, store: Store, key: str) -> list[bytes] | None:
        now = self._mono()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < CACHE_SEC:
            self._cache.move_to_end(key)
            return hit[1]
        raw = await asyncio.to_thread(store.get, key)
        if raw is None:
            return None
        lines = await asyncio.to_thread(split_object, raw)
        self._cache[key] = (now, lines)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_ITEMS:
            self._cache.popitem(last=False)
        return lines

    async def session(self, sid: str, after: str | None, bus: RedisBus | None) -> bytes:
        """sid 꼴 검사(400)는 경로가 먼저 한다. 머리·arch·객체가 없으면 meta null·묶음 []."""
        start = int(after) if after is not None and _AFTER.fullmatch(after) else 0
        store = self._store
        if store is None:
            return _failed("s3_off", start)
        try:
            if bus is None:
                raise ConnectionError("버스 없음")
            (meta,) = await bus.replay_metas([sid])
        except Exception as exc:
            logger.warning(
                "옮긴 화면 이동 기록 읽기 — Redis 실패: %s", type(exc).__name__
            )
            return _failed("redis", start)
        head = row(sid, meta)
        if head is None or "arch" not in meta:
            return page_body(None, [], start, 0, True)
        try:
            lines = await self._lines(store, object_key(sid, int(meta["first"])))
        except Exception as exc:
            logger.warning("옮긴 화면 이동 기록 읽기 — S3 실패: %s", type(exc).__name__)
            return _failed("s3", start)
        if lines is None:
            return page_body(None, [], start, 0, True)
        part = lines[start : start + PAGE_BATCHES]
        return await asyncio.to_thread(page_body, head, part, start, len(lines), True)
