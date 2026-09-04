"""GET /premium — 스펙 004 §3.1·§3.2·§4."""

import pytest

from app.core.live_store import LiveStore
from app.features.analysis.tests.helpers import (
    FIXED_DT,
    FIXED_SEC,
    SEED_RATE,
    make_client,
    seeded_row,
    standard_store,
)


def test_standard_seed_btc_fwd_and_rev():
    """표준 시드 BTC fwd +0.503%, rev −0.699% — 부호 반전이 아니다 (§4)."""
    client = make_client(standard_store())
    body = client.get("/premium", params={"sym": "BTC"}).json()
    dom_bid = 100_000_000 * (1 - 0.0005)
    dom_ask = 100_000_000 * (1 + 0.0005)
    fx_ask_krw = 71_000 * (1 + 0.0005) * SEED_RATE
    fx_bid_krw = 71_000 * (1 - 0.0005) * SEED_RATE
    fwd = body["fwd"]["premiumPercent"]
    rev = body["rev"]["premiumPercent"]
    assert fwd == pytest.approx((dom_bid / fx_ask_krw - 1) * 100)
    assert rev == pytest.approx((fx_bid_krw / dom_ask - 1) * 100)
    assert fwd == pytest.approx(0.503, abs=1e-3)
    assert rev == pytest.approx(-0.699, abs=1e-3)
    assert fwd != pytest.approx(-rev)  # 부호 반전 아님
    assert body["fwd"]["profitable"] is True
    assert body["rev"]["profitable"] is False
    assert body["bestDirection"] == "fwd"
    assert body["bestPremiumPercent"] == pytest.approx(fwd)
    assert body["sym"] == "BTC"
    assert body["dom"] == "upbit"
    assert body["fx"] == "binance"
    assert body["domPrice"] == 100_000_000
    assert body["fwd"]["premiumKrw"] == pytest.approx(dom_bid - fx_ask_krw)


def test_fwd_uses_rate_ask_and_rev_uses_rate_bid():
    """fwd 는 환율 ask, rev 는 환율 bid (§3.1·§4)."""
    store = standard_store()
    store.set_rate("upbit", 1_410.0, 1_390.0, FIXED_DT)
    client = make_client(store)
    body = client.get("/premium", params={"sym": "BTC"}).json()
    dom_bid = 100_000_000 * (1 - 0.0005)
    dom_ask = 100_000_000 * (1 + 0.0005)
    fx_ask = 71_000 * (1 + 0.0005)
    fx_bid = 71_000 * (1 - 0.0005)
    assert body["fwd"]["usdKrwRate"] == 1_410.0
    assert body["rev"]["usdKrwRate"] == 1_390.0
    assert body["fwd"]["premiumPercent"] == pytest.approx(
        (dom_bid / (fx_ask * 1_410.0) - 1) * 100
    )
    assert body["rev"]["premiumPercent"] == pytest.approx(
        (fx_bid * 1_390.0 / dom_ask - 1) * 100
    )
    assert body["fwd"]["usd"] == pytest.approx(fx_ask)
    assert body["rev"]["usd"] == pytest.approx(fx_bid)


def test_foreign_dom_is_400():
    client = make_client(standard_store())
    res = client.get("/premium", params={"sym": "BTC", "dom": "binance"})
    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "invalid_request"
    assert (
        "upbit" in error["message"] and "bithumb" in error["message"]
    )  # 선택 가능 목록


def test_unknown_dom_is_404_unsupported():
    client = make_client(standard_store())
    res = client.get("/premium", params={"sym": "BTC", "dom": "coinbase"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "unsupported_exchange"


def test_missing_pieces_are_404():
    """국내 스냅샷 없음·환율 없음·binance 스냅샷 없음 → 404 (§4)."""
    # 국내 스냅샷 없음
    store = LiveStore()
    store.replace_exchange("binance", [seeded_row("binance", "BTC", 71_000)], FIXED_DT)
    store.set_rate("upbit", SEED_RATE, SEED_RATE, FIXED_DT)
    store.mark_received(FIXED_SEC)
    res = make_client(store).get("/premium", params={"sym": "BTC"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "market_data_not_found"

    # 환율 없음
    store = LiveStore()
    store.replace_exchange("upbit", [seeded_row("upbit", "BTC", 100_000_000)], FIXED_DT)
    store.replace_exchange("binance", [seeded_row("binance", "BTC", 71_000)], FIXED_DT)
    store.mark_received(FIXED_SEC)
    res = make_client(store).get("/premium", params={"sym": "BTC"})
    assert res.status_code == 404
    assert "환율" in res.json()["error"]["message"]

    # binance 스냅샷 없음
    store = LiveStore()
    store.replace_exchange("upbit", [seeded_row("upbit", "BTC", 100_000_000)], FIXED_DT)
    store.set_rate("upbit", SEED_RATE, SEED_RATE, FIXED_DT)
    store.mark_received(FIXED_SEC)
    res = make_client(store).get("/premium", params={"sym": "BTC"})
    assert res.status_code == 404
    assert "binance" in res.json()["error"]["message"]


def test_best_direction_is_less_bad_when_both_lose():
    """둘 다 손해면 덜 나쁜 쪽 (§3.2-3·§4)."""
    store = LiveStore()
    # 국내가 해외×환율보다 아주 살짝만 비싸 양방향 모두 손해가 나는 시드
    store.replace_exchange("upbit", [seeded_row("upbit", "NEG", 1_400_140)], FIXED_DT)
    store.replace_exchange("binance", [seeded_row("binance", "NEG", 1_000)], FIXED_DT)
    store.set_rate("upbit", SEED_RATE, SEED_RATE, FIXED_DT)
    store.mark_received(FIXED_SEC)
    body = make_client(store).get("/premium", params={"sym": "NEG"}).json()
    assert body["fwd"]["premiumPercent"] < 0
    assert body["rev"]["premiumPercent"] < 0
    assert body["fwd"]["premiumPercent"] > body["rev"]["premiumPercent"]
    assert body["bestDirection"] == "fwd"
    assert body["bestPremiumPercent"] == pytest.approx(body["fwd"]["premiumPercent"])
    assert body["fwd"]["profitable"] is False


def test_sym_is_case_insensitive():
    client = make_client(standard_store())
    body = client.get("/premium", params={"sym": "btc"}).json()
    assert body["sym"] == "BTC"
