"""1분 봉 집계기와 계층 롤업 — 스펙 014 §3.3~3.5.

틱 루프가 매초 현재 틱을 넘기고(013 감지기 다음 자리), 여기서 (국내, 해외, 코인) 조합마다 그 분의
김프·역프 OHLC·가격 종가·입출금 상태·막힌 초를 모아 분이 닫히는 순간 `candles_1m` 점으로 만든다.
쓰기 태스크는 그 점을 쓴 **다음** 같은 회차에서 5m → 1h → 4h → 1d 순으로 아래 계층을 접어 위 계층에 쓴다 —
순서가 곧 정합성이다(방금 닫힌 분이 먼저 들어가고, 5m 이 써진 뒤 1h 가 그것을 읽는다).
core 에 사는 이유: 쓰는 쪽이 틱 루프(core)라 기능 폴더가 될 수 없다. 읽기 API 는 features/history.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.influx import CandleRow, InfluxPoint, candle_point
from app.core.models import Tick, TickRow

logger = logging.getLogger("marketlens.candles")

# 창 정렬은 KST 벽시계 — 4h 를 UTC 로 자르면 KST 자정이 4h 경계에 안 걸려 일봉을 못 만든다 (§3.4)
KST_OFFSET_SEC = 32_400
WRITE_INTERVAL_SEC = 60  # 쓰기 회차·실패 재시도 주기
PENDING_LIMIT = 10_000  # 1m 미전송 점 상한(≈20분치) — 넘치면 오래된 분부터 버린다
BUCKET_SETUP_TIMEOUT_SEC = 3.0  # 기동 시 버킷 생성 상한
RESTORE_TIMEOUT_SEC = 3.0  # 기동 시 계층당 따라잡기 기준점 조회 상한
MAX_WINDOWS_PER_ROUND = (
    12  # 한 회차 계층당 접는 창 수 — 재기동 따라잡기가 Influx 를 누르지 않게
)
LIMIT_POINTS = 1_440  # /history/candles 요청당 상한(창 수)


@dataclass(frozen=True)
class Tier:
    res: str
    bucket: str
    window_sec: int
    retention_sec: int  # 0 = 무제한 (Influx 규약)


# 계층 순서 = 사슬 순서. 각 계층은 바로 아래 계층만 읽는다 (§3.5)
TIERS: tuple[Tier, ...] = (
    Tier("1m", "candles_1m", 60, 7 * 86_400),
    Tier("5m", "candles_5m", 300, 30 * 86_400),
    Tier("1h", "candles_1h", 3_600, 90 * 86_400),
    Tier("4h", "candles_4h", 14_400, 365 * 86_400),
    Tier("1d", "candles_1d", 86_400, 0),
)
TIER_BY_RES: dict[str, Tier] = {t.res: t for t in TIERS}


def window_start(ts: int, window_sec: int) -> int:
    """KST 벽시계 정렬 창 시작 — 1m·5m·1h 는 UTC 정렬과 같고 4h·1d 가 다르다."""
    return (ts + KST_OFFSET_SEC) // window_sec * window_sec - KST_OFFSET_SEC


def limit_sec(tier: Tier) -> int:
    """`/history/candles` 한 요청이 덮을 수 있는 최대 구간(초) = 1,440창."""
    return LIMIT_POINTS * tier.window_sec


def _tri(v: bool | None) -> int:
    """입출금 3상태 → 저장값 (1 가능·0 불가·−1 모름) — Influx 에 null 이 없다."""
    return -1 if v is None else int(v)


class CandleStore(Protocol):
    """Influx 중 이 모듈이 쓰는 부분 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def write(self, points: list[InfluxPoint], bucket: str | None = None) -> None: ...

    def query_candles(
        self,
        bucket: str,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        fx: str | None = None,
        base: str | None = None,
    ) -> list[CandleRow]: ...

    def latest_candle_ts(self, bucket: str, *, start: int) -> int | None: ...

    def earliest_candle_ts(self, bucket: str, *, start: int) -> int | None: ...

    def list_buckets(self) -> set[str]: ...

    def create_bucket(self, name: str, retention_sec: int) -> None: ...


# --- 버킷 (§3.4) ---


def _create_missing_buckets(store: CandleStore) -> list[str]:
    existing = store.list_buckets()
    created: list[str] = []
    for tier in TIERS:
        if tier.bucket not in existing:
            # 이미 있는 버킷의 retention 은 건드리지 않는다 — 사람이 바꾼 값을 존중
            store.create_bucket(tier.bucket, tier.retention_sec)
            created.append(tier.bucket)
    return created


