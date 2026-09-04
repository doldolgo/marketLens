"""GET /orderbook/{exchange} — 스펙 004 §3.2·§4."""

from app.features.analysis.tests.helpers import (
    FIXED_MS,
    make_client,
    seed_levels,
    standard_store,
)


def test_depth_trims_and_keeps_stored_order():
    """depth 만큼만 자르고 저장 순서를 유지한다 (§4)."""
    client = make_client(standard_store())
    body = client.get(
        "/orderbook/upbit", params={"symbol": "BTC/KRW", "depth": 3}
    ).json()
    asks, bids = seed_levels(100_000_000, "KRW")
    assert [lv["price"] for lv in body["asks"]] == [lv[0] for lv in asks[:3]]
    assert [lv["size"] for lv in body["asks"]] == [lv[1] for lv in asks[:3]]
    assert [lv["price"] for lv in body["bids"]] == [lv[0] for lv in bids[:3]]
    assert body["exchange"] == "upbit"
    assert body["symbol"] == "BTC/KRW"
    assert body["base"] == "BTC"
    assert body["quote"] == "KRW"
    assert body["timestamp"] == FIXED_MS
    assert body["dataUpdatedAt"] == FIXED_MS
    assert body["dataReceivedAt"] == FIXED_MS


def test_depth_beyond_stored_returns_all():
    """저장 단계 수를 넘는 depth 는 저장분 전부 (§3.0)."""
    client = make_client(standard_store())
    body = client.get(
        "/orderbook/upbit", params={"symbol": "BTC/KRW", "depth": 99}
    ).json()
    assert len(body["asks"]) == 5
    assert len(body["bids"]) == 5


def test_quote_mismatch_is_404_with_stored_quote_hint():
    """요청 quote 가 저장 quote 와 다르면 404 + 저장 quote 안내 (§3.0·§4)."""
    client = make_client(standard_store())
    res = client.get("/orderbook/upbit", params={"symbol": "BTC/USDT"})
    assert res.status_code == 404
    error = res.json()["error"]
    assert error["code"] == "market_data_not_found"
    assert "BTC/KRW" in error["message"]


def test_unknown_exchange_is_404_unsupported():
    client = make_client(standard_store())
    res = client.get("/orderbook/coinbase", params={"symbol": "BTC/KRW"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "unsupported_exchange"


def test_bad_symbol_format_is_400_invalid_symbol():
    client = make_client(standard_store())
    for symbol in ["BTCKRW", "BTC/KRW/EXTRA", "BTC/", "/KRW"]:
        res = client.get("/orderbook/upbit", params={"symbol": symbol})
        assert res.status_code == 400, symbol
        assert res.json()["error"]["code"] == "invalid_symbol"


def test_symbol_separators_and_case_are_normalized():
    """`-`·`_` 구분자와 소문자를 허용하고 대문자 BASE/QUOTE 로 정규화한다 (§3.0)."""
    client = make_client(standard_store())
    for symbol in ["BTC-KRW", "btc_krw", "btc/krw"]:
        body = client.get("/orderbook/upbit", params={"symbol": symbol}).json()
        assert body["symbol"] == "BTC/KRW", symbol


def test_missing_snapshot_is_404():
    client = make_client(standard_store())
    res = client.get("/orderbook/upbit", params={"symbol": "SOL/KRW"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "market_data_not_found"
    assert "수집 루프가 한 사이클" in res.json()["error"]["message"]


def test_depth_below_one_is_422():
    """쿼리 범위 위반은 FastAPI 기본 422 (§3.0)."""
    client = make_client(standard_store())
    res = client.get("/orderbook/upbit", params={"symbol": "BTC/KRW", "depth": 0})
    assert res.status_code == 422
