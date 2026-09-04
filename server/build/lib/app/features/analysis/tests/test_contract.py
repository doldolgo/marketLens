"""분석 응답 계약 — 모든 키 camelCase, 에러 본문 고정 형식 (스펙 004 §3.0·§4)."""

from app.features.analysis.tests.helpers import make_client, standard_store


def _assert_camel_keys(obj: object, path: str = "$") -> None:
    """HTTP JSON 키에는 snake_case 가 없어야 한다."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert "_" not in key, f"{path}.{key} 가 camelCase 가 아니다"
            _assert_camel_keys(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _assert_camel_keys(value, f"{path}[{i}]")


def test_all_analysis_responses_are_camel_case():
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
        _assert_camel_keys(body)
        # 공통 꼬리 필드 (§3.0)
        assert "dataReceivedAt" in body
        assert "fetchedAt" in body


def test_error_body_shape_is_fixed():
    client = make_client(standard_store())
    res = client.get("/orderbook/coinbase", params={"symbol": "BTC/KRW"})
    assert res.status_code == 404
    body = res.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message", "detail"}
