"""수집 실패 이력 추적기 — 거래소별 연속 실패 구간(outage) 단위 (스펙 011 §3.3~3.4).

수집 사이클이 매 회 거래소별 성공/실패를 넘기고, 여기서 구간을 열고·세고·닫는다.
정상 사이클은 기록하지 않는다. 메모리가 진실이고 Influx `collect_fail` 은 재기동 복원용이다.
core 에 사는 이유: 쓰는 쪽이 수집기(core)라 기능 폴더가 될 수 없다. 읽기 API 는 features/health.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from app.core.influx import CollectFailRow, InfluxPoint, collect_fail_point

logger = logging.getLogger("marketlens.outages")

RETENTION_MS = 24 * 3600 * 1000  # 닫힌 구간은 24시간 뒤 메모리에서 버린다
CLOSE_AFTER_SUCCESSES = 3  # 연속 성공 3사이클이면 구간을 닫는다 — 플래핑은 한 구간
RESTORE_TIMEOUT_SEC = 3.0  # 기동 복원 조회 상한 — 넘기면 빈 목록으로 기동
MESSAGE_LIMIT = 300  # 거래소 원문 body 상한


@dataclass
class Outage:
    """거래소 하나의 연속 실패 구간 1건. 유일키 = (exchange, started_at). 시각은 전부 epoch ms."""

    exchange: str
    kind: str
    started_at: int
    ended_at: int | None  # None = 진행 중
    count: int
    last_failed_at: int
    status_code: int | None
    message: str
    url: str | None
    retry_after_sec: int | None

    def to_row(self) -> CollectFailRow:
        return CollectFailRow(
            exchange=self.exchange,
            kind=self.kind,
            started_ts=self.started_at // 1000,
            count=self.count,
            last_failed_ts=self.last_failed_at // 1000,
            status_code=self.status_code,
            message=self.message,
            url=self.url,
            retry_after_sec=self.retry_after_sec,
            ended_ts=self.ended_at // 1000 if self.ended_at is not None else None,
        )

    @classmethod
    def from_row(cls, row: CollectFailRow) -> "Outage":
        return cls(
            exchange=row.exchange,
            kind=row.kind,
            started_at=row.started_ts * 1000,
            ended_at=row.ended_ts * 1000 if row.ended_ts is not None else None,
            count=row.count,
            last_failed_at=row.last_failed_ts * 1000,
            status_code=row.status_code,
            message=row.message,
            url=row.url,
            retry_after_sec=row.retry_after_sec,
        )


class OutageWriter(Protocol):
    """구간 열림·닫힘 1점 쓰기 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def write(self, points: list[InfluxPoint]) -> None: ...


