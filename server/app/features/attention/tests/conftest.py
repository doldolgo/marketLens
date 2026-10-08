"""화면 영역 통계 테스트 공통 — api 역할 앱(lifespan 없음)에 시계를 바꾼 서비스와 fakeredis 버스를 꽂는다 (스펙 052 §4)."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.redis_bus import RedisBus
from app.features.attention.service import AttentionService
from app.main import create_app

KST = ZoneInfo("Asia/Seoul")
EFFECTIVE = "2026-10-18"
# 시행일 다음 날 낮 — 게이트 뒤
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=KST).timestamp()


def kst(*args: int) -> float:
    return datetime(*args, tzinfo=KST).timestamp()


def beacon(
    page: str = "landing",
    device: str = "pc",
    pv: int = 1,
    a: dict[str, list[int]] | None = None,
) -> bytes:
    return json.dumps(
        {"v": 1, "page": page, "device": device, "pv": pv, "a": a or {}}
    ).encode()


@dataclass
class Clock:
    now: float = NOON
    mono: float = 1000.0

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.mono


@dataclass
class Ticks:
    """주기 작업의 잠 — `release()` 한 번에 한 회차만 돈다."""

    gates: list[asyncio.Event] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)

    async def sleep(self, sec: float) -> None:
        self.seconds.append(sec)
        gate = asyncio.Event()
        self.gates.append(gate)
        await gate.wait()

    async def release(self) -> None:
        # 지금 잠든 회차를 깨우고 그 회차(보내기)가 끝나 다음 잠에 들 때까지 기다린다
        waiting = len(self.gates)
        self.gates[-1].set()
        for _ in range(100):
            await asyncio.sleep(0)
            if len(self.gates) > waiting:
                return
        raise AssertionError("주기 작업이 다음 잠에 들지 않았다")


@dataclass
class Rig:
    app: FastAPI
    service: AttentionService
    clock: Clock
    ticks: Ticks
    bus: RedisBus
    redis: fakeredis.FakeRedis
    client: httpx.AsyncClient

    async def post(self, body: bytes, **headers: str) -> httpx.Response:
        return await self.client.post("/attention", content=body, headers=headers)

    def day(self, key: str) -> dict[str, int]:
        return {
            k.decode(): int(v) for k, v in self.redis.hgetall(f"attn:d:{key}").items()
        }


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
    ticks = Ticks()
    service = AttentionService(
        clock=clock.time,
        mono=clock.monotonic,
        sleep=ticks.sleep,
        privacy_effective=EFFECTIVE,
    )
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    api_app.state.attention = service
    api_app.state.spreads_bus = bus
    service.start(bus)
    await asyncio.sleep(0)  # 첫 잠에 들게
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Rig(
            app=api_app,
            service=service,
            clock=clock,
            ticks=ticks,
            bus=bus,
            redis=fakeredis.FakeRedis(server=server),
            client=client,
        )
    await service.aclose()
