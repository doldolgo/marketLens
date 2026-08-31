"""분석 응답 계약 — 모든 키 snake_case, 에러 본문 고정 형식 (스펙 004 §3.0·§4)."""

from app.features.analysis.tests.helpers import make_client, standard_store


def _assert_snake_keys(obj: object, path: str = "$") -> None:
    """camelCase 키가 하나도 없어야 한다 — 004 는 casing 예외로 전부 snake_case (§3.0)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key == key.lower(), f"{path}.{key} 가 snake_case 가 아니다"
            _assert_snake_keys(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _assert_snake_keys(value, f"{path}[{i}]")


def test_all_analysis_responses_are_snake_case():
    client = make_client(standard_store())
    calls = [
        ("/orderbook/upbit", {"symbol": "BTC/KRW"}),
        ("/slippage/upbit", {"symbol": "BTC/KRW", "amount": 1_000_000}),
        ("/arbitrage", {"sym": "BTC", "amount": 1_000_000}),
        ("/premium", {"sym": "BTC"}),
        ("/premium/scan", {}),
        ("/matrix", {}),
    ]
    for path, params in calls:
        res = client.get(path, params=params)
        assert res.status_code == 200, (path, res.text)
        body = res.json()
        _assert_snake_keys(body)
        # 공통 꼬리 필드 (§3.0)
        assert "data_received_at" in body
        assert "fetched_at" in body


def test_error_body_shape_is_fixed():
    client = make_client(standard_store())
    res = client.get("/orderbook/coinbase", params={"symbol": "BTC/KRW"})
    assert res.status_code == 404
    body = res.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message", "detail"}
