"""과거 사건 백필 — 틱 다시 만들기·빈 틱·경계·쓰는 점·다시 돌리기·조각·검증 비교 (스펙 084 §3·§4). 네트워크 없음."""

import random

import pytest

import scripts.premium_events_backfill as bf
from app.core.influx import EventListRow, InfluxPoint, InfluxUnavailableError
from app.core.models import Tick, TickRow
from app.core.premium_events import PremiumEventDetector
from scripts.premium_events_backfill import (
    CUTOFF,
    DAY,
    Replay,
    build_ticks,
    compare,
    plan_chunks,
)
from tests.premium_event_fakes import FakeInflux

T0 = 1_780_000_000  # 경계 C 전, UTC 날 경계가 아니어도 된다


def _pts(
    seq: list[tuple[int, float]], dom: str = "upbit", fx: str = "binance"
) -> list[tuple[str, str, str, int, float]]:
    """(ts, 값) 열 → 김프 값만 움직이는 저장 점(역프는 −값 − 0.2, 문턱 아래)."""
    out = []
    for ts, v in seq:
        out.append((dom, fx, "fwd", ts, v))
        out.append((dom, fx, "rev", ts, -v - 0.2))
    return out


@pytest.fixture(autouse=True)
def _clear_detector_warnings() -> None:
    # 감지기 경고 모음은 프로세스 전역이다 — 한 시험의 경고가 다음 시험의 코인을 멈추지 않게
    bf._warnings.messages.clear()


async def _replay(
    ticks: list[Tick], boundary: int, *, close_open: bool = True
) -> Replay:
    rep = Replay()
    await rep.feed(ticks)
    await rep.finish(boundary, close_open=close_open)
    return rep


def _events(points: list[InfluxPoint]) -> dict[tuple, dict]:
    return {bf.point_key(p): dict(p.fields) for p in points}


async def _dense_reference(ticks: list[Tick], boundary: int) -> dict[tuple, dict]:
    """운영 기준 — 첫 틱부터 경계 − 1 까지 매초 틱이 돌고, 저장 점이 없는 초는 행 없는 틱이다. 닫힌 사건만."""
    closed: list[InfluxPoint] = []

    class Sink:
        def write(self, points: list[InfluxPoint]) -> None:
            closed.extend(p for p in points if p.fields["end_ts"])

    det = PremiumEventDetector(writer=Sink())
    by_ts = {t.ts: t for t in ticks}
    for s in range(ticks[0].ts, boundary):
        det.observe(by_ts.get(s) or Tick(ts=s, rows=(), dw_failed=()))
        if s % 300 == 0:
            await det.flush()
    await det.flush()
    return _events(closed)


# ── 틱 다시 만들기 (§3.3) ────────────────────────────────────────────────────


def test_points_of_the_same_second_become_one_tick_and_half_points_are_skipped() -> (
    None
):
    pts = [
        # 줄기마다 이어서 온다 — 순서는 상관없다
        ("upbit", "binance", "fwd", T0 + 1, 1.5),
        ("upbit", "binance", "fwd", T0, 0.2),
        ("bithumb", "binance", "rev", T0, 0.3),
        ("bithumb", "binance", "fwd", T0, 0.1),
        ("upbit", "binance", "rev", T0, -0.4),
        ("upbit", "binance", "rev", T0 + 1, -1.7),
        # 반쪽 점 — 그 초에 빗썸 fwd 만
        ("bithumb", "binance", "fwd", T0 + 2, 9.0),
    ]
    ticks = build_ticks("ABC", pts)
    assert [t.ts for t in ticks] == [T0, T0 + 1]
    assert ticks[0].rows == (
        TickRow("bithumb", "binance", "ABC", 0.1, 0.3),
        TickRow("upbit", "binance", "ABC", 0.2, -0.4),
    )
    assert ticks[1].rows == (TickRow("upbit", "binance", "ABC", 1.5, -1.7),)
    assert all(r.net_dom is None and r.net_fx is None for t in ticks for r in t.rows)


# ── 빈 틱 — 결측 판정이 운영(매초 틱)과 같다 (§3.3) ─────────────────────────


