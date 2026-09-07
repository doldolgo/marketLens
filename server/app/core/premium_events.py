"""김프/역프 사건 감지기 — 틱이 판정하고 Influx `premium_event` 에 1건 1점 (스펙 013 §3.2~3.3).

틱 루프가 매초 현재 틱을 넘기고, 여기서 조합 `(dom, fx, base, dir)` 마다 사건을 열고·갱신하고·닫는다.
메모리에 드는 것은 열린 사건뿐이고 닫힌 사건은 Influx 가 진실이다.
core 에 사는 이유: 쓰는 쪽이 틱 루프(core)라 기능 폴더가 될 수 없다. 읽기 API 는 features/history.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.influx import InfluxPoint, PremiumEventRow, premium_event_point
from app.core.models import Tick

logger = logging.getLogger("marketlens.premium_events")

# §3.2 기준은 고정 — 조정 UI 없음. 점에도 같이 남긴다(나중에 바뀌어도 과거 사건의 의미가 남게)
ENTER_PERCENT = 1.0  # 진입: 값이 이 이상이면 연다
EXIT_PERCENT = 0.5  # 종료: 값이 이 이하가 되면 닫는다 (사이는 히스테리시스로 이어진다)
MIN_DURATION_SEC = 60  # 이 이하로 관측 중 닫힌 사건은 스파이크 — 없던 것으로
MAX_GAP_SEC = 600  # 결측허용: 마지막 관측 뒤 이만큼 행이 안 돌아오면 last_ts 로 닫는다
WRITE_INTERVAL_SEC = 60  # 열린 사건의 Influx 갱신·실패 재시도 주기
RESTORE_WINDOW_SEC = 7 * 86_400  # 기동 복원이 읽는 start_ts 범위
RESTORE_TIMEOUT_SEC = 3.0  # 기동 복원 조회 상한 — 넘기면 빈 상태로 기동
PENDING_LIMIT = 1_000  # 미전송 점 상한 — 넘치면 오래된 것부터 버린다

Key = tuple[str, str, str, str]  # (dom, fx, base, dir)


@dataclass
class PremiumEvent:
    """사건 1건 — 유일키 = (dom, fx, base, dir, start_ts). 시각은 전부 epoch 초."""

    dom: str
    fx: str
    base: str
    dir: str  # kimp | reverse
    start_ts: int
    end_ts: int | None  # None = 진행 중
    max_percent: float
    max_ts: int
    last_ts: int
    samples: int
    written: bool = False  # 열린 지 60초를 넘겨 Influx 에 점이 있(어야 하)는가

    @property
    def key(self) -> Key:
        return (self.dom, self.fx, self.base, self.dir)

    def to_row(self) -> PremiumEventRow:
        return PremiumEventRow(
            dom=self.dom,
            fx=self.fx,
            base=self.base,
            dir=self.dir,
            start_ts=self.start_ts,
            end_ts=self.end_ts or 0,
            duration_seconds=(self.end_ts - self.start_ts) if self.end_ts else 0,
            max_percent=self.max_percent,
            max_ts=self.max_ts,
            last_ts=self.last_ts,
            samples=self.samples,
            enter_percent=ENTER_PERCENT,
            exit_percent=EXIT_PERCENT,
        )

    @classmethod
    def from_row(cls, row: PremiumEventRow) -> "PremiumEvent":
        return cls(
            dom=row.dom,
            fx=row.fx,
            base=row.base,
            dir=row.dir,
            start_ts=row.start_ts,
            end_ts=row.end_ts or None,
            max_percent=row.max_percent,
            max_ts=row.max_ts,
            last_ts=row.last_ts,
            samples=row.samples,
            written=True,
        )


class EventWriter(Protocol):
    """점 쓰기 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def write(self, points: list[InfluxPoint]) -> None: ...


