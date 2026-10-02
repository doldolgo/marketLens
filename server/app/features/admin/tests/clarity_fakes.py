"""Clarity 피드 테스트 공용 — 가짜 Data Export(httpx MockTransport)·fakeredis·손으로 미는 시계 (스펙 035 §4)."""

import asyncio
import json
from typing import Any

import fakeredis
import httpx

from app.core.redis_bus import RedisBus
from app.features.admin.visits import VisitFeeds

TOKEN = "test-clarity-token-0f9e8d7c"
NOW = 1_790_847_000.0
HOUR_MS = 3_600_000
EXPORT = [
    {
        "metricName": "Traffic",
        "information": [
            {
                "totalSessionCount": "120",
                "totalBotSessionCount": "8",
                "distantUserCount": "95",
                "PagesPerSessionPercentage": 1.75,
            }
        ],
    },
    {
        "metricName": "Popular Pages",
        "information": [
            {
                "url": "https://kimptrack.com/app/?tab=history&gclid=AD1#top",
                "visits": "40",
            },
            {"url": "/privacy?utm_source=x#consent", "visits": "3"},
        ]
        + [{"url": f"https://kimptrack.com/p{i}", "visits": "1"} for i in range(30)],
    },
    {
        "metricName": "Referrer URL",
        "information": [
            {
                "url": "https://user@www.google.com:443/search?q=secret#x",
                "sessions": "7",
            },
            {"url": "Direct", "sessions": "50"},
        ],
    },
    {
        "metricName": "Page Title",
        "information": [{"title": "T" * 300, "nested": ["/a?b=c"]}],
    },
]


class Clarity:
    """가짜 Data Export — 부른 요청을 모으고, 차례로 응답(상태·본문)을 준다."""

    def __init__(self, *replies: tuple[int, Any]) -> None:
        self.replies = list(replies) or [(200, EXPORT)]
        self.requests: list[httpx.Request] = []
        self.gate: asyncio.Event | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        status, body = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(body, bytes):  # JSON 표준 밖 리터럴 등 — 받은 바이트 그대로
            return httpx.Response(
                status, content=body, headers={"content-type": "application/json"}
            )
        return httpx.Response(status, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


class World:
    def __init__(
        self, clarity: Clarity, *, token: str | None = TOKEN, wait: float = 2.0
    ) -> None:
        self.t = 0.0
        self.clarity = clarity
        self.server = fakeredis.FakeServer()
        self.token = token
        self.wait = wait
        self.feeds = self.new_app()

    def new_app(self) -> VisitFeeds:
        """같은 Redis 에 새로 뜬 api — 재시작(배포) 흉내."""
        return VisitFeeds(
            access_dir=None,
            clarity_token=self.token,
            transport=self.clarity.transport,
            clock=lambda: NOW + self.t,
            mono=lambda: self.t,
            wait_sec=self.wait,
        )

    @property
    def bus(self) -> RedisBus:
        return RedisBus(fakeredis.aioredis.FakeRedis(server=self.server))

    def stored(self) -> dict[str, Any] | None:
        raw = fakeredis.FakeRedis(server=self.server).get("admin:clarity")
        return None if raw is None else json.loads(raw)

    async def get(self, feeds: VisitFeeds | None = None) -> dict[str, Any]:
        return await (feeds or self.feeds).clarity(bus=self.bus)

    def advance(self, hours: int = 0, sec: int = 0) -> None:
        self.t += hours * 3600 + sec