@pytest.mark.parametrize(
    ("back_after", "events"),
    [
        (
            601,
            [(T0, T0 + 900)],
        ),  # 601초 만에 돌아오면 이어진다 — 닫힘은 돌아온 뒤 0.5 이하에서
        (602, [(T0, T0 + 100), (T0 + 702, T0 + 900)]),  # 602초면 L 로 닫히고 새 사건
    ],
)
async def test_rows_back_after_gap_continue_or_open_new_like_production(
    back_after: int, events: list[tuple[int, int]]
) -> None:
    last = T0 + 100
    seq = [(T0 + i, 1.2) for i in range(0, 101)]
    seq += [
        (last + back_after + i, 1.2) for i in range(0, 900 - last - back_after + T0)
    ]
    seq += [(T0 + 900, 0.3)]
    ticks = build_ticks("ABC", _pts(seq))
    rep = await _replay(ticks, T0 + 2000)
    got = sorted((p.ts, p.fields["end_ts"]) for p in rep.closed)
    assert got == events
    assert _events(rep.closed) == await _dense_reference(ticks, T0 + 2000)


def _random_series(rng: random.Random, start: int, n: int) -> list[tuple[int, float]]:
    """초 단위 틱·분당 점·변동분 점이 섞이고 결측·문턱을 오가는 값."""
    out = []
    ts = start
    v = 0.0
    for _ in range(n):
        r = rng.random()
        if r < 0.6:
            ts += 1
        elif r < 0.8:
            ts += rng.randint(2, 70)  # 변동분·분당 점
        elif r < 0.97:
            ts += rng.randint(55, 65)
        else:
            ts += rng.choice([599, 600, 601, 602, 603, rng.randint(604, 2000)])
        v = max(-2.0, min(3.0, v + rng.uniform(-0.35, 0.35)))
        if rng.random() < 0.05:
            v = rng.choice([0.5, 1.0, 0.4999, 1.0001])
        out.append((ts, round(v, 4)))
    return out


@pytest.mark.parametrize("seed", range(6))
async def test_sparse_replay_equals_production_tick_stream(seed: int) -> None:
    rng = random.Random(seed)
    pts = _pts(_random_series(rng, T0, 3_000))
    pts += _pts(_random_series(rng, T0 + 7, 2_500), dom="bithumb")
    # 역프가 움직이는 쌍도 — 김프 값과 거울
    pts += [
        (d, f, "rev" if fld == "fwd" else "fwd", ts, v)
        for d, f, fld, ts, v in _pts(_random_series(rng, T0 + 3, 2_000), fx="bybit")
    ]
    ticks = build_ticks("ABC", pts)
    boundary = ticks[-1].ts + 5_000
    rep = await _replay(ticks, boundary, close_open=False)
    ref = await _dense_reference(ticks, boundary)
    assert len(ref) > 20
    assert _events(rep.closed) == ref


# ── 쓰는 점 (§3.5) ─────────────────────────────────────────────────────────


async def test_only_closed_events_are_kept_with_no_network_and_fixed_thresholds() -> (
    None
):
    seq = [(T0 + i, 1.0 + i / 1000) for i in range(0, 200)] + [(T0 + 200, 0.5)]
    seq += [
        (T0 + 300 + i, 1.4) for i in range(0, 400)
    ]  # 경계까지 열린 채 — 진행 중 점은 받지 않는다
    rep = Replay()
    await rep.feed(build_ticks("ABC", _pts(seq)))
    await rep.finish(T0 + 700, close_open=False)
    assert [p.ts for p in rep.closed] == [T0]
    f = rep.closed[0].fields
    assert rep.closed[0].measurement == "premium_event"
    assert rep.closed[0].tags == {
        "dom": "upbit",
        "fx": "binance",
        "base": "ABC",
        "dir": "kimp",
    }
    assert (f["end_ts"], f["duration_seconds"], f["samples"], f["last_ts"]) == (
        T0 + 200,
        200,
        200,
        T0 + 199,
    )
    assert (f["max_percent"], f["max_ts"]) == (1.0 + 199 / 1000, T0 + 199)
    assert (f["enter_percent"], f["exit_percent"]) == (1.0, 0.5)
    assert (f["net_dom"], f["net_fx"]) == ("-", "-")


