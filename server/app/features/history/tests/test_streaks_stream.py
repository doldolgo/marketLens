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


def _random_reader(seed: int, *, coins: int, seconds: int) -> FakeInfluxReader:
    """문턱을 넘나드는 값·수집 공백(maxGap 을 넘는 것 포함)·음의 0·아주 작은 값·큰 값이 섞인 1초 원값."""
    rnd = random.Random(seed)
    reader = FakeInfluxReader()
    for c in range(coins):
        rows = []
        ts = T0
        fwd = rnd.uniform(-1, 2)
        while ts < T0 + seconds:
            fwd += rnd.gauss(0, 0.15)
            special = rnd.random()
            if special < 0.01:
                value = -0.0
            elif special < 0.02:
                value = rnd.choice((1e-9, 1e15, -1e15, 0.5, 1.0))
            else:
                value = round(fwd, rnd.choice((2, 6, 12)))
            rows.append((ts, value, -value - rnd.uniform(0, 0.3)))
            ts += 1 if rnd.random() > 0.002 else rnd.choice((300, 601, 1_500))
        reader.seed("upbit", "binance", f"C{c:02d}", rows)
    return reader


# ── 같은 바이트 ─────────────────────────────────────────────────────────────


def test_streaks_stream_gives_the_same_bytes_as_the_row_list_computation() -> None:
    reader = _random_reader(7, coins=3, seconds=20_000)
    for base in ("C00", "C01", "C02"):
        for threshold in (0.0, 0.5, 1.0, 1.3):
            got = build_streaks(
                reader,  # type: ignore[arg-type]
                dom="upbit",
                fx="binance",
                base=base,
                threshold=threshold,
                start=T0,
                end=T0 + 20_000,
                max_gap=600,
            )
            want = _reference_streaks(reader, base, threshold, T0, T0 + 20_000)
            assert _masked(encode_model(got)) == want, (base, threshold)


def test_bulk_stream_gives_the_same_bytes_as_the_row_list_computation() -> None:
    reader = _random_reader(11, coins=12, seconds=3_600)
    for threshold in (0.0, 0.5, 1.0):
        got = build_bulk(
            reader,  # type: ignore[arg-type]
            dom="upbit",
            fx="binance",
            threshold=threshold,
            start=T0,
            end=T0 + 3_600,
            max_gap=600,
        )
        want = _reference_bulk(reader, threshold, T0, T0 + 3_600)
        assert _masked(encode_model(got)) == want, threshold
        # 라우터도 같은 바이트
        res = make_client(reader).get(
            "/history/streaks/bulk",
            params={"threshold": threshold, "start": T0, "end": T0 + 3_600},
        )
        assert _masked(res.content) == want


# ── 메모리 — 점 목록을 만들지 않는다 ────────────────────────────────────────


class _Endless:
    """점을 만들어 가며 흘려보내는 리더 — 들고 있는 것이 없어 메모리는 받는 쪽만 잰다."""

    def __init__(self, n: int) -> None:
        self.n = n

    def stream_premium(self, **kw: object) -> Iterator[tuple[str, str, int, float]]:
        for field, sign in (("fwd", 1.0), ("rev", -1.0)):
            for i in range(self.n):
                # 문턱 0.5 를 1,000점마다 넘나든다 — 구간은 수백 개, 점은 수십만 개
                yield "BTC", field, T0 + i, sign * (0.2 if i // 1_000 % 2 else 0.9)


def test_streaks_memory_grows_with_segments_not_points() -> None:
    reader = _Endless(100_000)
    tracemalloc.start()
    try:
        body = build_streaks(
            reader,  # type: ignore[arg-type]
            dom="upbit",
            fx="binance",
            base="BTC",
            threshold=0.5,
            start=T0,
            end=T0 + 200_000,
            max_gap=600,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert body.scanned == 100_000 and body.kimp.count == 50
    # 행 목록이면 점마다 100B 넘게(10MB+) — 흘려 세면 구간 모델 수십 개와 조각 하나뿐이다
    assert peak < 2_000_000, peak


# ── 실패·반쪽 점 ─────────────────────────────────────────────────────────────


class _BreaksMidway(FakeInfluxReader):
    def stream_premium(self, **kw: object) -> Iterator[tuple[str, str, int, float]]:  # type: ignore[override]
        yield "BTC", "fwd", T0, 1.0
        raise InfluxUnavailableError("연결 끊김 (테스트)")


def test_a_stream_that_breaks_midway_is_503_not_a_partial_answer() -> None:
    res = make_client(_BreaksMidway()).get(
        "/history/streaks", params={"base": "BTC", "start": T0, "end": T0 + 60}
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "storage_unavailable"


class _HalfPoints(FakeInfluxReader):
    """ETH 는 fwd 줄기만, BTC 는 fwd 가 한 점 더 있다 — 두 필드는 늘 같이 쓰이므로 실데이터에는 없는 모양."""

    def stream_premium(self, **kw: object) -> Iterator[tuple[str, str, int, float]]:  # type: ignore[override]
        yield from (("BTC", "fwd", T0 + i, 1.0) for i in range(3))
        yield from (("BTC", "rev", T0 + i, -1.0) for i in range(2))
        yield "ETH", "fwd", T0, 1.0


def test_half_points_are_counted_per_direction() -> None:
    client = make_client(_HalfPoints())
    body = client.get(
        "/history/streaks", params={"base": "BTC", "start": T0, "end": T0 + 60}
    ).json()
    # 방향마다 따로 센다 — scanned·lastUpdatedTs 는 fwd 줄기 기준
    assert body["kimp"]["segments"][0]["samples"] == 3
    assert body["scanned"] == 3 and body["lastUpdatedTs"] == T0 + 2
    # 한 방향 줄기뿐인 코인은 기록 없음과 같다
    res = client.get(
        "/history/streaks", params={"base": "ETH", "start": T0, "end": T0 + 60}
    )
    assert res.status_code == 404
    bulk = client.get(
        "/history/streaks/bulk", params={"start": T0, "end": T0 + 60}
    ).json()
    assert [c["base"] for c in bulk["coins"]] == ["BTC"]
