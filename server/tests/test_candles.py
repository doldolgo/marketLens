"""1분 봉 집계기·쓰기·버킷·창 정렬·롤업·따라잡기 (스펙 014 §3.3~3.5, §4)."""

import logging

import pytest

from app.core.candles import (
    PENDING_LIMIT,
    TIERS,
    CandleAggregator,
    ensure_candle_buckets,
    window_start,
)
from tests.candle_fakes import T0, FakeCandleStore, candle, row, tick

M = 60
H = 3_600
D = 86_400


def fields(store: FakeCandleStore, bucket: str, ts: int, base: str = "BTC") -> dict:
    return store.data[bucket][("upbit", "binance", base, ts)]


def make(store: FakeCandleStore | None = None, now: float = 0.0):
    clock = [now]
    agg = CandleAggregator(store, clock=lambda: clock[0])
    return agg, clock


# ── 집계 (§3.3) ────────────────────────────────────────────────────────────────


async def test_minute_ohlc_samples_and_last_values() -> None:
    store = FakeCandleStore()
    agg, _ = make(store)
    agg.observe(tick(T0, row(0.5, dom_price=100.0, dom_dep=True)))
    agg.observe(tick(T0 + 1, row(0.9, dom_price=101.0)))
    agg.observe(
        tick(
            T0 + 2, row(0.7, dom_price=102.0, fx_price=71.0, rate=1410.0, dom_dep=None)
        )
    )
    agg.observe(tick(T0 + M))  # 분 닫힘
    await agg.flush()
    f = fields(store, "candles_1m", T0)
    assert (f["fwd_o"], f["fwd_h"], f["fwd_l"], f["fwd_c"], f["samples"]) == (
        0.5,
        0.9,
        0.5,
        0.7,
        3,
    )
    assert (f["rev_o"], f["rev_h"], f["rev_l"], f["rev_c"]) == (-0.5, -0.5, -0.9, -0.7)
    assert (f["krw"], f["usdt"], f["rate"]) == (102.0, 71.0, 1410.0)
    assert (f["dom_dep"], f["dom_wd"], f["fx_dep"], f["fx_wd"]) == (
        -1,
        1,
        1,
        1,
    )  # 마지막 행 값, None → −1


async def test_blocked_seconds_follow_direction_paths_and_ignore_unknown() -> None:
    store = FakeCandleStore()
    agg, _ = make(store)
    for i in range(10):
        agg.observe(tick(T0 + i, row(fx_wd=False)))  # 해외 출금 막힘 = 김프 경로만
    for i in range(10, 15):
        agg.observe(tick(T0 + i, row(fx_wd=None, dom_dep=None)))  # 모름은 세지 않는다
    for i in range(15, 18):
        agg.observe(
            tick(T0 + i, row(dom_wd=False, fx_dep=None))
        )  # 국내 출금 막힘 = 역프 경로만
    agg.observe(tick(T0 + M))
    await agg.flush()
    f = fields(store, "candles_1m", T0)
    assert (f["blocked_fwd_sec"], f["blocked_rev_sec"], f["samples"]) == (10, 3, 18)
    assert (f["dom_wd"], f["fx_dep"], f["fx_wd"]) == (
        0,
        -1,
        1,
    )  # True→1 False→0 None→−1


async def test_minute_closes_at_plus_60_and_only_present_combos_get_points() -> None:
    store = FakeCandleStore()
    agg, _ = make(store)
    agg.observe(tick(T0, row(base="BTC"), row(base="ETH")))
    agg.observe(tick(T0 + 59, row(base="BTC")))
    await agg.flush()
    assert store.writes == []  # 아직 열린 분
    agg.observe(tick(T0 + M, row(base="BTC")))  # 이 틱은 새 분에 속한다
    await agg.flush()
    keys = set(store.data["candles_1m"])
    assert keys == {("upbit", "binance", "BTC", T0), ("upbit", "binance", "ETH", T0)}
    assert fields(store, "candles_1m", T0)["samples"] == 2
    assert fields(store, "candles_1m", T0, "ETH")["samples"] == 1


async def test_tick_gap_closes_only_the_open_minute() -> None:
    store = FakeCandleStore()
    agg, _ = make(store)
    agg.observe(tick(T0, row()))
    agg.observe(
        tick(T0 + 120, row())
    )  # 120초 건너뜀 — T0 분만 닫히고 T0+60 분은 없는 것
    agg.observe(tick(T0 + 180, row()))
    await agg.flush()
    assert sorted(k[3] for k in store.data["candles_1m"]) == [T0, T0 + 120]