# ── 경계 C (§3.4) ───────────────────────────────────────────────────────────


async def test_boundary_closes_written_events_at_last_ts_and_drops_young_ones() -> None:
    c = T0 + 10_000
    pts = _pts(
        [(c - 500 + i, 1.3) for i in range(0, 480)]
    )  # 열린 지 499초, 끝 관측 c − 21
    pts += _pts(
        [(c - 40 + i, 1.1) for i in range(0, 40)], dom="bithumb"
    )  # 열린 지 39초
    pts += _pts(
        [(c - 2_000 + i, 1.6) for i in range(0, 300)], fx="okx"
    )  # c 전 600초 넘게 끊김
    rep = await _replay(build_ticks("ABC", pts), c)
    got = {(p.tags["dom"], p.tags["fx"]): p.fields for p in rep.closed}
    assert set(got) == {("upbit", "binance"), ("upbit", "okx")}
    assert (
        got[("upbit", "binance")]["end_ts"],
        got[("upbit", "binance")]["last_ts"],
    ) == (
        c - 21,
        c - 21,
    )
    assert (
        got[("upbit", "okx")]["end_ts"] == c - 2_000 + 299
    )  # 결측 닫힘(운영과 같은 규칙)
    assert (rep.boundary_closed, rep.boundary_dropped) == (1, 1)
    assert all(p.ts < c and int(p.fields["end_ts"]) < c for p in rep.closed)


async def test_last_tick_long_before_boundary_still_closes_by_gap_rule() -> None:
    c = T0 + 50_000
    rep = await _replay(build_ticks("ABC", _pts([(T0 + i, 1.5) for i in range(90)])), c)
    assert [(p.ts, p.fields["end_ts"]) for p in rep.closed] == [(T0, T0 + 89)]
    assert (rep.boundary_closed, rep.boundary_dropped) == (0, 0)


# ── 다시 돌리기·받아 가기 (§3.3·§3.5) ───────────────────────────────────────


async def test_running_twice_writes_the_same_points() -> None:
    rng = random.Random(7)
    ticks = build_ticks("ABC", _pts(_random_series(rng, T0, 4_000)))
    store = FakeInflux()
    first = await _replay(ticks, ticks[-1].ts + 10)
    store.write(first.closed)
    snapshot = dict(store.data)
    second = await _replay(ticks, ticks[-1].ts + 10)
    store.write(second.closed)
    assert first.closed == second.closed
    assert store.data == snapshot and len(store.data) == len(first.closed) > 10


