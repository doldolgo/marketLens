"""GET /arbitrage — 스펙 004 §3.2·§4."""

import pytest

from app.core.live_store import LiveStore
from app.features.analysis.tests.helpers import (
    FIXED_DT,
    FIXED_SEC,
    SEED_RATE,
    make_client,
    make_row,
    standard_store,
)


def test_auto_selects_cheapest_ask_and_highest_bid():
    """표준 시드: binance 매수 → bithumb 매도, premium_capture_percent=100 (§4)."""
    client = make_client(standard_store())
    body = client.get("/arbitrage", params={"sym": "BTC", "amount": 1_000_000}).json()
    assert body["buy"]["exchange"] == "binance"
    assert body["sell"]["exchange"] == "bithumb"
    assert body["candidates"][0]["exchange"] == "binance"  # 싼 순(best_ask)
    assert body["premium_capture_percent"] == pytest.approx(100.0)
    # 표면 김프 = binance 매수→bithumb 매도 +0.603% (§4 표준 시드)
    assert body["premium_percent"] == pytest.approx(0.603, abs=1e-3)
    expected_prem = (
        (100_100_000 * (1 - 0.0005)) / (71_000 * (1 + 0.0005) * SEED_RATE) - 1
    ) * 100
    assert body["premium_percent"] == pytest.approx(expected_prem)
    assert body["profit_krw"] > 0
    assert body["profit_percent"] == pytest.approx(expected_prem)
    assert body["input_amount_krw"] == 1_000_000
    assert body["usd_krw_rate"] == SEED_RATE
    # 1단계 안에서 끝나 슬리피지 0
    assert body["buy"]["slippage_percent"] == 0.0
    assert body["sell"]["slippage_percent"] == 0.0


def test_single_candidate_is_409():
    """후보 1곳(해외만 상장) → 409 (§4). 표준 시드의 SOL 은 binance 에만 있다."""
    client = make_client(standard_store())
    res = client.get("/arbitrage", params={"sym": "SOL", "amount": 1_000_000})
    assert res.status_code == 409
    error = res.json()["error"]
    assert error["code"] == "no_arbitrage_opportunity"
    assert error["detail"]["candidates"] == ["binance"]


def test_blocked_deposit_at_sell_side_warns():
    """입금 false 인 매도처(표준 시드 bithumb) → deposit_available=false + 경고 (§4)."""
    client = make_client(standard_store())
    body = client.get("/arbitrage", params={"sym": "BTC", "amount": 1_000_000}).json()
    assert body["deposit_available"] is False
    assert body["withdrawal_available"] is True  # 매수처 binance 출금
    assert any("입금이 막혀" in w for w in body["warnings"])
    # 항상 마지막은 수수료 미반영 문구 (§3.0)
    assert "미반영 이론값" in body["warnings"][-1]


def test_null_deposit_warns_do_not_assume_open():
    """null 은 '모름' — 숨기지 않고 '확인 못 함' 경고 (§3.2-8·§4)."""
    store = standard_store(bithumb_dep=None, bithumb_wd=None)
    body = (
        make_client(store)
        .get("/arbitrage", params={"sym": "BTC", "amount": 1_000_000})
        .json()
    )
    assert body["deposit_available"] is None
    assert any("확인 못" in w and "가정하지" in w for w in body["warnings"])


def test_per_exchange_rates_and_matching_sides():
    """매수측 환율은 거래소별(국내 자기 환율, 해외 기준 환율), ask≠bid 면 다리별로 맞는 쪽 (§4)."""
    store = LiveStore()
    # bithumb 이 USDT 마켓을 가진 상황 — 자기 환율(1,350/1,340)로 환산해야 한다
    store.replace_exchange(
        "bithumb",
        [
            make_row(
                "bithumb",
                "AB",
                quote="USDT",
                price=100.5,
                asks=[[101.0, 10.0]],
                bids=[[100.4, 10.0]],
            )
        ],
        FIXED_DT,
    )
    store.replace_exchange(
        "binance",
        [
            make_row(
                "binance", "AB", price=100.0, asks=[[100.0, 10.0]], bids=[[99.0, 10.0]]
            )
        ],
        FIXED_DT,
    )
    store.set_rate("upbit", 1_410.0, 1_390.0, FIXED_DT)  # 기준 환율 (해외 환산용)
    store.set_rate("bithumb", 1_350.0, 1_340.0, FIXED_DT)  # bithumb 자기 환율
    store.mark_received(FIXED_SEC)
    body = (
        make_client(store)
        .get("/arbitrage", params={"sym": "AB", "amount": 136_350})
        .json()
    )
    # 매수 = bithumb ask 101×1,350(자기 ask), 매도 = binance bid 99×1,390(기준 bid)
    assert body["buy"]["exchange"] == "bithumb"
    assert body["sell"]["exchange"] == "binance"
    assert body["buy"]["average_price_krw"] == pytest.approx(101.0 * 1_350.0)
    assert body["sell"]["average_price_krw"] == pytest.approx(99.0 * 1_390.0)
    assert body["quantity"] == pytest.approx(1.0)


