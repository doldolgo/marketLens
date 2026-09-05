"""바이낸스 행의 asks/bids 가 20단계면 걷기·응답이 그 전부를 쓴다 — 스펙 004 §4 (001 §3.3 의 행 계약)."""

import pytest

from app.core.live_store import LiveStore
from app.features.analysis.tests.helpers import (
    FIXED_DT,
    FIXED_SEC,
    make_client,
    make_row,
)

ASKS = [[71_000.0 + i * 100, 0.1] for i in range(20)]
BIDS = [[70_900.0 - i * 100, 0.1] for i in range(20)]


def _store() -> LiveStore:
    store = LiveStore()
    store.put_rows(
        [make_row("binance", "BTC", price=71_000.0, asks=ASKS, bids=BIDS)], FIXED_DT
    )
    store.mark_received(FIXED_SEC)
    return store


def test_orderbook_returns_all_twenty_levels_before_depth_trim() -> None:
    body = (
        make_client(_store())
        .get("/orderbook/binance", params={"symbol": "BTC/USDT", "depth": 99})
        .json()
    )
    assert [lv["price"] for lv in body["asks"]] == [lv[0] for lv in ASKS]
    assert len(body["bids"]) == 20


def test_slippage_walks_twenty_levels_and_turns_positive_with_size() -> None:
    client = make_client(_store())
    small = client.get(
        "/slippage/binance", params={"symbol": "BTC/USDT", "amount": 5_000}
    ).json()
    assert small["depthAvailable"] == 20 and small["bestPrice"] == ASKS[0][0]
    assert small["levelsConsumed"] == 1 and small["slippagePercent"] == 0.0
    big = client.get(
        "/slippage/binance", params={"symbol": "BTC/USDT", "amount": 20_000}
    ).json()
    quantity = 0.1 + 0.1 + (20_000 - 7_100 - 7_110) / 71_200
    assert big["levelsConsumed"] == 3
    assert big["quantity"] == pytest.approx(quantity)
    assert big["slippagePercent"] > 0
