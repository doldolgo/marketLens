"""틱 저장 3계층의 ②·③ — 인계기(LiveStore 슬롯 → Redis)와 flusher(Redis 전량 → Influx) (스펙 009).

인계 함수는 001 의 core 계약 `handoff(tick)`(동기·무예외)을 구현한다. 틱을 §3.4 모양(gzip JSON, 레벨 6)으로
큐에 넣고 별도 태스크가 순서대로 XADD 한다 — 틱 루프를 막지 않는다. Redis 가 안 닿으면 그 틱은
버린다(원문은 010 에 남아 재생 가능). spark 링버퍼는 Redis 성공과 무관하게 여기서 갱신한다.
flusher 는 LiveStore 도 틱 루프도 읽지 않는다 — 원천은 Redis 뿐이다. 점 객체를 만들지 않고 엔트리에서 line
protocol 줄을 바로 만들어 흘려 쓴다(§3.5).
"""

import asyncio
import contextlib
import gzip
import json
import logging
import math
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.influx import (
    dw_fail_point,
    premium_head,
    premium_line,
    premium_line_text,
    to_line,
)
from app.core.live_store import LiveStore
from app.core.models import Tick
from app.core.redis_stream import (
    PAGE,
    SOCKET_TIMEOUT_SEC,
    RedisTickStream,
    StreamEntry,
)
from app.core.spark import SparkBuffer

logger = logging.getLogger("marketlens.tick_store")

QUEUE_LIMIT = 600  # 10분 — 넘치면 오래된 틱부터 버린다 (§3.3)
DRAIN_DEADLINE_SEC = (
    SOCKET_TIMEOUT_SEC  # 종료 시 큐 비우기 총 상한 = 명령 타임아웃 1회분 (§3.3)
)
FLUSH_INTERVAL_SEC = 60.0
WRITE_BATCH = 5_000  # Influx 쓰기 1번의 줄 수 — 모든 배치가 성공해야 페이지 성공 (§3.5)
# gzip 레벨 6(2026-09-28 사람 결정) — 9 는 압축 CPU 가 약 2배인데 크기는 1.5% 작을 뿐이다. 틱 루프 동기 구간에서 돈다
GZIP_LEVEL = 6


