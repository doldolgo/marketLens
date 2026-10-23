"""Redis pub/sub·키 클라이언트 — 스프레드 표 게시·구독 (스펙 017 §3.1·§3.2) + `GET /spreads` 읽기 (018 §3.1).

redis 라이브러리를 import 하는 곳은 `redis_stream.py` 와 이 모듈 둘뿐이다. 스트림 `ticks`(009)와
채널·키(017)는 쓰는 프로세스가 다르고(수집 vs api) 연결 수명도 다르다(명령마다 lazy vs 오래 사는 구독)
— 그래서 클래스를 나눴다. 명령 자동 재시도는 009 와 같이 끈다 — 실패는 호출자가 처리한다.

채널·키 (db.md Redis 절):
- 채널 `spreads`        — 표 JSON, 실시간(수집 → api). 구독자가 있을 때만 PUBLISH 한다(`PUBSUB NUMSUB`, 2026-10-13)
- 키 `spreads:latest`   — 같은 JSON, TTL 10초. 늦게 붙은 구독자의 첫 표. 수집이 멈추면 사라진다
- 채널 `gap`·키 `gap:latest` — 현선갭 표(048 §3.3), 같은 규칙. 채널·키 이름은 표 id 하나에서 나온다(`<id>`·`<id>:latest`) —
  게시·구독·첫 표 읽기는 표 id 를 인자로 받고 기본값이 spreads 라 017 의 호출은 그대로다
- 채널 `pp`·키 `pp:latest` — 선선갭 표(049 §3.3), 같은 규칙
- 키 `spreads:want`     — 값 "1", TTL 15초. api 가 접속자가 있는 동안 5초마다(+ `GET /spreads` 요청마다, 018) 쓴다.
  읽는 쪽은 없다 — 수집은 2026-09-26 부터 접속자와 무관하게 매 틱 표를 만든다 (017 §3.1)
- 키 `gap:want`         — 같은 모양(값 "1", TTL 15초). api 의 gap 허브가 접속자가 있는 동안 5초마다(첫 접속 때 곧바로) 쓰고,
  수집의 gap 게시기가 0.5초마다 읽어 **키가 있을 때만** 표를 만들어 게시한다 (048 §3.3) — 아무도 안 보는 동안
  표 1장(약 3,400행·760KB)을 매초 두 번(PUBLISH·SET) 보내지 않게
- 키 `pp:want`          — 같은 모양·같은 규칙. api 의 pp 허브가 쓰고 수집의 pp 게시기가 읽는다 (049 §3.3)
- 키 `collect:heartbeat` — 틱 시각(ms) 문자열, TTL 30초. 수집이 매 틱 쓰고 api 의 `/health` 가 읽는다 (025)
- 키 `premium_events:open` — 열린 사건 전체의 JSON 사본, TTL 600초. 수집의 사건 쓰기 태스크가 60초 갱신 회차·닫힘 점을
  쓴 회차·종료 때 쓰고 수집 자신이 기동 복원 때 읽는다 (013 §3.3)
- 리스트 `alerts:log` — 보낸 Slack 알림 JSON 줄 최신 1,000건, 만료 없음. 두 역할의 알림기가 왼쪽에 넣고 수집기의
  `/admin/alerts` 가 읽는다 (034 §3.3)
- 키 `admin:clarity` — Clarity 기본 요약의 마지막 시도·결과·마지막 성공 값 JSON, 만료 없음. api 의 `/admin/clarity` 가 읽고 쓴다 (035 §3.3·040 §3.2)
- 키 `admin:clarity:pages` — Clarity 페이지×기기 묶음의 같은 모양 JSON(주소 없음), 만료 없음. 같은 api 경로가 읽고 쓴다 (040 §3.2)
- 해시 `admin:clarity:dims` — Clarity 기기·유입 차원 호출의 기록 칸 둘(필드 `device`·`channel`, 값은 같은 모양 JSON — 주소 없음),
  만료 없음. 같은 api 경로가 읽고 쓴다 (068 §3.1)
- 키 `admin:clarity:day:<YYYYMMDD>` — Clarity 날별 기록 JSON(KST 날짜, 그날 마지막 기본 성공 값 + 그날 차원 값), EXPIREAT
  그날 끝 + 90일. api 의 `/admin/clarity` 가 덮어쓰고 `/admin/clarity/history` 가 읽는다 (068 §3.2)
- 키 `flow:eth:last_block` — 이더리움 감지기가 마지막으로 처리한 블록 번호 문자열, 만료 없음. 수집이 블록마다 쓰고
  연결(재연결) 직후 공백 재생의 시작점으로 읽는다 (050 §3.4)
- 집합 `flow:eth:deposit_addrs`·`flow:eth:hot_wallets`·`flow:eth:internal` — 씨앗에 더한 업비트 주소(소문자), 만료 없음.
  수집이 블록마다 자가 확장으로 더하고 기동 때 씨앗에 합친다 (050 §3.2)
- 해시 `attn:d:<YYYYMMDD>` — 화면 영역 이용 통계의 KST 하루 합계, 필드 `<page>|<device>|<area>|<ms|clicks|seen>`·페이지뷰
  `<page>|<device>||pv`, 값은 정수 합, EXPIREAT 그날 끝 + 90일. api 가 10초 묶음으로 더하고 api 의 `/admin/attention` 이 읽는다 (052 §3.5)
- 화면 이동 기록 `rp:*` (073 §3.5) — 해시 `rp:m:<sid>`(조각 머리), 리스트 `rp:e:<sid>`(zlib 로 누른 묶음 JSON),
  정렬 집합 `rp:ip:<ip>`(sid → first ms)·`rp:recent`(sid → last ms), 정수 `rp:b:<YYYYMMDD>`(KST 그날 누른 바이트 합).
  모두 만료 = 마지막 쓰기 + 14일. api 가 묶음마다 곧바로 쓰고 api 의 `/admin/replay/*` 가 읽는다
"""

