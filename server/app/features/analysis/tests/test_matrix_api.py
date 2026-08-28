"""GET /matrix — 스펙 004 §3.2·§4."""

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


def test_standard_seed_grid():
    """행은 BTC·ETH·XRP(SOL 제외), BTC fwd=binance→bithumb, 첫 행 XRP, 조합 5개 (§4)."""
    client = make_client(standard_store())
    body = client.get("/matrix").json()
    syms = [c["sym"] for c in body["coins"]]
    assert syms == ["XRP", "BTC", "ETH"]  # fwd 김프 내림차순
    assert "SOL" not in syms  # 국내 미상장은 격자에서 빠진다
    assert body["scanned_coins"] == 3 == len(body["coins"])
    assert body["scanned_combinations"] == 5  # BTC 2×1 + ETH 1×1 + XRP 2×1
    assert body["dom_list"] == ["bithumb", "upbit"]
    assert body["fx_list"] == ["binance"]
    assert body["amount_krw"] == 10_000_000  # 기본값

    btc = next(c for c in body["coins"] if c["sym"] == "BTC")
    assert btc["fwd"]["buy_exchange"] == "binance"
    assert (
        btc["fwd"]["sell_exchange"] == "bithumb"
    )  # 국내 매도는 비싼 bithumb 쪽이 최대
    assert btc["fwd"]["deposit_available"] is False  # 매도처 bithumb 입금 막힘
    assert btc["fwd"]["withdrawal_available"] is True  # 매수처 binance 출금
    assert btc["fwd"]["premium_percent"] == pytest.approx(0.603, abs=1e-3)
    assert btc["suspicious"] is False

    xrp = body["coins"][0]
    assert xrp["fwd"]["sell_exchange"] == "bithumb"
    assert xrp["fwd"]["premium_percent"] == pytest.approx(1.053, abs=1e-3)

    # rev 최대 조합 — upbit 쪽이 덜 나쁘다 (−0.699% vs bithumb −0.799%)
    assert btc["rev"]["buy_exchange"] == "upbit"
    assert btc["rev"]["sell_exchange"] == "binance"

    # 입출금 막힘 표시 조합이 있어 경고, 수수료 문구는 그 앞 (§3.2-8 순서)
    fee_idx = next(i for i, w in enumerate(body["warnings"]) if "미반영 이론값" in w)
    dw_idx = next(i for i, w in enumerate(body["warnings"]) if "입출금 막힘" in w)
    assert fee_idx < dw_idx


def test_large_amount_exhausts_depth():
    """amount_krw=50,000,000 이면 depth_exhausted=true — 한쪽 5단계 합 1,500만원 (§4)."""
    client = make_client(standard_store())
    body = client.get("/matrix", params={"amount_krw": 50_000_000}).json()
    btc = next(c for c in body["coins"] if c["sym"] == "BTC")
    assert btc["fwd"]["depth_exhausted"] is True
    assert btc["rev"]["depth_exhausted"] is True


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
    body = make_client(store).get("/matrix", params={"amount_krw": 500_000}).json()
    tt = body["coins"][0]
    # 표면 김프 = (141,000/140,000 − 1)×100, 되맞춘 실효 수익률도 같아 총 슬리피지 ≈ 0
    assert tt["fwd"]["premium_percent"] == pytest.approx((141_000 / 140_000 - 1) * 100)
    assert tt["fwd"]["total_slippage_percent"] == pytest.approx(0.0, abs=1e-9)
    assert tt["fwd"]["depth_exhausted"] is True


def test_rateless_domestic_combos_are_skipped():
    """환율 없는 국내 거래소 조합은 빠진다 — 남의 테더 프리미엄을 빌리지 않는다 (§3.2-2·§4)."""
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit",
                "TT",
                price=141_500,
                asks=[[142_000.0, 5.0]],
                bids=[[141_000.0, 5.0]],
            )
        ],
        FIXED_DT,
    )
    # bithumb 이 더 비싸게 사 주지만 환율이 없다 → 조합에서 빠져야 한다
    store.replace_exchange(
        "bithumb",
        [
            make_row(
                "bithumb",
                "TT",
                price=146_000,
                asks=[[146_000.0, 5.0]],
                bids=[[145_000.0, 5.0]],
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
    store.set_rate("upbit", SEED_RATE, SEED_RATE, FIXED_DT)  # bithumb 환율은 없음
    store.mark_received(FIXED_SEC)
    body = make_client(store).get("/matrix", params={"amount_krw": 500_000}).json()
    tt = body["coins"][0]
    assert tt["fwd"]["sell_exchange"] == "upbit"
    assert body["dom_list"] == ["upbit"]
    assert body["scanned_combinations"] == 1


def test_cap_warning_order():
    """한도 10억원 초과 경고가 맨 앞, 그 다음 수수료 문구 (§3.2-8)."""
    client = make_client(standard_store())
    body = client.get("/matrix", params={"amount_krw": 2_000_000_000}).json()
    assert "저장 한도" in body["warnings"][0]
    assert "미반영 이론값" in body["warnings"][1]


def test_empty_store_and_no_rates_are_404():
    res = make_client(LiveStore()).get("/matrix")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "market_data_not_found"

    store = LiveStore()
    store.replace_exchange(
        "upbit", [make_row("upbit", "BTC", price=100_000_000)], FIXED_DT
    )
    store.mark_received(FIXED_SEC)
    res = make_client(store).get("/matrix")
    assert res.status_code == 404
    assert "환율" in res.json()["error"]["message"]


def test_negative_amount_is_422():
    client = make_client(standard_store())
    assert client.get("/matrix", params={"amount_krw": -1}).status_code == 422
