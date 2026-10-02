"""Redis pub/sub·키 클라이언트 — 스프레드 표 게시·구독 (스펙 017 §3.1·§3.2) + `GET /spreads` 읽기 (018 §3.1).

redis 라이브러리를 import 하는 곳은 `redis_stream.py` 와 이 모듈 둘뿐이다. 스트림 `ticks`(009)와
채널·키(017)는 쓰는 프로세스가 다르고(수집 vs api) 연결 수명도 다르다(명령마다 lazy vs 오래 사는 구독)
— 그래서 클래스를 나눴다. 명령 자동 재시도는 009 와 같이 끈다 — 실패는 호출자가 처리한다.

채널·키 (db.md Redis 절):
- 채널 `spreads`        — 표 JSON, 실시간(수집 → api)
- 키 `spreads:latest`   — 같은 JSON, TTL 10초. 늦게 붙은 구독자의 첫 표. 수집이 멈추면 사라진다
- 키 `spreads:want`     — 값 "1", TTL 15초. api 가 접속자가 있는 동안 5초마다(+ `GET /spreads` 요청마다, 018) 쓴다.
  읽는 쪽은 없다 — 수집은 2026-09-26 부터 접속자와 무관하게 매 틱 표를 만든다 (017 §3.1)
- 키 `collect:heartbeat` — 틱 시각(ms) 문자열, TTL 30초. 수집이 매 틱 쓰고 api 의 `/health` 가 읽는다 (025)
- 키 `premium_events:open` — 열린 사건 전체의 JSON 사본, TTL 600초. 수집의 사건 쓰기 태스크가 60초 갱신 회차·닫힘 점을
  쓴 회차·종료 때 쓰고 수집 자신이 기동 복원 때 읽는다 (013 §3.3)
- 리스트 `alerts:log` — 보낸 Slack 알림 JSON 줄 최신 1,000건, 만료 없음. 두 역할의 알림기가 왼쪽에 넣고 수집기의
  `/admin/alerts` 가 읽는다 (034 §3.3)
- 키 `admin:clarity` — Clarity 기본 요약의 마지막 시도·결과·마지막 성공 값 JSON, 만료 없음. api 의 `/admin/clarity` 가 읽고 쓴다 (035 §3.3·040 §3.2)
- 키 `admin:clarity:pages` — Clarity 페이지×기기 묶음의 같은 모양 JSON(주소 없음), 만료 없음. 같은 api 경로가 읽고 쓴다 (040 §3.2)
"""

