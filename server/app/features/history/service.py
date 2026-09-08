"""`/history/*` 계산 — 순수 계산. Influx 리더를 인자로 받고 전역을 import 하지 않는다.

리더는 `core.influx.InfluxClient` 시그니처의 일부(query_premium)만 쓴다 —
테스트는 같은 시그니처의 fake 를 넣어 Influx 없이 돈다 (architecture.md 원칙).
"""

import re
import time
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal, Protocol

from app.core.candles import TIER_BY_RES, limit_sec
from app.core.influx import CandleRow, PremiumEventRow, PremiumRow
from app.core.premium_events import MIN_DURATION_SEC
from app.core.premium_events import PremiumEvent as OpenEvent
from app.features.history.models import (
    BulkCoin,
    BulkResponse,
    CandleOut,
    CandlesResponse,
    DirectionSummary,
    EventOut,
    EventsResponse,
    Overall,
    PremiumEvent,
    PremiumHistoryResponse,
    PremiumSummary,
    Segment,
    StreaksResponse,
)

KST = timezone(timedelta(hours=9))


class PremiumReader(Protocol):
    """이 기능이 쓰는 저장소 읽기 최소 인터페이스."""

    def query_premium(
        self, *, dom: str, fx: str, base: str | None, start: int, stop: int
    ) -> list[PremiumRow]: ...


class EventReader(Protocol):
    """`/history/events` 가 쓰는 저장소 읽기 — 013 사건 점."""

    def query_premium_events(
        self,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        dir: str | None = None,
        base: str | None = None,
    ) -> list[PremiumEventRow]: ...


class CandleReader(Protocol):
    """`/history/candles` 가 쓰는 저장소 읽기 — 014 계층 버킷 하나."""

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


