"""048 §4 — 표 계산: entry·exit 식, 조합, 정렬, status·age, 표 JSON 모양, 4,800행 생성 시간 (네트워크 없음)."""

import json
import math
import time
from datetime import timedelta

import pytest

from app.core.config import PERP_SOURCES
from app.core.live_store import LiveStore
from app.core.premium import premium_percent
from app.features.gap.push import encode_gap_table
from app.features.gap.service import SPOT_EXCHANGES, build_gap_table
from app.features.gap.tests.helpers import T0, perp, spot

ROW_KEYS = [
    "sym",
    "spot",
    "perp",
    "spotPrice",
    "entry",
    "exit",
    "funding",
    "intervalH",
    "nextFundingTs",
    "status",
    "age",
]


def rows_of(store: LiveStore) -> list[dict]:
    return build_gap_table(store, now=T0)["rows"]  # type: ignore[return-value]


def test_entry_and_exit_use_best_quotes_and_funding_three_values() -> None:
    store = LiveStore()
    spot(store, "binance", "BTC", ask=[100.0, 1.0], bid=[99.0, 1.0], price=99.5)
    perp(store, "bybit_perp", "BTC", bid=100.5, ask=100.6)
    [row] = rows_of(store)
    assert list(row) == ROW_KEYS
    assert row["entry"] == pytest.approx(
        0.5
    )  # 현물 ask 100 에 사고 perp bid 100.5 에 숏
    assert row["exit"] == pytest.approx(
        (100.6 / 99 - 1) * 100
    )  # 현물 bid 99 에 팔고 perp ask 100.6 에 청산
    assert row["spotPrice"] == 99.5
    assert row["funding"] == pytest.approx(0.01)  # 0.0001 × 100 — 한 주기의 퍼센트
    assert row["intervalH"] == 8
    assert row["nextFundingTs"] == 1_791_475_200  # ms → 초
    assert row["status"] == "ok" and row["age"] == 0.0


def test_multiplier_symbols_compare_one_coin_values() -> None:
    # 046 이 이미 1코인 단위로 나눠 둔 값끼리 — 현물 PEPE 0.00001 vs perp 0.0000101 → 1%
    store = LiveStore()
    spot(store, "binance", "PEPE", ask=[0.00001, 1e9], bid=[0.0000099, 1e9])
    perp(
        store,
        "binance_perp",
        "PEPE",
        bid=0.0000101,
        ask=0.0000102,
        bid_size=1e9,
        ask_size=1e9,
    )
    [row] = rows_of(store)
    assert row["entry"] == pytest.approx(1.0)
    assert row["entry"] == premium_percent(buy_krw=0.00001, sell_krw=0.0000101)


def test_combos_need_both_sides_include_same_company_and_exclude_domestic() -> None:
    store = LiveStore()
    spot(store, "upbit", "BTC", quote="KRW")  # 국내 — 끼지 않는다
    spot(store, "binance", "BTC")
    spot(store, "bybit", "BTC")
    spot(store, "binance", "ONLYSPOT")
    perp(store, "binance_perp", "BTC")
    perp(store, "bybit_perp", "BTC")
    perp(store, "bitget_perp", "ONLYPERP")
    combos = [(r["sym"], r["spot"], r["perp"]) for r in rows_of(store)]
    assert combos == [
        ("BTC", "binance", "binance_perp"),  # 같은 회사의 현물·perp 도 한 행
        ("BTC", "binance", "bybit_perp"),
        ("BTC", "bybit", "binance_perp"),
        ("BTC", "bybit", "bybit_perp"),
    ]


def test_rows_sorted_by_sym_spot_order_and_perp_source_order() -> None:
    store = LiveStore()
    for sym in ("ETH", "BTC"):
        for ex in reversed(SPOT_EXCHANGES):
            spot(store, ex, sym)
        for src in reversed(PERP_SOURCES):
            perp(store, src, sym)
    rows = rows_of(store)
    expected = [
        (sym, ex, src)
        for sym in ("BTC", "ETH")
        for ex in SPOT_EXCHANGES
        for src in PERP_SOURCES
    ]
    assert [(r["sym"], r["spot"], r["perp"]) for r in rows] == expected
    # perp 는 원천 순서다 — 사전순(binance·bitget·bybit)이 아니다
    assert PERP_SOURCES[:3] == ("binance_perp", "bybit_perp", "bitget_perp")


