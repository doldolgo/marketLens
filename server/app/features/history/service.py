"""`/history/*` 계산 — 순수 계산. Influx 리더를 인자로 받고 전역을 import 하지 않는다.

리더는 `core.influx.InfluxClient` 시그니처의 일부(query_premium)만 쓴다 —
테스트는 같은 시그니처의 fake 를 넣어 Influx 없이 돈다 (architecture.md 원칙).
"""

import time
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal, Protocol

from app.core.influx import PremiumRow
from app.features.history.models import (
    BulkCoin,
    BulkResponse,
    DirectionSummary,
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
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            raise HistoryApiError(
                400,
                "invalid_request",
                f"date 형식이 잘못됐습니다: {date_str!r} (YYYY-MM-DD)",
            ) from None
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
    if start is not None and end_eff <= start:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"end({end_eff})가 start({start}) 이하입니다.",
        )
    rows = reader.query_premium(
        dom=dom,
        fx=fx,
        base=base.upper(),
        start=start if start is not None else 0,
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
        # start 미지정이면 그 코인 기록의 첫 ts (§3.4)
        start_ts=start if start is not None else rows[0].ts,
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
    start: int,
    end: int | None,
    max_gap: int,
) -> BulkResponse:
    now_sec = int(time.time())
    end_eff = end if end is not None else now_sec + 1
    if end_eff <= start:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"end({end_eff})가 start({start}) 이하입니다.",
        )
    rows = reader.query_premium(dom=dom, fx=fx, base=None, start=start, stop=end_eff)
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
        start_ts=start,
        end_ts=end_eff,
        coin_count=len(coins),
        coins=coins,
        fetched_at=_now_ms(),
    )
