"""바이낸스 행의 asks/bids 가 20단계면 걷기·응답이 그 전부를 쓴다 — 스펙 004 §4 "깊이 반영" (001 §3.3 의 행 계약)."""

import pytest

from app.core.live_store import LiveStore
from app.features.analysis.tests.helpers import (
    FIXED_DT,
    FIXED_SEC,
    SEED_RATE,
    make_client,
    make_row,
)

ASKS = [[71_000.0 + i * 100, 0.1] for i in range(20)]
BIDS = [[70_900.0 - i * 100, 0.1] for i in range(20)]
# 국내(upbit) 쪽은 1단계에 10 BTC 를 두어 해외 다리만 여러 단계를 먹게 한다 — 실효 수익률 차이가 전부 바이낸스 깊이에서 나온다
UPBIT_ASKS = [[100_050_000.0, 10.0]]
UPBIT_BIDS = [[99_950_000.0, 10.0]]
# 20단계 시드의 최우선 1단계만 남긴 시드 — 같은 규모에서 바로 소진된다
ONE_ASKS = ASKS[:1]
ONE_BIDS = BIDS[:1]
AMOUNT_KRW = 30_000_000.0  # 바이낸스 1단계(≈994만원)를 넘겨 여러 단계를 먹는 규모


def _store() -> LiveStore:
    store = LiveStore()
    store.put_rows(
        [make_row("binance", "BTC", price=71_000.0, asks=ASKS, bids=BIDS)], FIXED_DT
    )
    store.mark_received(FIXED_SEC)
    return store


def _pair_store(asks: list[list[float]], bids: list[list[float]]) -> LiveStore:
    """upbit(깊은 1단계) + binance(주어진 단계) + upbit 환율 — arbitrage·matrix 공용."""
    store = LiveStore()
    store.put_rows(
        [
            make_row(
                "upbit", "BTC", price=100_000_000.0, asks=UPBIT_ASKS, bids=UPBIT_BIDS
            ),
            make_row("binance", "BTC", price=71_000.0, asks=asks, bids=bids),
        ],
        FIXED_DT,
    )
    store.set_rate("upbit", SEED_RATE, SEED_RATE, FIXED_DT)
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


def test_arbitrage_foreign_leg_walks_twenty_levels() -> None:
    """1단계 시드와 20단계 시드의 실효 수익률이 다르다 — 해외 매수 다리가 바이낸스 깊이를 걷는다 (§4)."""
    params = {"sym": "BTC", "amount": AMOUNT_KRW}
    one = make_client(_pair_store(ONE_ASKS, ONE_BIDS)).get("/arbitrage", params=params)
    twenty = make_client(_pair_store(ASKS, BIDS)).get("/arbitrage", params=params)
    assert one.status_code == 200 and twenty.status_code == 200
    one_body, twenty_body = one.json(), twenty.json()
    for body in (one_body, twenty_body):
        assert body["buy"]["exchange"] == "binance"
        assert body["sell"]["exchange"] == "upbit"
    # 1단계 시드는 0.1 BTC 에서 소진, 20단계 시드는 30,000,000원을 여러 단계로 채운다
    assert one_body["buy"]["depthExhausted"] is True
    assert one_body["buy"]["levelsConsumed"] == 1
    assert twenty_body["buy"]["depthExhausted"] is False
    assert twenty_body["buy"]["levelsConsumed"] > 1
    assert twenty_body["candidates"][0]["depthLevels"] == 20
    # 단계를 올라가며 샀으니 실효 수익률이 1단계 시드보다 낮고, 슬리피지가 양수다
    assert twenty_body["profitPercent"] < one_body["profitPercent"]
    assert one_body["buy"]["slippagePercent"] == 0.0
    assert twenty_body["buy"]["slippagePercent"] > 0


def test_matrix_foreign_legs_walk_twenty_levels() -> None:
    """fwd 는 바이낸스 asks 를, rev 는 바이낸스 bids 를 20단계 걷는다 — 1단계 시드는 소진돼 슬리피지 0 (§4)."""
    params = {"amountKrw": AMOUNT_KRW}
    one = make_client(_pair_store(ONE_ASKS, ONE_BIDS)).get("/matrix", params=params)
    twenty = make_client(_pair_store(ASKS, BIDS)).get("/matrix", params=params)
    assert one.status_code == 200 and twenty.status_code == 200
    one_btc = one.json()["coins"][0]
    twenty_btc = twenty.json()["coins"][0]
    assert one_btc["sym"] == "BTC" and twenty_btc["sym"] == "BTC"
    for direction in ("fwd", "rev"):
        # 표면 김프는 1단계 기준이라 두 시드에서 같다 — 차이는 실효 수익률(걷기)에서만 난다
        assert one_btc[direction]["premiumPercent"] == pytest.approx(
            twenty_btc[direction]["premiumPercent"]
        )
        assert one_btc[direction]["depthExhausted"] is True
        assert one_btc[direction]["totalSlippagePercent"] == pytest.approx(0.0)
        assert twenty_btc[direction]["depthExhausted"] is False
        assert twenty_btc[direction]["totalSlippagePercent"] > 0
