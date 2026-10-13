"""거래소 원문 아카이브 — 원문 싱크·분 창 버퍼·표본화·객체 조립·닫기 회차·업로드 워커 (스펙 010).

기록 함수는 001 의 core 계약 `record(exchange, source, received_at_ms, payload, key)`(동기·무예외)을
구현한다. 받은 문자열을 그 거래소의 UTC 분 창 버퍼에 참조로만 붙이는 메모리 작업뿐이라 수신 경로를 막지 않는다.
`key` 가 있는 원문(시세 프레임)은 창 안에서 `(source, key)` 마다 마지막 1건만 남기고, 없는 원문은 전량 남긴다.
닫기 회차(태스크, 매초)가 지난 창을 닫고, 스레드에서 살아남은 원문만 정렬·줄 조립(유효성 검사 포함)해 gzip 한 뒤
대기열에 넣는다. 업로드 워커(데몬 스레드 하나)가 대기열 머리부터 S3 에 올린다 — 둘은 잠금으로만 만나고
닫기 주기는 업로드 결과와 무관하다.
어떤 실패도 수집·/spreads·Redis·Influx 경로에 번지지 않는다. S3 를 읽는 코드는 없다.
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

WINDOW_MS = 60_000  # UTC 분 창 — 창 번호 = receivedAt // WINDOW_MS (§3.5)
QUEUE_LIMIT_BYTES = 256 * 1024 * 1024  # 대기열 상한 — 거래소 합산 압축 후 (§3.6)
LOOP_INTERVAL_SEC = 1.0  # 닫기 회차 주기
RETRY_INTERVAL_SEC = 1.0  # 실패한 머리 객체를 워커가 다시 시도하는 간격 (§3.6)
DRAIN_DEADLINE_SEC = 5.0  # 종료 시 대기열 비우기 합계 상한 (§3.6)
GZIP_LEVEL = 6  # 레벨 9 는 3배 넘게 걸리고 이득은 1% 미만 (§3.5)
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


def _drop_object(_pairs: list[tuple[str, object]]) -> None:
    """`object_pairs_hook` — 객체를 만들지 않고 버린다. 검사에는 파싱 성공 여부만 필요하다."""
    return None


def _is_verbatim_json(payload: str) -> bool:
    """원문을 그대로 이어 붙여도 되는가 — 표준 JSON 이고 최상위가 객체·배열이며 줄바꿈이 없다.

    분마다 살아남는 원문의 대부분(바이트 기준)은 목록 REST 본문(바이낸스 exchangeInfo 2.5MB 등)이다.
    훅 없이 파싱하면 본문마다 dict 수만 개를 만들었다 버리고(2.5MB 한 건에 메모리 최고 8MB, 세대0 수집 여러 번),
    그 C 호출 한 번(Mac 에서 11~14ms) 동안 GIL 을 놓지 않아 닫기 스레드가 도는 사이 이벤트 루프가 그만큼 멈춘다.
    `object_pairs_hook` 으로 객체마다 None 을 돌려주면 트리가 남지 않고(같은 본문 0.02MB), 훅이 파이썬 함수라
    긴 파싱 도중에도 GIL 을 넘길 지점이 생긴다 — 루프가 기다리는 최장 시간이 C 호출 길이에서 전환 간격(5ms)
    수준으로 준다(캡처 60초 재생, Mac 실측: 메인 스레드가 GIL 을 못 받은 최장 간격 14.8→6.4ms,
    asyncio 1ms 타이머 늦음 최대 약 20→15ms). 검사 CPU 도 분당 약 11% 준다.
    숫자 변환·문자열 디코드·비표준 상수 거부는 같은 스캐너가 그대로 하므로 판정은 바뀌지 않는다.
    """
    if "\n" in payload or "\r" in payload:
        return False
    try:
        json.loads(
            payload, parse_constant=_reject_constant, object_pairs_hook=_drop_object
        )
    except ValueError:
        return False
    except RecursionError:
        # 훅 호출이 C 재귀 한도 바로 앞에서 한 단을 더 써서, 객체가 한도 직전 깊이(약 1만 단)로 중첩된 원문은
        # 훅 쪽만 RecursionError 를 낸다. 훅 없는 판정으로 다시 봐서 어느 깊이에서든 기준과 같게 한다
        # (거기서도 RecursionError 면 지금처럼 밖으로 나가 그 줄만 버린다). 거래소 원문에는 없는 병적인 입력이다.
        return _is_verbatim_json_tree(payload)
    # 파싱이 성공했으면 최상위 값의 종류는 첫 글자로 정해진다. 앞에서 줄바꿈을 걸렀으므로 앞에 올 수 있는
    # JSON 공백은 공백·탭뿐이다 — 앞 공백이 없으면 lstrip 은 같은 객체를 돌려줘 복사도 없다.
    return payload.lstrip(" \t")[:1] in ("{", "[")


def _is_verbatim_json_tree(payload: str) -> bool:
    """훅 없이 값 트리를 만들어 보는 판정 — `_is_verbatim_json` 이 RecursionError 를 만났을 때만 쓴다."""
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


def object_key(exchange: str, window: int) -> str:
    """`raw/exchange=<id>/dt=YYYY-MM-DD/hh=HH/YYYYMMDDTHHMM00Z.jsonl.gz` — 전부 UTC, 시각 = 창의 시작(분)."""
    at = datetime.fromtimestamp(window * WINDOW_MS / 1000, tz=UTC)
    return (
        f"{KEY_PREFIX}/exchange={exchange}/dt={at:%Y-%m-%d}/hh={at:%H}/"
        f"{at:%Y%m%dT%H%M}00Z.jsonl.gz"
    )


def pack(lines: list[bytes]) -> bytes:
    """줄들을 순서대로 이어 gzip — 레벨·mtime 0 고정이라 같은 입력은 바이트까지 같다 (§3.5)."""
    return gzip.compress(b"".join(lines), compresslevel=GZIP_LEVEL, mtime=0)


# --- 분 창 버퍼와 닫힌 객체 (§3.5) ---


# 받은 원문의 참조 = (receivedAt, seq, source, payload) — 줄은 창을 닫을 때 살아남은 것만 조립한다 (§3.5).
# 시세 프레임은 분당 (source, key) 마지막 1건만 남으므로 기록마다 줄을 만들면 99% 이상을 버린다.
# 클래스(slots 데이터클래스)가 아니라 튜플인 것은 수신 경로에서 프레임마다(초당 수천 번) 만들어지기 때문이다 —
# 파이썬 __init__ 호출이 없어 기록 1회가 약 90~130ns 짧다(캡처 60초 재생, Mac 실측). seq 는 기록 순번이라
# receivedAt 이 같을 때의 순서를 정하고, 아카이브 안에서 유일하다.
_Entry = tuple[int, int, str, str]


@dataclass
class _Buffer:
    """거래소 하나의 분 창 하나. `keyed` 는 (source, key) 당 마지막 1건, `plain` 은 key 없는 원문 전량."""

    keyed: dict[tuple[str, str], _Entry] = field(default_factory=dict)
    plain: list[_Entry] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.keyed) + len(self.plain)

    def entries(self) -> list[_Entry]:
        """줄 순서 = receivedAt 오름차순, 같으면 기록 순 (§3.5).

        튜플 그대로 정렬한다 — 앞 두 칸이 (receivedAt, seq) 이고 seq 가 유일해 비교가 source·payload 까지
        가지 않으므로 key 함수를 쓴 정렬과 순서가 같다. 닫힌 버퍼를 스레드에서 조립할 때 부른다.
        """
        return sorted([*self.keyed.values(), *self.plain])


@dataclass(frozen=True)
class _Closed:
    """닫혔지만 아직 정렬·줄 조립·gzip 전인 버퍼 — 셋 다 스레드에서 한다.

    버퍼는 `_buffers`·`_open` 에서 이미 떼어 냈으므로 더는 기록이 붙지 않는다 — 스레드가 잠금 없이 읽어도 된다.
    """

    exchange: str
    key: str
    buf: _Buffer


def _format_entries(exchange: str, entries: list[_Entry]) -> list[bytes]:
    """살아남은 원문을 순서대로 줄로 — 한 줄의 실패는 그 줄만 버리고 로그, 객체는 나머지로 만든다."""
    lines: list[bytes] = []
    # format_line 은 부를 때마다 모듈 전역에서 찾는다 — 테스트가 바꿔치기해 조립 횟수·실패를 본다
    for received_at_ms, _seq, source, payload in entries:
        try:
            lines.append(format_line(exchange, source, received_at_ms, payload))
        except Exception:
            logger.exception(
                "원문 줄 조립 실패 — 이 줄은 버린다 %s %s receivedAt=%d",
                exchange,
                source,
                received_at_ms,
            )
    return lines


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
        # 버퍼는 이벤트 루프 스레드만 만진다 — 기록 함수와 닫기 회차. 키 = (거래소, 창 번호).
        self._buffers: dict[tuple[str, int], _Buffer] = {}
        # 거래소마다 마지막으로 기록한 (창 번호, 버퍼) — 평상시 거래소당 열린 창은 하나라, 같은 창이면
        # (거래소, 창) 튜플 키를 만들어 `_buffers` 를 찾는 일을 건너뛴다. 닫기 회차가 버퍼를 떼어 낼 때
        # 그 버퍼를 가리키는 칸도 지운다(`_forget`) — 남겨 두면 늦게 온 원문이 닫힌 버퍼에 붙어 사라진다.
        self._open: dict[str, tuple[int, _Buffer]] = {}
        self._seq = 0
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
        self,
        exchange: str,
        source: str,
        received_at_ms: int,
        payload: str,
        key: str | None = None,
    ) -> None:
        """동기·무예외·즉시 반환 — 받은 문자열을 그 거래소의 분 창 버퍼에 참조로 붙인다.

        `key` 가 있으면 창 안의 같은 (source, key) 원문을 이것으로 바꾼다(표본화 — §3.5).
        줄 조립(JSON 유효성 검사·머리 직렬화)은 여기서 하지 않는다 — 커넥터가 이미 파싱한 프레임을
        수신 경로에서 다시 파싱하지 않고, 닫을 때 살아남은 원문에만 한다.

        이벤트 루프 스레드에서만 부른다(커넥터·입출금 조회기가 전부 코루틴이다). 닫힌 버퍼를 스레드가 잠금 없이
        읽어도 되는 근거가 이것이다 — 다른 스레드에서 부르면 닫기 회차가 떼어 낸 버퍼에 원문이 붙을 수 있고,
        스레드가 그 버퍼를 정렬하던 중이면 dict 크기가 바뀌어 객체 하나를 통째로 잃는다.
        """
        try:
            window = received_at_ms // WINDOW_MS
            opened = self._open.get(exchange)
            if opened is not None and opened[0] == window:
                buf = opened[1]
            else:
                # 거래소의 첫 기록·분이 바뀐 첫 기록·시계 역행 — 지금까지처럼 (거래소, 창) 버퍼를 찾거나 만든다
                buf = self._buffers.get((exchange, window))
                if buf is None:
                    buf = _Buffer()
                    self._buffers[exchange, window] = buf
                self._open[exchange] = (window, buf)
            self._seq = seq = self._seq + 1
            if key is None:
                buf.plain.append((received_at_ms, seq, source, payload))
            else:
                buf.keyed[source, key] = (received_at_ms, seq, source, payload)
        except Exception:
            logger.exception(
                "원문 기록 중 예외 — 이 원문은 버린다 %s %s", exchange, source
            )

    def buffered(self, exchange: str) -> int:
        """아직 닫히지 않은 그 거래소 버퍼의 원문 수(표본화 후, 열린 창 전부 합산)."""
        return sum(len(buf) for (ex, _), buf in self._buffers.items() if ex == exchange)

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
        """회차 1번 — 지난 창의 버퍼(force 면 전부)를 닫고 스레드에서 gzip 해 대기열에 넣는다.

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
                sum(len(item.buf) for item in closed),
            )
        return len(closed)

    def _close_due(self, now_ms: int, *, force: bool) -> list[_Closed]:
        """지금 시각의 창보다 앞선 창(force 면 전부)을 닫는다 — 닫힌 순서 = 거래소별 창 번호 순.

        루프에서는 버퍼를 떼어 내기만 하고, 줄 순서 정렬은 조립과 함께 스레드에서 한다 — 분 경계 회차의
        루프 몫이 약 1.2ms 에서 0.1ms 로 준다(캡처 60초 재생, Mac 실측). 정렬 CPU 는 스레드로 옮겨질 뿐이다.
        """
        current = now_ms // WINDOW_MS
        closed: list[_Closed] = []
        for (exchange, window), buf in sorted(self._buffers.items()):
            if not len(buf):
                del self._buffers[exchange, window]
                self._forget(exchange, buf)
                continue
            if force or window < current:
                del self._buffers[exchange, window]
                self._forget(exchange, buf)
                closed.append(_Closed(exchange, object_key(exchange, window), buf))
        return closed

    def _forget(self, exchange: str, buf: _Buffer) -> None:
        """떼어 낸 버퍼를 열린 창 기억에서도 지운다 — 그 창에 늦게 온 원문은 새 버퍼로 가서 다음 회차에 올라간다.

        기억이 다른(더 새) 버퍼를 가리키면 그대로 둔다 — 다음 분 창이 이미 열린 거래소의 앞 창을 닫는 평상시 경우다.
        """
        opened = self._open.get(exchange)
        if opened is not None and opened[1] is buf:
            del self._open[exchange]

    def _pack_and_enqueue(self, closed: list[_Closed]) -> None:
        """스레드에서 — 닫힌 버퍼의 원문을 정렬·줄 조립·gzip 해 대기열 꼬리에 넣고 워커를 깨운다. 예외를 내지 않는다."""
        for item in closed:
            try:
                lines = _format_entries(item.exchange, item.buf.entries())
                obj = RawObject(item.key, pack(lines), len(lines))
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
