"""앱 골격 — /health, 404, 거래소 예외 → 에러 응답 변환 (스펙 001 §3.1, §4)."""

import logging

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.redis_bus import RedisBus
from app.main import create_app

# TestClient 를 컨텍스트 없이 쓰면 lifespan(수집 루프)이 돌지 않는다 — 네트워크 호출 없음


T0 = 1_700_000_000  # epoch 초


def test_health_is_starting_before_first_tick() -> None:
    # 025 §3.5 — lifespan 없이는 틱이 없다 → starting·503
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "starting", "version": "0.1.0", "lastTickAt": None}


def test_health_collector_ok_then_stale_by_last_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app()
    store = LiveStore()
    app.state.live_store = store
    client = TestClient(app)
    store.mark_received(T0)
    monkeypatch.setattr("app.main.time.time", lambda: T0 + 29.0)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.1.0", "lastTickAt": T0 * 1000}
    monkeypatch.setattr("app.main.time.time", lambda: T0 + 31.0)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "stale"
    assert resp.json()["lastTickAt"] == T0 * 1000


def test_health_api_reads_heartbeat_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # api 역할 — Redis `collect:heartbeat` 만 본다: 없음 stale / 최신 ok / 오래됨 stale
    monkeypatch.setenv("ROLE", "api")
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()
    server = fakeredis.FakeServer()
    app.state.spreads_bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    client = TestClient(app)
    monkeypatch.setattr("app.main.time.time", lambda: T0 + 10.0)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "stale", "version": "0.1.0", "lastTickAt": None}
    fakeredis.FakeRedis(server=server).set("collect:heartbeat", str(T0 * 1000), ex=30)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok" and resp.json()["lastTickAt"] == T0 * 1000
    monkeypatch.setattr("app.main.time.time", lambda: T0 + 31.0)
    assert client.get("/health").json()["status"] == "stale"


def test_health_api_redis_failure_is_redis_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROLE", "api")
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()

    class Broken:
        async def heartbeat(self) -> int | None:
            raise RuntimeError("connection refused")

    app.state.spreads_bus = Broken()
    resp = TestClient(app).get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "redis_down"


def test_unhandled_exception_is_500_in_error_shape_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 025 §3.3 — 처리 안 된 예외는 앱 에러 형식 500 + marketlens.main ERROR 1건(내용은 응답에 없다)
    app = create_app()

    @app.get("/_test/boom")
    async def _boom() -> None:
        raise RuntimeError("secret detail")

    caplog.set_level(logging.ERROR, logger="marketlens.main")
    resp = TestClient(app, raise_server_exceptions=False).get("/_test/boom")
    assert resp.status_code == 500
    assert resp.json() == {
        "error": {"code": "internal_error", "message": "internal error", "detail": None}
    }
    assert "secret" not in resp.text
    errors = [r for r in caplog.records if r.name == "marketlens.main"]
    assert len(errors) == 1 and errors[0].exc_info is not None
    assert "GET /_test/boom" in errors[0].getMessage()


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
    assert "statusCode" not in body["error"]["detail"]


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
    assert detail["statusCode"] == 500
    assert len(detail["body"]) == 500  # 본문 앞 500자만
