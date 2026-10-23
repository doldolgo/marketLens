"""017 §4 — `/ws/spreads` 계약 (TestClient 의 WebSocket + fakeredis, 허브는 lifespan 안에서 기동).

모든 프레임은 gzip 바이너리 — 브라우저가 DecompressionStream 으로 푸는 것과 같은 방식으로 풀어 본다.
"""

import gzip
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.redis_bus import RedisBus
from app.features.spreads.hub import SpreadsHub
from app.features.spreads.tests.test_push import row, table
from app.features.spreads.ws import ws_router


def recv(ws) -> dict:
    return json.loads(gzip.decompress(ws.receive_bytes()))


def make_ws_app() -> tuple[FastAPI, RedisBus]:
    """라우터 + 허브만 있는 앱 — 허브 태스크는 TestClient 의 루프 안(lifespan)에서 돌아야 한다."""
    server = fakeredis.FakeServer()
    publisher_side = RedisBus(fakeredis.aioredis.FakeRedis(server=server))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub = SpreadsHub(bus=RedisBus(fakeredis.aioredis.FakeRedis(server=server)))
        hub.start()
        app.state.spreads_hub = hub
        try:
            yield
        finally:
            await hub.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(ws_router)
    return app, publisher_side


def test_connect_gets_waiting_then_snapshot_then_delta_and_heartbeat() -> None:
    app, bus = make_ws_app()
    with TestClient(app) as client, client.websocket_connect("/ws/spreads") as ws:
        assert recv(ws) == {"type": "waiting"}
        client.portal.call(bus.publish_table, json.dumps(table([row("BTC")])))
        first = recv(ws)
        assert first["type"] == "snapshot" and first["rows"][0]["sym"] == "BTC"
        ws.send_text("ignored")  # 클라이언트 → 서버 메시지는 무시 (§3.3)
        client.portal.call(bus.publish_table, json.dumps(table([row("BTC", age=5.0)])))
        second = recv(ws)
        assert second["type"] == "delta" and second["rows"] == []
        # 1초 넘게 보낼 게 없으면 heartbeat
        assert recv(ws) == {"type": "heartbeat"}


def test_connect_with_latest_key_gets_snapshot_immediately() -> None:
    app, bus = make_ws_app()
    with TestClient(app) as client:
        client.portal.call(bus.publish_table, json.dumps(table([row("ETH")])))
        with client.websocket_connect("/ws/spreads") as ws:
            snapshot = recv(ws)
            assert snapshot["type"] == "snapshot"
            assert snapshot["rows"][0]["sym"] == "ETH"
            assert client.portal.call(bus._client.ttl, "spreads:want") == 15


def test_three_clients_receive_the_same_frames() -> None:
    app, bus = make_ws_app()
    with TestClient(app) as client:
        sockets = [
            client.websocket_connect("/ws/spreads").__enter__() for _ in range(3)
        ]
        try:
            assert all(recv(ws)["type"] == "waiting" for ws in sockets)
            client.portal.call(bus.publish_table, json.dumps(table([row("BTC")])))
            client.portal.call(
                bus.publish_table, json.dumps(table([row("BTC", fwd=3.0)]))
            )
            frames = [[ws.receive_bytes(), ws.receive_bytes()] for ws in sockets]
            assert frames[0] == frames[1] == frames[2]
            assert json.loads(gzip.decompress(frames[0][1]))["rows"][0]["fwd"] == 3.0
        finally:
            for ws in sockets:
                ws.__exit__(None, None, None)


def test_v2_query_gets_patch_and_plain_path_keeps_delta() -> None:
    """017 §3.3 — `?v=2` 접속자는 바뀐 필드만 싣는 patch, 쿼리 없는 옛 탭은 delta 그대로. snapshot 은 같다."""
    app, bus = make_ws_app()
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/spreads?v=2") as new,
        client.websocket_connect("/ws/spreads") as old,
    ):
        assert recv(new) == recv(old) == {"type": "waiting"}
        client.portal.call(
            bus.publish_table, json.dumps(table([row("BTC"), row("ETH")]))
        )
        assert recv(new) == recv(old)
        client.portal.call(
            bus.publish_table,
            json.dumps(table([row("BTC", fwd=3.0, age=0.7), row("ETH", age=0.7)])),
        )
        patch = recv(new)
        assert patch["type"] == "patch" and patch["rows"] == []
        assert patch["patch"] == [
            {"sym": "BTC", "dom": "upbit", "fx": "binance", "fwd": 3.0, "age": 0.7}
        ]
        delta = recv(old)
        assert delta["type"] == "delta" and [r["sym"] for r in delta["rows"]] == ["BTC"]
        assert len(delta["rows"][0]) == len(row("BTC"))