def test_without_influx_aggregation_runs_and_nothing_is_staged() -> None:
    agg, _ = make(None)
    agg.observe(tick(T0, row()))
    agg.observe(tick(T0 + M, row()))
    assert agg.pending_count == 0


# ── 쓰기 (§3.4) ────────────────────────────────────────────────────────────────


async def test_one_write_per_round_to_candles_1m_and_retry_after_failure() -> None:
    store = FakeCandleStore()
    agg, clock = make(store, now=T0 + M)
    agg.observe(tick(T0, row(base="BTC"), row(base="ETH")))
    agg.observe(tick(T0 + M, row(base="BTC")))
    store.fail = True
    await agg.write_round()
    assert agg.pending_count == 2 and store.writes == []
    store.fail = False
    await agg.write_round()
    assert agg.pending_count == 2  # 실패 뒤 60초 안엔 재시도 없음
    clock[0] = T0 + M + 60
    await agg.write_round()
    assert store.writes == [("candles_1m", 2)] and agg.pending_count == 0


async def test_pending_cap_drops_oldest_minutes_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeCandleStore()
    agg, _ = make(store)
    store.fail = True
    combos = 500
    minutes = PENDING_LIMIT // combos + 1  # 21분 × 500 = 10,500 → 500점 초과
    for i in range(minutes + 1):
        agg.observe(tick(T0 + i * M, *(row(base=f"C{k}") for k in range(combos))))
    with caplog.at_level(logging.WARNING, logger="marketlens.candles"):
        await agg.flush()
    assert agg.pending_count == PENDING_LIMIT
    store.fail = False
    await agg.flush()
    minutes_stored = sorted({k[3] for k in store.data["candles_1m"]})
    assert (
        minutes_stored[0] == T0 + M and len(minutes_stored) == minutes - 1
    )  # 첫 분이 통째로 빠졌다


async def test_ensure_buckets_creates_missing_keeps_existing_and_survives_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeCandleStore()
    store.buckets["candles_1h"] = 12_345  # 사람이 바꾼 retention
    await ensure_candle_buckets(store)
    assert store.buckets == {
        "marketlens": 0,
        "candles_1m": 604_800,
        "candles_5m": 2_592_000,
        "candles_1h": 12_345,
        "candles_4h": 31_536_000,
        "candles_1d": 0,
    }
    broken = FakeCandleStore()
    broken.list_fail = True
    with caplog.at_level(logging.WARNING, logger="marketlens.candles"):
        await ensure_candle_buckets(broken)  # 예외 없이 기동한다
    assert "봉 버킷 준비 실패" in caplog.text
    await ensure_candle_buckets(None)


# ── 창 정렬 (§3.4) ────────────────────────────────────────────────────────────


def test_windows_align_to_kst_wall_clock() -> None:
    # T0 = KST 자정. 4h 창 시작은 KST 00·04·…·20시, 1d 창 시작은 KST 자정
    assert window_start(T0 + 5 * H + 7, 4 * H) == T0 + 4 * H
    assert window_start(T0 + 23 * H, 4 * H) == T0 + 20 * H
    assert window_start(T0 + 23 * H + 59 * M, D) == T0
    assert window_start(T0 + D, D) == T0 + D
    # UTC 정렬과 다르다 — UTC 자정(T0 + 9h)은 1d 경계가 아니다
    assert window_start(T0 + 9 * H, D) == T0
    assert window_start(T0 + 7 * M + 3, 5 * M) == T0 + 5 * M


# ── 롤업 (§3.5) ────────────────────────────────────────────────────────────────


def seed_1m(store: FakeCandleStore, start: int, n: int, **kw) -> None:
    for i in range(n):
        store.seed("candles_1m", candle(start + i * M, **kw))


