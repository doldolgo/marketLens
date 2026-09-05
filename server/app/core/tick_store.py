"""틱 저장 3계층의 ②·③ — 인계기(LiveStore 슬롯 → Redis)와 flusher(Redis 전량 → Influx) (스펙 009).

인계 함수는 001 의 core 계약 `handoff(tick)`(동기·무예외)을 구현한다. 틱을 §3.4 모양(gzip JSON)으로
큐에 넣고 별도 태스크가 순서대로 XADD 한다 — 틱 루프를 막지 않는다. Redis 가 안 닿으면 그 틱은
버린다(원문은 010 에 남아 재생 가능). spark 링버퍼는 Redis 성공과 무관하게 여기서 갱신한다.
flusher 는 LiveStore 도 틱 루프도 읽지 않는다 — 원천은 Redis 뿐이다.
"""

import asyncio
import contextlib
import gzip
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.influx import InfluxPoint, dw_fail_point, premium_point
from app.core.live_store import LiveStore
from app.core.models import Tick, TickRow
from app.core.redis_stream import RedisTickStream
from app.core.spark import SparkBuffer

logger = logging.getLogger("marketlens.tick_store")

QUEUE_LIMIT = 600  # 10분 — 넘치면 오래된 틱부터 버린다 (§3.3)
FLUSH_INTERVAL_SEC = 60.0
WRITE_BATCH = 5_000  # Influx 쓰기 1번의 점 수 — 모든 배치가 성공해야 회차 성공 (§3.5)


class PointWriter(Protocol):
    """Influx 쓰기 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def write(self, points: list[InfluxPoint]) -> None: ...


# --- 틱 레코드 ↔ 엔트리 `data` (§3.2·§3.4) ---


def encode_tick(tick: Tick) -> bytes:
    """`{ts, rows:[{dom,fx,base,fwd,rev}], dwFailed}` JSON 을 gzip — 계층을 넘을 때 유일한 변환."""
    record = {
        "ts": tick.ts,
        "rows": [
            {"dom": r.dom, "fx": r.fx, "base": r.base, "fwd": r.fwd, "rev": r.rev}
            for r in tick.rows
        ],
        "dwFailed": list(tick.dw_failed),
    }
    return gzip.compress(json.dumps(record, separators=(",", ":")).encode())


def decode_tick(data: bytes) -> Tick:
    record = json.loads(gzip.decompress(data))
    return Tick(
        ts=int(record["ts"]),
        rows=tuple(
            TickRow(
                dom=r["dom"],
                fx=r["fx"],
                base=r["base"],
                fwd=float(r["fwd"]),
                rev=float(r["rev"]),
            )
            for r in record["rows"]
        ),
        dw_failed=tuple(record["dwFailed"]),
    )


def tick_points(tick: Tick) -> list[InfluxPoint]:
    """틱 1개 → `premium` 조합별 1점 + `dwFailed` 거래소별 `dw_fail` 1점. time 은 전부 틱의 ts."""
    points = [
        premium_point(dom=r.dom, fx=r.fx, base=r.base, ts=tick.ts, fwd=r.fwd, rev=r.rev)
        for r in tick.rows
    ]
    points.extend(dw_fail_point(exchange=ex, ts=tick.ts) for ex in tick.dw_failed)
    return points


# --- 계층 ① → ② 인계기 (§3.3) ---


class TickRelay:
    """`handoff(tick)` 구현 — 동기·무예외. 호출은 틱 루프(직전 틱·종료 시 마지막 틱)뿐이다."""

    def __init__(
        self,
        *,
        stream: RedisTickStream | None,
        store: LiveStore,
        spark: SparkBuffer | None = None,
    ) -> None:
        self._stream = stream
        self._store = store
        self._spark = spark if spark is not None else SparkBuffer()
        self._queue: deque[tuple[int, bytes]] = deque(maxlen=QUEUE_LIMIT)
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def __call__(self, tick: Tick) -> None:
        try:
            # spark 는 Redis 와 무관한 메모리 계산 — 큐에 안 넣는 빈 틱도 갱신 대상은 아니다(rows 가 없다)
            self._spark.update(tick)
            self._store.set_spark(self._spark.snapshot())
            if not tick.rows and not tick.dw_failed:
                return  # 실을 값이 없다 — 예: 어느 국내 거래소에서도 USDT 시세를 못 받은 초
            if len(self._queue) == QUEUE_LIMIT:
                logger.warning(
                    "인계 큐 상한(%d) — 가장 오래된 틱 ts=%d 을 버린다",
                    QUEUE_LIMIT,
                    self._queue[0][0],
                )
            self._queue.append((tick.ts, encode_tick(tick)))
            self._wake.set()
        except Exception:
            logger.exception("틱 인계 처리 중 예외 — 이 틱은 버린다 ts=%d", tick.ts)

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
        """보내기 태스크를 멈추고 큐에 남은 틱을 한 번씩 보내 본다(종료 시 마지막 틱 포함)."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self.drain()


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
        """회차 1번 — 전량 읽기 → Influx 배치 쓰기 → 성공 시에만 읽은 ID 삭제. 성공 여부를 돌려준다."""
        first_id: str | None = None
        try:
            entries = await self._stream.read_all()
            first_id = entries[0].id if entries else None
            if self._failed_first_id is not None and first_id != self._failed_first_id:
                logger.warning(
                    "Redis 스트림 잘림 — 직전 실패 회차의 첫 ID %s 가 사라지고 %s 부터 읽힌다 (MAXLEN 유실)",
                    self._failed_first_id,
                    first_id,
                )
            if not entries:
                # 비면 이번 회차 생략 — 쓰기 0회, 삭제 0회. 읽기는 성공했으므로 연속 실패는 끊긴다.
                self._failures = 0
                self._failed_first_id = None
                return True
            points: list[InfluxPoint] = []
            for entry in entries:
                points.extend(tick_points(decode_tick(entry.data)))
            for i in range(0, len(points), WRITE_BATCH):
                await asyncio.to_thread(self._writer.write, points[i : i + WRITE_BATCH])
            # Redis 를 비우는 시점은 Influx 쓰기가 전부 끝난 뒤뿐이다. 1단계 뒤 들어온 엔트리는 남는다.
            await self._stream.delete([e.id for e in entries])
        except Exception as exc:
            self._failures += 1
            self._failed_first_id = (
                first_id if first_id is not None else self._failed_first_id
            )
            logger.warning("DB 저장 실패 (연속 %d회): %r", self._failures, exc)
            return False
        if self._failures:
            logger.info("DB 저장 재개 — 밀린 틱 %d건 적재", len(entries))
        self._failures = 0
        self._failed_first_id = None
        return True

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
