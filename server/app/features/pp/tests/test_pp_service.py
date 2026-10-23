"""049 §4 — 표 계산: longA·longB 식, 조합(순서 없는 쌍 하나 = 행 하나), 코인 범위, 정렬, status·age, 표 JSON 모양,
2,700행 생성 시간 (네트워크 없음)."""

import json
import math
import time
from datetime import timedelta

import pytest

from app.core.config import PERP_SOURCES
from app.core.live_store import LiveStore
from app.core.premium import premium_percent
from app.features.pp.push import encode_pp_table
from app.features.pp.service import build_pp_table
from app.features.pp.tests.helpers import ROW_KEYS, T0, perp


def rows_of(store: LiveStore, now=T0) -> list[dict]:  # noqa: ANN001
    return build_pp_table(store, now=now)["rows"]  # type: ignore[return-value]


def test_long_a_and_long_b_use_best_quotes_both_ways() -> None:
    store = LiveStore()
    perp(store, "binance_perp", "BTC", ask=100.0, bid=99.9)
    perp(store, "bybit_perp", "BTC", ask=100.3, bid=100.2)
    [row] = rows_of(store)
    assert list(row) == ROW_KEYS
    assert (row["a"], row["b"]) == ("binance_perp", "bybit_perp")
    # a 의 ask 100 에 롱·b 의 bid 100.2 에 숏 → 0.2%
    assert row["longA"] == pytest.approx(0.2)
    # b 의 ask 100.3 에 롱·a 의 bid 99.9 에 숏
    assert row["longB"] == pytest.approx((99.9 / 100.3 - 1) * 100)
    # core 식과 비트까지 같다
    assert row["longA"] == premium_percent(buy_krw=100.0, sell_krw=100.2)
    assert row["longB"] == premium_percent(buy_krw=100.3, sell_krw=99.9)
    assert row["status"] == "ok" and row["age"] == 0.0


def test_funding_three_values_each_side_are_carried_as_is() -> None:
    store = LiveStore()
    perp(
        store,
        "bitget_perp",
        "ETH",
        funding_rate=0.0002,
        funding_interval_h=4,
        next_funding_ms=1_791_460_800_000,
    )
    perp(
        store,
        "hyperliquid_perp",
        "ETH",
        funding_rate=0.0000125,
        funding_interval_h=1,
        next_funding_ms=1_791_464_400_999,
    )
    [row] = rows_of(store)
    assert (row["a"], row["b"]) == ("bitget_perp", "hyperliquid_perp")
    assert row["fundA"] == pytest.approx(
        0.02
    )  # 한 주기 퍼센트 — 시간당은 화면이 만든다
    assert (row["intA"], row["nextA"]) == (4, 1_791_460_800)
    assert row["fundB"] == pytest.approx(0.00125)
    assert (row["intB"], row["nextB"]) == (1, 1_791_464_400)  # ms → 초(내림)


def test_unknown_funding_is_null() -> None:
    store = LiveStore()
    perp(
        store,
        "binance_perp",
        "SOL",
        funding_rate=None,
        next_funding_ms=None,
        funding_interval_h=None,
    )
    perp(store, "bybit_perp", "SOL")
    [row] = rows_of(store)
    assert (row["fundA"], row["intA"], row["nextA"]) == (None, None, None)
    assert row["status"] == "ok"


def test_one_unordered_pair_per_row_and_count_by_sources() -> None:
    store = LiveStore()
    for src in PERP_SOURCES:  # 네 곳 → 6행
        perp(store, src, "BTC")
    for src in PERP_SOURCES[1:]:  # 세 곳 → 3행
        perp(store, src, "ETH")
    perp(store, "bybit_perp", "SOLO")  # 한 곳 → 행 없음
    rows = rows_of(store)
    btc = [(r["a"], r["b"]) for r in rows if r["sym"] == "BTC"]
    assert btc == [
        ("binance_perp", "bybit_perp"),
        ("binance_perp", "bitget_perp"),
        ("binance_perp", "hyperliquid_perp"),
        ("bybit_perp", "bitget_perp"),
        ("bybit_perp", "hyperliquid_perp"),
        ("bitget_perp", "hyperliquid_perp"),
    ]
    assert len([r for r in rows if r["sym"] == "ETH"]) == 3
    assert not [r for r in rows if r["sym"] == "SOLO"]
    # (b, a) 행은 없다 — 순서는 원천 순서(사전순이 아니다: bybit 가 bitget 보다 앞)
    order = {s: i for i, s in enumerate(PERP_SOURCES)}
    assert all(order[r["a"]] < order[r["b"]] for r in rows)


