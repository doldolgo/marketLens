"""거래소 원문 아카이브 — 원문 싱크·거래소별 버퍼·객체 조립·업로드 루프 (스펙 010).

기록 함수는 001 의 core 계약 `record(exchange, source, received_at_ms, payload)`(동기·무예외)을
구현한다. 줄을 만들어 그 거래소 버퍼에 붙이는 메모리 작업뿐이라 수신 경로를 막지 않는다.
버퍼를 닫고(60초 또는 32MB) gzip 해 S3 에 올리는 일은 별도 태스크가 매초 스레드에서 한다 —
어떤 실패도 수집·/spreads·Redis·Influx 경로에 번지지 않는다. S3 를 읽는 코드는 없다.
"""

import asyncio
import contextlib
import gzip
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

logger = logging.getLogger("marketlens.raw_archive")

CLOSE_AFTER_MS = 60_000  # 버퍼를 닫는 경과 시간 — 첫 줄의 receivedAt 기준 (§3.5)
CLOSE_AT_BYTES = 32 * 1024 * 1024  # 비압축 32MB 도달 시 60초 전에 닫는다 (§3.5)
QUEUE_LIMIT_BYTES = 256 * 1024 * 1024  # 대기열 상한 — 거래소 합산 압축 후 (§3.6)
LOOP_INTERVAL_SEC = 1.0
DRAIN_DEADLINE_SEC = 5.0  # 종료 시 열린 버퍼 업로드 합계 상한 (§3.6)
KEY_PREFIX = "raw"


class Uploader(Protocol):
    """S3 쓰기 — 실물은 core.s3.S3Uploader, 테스트는 fake."""

    def put(self, key: str, body: bytes) -> None: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


# --- 레코드 한 줄 (§3.4) ---


def _is_verbatim_json(payload: str) -> bool:
    """원문을 그대로 이어 붙여도 되는가 — 유효한 JSON 이고 최상위가 객체·배열이며 줄바꿈이 없다."""
    if "\n" in payload or "\r" in payload:
        return False
    try:
        value = json.loads(payload)
    except ValueError:
        return False
    return isinstance(value, dict | list)


def format_line(exchange: str, source: str, received_at_ms: int, payload: str) -> bytes:
    """JSON Lines 한 줄(줄바꿈 포함). 키 4개 순서 고정, `raw` 는 원문 바이트 그대로 또는 문자열로 감싼다."""
    head = json.dumps(
        {"exchange": exchange, "source": source, "receivedAt": received_at_ms},
        separators=(",", ":"),
    )
    raw = payload if _is_verbatim_json(payload) else json.dumps(payload)
    return f'{head[:-1]},"raw":{raw}}}\n'.encode()


def object_key(exchange: str, first_received_at_ms: int) -> str:
    """`raw/exchange=<id>/dt=YYYY-MM-DD/hh=HH/YYYYMMDDTHHMMSS.mmmZ.jsonl.gz` — 전부 UTC, 첫 줄의 수신 시각."""
    at = datetime.fromtimestamp(first_received_at_ms / 1000, tz=UTC)
    stamp = f"{at:%Y%m%dT%H%M%S}.{first_received_at_ms % 1000:03d}Z"
    return (
        f"{KEY_PREFIX}/exchange={exchange}/dt={at:%Y-%m-%d}/hh={at:%H}/{stamp}.jsonl.gz"
    )


def pack(lines: list[bytes]) -> bytes:
    """줄들을 순서대로 이어 gzip — mtime 0 고정이라 같은 입력은 바이트까지 같다 (§3.5)."""
    return gzip.compress(b"".join(lines), mtime=0)


# --- 거래소별 버퍼와 닫힌 객체 (§3.5) ---


@dataclass
class _Buffer:
    first_received_at_ms: int
    lines: list[bytes] = field(default_factory=list)
    size: int = 0  # 비압축 바이트

    def due(self, now_ms: int) -> bool:
        return (
            now_ms - self.first_received_at_ms >= CLOSE_AFTER_MS
            or self.size >= CLOSE_AT_BYTES
        )


@dataclass(frozen=True)
class _Closed:
    """닫혔지만 아직 gzip 전인 버퍼 — 직렬화는 스레드에서 한다."""

    exchange: str
    key: str
    lines: list[bytes]


@dataclass(frozen=True)
class RawObject:
    key: str
    body: bytes  # gzip JSON Lines
    lines: int


# --- 아카이브 (§3.5·§3.6) ---