class HistoryApiError(Exception):
    """라우터가 `{"error": {code, message, detail}}` 로 변환한다."""

    def __init__(
        self, http_status: int, code: str, message: str, detail: object = None
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.detail = detail


def _kst(ts: int) -> str:
    """epoch 초 → KST ISO 8601 (+09:00 으로 끝난다)."""
    return datetime.fromtimestamp(ts, tz=KST).isoformat()


def _utc_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── /history/premium ───────────────────────────────────────────────────────


def period_bounds(
    unit: Literal["week", "month"], day: date
) -> tuple[datetime, datetime]:
    """`day` 가 속한 ISO 주(월 00:00 UTC ~ 다음 월) 또는 달(1일 ~ 다음 달 1일), end exclusive."""
    if unit == "week":
        start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(
            days=day.weekday()
        )
        return start, start + timedelta(days=7)
    start = datetime(day.year, day.month, 1, tzinfo=UTC)
    if day.month == 12:
        end = datetime(day.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(day.year, day.month + 1, 1, tzinfo=UTC)
    return start, end


def build_premium_history(
    reader: PremiumReader,
    *,
    dom: str,
    fx: str,
    base: str,
    unit: Literal["week", "month"],
    date_str: str | None,
) -> PremiumHistoryResponse:
    if date_str is None:
        day = datetime.now(UTC).date()
    else:
        # fromisoformat 은 3.11+ 에서 20260828·2026-W35-4 같은 변형도 받으므로 형식을 먼저 고정한다
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            raise HistoryApiError(
                400,
                "invalid_request",
                f"date 형식이 잘못됐습니다: {date_str!r} (YYYY-MM-DD)",
            )
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            raise HistoryApiError(
                400,
                "invalid_request",
                f"date 형식이 잘못됐습니다: {date_str!r} (YYYY-MM-DD)",
            ) from None
        if not 1970 <= day.year <= 2100:
            # period_bounds 의 연도 연산이 넘치지 않는 안전 범위 — 밖이면 형식 오류와 같은 400
            raise HistoryApiError(
                400,
                "invalid_request",
                f"date 는 1970~2100 범위여야 합니다: {date_str!r}",
            )
    start_dt, end_dt = period_bounds(unit, day)
    start_sec = int(start_dt.timestamp())
    end_sec = int(end_dt.timestamp())

    rows = reader.query_premium(
        dom=dom, fx=fx, base=base.upper(), start=start_sec, stop=end_sec
    )
    if not rows:
        raise HistoryApiError(
            404,
            "market_data_not_found",
            f"{base.upper()} 의 {unit} 구간({_utc_z(start_dt)} ~ {_utc_z(end_dt)})에 기록이 없습니다.",
            {"dom": dom, "fx": fx, "base": base.upper()},
        )

    events: list[PremiumEvent] = []
    prev_ts = rows[0].ts
    for row in rows:
        events.append(PremiumEvent(dt=row.ts - prev_ts, fwd=row.fwd, rev=row.rev))
        prev_ts = row.ts
    fwds = [r.fwd for r in rows]
    return PremiumHistoryResponse(
        dom=dom,
        fx=fx,
        base=base.upper(),
        unit=unit,
        start=_utc_z(start_dt),
        end=_utc_z(end_dt),
        first_ts=rows[0].ts,
        count=len(rows),
        summary=PremiumSummary(
            first_fwd=fwds[0], last_fwd=fwds[-1], min_fwd=min(fwds), max_fwd=max(fwds)
        ),
        events=events,
        fetched_at=_now_ms(),
    )


# ── streaks ────────────────────────────────────────────────────────────────


def _segments(
    points: list[tuple[int, float]], threshold: float, max_gap: int
) -> list[Segment]:
    """ts 오름차순 (ts, 값) 에서 threshold 이상 연속 구간을 묶는다 — 스펙 005 §3.4 규칙 1~2.

    직전 기록(값과 무관)과 max_gap 초보다 벌어지면 구간을 닫는다 — 끊긴 수집을
    이어 붙여 "3시간 연속" 을 만들지 않는다.
    """
    segments: list[Segment] = []
    cur: list[tuple[int, float]] = []

    def close() -> None:
        if not cur:
            return
        values = [v for _, v in cur]
        start_ts, end_ts = cur[0][0], cur[-1][0]
        segments.append(
            Segment(
                start_ts=start_ts,
                end_ts=end_ts,
                start=_kst(start_ts),
                end=_kst(end_ts),
                duration_seconds=end_ts - start_ts,
                samples=len(cur),
                max_percent=max(values),
                avg_percent=sum(values) / len(values),
            )
        )
        cur.clear()

    prev_ts: int | None = None
    for ts, value in points:
        if prev_ts is not None and ts - prev_ts > max_gap:
            close()
        if value >= threshold:
            cur.append((ts, value))
        else:
            close()
        prev_ts = ts
    close()
    return segments


def _direction_summary(segments: list[Segment]) -> DirectionSummary:
    if not segments:
        return DirectionSummary(
            count=0,
            max_duration_seconds=0,
            avg_duration_seconds=0.0,
            max_percent=0.0,
            avg_percent=0.0,
            segments=[],
        )
    total_samples = sum(s.samples for s in segments)
    weighted = sum(s.avg_percent * s.samples for s in segments)
    return DirectionSummary(
        count=len(segments),
        max_duration_seconds=max(s.duration_seconds for s in segments),
        avg_duration_seconds=sum(s.duration_seconds for s in segments) / len(segments),
        max_percent=max(s.max_percent for s in segments),
        avg_percent=weighted / total_samples,
        segments=segments,
    )


def _overall(rows: list[PremiumRow], union: list[Segment]) -> Overall:
    fwds = [r.fwd for r in rows]
    revs = [r.rev for r in rows]
    return Overall(
        max_kimp_percent=max(fwds),
        avg_kimp_percent=sum(fwds) / len(fwds),
        max_reverse_percent=max(revs),
        avg_reverse_percent=sum(revs) / len(revs),
        max_duration_seconds=max((s.duration_seconds for s in union), default=0),
        avg_duration_seconds=(
            sum(s.duration_seconds for s in union) / len(union) if union else 0.0
        ),
        segment_count=len(union),
    )


def _streak_parts(
    rows: list[PremiumRow], threshold: float, max_gap: int
) -> tuple[DirectionSummary, DirectionSummary, Overall]:
    """행 목록 → (kimp, reverse, overall). fwd/rev 를 절댓값 없이 각각 계산한다."""
    kimp_segments = _segments([(r.ts, r.fwd) for r in rows], threshold, max_gap)
    rev_segments = _segments([(r.ts, r.rev) for r in rows], threshold, max_gap)
    union = kimp_segments + rev_segments
    return (
        _direction_summary(kimp_segments),
        _direction_summary(rev_segments),
        _overall(rows, union),
    )


# start 미지정 시 조회 창 (§3.4) — 전 구간 조회가 Influx 를 죽이므로 최근 7일만
DEFAULT_WINDOW_SEC = 7 * 86_400


def default_start(start: int | None, end_eff: int) -> int:
    """start 가 없으면 end − 7일 (음수는 0 으로 — Flux range 가 epoch 이전을 못 받는다)."""
    if start is not None:
        return start
    return max(0, end_eff - DEFAULT_WINDOW_SEC)


def build_streaks(
    reader: PremiumReader,
    *,
    dom: str,
    fx: str,
    base: str,
    threshold: float,
    start: int | None,
    end: int | None,
    max_gap: int,
) -> StreaksResponse:
    now_sec = int(time.time())
    end_eff = end if end is not None else now_sec + 1
    if end is not None and end_eff <= 0:
        # start 기본값(첫 ts ≥ 0)보다 항상 작거나 같다 — end ≤ start 규칙의 특수형
        raise HistoryApiError(400, "invalid_request", f"end({end_eff})가 0 이하입니다.")
    if start is not None and end_eff <= start:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"end({end_eff})가 start({start}) 이하입니다.",
        )
    start_eff = default_start(start, end_eff)
    rows = reader.query_premium(
        dom=dom,
        fx=fx,
        base=base.upper(),
        start=start_eff,
        stop=end_eff,
    )
    if not rows:
        raise HistoryApiError(
            404,
            "market_data_not_found",
            f"{base.upper()} 의 기록이 없습니다.",
            {"dom": dom, "fx": fx, "base": base.upper()},
        )
    kimp, reverse, overall = _streak_parts(rows, threshold, max_gap)
    last_ts = rows[-1].ts
    return StreaksResponse(
        base=base.upper(),
        dom=dom,
        fx=fx,
        threshold_percent=threshold,
        max_gap_seconds=max_gap,
        start_ts=start_eff,
        end_ts=end_eff,
        kimp=kimp,
        reverse=reverse,
        overall=overall,
        scanned=len(rows),
        last_updated_ts=last_ts,
        last_updated=_kst(last_ts),
        fetched_at=_now_ms(),
    )


def build_bulk(
    reader: PremiumReader,
    *,
    dom: str,
    fx: str,
    threshold: float,
    start: int | None,
    end: int | None,
    max_gap: int,
) -> BulkResponse:
    now_sec = int(time.time())
    end_eff = end if end is not None else now_sec + 1
    if start is not None and end_eff <= start:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"end({end_eff})가 start({start}) 이하입니다.",
        )
    start_eff = default_start(start, end_eff)
    rows = reader.query_premium(
        dom=dom, fx=fx, base=None, start=start_eff, stop=end_eff
    )
    by_base: dict[str, list[PremiumRow]] = {}
    for row in rows:
        by_base.setdefault(row.base, []).append(row)

    coins: list[BulkCoin] = []
    for base in sorted(by_base):
        coin_rows = by_base[base]
        kimp, reverse, overall = _streak_parts(coin_rows, threshold, max_gap)
        coins.append(
            BulkCoin(
                base=base,
                scanned=len(coin_rows),
                last_ts=coin_rows[-1].ts,
                kimp=kimp,
                reverse=reverse,
                overall=overall,
            )
        )
    # 기록 없으면 404 가 아니라 빈 coins (§3.4)
    return BulkResponse(
        dom=dom,
        fx=fx,
        threshold_percent=threshold,
        max_gap_seconds=max_gap,
        start_ts=start_eff,
        end_ts=end_eff,
        coin_count=len(coins),
        coins=coins,
        fetched_at=_now_ms(),
    )


