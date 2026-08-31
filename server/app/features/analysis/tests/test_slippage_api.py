"""GET /slippage/{exchange} + 호가창 소진(walk) 동작 — 스펙 004 §3.1·§3.2·§4.

walk 은 private 이므로 공개 동작(/slippage 응답)으로 검증한다 (conventions.md).
"""

import pytest

from app.core.live_store import LiveStore
from app.features.analysis.tests.helpers import (
    FIXED_DT,
    FIXED_SEC,
    LEVEL_KRW,
    make_client,
    make_row,
    seed_levels,
    standard_store,
)


def _custom_store(asks: list[list[float]], bids: list[list[float]]) -> LiveStore:
    store = LiveStore()
    store.replace_exchange(
        "upbit", [make_row("upbit", "TT", asks=asks, bids=bids)], FIXED_DT
    )
    store.mark_received(FIXED_SEC)
    return store


def test_amount_walk_partial_fill_mid_level():
    """금액이 단계 중간에서 끝나면 부분 체결·exhausted=false (§4)."""
    client = make_client(standard_store())
    body = client.get(
        "/slippage/upbit", params={"symbol": "BTC/KRW", "amount": 4_500_000}
    ).json()
    asks, _ = seed_levels(100_000_000, "KRW")
    # 1단계 300만원 전량 + 2단계에서 150만원 부분 체결
    expected_qty = asks[0][1] + 1_500_000 / asks[1][0]
    assert body["levelsConsumed"] == 2
    assert body["depthExhausted"] is False
    assert body["amount"] == pytest.approx(4_500_000)
    assert body["quantity"] == pytest.approx(expected_qty)
    assert body["requestedAmount"] == 4_500_000
    assert body["requestedQuantity"] is None
    assert body["depthAvailable"] == 5


def test_amount_walk_exhausted_returns_actual_amount():
    """전 단계를 넘으면 exhausted=true 이고 amount 는 실제 체결액 (§4)."""
    client = make_client(standard_store())
    body = client.get(
        "/slippage/upbit", params={"symbol": "BTC/KRW", "amount": 20_000_000}
    ).json()
    assert body["depthExhausted"] is True
    assert body["levelsConsumed"] == 5
    assert body["amount"] == pytest.approx(5 * LEVEL_KRW)  # 5단계 합 1,500만원


def test_quantity_walk_shortfall_returns_actual_quantity():
    """수량 부족 시 quantity 는 실제 체결량 (§4)."""
    client = make_client(standard_store())
    body = client.get(
        "/slippage/upbit",
        params={"symbol": "BTC/KRW", "side": "sell", "quantity": 1.0},
    ).json()
    _, bids = seed_levels(100_000_000, "KRW")
    capacity = sum(lv[1] for lv in bids)
    assert body["depthExhausted"] is True
    assert body["quantity"] == pytest.approx(capacity)
    assert body["amount"] == pytest.approx(5 * LEVEL_KRW)


def test_spec_example_two_levels():
    """asks [(100,1),(120,10)] 에 amount=220 → 수량 2.0, 평균 110, slippage 10%, 2단계 (§4)."""
    store = _custom_store(asks=[[100.0, 1.0], [120.0, 10.0]], bids=[[90.0, 1.0]])
    client = make_client(store)
    body = client.get(
        "/slippage/upbit", params={"symbol": "TT/KRW", "amount": 220}
    ).json()
    assert body["quantity"] == pytest.approx(2.0)
    assert body["averagePrice"] == pytest.approx(110.0)
    assert body["slippagePercent"] == pytest.approx(10.0)
    assert body["levelsConsumed"] == 2
    assert body["bestPrice"] == 100.0


