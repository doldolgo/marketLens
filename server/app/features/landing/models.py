"""`GET /landing` 응답 모델 — 스펙 022 §3.2.

내부 필드는 snake_case, HTTP 경계(라우터)에서 camelCase 로 바꾼다. 필드 순서는 스펙 예시와 같다.
세 부분(`live`·`trail`·`events`)은 각자 실패하면 그 부분만 None(null) 이다.
"""

from typing import Literal

from pydantic import BaseModel

Direction = Literal["kimp", "reverse"]


class RouteOut(BaseModel):
    """옮길 수 있는 경로 하나 — 표의 행 1개 × 방향 1개."""

    sym: str
    dom: str
    fx: str
    dir: Direction
    pct: float  # 그 방향의 슬리피지 차감 후 순값 %
    slip: float  # 그 방향의 차감폭 %p (최우선 호가 기준 원값 = pct + slip)
    krw: float  # 국내 최우선 매수호가(원) — 행 그대로
    usd: float  # 해외 마지막 체결가 — 행 그대로
    net_dom: str | None
    net_fx: str | None


class DepthGapOut(BaseModel):
    """호가 깊이 예시 — 맨 위 호가 기준 원값과 $1,000 순값이 가장 크게 벌어진 옮길 수 있는 경로 하나."""

    sym: str
    dom: str
    fx: str
    dir: Direction
    raw: float  # 원값 = pct + slip
    pct: float  # 순값
    slip: float  # 차감폭 %p


class LiveOut(BaseModel):
    data_received_at: int | None  # 표의 값 그대로(epoch ms)
    rate: float  # 업비트 USDT 매도호가(원)
    coins: int
    pairs: int
    over1: int
    over1_movable: int
    depth_gap: DepthGapOut | None  # 후보가 없으면 None
    top: list[RouteOut]


class TrailOut(BaseModel):
    """`live.top[0]` 경로의 최근 1시간 — 1분 봉 종가(슬리피지 차감 전 원값)."""

    sym: str
    dom: str
    fx: str
    dir: Direction
    points: list[tuple[int, float]]  # [창 시작 epoch 초, 값] ts 오름차순


class EventOut(BaseModel):
    sym: str
    dom: str
    fx: str
    dir: Direction
    max_percent: float
    start_ts: int
    end_ts: int  # 끝난 시각 — top 은 닫힌 사건만 고른다
    duration_seconds: int
    last_ts: int


class EventsOut(BaseModel):
    start: int
    stop: int
    count: int
    kimp: int
    reverse: int
    open: int
    top: list[EventOut]


class LandingResponse(BaseModel):
    served_at: int  # 이 응답을 만든 시각(epoch ms) — 캐시와 무관
    live: LiveOut | None
    trail: TrailOut | None
    events: EventsOut | None