class RawArchive:
    def __init__(
        self,
        *,
        uploader: Uploader,
        clock: Callable[[], int] = _now_ms,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        drain_deadline_sec: float = DRAIN_DEADLINE_SEC,
    ) -> None:
        self._uploader = uploader
        self._clock = clock
        self._sleep = sleep
        self._drain_deadline_sec = drain_deadline_sec
        self._buffers: dict[str, _Buffer] = {}
        # 거래소를 합쳐 닫힌 순서 하나의 FIFO — 스레드 회차와 종료 경로만 만진다 (§3.6)
        self._queue: deque[RawObject] = deque()
        self._queued_bytes = 0
        self._failures = 0  # 연속 업로드 실패 횟수
        self._task: asyncio.Task[None] | None = None

    # --- 원문 싱크 계약 (001 §3.7) ---

    def record(
        self, exchange: str, source: str, received_at_ms: int, payload: str
    ) -> None:
        """동기·무예외·즉시 반환 — 줄을 만들어 그 거래소 버퍼에 붙인다. 닫는 것은 루프의 몫."""
        try:
            line = format_line(exchange, source, received_at_ms, payload)
            buf = self._buffers.get(exchange)
            if buf is None:
                buf = _Buffer(first_received_at_ms=received_at_ms)
                self._buffers[exchange] = buf
            buf.lines.append(line)
            buf.size += len(line)
        except Exception:
            logger.exception(
                "원문 기록 중 예외 — 이 원문은 버린다 %s %s", exchange, source
            )

    def buffered(self, exchange: str) -> int:
        """아직 닫히지 않은 그 거래소 버퍼의 줄 수."""
        buf = self._buffers.get(exchange)
        return 0 if buf is None else len(buf.lines)

    @property
    def pending(self) -> int:
        """업로드 대기열의 객체 수."""
        return len(self._queue)

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    # --- 업로드 루프 (§3.6) ---

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """매초: 닫을 버퍼를 닫고 대기열을 올린다. 회차 안 예외는 밖으로 나오지 않는다."""
        while True:
            await self._sleep(LOOP_INTERVAL_SEC)
            await self.run_once()

    async def run_once(self, *, force_close: bool = False) -> int:
        """회차 1번 — 닫는 조건을 만족한 버퍼(force 면 전부)를 닫고, 스레드에서 gzip·업로드. 올린 수를 돌려준다."""
        closed = self._close_due(self._clock(), force=force_close)
        try:
            return await asyncio.to_thread(self._pack_and_upload, closed)
        except Exception:
            logger.exception("원문 업로드 회차 중 예외 — 다음 회차에 이어간다")
            return 0

    def _close_due(self, now_ms: int, *, force: bool) -> list[_Closed]:
        closed: list[_Closed] = []
        for exchange, buf in list(self._buffers.items()):
            if not buf.lines:
                continue
            if force or buf.due(now_ms):
                del self._buffers[exchange]  # 다음 줄은 새 버퍼(첫 줄 시각이 곧 새 키)
                closed.append(
                    _Closed(
                        exchange,
                        object_key(exchange, buf.first_received_at_ms),
                        buf.lines,
                    )
                )
        return closed

    def _pack_and_upload(self, closed: list[_Closed]) -> int:
        """스레드에서 — 닫힌 버퍼를 gzip 해 대기열 꼬리에 넣고, 머리부터 하나씩 올린다."""
        for item in closed:
            self._enqueue(RawObject(item.key, pack(item.lines), len(item.lines)))
        return self._upload_queue()

    def _enqueue(self, obj: RawObject) -> None:
        self._queue.append(obj)
        self._queued_bytes += len(obj.body)
        # 상한(압축 후 256MB)을 넘으면 가장 오래된 것부터 버린다 — 방금 넣은 새 객체는 남는다 (§3.6)
        while self._queued_bytes > QUEUE_LIMIT_BYTES and len(self._queue) > 1:
            dropped = self._queue.popleft()
            self._queued_bytes -= len(dropped.body)
            logger.error(
                "원문 대기열 상한(%dMB) 초과 — 가장 오래된 객체를 버린다 key=%s (%d줄)",
                QUEUE_LIMIT_BYTES // (1024 * 1024),
                dropped.key,
                dropped.lines,
            )

    def _upload_queue(self) -> int:
        """머리부터 순서대로. 실패하면 그 객체를 머리에 그대로 두고 멈춘다 — 순서가 바뀌지 않는다."""
        uploaded = 0
        while self._queue:
            obj = self._queue[0]
            try:
                self._uploader.put(obj.key, obj.body)
            except Exception as exc:
                self._failures += 1
                logger.warning(
                    "S3 원문 업로드 실패 (연속 %d회) key=%s: %r",
                    self._failures,
                    obj.key,
                    exc,
                )
                return uploaded
            self._queue.popleft()
            self._queued_bytes -= len(obj.body)
            uploaded += 1
        if self._failures and uploaded:
            logger.info("S3 원문 업로드 재개 — 밀린 객체 %d개 적재", uploaded)
        self._failures = 0
        return uploaded

    async def aclose(self) -> None:
        """루프를 멈추고 열린 버퍼를 전부 닫아 합계 5초 안에서 올려 본다. 넘으면 남은 것은 잃는다 (§3.6)."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        try:
            await asyncio.wait_for(
                self.run_once(force_close=True), self._drain_deadline_sec
            )
        except TimeoutError:
            logger.warning(
                "종료 원문 업로드 데드라인(%.0f초) 초과 — 대기열 %d개 객체를 잃는다",
                self._drain_deadline_sec,
                len(self._queue),
            )
