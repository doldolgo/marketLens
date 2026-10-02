"""Clarity 피드 테스트 공용 — 가짜 Data Export(httpx MockTransport)·fakeredis·손으로 미는 시계 (스펙 035 §4·040 §4)."""

import asyncio
import json
from collections.abc import Callable
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

# 실제 응답의 이름 철자(2026-10-02 첫 응답 — 035 §7) — 공백 없는 CamelCase. 위 EXPORT 는 문서 철자다.
# 첫 응답은 세션 0 이라 행 값·차원 지표의 키는 지어낸 것이고, 한 행 지표의 키만 실제와 같다
REAL_EXPORT = [
    {
        "metricName": "Traffic",
        "information": [
            {
                "totalSessionCount": "3",
                "totalBotSessionCount": "0",
                "distantUserCount": "2",
                "PagesPerSessionPercentage": 1.5,
            }
        ],
    },
    {
        "metricName": "DeadClickCount",
        "information": [
            {
                "sessionsCount": "0",
                "sessionsWithMetricPercentage": 0,
                "sessionsWithoutMetricPercentage": 100,
                "pagesViews": "4",
                "subTotal": "0",
            }
        ],
    },
    {
        "metricName": "PopularPages",
        "information": [
            {"url": "https://kimptrack.com/app/?tab=history&gclid=SECRET1#top"},
            {"url": "/privacy?utm_source=SECRET2#consent"},
        ],
    },
    {
        "metricName": "ReferrerUrl",
        "information": [
            {"url": "https://u:SECRET3@www.google.com:443/search?q=SECRET4#x"},
            {"url": "android-app://com.google.android.gm/?x=SECRET5"},
            {"url": "www.google.com/search?q=SECRET6"},
            {"url": "Direct"},
        ],
    },
]
# 같은 지표의 문서 철자(Microsoft Learn) — 실제 이름 → 문서 이름
DOC_NAMES = {
    "DeadClickCount": "Dead Click Count",
    "PopularPages": "Popular Pages",
    "ReferrerUrl": "Referrer URL",
    "ScrollDepth": "Scroll Depth",
    "EngagementTime": "Engagement Time",
    "RageClickCount": "Rage Click Count",
    "ExcessiveScroll": "Excessive Scroll",
    "QuickbackClick": "Quickback Click",
    "ScriptErrorCount": "Script Error Count",
    "ErrorClickCount": "Error Click Count",
    "Country": "Country/Region",
}


def signal(sessions: Any, pct: Any, views: Any, total: Any) -> dict[str, Any]:
    """불만 신호 한 행 — 035 §7 의 실제 행 키 다섯."""
    return {
        "sessionsCount": sessions,
        "sessionsWithMetricPercentage": pct,
        "sessionsWithoutMetricPercentage": 50,
        "pagesViews": views,
        "subTotal": total,
    }


