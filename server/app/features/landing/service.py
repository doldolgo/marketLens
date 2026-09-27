"""랜딩 요약 — `GET /landing` 의 세 부분(live·trail·events)을 만든다 (스펙 022 §3.2).

계산(`build_live`·`build_trail`·`build_events`)은 순수 함수다. `LandingService` 는 부분마다 프로세스 메모리
캐시(live 5초·trail·events 60초)를 들고, 비었거나 만료된 부분에 요청이 몰려도 저장소는 한 번만 읽는다.
저장소는 core 의 Redis(`spreads:latest` 읽기만 — `spreads:want` 는 쓰지 않는다)와 Influx(봉·사건 조회)
클라이언트를 인자로 받는다. 다른 기능은 import 하지 않는다.
"""

import json
from typing import Any, Protocol

from app.core.candles import TIER_BY_RES
from app.core.influx import CandleRow, PremiumEventRow
from app.features.landing.models import (
    EventOut,
    EventsOut,
    LiveOut,
    RouteOut,
    TrailOut,
)

LIVE_TTL_SEC = 5.0
TRAIL_TTL_SEC = 60.0
EVENTS_TTL_SEC = 60.0
TRAIL_WINDOW_SEC = 3_600
EVENTS_WINDOW_SEC = 604_800  # 7일
TOP_N = 5
OVER_PCT = 1.0  # 013 사건 진입 기준과 같은 값
TRAIL_MIN_POINTS = 2  # 선 하나를 그리려면 두 점이 있어야 한다
TRAIL_BUCKET = TIER_BY_RES["1m"].bucket  # candles_1m (014)

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
    """Influx 읽기 두 가지 — core.influx.InfluxClient 시그니처의 일부."""

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

    def query_premium_events(
        self,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        dir: str | None = None,
        base: str | None = None,
    ) -> list[PremiumEventRow]: ...


def build_live(text: str) -> LiveOut | None:
    """`spreads:latest` 본문 → live. JSON 이 아니거나 `rows` 가 배열이 아니면 None."""
    try:
        table = json.loads(text)
    except ValueError:
        return None
    if not isinstance(table, dict) or not isinstance(table.get("rows"), list):
        return None
    rows: list[dict[str, Any]] = table["rows"]
    best: dict[str, RouteOut] = {}
    over1 = over1_movable = 0
    for row in rows:
        if row["status"] != "ok":
            continue  # stale·fail 행의 값은 지금 값이 아니다
        for direction, value_key, slip_key, out_key, in_key in _DIRECTIONS:
            value = row[value_key]
            # null(모름)은 열림이 아니다 — true 일 때만 옮길 수 있다
            movable = row[out_key] is True and row[in_key] is True
            if value >= OVER_PCT:
                over1 += 1
                if movable:
                    over1_movable += 1
            if not movable:
                continue
            current = best.get(row["sym"])
            # 코인당 하나 — 값이 같으면 표에서 먼저 나온 것을 둔다
            if current is None or value > current.pct:
                best[row["sym"]] = RouteOut(
                    sym=row["sym"],
                    dom=row["dom"],
                    fx=row["fx"],
                    dir=direction,
                    pct=value,
                    slip=row[slip_key],
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


def build_events(rows: list[PremiumEventRow], *, start: int, stop: int) -> EventsOut:
    """7일 사건 → 방향별·진행 중 수와 코인당 1개(`max_percent` 가 가장 큰 사건) 상위 5개."""
    best: dict[str, PremiumEventRow] = {}
    for row in rows:
        current = best.get(row.base)
        # 같은 코인에서 최고값이 같으면 나중에 시작한 사건을 둔다
        if current is None or (row.max_percent, row.start_ts) > (
            current.max_percent,
            current.start_ts,
        ):
            best[row.base] = row
    top = sorted(best.values(), key=lambda r: (-r.max_percent, r.base))[:TOP_N]
    return EventsOut(
        start=start,
        stop=stop,
        count=len(rows),
        kimp=sum(r.dir == "kimp" for r in rows),
        reverse=sum(r.dir == "reverse" for r in rows),
        open=sum(r.end_ts == 0 for r in rows),
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
            for r in top
        ],
    )
