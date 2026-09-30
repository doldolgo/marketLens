"""streaks·bulk 흘려 세기 — 행 목록을 만든 계산과 같은 바이트, 메모리는 구간 수에 비례 (005 §3.4, 2026-09-28).

기준선은 이 파일의 `_reference_*` — 저장소가 pivot 해 준 (ts, fwd, rev) 행 목록을 전부 올려 구간을 세던 계산을
그대로 옮긴 것이다. 새 경로는 (코인, 방향) 줄기마다 흘러오는 점을 상태기계로 센다.
"""

import random
import re
import tracemalloc
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from app.core.influx import InfluxUnavailableError, PremiumRow
from app.features.history.models import (
    BulkCoin,
    BulkResponse,
    DirectionSummary,
    Overall,
    Segment,
    StreaksResponse,
)
from app.features.history.service import build_bulk, build_streaks, encode_model
from app.features.history.tests.helpers import FakeInfluxReader, make_client

T0 = 1_700_000_000
KST = timezone(timedelta(hours=9))


def _kst(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=KST).isoformat()


# ── 기준선: 행 목록을 만든 계산 ─────────────────────────────────────────────


def _reference_segments(
    points: list[tuple[int, float]], threshold: float, max_gap: int
) -> list[Segment]:
    segments: list[Segment] = []
    cur: list[tuple[int, float]] = []

    def close() -> None:
        if not cur:
            return
        values = [v for _, v in cur]
        s, e = cur[0][0], cur[-1][0]
        segments.append(
            Segment(
                start_ts=s,
                end_ts=e,
                start=_kst(s),
                end=_kst(e),
                duration_seconds=e - s,
                samples=len(cur),
                max_percent=max(values),
                avg_percent=sum(values) / len(values),
            )
        )
        cur.clear()

    prev: int | None = None
    for ts, v in points:
        if prev is not None and ts - prev > max_gap:
            close()
        if v >= threshold:
            cur.append((ts, v))
        else:
            close()
        prev = ts
    close()
    return segments


def _reference_summary(segs: list[Segment]) -> DirectionSummary:
    if not segs:
        return DirectionSummary(
            count=0,
            max_duration_seconds=0,
            avg_duration_seconds=0.0,
            max_percent=0.0,
            avg_percent=0.0,
            segments=[],
        )
    return DirectionSummary(
        count=len(segs),
        max_duration_seconds=max(s.duration_seconds for s in segs),
        avg_duration_seconds=sum(s.duration_seconds for s in segs) / len(segs),
        max_percent=max(s.max_percent for s in segs),
        avg_percent=sum(s.avg_percent * s.samples for s in segs)
        / sum(s.samples for s in segs),
        segments=segs,
    )


def _reference_parts(
    rows: list[PremiumRow], threshold: float, max_gap: int
) -> tuple[DirectionSummary, DirectionSummary, Overall]:
    k = _reference_segments([(r.ts, r.fwd) for r in rows], threshold, max_gap)
    rv = _reference_segments([(r.ts, r.rev) for r in rows], threshold, max_gap)
    union = k + rv
    fwds, revs = [r.fwd for r in rows], [r.rev for r in rows]
    overall = Overall(
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
    return _reference_summary(k), _reference_summary(rv), overall


def _masked(body: bytes) -> bytes:
    return re.sub(rb'"fetchedAt":\d+', b'"fetchedAt":0', body)


def _reference_streaks(
    reader: FakeInfluxReader, base: str, threshold: float, start: int, end: int
) -> bytes:
    rows = reader.query_premium(
        dom="upbit", fx="binance", base=base, start=start, stop=end
    )
    kimp, reverse, overall = _reference_parts(rows, threshold, 600)
    return _masked(
        encode_model(
            StreaksResponse(
                base=base,
                dom="upbit",
                fx="binance",
                threshold_percent=threshold,
                max_gap_seconds=600,
                start_ts=start,
                end_ts=end,
                kimp=kimp,
                reverse=reverse,
                overall=overall,
                scanned=len(rows),
                last_updated_ts=rows[-1].ts,
                last_updated=_kst(rows[-1].ts),
                fetched_at=0,
            )
        )
    )


def _reference_bulk(
    reader: FakeInfluxReader, threshold: float, start: int, end: int
) -> bytes:
    rows = reader.query_premium(
        dom="upbit", fx="binance", base=None, start=start, stop=end
    )
    by_base: dict[str, list[PremiumRow]] = {}
    for r in rows:
        by_base.setdefault(r.base, []).append(r)
    coins = []
    for b in sorted(by_base):
        kimp, reverse, overall = _reference_parts(by_base[b], threshold, 600)
        coins.append(
            BulkCoin(
                base=b,
                scanned=len(by_base[b]),
                last_ts=by_base[b][-1].ts,
                kimp=kimp,
                reverse=reverse,
                overall=overall,
            )
        )
    return _masked(
        encode_model(
            BulkResponse(
                dom="upbit",
                fx="binance",
                threshold_percent=threshold,
                max_gap_seconds=600,
                start_ts=start,
                end_ts=end,
                coin_count=len(coins),
                coins=coins,
                fetched_at=0,
            )
        )
    )