async def test_hundreds_of_closes_per_hour_are_not_dropped() -> None:
    # 쌍 8개 × 62초마다 열고 닫는 사건 3시간 — 한 시간에 닫힘 460여 건, 합 1,300여 건(상한 1,000 넘음)
    pairs = [
        (d, f)
        for d in ("upbit", "bithumb")
        for f in ("binance", "bybit", "bitget", "okx")
    ]
    pts = []
    for d, f in pairs:
        seq = []
        for k in range(3 * 3600 // 63):
            s = T0 + k * 63
            seq += [(s + i, 1.2) for i in range(62)] + [(s + 62, 0.1)]
        pts += _pts(seq, d, f)
    ticks = build_ticks("ABC", pts)
    rep = await _replay(ticks, ticks[-1].ts + 1)
    assert len(rep.closed) == len(pairs) * (3 * 3600 // 63) > 1_000
    assert bf._warnings.messages == []


# ── 조각 (§3.6) ────────────────────────────────────────────────────────────


def test_plan_chunks_groups_days_within_budget_and_splits_big_days() -> None:
    d0 = 20_500 * DAY
    days = [
        (d0, 30),
        (d0 + DAY, 50),
        (d0 + 3 * DAY, 30),
        (d0 + 4 * DAY, 250),
        (d0 + 5 * DAY, 10),
    ]
    chunks = plan_chunks(days, 0, d0 + 6 * DAY, budget=100)
    assert chunks == [
        (d0, d0 + 2 * DAY),  # 30 + 50 — 다음 날 30 이면 넘는다
        (d0 + 3 * DAY, d0 + 4 * DAY),
        # 250 > 100 → 같은 길이 셋
        (d0 + 4 * DAY, d0 + 4 * DAY + 28_800),
        (d0 + 4 * DAY + 28_800, d0 + 4 * DAY + 57_600),
        (d0 + 4 * DAY + 57_600, d0 + 5 * DAY),
        (d0 + 5 * DAY, d0 + 6 * DAY),
    ]
    # 창 시작·끝이 날 중간이면 그 안으로 자른다
    assert plan_chunks([(d0 + 100, 10)], d0 + 100, d0 + 500) == [(d0 + 100, d0 + 500)]


async def test_chunked_replay_equals_one_chunk() -> None:
    rng = random.Random(11)
    pts = _pts(_random_series(rng, T0, 5_000)) + _pts(
        _random_series(rng, T0, 5_000), dom="bithumb"
    )
    boundary = max(p[3] for p in pts) + 100
    whole = await _replay(build_ticks("ABC", pts), boundary)
    edges = sorted({T0 - 1, boundary, *rng.sample(range(T0, boundary), 9)})
    rep = Replay()
    for a, b in zip(edges, edges[1:], strict=False):
        await rep.feed(build_ticks("ABC", [p for p in pts if a <= p[3] < b]))
    await rep.finish(boundary, close_open=True)
    assert rep.closed == whole.closed and len(whole.closed) > 10


# ── 실행 — dry-run·실패 (§3.5·§3.6) ────────────────────────────────────────


class _Store(FakeInflux):
    """백필 스크립트가 부르는 조회 셋 + 덮어쓰기 쓰기."""

    def __init__(
        self, points: dict[str, list[tuple[str, str, str, int, float]]]
    ) -> None:
        super().__init__()
        self.points = points
        self.fail_bases: set[str] = set()
        self.stream_calls = 0

    def premium_day_counts(self, *, start: int, stop: int, base: str | None = None):  # noqa: ANN201
        counts: dict[tuple[str, int], int] = {}
        for b, pts in self.points.items():
            for _d, _f, fld, ts, _v in pts:
                if fld == "fwd" and start <= ts < stop:
                    day = max(ts // DAY * DAY, start)
                    counts[(b, day)] = counts.get((b, day), 0) + 1
        return sorted((b, d, n) for (b, d), n in counts.items())

    def stream_premium_base(self, *, base: str, start: int, stop: int):  # noqa: ANN201
        self.stream_calls += 1
        if base in self.fail_bases:
            raise InfluxUnavailableError("조회 실패 (테스트)")
        return iter([p for p in self.points[base] if start <= p[3] < stop])


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bf, "QUERY_SLEEP_MIN", 0.0)
    monkeypatch.setattr(bf, "QUERY_SLEEP_RATIO", 0.0)
    monkeypatch.setattr(bf, "WRITE_SLEEP", 0.0)
    monkeypatch.setattr(bf, "RETRY_SLEEP", 0.0)


def _store() -> _Store:
    a = _pts([(T0 + i, 1.4) for i in range(100)] + [(T0 + 100, 0.2)])
    b = _pts([(T0 + 5 + i, 1.1) for i in range(300)] + [(T0 + 305, 0.0)], dom="bithumb")
    return _Store({"AAA": a, "BBB": b})


async def test_dry_run_writes_nothing_and_write_writes_closed_events(
    no_sleep: None,
) -> None:
    store = _store()
    assert await bf.run_backfill(store, [], write=False) == 0
    assert store.write_calls == 0
    assert await bf.run_backfill(store, [], write=True) == 0
    got = sorted((dict(tags)["base"], ts) for _m, tags, ts in store.data)
    assert got == [("AAA", T0), ("BBB", T0 + 5)]
    assert all(int(f["end_ts"]) < CUTOFF for f in store.data.values())


async def test_query_failing_after_retries_stops_with_only_closed_events_written(
    no_sleep: None,
) -> None:
    store = _store()
    store.fail_bases = {"BBB"}
    with pytest.raises(InfluxUnavailableError):
        await bf.run_backfill(store, ["AAA", "BBB"], write=True)
    assert store.stream_calls == 1 + (bf.RETRIES + 1)
    assert len(store.data) == 1  # AAA 의 닫힌 사건 하나
    assert all(int(f["end_ts"]) > 0 for f in store.data.values())


async def test_verify_writes_nothing(no_sleep: None) -> None:
    store = _store()
    s = CUTOFF + 10 * DAY
    store.points = {
        "AAA": _pts([(s + 50 + i, 1.4) for i in range(100)] + [(s + 150, 0.2)])
    }
    store.rows = []
    assert await bf.run_verify(store, ["AAA"], s, s + DAY) == 0
    assert store.write_calls == 0


# ── 검증 비교 (§3.7) ───────────────────────────────────────────────────────


def _ev(
    start: int,
    end: int,
    *,
    base: str = "ABC",
    last: int | None = None,
    samples: int = 10,
) -> EventListRow:
    return EventListRow(
        dom="upbit",
        fx="binance",
        base=base,
        dir="kimp",
        start_ts=start,
        end_ts=end,
        max_percent=1.5,
        max_ts=start,
        last_ts=last if last is not None else max(end - 1, start),
        samples=samples,
        net_dom=None,
        net_fx=None,
    )


def _pt(e: EventListRow) -> InfluxPoint:
    return InfluxPoint(
        measurement="premium_event",
        tags={"dom": e.dom, "fx": e.fx, "base": e.base, "dir": e.dir},
        fields={
            "end_ts": e.end_ts,
            "max_percent": e.max_percent,
            "max_ts": e.max_ts,
            "last_ts": e.last_ts,
            "samples": e.samples,
        },
        ts=e.start_ts,
    )


def test_compare_counts_matches_and_differences_inside_the_comparable_part() -> None:
    w0, w1 = T0, T0 + DAY
    prod = [
        _ev(w0 + 100, w0 + 500),
        _ev(w0 + 1_000, w0 + 2_000, samples=900),
        _ev(w0 + 3_000, w0 + 3_100),  # 재생에 없음
        _ev(w1 - 1_000, w1 - 300),  # 창 끝 − 610초보다 늦게 끝남 — 안 센다
    ]
    rep = [
        _pt(prod[0]),
        _pt(_ev(w0 + 1_000, w0 + 2_000, samples=899)),
        _pt(_ev(w0 + 4_000, w0 + 4_200)),
        _pt(prod[3]),
    ]
    cmp = compare(prod, rep, w0, w1)
    assert (cmp.prod, cmp.replay, cmp.matched) == (3, 3, 1)
    assert [k[4] for k in cmp.only_prod] == [w0 + 3_000]
    assert [k[4] for k in cmp.only_replay] == [w0 + 4_000]
    assert cmp.differ[0][1] == {"samples": (900.0, 899.0)}
    assert dict(cmp.field_diffs) == {"samples": 1}


def test_compare_starts_each_combo_after_production_events_open_before_the_window() -> (
    None
):
    w0, w1 = T0, T0 + DAY
    prod = [
        _ev(
            w0 - 5_000, w0 + 200
        ),  # 창 전에 열려 창 안에서 닫힘 → 이 조합은 w0 + 801 부터
        _ev(w0 + 700, w0 + 790),  # 그 전 — 안 센다
        _ev(w0 + 900, w0 + 1_000),
        _ev(w0 - 9_000, 0, base="XYZ", last=w1 + 50),  # 지금도 진행 중 → 조합째 빠진다
        _ev(w0 + 5_000, w0 + 6_000, base="XYZ"),
        _ev(w0 - 9_000, 0, base="OLD", last=w0 - 5_000),  # 오래된 고아 점 — 창과 무관
        _ev(w0 + 50, w0 + 150, base="OLD"),
    ]
    rep = [_pt(_ev(w0, w0 + 200)), _pt(prod[2]), _pt(prod[4]), _pt(prod[6])]
    cmp = compare(prod, rep, w0, w1)
    assert (cmp.prod, cmp.replay, cmp.matched) == (2, 2, 2)
    assert cmp.excluded_combos == 1
    assert cmp.only_prod == [] and cmp.only_replay == []