async def test_five_1m_fold_into_one_5m_with_fold_rules() -> None:
    store = FakeCandleStore()
    vals = [
        (0.5, 0.6, 0.4, 0.55),
        (0.55, 0.9, 0.5, 0.8),
        (0.8, 0.85, 0.3, 0.4),
        (0.4, 0.5, 0.35, 0.45),
        (0.45, 0.7, 0.44, 0.66),
    ]
    for i, (o, h, lo, c) in enumerate(vals):
        store.seed(
            "candles_1m",
            candle(
                T0 + i * M,
                o=o,
                h=h,
                lo=lo,
                c=c,
                krw=100.0 + i,
                blocked_fwd=i,
                samples=50 + i,
                dom_dep=0 if i == 4 else 1,
            ),
        )
    agg, _ = make(store, now=T0 + 5 * M)
    await agg.restore(T0 + 5 * M)
    agg.observe(tick(T0 + 5 * M))  # 1m 완료 시각 = 열린 분의 시작
    await agg.flush()
    f = fields(store, "candles_5m", T0)
    assert (f["fwd_o"], f["fwd_h"], f["fwd_l"], f["fwd_c"]) == (0.5, 0.9, 0.3, 0.66)
    assert (f["rev_o"], f["rev_h"], f["rev_l"], f["rev_c"]) == (-0.5, -0.3, -0.9, -0.66)
    assert (f["krw"], f["dom_dep"], f["blocked_fwd_sec"], f["samples"]) == (
        104.0,
        0,
        10,
        260,
    )


async def test_partial_lower_folds_and_empty_lower_gives_no_point() -> None:
    store = FakeCandleStore()
    seed_1m(store, T0, 3)  # 첫 5m 창에 3개뿐 — 그것으로 접는다. 둘째 창은 비어 있다
    agg, _ = make(store, now=T0 + 10 * M)
    await agg.restore(T0 + 10 * M)
    agg.observe(tick(T0 + 10 * M))
    await agg.flush()
    assert [r.ts for r in store.candles("candles_5m")] == [T0]
    assert fields(store, "candles_5m", T0)["samples"] == 180


async def test_chain_order_and_1h_reads_5m_written_in_same_round() -> None:
    store = FakeCandleStore()
    seed_1m(store, T0, 60)  # 정확히 1시간
    agg, _ = make(store, now=T0 + H)
    await agg.restore(T0 + H)
    agg.observe(tick(T0 + H, row()))  # 열린 분 — 1m 쓸 것은 아직 없다
    await agg.flush()
    buckets = [b for b, _ in store.writes]
    assert buckets == ["candles_5m"] * 12 + ["candles_1h"]
    assert fields(store, "candles_1h", T0)["samples"] == 3_600
    assert store.candles("candles_4h") == [] and store.candles("candles_1d") == []


async def test_at_most_12_windows_per_tier_per_round_and_future_windows_wait() -> None:
    store = FakeCandleStore()
    seed_1m(store, T0, 70)  # 14개의 5m 창, 마지막 창(T0+65m)은 끝이 now(T0+69m+1) 이후
    now = T0 + 69 * M + 1
    agg, _ = make(store, now=now)
    await agg.restore(now)
    agg.observe(tick(now))
    await agg.flush()
    assert [r.ts for r in store.candles("candles_5m")] == [
        T0 + i * 5 * M for i in range(12)
    ]
    await agg.flush()  # 13번째는 다음 회차, 14번째(끝 T0+70m > now)는 접지 않는다
    assert [r.ts for r in store.candles("candles_5m")] == [
        T0 + i * 5 * M for i in range(13)
    ]


async def test_rollup_failure_stops_round_and_resumes_after_gate() -> None:
    store = FakeCandleStore()
    seed_1m(store, T0, 10)
    agg, clock = make(store, now=T0 + 10 * M)
    await agg.restore(T0 + 10 * M)
    agg.observe(tick(T0 + 10 * M))
    store.query_fail = True
    await agg.write_round()
    assert store.candles("candles_5m") == []
    store.query_fail = False
    await agg.write_round()
    assert store.candles("candles_5m") == []  # 실패 뒤 60초 안엔 안 한다
    clock[0] = T0 + 11 * M
    await agg.write_round()
    assert [r.ts for r in store.candles("candles_5m")] == [T0, T0 + 5 * M]


async def test_upper_tier_never_passes_lower_tier_completion() -> None:
    """밀린 3시간: 첫 회차 1h 는 5m 이 접힌 첫 시간만, 나머지는 다음 회차들에서 — 빈 창을 확정하지 않는다."""
    store = FakeCandleStore()
    seed_1m(store, T0, 3 * 60)
    now = T0 + 3 * H
    agg, _ = make(store, now=now)
    await agg.restore(now)
    agg.observe(tick(now))
    await agg.flush()  # 5m: T0~T0+1h (12창), 1h: T0 창만 (T0+1h·T0+2h 창은 끝이 now 이전이지만 5m 이 아직 없다)
    assert [r.ts for r in store.candles("candles_1h")] == [T0]
    await agg.flush()
    assert [r.ts for r in store.candles("candles_1h")] == [T0, T0 + H]
    await agg.flush()
    assert [r.ts for r in store.candles("candles_1h")] == [T0, T0 + H, T0 + 2 * H]
    assert (
        fields(store, "candles_1h", T0 + 2 * H)["samples"] == 3_600
    )  # 구멍 없이 채워졌다
    assert store.candles("candles_4h") == []  # 4h 창(끝 T0+4h)은 아직 지금 이후