class PointWriter(Protocol):
    """Influx 쓰기 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def write_lines(self, lines: list[str], bucket: str | None = None) -> None: ...


# --- 틱 레코드 → 엔트리 `data` (§3.2·§3.4) ---


def encode_tick(tick: Tick) -> bytes:
    """`{ts, rows:[{dom,fx,base,fwd,rev}], dwFailed}` JSON 을 gzip — 계층을 넘을 때 유일한 변환.

    gzip 머리의 시각은 0 으로 둔다 — 같은 틱은 언제 인코딩해도 같은 바이트다. 풀면 레벨과 무관하게 같은 JSON 이다.
    """
    record = {
        "ts": tick.ts,
        "rows": [
            {"dom": r.dom, "fx": r.fx, "base": r.base, "fwd": r.fwd, "rev": r.rev}
            for r in tick.rows
        ],
        "dwFailed": list(tick.dw_failed),
    }
    return gzip.compress(
        json.dumps(record, separators=(",", ":")).encode(),
        compresslevel=GZIP_LEVEL,
        mtime=0,
    )


# --- 계층 ① → ② 인계기 (§3.3) ---

_INF = math.inf


class TickRelay:
    """`handoff(tick)` 구현 — 동기·무예외. 호출은 틱 루프(직전 틱·종료 시 마지막 틱)뿐이다."""

    def __init__(
        self,
        *,
        stream: RedisTickStream | None,
        store: LiveStore,
        spark: SparkBuffer | None = None,
        drain_deadline_sec: float = DRAIN_DEADLINE_SEC,
    ) -> None:
        self._stream = stream
        self._store = store
        self._spark = spark if spark is not None else SparkBuffer()
        self._drain_deadline_sec = drain_deadline_sec
        self._queue: deque[tuple[int, bytes]] = deque(maxlen=QUEUE_LIMIT)
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # 레코드 JSON 을 조합마다 기억한다(2026-10-09) — 조합 → 행 글의 머리('{"dom":…,"base":…,"fwd":'),
        # 조합 → (fwd, rev, 행 글). 매초 1,800여 행 중 값이 바뀌는 조합은 약 40% 라 나머지는 직전 글을 다시 쓴다.
        # 조합 수만큼(약 1,800개, 0.8MB) 자라고 사라진 조합도 남지만 작다. 틱 루프(이벤트 루프)만 만진다
        self._row_heads: dict[tuple[str, str, str], str] = {}
        self._row_texts: dict[tuple[str, str, str], tuple[float, float, str]] = {}

    def __call__(self, tick: Tick) -> None:
        try:
            # spark 는 Redis 와 무관한 메모리 계산 — 큐에 안 넣는 빈 틱도 갱신 대상은 아니다(rows 가 없다)
            self._spark.update(tick)
            self._spark.publish(self._store)
            if not tick.rows and not tick.dw_failed:
                return  # 실을 값이 없다 — 예: 어느 국내 거래소에서도 USDT 시세를 못 받은 초
            if len(self._queue) == QUEUE_LIMIT:
                logger.warning(
                    "인계 큐 상한(%d) — 가장 오래된 틱 ts=%d 을 버린다",
                    QUEUE_LIMIT,
                    self._queue[0][0],
                )
            self._queue.append((tick.ts, self._encode(tick)))
            self._wake.set()
        except Exception:
            logger.exception("틱 인계 처리 중 예외 — 이 틱은 버린다 ts=%d", tick.ts)

    def _encode(self, tick: Tick) -> bytes:
        """`encode_tick(tick)` 과 같은 바이트 — 값이 직전 틱과 같은 조합은 행 글을 다시 쓰지 않는다.

        gzip 레벨·머리 시각과 '틱 동기 구간에서 인코딩'(§3.4)은 그대로다. 빠른 길로 못 쓰는 틱은 `encode_tick` 으로 간다.
        """
        text = self._record_json(tick)
        if text is None:
            return encode_tick(tick)
        return gzip.compress(text.encode(), compresslevel=GZIP_LEVEL, mtime=0)

    def _record_json(self, tick: Tick) -> str | None:
        """`encode_tick` 이 gzip 에 넣는 JSON 과 같은 글 — 못 쓰는 틱이면 None (§3.4).

        행 글은 `{"dom":…,"fx":…,"base":…,"fwd":…,"rev":…}` 이고 이름 셋은 조합마다 한 번 `json.dumps` 로
        이스케이프해 둔다(인계 레코드는 ensure_ascii 기본값이라 같은 함수로 쓴다). fwd·rev 는 `repr` — json 의 float
        표기와 같다. 같은 조합의 fwd·rev 가 직전과 같으면(0.0 이면 부호까지) 직전 행 글을 그대로 쓴다 — 같은 float 는
        같은 글자다. 순서가 중요하다: 타입을 먼저 본다(float 2.0 뒤에 int 2 나 True 가 오면 2 == 2.0 이라도 json 은
        "2"·"true" 로 쓴다). float 가 아니거나 유한하지 않거나(json 은 NaN·Infinity 로 쓴다) ts 가 int 가 아니면 None.
        """
        ts = tick.ts
        if type(ts) is not int:
            return None
        heads = self._row_heads
        cache = self._row_texts
        parts: list[str] = []
        append = parts.append
        for r in tick.rows:
            fwd = r.fwd
            rev = r.rev
            if type(fwd) is not float or type(rev) is not float:
                return None
            key = (r.dom, r.fx, r.base)
            hit = cache.get(key)
            if (
                hit is not None
                and hit[0] == fwd
                and hit[1] == rev
                and (fwd != 0.0 or math.copysign(1.0, hit[0]) == math.copysign(1.0, fwd))
                and (rev != 0.0 or math.copysign(1.0, hit[1]) == math.copysign(1.0, rev))
            ):
                append(hit[2])
                continue
            if not (-_INF < fwd < _INF and -_INF < rev < _INF):
                return None
            head = heads.get(key)
            if head is None:
                head = heads[key] = (
                    '{"dom":'
                    + json.dumps(r.dom)
                    + ',"fx":'
                    + json.dumps(r.fx)
                    + ',"base":'
                    + json.dumps(r.base)
                    + ',"fwd":'
                )
            row_text = f'{head}{fwd!r},"rev":{rev!r}}}'
            cache[key] = (fwd, rev, row_text)
            append(row_text)
        dw = json.dumps(list(tick.dw_failed), separators=(",", ":"))
        return f'{{"ts":{ts!r},"rows":[{",".join(parts)}],"dwFailed":{dw}}}'

    @property
    def pending(self) -> int:
        return len(self._queue)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run_sender_loop())

    async def run_sender_loop(self) -> None:
        """큐가 차면 깨어나 순서대로 XADD. 앱과 함께 돌고 종료 시 취소된다."""
        while True:
            await self._wake.wait()
            self._wake.clear()
            await self.drain()

    async def drain(self) -> int:
        """큐의 틱을 순서대로 Redis 에 보낸다. 실패한 틱은 버리고 경고 1줄. 보낸 수를 돌려준다."""
        sent = 0
        while self._queue:
            ts, data = self._queue.popleft()
            if self._stream is None:
                logger.warning("Redis 가 없어 틱을 버린다 ts=%d", ts)
                continue
            try:
                await self._stream.add(ts, data)
                sent += 1
            except Exception as exc:
                logger.warning("Redis 인계 실패 — 틱을 버린다 ts=%d: %r", ts, exc)
        return sent

    async def aclose(self) -> None:
        """보내기 태스크를 멈추고 큐에 남은 틱을 한 번씩 보내 본다(종료 시 마지막 틱 포함).

        총 DRAIN_DEADLINE_SEC 안에서만 — Redis 가 무응답이면 틱마다 타임아웃을 기다리게 되므로
        종료를 큐 길이만큼 붙들지 않는다. 넘으면 남은 틱은 버리고 개수를 경고 1줄로 남긴다.
        """
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        try:
            await asyncio.wait_for(self.drain(), self._drain_deadline_sec)
        except TimeoutError:
            dropped = len(self._queue)
            self._queue.clear()
            logger.warning(
                "종료 큐 비우기 데드라인(%.0f초) 초과 — 남은 틱 %d건을 버린다",
                self._drain_deadline_sec,
                dropped,
            )


# --- 계층 ② → ③ flusher (§3.5) ---


class Flusher:
    def __init__(
        self,
        *,
        stream: RedisTickStream,
        writer: PointWriter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._stream = stream
        self._writer = writer
        self._sleep = sleep
        self._failures = 0  # 연속 실패 회차 수
        self._failed_first_id: str | None = None  # 실패한 회차의 첫 ID — 잘림 감지 기준
        self._task: asyncio.Task[None] | None = None
        # (dom, fx, base) → `premium` 줄 머리. 조합당 한 번만 이스케이프한다(1,458개 수준 — 사라진 조합도 남지만 작다).
        # 페이지 쓰기 스레드만 만진다(회차는 한 번에 하나)
        self._heads: dict[tuple[str, str, str], str] = {}

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """기동 후 60초 잔 뒤 첫 회차, 이후 60초마다. 회차 안 예외는 밖으로 나오지 않는다."""
        while True:
            await self._sleep(FLUSH_INTERVAL_SEC)
            await self.flush_once()

    async def flush_once(self) -> bool:
        """회차 1번 = 페이지 반복 — 페이지 읽기 → Influx 배치 쓰기 → 그 페이지 ID 만 삭제 → 다음 페이지.

        메모리에 드는 것은 한 번에 한 페이지뿐이다. 어느 페이지든 실패하면 회차 중단(앞서 지운 페이지는
        이미 Influx 에 있다). 성공 여부를 돌려준다.
        """
        after: str | None = None  # 직전 페이지의 마지막 ID
        moved = 0  # 이 회차가 옮긴 틱 수
        page: list[StreamEntry] = []
        deleting = (
            False  # XDEL 단계에서 난 실패는 잘림 판정 기준을 남기지 않는다 (§3.5)
        )
        try:
            while True:
                page = []  # 읽기가 실패하면 이 페이지의 첫 ID 는 모른다
                page = await self._stream.read_page(after)
                if after is None:
                    self._check_truncation(page[0].id if page else None)
                if not page:
                    break  # 첫 페이지가 비면 회차 생략(쓰기 0회, XDEL 0회) — 마지막 페이지 뒤면 끝
                await asyncio.to_thread(self._write_page, page)
                # Redis 를 비우는 시점은 그 엔트리의 Influx 쓰기가 끝난 뒤뿐이다. 읽은 뒤 들어온 엔트리는 남는다.
                deleting = True
                await self._stream.delete([e.id for e in page])
                deleting = False
                moved += len(page)
                if len(page) < PAGE:
                    break
                after = page[-1].id
        except Exception as exc:
            self._failures += 1
            if deleting:
                self._failed_first_id = None
            elif page:
                self._failed_first_id = page[0].id  # 지우지 못한 첫 엔트리
            elif after is not None:
                self._failed_first_id = (
                    None  # 읽기 실패인데 앞 페이지를 지웠다 — 기준을 모른다
                )
            logger.warning("DB 저장 실패 (연속 %d회): %r", self._failures, exc)
            return False
        if self._failures and moved:
            logger.info("DB 저장 재개 — 밀린 틱 %d건 적재", moved)
        self._failures = 0
        self._failed_first_id = None
        return True

    def _check_truncation(self, first_id: str | None) -> None:
        """실패 회차가 지우지 못한 첫 ID 부터 다시 읽혀야 한다 — 다르면(빈 스트림 포함) MAXLEN 잘림."""
        if self._failed_first_id is not None and first_id != self._failed_first_id:
            logger.warning(
                "Redis 스트림 잘림 — 직전 실패 회차의 첫 ID %s 가 사라지고 %s 부터 읽힌다 (MAXLEN 유실)",
                self._failed_first_id,
                first_id,
            )

    def _write_page(self, page: list[StreamEntry]) -> None:
        """스레드에서 — 틱을 하나씩 풀어 줄을 바로 만들고 WRITE_BATCH 줄이 차면 그 자리에서 쓴다 (§3.5).

        페이지를 점 목록으로 한꺼번에 펼치지 않는다 — 1,458조합 × 1,000틱이면 점 객체 146만 개(+1GB)가 한꺼번에
        산다. 메모리는 배치 하나 크기에 비례하고, Influx 가 막혀 있으면 첫 배치에서 실패해 나머지 틱은 풀지 않는다.
        순서(틱마다 `premium` 행 순서 → `dw_fail`)·배치 경계·본문 바이트는 페이지 전체를 점으로 만들어 5,000점씩
        쓴 것과 같다. 예외는 그대로 올라가 페이지 실패가 된다(모든 배치가 성공해야 XDEL).
        """
        heads = self._heads
        write = self._writer.write_lines
        buf: list[str] = []
        for entry in page:
            # 숫자 글자는 float 로 바꾸지 않고 글자 그대로 받는다(parse_float=str) — 인계기가 json.dumps(float repr)로 쓴
            # 글자라 줄에 다시 repr 할 글자와 같다(premium_line_text). 회차마다 11만 줄의 글자 → float → 글자 왕복을
            # 덜어 스레드가 GIL 을 잡는 시간이 절반이 된다(2026-10-09). 이 방법은 JSON 숫자와 문자열 리터럴을 가리지
            # 못한다 — fwd·rev 를 쓰는 곳은 `encode_tick` 하나이고 늘 float 라 문자열이 올 일이 없다
            record = json.loads(gzip.decompress(entry.data), parse_float=str)
            ts_value = record["ts"]
            # ts 는 늘 정수 글자지만 소수 글자였다면 기준(json 의 float → int)과 같게 읽는다
            ts = int(float(ts_value) if type(ts_value) is str else ts_value)
            tail = f" {ts}"
            for r in record["rows"]:
                key = (r["dom"], r["fx"], r["base"])
                head = heads.get(key)
                if head is None:
                    head = premium_head(*key)
                    heads[key] = head
                fwd = r["fwd"]
                rev = r["rev"]
                if type(fwd) is str and type(rev) is str:
                    buf.append(premium_line_text(head, fwd, rev, tail))
                else:
                    # 글자가 아닌 값(정수·NaN·Infinity 상수) — 지금처럼 float 로 바꿔 쓴다. 표기 규칙을 여기 두지 않게
                    # Influx 모듈 함수를 그대로 부른다(float(글자) 의 repr 은 그 글자라 섞여도 같은 바이트다)
                    buf.append(premium_line(head, float(fwd), float(rev), ts))
                if len(buf) == WRITE_BATCH:
                    write(buf)
                    buf = []
            for exchange in record["dwFailed"]:
                buf.append(to_line(dw_fail_point(exchange=exchange, ts=ts)))
                if len(buf) == WRITE_BATCH:
                    write(buf)
                    buf = []
        if buf:
            write(buf)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
