"""거래소 원문 아카이브 — 원문 싱크·거래소별 버퍼·객체 조립·닫기 회차·업로드 워커 (스펙 010).

기록 함수는 001 의 core 계약 `record(exchange, source, received_at_ms, payload)`(동기·무예외)을
구현한다. 줄을 만들어 그 거래소 버퍼에 붙이는 메모리 작업뿐이라 수신 경로를 막지 않는다.
닫기 회차(태스크, 매초)가 버퍼를 닫아(60초 또는 32MB) 스레드에서 gzip 해 대기열에 넣고,
업로드 워커(데몬 스레드 하나)가 대기열 머리부터 S3 에 올린다 — 둘은 잠금으로만 만나고
닫기 주기는 업로드 결과와 무관하다. 어떤 실패도 수집·/spreads·Redis·Influx 경로에 번지지
않는다. S3 를 읽는 코드는 없다.
"""

import asyncio
import contextlib
import gzip
import json
import logging
import threading
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
LOOP_INTERVAL_SEC = 1.0  # 닫기 회차 주기
RETRY_INTERVAL_SEC = 1.0  # 실패한 머리 객체를 워커가 다시 시도하는 간격 (§3.6)
DRAIN_DEADLINE_SEC = 5.0  # 종료 시 대기열 비우기 합계 상한 (§3.6)
GZIP_LEVEL = 6  # 32MB 에 0.3초 안팎 — 레벨 9 는 3배 넘게 걸리고 이득은 1% 미만 (§3.5)
KEY_PREFIX = "raw"
WORKER_NAME = "raw-archive-upload"


class Uploader(Protocol):
    """S3 쓰기 — 실물은 core.s3.S3Uploader, 테스트는 fake."""

    def put(self, key: str, body: bytes) -> None: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


# --- 레코드 한 줄 (§3.4) ---


def _reject_constant(name: str) -> None:
    """`NaN`·`Infinity` 는 표준 JSON 이 아니다 — 그대로 붙이면 줄 전체가 표준 파서에서 깨진다 (§3.4)."""
    raise ValueError(f"non-standard JSON constant {name}")


