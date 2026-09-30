"""거래소가 가격 0·NaN 단계를 보내도 분석 API 가 깨지지 않는다 — 001 §3.5-1 호가 정리를 지난 행만 읽는다 (004 §4)."""

import math

import pytest

from app.core.live_store import LiveStore
from app.core.quotes import QuoteSink
from app.features.analysis.tests.helpers import (
    FIXED_MS,
    make_client,
    seed_levels,
    standard_store,
)


def _store_with_bad_top_levels() -> tuple[LiveStore, float]:
    """표준 시드 위에 수신 경로(QuoteSink)로 최우선 가격 0·NaN 단계가 낀 호가를 한 번 더 받는다."""
    store = standard_store()
    sink = QuoteSink(store)
    sink.set_universe({"BTC", "ETH", "XRP", "SOL"})
    asks, bids = seed_levels(71_000, "USDT")
    sink.orderbook(
        exchange="binance",
        base="BTC",
        quote="USDT",
        native_symbol="BTCUSDT",
        asks=[[0.0, 5.0], [math.nan, 1.0], *asks],
        bids=[[math.nan, 1.0], *bids, [0.0, 9.0]],
        timestamp_ms=FIXED_MS,
        received_at_ms=FIXED_MS,
    )
    dom_asks, dom_bids = seed_levels(100_000_000, "KRW")
    sink.orderbook(
        exchange="upbit",
        base="BTC",
        quote="KRW",
        native_symbol="KRW-BTC",
        asks=[[0.0, 1.0], *dom_asks],
        bids=[[0.0, 1.0], *dom_bids],
        timestamp_ms=FIXED_MS,
        received_at_ms=FIXED_MS,
    )
    return store, asks[0][0]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/premium", {"sym": "BTC"}),
        ("/premium/scan", {"dom": "upbit"}),
        ("/matrix", {"amountKrw": 10_000_000}),
        ("/arbitrage", {"sym": "BTC", "amount": 10_000_000}),
        ("/slippage/binance", {"symbol": "BTC/USDT", "amount": 1_000}),
        ("/orderbook/binance", {"symbol": "BTC/USDT"}),
    ],
)
def test_zero_price_top_level_does_not_break_analysis(
    path: str, params: dict[str, object]
) -> None:
    store, _ = _store_with_bad_top_levels()
    resp = make_client(store).get(path, params=params)
    assert resp.status_code == 200, resp.text


def test_best_ask_is_the_first_valid_level() -> None:
    store, first_valid_ask = _store_with_bad_top_levels()
    body = make_client(store).get("/premium", params={"sym": "BTC"}).json()
    assert body["fwd"]["usd"] == pytest.approx(first_valid_ask)