def test_sell_slippage_positive_when_average_below_best():
    """매도 슬리피지는 평균가가 최우선가보다 낮을 때 양수 (§4)."""
    client = make_client(standard_store())
    _, bids = seed_levels(100_000_000, "KRW")
    quantity = bids[0][1] + bids[1][1] / 2  # 2단계까지 먹어 평균 < 최우선
    body = client.get(
        "/slippage/upbit",
        params={"symbol": "BTC/KRW", "side": "sell", "quantity": quantity},
    ).json()
    assert body["averagePrice"] < body["bestPrice"]
    assert body["slippagePercent"] > 0


def test_sell_slippage_clamped_at_zero():
    """0 미만이면 0 — 평균이 최우선보다 유리해도 음수를 내지 않는다 (§3.1-5)."""
    store = _custom_store(asks=[[200.0, 1.0]], bids=[[99.0, 1.0], [100.0, 10.0]])
    client = make_client(store)
    body = client.get(
        "/slippage/upbit", params={"symbol": "TT/KRW", "side": "sell", "quantity": 6.0}
    ).json()
    assert body["averagePrice"] > body["bestPrice"]
    assert body["slippagePercent"] == 0.0


def test_amount_and_quantity_validation_is_400_before_snapshot():
    """amount·quantity 둘 다/둘 다 없음/≤0 → 400, 빈 메모리여도 400 이 먼저 (§3.2·§4)."""
    empty_client = make_client(LiveStore())
    seeded_client = make_client(standard_store())
    cases = [
        {"symbol": "BTC/KRW"},  # 둘 다 없음
        {"symbol": "BTC/KRW", "amount": 100, "quantity": 1},  # 둘 다
        {"symbol": "BTC/KRW", "amount": -1},
        {"symbol": "BTC/KRW", "quantity": 0},
    ]
    for client in (empty_client, seeded_client):
        for params in cases:
            res = client.get("/slippage/upbit", params=params)
            assert res.status_code == 400, params
            assert res.json()["error"]["code"] == "invalid_request"


def test_single_level_fill_has_zero_slippage_and_warning():
    """1단계 안에서 끝나면 슬리피지 0 + '규모를 키우면' 경고 (§3.2·§4)."""
    client = make_client(standard_store())
    body = client.get(
        "/slippage/upbit", params={"symbol": "BTC/KRW", "amount": 1_000_000}
    ).json()
    assert body["slippagePercent"] == 0.0
    assert body["levelsConsumed"] == 1
    assert any("규모를 키우면" in w for w in body["warnings"])
    # 항상 마지막은 수수료 미반영 문구 (§3.0)
    assert "미반영 이론값" in body["warnings"][-1]
    assert any("타이밍 슬리피지" in w for w in body["warnings"])


def test_depth_param_limits_walked_levels():
    """depth 는 walk 단계 상한 — 넘치면 depth_exhausted (§3.0)."""
    client = make_client(standard_store())
    body = client.get(
        "/slippage/upbit",
        params={"symbol": "BTC/KRW", "amount": 4_500_000, "depth": 1},
    ).json()
    assert body["depthExhausted"] is True
    assert body["levelsConsumed"] == 1
    assert body["amount"] == pytest.approx(LEVEL_KRW)


def test_empty_side_is_404():
    """스냅샷은 있는데 걷는 쪽 호가가 비면 404 (§3.3)."""
    store = LiveStore()
    row = make_row("upbit", "TT", asks=[[100.0, 1.0]], bids=[])
    store.replace_exchange("upbit", [row], FIXED_DT)
    client = make_client(store)
    res = client.get(
        "/slippage/upbit", params={"symbol": "TT/KRW", "side": "sell", "quantity": 1}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "market_data_not_found"


def test_exchange_name_and_symbol_echo():
    client = make_client(standard_store())
    body = client.get(
        "/slippage/upbit", params={"symbol": "btc-krw", "amount": 1_000_000}
    ).json()
    assert body["name"] == "업비트"
    assert body["symbol"] == "BTC/KRW"
    assert body["quoteCurrency"] == "KRW"
    assert body["side"] == "buy"
