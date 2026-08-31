"""GET /premium/scan — 스펙 004 §3.2·§4."""

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


def test_btc_item_matches_premium_fwd():
    """scan BTC 항목의 수치가 /premium fwd 와 같다 (§4)."""
    client = make_client(standard_store())
    scan = client.get("/premium/scan").json()
    prem = client.get("/premium", params={"sym": "BTC"}).json()
    btc = next(i for i in scan["top_fwd"] if i["sym"] == "BTC")
    assert btc["premium_percent"] == pytest.approx(prem["fwd"]["premium_percent"])
    assert btc["premium_krw"] == pytest.approx(prem["fwd"]["premium_krw"])
    assert btc["usd"] == pytest.approx(prem["fwd"]["usd"])
    assert btc["dom_price"] == prem["dom_price"]
    assert btc["direction"] == "fwd"
    assert btc["fx_name"] == "Binance"


def test_standard_seed_counts_and_ordering():
    client = make_client(standard_store())
    body = client.get("/premium/scan").json()
    assert body["dom"] == "upbit"
    assert body["fx"] == "binance"
    assert body["usd_krw_rate"] == SEED_RATE
    assert body["scanned_coins"] == 3  # BTC·ETH·XRP (upbit KRW)
    assert body["scanned_pairs"] == 3  # SOL 은 국내 미상장이라 짝이 없다
    assert body["excluded_bases"] == []
    # 수익률 내림차순 — 표준 시드 fwd 1위는 XRP
    assert body["top_fwd"][0]["sym"] == "XRP"
    values = [i["premium_percent"] for i in body["top_fwd"]]
    assert values == sorted(values, reverse=True)
    assert body["best_fwd"]["sym"] == "XRP"
    assert body["suspicious_count"] == 0
    # 항상 마지막 두 경고: 1단계 한계 안내 → 수수료 미반영 (§3.0·§3.2-6)
    assert "1단계" in body["warnings"][-2]
    assert "미반영 이론값" in body["warnings"][-1]
    # 유동성 = 양쪽 1단계 체결 가능 금액 중 작은 쪽 — 표준 시드는 단계당 300만원
    assert body["top_fwd"][0]["liquidity_krw"] == pytest.approx(3_000_000, rel=1e-6)


def test_limit_returns_top_n_descending():
    """limit=N 은 수익률 내림차순 상위 N 개 (§4)."""
    client = make_client(standard_store())
    body = client.get("/premium/scan", params={"limit": 1}).json()
    assert len(body["top_fwd"]) == 1
    assert body["top_fwd"][0]["sym"] == "XRP"
    assert len(body["top_rev"]) == 1


def test_suspicious_when_abs_premium_at_least_5_percent():
    """|premium| ≥ 5% 는 suspicious=true + 1위 의심 경고 (§3.2·§4)."""
    store = standard_store(
        extra={
            # upbit 1,500원 vs binance 0.99 USDT → 김프 약 +8%
            "upbit": [seeded_row("upbit", "ZZZ", 1_500, dep=True, wd=True)],
            "binance": [seeded_row("binance", "ZZZ", 0.99, dep=True, wd=True)],
        }
    )
    body = make_client(store).get("/premium/scan").json()
    zzz = next(i for i in body["top_fwd"] if i["sym"] == "ZZZ")
    assert zzz["premium_percent"] >= 5
    assert zzz["suspicious"] is True
    assert zzz["suspicion_reason"] is not None
    assert body["suspicious_count"] >= 1
    assert body["best_fwd"]["sym"] == "ZZZ"
    assert any("의심 항목" in w for w in body["warnings"])


def test_excluded_bases_are_skipped_and_reported():
    """제외 코인(AI·PROS)은 빠지고 excluded_bases 에 보인다 (§3.0·§4)."""
    store = standard_store(
        extra={
            "upbit": [seeded_row("upbit", "AI", 1_000, dep=True, wd=True)],
            "binance": [seeded_row("binance", "AI", 0.7, dep=True, wd=True)],
        }
    )
    body = make_client(store).get("/premium/scan").json()
    assert body["excluded_bases"] == ["AI"]
    assert all(i["sym"] != "AI" for i in body["top_fwd"] + body["top_rev"])
    assert body["scanned_coins"] == 3  # 제외 코인은 검사 수에 안 들어간다


def test_low_liquidity_top_item_warns():
    """1위 유동성 < 100만원이면 '체결 가능 금액이 N원뿐' 경고 (§3.2-6)."""
    store = standard_store(
        extra={
            "upbit": [seeded_row("upbit", "TINY", 1_450, dep=True, wd=True)],
            "binance": [seeded_row("binance", "TINY", 0.99, dep=True, wd=True)],
        }
    )
    # TINY 를 fwd 1위로 만들되 1단계 유동성을 100만원 미만으로 줄인다
    tiny = store.get("upbit", "TINY")
    assert tiny is not None
    tiny.bids = [[1_449.0, 100.0]]  # 1단계 약 14.5만원
    body = make_client(store).get("/premium/scan").json()
    assert body["best_fwd"]["sym"] == "TINY"
    assert any("원뿐" in w for w in body["warnings"])


def test_rate_missing_is_404_before_snapshots():
    """환율 없음 404 는 스냅샷 검사보다 먼저 (§3.2)."""
    store = LiveStore()  # 스냅샷도 환율도 없다
    res = make_client(store).get("/premium/scan")
    assert res.status_code == 404
    assert "환율" in res.json()["error"]["message"]

    # 환율은 있는데 국내 스냅샷이 없으면 스냅샷 404
    store = LiveStore()
    store.set_rate("upbit", SEED_RATE, SEED_RATE, FIXED_DT)
    store.mark_received(FIXED_SEC)
    res = make_client(store).get("/premium/scan")
    assert res.status_code == 404
    assert "KRW 스냅샷" in res.json()["error"]["message"]


def test_limit_out_of_range_is_422():
    client = make_client(standard_store())
    assert client.get("/premium/scan", params={"limit": 0}).status_code == 422
    assert client.get("/premium/scan", params={"limit": 101}).status_code == 422
