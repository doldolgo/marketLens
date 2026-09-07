"""1분 봉 집계기·쓰기·버킷·창 정렬 (스펙 014 §3.3~3.4, §4)."""

import logging

import pytest

from app.core.candles import (
    PENDING_LIMIT,
    CandleAggregator,
    ensure_candle_buckets,
    window_start,
)
from tests.candle_fakes import T0, FakeCandleStore, row, tick

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
