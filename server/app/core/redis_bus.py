"""Redis pub/sub·키 클라이언트 — 스프레드 표 게시·구독 (스펙 017 §3.1·§3.2) + `GET /spreads` 읽기 (018 §3.1).

redis 라이브러리를 import 하는 곳은 `redis_stream.py` 와 이 모듈 둘뿐이다. 스트림 `ticks`(009)와
채널·키(017)는 쓰는 프로세스가 다르고(수집 vs api) 연결 수명도 다르다(명령마다 lazy vs 오래 사는 구독)
— 그래서 클래스를 나눴다. 명령 자동 재시도는 009 와 같이 끈다 — 실패는 호출자가 처리한다.

채널·키 (db.md Redis 절):
- 채널 `spreads`        — 표 JSON, 실시간(수집 → api)
- 키 `spreads:latest`   — 같은 JSON, TTL 10초. 늦게 붙은 구독자의 첫 표. 수집이 멈추면 사라진다
- 키 `spreads:want`     — 값 "1", TTL 15초. api 가 5초마다 갱신(+ `GET /spreads` 요청마다, 018), 수집이 5초마다 읽어 "원함"으로
- 키 `collect:heartbeat` — 틱 시각(ms) 문자열, TTL 30초. 수집이 매 틱 쓰고 api 의 `/health` 가 읽는다 (025)
"""

import time

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.core.redis_stream import CONNECT_TIMEOUT_SEC, SOCKET_TIMEOUT_SEC

CHANNEL = "spreads"
LATEST_KEY = "spreads:latest"
WANT_KEY = "spreads:want"
LATEST_TTL_SEC = 10
WANT_TTL_SEC = 15
HEARTBEAT_KEY = "collect:heartbeat"
HEARTBEAT_TTL_SEC = 30


class RedisUnavailableError(Exception):
    """연결 실패·타임아웃 — 005 의 InfluxUnavailableError 와 같은 역할. HTTP 경계가 503 으로 바꾼다 (018 §3.1)."""


class Subscription:
    """구독 연결 하나 — 끊기면 예외가 나고, 호출자(허브)가 백오프로 새로 만든다."""

    def __init__(self, pubsub: aioredis.client.PubSub) -> None:
        self._pubsub = pubsub

    async def get(self, timeout: float) -> str | None:
        """표 JSON, 또는 timeout 안에 없으면 None. 연결 문제는 예외.

        구독 확인 같은 제어 메시지는 건너뛰고 timeout 안에서 다음 것을 기다린다 — 호출자가
        "None = 조용함" 으로 읽을 수 있게.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            msg = await self._pubsub.get_message(timeout=remaining)
            if msg is None:
                return None
            if msg["type"] == "message":
                return _text(msg["data"])

    async def aclose(self) -> None:
        await self._pubsub.aclose()


class RedisBus:
    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> "RedisBus":
        """생성은 연결하지 않는다(lazy) — 009 의 스트림 클라이언트와 같다."""
        return cls(
            aioredis.from_url(
                url,
                socket_connect_timeout=CONNECT_TIMEOUT_SEC,
                socket_timeout=SOCKET_TIMEOUT_SEC,
                retry=Retry(NoBackoff(), 0),
            )
        )

    # --- 수집 프로세스 쪽 (§3.1) ---

    async def publish_table(self, data: str) -> None:
        """`PUBLISH spreads` + `SET spreads:latest EX 10` 을 한 왕복으로. 실패는 예외."""
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.publish(CHANNEL, data)
            pipe.set(LATEST_KEY, data, ex=LATEST_TTL_SEC)
            await pipe.execute()

    async def wanted(self) -> bool:
        """`spreads:want` 가 살아 있는가. 실패는 예외."""
        return await self._client.exists(WANT_KEY) == 1

    # --- 서빙 프로세스 쪽 (§3.2) ---

    async def want(self) -> None:
        """`SET spreads:want 1 EX 15` — 접속자가 있는 동안 5초마다. 실패는 예외."""
        await self._client.set(WANT_KEY, "1", ex=WANT_TTL_SEC)

    async def latest(self) -> str | None:
        """`GET spreads:latest` — 없으면 None(키 만료 = 수집이 표를 안 만들거나 멈춤). 실패는 예외."""
        value = await self._client.get(LATEST_KEY)
        return None if value is None else _text(value)

    async def latest_and_want(self) -> str | None:
        """`GET spreads:latest` + `SET spreads:want 1 EX 15` 를 한 왕복으로 — `GET /spreads` 요청마다 (018 §3.1).

        폴링만 하는 접속자(WebSocket 이 막힌 망)가 있는 동안에도 수집이 표를 만들어야 해서 읽기와 want 를
        같이 한다. 키가 없어도(404) want 는 쓴다 — 그래야 수집이 다음 5초 안에 표를 만들기 시작한다.
        Redis 에 못 닿으면 둘 다 안 되고 RedisUnavailableError 다 — 백오프 없음, 5초 폴링이 곧 재시도다.
        """
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                pipe.get(LATEST_KEY)
                pipe.set(WANT_KEY, "1", ex=WANT_TTL_SEC)
                value, _ = await pipe.execute()
        except RedisError as exc:
            raise RedisUnavailableError(str(exc)) from exc
        return None if value is None else _text(value)

    async def set_heartbeat(self, ts_ms: int) -> None:
        """`SET collect:heartbeat <ts_ms> EX 30` — 수집 틱 루프가 매초 (025 §3.4). 실패는 예외."""
        await self._client.set(HEARTBEAT_KEY, str(ts_ms), ex=HEARTBEAT_TTL_SEC)

    async def heartbeat(self) -> int | None:
        """`GET collect:heartbeat` — 없으면 None(수집이 30초 넘게 틱을 못 만듦). 실패는 예외."""
        value = await self._client.get(HEARTBEAT_KEY)
        if value is None:
            return None
        return int(_text(value))

    async def subscribe(self) -> Subscription:
        """채널 구독 연결을 새로 연다 — 여기서 실제 연결이 일어나므로 실패는 예외."""
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(CHANNEL)
        except Exception:
            await pubsub.aclose()
            raise
        return Subscription(pubsub)

    async def aclose(self) -> None:
        await self._client.aclose()


def _text(v: bytes | str) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)