# ── /history/events (스펙 013 §3.4) ─────────────────────────────────────────


def build_events(
    reader: EventReader,
    open_events: list[OpenEvent],
    *,
    start: int | None,
    end: int | None,
    dom: str | None,
    dir: str | None,
    base: str | None,
    now_sec: int | None = None,
) -> EventsResponse:
    """Influx 의 닫힌 사건 + 메모리의 진행 중 사건. 구간 판정은 start_ts 기준(`start ≤ start_ts < end`)."""
    now = now_sec if now_sec is not None else int(time.time())
    end_eff = end if end is not None else now + 1
    if start is not None and end_eff <= start:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"end({end_eff})가 start({start}) 이하입니다.",
        )
    start_eff = default_start(start, end_eff)
    base_u = base.upper() if base is not None else None
    rows = reader.query_premium_events(
        start=start_eff, stop=end_eff, dom=dom, dir=dir, base=base_u
    )
    by_key: dict[tuple[str, str, str, str, int], EventOut] = {}
    for r in rows:
        # 고아 점(end_ts 0 인데 메모리에 없음)은 복원 규칙과 같이 last_ts 에서 끝난 것으로 —
        # 아래에서 메모리의 진행 중 사건이 같은 키를 덮는다
        end_ts = r.end_ts or r.last_ts
        by_key[(r.dom, r.fx, r.base, r.dir, r.start_ts)] = EventOut(
            base=r.base,
            dom=r.dom,
            fx=r.fx,
            dir=r.dir,  # type: ignore[arg-type]
            start_ts=r.start_ts,
            end_ts=end_ts,
            duration_seconds=end_ts - r.start_ts,
            ongoing=False,
            max_percent=r.max_percent,
            max_ts=r.max_ts,
            samples=r.samples,
        )
    for ev in open_events:
        if dom is not None and ev.dom != dom:
            continue
        if dir is not None and ev.dir != dir:
            continue
        if base_u is not None and ev.base != base_u:
            continue
        if not (start_eff <= ev.start_ts < end_eff):
            continue
        if now - ev.start_ts <= MIN_DURATION_SEC:
            continue  # 열린 지 60초를 넘긴 것만 — 1분 못 넘길 스파이크는 아직 사건이 아니다
        by_key[(ev.dom, ev.fx, ev.base, ev.dir, ev.start_ts)] = EventOut(
            base=ev.base,
            dom=ev.dom,
            fx=ev.fx,
            dir=ev.dir,  # type: ignore[arg-type]
            start_ts=ev.start_ts,
            end_ts=None,
            duration_seconds=now - ev.start_ts,
            ongoing=True,
            max_percent=ev.max_percent,
            max_ts=ev.max_ts,
            samples=ev.samples,
        )
    events = sorted(by_key.values(), key=lambda e: (-e.start_ts, e.base))
    return EventsResponse(
        start_ts=start_eff,
        end_ts=end_eff,
        count=len(events),
        fetched_at=_now_ms(),
        events=events,
    )


