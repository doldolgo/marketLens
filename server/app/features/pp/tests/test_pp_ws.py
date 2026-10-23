"""049 §4 — `/ws/pp` 계약 (TestClient 의 WebSocket + fakeredis, 허브는 lifespan 안에서 기동)."""

import gzip
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.redis_bus import PP_CHANNEL, RedisBus
from app.features.pp.push import encode_pp_table
from app.features.pp.tests.helpers import ROW_KEYS, pp_row, pp_table
from app.features.pp.ws import ws_router
from app.features.spreads.hub import SpreadsHub


def recv(ws) -> dict:  # noqa: ANN001
    return json.loads(gzip.decompress(ws.receive_bytes()))


def make_ws_app() -> tuple[FastAPI, RedisBus]:
    server = fakeredis.FakeServer()
    publisher_side = RedisBus(fakeredis.aioredis.FakeRedis(server=server))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub = SpreadsHub(
            bus=RedisBus(fakeredis.aioredis.FakeRedis(server=server)), table=PP_CHANNEL
        )
        hub.start()
        app.state.pp_hub = hub
        try:
            yield
        finally:
            await hub.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(ws_router)
    return app, publisher_side


def test_connect_gets_waiting_then_snapshot_then_delta_and_heartbeat() -> None:
    app, bus = make_ws_app()
    with TestClient(app) as client, client.websocket_connect("/ws/pp") as ws:
        assert recv(ws) == {"type": "waiting"}
        client.portal.call(
            bus.publish_table,
            encode_pp_table(pp_table([pp_row("BTC"), pp_row("ETH")])),
            PP_CHANNEL,
        )
        first = recv(ws)
        assert first["type"] == "snapshot" and [r["sym"] for r in first["rows"]] == [
            "BTC",
            "ETH",
        ]
        assert list(first["rows"][0]) == ROW_KEYS
        ws.send_text("ignored")  # 클라이언트 → 서버 메시지는 무시
        client.portal.call(
            bus.publish_table,
            encode_pp_table(pp_table([pp_row("BTC", age=4.0)])),
            PP_CHANNEL,
        )
        second = recv(ws)
        assert second["type"] == "delta" and second["rows"] == []
        assert second["removed"] == ["ETH|binance_perp|bybit_perp"]
        assert recv(ws) == {"type": "heartbeat"}