async def ensure_candle_buckets(store: CandleStore | None) -> None:
    """없는 계층 버킷을 만든다 — 3초 상한, 실패하면 경고 1줄 후 계속(쓰기가 실패로 남는다)."""
    if store is None:
        logger.warning("Influx 가 없어 봉을 저장하지 않는다 — 집계만 돈다")
        return
    try:
        created = await asyncio.wait_for(
            asyncio.to_thread(_create_missing_buckets, store),
            timeout=BUCKET_SETUP_TIMEOUT_SEC,
        )
    except Exception as exc:
        logger.warning("봉 버킷 준비 실패 — 쓰기는 실패로 남는다: %r", exc)
        return
    if created:
        logger.info("봉 버킷 생성: %s", ", ".join(created))


# --- 접기 (순수) ---


def fold_candles(rows: list[CandleRow], ts: int) -> CandleRow:
    """같은 조합의 아래 봉(ts 오름차순, 1개 이상)을 창 `ts` 의 봉 하나로 (§3.5 접기 규칙)."""
    first, last = rows[0], rows[-1]
    return CandleRow(
        dom=last.dom,
        fx=last.fx,
        base=last.base,
        ts=ts,
        fwd_o=first.fwd_o,
        fwd_h=max(r.fwd_h for r in rows),
        fwd_l=min(r.fwd_l for r in rows),
        fwd_c=last.fwd_c,
        rev_o=first.rev_o,
        rev_h=max(r.rev_h for r in rows),
        rev_l=min(r.rev_l for r in rows),
        rev_c=last.rev_c,
        krw=last.krw,
        usdt=last.usdt,
        rate=last.rate,
        dom_dep=last.dom_dep,
        dom_wd=last.dom_wd,
        fx_dep=last.fx_dep,
        fx_wd=last.fx_wd,
        blocked_fwd_sec=sum(r.blocked_fwd_sec for r in rows),
        blocked_rev_sec=sum(r.blocked_rev_sec for r in rows),
        samples=sum(r.samples for r in rows),
    )


def fold_window(rows: list[CandleRow], ts: int) -> list[InfluxPoint]:
    """한 창의 아래 봉 전부(조합 섞임, ts 오름차순) → 조합별 위 점. 아래 점이 없는 조합은 점이 없다."""
    by_combo: dict[tuple[str, str, str], list[CandleRow]] = {}
    for r in rows:
        by_combo.setdefault((r.dom, r.fx, r.base), []).append(r)
    return [candle_point(fold_candles(rs, ts)) for _, rs in sorted(by_combo.items())]


# --- 롤업 (§3.5) ---