# 세션이 있는 기본 응답(지어낸 값 — 숫자와 숫자 글자를 섞는다). 이름·행 키는 실제 철자(040 §3.1)
SESSIONS_EXPORT = [
    {
        "metricName": "Traffic",
        "information": [
            {
                "totalSessionCount": "42",
                "totalBotSessionCount": "3",
                "distantUserCount": "37",
                "PagesPerSessionPercentage": 1.8571,
            }
        ],
    },
    {"metricName": "ScrollDepth", "information": [{"averageScrollDepth": 57.456}]},
    {
        "metricName": "EngagementTime",
        "information": [{"totalTime": "185", "activeTime": 61.333}],
    },
    {"metricName": "DeadClickCount", "information": [signal("6", 14.2857, "9", "11")]},
    {"metricName": "RageClickCount", "information": [signal(2, "4.76", 2, 3)]},
    {"metricName": "ExcessiveScroll", "information": [signal("0", 0, "0", "0")]},
    {"metricName": "QuickbackClick", "information": [signal("5", 11.904, 7, "5")]},
    {"metricName": "ScriptErrorCount", "information": [signal(1, 2.381, "1", 4)]},
    {"metricName": "ErrorClickCount", "information": [signal("1", "2.38", "1", "1")]},
    {
        "metricName": "Country",
        "information": [
            {"name": "Japan", "sessionsCount": "5"},
            {"name": "South Korea", "sessionsCount": "30"},
            {"name": "Canada", "sessionsCount": 5},
        ],
    },
    {
        "metricName": "PopularPages",
        "information": [{"url": "https://kimptrack.com/app/?tab=gap&sym=ETH"}],
    },
]
SESSIONS_SUMMARY = {
    "scrollDepth": 57.46,
    "totalSec": 185.0,
    "activeSec": 61.33,
    "signals": {
        "deadClick": {"sessions": 6, "sessionPct": 14.29, "pageViews": 9, "count": 11},
        "rageClick": {"sessions": 2, "sessionPct": 4.76, "pageViews": 2, "count": 3},
        "excessiveScroll": {
            "sessions": 0,
            "sessionPct": 0.0,
            "pageViews": 0,
            "count": 0,
        },
        "quickback": {"sessions": 5, "sessionPct": 11.9, "pageViews": 7, "count": 5},
        "scriptError": {"sessions": 1, "sessionPct": 2.38, "pageViews": 1, "count": 4},
        "errorClick": {"sessions": 1, "sessionPct": 2.38, "pageViews": 1, "count": 1},
    },
}


