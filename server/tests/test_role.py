"""프로세스 역할 계약 — ROLE=api 앱은 Influx 조회 경로 + /ws/spreads + GET /spreads(Redis 읽기) 만
서빙하고 백그라운드 태스크는 017 의 구독 태스크 하나다 (스펙 016 §3.1·§4, 017 §4, 018 §3.4·§4).

collector(기본) 의 전체 동작은 기존 테스트가 그대로 지킨다 — 여기서는 라우트 집합만 본다.
"""

import asyncio
import logging
from collections.abc import Callable, Iterator

import fakeredis
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.routing import WebSocketRoute

from app.core.config import get_settings
from app.core.redis_bus import RedisBus
from app.main import create_app

# api 역할이 답하는 여섯 경로 (016 §3.1 + 018 §3.4) — 그 외는 전부 404
API_ROUTES = {
    "/health",
    "/history/premium",
    "/history/streaks",
    "/history/streaks/bulk",
    "/history/candles",
    "/spreads",
}


@pytest.fixture
def set_role(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str | None], None]]:
    """ROLE env 를 바꾸고 설정 캐시를 비운다 — 테스트 뒤에도 비워 다음 테스트가 기본값을 읽게."""

    def _set(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("ROLE", raising=False)
        else:
            monkeypatch.setenv("ROLE", value)
        # 토큰이 있으면 lifespan 이 Influx 에 ping 한다 — 이 테스트는 네트워크 없이 돈다
        monkeypatch.setenv("INFLUX_TOKEN", "")
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _paths(app) -> set[str]:  # noqa: ANN001 — FastAPI 앱
    """등록된 경로 집합 — 공개 OpenAPI 스키마로 본다(include_router 의 내부 구조에 기대지 않게)."""
    return set(app.openapi()["paths"])


def test_api_role_serves_only_influx_routes_and_404s_the_rest(set_role) -> None:  # noqa: ANN001
    set_role("api")
    client = TestClient(create_app())  # lifespan 없음 = Influx 없음 → /history/* 503

    health = client.get("/health")
    # 025 — lifespan 없이는 Redis 자리가 없어 starting·503
    assert health.status_code == 503 and health.json()["status"] == "starting"
    # 응답·에러는 005·014 계약 그대로 — 토큰 없으면 503 storage_unavailable
    for path, params in (
        ("/history/premium", {"base": "BTC", "unit": "week"}),
        ("/history/streaks", {"base": "BTC"}),
        ("/history/streaks/bulk", {}),
        ("/history/candles", {"base": "BTC"}),
    ):
        resp = client.get(path, params=params)
        assert resp.status_code == 503, path
        assert resp.json()["error"]["code"] == "storage_unavailable", path
    # 메모리 저장소를 읽는 경로는 없다 — /history/events 도 진행 중 사건을 메모리에서 읽으므로 404
    # (GET /spreads 는 있지만 Redis 만 읽는다 — 아래 테스트)
    assert client.post("/refresh").status_code == 404
    assert client.get("/health/collect").status_code == 404
    assert client.get("/history/events").status_code == 404
    assert (
        client.get("/orderbook/upbit", params={"symbol": "BTC/KRW"}).status_code == 404
    )
    assert _paths(client.app) == API_ROUTES
    # WebSocket 경로는 OpenAPI 에 안 실린다 — 라우트 표에서 본다 (017 §3.3)
    assert _ws_paths(client.app) == {"/ws/spreads"}


def _ws_paths(app) -> set[str]:  # noqa: ANN001 — FastAPI 앱
    """WebSocket 경로 집합 — include_router 가 라우터를 중첩으로 두므로 재귀로 훑는다."""

    def walk(routes) -> set[str]:  # noqa: ANN001
        found: set[str] = set()
        for r in routes:
            if isinstance(r, WebSocketRoute):
                found.add(r.path)
            # 포함된 라우터는 원본 라우터를 감싼 객체로 남는다 — 그 안을 다시 훑는다
            inner = getattr(r, "original_router", None)
            found |= walk(
                inner.routes if inner is not None else getattr(r, "routes", [])
            )
        return found

    return walk(app.routes)


def test_api_role_serves_spreads_from_redis_key_without_memory(set_role) -> None:  # noqa: ANN001
    # 018 §3.1·§4 — api 역할에 메모리 저장소는 없고, GET /spreads 는 키가 없으면 404·있으면 그 바이트
    set_role("api")
    server = fakeredis.FakeServer()
    app = create_app()  # lifespan 없음 — Redis 자리만 fakeredis 로 채운다
    app.state.spreads_bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    client = TestClient(app)
    assert not hasattr(app.state, "live_store")
    resp = client.get("/spreads")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "market_data_not_found"
    fakeredis.FakeRedis(server=server).set("spreads:latest", '{"rows":[]}', ex=10)
    assert client.get("/spreads").content == b'{"rows":[]}'
    assert fakeredis.FakeRedis(server=server).ttl("spreads:want") == 15


async def test_api_role_starts_with_only_the_spreads_hub_task_and_no_s3_exchange_logs(
    set_role,  # noqa: ANN001
    caplog: pytest.LogCaptureFixture,
) -> None:
    set_role("api")
    caplog.set_level(logging.INFO, logger="marketlens")
    app = create_app()
    # lifespan 을 이 루프에서 직접 돌린다 — 안에서 만든 태스크가 있으면 여기서 보인다
    async with app.router.lifespan_context(app):
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        # 017 — Redis 구독 태스크 하나뿐 (스트림·flusher·쓰기 태스크 없음)
        assert [t.get_name() for t in others] == ["spreads_hub"]
        assert app.state.influx is None  # 토큰 없음
    # S3·거래소 줄이 찍히면 역할 분기가 샌 것이다 (016 §3.5). Redis 줄은 017 구독이 남길 수 있다
    for banned in (
        "S3",
        "거래소",
        "업비트",
        "빗썸",
        "바이낸스",
        "우주",
        "스트림",
    ):
        assert not any(banned in r.getMessage() for r in caplog.records), banned
    assert any("INFLUX_TOKEN" in r.getMessage() for r in caplog.records)


def test_collector_is_default_and_keeps_full_route_set(set_role) -> None:  # noqa: ANN001
    set_role(None)
    app = create_app()
    assert app.state.settings.role == "collector"
    paths = _paths(app)
    assert API_ROUTES <= paths
    assert {"/spreads", "/refresh", "/health/collect", "/history/events"} <= paths
    assert any(p.startswith("/orderbook") for p in paths)
    assert _ws_paths(app) == {"/ws/spreads"}  # 두 역할 모두 (017 §3.2)


def test_unknown_role_fails_when_settings_are_read_before_app_object(set_role) -> None:  # noqa: ANN001
    set_role("foo")
    with pytest.raises(ValidationError) as exc_info:
        create_app()
    # 오류 메시지에 허용값 둘이 적힌다 — 사람이 compose 값을 바로 고칠 수 있게
    message = str(exc_info.value)
    assert "collector" in message and "api" in message