class Rollup:
    """계층마다 "마지막으로 접은 창"을 들고, 회차마다 그 다음 창부터 차례로 접어 쓴다."""

    def __init__(self, store: CandleStore | None) -> None:
        self._store = store
        # 위 계층 res → 마지막으로 접은 창 시작. None = 기준점이 없어(복원 전·Influx 없음) 접지 않는다
        self._last_done: dict[str, int | None] = {t.res: None for t in TIERS[1:]}

    async def restore(self, now_sec: int) -> None:
        """기동 시 계층마다 기준점 — 위 버킷의 가장 늦은 점, 비면 아래 버킷 가장 오래된 점의 창 직전, 실패면 지금 창 직전."""
        if self._store is None:
            return
        for i, upper in enumerate(TIERS[1:], start=1):
            try:
                last = await asyncio.wait_for(
                    asyncio.to_thread(self._edge, i, now_sec),
                    timeout=RESTORE_TIMEOUT_SEC,
                )
            except Exception as exc:
                last = window_start(now_sec, upper.window_sec) - upper.window_sec
                logger.warning(
                    "%s 롤업 기준점 조회 실패 — 지금 창부터 시작: %r", upper.res, exc
                )
            self._last_done[upper.res] = last

    def _edge(self, i: int, now_sec: int) -> int:
        """계층 i 의 기준점 — 위 버킷의 가장 늦은 점, 없으면(첫 배포·긴 공백) 아래 계층들 중 가장 오래된 점의 창 직전.

        아래를 한 계층만 보지 않는 이유: 첫 배포에는 1m 만 있고 5m·1h 는 아직 비어 있다 — 1h 가 5m 만 보면
        지금 창에 앵커를 잡아 1m 에 있던 첫 시간을 영영 접지 않는다. 각 버킷은 자기 보관 기간 안에서만 찾는다 —
        그 밖의 빈틈은 메울 수 없고 전 구간 스캔도 피한다.
        """
        assert self._store is not None
        upper, lower = TIERS[i], TIERS[i - 1]
        latest = self._store.latest_candle_ts(
            upper.bucket, start=now_sec - lower.retention_sec
        )
        if latest is not None:
            return latest
        anchor = now_sec
        for tier in TIERS[:i]:
            earliest = self._store.earliest_candle_ts(
                tier.bucket, start=now_sec - tier.retention_sec
            )
            if earliest is not None:
                anchor = min(anchor, earliest)
        return window_start(anchor, upper.window_sec) - upper.window_sec

    def last_done(self, res: str) -> int | None:
        return self._last_done[res]

    def run_round(self, now_sec: int, minute_frontier: int | None) -> None:
        """5m → 1h → 4h → 1d 순으로 계층당 최대 12창을 접어 쓴다. 동기 — 스레드에서 돈다.

        접는 창의 끝 ≤ min(지금, 아래 계층 완료 시각). `minute_frontier` 는 1m 이 Influx 에 다 들어간 시각(집계기가
        열어 둔 분의 시작), 그 위 계층의 완료 시각은 아래 계층이 마지막으로 접은 창의 끝 — 위 계층이 아래를 앞질러
        "아래 점이 없다" 로 빈 창을 확정하면 다시 보지 않아 영구 구멍이 되기 때문이다 (§3.5).
        Influx 실패는 예외로 전파돼 그 자리에서 회차가 멈춘다 — 접은 창까지만 기준점이 전진했으므로
        다음 회차가 같은 창부터 다시 한다(같은 키 덮어쓰기라 안전).
        """
        if self._store is None:
            return
        lower_done = minute_frontier
        for lower, upper in zip(TIERS, TIERS[1:], strict=False):
            last = self._last_done[upper.res]
            if last is None or lower_done is None:
                return  # 기준점이 없거나(복원 전) 아래가 아직 아무것도 못 쓴 회차 — 그 위도 접을 수 없다
            w = upper.window_sec
            bound = min(now_sec, lower_done)
            ws = last + w
            done = 0
            while ws + w <= bound and done < MAX_WINDOWS_PER_ROUND:
                rows = self._store.query_candles(lower.bucket, start=ws, stop=ws + w)
                points = fold_window(rows, ws)
                if points:
                    self._store.write(points, upper.bucket)
                self._last_done[upper.res] = ws
                ws += w
                done += 1
            lower_done = self._last_done[upper.res] + w  # type: ignore[operator]


# --- 1분 집계기 (§3.3~3.4) ---


@dataclass
class _Acc:
    """한 분·한 조합의 누적 — 시가·고·저는 여기, 종가·가격·입출금은 마지막 행에서."""

    fwd_o: float
    fwd_h: float
    fwd_l: float
    rev_o: float
    rev_h: float
    rev_l: float
    last: TickRow
    blocked_fwd: int = 0
    blocked_rev: int = 0
    samples: int = 0