import time
from collections.abc import Iterable, Mapping, Sequence

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.core.redis_stream import CONNECT_TIMEOUT_SEC, SOCKET_TIMEOUT_SEC, auth_kwargs

CHANNEL = "spreads"
LATEST_KEY = "spreads:latest"
GAP_CHANNEL = "gap"  # 048 — 현선갭 표. 키는 latest_key(GAP_CHANNEL)
PP_CHANNEL = "pp"  # 049 — 선선갭 표. 키는 latest_key(PP_CHANNEL)
WANT_KEY = "spreads:want"  # = want_key(CHANNEL)
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
# 068 — 기기·유입 차원 호출 둘의 기록. 해시 하나에 칸이 호출마다 하나라 한 칸 쓰기가 다른 칸을 덮지 않고(읽고 고쳐
# 쓰지 않는다), 키를 지우면 두 칸이 같이 사라진다
CLARITY_DIMS_KEY = "admin:clarity:dims"
# 068 — Clarity 날별 기록 `admin:clarity:day:<YYYYMMDD>`(KST 날짜)
CLARITY_DAY_PREFIX = "admin:clarity:day:"
# 050 — 이더리움 감지기. 마지막 처리 블록(재생 시작점)과 씨앗에 더한 주소 집합 셋 — 전부 만료 없음
FLOW_LAST_BLOCK_KEY = "flow:eth:last_block"
FLOW_DEPOSIT_ADDRS_KEY = "flow:eth:deposit_addrs"
FLOW_HOT_WALLETS_KEY = "flow:eth:hot_wallets"
FLOW_INTERNAL_KEY = "flow:eth:internal"
# 052 — 화면 영역 이용 통계 하루 합계 해시 `attn:d:<YYYYMMDD>`(KST 날짜)
ATTENTION_PREFIX = "attn:d:"
# 073 — 화면 이동 기록. 조각(sid)마다 머리 해시·묶음 리스트, IP 별·최근 정렬 집합, KST 날마다 누른 바이트 합
REPLAY_META_PREFIX = "rp:m:"
REPLAY_EVENTS_PREFIX = "rp:e:"
REPLAY_IP_PREFIX = "rp:ip:"
REPLAY_RECENT_KEY = "rp:recent"
REPLAY_DAY_PREFIX = "rp:b:"
# 078 — S3 로 옮길 차례(keep 조각 sid → last). 수집기가 3시간 조용한 조각을 옮기고 뺀다
REPLAY_TODO_KEY = "rp:todo"


def want_key(table: str) -> str:
    """표 id → 보는 사람 흔적 키 `<id>:want` (017 §3.2·048 §3.3)."""
    return f"{table}:want"


