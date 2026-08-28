"""앱 골격 — /health, 404, 거래소 예외 → 에러 응답 변환 (스펙 001 §3.1, §4)."""

from fastapi.testclient import TestClient

from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.main import create_app

# TestClient 를 컨텍스트 없이 쓰면 lifespan(수집 루프)이 돌지 않는다 — 네트워크 호출 없음


def test_health_ok() -> None:
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.1.0"}


def test_unknown_path_is_404() -> None:
    client = TestClient(create_app())
    resp = client.get("/no-such-path")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"


def test_exchange_timeout_becomes_504() -> None:
    app = create_app()

    @app.get("/_test/timeout")
    async def _raise_timeout() -> None:
        raise ExchangeTimeoutError(
            "upbit", "https://api.upbit.com/v1/ticker", "응답 시간 초과"
        )

    resp = TestClient(app).get("/_test/timeout")
    assert resp.status_code == 504
    body = resp.json()
    assert body["error"]["code"] == "exchange_timeout"
    assert body["error"]["detail"]["exchange"] == "upbit"
    assert body["error"]["detail"]["url"] == "https://api.upbit.com/v1/ticker"
    assert "status_code" not in body["error"]["detail"]


def test_exchange_api_error_becomes_502_with_body_truncated() -> None:
    app = create_app()

    @app.get("/_test/api-error")
    async def _raise_api_error() -> None:
        raise ExchangeApiError(
            "binance",
            "https://api.binance.com/api/v3/ticker/price",
            "비-200 응답: 500",
            status_code=500,
            body="x" * 600,
        )

    resp = TestClient(app).get("/_test/api-error")
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["code"] == "exchange_api_error"
    detail = body["error"]["detail"]
    assert detail["exchange"] == "binance"
    assert detail["status_code"] == 500
    assert len(detail["body"]) == 500  # 본문 앞 500자만