import time
from collections.abc import Mapping

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
DAY_OPEN_PREFIX = "dayopen:"  # 026 — `dayopen:<YYYY-MM-DD>` 해시
DAY_OPEN_TTL_SEC = 48 * 3600
# 013 — 열린 사건 사본(다음 저장이 덮는다). TTL = 결측 허용 600초 — 그보다 오래된 사본의 사건은 복원이 어차피 닫고,
# 수집이 오래 멈췄거나 옛 코드로 되돌렸다 온 기동은 낡은 사본 대신 Influx 로 복원한다
OPEN_EVENTS_KEY = "premium_events:open"
OPEN_EVENTS_TTL_SEC = 600
# 034 — 보낸 알림 기록. 만료 없이 최신 이만큼만 남긴다(넣기·자르기 한 왕복)
ALERT_LOG_KEY = "alerts:log"
ALERT_LOG_MAX = 1000
# 035 — Clarity 호출 기록. 만료 없음 — api 재시작(배포)이 하루 10회 한도를 쓰지 않게 시도 시각을 남긴다
CLARITY_KEY = "admin:clarity"
# 040 — 페이지×기기 묶음 호출의 기록. 기본 기록과 따로 둬 두 간격·결과·값이 서로를 덮지 않는다
CLARITY_PAGES_KEY = "admin:clarity:pages"


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

        want 는 018 계약대로 요청마다(키가 없어 404 여도) 쓰지만 지금은 읽는 쪽이 없다 — 수집은 2026-09-26 부터
        want 와 무관하게 매 틱 표를 만든다(017 §3.1). Redis 에 못 닿으면 둘 다 안 되고 RedisUnavailableError 다 —
        백오프 없음, 다음 요청이 곧 재시도다.
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

    async def day_open_load(self, date: str) -> dict[str, float]:
        """`HGETALL dayopen:<date>` — 기동·자정 직후 장부 복원 (026 §3.1). 없으면 빈 dict. 실패는 예외."""
        raw = await self._client.hgetall(DAY_OPEN_PREFIX + date)
        return {_text(k): float(_text(v)) for k, v in raw.items()}

    async def day_open_save(self, date: str, prices: Mapping[str, float]) -> None:
        """`HSET dayopen:<date>` + `EXPIRE 48h` 를 한 왕복으로 (026 §3.1). 실패는 예외."""
        key = DAY_OPEN_PREFIX + date
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.hset(key, mapping={k: repr(v) for k, v in prices.items()})
            pipe.expire(key, DAY_OPEN_TTL_SEC)
            await pipe.execute()

    async def open_events_save(self, data: str) -> None:
        """`SET premium_events:open <JSON> EX 600` — 사건 쓰기 태스크가 60초 갱신·닫힘 회차·종료 때 (013 §3.3). 실패는 예외."""
        await self._client.set(OPEN_EVENTS_KEY, data, ex=OPEN_EVENTS_TTL_SEC)

    async def open_events_load(self) -> str | None:
        """`GET premium_events:open` — 기동 복원이 먼저 읽는다. 없으면 None. 실패는 예외."""
        value = await self._client.get(OPEN_EVENTS_KEY)
        return None if value is None else _text(value)

    async def heartbeat(self) -> int | None:
        """`GET collect:heartbeat` — 없으면 None(수집이 30초 넘게 틱을 못 만듦). 실패는 예외."""
        value = await self._client.get(HEARTBEAT_KEY)
        if value is None:
            return None
        return int(_text(value))

    async def ping(self) -> None:
        """`PING` — 관리자 상태의 Redis 확인(029 §3.4). 실패는 예외(시간 제한은 호출자가 건다)."""
        await self._client.ping()

    async def alert_log_push(self, line: str) -> None:
        """`LPUSH alerts:log` + `LTRIM 0 999` 를 한 왕복으로 — 알림기가 보낸 뒤 한 줄 (034 §3.3). 실패는 예외."""
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.lpush(ALERT_LOG_KEY, line)
            pipe.ltrim(ALERT_LOG_KEY, 0, ALERT_LOG_MAX - 1)
            await pipe.execute()

    async def alert_log_recent(self, limit: int) -> list[str]:
        """`LRANGE alerts:log 0 limit-1` — 최신순. 없으면 빈 목록. 실패는 예외."""
        values = await self._client.lrange(ALERT_LOG_KEY, 0, limit - 1)
        return [_text(v) for v in values]

    async def clarity_load(self) -> str | None:
        """`GET admin:clarity` — Clarity 마지막 시도·결과·마지막 성공 값 JSON (035 §3.3). 없으면 None. 실패는 예외."""
        value = await self._client.get(CLARITY_KEY)
        return None if value is None else _text(value)

    async def clarity_save(self, data: str) -> None:
        """`SET admin:clarity <JSON>` — 만료 없음 (035 §3.3). 실패는 예외."""
        await self._client.set(CLARITY_KEY, data)

    async def clarity_pages_load(self) -> str | None:
        """`GET admin:clarity:pages` — Clarity 페이지×기기 묶음 기록 JSON (040 §3.2). 없으면 None. 실패는 예외."""
        value = await self._client.get(CLARITY_PAGES_KEY)
        return None if value is None else _text(value)

    async def clarity_pages_save(self, data: str) -> None:
        """`SET admin:clarity:pages <JSON>` — 만료 없음 (040 §3.2). 실패는 예외."""
        await self._client.set(CLARITY_PAGES_KEY, data)

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