class EventReader(Protocol):
    """기동 시 7일 복원 조회 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def query_premium_events(
        self,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        dir: str | None = None,
        base: str | None = None,
    ) -> list[PremiumEventRow]: ...


class PremiumEventDetector:
    def __init__(
        self,
        writer: EventWriter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._writer = writer
        self._clock = clock
        self._open: dict[Key, PremiumEvent] = {}
        # 쓸 점 — (키, start_ts) 당 최신 1점. 열림·갱신·닫힘·실패분이 전부 여기 모이고 한 회차에 한 번에 쓴다.
        # 같은 키 덮어쓰기라 마지막 상태만 보내면 되고, 삽입 순서가 곧 쓰기 순서다.
        self._pending: dict[tuple[Key, int], InfluxPoint] = {}
        self._wake = asyncio.Event()
        self._failed_at: float | None = (
            None  # 마지막 쓰기 실패 시각 — 60초 안엔 재시도 안 함
        )
        self._last_refresh_ts = 0  # 열린 사건 60초 갱신의 마지막 틱

    # --- 복원 (틱 루프 시작 전에 1회) ---

    async def restore(self, reader: EventReader | None, now_sec: int) -> None:
        """최근 7일의 `end_ts 0` 점을 메모리로. 없거나·실패·3초 초과면 빈 상태 + 경고 1줄."""
        if reader is None:
            logger.warning("Influx 가 없어 사건을 복원하지 않는다 — 빈 상태로 시작")
            return
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(
                    reader.query_premium_events,
                    start=now_sec - RESTORE_WINDOW_SEC,
                    stop=now_sec + 1,
                ),
                timeout=RESTORE_TIMEOUT_SEC,
            )
        except Exception as exc:
            logger.warning("사건 복원 실패 — 빈 상태로 시작: %r", exc)
            return
        ongoing = sorted((r for r in rows if r.end_ts == 0), key=lambda r: r.start_ts)
        closed = 0
        for row in ongoing:
            ev = PremiumEvent.from_row(row)
            prev = self._open.get(ev.key)
            if prev is not None:
                # 같은 조합에 진행 중이 둘 이상(닫힘 쓰기 유실) — 늦은 것만 살리고 옛것은 last_ts 로 닫는다
                self._close_at(prev, prev.last_ts)
                closed += 1
            self._open[ev.key] = ev
        for ev in list(self._open.values()):
            if now_sec - ev.last_ts > MAX_GAP_SEC:
                # 죽어 있던 시간도 결측과 같다 — 600초를 넘겼으면 마지막 관측에서 끝난 사건
                self._close_at(ev, ev.last_ts)
                closed += 1
        logger.info("사건 복원: 진행 중 %d건, 닫음 %d건", len(self._open), closed)

    # --- 틱 루프가 매초 부른다 (EventSink) ---

    def observe(self, tick: Tick) -> None:
        ts = tick.ts
        seen: set[Key] = set()
        for row in tick.rows:
            for dir_, value in (("kimp", row.fwd), ("reverse", row.rev)):
                key: Key = (row.dom, row.fx, row.base, dir_)
                seen.add(key)
                ev = self._open.get(key)
                if ev is None:
                    if value >= ENTER_PERCENT:
                        self._open[key] = PremiumEvent(
                            dom=row.dom,
                            fx=row.fx,
                            base=row.base,
                            dir=dir_,
                            start_ts=ts,
                            end_ts=None,
                            max_percent=value,
                            max_ts=ts,
                            last_ts=ts,
                            samples=1,
                        )
                elif value > EXIT_PERCENT:
                    ev.samples += 1
                    ev.last_ts = ts
                    if value > ev.max_percent:
                        ev.max_percent = value
                        ev.max_ts = ts
                else:
                    self._close_observed(ev, ts)
        for key, ev in list(self._open.items()):
            if key not in seen and ts - ev.last_ts > MAX_GAP_SEC:
                # 결측: 관측 못 한 시간은 사건에 넣지 않는다 — 마지막 관측에서 끝난 것으로
                self._close_at(ev, ev.last_ts)
        refresh = ts - self._last_refresh_ts >= WRITE_INTERVAL_SEC
        for ev in self._open.values():
            if not ev.written and ts - ev.start_ts > MIN_DURATION_SEC:
                ev.written = True  # 열린 지 60초를 넘긴 순간 첫 점
                self._stage(ev)
            elif ev.written and refresh:
                self._stage(ev)  # 60초마다 max·last_ts·samples 갱신 — 복원 정확도
        if refresh:
            self._last_refresh_ts = ts

    # --- 읽기 (features/history) ---

    def open_events(self) -> list[PremiumEvent]:
        return list(self._open.values())

    # --- Influx 쓰기 태스크 ---

    async def run_writer_loop(self) -> None:
        """앱과 함께 돌고 종료 시 취소된다. 점이 생기면 깨어나고, 없어도 60초마다 재시도한다."""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=WRITE_INTERVAL_SEC)
            except TimeoutError:
                pass
            self._wake.clear()
            await self.write_round()

    async def flush(self) -> None:
        """미전송 점을 지금 전부 쓴다(실패 대기 무시) — 테스트용."""
        await self.write_round(force=True)

    async def write_round(self, force: bool = False) -> None:
        """쓰기 회차 1번 — 미전송 점 전부를 쓰기 1번으로. 실패하면 남겨 두고 다음 회차에."""
        if self._writer is None or not self._pending:
            return
        now = self._clock()
        if (
            not force
            and self._failed_at is not None
            and now - self._failed_at < WRITE_INTERVAL_SEC
        ):
            return  # 실패 뒤 60초 안엔 재시도 안 함 — 불통 중 60초짜리 실패 호출이 쓰기 스레드를 연달아 막지 않게
        batch = list(self._pending.items())
        try:
            await asyncio.to_thread(self._writer.write, [p for _, p in batch])
        except Exception as exc:
            self._failed_at = now
            logger.warning(
                "premium_event 쓰기 실패(%d점) — 다음 회차 재시도: %r", len(batch), exc
            )
            return
        self._failed_at = None
        for key, point in batch:
            if self._pending.get(key) is point:
                del self._pending[key]  # 쓰는 동안 새로 갱신된 점은 남긴다

    # --- 내부 ---

    def _close_observed(self, ev: PremiumEvent, end_ts: int) -> None:
        """값이 종료 이하가 된 틱에서 닫는다. 1분을 못 넘긴 스파이크는 사건이 아니다."""
        self._open.pop(ev.key, None)
        ev.end_ts = end_ts
        if end_ts - ev.start_ts <= MIN_DURATION_SEC:
            return  # 버리기 — 60초 전이라 Influx 에 쓴 점도 없다
        self._stage(ev)

    def _close_at(self, ev: PremiumEvent, end_ts: int) -> None:
        """결측·복원으로 닫는다 — 관측한 만큼은 사실이므로 duration 이 60 이하여도 남긴다."""
        self._open.pop(ev.key, None)
        ev.end_ts = end_ts
        self._stage(ev)

    def _stage(self, ev: PremiumEvent) -> None:
        if self._writer is None:
            return
        key = (ev.key, ev.start_ts)
        self._pending.pop(key, None)  # 순서를 뒤로 — 최신 상태가 마지막에 쓰인다
        self._pending[key] = premium_event_point(ev.to_row())
        while len(self._pending) > PENDING_LIMIT:
            dropped = next(iter(self._pending))
            del self._pending[dropped]
            logger.warning(
                "premium_event 미전송 %d건 초과 — 버림: %r", PENDING_LIMIT, dropped
            )
        self._wake.set()