class CandleAggregator:
    def __init__(
        self,
        store: CandleStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._clock = clock
        self._rollup = Rollup(store)
        self._open_minute: int | None = (
            None  # 지금 모으는 분의 시작 — 이 시각 전의 분은 전부 닫혔다
        )
        self._acc: dict[tuple[str, str, str], _Acc] = {}
        # 미전송 1m 점 — 키 (dom, fx, base, 분). 삽입 순서 = 분 순서라 넘치면 앞에서부터 버린다
        self._pending: dict[tuple[str, str, str, int], InfluxPoint] = {}
        self._wake = asyncio.Event()
        self._failed_at: float | None = (
            None  # 마지막 Influx 실패 시각 — 60초 안엔 재시도 안 함
        )

    @property
    def rollup(self) -> Rollup:
        return self._rollup

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def restore(self, now_sec: int) -> None:
        """기동 시 1회 — 롤업 기준점. 진행 중이던 분은 복원하지 않는다(재기동 후 첫 분은 samples < 60)."""
        await self._rollup.restore(now_sec)

    # --- 틱 루프가 매초 부른다 (EventSink 모양) ---

    def observe(self, tick: Tick) -> None:
        if self._open_minute is not None and tick.ts >= self._open_minute + 60:
            self._close_minute()  # 건너뛴 분은 없는 것 — 열려 있던 분 하나만 닫는다
        if self._open_minute is None:
            self._open_minute = tick.ts // 60 * 60
        for row in tick.rows:
            key = (row.dom, row.fx, row.base)
            acc = self._acc.get(key)
            if acc is None:
                acc = _Acc(row.fwd, row.fwd, row.fwd, row.rev, row.rev, row.rev, row)
                self._acc[key] = acc
            else:
                acc.fwd_h = max(acc.fwd_h, row.fwd)
                acc.fwd_l = min(acc.fwd_l, row.fwd)
                acc.rev_h = max(acc.rev_h, row.rev)
                acc.rev_l = min(acc.rev_l, row.rev)
                acc.last = row
            acc.samples += 1
            # 김프 = 해외 출금 → 국내 입금, 역프 = 국내 출금 → 해외 입금. None(모름)은 막힘으로 세지 않는다
            if row.fx_wd is False or row.dom_dep is False:
                acc.blocked_fwd += 1
            if row.dom_wd is False or row.fx_dep is False:
                acc.blocked_rev += 1

    def _close_minute(self) -> None:
        minute = self._open_minute
        assert minute is not None
        if self._store is not None:
            for (dom, fx, base), acc in sorted(self._acc.items()):
                last = acc.last
                row = CandleRow(
                    dom=dom,
                    fx=fx,
                    base=base,
                    ts=minute,
                    fwd_o=acc.fwd_o,
                    fwd_h=acc.fwd_h,
                    fwd_l=acc.fwd_l,
                    fwd_c=last.fwd,
                    rev_o=acc.rev_o,
                    rev_h=acc.rev_h,
                    rev_l=acc.rev_l,
                    rev_c=last.rev,
                    krw=last.dom_price,
                    usdt=last.fx_price,
                    rate=last.rate,
                    dom_dep=_tri(last.dom_dep),
                    dom_wd=_tri(last.dom_wd),
                    fx_dep=_tri(last.fx_dep),
                    fx_wd=_tri(last.fx_wd),
                    blocked_fwd_sec=acc.blocked_fwd,
                    blocked_rev_sec=acc.blocked_rev,
                    samples=acc.samples,
                )
                self._pending[(dom, fx, base, minute)] = candle_point(row)
            dropped = 0
            while len(self._pending) > PENDING_LIMIT:
                del self._pending[next(iter(self._pending))]
                dropped += 1
            if dropped:
                logger.warning(
                    "candle 미전송 %d점 초과 — 오래된 분부터 %d점 버림",
                    PENDING_LIMIT,
                    dropped,
                )
            if self._acc:
                self._wake.set()
        self._open_minute = None
        self._acc = {}

    # --- Influx 쓰기 태스크 ---

    async def run_writer_loop(self) -> None:
        """앱과 함께 돌고 종료 시 취소된다. 분이 닫히면 깨어나고, 없어도 60초마다 회차를 돈다."""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=WRITE_INTERVAL_SEC)
            except TimeoutError:
                pass
            self._wake.clear()
            await self.write_round()

    async def flush(self) -> None:
        """미전송 점을 지금 쓰고 롤업까지 돈다(실패 대기 무시) — 테스트용."""
        await self.write_round(force=True)

    async def write_round(self, force: bool = False) -> None:
        """회차 1번 — 미전송 1m 전부를 쓰기 1번으로 → 성공하면 같은 회차에서 롤업. 실패는 남겨 두고 60초 뒤."""
        if self._store is None:
            return
        now = self._clock()
        if (
            not force
            and self._failed_at is not None
            and now - self._failed_at < WRITE_INTERVAL_SEC
        ):
            return  # 불통 중 60초짜리 실패 호출이 쓰기 스레드를 연달아 막지 않게
        if self._pending:
            batch = list(self._pending.items())
            try:
                await asyncio.to_thread(
                    self._store.write, [p for _, p in batch], TIERS[0].bucket
                )
            except Exception as exc:
                self._failed_at = now
                logger.warning(
                    "candle 쓰기 실패(%d점) — 다음 회차 재시도: %r", len(batch), exc
                )
                return
            self._failed_at = None
            for key, point in batch:
                if self._pending.get(key) is point:
                    del self._pending[key]
        try:
            # 이 시각 전의 1m 은 전부 Influx 에 있다(썼거나 상한에서 버렸다) — 롤업이 앞지르지 못하는 선
            await asyncio.to_thread(self._rollup.run_round, int(now), self._open_minute)
        except Exception as exc:
            self._failed_at = now
            logger.warning("candle 롤업 실패 — 접은 창 다음부터 다음 회차에: %r", exc)
