"""김프/역프 사건 감지기 — 틱이 판정하고 Influx `premium_event` 에 1건 1점 (스펙 013 §3.2~3.3).

틱 루프가 매초 현재 틱을 넘기고, 여기서 조합 `(dom, fx, base, dir)` 마다 사건을 열고·갱신하고·닫는다.
메모리에 드는 것은 열린 사건뿐이고 닫힌 사건은 Influx 가 진실이다. 예외는 복원용 사본 하나 — 열린 사건 전체를
60초 갱신 회차·닫힘 점을 쓴 회차·종료 때 Redis 키 하나에 JSON 으로 두고, 기동 복원은 그것을 먼저 읽는다(없거나
실패하면 Influx).
core 에 사는 이유: 쓰는 쪽이 틱 루프(core)라 기능 폴더가 될 수 없다. 읽기 API 는 features/history.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Iterable
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
# 기동 복원이 읽는 start_ts 범위(Influx 두 단계 조회 모두)
RESTORE_WINDOW_SEC = 7 * 86_400
# 기동 복원 상한(사본 읽기·Influx 조회 각각, 조회의 HTTP 타임아웃도 같은 값) — 넘기면 다음 원천·빈 상태
RESTORE_TIMEOUT_SEC = 3.0
# 미전송 점 상한 — 넘치면 오래된 것부터 버린다(복원이 닫은 점은 별도, 상한 없음)
PENDING_LIMIT = 1_000
# 종료 때 사본 저장 + 미전송 쓰기 1회의 총 상한 — Influx 불통이 종료를 붙들지 않게
FINAL_TIMEOUT_SEC = 3.0
# 사본 한 건의 키 — 열린 사건을 다시 만드는 데 필요한 전부(`end_ts` 는 늘 진행 중이라 없다)
_SNAPSHOT_FIELDS = (
    "dom",
    "fx",
    "base",
    "dir",
    "start_ts",
    "max_percent",
    "max_ts",
    "last_ts",
    "samples",
    "net_dom",
    "net_fx",
    "written",
)

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
    # 024 §3.5 — 마지막으로 본 틱 행의 망 표시명. 진행 중이면 "지금 옮길 망", 닫히면 "닫힐 때 망". 이력은 남기지 않는다
    net_dom: str | None = None
    net_fx: str | None = None
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
            net_dom=self.net_dom,
            net_fx=self.net_fx,
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
            net_dom=row.net_dom,
            net_fx=row.net_fx,
            written=True,
        )


class EventWriter(Protocol):
    """점 쓰기 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def write(self, points: list[InfluxPoint]) -> None: ...