def _is_verbatim_json(payload: str) -> bool:
    """원문을 그대로 이어 붙여도 되는가 — 표준 JSON 이고 최상위가 객체·배열이며 줄바꿈이 없다."""
    if "\n" in payload or "\r" in payload:
        return False
    try:
        value = json.loads(payload, parse_constant=_reject_constant)
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
    """줄들을 순서대로 이어 gzip — 레벨·mtime 0 고정이라 같은 입력은 바이트까지 같다 (§3.5)."""
    return gzip.compress(b"".join(lines), compresslevel=GZIP_LEVEL, mtime=0)


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
        retry_interval_sec: float = RETRY_INTERVAL_SEC,
    ) -> None:
        self._uploader = uploader
        self._clock = clock
        self._sleep = sleep
        self._drain_deadline_sec = drain_deadline_sec
        self._retry_interval_sec = retry_interval_sec
        # 버퍼는 이벤트 루프 스레드만 만진다 — 기록 함수와 닫기 회차.
        self._buffers: dict[str, _Buffer] = {}
        # 거래소를 합쳐 닫힌 순서 하나의 FIFO. 넣기·상한 버림(닫기 회차)과 머리 빼기(워커)는
        # 전부 `_changed`(잠금 + 조건변수) 아래에서만 한다 (§3.6).
        self._queue: deque[RawObject] = deque()
        self._queued_bytes = 0
        self._failures = 0  # 연속 업로드 실패 횟수
        self._changed = threading.Condition()
        self._closing = False  # 종료 — 워커가 빠져나온다
        self._worker: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        self._packing: asyncio.Future[None] | None = None  # 진행 중인 gzip·넣기

    # --- 원문 싱크 계약 (001 §3.7) ---

    def record(
        self, exchange: str, source: str, received_at_ms: int, payload: str
    ) -> None:
        """동기·무예외·즉시 반환 — 줄을 만들어 그 거래소 버퍼에 붙인다. 닫는 것은 닫기 회차의 몫."""
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
        with self._changed:
            return len(self._queue)

    @property
    def consecutive_failures(self) -> int:
        with self._changed:
            return self._failures

    # --- 닫기 회차 (§3.6) ---

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """매초 닫기 회차. 회차 안 예외는 밖으로 나오지 않는다 — 태스크가 죽으면 기록은 계속 붙는데
        버퍼가 닫히지 않아 메모리가 무한히 자라므로, 예상 밖 예외는 로그 1줄로 삼키고 다음 회차를 돈다."""
        while True:
            await self._sleep(LOOP_INTERVAL_SEC)
            try:
                await self.run_once()
            except Exception:
                logger.exception("원문 닫기 회차 예외 — 다음 회차를 이어간다")

    async def run_once(self, *, force_close: bool = False) -> int:
        """회차 1번 — 닫는 조건을 만족한 버퍼(force 면 전부)를 닫고 스레드에서 gzip 해 대기열에 넣는다.

        업로드는 기다리지 않는다(워커의 몫). 닫은 객체 수를 돌려준다.
        """
        closed = self._close_due(self._clock(), force=force_close)
        if not closed:
            return 0
        # 회차가 취소돼도(종료) gzip·넣기는 끝까지 간다 — 닫힌 버퍼는 이미 버퍼 목록에서 빠졌으므로
        # 여기서 잃으면 되돌릴 수 없다. aclose 가 이 future 를 기다린다.
        try:
            self._packing = asyncio.ensure_future(
                asyncio.to_thread(self._pack_and_enqueue, closed)
            )
            await asyncio.shield(self._packing)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 스레드 실행기 종료 등 — 이미 닫힌 줄들은 대기열에 이르지 못했다. 잃은 양을 로그에 남긴다 (§3.6)
            logger.exception(
                "원문 닫기 회차 예외 — 닫힌 객체 %d개(%d줄)를 잃는다, 다음 회차를 이어간다",
                len(closed),
                sum(len(item.lines) for item in closed),
            )
        return len(closed)

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

    def _pack_and_enqueue(self, closed: list[_Closed]) -> None:
        """스레드에서 — 닫힌 버퍼를 gzip 해 대기열 꼬리에 넣고 워커를 깨운다. 예외를 내지 않는다."""
        for item in closed:
            try:
                obj = RawObject(item.key, pack(item.lines), len(item.lines))
            except Exception:
                logger.exception(
                    "원문 객체 직렬화 실패 — 이 객체는 잃는다 key=%s", item.key
                )
                continue
            with self._changed:
                self._enqueue_locked(obj)
                self._changed.notify_all()
        self._ensure_worker()

    def _enqueue_locked(self, obj: RawObject) -> None:
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

    # --- 업로드 워커 (§3.6) ---

    def _ensure_worker(self) -> None:
        """워커 스레드는 첫 객체가 생길 때 하나만 띄운다. 데몬이라 진행 중인 put 이 프로세스 종료를 붙들지 않는다."""
        with self._changed:
            if self._closing or (self._worker is not None and self._worker.is_alive()):
                return
            self._worker = threading.Thread(
                target=self._upload_forever, name=WORKER_NAME, daemon=True
            )
            self._worker.start()

    def _upload_forever(self) -> None:
        """머리 객체를 올리고 성공하면 뺀다. 실패하면 머리에 그대로 두고 1초 뒤 다시 — 순서가 바뀌지 않는다."""
        while True:
            with self._changed:
                while not self._queue and not self._closing:
                    self._changed.wait()
                if self._closing:
                    return
                obj = self._queue[0]
            try:
                self._uploader.put(obj.key, obj.body)
            except Exception as exc:
                self._after_failure(obj, exc)
                continue
            self._after_success(obj)

    def _after_failure(self, obj: RawObject, exc: Exception) -> None:
        with self._changed:
            if not self._queue or self._queue[0] is not obj:
                return  # 올리는 사이 상한으로 버려진 객체 — 다음 머리로
            self._failures += 1
            # 횟수와 로그 줄은 같은 잠금 안에서 — 관찰자가 둘을 따로 보지 않는다
            logger.warning(
                "S3 원문 업로드 실패 (연속 %d회) key=%s: %r",
                self._failures,
                obj.key,
                exc,
            )
            # 간격이 다 지나야 다시 시도한다 — 다른 객체가 들어오는 notify 에 앞당기지 않고, 종료만 끊는다
            deadline = time.monotonic() + self._retry_interval_sec
            while not self._closing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)

    def _after_success(self, obj: RawObject) -> None:
        with self._changed:
            if not self._queue or self._queue[0] is not obj:
                return  # 올리는 사이 버려진 객체 — 대기열에는 반영할 것이 없다
            self._queue.popleft()
            self._queued_bytes -= len(obj.body)
            recovered = self._failures
            self._failures = 0
            self._changed.notify_all()  # 종료 대기가 "비었다" 를 본다
        if recovered:
            logger.info(
                "S3 원문 업로드 재개 — 연속 실패 %d회 뒤 적재 key=%s",
                recovered,
                obj.key,
            )

    # --- 종료 (§3.6) ---

    async def aclose(self) -> None:
        """닫기 회차를 멈추고 열린 버퍼를 전부 닫아 넣은 뒤, 워커가 비우기를 합계 5초 안에서 기다린다.

        넘으면 남은 객체는 버리고 경고 1줄. 워커는 데몬이라 진행 중인 put 은 기다리지 않는다.
        """
        deadline = time.monotonic() + self._drain_deadline_sec
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._packing is not None and not self._packing.done():
            with contextlib.suppress(Exception):
                await (
                    self._packing
                )  # 취소된 회차의 gzip·넣기가 끝나야 그 객체가 대기열에 있다
        await self.run_once(force_close=True)
        drained = await asyncio.to_thread(self._wait_drained, deadline)
        with self._changed:
            dropped = 0 if drained else len(self._queue)
            self._queue.clear()
            self._queued_bytes = 0
            self._closing = True
            self._changed.notify_all()
        if dropped:
            logger.warning(
                "종료 원문 업로드 데드라인(%.0f초) 초과 — 대기열 %d개 객체를 잃는다",
                self._drain_deadline_sec,
                dropped,
            )

    def _wait_drained(self, deadline: float) -> bool:
        """스레드에서 — 대기열이 빌 때까지 deadline 안에서 기다린다. 비었으면 True."""
        with self._changed:
            while self._queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(remaining)
            return True