def documented(export: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 응답을 문서 철자 이름으로."""
    return [
        {**m, "metricName": DOC_NAMES.get(m["metricName"], m["metricName"])}
        for m in export
    ]


def dims(url: str, device: str, **values: Any) -> dict[str, Any]:
    """차원 행 — 문서 예처럼 차원 이름(`URL`·`Device`)이 키로 붙는다."""
    return {"URL": url, "Device": device, **values}


# 세션이 있는 묶음 응답(지어낸 값). 랜딩 휴대폰은 두 주소(세션 3·1)가 한 칸으로 묶인다
PAGES_EXPORT = [
    {
        "metricName": "Traffic",
        "information": [
            dims("https://kimptrack.com/", "Mobile", totalSessionCount="3"),
            dims(
                "https://kimptrack.com/?utm_source=x", "Mobile", totalSessionCount="1"
            ),
            dims(
                "https://kimptrack.com/app/?tab=gap&sym=ETH", "PC", totalSessionCount=2
            ),
        ],
    },
    {
        "metricName": "ScrollDepth",
        "information": [
            dims("https://kimptrack.com/", "Mobile", averageScrollDepth=40),
            dims(
                "https://kimptrack.com/?utm_source=x", "Mobile", averageScrollDepth="80"
            ),
            dims(
                "https://kimptrack.com/app/?tab=gap&sym=ETH",
                "PC",
                averageScrollDepth=12.5,
            ),
        ],
    },
    {
        "metricName": "PopularPages",
        "information": [dims("https://kimptrack.com/", "Mobile", visitsCount="4")],
    },
]


class Clarity:
    """가짜 Data Export — 쿼리(`dimension1`)로 기본·묶음을 가르고, 부른 요청·시각·동시 호출 수를 적고,
    종류마다 차례로 응답(상태·본문)을 준다. 본문 `TIMEOUT` 은 시간 초과."""

    TIMEOUT = object()

    def __init__(
        self, *replies: tuple[int, Any], pages: tuple[tuple[int, Any], ...] = ()
    ) -> None:
        self.replies = list(replies) or [(200, EXPORT)]
        self.page_replies = list(pages) or [(200, PAGES_EXPORT)]
        self.requests: list[httpx.Request] = []
        self.calls: list[tuple[float, str]] = []  # (부른 시각 — World 시계 초, 종류)
        self.gate: asyncio.Event | None = None
        self.delay = {"base": 0.0, "pages": 0.0}  # 실제 초 — 느린 호출 흉내
        # 가짜 시계 초 — 호출이 걸리는 동안 World 시계를 민다(보낸 시각은 그 앞에 적는다)
        self.takes = {"base": 0, "pages": 0}
        self.clock: Callable[[], float] = lambda: 0.0
        self.spend: Callable[[int], None] = lambda sec: None
        self.active = 0
        self.max_active = 0

    @staticmethod
    def kind(request: httpx.Request) -> str:
        return "pages" if "dimension1" in request.url.params else "base"

    def count(self, kind: str) -> int:
        return sum(1 for _, k in self.calls if k == kind)

    def of(self, kind: str) -> list[httpx.Request]:
        return [r for r in self.requests if self.kind(r) == kind]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        kind = self.kind(request)
        self.requests.append(request)
        self.calls.append((self.clock(), kind))  # 보낸 시각
        self.spend(self.takes[kind])
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.delay[kind]:
                await asyncio.sleep(self.delay[kind])
            queue = self.replies if kind == "base" else self.page_replies
            status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        finally:
            self.active -= 1
        if body is Clarity.TIMEOUT:
            raise httpx.ReadTimeout("slow", request=request)
        if isinstance(body, bytes):  # JSON 표준 밖 리터럴 등 — 받은 바이트 그대로
            return httpx.Response(
                status, content=body, headers={"content-type": "application/json"}
            )
        return httpx.Response(status, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


class FlakyBus(RedisBus):
    """fakeredis 위의 버스 — Clarity 기록 읽기만·쓰기만 실패하게 바꿀 수 있다."""

    def __init__(self, world: "World") -> None:
        super().__init__(fakeredis.aioredis.FakeRedis(server=world.server))
        self._world = world

    def _check(self, kind: str) -> None:
        self._world.redis_calls.append(kind)
        if kind in self._world.broken:
            raise ConnectionError(f"redis {kind} down")

    async def clarity_load(self) -> str | None:
        self._check("read")
        return await super().clarity_load()

    async def clarity_pages_load(self) -> str | None:
        self._check("read")
        return await super().clarity_pages_load()

    async def clarity_save(self, data: str) -> None:
        self._check("write")
        await super().clarity_save(data)

    async def clarity_pages_save(self, data: str) -> None:
        self._check("write")
        await super().clarity_pages_save(data)


class World:
    def __init__(
        self, clarity: Clarity, *, token: str | None = TOKEN, wait: float = 2.0
    ) -> None:
        self.t = 0.0
        self.clarity = clarity
        self.server = fakeredis.FakeServer()
        self.token = token
        self.wait = wait
        self.broken: set[str] = set()  # "read"·"write" — 그쪽 Redis 명령이 실패한다
        # 피드가 Clarity 기록에 닿은 차례("read"·"write") — 토큰이 없으면 비어 있어야 한다
        self.redis_calls: list[str] = []
        self.feeds = self.new_app()
        clarity.clock = lambda: self.t
        clarity.spend = lambda sec: self.advance(sec=sec)

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
        return FlakyBus(self)

    def stored(self, key: str = "admin:clarity") -> dict[str, Any] | None:
        raw = fakeredis.FakeRedis(server=self.server).get(key)
        return None if raw is None else json.loads(raw)

    def stored_pages(self) -> dict[str, Any] | None:
        return self.stored("admin:clarity:pages")

    def seed(self, key: str, text: str) -> None:
        fakeredis.FakeRedis(server=self.server).set(key, text)

    def forget(self, *keys: str) -> None:
        """런북의 키 지우기·기록 잃음 흉내."""
        fakeredis.FakeRedis(server=self.server).delete(*keys)

    async def get(self, feeds: VisitFeeds | None = None) -> dict[str, Any]:
        return await (feeds or self.feeds).clarity(bus=self.bus)

    def advance(self, hours: int = 0, sec: int = 0) -> None:
        self.t += hours * 3600 + sec