async def test_5m_stops_at_the_minute_the_aggregator_has_open() -> None:
    store = FakeCandleStore()
    seed_1m(
        store, T0, 10
    )  # Influx 엔 T0+9m 까지 있지만 집계기는 T0+7m 분을 열어 둔 상태
    agg, _ = make(store, now=T0 + 10 * M)
    await agg.restore(T0 + 10 * M)
    agg.observe(tick(T0 + 7 * M))
    await agg.flush()
    assert [r.ts for r in store.candles("candles_5m")] == [
        T0
    ]  # T0+5m 창(끝 T0+10m)은 1m 완료 시각(T0+7m)을 넘는다
    agg.observe(tick(T0 + 10 * M))
    await agg.flush()
    assert [r.ts for r in store.candles("candles_5m")] == [T0, T0 + 5 * M]


async def test_no_tick_yet_means_no_rollup_this_round() -> None:
    store = FakeCandleStore()
    seed_1m(store, T0, 10)
    agg, _ = make(store, now=T0 + 10 * M)
    await agg.restore(T0 + 10 * M)
    await agg.flush()  # 1m 완료 시각을 모른다 — 접지 않는다
    assert store.candles("candles_5m") == []


# ── 따라잡기 (§3.5) ────────────────────────────────────────────────────────────


async def test_restore_resumes_after_latest_upper_point() -> None:
    store = FakeCandleStore()
    seed_1m(store, T0, 15)
    store.seed("candles_5m", candle(T0))  # 첫 창은 이미 접혀 있다
    agg, _ = make(store, now=T0 + 15 * M)
    await agg.restore(T0 + 15 * M)
    assert agg.rollup.last_done("5m") == T0
    agg.observe(tick(T0 + 15 * M))
    await agg.flush()
    assert [r.ts for r in store.candles("candles_5m")] == [T0, T0 + 5 * M, T0 + 10 * M]
    assert (
        fields(store, "candles_5m", T0)["samples"] == 60
    )  # 있던 점은 다시 접지 않았다


async def test_restore_starts_from_oldest_lower_point_when_upper_is_empty() -> None:
    store = FakeCandleStore()
    seed_1m(store, T0 + 7 * M, 8)  # 1m 은 T0+7m 부터 — 첫 5m 창은 T0+5m
    now = T0 + 15 * M
    agg, _ = make(store, now=now)
    await agg.restore(now)
    assert agg.rollup.last_done("5m") == T0
    assert agg.rollup.last_done("1d") == window_start(T0 + 7 * M, D) - D
    agg.observe(tick(now))
    await agg.flush()
    assert [r.ts for r in store.candles("candles_5m")] == [T0 + 5 * M, T0 + 10 * M]


async def test_restore_failure_starts_from_now_window_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeCandleStore()
    seed_1m(store, T0, 30)
    store.query_fail = True
    now = T0 + 30 * M
    agg, _ = make(store, now=now)
    with caplog.at_level(logging.WARNING, logger="marketlens.candles"):
        await agg.restore(now)
    assert "기준점 조회 실패" in caplog.text
    assert (
        agg.rollup.last_done("5m") == now - 5 * M
    )  # 지금 창(끝 now+5m)부터 — 과거 창은 접지 않는다
    store.query_fail = False
    await agg.flush()
    assert store.candles("candles_5m") == []


async def test_restore_empty_buckets_then_first_minutes_fold_when_window_ends() -> None:
    store = FakeCandleStore()
    agg, clock = make(store, now=T0)
    await agg.restore(T0)
    for i in range(5 * M + 1):  # T0 ~ T0+5m 틱 — 5개 분이 닫힌다
        agg.observe(tick(T0 + i, row()))
    clock[0] = T0 + 5 * M
    await agg.flush()
    assert [r.ts for r in store.candles("candles_1m")] == [T0 + i * M for i in range(5)]
    assert [r.ts for r in store.candles("candles_5m")] == [T0]
    assert fields(store, "candles_5m", T0)["samples"] == 300
    assert TIERS[0].bucket == "candles_1m"