# ── /history/candles ───────────────────────────────────────────────────────


def _tri_to_bool(v: int) -> bool | None:
    """저장값 1/0/−1 → true/false/null (014 §3.6)."""
    return None if v < 0 else bool(v)


def build_candles(
    reader: CandleReader,
    *,
    base: str,
    res: str,
    dom: str,
    fx: str,
    dir: str,
    start: int | None,
    end: int | None,
    now_sec: int | None = None,
) -> CandlesResponse:
    """계층 버킷 하나에서 `start ≤ 창 시작 < end` 인 봉 — 진행 중인 창은 없다(닫힌 창만 저장되므로).

    상한 = 1,440 × 창 길이. `end − start` 가 상한을 넘으면 400 — 실수로 수십만 점을 읽는 호출이 Influx 를 못 건드리게.
    """
    tier = TIER_BY_RES[res]
    limit = limit_sec(tier)
    end_eff = (
        end
        if end is not None
        else (now_sec if now_sec is not None else int(time.time()))
    )
    start_eff = start if start is not None else max(0, end_eff - limit)
    if end_eff <= start_eff:
        raise HistoryApiError(
            400, "invalid_request", f"end({end_eff})가 start({start_eff}) 이하입니다."
        )
    if end_eff - start_eff > limit:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"window exceeds limit: {res} 은 한 번에 {limit}초(1,440창)까지입니다.",
            {"limitSec": limit},
        )
    base_u = base.upper()
    rows = reader.query_candles(
        tier.bucket, start=start_eff, stop=end_eff, dom=dom, fx=fx, base=base_u
    )
    kimp = dir == "kimp"
    candles = [
        CandleOut(
            ts=r.ts,
            open=r.fwd_o if kimp else r.rev_o,
            high=r.fwd_h if kimp else r.rev_h,
            low=r.fwd_l if kimp else r.rev_l,
            close=r.fwd_c if kimp else r.rev_c,
            krw=r.krw,
            usdt=r.usdt,
            fx_rate=r.rate,
            # 방향 경로의 두 끝 — 김프는 해외 출금 → 국내 입금, 역프는 국내 출금 → 해외 입금
            deposit_ok=_tri_to_bool(r.dom_dep if kimp else r.fx_dep),
            withdraw_ok=_tri_to_bool(r.fx_wd if kimp else r.dom_wd),
            # 거래소별 4상태 — 차트가 거래소마다 입금·출금 줄을 따로 그린다(015). 경로 밖 칸도 값이 있어야 "모름" 이 안 뜬다
            dom_deposit_ok=_tri_to_bool(r.dom_dep),
            dom_withdraw_ok=_tri_to_bool(r.dom_wd),
            fx_deposit_ok=_tri_to_bool(r.fx_dep),
            fx_withdraw_ok=_tri_to_bool(r.fx_wd),
            blocked_sec=r.blocked_fwd_sec if kimp else r.blocked_rev_sec,
            samples=r.samples,
        )
        for r in sorted(rows, key=lambda r: r.ts)
    ]
    return CandlesResponse(
        base=base_u,
        res=res,  # type: ignore[arg-type]
        dom=dom,
        fx=fx,
        dir=dir,  # type: ignore[arg-type]
        start_ts=start_eff,
        end_ts=end_eff,
        count=len(candles),
        fetched_at=_now_ms(),
        candles=candles,
    )