def latest_key(table: str) -> str:
    """표 id → 마지막 표 키 `<id>:latest` (017 §3.1·048 §3.3)."""
    return f"{table}:latest"


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
    def from_url(cls, url: str, password: str | None = None) -> "RedisBus":
        """생성은 연결하지 않는다(lazy) — 009 의 스트림 클라이언트와 같다. 구독 연결도 같은 인증을 쓴다(021 §3.7)."""
        return cls(
            aioredis.from_url(
                url,
                socket_connect_timeout=CONNECT_TIMEOUT_SEC,
                socket_timeout=SOCKET_TIMEOUT_SEC,
                retry=Retry(NoBackoff(), 0),
                **auth_kwargs(password),
            )
        )

    # --- 수집 프로세스 쪽 (§3.1) ---

    async def publish_table(self, data: str, table: str = CHANNEL) -> int:
        """`SET <id>:latest EX 10` 은 매번, `PUBLISH <id>` 는 그 채널에 구독자가 있을 때만 — 받은 구독자 수를 돌려준다.

        한 왕복에 SET 과 `PUBSUB NUMSUB <id>` 를 보내고 구독자가 있으면 이어서 PUBLISH 한다(017 §3.1, 2026-10-13). api 의
        허브는 접속자가 있는 동안만 채널을 구독하므로, 아무도 안 보는 동안 표 1장(spreads 약 0.7MB)을 매초 채널로 한 번 더
        보내지 않는다 — collect → data 송신이 그만큼 준다. 구독이 막 열려 이 회차를 놓친 허브는 다음 회차부터 받는다:
        허브의 첫 표는 `<id>:latest` 이고 놓친 표는 다음 delta 가 직전 인덱스와 비교하므로 표가 어긋나지 않는다(§3.2).
        실패는 예외. 기본은 spreads, 048 은 `gap`.
        """
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.set(latest_key(table), data, ex=LATEST_TTL_SEC)
            pipe.pubsub_numsub(table)
            _, numsub = await pipe.execute()
        if not numsub or not int(numsub[0][1]):
            return 0
        return int(await self._client.publish(table, data))

    # --- 서빙 프로세스 쪽 (§3.2) ---

    async def want(self, table: str = CHANNEL) -> None:
        """`SET <id>:want 1 EX 15` — 그 표의 허브에 접속자가 있는 동안 5초마다. 실패는 예외. 기본은 spreads."""
        await self._client.set(want_key(table), "1", ex=WANT_TTL_SEC)

    async def wanted(self, table: str) -> bool:
        """`EXISTS <id>:want` — 그 표를 보는 사람이 있는가(수집 쪽, 048 §3.3). 실패는 예외."""
        return bool(await self._client.exists(want_key(table)))

    async def latest(self, table: str = CHANNEL) -> str | None:
        """`GET <id>:latest` — 없으면 None(키 만료 = 수집이 표를 안 만들거나 멈춤). 실패는 예외."""
        value = await self._client.get(latest_key(table))
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

    async def clarity_dims_load(self, field: str) -> str | None:
        """`HGET admin:clarity:dims <field>` — 차원 호출 한 칸(`device`·`channel`)의 기록 JSON (068 §3.1). 없으면 None. 실패는 예외."""
        value = await self._client.hget(CLARITY_DIMS_KEY, field)
        return None if value is None else _text(value)

    async def clarity_dims_save(self, field: str, data: str) -> None:
        """`HSET admin:clarity:dims <field> <JSON>` — 그 칸만, 만료 없음 (068 §3.1). 실패는 예외."""
        await self._client.hset(CLARITY_DIMS_KEY, field, data)

    async def clarity_day_save(self, day: str, data: str, expire_at: int) -> None:
        """`SET admin:clarity:day:<day> <JSON> EXAT <expire_at>` — 그날 칸을 덮어쓴다 (068 §3.2). 실패는 예외.

        expire_at 은 epoch 초 — 그 KST 날의 끝 + 90일. 덮어쓸 때마다 같은 값이라 만료가 밀리지 않는다.
        """
        await self._client.set(CLARITY_DAY_PREFIX + day, data, exat=expire_at)

    async def clarity_days_load(self, days: Sequence[str]) -> list[str | None]:
        """`MGET admin:clarity:day:<day>…` 한 번 — 날 순서대로 기록 JSON, 없는 날은 None (068 §3.2). 실패는 예외."""
        if not days:
            return []
        values = await self._client.mget([CLARITY_DAY_PREFIX + day for day in days])
        return [None if v is None else _text(v) for v in values]

    async def flow_last_block_load(self) -> int | None:
        """`GET flow:eth:last_block` — 연결 직후 공백 재생의 시작점 (050 §3.4). 없으면 None(첫 기동 = head 부터). 실패는 예외."""
        value = await self._client.get(FLOW_LAST_BLOCK_KEY)
        if value is None:
            return None
        return int(_text(value))

    async def flow_last_block_save(self, block: int) -> None:
        """`SET flow:eth:last_block <번호>` — 감지기가 블록마다, 만료 없음 (050 §3.4). 실패는 예외."""
        await self._client.set(FLOW_LAST_BLOCK_KEY, str(block))

    async def flow_set_load(self, key: str) -> set[str]:
        """`SMEMBERS <집합 키>` — 기동 때 씨앗에 합칠 추가 주소 (050 §3.2). 없으면 빈 집합. 실패는 예외.

        키는 `FLOW_DEPOSIT_ADDRS_KEY`·`FLOW_HOT_WALLETS_KEY`·`FLOW_INTERNAL_KEY` 셋 중 하나다.
        """
        values = await self._client.smembers(key)
        return {_text(v) for v in values}

    async def flow_set_add(self, key: str, addrs: Iterable[str]) -> None:
        """`SADD <집합 키> …` — 자가 확장으로 새로 안 주소를 즉시, 만료 없음 (050 §3.2). 빈 목록은 명령하지 않는다. 실패는 예외."""
        members = list(addrs)
        if not members:
            return
        await self._client.sadd(key, *members)

    async def attention_add(
        self, day: str, counts: Mapping[str, int], expire_at: int
    ) -> None:
        """`HINCRBY attn:d:<day>` 묶음 + `EXPIREAT <expire_at>` 를 한 파이프라인으로 (052 §3.5). 빈 묶음은 명령하지 않는다. 실패는 예외.

        expire_at 은 epoch 초 — 그 KST 날의 끝 + 90일. 쓸 때마다 같은 값이라 몇 번 써도 만료가 밀리지 않는다.
        """
        if not counts:
            return
        key = ATTENTION_PREFIX + day
        async with self._client.pipeline(transaction=False) as pipe:
            for field, n in counts.items():
                pipe.hincrby(key, field, n)
            pipe.expireat(key, expire_at)
            await pipe.execute()

    async def attention_days(self, days: Sequence[str]) -> dict[str, dict[str, int]]:
        """`HGETALL attn:d:<day>` 를 날마다 한 파이프라인으로 — 관리자 피드의 창 (052 §3.6). 키 없는 날은 빈 dict. 실패는 예외."""
        if not days:
            return {}
        async with self._client.pipeline(transaction=False) as pipe:
            for day in days:
                pipe.hgetall(ATTENTION_PREFIX + day)
            found = await pipe.execute()
        return {
            day: {_text(k): int(_text(v)) for k, v in raw.items()}
            for day, raw in zip(days, found, strict=True)
        }

    async def replay_load(self, sid: str, day: str) -> tuple[dict[str, str], int]:
        """`HGETALL rp:m:<sid>` + `GET rp:b:<day>` 한 왕복 — 묶음을 쓰기 전 조각 머리와 그날 바이트 합 (073 §3.5).
        조각이 없으면 빈 dict, 그날 키가 없으면 0. 실패는 예외."""
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.hgetall(REPLAY_META_PREFIX + sid)
            pipe.get(REPLAY_DAY_PREFIX + day)
            meta, used = await pipe.execute()
        return {_text(k): _text(v) for k, v in meta.items()}, int(used or 0)

    async def replay_append(
        self,
        sid: str,
        meta: Mapping[str, str],
        blob: bytes,
        day: str,
        ttl_sec: int,
    ) -> None:
        """묶음 하나를 MULTI 한 번으로 (073 §3.5·078 §3.1) — 머리 `HSET`, 묶음 `RPUSH`, `rp:ip:<meta ip>` 에 sid → first,
        `rp:recent` 에 sid → last, `rp:b:<day>` 에 묶음 바이트를 더한다. 묶음·그날 키는 `EXPIRE ttl_sec`, 두 집합은 만료·잘라내기
        없이 `PERSIST`(앞 코드가 단 만료를 걷는다). 머리는 `keep` 이 1 이면 만료 없이 두고 `rp:todo` 에 sid → last 를 넣으며,
        아니면 `EXPIRE ttl_sec`. meta 는 머리 전체(`ip`·`first`·`last` 필수 — 호출자가 새 값을 다 셈한다). 실패는 예외."""
        ip_key = REPLAY_IP_PREFIX + meta["ip"]
        meta_key = REPLAY_META_PREFIX + sid
        keep = meta.get("keep") == "1"
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hset(meta_key, mapping=dict(meta))
            pipe.rpush(REPLAY_EVENTS_PREFIX + sid, blob)
            pipe.zadd(ip_key, {sid: int(meta["first"])})
            pipe.zadd(REPLAY_RECENT_KEY, {sid: int(meta["last"])})
            pipe.incrby(REPLAY_DAY_PREFIX + day, len(blob))
            for key in (REPLAY_EVENTS_PREFIX + sid, REPLAY_DAY_PREFIX + day):
                pipe.expire(key, ttl_sec)
            pipe.persist(ip_key)
            pipe.persist(REPLAY_RECENT_KEY)
            if keep:
                pipe.persist(meta_key)
                pipe.zadd(REPLAY_TODO_KEY, {sid: int(meta["last"])})
            else:
                pipe.expire(meta_key, ttl_sec)
            await pipe.execute()

    async def replay_mark_cut(self, sid: str, ttl_sec: int | None) -> None:
        """`HSET rp:m:<sid> cut 1` — 조각 상한(1MB·묶음 수)으로 묶음을 버렸다는 표시 (073 §3.5). ttl_sec 가 None(keep 조각,
        078 §3.1)이면 만료를 달지 않는다. 실패는 예외."""
        key = REPLAY_META_PREFIX + sid
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.hset(key, "cut", "1")
            if ttl_sec is not None:
                pipe.expire(key, ttl_sec)
            await pipe.execute()

    async def replay_recent(self, min_ms: int | None, limit: int) -> list[str]:
        """`ZREVRANGEBYSCORE rp:recent +inf <min_ms> LIMIT 0 <limit>` — last 가 min_ms 이상인 sid, 최근 순 (073 §3.6).
        min_ms 가 None 이면 전부. 실패는 예외."""
        low = "-inf" if min_ms is None else min_ms
        found = await self._client.zrevrangebyscore(
            REPLAY_RECENT_KEY, "+inf", low, start=0, num=limit
        )
        return [_text(v) for v in found]

    async def replay_ip(self, ip: str, limit: int) -> list[str]:
        """`ZREVRANGE rp:ip:<ip> 0 <limit-1>` — 그 IP 의 sid, first 가 늦은 순 (073 §3.6). 없으면 빈 목록. 실패는 예외."""
        found = await self._client.zrevrange(REPLAY_IP_PREFIX + ip, 0, limit - 1)
        return [_text(v) for v in found]

    async def replay_metas(self, sids: Sequence[str]) -> list[dict[str, str]]:
        """`HGETALL rp:m:<sid>` 를 sid 마다 한 파이프라인으로 — 순서대로, 만료된 조각은 빈 dict (073 §3.6). 실패는 예외."""
        if not sids:
            return []
        async with self._client.pipeline(transaction=False) as pipe:
            for sid in sids:
                pipe.hgetall(REPLAY_META_PREFIX + sid)
            found = await pipe.execute()
        return [{_text(k): _text(v) for k, v in raw.items()} for raw in found]

    async def replay_batches(
        self, sid: str, start: int, count: int
    ) -> tuple[dict[str, str], list[bytes], int]:
        """`HGETALL rp:m:<sid>` + `LRANGE rp:e:<sid> start start+count-1` + `LLEN` 한 왕복 — 머리·누른 묶음 바이트·전체 묶음 수
        (073 §3.6). 없으면 (빈 dict, [], 0). 실패는 예외."""
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.hgetall(REPLAY_META_PREFIX + sid)
            pipe.lrange(REPLAY_EVENTS_PREFIX + sid, start, start + count - 1)
            pipe.llen(REPLAY_EVENTS_PREFIX + sid)
            meta, blobs, total = await pipe.execute()
        return (
            {_text(k): _text(v) for k, v in meta.items()},
            list(blobs),
            int(total),
        )

    async def replay_todo(self, max_ms: int, limit: int) -> list[tuple[str, int]]:
        """`ZRANGEBYSCORE rp:todo -inf <max_ms> WITHSCORES LIMIT 0 <limit>` — last 가 max_ms 이하인 옮길 조각, 오래된 순
        (078 §3.2). 실패는 예외."""
        found = await self._client.zrangebyscore(
            REPLAY_TODO_KEY, "-inf", max_ms, start=0, num=limit, withscores=True
        )
        return [(_text(sid), int(score)) for sid, score in found]

    async def replay_todo_oldest(self) -> int | None:
        """`ZRANGE rp:todo 0 0 WITHSCORES` — 가장 오래 기다린 조각의 last, 비었으면 None (078 §3.2). 실패는 예외."""
        found = await self._client.zrange(REPLAY_TODO_KEY, 0, 0, withscores=True)
        return int(found[0][1]) if found else None

    async def replay_dump(self, sid: str) -> tuple[dict[str, str], list[bytes]]:
        """`HGETALL rp:m:<sid>` + `LRANGE rp:e:<sid> 0 -1` 한 왕복 — 옮길 조각의 머리와 누른 묶음 전부 (078 §3.2). 실패는 예외."""
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.hgetall(REPLAY_META_PREFIX + sid)
            pipe.lrange(REPLAY_EVENTS_PREFIX + sid, 0, -1)
            meta, blobs = await pipe.execute()
        return {_text(k): _text(v) for k, v in meta.items()}, list(blobs)

    async def replay_archived(self, sid: str, last: int, count: int) -> bool:
        """옮긴 뒤 (078 §3.2) — `rp:todo` 의 점수가 읽은 last 와 같으면 `ZREM` 하고 머리에 `arch=<count>`, True. 그 사이 묶음이
        와 점수가 바뀌었으면 아무것도 하지 않고 False(다음 차례에 다시 통째로 옮긴다). 읽기와 쓰기 사이는 원자가 아니다 — 3시간
        조용했던 조각이라 그 틈에 묶음이 올 일이 없다(replay.js 조각 상한 2시간). 실패는 예외."""
        score = await self._client.zscore(REPLAY_TODO_KEY, sid)
        if score is None or int(score) != last:
            return False
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.zrem(REPLAY_TODO_KEY, sid)
            pipe.hset(REPLAY_META_PREFIX + sid, "arch", str(count))
            await pipe.execute()
        return True

    async def replay_drop_todo(self, sid: str) -> None:
        """`ZREM rp:todo sid` — 머리가 없는 조각(옮길 것이 없다) (078 §3.2). 실패는 예외."""
        await self._client.zrem(REPLAY_TODO_KEY, sid)

    async def replay_forget(self, ip: str) -> list[dict[str, str]]:
        """IP 하나의 화면 이동 기록을 지운다 (078 §3.6) — `rp:ip:<ip>` 의 sid 마다 머리(지우기 전 값을 돌려준다 — 호출자가 S3 키를
        셈한다)·묶음·`rp:recent`·`rp:todo` 항목, 그리고 그 집합. 실패는 예외."""
        ip_key = REPLAY_IP_PREFIX + ip
        sids = [_text(v) for v in await self._client.zrange(ip_key, 0, -1)]
        metas = await self.replay_metas(sids)
        async with self._client.pipeline(transaction=True) as pipe:
            for sid in sids:
                pipe.delete(REPLAY_META_PREFIX + sid, REPLAY_EVENTS_PREFIX + sid)
                pipe.zrem(REPLAY_RECENT_KEY, sid)
                pipe.zrem(REPLAY_TODO_KEY, sid)
            pipe.delete(ip_key)
            await pipe.execute()
        return [{**meta, "sid": sid} for sid, meta in zip(sids, metas, strict=True)]

    async def subscribe(self, channel: str = CHANNEL) -> Subscription:
        """채널 구독 연결을 새로 연다 — 여기서 실제 연결이 일어나므로 실패는 예외. 한 연결은 한 채널만 (048 §3.4)."""
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(channel)
        except Exception:
            await pubsub.aclose()
            raise
        return Subscription(pubsub)

    async def aclose(self) -> None:
        await self._client.aclose()


def _text(v: bytes | str) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)
