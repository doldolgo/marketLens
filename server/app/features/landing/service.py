"""랜딩 요약 — `GET /landing` 의 세 부분(live·trail·events)을 만든다 (스펙 022 §3.2).

계산(`build_live`·`build_trail`·`build_events`)은 순수 함수다. `LandingService` 는 부분마다 프로세스 메모리
캐시(live 5초·trail·events 60초)를 들고, 비었거나 만료된 부분에 요청이 몰려도 저장소는 한 번만 읽는다.
Influx 부분(trail·events)은 3초까지만 기다리고, 늦으면 직전 값을 싣고 조회는 뒤에서 마저 돈다.
저장소는 core 의 Redis(`spreads:latest` 읽기만 — `spreads:want` 는 쓰지 않는다)와 Influx(봉 조회·사건 요약 조회)
클라이언트를 인자로 받는다. 사건은 점을 올리지 않고 core 의 요약 조회가 Flux 에서 접은 결과만 받는다. 다른 기능은
import 하지 않는다.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, Protocol

from app.core.candles import TIER_BY_RES
from app.core.influx import CandleRow, EventSummary, InfluxUnavailableError
from app.core.premium_events import MAX_GAP_SEC
from app.features.landing.models import (
    DepthGapOut,
    EventOut,
    EventsOut,
    LandingResponse,
    LiveOut,
    RouteOut,
    TrailOut,
)

logger = logging.getLogger("marketlens.landing")

LIVE_TTL_SEC = 5.0
TRAIL_TTL_SEC = 60.0
EVENTS_TTL_SEC = 60.0
TRAIL_WINDOW_SEC = 3_600
EVENTS_WINDOW_SEC = 604_800  # 7일
TOP_N = 5
# 013 사건 진입 기준과 같은 값·같은 원값(순값 + 차감폭 — 틱의 최우선 호가 값)으로 센다
OVER_PCT = 1.0
# 호가 깊이 예시 — 원값(맨 위 호가)이 이만큼 벌어졌고 차감폭이 이만큼 이상인 경로만. 1위 경로는 대개 호가가
# 두꺼워 원값과 순값이 0.01~0.1%p 밖에 안 달라 요점이 안 보인다
DEPTH_RAW_MIN = 1.0
DEPTH_SLIP_MIN = 0.1
TRAIL_MIN_POINTS = 2  # 선 하나를 그리려면 두 점이 있어야 한다
TRAIL_BUCKET = TIER_BY_RES["1m"].bucket  # candles_1m (014)
# Influx 부분은 이만큼만 기다리고 늦으면 직전 값 — 클라이언트 자체 타임아웃(60초)만큼 응답이 늦지 않게
INFLUX_WAIT_SEC = 3.0

# 방향 → (값 키, 차감폭 키, 출발 쪽 출금 키, 도착 쪽 입금 키).
# 김프는 해외에서 사서 국내로(해외 출금·국내 입금), 역프는 국내에서 사서 해외로(국내 출금·해외 입금).
_DIRECTIONS = (
    ("kimp", "fwd", "slipFwd", "wdFx", "depDom"),
    ("reverse", "rev", "slipRev", "wdDom", "depFx"),
)


class TableReader(Protocol):
    """Redis `spreads:latest` 읽기 — core.redis_bus.RedisBus.latest 시그니처."""

    async def latest(self) -> str | None: ...


class LandingReader(Protocol):
    """Influx 읽기 두 가지(봉·사건 요약) — core.influx.InfluxClient 시그니처의 일부."""

    def query_candles(
        self,
        bucket: str,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        fx: str | None = None,
        base: str | None = None,
    ) -> list[CandleRow]: ...

    def query_event_summary(
        self, *, start: int, stop: int, open_since: int, top_n: int
    ) -> EventSummary: ...


# 표 글자에서 행의 spark 목록을 찾는 두 표식 — 017 게시기는 공백 없이(`separators=(",", ":")`) 행 키를 003 순서로
# 적으므로 spark 는 `,"spark":[…]` 로 시작해 바로 다음 키 status 앞의 `],"status":` 에서 끝난다
_SPARK_HEAD = ',"spark":['
_SPARK_TAIL = '],"status":'


def _strip_spark(text: str) -> str:
    """표 글자에서 행마다 `,"spark":[…]` 를 걷어 낸 글자 — live 는 spark 를 읽지 않는다.

    표 글자의 약 40% 가 spark 부동소수(행 1,800여 개 × 30개)라 그만큼 파싱을 던다. 걷어 낸 글자를 파싱하면
    spark 키만 빠질 뿐 나머지 값은 원문을 파싱한 것과 같다 — JSON 문자열 안에서는 따옴표가 `\\"` 로 적혀
    두 표식이 문자열 안에 생길 수 없고, 목록 안에 `[` 가 없으면서 첫 `]` 바로 뒤가 `,"status":` 이면 그 `]` 가
    목록의 끝이다. 한 행이라도 모양이 다르면(spark 가 행 끝·다음 키가 status 가 아님·목록 안에 `[`) 원문을 그대로
    돌려줘 전체를 파싱한다 — 다음 행의 status 까지 건너뛰면 두 행이 한 행으로 합쳐져 아무 신호 없이 틀리기
    때문이다. 걷어 낸 목록 안의 글자는 검사하지 않는다(spark 목록 안에서만 JSON 이 깨진 글자도 파싱된다) —
    게시기가 `json.dumps` 로 적으므로 그런 표는 생기지 않는다.
    """
    find = text.find
    parts: list[str] = []
    pos = 0
    while True:
        i = find(_SPARK_HEAD, pos)
        if i < 0:
            if not parts:
                return text  # spark 가 없는 표(빈 표·다른 모양) — 사본을 만들지 않는다
            parts.append(text[pos:])
            return "".join(parts)
        start = i + len(_SPARK_HEAD)
        end = find("]", start)
        if end < 0 or not text.startswith(_SPARK_TAIL, end) or find("[", start, end) >= 0:
            return text
        parts.append(text[pos:i])
        pos = end + 1  # `]` 다음의 `,"status":` 부터 이어 붙인다


def build_live(text: str) -> LiveOut | None:
    """`spreads:latest` 본문 → live. JSON 이 아니거나 `rows` 가 배열이 아니면 None."""
    try:
        table = json.loads(_strip_spark(text))
    except ValueError:
        return None
    if not isinstance(table, dict) or not isinstance(table.get("rows"), list):
        return None
    rows: list[dict[str, Any]] = table["rows"]
    best: dict[str, RouteOut] = {}
    depth: DepthGapOut | None = None
    over1 = over1_movable = 0
    for row in rows:
        if row["status"] != "ok":
            continue  # stale·fail 행의 값은 지금 값이 아니다
        for direction, value_key, slip_key, out_key, in_key in _DIRECTIONS:
            value = row[value_key]
            slip = row[slip_key]
            raw = value + slip  # 원값 — 사건 진입과 같은 기준으로 센다
            # null(모름)은 열림이 아니다 — true 일 때만 옮길 수 있다
            movable = row[out_key] is True and row[in_key] is True
            if raw >= OVER_PCT:
                over1 += 1
                if movable:
                    over1_movable += 1
            if not movable:
                continue
            # 호가 깊이 예시 — 차감폭이 가장 큰 하나, 같으면 sym 오름차순(그래도 같으면 표에서 먼저 나온 것)
            if (
                raw >= DEPTH_RAW_MIN
                and slip >= DEPTH_SLIP_MIN
                and (
                    depth is None
                    or slip > depth.slip
                    or (slip == depth.slip and row["sym"] < depth.sym)
                )
            ):
                depth = DepthGapOut(
                    sym=row["sym"],
                    dom=row["dom"],
                    fx=row["fx"],
                    dir=direction,
                    raw=raw,
                    pct=value,
                    slip=slip,
                )
            current = best.get(row["sym"])
            # 코인당 하나 — 값이 같으면 표에서 먼저 나온 것을 둔다
            if current is None or value > current.pct:
                best[row["sym"]] = RouteOut(
                    sym=row["sym"],
                    dom=row["dom"],
                    fx=row["fx"],
                    dir=direction,
                    pct=value,
                    slip=slip,
                    krw=row["krw"],
                    usd=row["usd"],
                    net_dom=row["netDom"],
                    net_fx=row["netFx"],
                )
    # 0 이하 값도 숨기지 않는다 — 지금 시장이 그렇다는 뜻이다
    top = sorted(best.values(), key=lambda r: (-r.pct, r.sym))[:TOP_N]
    return LiveOut(
        data_received_at=table.get("dataReceivedAt"),
        rate=table["rate"],
        coins=len({row["sym"] for row in rows}),
        pairs=len(rows),
        over1=over1,
        over1_movable=over1_movable,
        depth_gap=depth,
        top=top,
    )


def build_trail(route: RouteOut, candles: list[CandleRow]) -> TrailOut | None:
    """그 경로의 1분 봉 → `[창 시작, 종가]` ts 오름차순. 김프는 `fwd_c`, 역프는 `rev_c`(차감 전 원값)."""
    points = sorted(
        (c.ts, c.fwd_c if route.dir == "kimp" else c.rev_c) for c in candles
    )
    if len(points) < TRAIL_MIN_POINTS:
        return None
    return TrailOut(
        sym=route.sym, dom=route.dom, fx=route.fx, dir=route.dir, points=points
    )


def build_events(summary: EventSummary, *, start: int, stop: int) -> EventsOut:
    """core 요약 → 7일 사건 부분. 코인마다 가장 늦게 끝난 닫힌 사건을 끝난 순 5개 — 고르기는 요약 조회가 한다.

    최고값 순으로 고르지 않는다 — 7일 최고값 자리는 입출금이 막혔거나 이름만 같은 다른 코인의
    수백 % 값이 차지해서, 계속 기록하고 있다는 것을 현실적인 값으로 보여 주지 못한다.
    """
    return EventsOut(
        start=start,
        stop=stop,
        count=summary.kimp + summary.reverse,
        kimp=summary.kimp,
        reverse=summary.reverse,
        open=summary.open,
        top=[
            EventOut(
                sym=r.base,
                dom=r.dom,
                fx=r.fx,
                dir=r.dir,  # type: ignore[arg-type] — 태그 값, 모델이 kimp|reverse 로 검증한다
                max_percent=r.max_percent,
                start_ts=r.start_ts,
                end_ts=r.end_ts,
                duration_seconds=r.duration_seconds,
                last_ts=r.last_ts,
            )
            for r in summary.latest
        ],
    )


async def _load_live(bus: TableReader | None) -> LiveOut | None:
    if bus is None:
        return None
    try:
        text = await bus.latest()
    except Exception:
        # Redis 불달·타임아웃 — 이 부분만 null. redis 예외 타입은 core 밖에서 import 하지 않는다
        return None
    if text is None:
        return None  # 키 없음 — 수집이 표를 안 만들거나 멈췄다
    try:
        return build_live(text)
    except Exception as exc:
        logger.warning("랜딩 live 계산 실패 — 표 모양이 계약과 다르다: %r", exc)
        return None


async def _load_trail(
    influx: LandingReader, route: RouteOut, now: int
) -> TrailOut | None:
    try:
        candles = await asyncio.to_thread(
            influx.query_candles,
            TRAIL_BUCKET,
            start=now - TRAIL_WINDOW_SEC,
            stop=now,
            dom=route.dom,
            fx=route.fx,
            base=route.sym,
        )
        return build_trail(route, candles)
    except InfluxUnavailableError:
        return None
    except Exception as exc:
        logger.warning("랜딩 trail 계산 실패: %r", exc)
        return None


async def _load_events(influx: LandingReader, now: int) -> EventsOut | None:
    start = now - EVENTS_WINDOW_SEC
    try:
        # 진행 중 = end_ts 0 이고 마지막 관측이 600초 안 — 013 복원이 고아 점을 닫는 규칙과 같다
        summary = await asyncio.to_thread(
            influx.query_event_summary,
            start=start,
            stop=now,
            open_since=now - MAX_GAP_SEC,
            top_n=TOP_N,
        )
        return build_events(summary, start=start, stop=now)
    except InfluxUnavailableError:
        return None
    except Exception as exc:
        logger.warning("랜딩 events 계산 실패: %r", exc)
        return None


class _Slot[T]:
    """부분 하나의 캐시 — 결과(None 포함)를 ttl 초 들고, 만료되거나 키(경로)가 바뀌면 다시 읽는다.

    null 도 캐시하는 것은 장애 중에 요청마다 Redis·Influx 를 두드리지 않기 위해서다. 갱신은 키마다 한 번에
    하나 — 조회는 태스크로 돌고, 그동안 온 요청은 새로 시작하지 않고 같은 태스크를 기다린다. 태스크는
    기다리던 요청이 먼저 떠나도 끝까지 돌아, 끝나는 순간 캐시를 채우고 그때부터 ttl 을 센다.
    `wait` 가 있으면(Influx 부분) 그만큼만 기다리고, 넘으면 같은 키의 직전 값(만료됐어도)을, 없으면 None 을 준다.
    """

    def __init__(
        self, ttl: float, mono: Callable[[], float], wait: float | None = None
    ) -> None:
        self._ttl = ttl
        self._mono = mono
        self._wait = wait
        self._filled = False
        self._key: Hashable = None
        self._value: T | None = None
        self._expires = 0.0
        self._pending: dict[Hashable, asyncio.Task[T | None]] = {}

    def _fresh(self, key: Hashable) -> bool:
        return self._filled and self._key == key and self._mono() < self._expires

    async def get(
        self, key: Hashable, load: Callable[[], Awaitable[T | None]]
    ) -> T | None:
        if self._fresh(key):
            return self._value
        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(self._fill(key, load))
            self._pending[key] = task
        # shield — 이 요청이 끊기거나 기다림이 끝나도 조회는 취소되지 않는다
        if self._wait is None:
            return await asyncio.shield(task)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self._wait)
        except TimeoutError:
            # 직전 값은 같은 키의 것만 — 다른 경로의 추이를 이 경로 카드에 싣지 않는다
            return self._value if self._filled and self._key == key else None

    async def _fill(
        self, key: Hashable, load: Callable[[], Awaitable[T | None]]
    ) -> T | None:
        try:
            value = await load()
            self._key = key
            self._value = value
            self._filled = True
            self._expires = self._mono() + self._ttl
            return value
        finally:
            self._pending.pop(key, None)


class LandingService:
    """앱 하나에 하나 — 세 부분의 캐시를 든다. 시계(벽시계·단조)와 Influx 대기 상한은 테스트가 바꿀 수 있게 주입한다."""

    def __init__(
        self,
        *,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        influx_wait_sec: float = INFLUX_WAIT_SEC,
    ) -> None:
        self._wall = wall
        # live 는 Redis 한 번이라 끝까지 기다린다 — 3초 규칙은 Influx 부분(trail·events)에만
        self._live: _Slot[LiveOut] = _Slot(LIVE_TTL_SEC, mono)
        self._trail: _Slot[TrailOut] = _Slot(TRAIL_TTL_SEC, mono, influx_wait_sec)
        self._events: _Slot[EventsOut] = _Slot(EVENTS_TTL_SEC, mono, influx_wait_sec)

    async def summary(
        self, *, bus: TableReader | None, influx: LandingReader | None
    ) -> LandingResponse:
        # events 는 live 와 무관하다 — 함께 기다려 응답 시간이 두 저장소 시간의 합이 되지 않게
        (live, trail), events = await asyncio.gather(
            self._live_and_trail(bus, influx), self._events_part(influx)
        )
        return LandingResponse(
            served_at=int(self._wall() * 1000), live=live, trail=trail, events=events
        )

    async def _live_and_trail(
        self, bus: TableReader | None, influx: LandingReader | None
    ) -> tuple[LiveOut | None, TrailOut | None]:
        live = await self._live.get(None, lambda: _load_live(bus))
        if live is None or not live.top or influx is None:
            return live, None
        route = live.top[0]
        # 경로가 바뀌면 만료 전이라도 새로 읽는다
        key = (route.dom, route.fx, route.sym, route.dir)
        trail = await self._trail.get(
            key, lambda: _load_trail(influx, route, int(self._wall()))
        )
        return live, trail

    async def _events_part(self, influx: LandingReader | None) -> EventsOut | None:
        if influx is None:
            return None  # 토큰 없음 — 읽을 저장소가 없다
        return await self._events.get(
            None, lambda: _load_events(influx, int(self._wall()))
        )
