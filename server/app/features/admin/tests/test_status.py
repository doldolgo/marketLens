"""`GET /admin/status` 계약 — 접속 수·Redis·Influx·버전, 2초 제한, 매달린 Influx ping 을 쌓지 않음 (스펙 029 §3.4·§4).

요청은 테스트 루프 하나에서 ASGI 로 보낸다 — 앞선 ping 태스크가 다음 요청까지 같은 루프에 살아 있어야
"진행 중이면 새로 띄우지 않는다" 를 볼 수 있다. Redis 는 fakeredis, Influx 는 ping 만 흉내 낸다.
"""

import asyncio
import threading
import time
from collections.abc import Iterator

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.core.config import APP_VERSION, get_settings
from app.core.redis_bus import RedisBus
from app.features.admin.service import AdminStatusService
from app.main import create_app


class FakeHub:
    def __init__(self, connections: int) -> None:
        self.connections = connections


class FakeInflux:
    """동기 ping — `gate` 가 닫혀 있으면 스레드에서 열릴 때까지 매달린다(60초 타임아웃의 Influx 처럼)."""

    def __init__(self, result: bool = True, *, blocked: bool = False) -> None:
        self.result = result
        self.calls = 0
        self.gate = threading.Event()
        self.finished = threading.Event()
        if not blocked:
            self.gate.set()

    def ping(self) -> bool:
        self.calls += 1
        self.gate.wait(10)
        self.finished.set()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StuckBus:
    """PING 이 돌아오지 않는 Redis — 같은 data 박스가 멈춘 경우 (§3.6)."""

    async def ping(self) -> None:
        await asyncio.Event().wait()


@pytest.fixture
def api_role(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")  # lifespan 이 돌아도 Influx 에 붙지 않게
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_app(
    *,
    hub: FakeHub | None = None,
    bus: object | None = None,
    influx: object | None = None,
    timeout_sec: float = 2.0,
) -> FastAPI:
    """lifespan 없이 자리만 채운다 — None 이면 그 자리가 없는 것(기동 전·토큰 없음)과 같다.

    허브는 main.py 처럼 세는 함수로만 넘긴다 — admin 은 허브를 모른다."""
    app = create_app()
    for name, value in (("spreads_bus", bus), ("influx", influx)):
        if value is not None:
            setattr(app.state, name, value)
    service = AdminStatusService(timeout_sec=timeout_sec)
    if hub is not None:
        service.ws_connections = lambda: hub.connections
    app.state.admin = service
    return app


def fake_bus(server: fakeredis.FakeServer) -> RedisBus:
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server))


async def get_status(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        return await client.get("/admin/status")


async def test_all_up_answers_four_keys_with_version(api_role: None) -> None:
    app = make_app(
        hub=FakeHub(2), bus=fake_bus(fakeredis.FakeServer()), influx=FakeInflux()
    )
    resp = await get_status(app)
    assert resp.status_code == 200
    assert resp.json() == {
        "wsConnections": 2,
        "redis": "ok",
        "influx": "ok",
        "version": APP_VERSION,
    }


async def test_nothing_wired_before_lifespan_is_zero_and_down(api_role: None) -> None:
    # 허브·버스·Influx 가 아직 없다(기동 전) — 오류가 아니라 0·down 으로 200
    resp = await get_status(make_app())
    assert resp.status_code == 200
    assert resp.json() == {
        "wsConnections": 0,
        "redis": "down",
        "influx": "down",
        "version": APP_VERSION,
    }


async def test_redis_ping_uses_the_bus_and_error_is_down(api_role: None) -> None:
    server = fakeredis.FakeServer()
    bus = fake_bus(server)
    await bus.ping()  # 성공은 None
    server.connected = False
    with pytest.raises(RedisError):
        await bus.ping()  # 실패는 예외 — down 으로 바꾸는 것은 호출자 몫
    body = (await get_status(make_app(bus=bus, influx=FakeInflux()))).json()
    assert body["redis"] == "down" and body["influx"] == "ok"


@pytest.mark.parametrize("result", [False, RuntimeError("boom")])
async def test_influx_ping_false_or_error_is_down(
    api_role: None, result: bool | Exception
) -> None:
    body = (await get_status(make_app(influx=FakeInflux(result)))).json()
    assert body["influx"] == "down"


async def test_slow_influx_is_down_and_not_pinged_again_until_it_returns(
    api_role: None,
) -> None:
    influx = FakeInflux(blocked=True)
    app = make_app(bus=fake_bus(fakeredis.FakeServer()), influx=influx, timeout_sec=0.2)
    first = (await get_status(app)).json()
    assert first["influx"] == "down" and first["redis"] == "ok"
    # 앞선 ping 이 스레드에서 아직 돈다 — 두 번째 요청은 새로 띄우지 않고 곧바로 down
    started = time.monotonic()
    second = (await get_status(app)).json()
    assert second["influx"] == "down"
    assert time.monotonic() - started < 0.2
    assert influx.calls == 1
    influx.gate.set()
    assert await asyncio.to_thread(influx.finished.wait, 2)
    await asyncio.sleep(0.05)  # 스레드 결과가 루프의 태스크에 닿을 틈
    third = (await get_status(app)).json()
    assert third["influx"] == "ok" and influx.calls == 2


async def test_both_stores_stuck_answer_down_within_three_seconds(
    api_role: None,
) -> None:
    # Redis·Influx 가 같은 data 박스에서 함께 멈춤 — 각각 2초 제한이 동시에 돈다 (§3.4·§3.6)
    influx = FakeInflux(blocked=True)
    app = make_app(hub=FakeHub(1), bus=StuckBus(), influx=influx)
    app.state.admin = AdminStatusService()  # 기본 2초 제한 그대로
    started = time.monotonic()
    resp = await get_status(app)
    elapsed = time.monotonic() - started
    influx.gate.set()
    assert resp.status_code == 200
    assert resp.json()["redis"] == "down" and resp.json()["influx"] == "down"
    assert 1.9 <= elapsed < 3.0


def test_lifespan_wires_the_hub_so_two_ws_clients_count_two(
    api_role: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 실제 api lifespan(허브·버스) 위에서 /ws/spreads 둘을 열면 2, Influx 는 토큰이 없어 down
    server = fakeredis.FakeServer()

    def make_bus(url: str) -> RedisBus:
        return fake_bus(server)

    monkeypatch.setattr("app.main.RedisBus.from_url", make_bus)
    with TestClient(create_app()) as client:
        with (
            client.websocket_connect("/ws/spreads") as a,
            client.websocket_connect("/ws/spreads") as b,
        ):
            for ws in (a, b):
                ws.receive_bytes()  # waiting — 허브에 붙었다
            body = client.get("/admin/status").json()
            assert body["wsConnections"] == 2
            assert body["redis"] == "ok" and body["influx"] == "down"
        # 둘 다 닫히면 0 — 허브가 연결을 뗀 뒤
        deadline = time.monotonic() + 2
        while client.get("/admin/status").json()["wsConnections"] and (
            time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert client.get("/admin/status").json()["wsConnections"] == 0


def test_openapi_documents_the_status_shape(api_role: None) -> None:
    schema = create_app().openapi()
    ok = schema["paths"]["/admin/status"]["get"]["responses"]["200"]
    ref = ok["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    model = schema["components"]["schemas"][ref]
    assert set(model["properties"]) == {"wsConnections", "redis", "influx", "version"}
    assert model["properties"]["redis"]["enum"] == ["ok", "down"]
