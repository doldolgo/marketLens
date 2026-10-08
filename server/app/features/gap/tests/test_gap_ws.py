"""048 §4 — `/ws/gap` 계약 (TestClient 의 WebSocket + fakeredis, 허브는 lifespan 안에서 기동)."""

import gzip
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.redis_bus import GAP_CHANNEL, RedisBus
from app.features.gap.tests.helpers import gap_row, gap_table
from app.features.gap.ws import ws_router
from app.features.spreads.hub import SpreadsHub


def recv(ws) -> dict:  # noqa: ANN001
    return json.loads(gzip.decompress(ws.receive_bytes()))


def make_ws_app() -> tuple[FastAPI, RedisBus]:
    server = fakeredis.FakeServer()
    publisher_side = RedisBus(fakeredis.aioredis.FakeRedis(server=server))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub = SpreadsHub(
            bus=RedisBus(fakeredis.aioredis.FakeRedis(server=server)), table=GAP_CHANNEL
        )
        hub.start()
        app.state.gap_hub = hub
        try:
            yield
        finally:
            await hub.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(ws_router)
    return app, publisher_side


def test_connect_gets_waiting_then_snapshot_then_delta_and_heartbeat() -> None:
    app, bus = make_ws_app()
    with TestClient(app) as client, client.websocket_connect("/ws/gap") as ws:
        assert recv(ws) == {"type": "waiting"}
        client.portal.call(
            bus.publish_table, json.dumps(gap_table([gap_row("BTC")])), GAP_CHANNEL
        )
        first = recv(ws)
        assert first["type"] == "snapshot" and first["rows"][0]["perp"] == "bybit_perp"
        ws.send_text("ignored")  # 클라이언트 → 서버 메시지는 무시
        client.portal.call(
            bus.publish_table,
            json.dumps(gap_table([gap_row("BTC", age=4.0)])),
            GAP_CHANNEL,
        )
        second = recv(ws)
        assert (
            second["type"] == "delta"
            and second["rows"] == []
            and second["removed"] == []
        )
        assert recv(ws) == {"type": "heartbeat"}


def test_connect_with_gap_latest_key_gets_snapshot_immediately() -> None:
    app, bus = make_ws_app()
    with TestClient(app) as client:
        client.portal.call(
            bus.publish_table, json.dumps(gap_table([gap_row("ETH")])), GAP_CHANNEL
        )
        with client.websocket_connect("/ws/gap") as ws:
            snapshot = recv(ws)
            assert (
                snapshot["type"] == "snapshot" and snapshot["rows"][0]["sym"] == "ETH"
            )
            assert set(snapshot["rows"][0]) == {
                "sym",
                "spot",
                "perp",
                "spotPrice",
                "entry",
                "exit",
                "funding",
                "intervalH",
                "nextFundingTs",
                "status",
                "age",
            }