def test_sell_side_exhaustion_rematches_buy():
    """매도측 소진 시 매수를 되맞춰 실효 수익률이 −50% 대로 떨어지지 않는다 (§4)."""
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit",
                "TT",
                price=141_500,
                asks=[[142_000.0, 5.0]],
                bids=[[141_000.0, 0.5]],  # 매도 가능 수량 0.5 뿐
            )
        ],
        FIXED_DT,
    )
    store.replace_exchange(
        "binance",
        [
            make_row(
                "binance", "TT", price=100.0, asks=[[100.0, 5.0]], bids=[[99.0, 5.0]]
            )
        ],
        FIXED_DT,
    )
    store.set_rate("upbit", SEED_RATE, SEED_RATE, FIXED_DT)
    store.mark_received(FIXED_SEC)
    body = (
        make_client(store)
        .get("/arbitrage", params={"sym": "TT", "amount": 500_000})
        .json()
    )
    assert body["buy"]["exchange"] == "binance"
    assert body["sell"]["exchange"] == "upbit"
    assert body["quantity"] == pytest.approx(0.5)  # 실제 판 수량
    # 되맞춘 매수액 = 0.5 × 140,000 = 70,000, 매도액 = 0.5 × 141,000 = 70,500
    assert body["buy"]["amount_krw"] == pytest.approx(70_000)
    assert body["sell"]["amount_krw"] == pytest.approx(70_500)
    assert body["profit_percent"] == pytest.approx((70_500 / 70_000 - 1) * 100)
    assert body["profit_percent"] > -50  # 쓰레기 값 방지가 목적
    assert body["sell"]["depth_exhausted"] is True
    assert any("되맞췄" in w for w in body["warnings"])


def test_buy_side_exhaustion_warns_partial_fill():
    """매수측 소진 → '투입 금액 중 X원만 체결' 경고 + 실제 체결분 계산 (§3.2-9·§3.3)."""
    client = make_client(standard_store())
    body = client.get("/arbitrage", params={"sym": "BTC", "amount": 100_000_000}).json()
    assert body["buy"]["depth_exhausted"] is True
    assert any("원만 체결" in w for w in body["warnings"])


def test_missing_snapshot_and_missing_base_rate_are_404():
    client = make_client(standard_store())
    res = client.get("/arbitrage", params={"sym": "NOPE", "amount": 1_000_000})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "market_data_not_found"

    # 기준 환율(upbit) 없음 — bithumb 환율만 있어도 404 다
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [make_row("upbit", "BTC", price=100_000_000)],
        FIXED_DT,
    )
    store.replace_exchange(
        "binance",
        [make_row("binance", "BTC", price=71_000)],
        FIXED_DT,
    )
    store.set_rate("bithumb", SEED_RATE, SEED_RATE, FIXED_DT)
    store.mark_received(FIXED_SEC)
    res = make_client(store).get(
        "/arbitrage", params={"sym": "BTC", "amount": 1_000_000}
    )
    assert res.status_code == 404
    assert "환율" in res.json()["error"]["message"]


def test_amount_range_violation_is_422():
    client = make_client(standard_store())
    assert (
        client.get("/arbitrage", params={"sym": "BTC", "amount": -1}).status_code == 422
    )
    assert client.get("/arbitrage", params={"sym": "BTC"}).status_code == 422