def test_fail_when_any_best_quote_missing_or_zero_but_funding_kept() -> None:
    store = LiveStore()
    spot(store, "binance", "A", ask=[100.0, 0.0])  # 현물 ask 잔량 0
    perp(store, "bybit_perp", "A")
    spot(store, "binance", "B")
    perp(store, "bybit_perp", "B", bid=0.0)  # perp bid 가격 0
    spot(store, "binance", "C", ask=[100.0, 1.0])
    perp(store, "bybit_perp", "C", ask_size=0.0)  # perp ask 잔량 0
    spot(store, "binance", "D")
    store.get("binance", "D").bids = []  # 현물 bid 없음
    perp(store, "bybit_perp", "D")
    rows = rows_of(store)
    assert [r["status"] for r in rows] == ["fail"] * 4
    for r in rows:
        assert (r["entry"], r["exit"], r["spotPrice"]) == (0, 0, 0)
        assert (r["funding"], r["intervalH"], r["nextFundingTs"]) == (
            pytest.approx(0.01),
            8,
            1_791_475_200,
        )


def test_age_is_older_stream_and_stale_at_five_seconds() -> None:
    store = LiveStore()
    spot(store, "binance", "BTC", now=T0 - timedelta(seconds=2))
    perp(store, "bybit_perp", "BTC", now=T0 - timedelta(seconds=4.9))
    [row] = rows_of(store)
    assert row["age"] == pytest.approx(4.9) and row["status"] == "ok"
    store.stream("bybit_perp").last_message_at = int(
        (T0 - timedelta(seconds=5)).timestamp() * 1000
    )
    [row] = rows_of(store)
    assert row["age"] == pytest.approx(5.0) and row["status"] == "stale"


def test_row_updated_300s_ago_is_stale_even_if_stream_is_live() -> None:
    # 003 §3.2-4 와 같은 예외 — perp 행만 300초 안 바뀌어도 그 행의 실제 경과 초
    store = LiveStore()
    spot(store, "binance", "BTC")
    perp(store, "bybit_perp", "BTC", now=T0 - timedelta(seconds=300))
    store.stream("bybit_perp").last_message_at = int(T0.timestamp() * 1000)
    [row] = rows_of(store)
    assert row["age"] == 300.0 and row["status"] == "stale"


def test_funding_null_row_can_be_ok() -> None:
    store = LiveStore()
    spot(store, "binance", "BTC")
    perp(
        store,
        "bybit_perp",
        "BTC",
        funding_rate=None,
        next_funding_ms=None,
        funding_interval_h=None,
    )
    [row] = rows_of(store)
    assert row["status"] == "ok"
    assert (row["funding"], row["intervalH"], row["nextFundingTs"]) == (
        None,
        None,
        None,
    )


def test_table_shape_and_encoding_rejects_nan() -> None:
    store = LiveStore()
    spot(store, "binance", "BTC")
    perp(store, "bybit_perp", "BTC")
    store.mark_received(1_791_000_000)
    table = build_gap_table(store, now=T0)
    assert list(table) == ["rows", "warnings", "dataReceivedAt", "fetchedAt"]
    assert table["warnings"] == [] and table["dataReceivedAt"] == 1_791_000_000
    parsed = json.loads(encode_gap_table(table))
    assert list(parsed["rows"][0]) == ROW_KEYS
    # 빈 표도 모양은 같다 — 접속자는 행 0 개를 받는다(§3.6 빈 상태)
    assert build_gap_table(LiveStore(), now=T0)["rows"] == []

    store.get("binance", "BTC").asks = [[math.nan, 1.0]]
    with pytest.raises(ValueError):
        encode_gap_table(build_gap_table(store, now=T0))


def test_4800_row_table_builds_under_the_warn_threshold() -> None:
    """§4 — 300코인 × 현물 4 × perp 4 = 4,800행이 300ms 경고 문턱 안에 생성·인코딩된다(실측값은 §5)."""
    store = LiveStore()
    sources = list(PERP_SOURCES)
    while len(sources) < 4:
        sources.append(f"extra{len(sources)}_perp")
    for i in range(300):
        sym = f"C{i:03d}"
        for ex in SPOT_EXCHANGES:
            spot(store, ex, sym)
        for src in sources:
            perp(store, src, sym)
    from app.features.gap import service

    original = service.PERP_SOURCES
    service.PERP_SOURCES = tuple(sources)
    try:
        started = time.perf_counter()
        text = encode_gap_table(build_gap_table(store))
        elapsed = time.perf_counter() - started
    finally:
        service.PERP_SOURCES = original
    assert len(json.loads(text)["rows"]) == 300 * len(SPOT_EXCHANGES) * 4
    assert elapsed < 0.3, f"4,800행 표 {elapsed * 1000:.0f}ms"