class EventReader(Protocol):
    """기동 시 복원 조회(7일 안 `end_ts 0`) — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def query_ongoing_events(
        self, *, start: int, stop: int, timeout_sec: float | None = None
    ) -> list[PremiumEventRow]: ...


class OpenEventStore(Protocol):
    """열린 사건 사본(Redis 키 하나) — 실물은 core.redis_bus.RedisBus, 테스트는 fake."""

    async def open_events_load(self) -> str | None: ...

    async def open_events_save(self, data: str) -> None: ...


def _snapshot_json(events: Iterable["PremiumEvent"]) -> str:
    """열린 사건 전체 → 사본 JSON(사건마다 객체 하나). 틱 루프에서 60초에 한 번 — ≈255건이면 60KB 안팎이다."""
    return json.dumps(
        [{k: getattr(ev, k) for k in _SNAPSHOT_FIELDS} for ev in events],
        separators=(",", ":"),
    )


def _snapshot_events(data: str) -> list["PremiumEvent"]:
    """사본 JSON → 진행 중 사건. 모양이 어긋나면 예외(호출자가 Influx 로 되돌아간다)."""
    return [
        PremiumEvent(
            dom=str(d["dom"]),
            fx=str(d["fx"]),
            base=str(d["base"]),
            dir=str(d["dir"]),
            start_ts=int(d["start_ts"]),
            end_ts=None,
            max_percent=float(d["max_percent"]),
            max_ts=int(d["max_ts"]),
            last_ts=int(d["last_ts"]),
            samples=int(d["samples"]),
            net_dom=d["net_dom"],
            net_fx=d["net_fx"],
            written=bool(d["written"]),
        )
        for d in json.loads(data)
    ]


class PremiumEventDetector:
    def __init__(
        self,
        writer: EventWriter | None = None,
        clock: Callable[[], float] = time.time,
        snapshots: OpenEventStore | None = None,
    ) -> None:
        self._writer = writer
        self._clock = clock
        self._snapshots = snapshots
        self._open: dict[Key, PremiumEvent] = {}
        # 쓸 점 — (키, start_ts) 당 최신 1점. 열림·갱신·닫힘·실패분이 전부 여기 모이고 한 회차에 한 번에 쓴다.
        # 같은 키 덮어쓰기라 마지막 상태만 보내면 되고, 삽입 순서가 곧 쓰기 순서다.
        self._pending: dict[tuple[Key, int], InfluxPoint] = {}
        # 미전송 맵에서 닫힘 점인 키 — 이것이 비어야(닫힘이 Influx 에 들어가야) 사본에서 닫힌 사건을 뺀다
        self._pending_closes: set[tuple[Key, int]] = set()
        # 마지막 사본 뒤에 닫힌 사건이 있다 — 닫힘 점을 다 쓴 쓰기 회차가 사본을 다시 만든다
        self._closed_since_snapshot = False
        # 복원이 닫은 점 — 미전송 상한과 별개로 첫 쓰기 회차에 미전송 점보다 먼저 전부 쓴다(고아 수천 건도 버리지 않는다)
        self._restore_closes: list[InfluxPoint] = []
        # 상한에서 버린 미전송 점 수 — 쓰기를 시도하는 회차가 1줄로 알린다
        self._dropped = 0
        # 쓰기 태스크가 저장할 사본 — 틱 루프는 직렬화만 하고, 가장 최근 것 하나만 남는다
        self._snapshot: str | None = None
        # 사본 저장이 겹쳐 옛 사본이 새 사본을 덮지 않게
        self._snapshot_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._failed_at: float | None = (
            None  # 마지막 쓰기 실패 시각 — 60초 안엔 재시도 안 함
        )
        self._last_refresh_ts = 0  # 열린 사건 60초 갱신의 마지막 틱

    # --- 복원 (틱 루프 시작 전에 1회) ---

    async def restore(self, reader: EventReader | None, now_sec: int) -> None:
        """열린 사건을 메모리로 — Redis 사본을 먼저 읽고, 없거나 실패하면 Influx(7일 안 `end_ts 0`)로 되돌아간다.

        사본이 있으면 Influx 는 읽지 않는다. 둘 다 못 읽으면 빈 상태 + 경고 1줄. 어느 원천이든 같은 규칙 —
        같은 조합에 둘 이상이면 늦게 시작한 것만 살리고, 마지막 관측이 600초를 넘었으면 `last_ts` 로 닫는다.
        닫은 점은 첫 쓰기 회차가 상한 없이 먼저 쓴다(복원 안에서 동기로 쓰지 않는다 — 쓰기 1회는 최대 60초다).
        """
        events = await self._load_snapshot()
        source = "Redis 사본"
        if events is None:
            if reader is None:
                logger.warning("Influx 가 없어 사건을 복원하지 않는다 — 빈 상태로 시작")
                return
            try:
                rows = await asyncio.wait_for(
                    asyncio.to_thread(
                        reader.query_ongoing_events,
                        start=now_sec - RESTORE_WINDOW_SEC,
                        stop=now_sec + 1,
                        timeout_sec=RESTORE_TIMEOUT_SEC,
                    ),
                    timeout=RESTORE_TIMEOUT_SEC,
                )
            except Exception as exc:
                logger.warning("사건 복원 실패 — 빈 상태로 시작: %r", exc)
                return
            events = [PremiumEvent.from_row(r) for r in rows]
            source = "Influx"
        closed = 0
        for ev in sorted(events, key=lambda e: e.start_ts):
            prev = self._open.get(ev.key)
            if prev is not None:
                # 같은 조합에 진행 중이 둘 이상(닫힘 쓰기 유실) — 늦은 것만 살리고 옛것은 last_ts 로 닫는다
                self._close_restored(prev)
                closed += 1
            self._open[ev.key] = ev
        for ev in list(self._open.values()):
            if now_sec - ev.last_ts > MAX_GAP_SEC:
                # 죽어 있던 시간도 결측과 같다 — 600초를 넘겼으면 마지막 관측에서 끝난 사건
                self._close_restored(ev)
                closed += 1
        if self._restore_closes:
            self._wake.set()
        logger.info(
            "사건 복원(%s): 진행 중 %d건, 닫음 %d건", source, len(self._open), closed
        )

    async def _load_snapshot(self) -> list[PremiumEvent] | None:
        """Redis 사본 — 저장소가 없거나 키가 없거나 읽기·해석이 실패하면 None(실패는 경고 1줄)."""
        if self._snapshots is None:
            return None
        try:
            data = await asyncio.wait_for(
                self._snapshots.open_events_load(), timeout=RESTORE_TIMEOUT_SEC
            )
            return None if data is None else _snapshot_events(data)
        except Exception as exc:
            logger.warning("열린 사건 사본 읽기 실패 — Influx 로 복원한다: %r", exc)
            return None

    # --- 틱 루프가 매초 부른다 (EventSink) ---

    def observe(self, tick: Tick) -> None:
        ts = tick.ts
        # 열린 사건이 걸린 조합 — 이 조합의 행은 값이 낮아도 유지·닫기 판정을 받아야 한다(한 틱에 조합당 한 행)
        busy = {key[:3] for key in self._open}
        for row in tick.rows:
            fwd = row.fwd
            rev = row.rev
            if (
                fwd < ENTER_PERCENT
                and rev < ENTER_PERCENT
                and (row.dom, row.fx, row.base) not in busy
            ):
                # 빠른 길 — 열 사건도, 이어 가거나 닫을 사건도 없다. NaN 은 비교가 거짓이라 아래 판정으로 간다
                continue
            for dir_, value in (("kimp", fwd), ("reverse", rev)):
                key: Key = (row.dom, row.fx, row.base, dir_)
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
                            net_dom=row.net_dom,
                            net_fx=row.net_fx,
                        )
                elif value > EXIT_PERCENT:
                    ev.samples += 1
                    ev.last_ts = ts
                    if value > ev.max_percent:
                        ev.max_percent = value
                        ev.max_ts = ts
                    # 024 §3.5 — 60초 갱신·닫힘이 "그 시점 틱 행의 망" 을 쓰도록 매 틱 최신값을 든다(마지막 값만 남는다)
                    ev.net_dom, ev.net_fx = row.net_dom, row.net_fx
                else:
                    ev.net_dom, ev.net_fx = row.net_dom, row.net_fx
                    self._close_observed(ev, ts)
        for ev in list(self._open.values()):
            # 결측: 관측 못 한 시간은 사건에 넣지 않는다 — 마지막 관측에서 끝난 것으로. 이번 틱에 행이 있던
            # 사건은 위에서 last_ts 가 이 틱이 됐거나 닫혀 빠졌으므로, 이 조건 하나가 곧 "이번 틱에 안 보였음" 이다
            if ts - ev.last_ts > MAX_GAP_SEC:
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
            if self._snapshots is not None:
                # 사본은 60초 갱신과 같은 회차에 — 저장은 쓰기 태스크가 한다(루프에서는 직렬화만)
                self._snapshot = _snapshot_json(self._open.values())
                self._wake.set()

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
        """쓰기 회차 1번 — 복원이 닫은 점과 미전송 점을 쓰기 1번으로, 이어 사본이 있으면 Redis 에 저장.

        마지막 사본 뒤에 닫힌 사건이 있고 그 닫힘 점을 모두 썼으면(버린 스파이크처럼 쓸 점이 없어도) 60초 갱신을
        기다리지 않고 지금 열린 사건으로 사본을 다시 만든다 — 닫힌 사건이 사본에 남은 채 비정상 종료되면 600초 안의
        재기동 복원이 그 사건을 다시 열어 Influx 의 올바른 닫힘 점을 덮는다. 닫힘 쓰기가 먼저, 사본이 다음이다
        (사본이 먼저면 그 사이 종료에 Influx 의 진행 중 점이 고아로 남는다).
        """
        await self._write_points(force)
        if (
            self._closed_since_snapshot
            and self._snapshots is not None
            and not self._pending_closes
            and not self._restore_closes
        ):
            # 루프 안이라 틱과 겹치지 않는다 — 열린 사건 ≈255건 직렬화 0.2ms
            self._closed_since_snapshot = False
            self._snapshot = _snapshot_json(self._open.values())
        await self._save_snapshot()

    async def final_round(self) -> None:
        """종료 때 1회(쓰기 태스크를 멈추기 전) — 지금 열린 사건의 사본 저장과 미전송 점 쓰기(실패 대기 무시).

        사본을 먼저 저장한다 — 다음 기동 복원이 읽는 것이 사본이라서다. 둘 합쳐 FINAL_TIMEOUT_SEC 안에서만.
        """
        if self._snapshots is not None:
            self._snapshot = _snapshot_json(self._open.values())
        try:
            await asyncio.wait_for(self._final(), timeout=FINAL_TIMEOUT_SEC)
        except TimeoutError:
            logger.warning(
                "종료 사건 저장이 %.0f초를 넘겨 멈춘다 — 못 쓴 점은 다음 기동 복원이 메운다",
                FINAL_TIMEOUT_SEC,
            )

    async def _final(self) -> None:
        await self._save_snapshot()
        await self._write_points(force=True)

    async def _write_points(self, force: bool) -> None:
        if self._writer is None or not (self._pending or self._restore_closes):
            return
        now = self._clock()
        if (
            not force
            and self._failed_at is not None
            and now - self._failed_at < WRITE_INTERVAL_SEC
        ):
            return  # 실패 뒤 60초 안엔 재시도 안 함 — 불통 중 60초짜리 실패 호출이 쓰기 스레드를 연달아 막지 않게
        if self._dropped:
            logger.warning(
                "premium_event 미전송 %d건 초과 — 오래된 것부터 %d건 버림",
                PENDING_LIMIT,
                self._dropped,
            )
            self._dropped = 0
        closes = self._restore_closes
        batch = list(self._pending.items())
        try:
            await asyncio.to_thread(self._writer.write, closes + [p for _, p in batch])
        except Exception as exc:
            self._failed_at = now
            logger.warning(
                "premium_event 쓰기 실패(%d점) — 다음 회차 재시도: %r",
                len(closes) + len(batch),
                exc,
            )
            return
        self._failed_at = None
        self._restore_closes = []
        for key, point in batch:
            if self._pending.get(key) is point:
                del self._pending[key]  # 쓰는 동안 새로 갱신된 점은 남긴다
                self._pending_closes.discard(key)

    async def _save_snapshot(self) -> None:
        """가장 최근 사본 하나를 Redis 에 — 실패는 경고 1줄 후 버린다(다음 60초 회차나 닫힘 회차가 새 사본을 만든다)."""
        if self._snapshots is None:
            return
        async with self._snapshot_lock:
            data = self._snapshot
            if data is None:
                return
            self._snapshot = None
            try:
                await self._snapshots.open_events_save(data)
            except Exception as exc:
                logger.warning("열린 사건 사본 저장 실패: %r", exc)

    # --- 내부 ---

    def _close_observed(self, ev: PremiumEvent, end_ts: int) -> None:
        """값이 종료 이하가 된 틱에서 닫는다. 1분을 못 넘긴 스파이크는 사건이 아니다."""
        self._open.pop(ev.key, None)
        ev.end_ts = end_ts
        self._closed_since_snapshot = True
        if end_ts - ev.start_ts <= MIN_DURATION_SEC:
            # 버리기 — 60초 전이라 Influx 에 쓴 점도 없다. 사본에는 들어 있을 수 있어 쓰기 태스크를 깨워 다시 저장한다
            # (그대로 두면 비정상 종료 뒤 복원이 스파이크를 되살려 사건으로 남긴다)
            if self._snapshots is not None:
                self._wake.set()
            return
        self._stage(ev)

    def _close_at(self, ev: PremiumEvent, end_ts: int) -> None:
        """결측으로 닫는다 — 관측한 만큼은 사실이므로 duration 이 60 이하여도 남긴다."""
        self._open.pop(ev.key, None)
        ev.end_ts = end_ts
        self._closed_since_snapshot = True
        self._stage(ev)

    def _close_restored(self, ev: PremiumEvent) -> None:
        """복원이 `last_ts` 로 닫는다 — 점은 미전송 맵이 아니라 복원 목록으로(상한 없이 첫 회차에)."""
        self._open.pop(ev.key, None)
        ev.end_ts = ev.last_ts
        self._closed_since_snapshot = True
        if self._writer is not None:
            self._restore_closes.append(premium_event_point(ev.to_row()))

    def _stage(self, ev: PremiumEvent) -> None:
        if self._writer is None:
            return
        key = (ev.key, ev.start_ts)
        self._pending.pop(key, None)  # 순서를 뒤로 — 최신 상태가 마지막에 쓰인다
        self._pending[key] = premium_event_point(ev.to_row())
        if ev.end_ts is not None:
            self._pending_closes.add(key)
        while len(self._pending) > PENDING_LIMIT:
            oldest = next(iter(self._pending))
            del self._pending[oldest]
            self._pending_closes.discard(oldest)
            self._dropped += 1
        self._wake.set()
