"""화면 이동 기록 테스트 공통 — api 역할 앱(lifespan 없음)에 시계를 바꾼 서비스와 fakeredis 버스를 꽂는다 (스펙 073 §4)."""

import json
import zlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.redis_bus import RedisBus
from app.features.replay.service import ReplayService
from app.main import create_app

KST = ZoneInfo("Asia/Seoul")
EFFECTIVE = "2026-10-18"
SID = "0123456789abcdef"
IP = "203.0.113.7"
KEEP_SEC = 14 * 86_400


def kst(*args: int) -> float:
    return datetime(*args, tzinfo=KST).timestamp()


# 시행일 다음 날 낮 — 게이트 뒤
NOON = kst(2026, 10, 19, 12, 0)

GOOD: dict[str, Any] = {
    "v": 1,
    "sid": SID,
    "seq": 0,
    "page": "app-spread",
    "dev": "pc",
    "w": 1280,
    "h": 800,
    "ev": [[0, "m", 10, 20]],
}


def body(**change: object) -> bytes:
    return json.dumps({**GOOD, **change}).encode()


@dataclass
class Clock:
    now: float = NOON
    mono: float = 1000.0

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.mono


@dataclass
class Rig:
    app: FastAPI
    service: ReplayService
    clock: Clock
    bus: RedisBus
    redis: fakeredis.FakeRedis
    client: httpx.AsyncClient

    async def post(
        self, content: bytes, ip: str | None = IP, **headers: str
    ) -> httpx.Response:
        if ip is not None:
            headers["X-Client-IP"] = ip
        return await self.client.post("/replay", content=content, headers=headers)

    async def get(self, path: str, **params: str) -> httpx.Response:
        return await self.client.get(f"/admin/replay/{path}", params=params)

    def meta(self, sid: str = SID) -> dict[str, str]:
        return {
            k.decode(): v.decode() for k, v in self.redis.hgetall(f"rp:m:{sid}").items()
        }

    def batches(self, sid: str = SID) -> list[dict[str, Any]]:
        return [
            json.loads(zlib.decompress(b))
            for b in self.redis.lrange(f"rp:e:{sid}", 0, -1)
        ]

    def keys(self) -> list[str]:
        return sorted(k.decode() for k in self.redis.keys("rp:*"))


@pytest.fixture
def api_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("ACCESS_LOG_DIR", raising=False)
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
async def rig(api_app: FastAPI) -> AsyncIterator[Rig]:
    clock = Clock()
    service = ReplayService(
        clock=clock.time, mono=clock.monotonic, privacy_effective=EFFECTIVE
    )
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    api_app.state.replay = service
    api_app.state.spreads_bus = bus
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Rig(
            app=api_app,
            service=service,
            clock=clock,
            bus=bus,
            redis=fakeredis.FakeRedis(server=server),
            client=client,
        )