class OutageReader(Protocol):
    """기동 시 24시간 복원 조회 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def query_collect_fail(self, *, start: int) -> list[CollectFailRow]: ...


class OutageTracker:
    def __init__(self, writer: OutageWriter | None = None) -> None:
        self._writer = writer
        self._outages: list[Outage] = []  # 24시간 안의 구간 전부(진행 중 포함)
        self._open: dict[str, Outage] = {}  # 거래소 → 진행 중 구간
        self._last_success: dict[str, int] = {}
        # 거래소 → (연속 성공 수, 그 연속의 첫 성공 시각) — 닫힘 판정용
        self._streak: dict[str, tuple[int, int]] = {}
        # Influx 쓰기는 사이클을 막지 않도록 큐에 넣고 별도 태스크가 순서대로 보낸다.
        # 열림 점보다 닫힘 점이 먼저 도착하면 count 가 옛값으로 덮이므로 순서가 중요하다.
        self._queue: asyncio.Queue[InfluxPoint] = asyncio.Queue()

    # --- 복원 (수집 루프 시작 전에 1회) ---

    async def restore(self, reader: OutageReader | None, now_ms: int) -> None:
        """최근 24시간 `collect_fail` 을 메모리로. 없거나·실패·3초 초과면 빈 목록 + 경고 1줄."""
        if reader is None:
            logger.warning(
                "Influx 가 없어 실패 이력을 복원하지 않는다 — 빈 목록으로 시작"
            )
            return
        start = (now_ms - RETENTION_MS) // 1000
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(reader.query_collect_fail, start=start),
                timeout=RESTORE_TIMEOUT_SEC,
            )
        except Exception as exc:
            logger.warning("실패 이력 복원 실패 — 빈 목록으로 시작: %r", exc)
            return
        restored = sorted(
            (Outage.from_row(r) for r in rows), key=lambda o: o.started_at
        )
        for o in restored:
            if o.ended_at is None:
                # 같은 거래소에 진행 중이 둘 이상이면(닫힘 쓰기 유실) 최신만 진행 중으로 둔다
                prev = self._open.get(o.exchange)
                if prev is not None:
                    prev.ended_at = prev.last_failed_at
                self._open[o.exchange] = o
            self._outages.append(o)
        self._prune(now_ms)
        logger.info(
            "실패 이력 복원: %d건 (진행 중 %d건)", len(self._outages), len(self._open)
        )

    # --- 사이클이 부른다 ---

    def record_failure(
        self,
        exchange: str,
        at_ms: int,
        *,
        kind: str,
        message: str,
        status_code: int | None,
        url: str | None,
        retry_after_sec: int | None,
    ) -> None:
        self._streak.pop(exchange, None)
        message = message[:MESSAGE_LIMIT]
        cur = self._open.get(exchange)
        if cur is not None and cur.kind != kind:
            # 원인이 바뀌면 이력에 남긴다 — 현재 구간을 이 시각에 닫고 새로 연다
            self._close(cur, at_ms)
            cur = None
        if cur is None:
            cur = Outage(
                exchange=exchange,
                kind=kind,
                started_at=at_ms,
                ended_at=None,
                count=1,
                last_failed_at=at_ms,
                status_code=status_code,
                message=message,
                url=url,
                retry_after_sec=retry_after_sec,
            )
            self._outages.append(cur)
            self._open[exchange] = cur
            self._enqueue(cur)
        else:
            cur.count += 1
            cur.last_failed_at = at_ms
            cur.status_code = status_code
            cur.message = message
            cur.url = url
            cur.retry_after_sec = retry_after_sec
        self._prune(at_ms)

    def record_success(self, exchange: str, at_ms: int) -> None:
        self._last_success[exchange] = at_ms
        self._prune(at_ms)
        cur = self._open.get(exchange)
        if cur is None:
            self._streak.pop(exchange, None)
            return
        n, first = self._streak.get(exchange, (0, at_ms))
        n += 1
        if n >= CLOSE_AFTER_SUCCESSES:
            # 종료 시각은 연속 성공의 첫 성공 사이클 — 잠깐 성공했다 다시 실패하면 같은 구간
            self._close(cur, first)
            self._streak.pop(exchange, None)
        else:
            self._streak[exchange] = (n, first)

    # --- 읽기 (features/health) ---

    def open_outage(self, exchange: str) -> Outage | None:
        return self._open.get(exchange)

    def outages(self) -> list[Outage]:
        """24시간 안의 구간 전부 — started_at 내림차순."""
        return sorted(self._outages, key=lambda o: o.started_at, reverse=True)

    def last_success_at(self, exchange: str) -> int | None:
        return self._last_success.get(exchange)

    # --- Influx 쓰기 태스크 ---

    async def run_writer_loop(self) -> None:
        """앱과 함께 돌고 종료 시 취소된다. 큐의 점을 순서대로 1점씩 쓴다."""
        while True:
            point = await self._queue.get()
            await self._write_one(point)

    async def flush(self) -> None:
        """큐에 쌓인 점을 지금 전부 쓴다 — 테스트·종료용."""
        while not self._queue.empty():
            await self._write_one(self._queue.get_nowait())

    async def _write_one(self, point: InfluxPoint) -> None:
        if self._writer is None:
            return
        try:
            await asyncio.to_thread(self._writer.write, [point])
        except Exception as exc:
            # 쓰기 실패는 로그 1줄 후 무시 — 열 때 실패해도 닫을 때의 쓰기가 점을 만든다
            logger.warning("collect_fail 쓰기 실패: %r", exc)

    # --- 내부 ---

    def _close(self, outage: Outage, ended_at: int) -> None:
        outage.ended_at = ended_at
        self._open.pop(outage.exchange, None)
        self._enqueue(outage)

    def _enqueue(self, outage: Outage) -> None:
        if self._writer is not None:
            self._queue.put_nowait(collect_fail_point(outage.to_row()))

    def _prune(self, now_ms: int) -> None:
        cutoff = now_ms - RETENTION_MS
        self._outages = [
            o for o in self._outages if o.ended_at is None or o.ended_at >= cutoff
        ]