def test_rows_sorted_by_sym_then_source_order() -> None:
    store = LiveStore()
    for sym in ("ZRX", "ARB", "BTC"):
        perp(store, "hyperliquid_perp", sym)
        perp(store, "bybit_perp", sym)
    assert [r["sym"] for r in rows_of(store)] == ["ARB", "BTC", "ZRX"]


def test_coin_range_is_the_whole_perp_universe_without_domestic_listing() -> None:
    # 현물 행이 하나도 없어도(국내 상장 없음) 두 원천에 있으면 행이 있다
    store = LiveStore()
    perp(store, "binance_perp", "HYPE")
    perp(store, "hyperliquid_perp", "HYPE")
    assert [r["sym"] for r in rows_of(store)] == ["HYPE"]


def test_missing_or_zero_quote_is_fail_with_zero_gaps_and_funding_kept() -> None:
    store = LiveStore()
    perp(store, "binance_perp", "BTC", bid_size=0.0)
    perp(store, "bybit_perp", "BTC")
    perp(store, "bitget_perp", "BTC", ask=0.0)
    rows = {(r["a"], r["b"]): r for r in rows_of(store)}
    assert {k: r["status"] for k, r in rows.items()} == {
        ("binance_perp", "bybit_perp"): "fail",
        ("binance_perp", "bitget_perp"): "fail",
        ("bybit_perp", "bitget_perp"): "fail",
    }
    row = rows[("binance_perp", "bybit_perp")]
    assert (row["longA"], row["longB"]) == (0.0, 0.0)
    assert row["fundA"] == pytest.approx(0.01) and row["intB"] == 8


def test_stale_by_stream_age_and_300_second_row_exception() -> None:
    store = LiveStore()
    perp(store, "binance_perp", "BTC")
    perp(store, "bybit_perp", "BTC", now=T0 - timedelta(seconds=6))
    [row] = rows_of(store)
    assert row["status"] == "stale" and row["age"] == pytest.approx(6.0)

    store = LiveStore()
    perp(store, "binance_perp", "ETH")
    old = perp(store, "bybit_perp", "ETH", now=T0 - timedelta(seconds=400))
    perp(store, "bybit_perp", "XRP")  # 스트림은 살아 있다 — 지금 메시지
    perp(store, "binance_perp", "XRP")
    assert old.updated_at is not None
    rows = {r["sym"]: r for r in rows_of(store)}
    # 행 자체가 300초 넘게 안 바뀌면 그 행의 실제 경과 초
    assert rows["ETH"]["status"] == "stale" and rows["ETH"]["age"] == pytest.approx(
        400.0
    )
    assert rows["XRP"]["status"] == "ok" and rows["XRP"]["age"] == 0.0


def test_table_json_shape_and_nan_is_rejected() -> None:
    store = LiveStore()
    perp(store, "binance_perp", "BTC")
    perp(store, "bybit_perp", "BTC")
    store.mark_received(int(T0.timestamp()))
    table = build_pp_table(store, now=T0)
    assert list(table) == ["rows", "warnings", "dataReceivedAt", "fetchedAt"]
    assert table["warnings"] == [] and table["dataReceivedAt"] == int(T0.timestamp())
    decoded = json.loads(encode_pp_table(table))
    assert all(len(r) == 13 for r in decoded["rows"])
    perp(store, "bitget_perp", "BTC", bid=math.inf)
    with pytest.raises(ValueError):
        encode_pp_table(build_pp_table(store, now=T0))


def test_build_time_for_2700_rows(capsys: pytest.CaptureFixture[str]) -> None:
    """§3.2 규모 — 450코인 × 네 곳 = 2,700행(최악) 표 1장 생성 시간을 남긴다(상한 300ms 의 한참 아래여야 한다)."""
    from app.features.pp.service import build_pp_text

    store = LiveStore()
    for i in range(450):
        for j, src in enumerate(PERP_SOURCES):
            px = 1 + i + j * 0.001
            perp(store, src, f"C{i:03d}", bid=px, ask=px * 1.0002)
    store.mark_received(int(T0.timestamp()))
    assert len(rows_of(store)) == 2700
    took = []
    for _ in range(5):
        started = time.perf_counter()
        build_pp_text(store, now=T0)
        took.append((time.perf_counter() - started) * 1000)
    took.sort()
    with capsys.disabled():
        print(f"\n선선표 2,700행 글 갈래 중앙값 {took[2]:.1f}ms")
    assert took[2] < 300
