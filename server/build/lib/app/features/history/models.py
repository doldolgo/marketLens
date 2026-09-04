"""GET /history/* 내부 응답 모델 — 스펙 005 §3.4.

Python 필드는 snake_case로 두고 라우터의 HTTP 직렬화 경계에서 camelCase로 변환한다.
내부 `*_ts` 는 epoch 초, `fetched_at` 은 epoch ms.
"""

from typing import Literal

from pydantic import BaseModel


class PremiumEvent(BaseModel):
    """컴팩트 사건 1개 — 절대시각 대신 직전 기록으로부터의 경과 초(구간 첫 기록은 0)."""

    dt: int
    fwd: float
    rev: float


class PremiumSummary(BaseModel):
    """구간 전체 통계."""

    first_fwd: float
    last_fwd: float
    min_fwd: float
    max_fwd: float


class PremiumHistoryResponse(BaseModel):
    dom: str
    fx: str
    base: str
    unit: Literal["week", "month"]
    start: str  # 구간 경계 ISO 8601 UTC
    end: str  # end exclusive
    first_ts: int  # 구간 첫 기록 시각 epoch 초
    count: int
    summary: PremiumSummary
    events: list[PremiumEvent]
    fetched_at: int


class Segment(BaseModel):
    """streak 구간 1개 — start/end 는 KST 표기."""

    start_ts: int
    end_ts: int
    start: str
    end: str
    duration_seconds: int  # end − start (기록 1개면 0)
    samples: int
    max_percent: float
    avg_percent: float


class DirectionSummary(BaseModel):
    """방향(kimp/reverse) 요약 — avg_percent 는 샘플 수 가중."""

    count: int
    max_duration_seconds: int
    avg_duration_seconds: float
    max_percent: float
    avg_percent: float
    segments: list[Segment]


class Overall(BaseModel):
    """기준치 무관 전체 행 기준 + 두 방향 구간 합집합의 지속 통계."""

    max_kimp_percent: float
    avg_kimp_percent: float
    max_reverse_percent: float
    avg_reverse_percent: float
    max_duration_seconds: int
    avg_duration_seconds: float
    segment_count: int


class StreaksResponse(BaseModel):
    """최상위 14키 — 스펙 005 §3.4-7."""

    base: str
    dom: str
    fx: str
    threshold_percent: float
    max_gap_seconds: int
    start_ts: int
    end_ts: int
    kimp: DirectionSummary
    reverse: DirectionSummary
    overall: Overall
    scanned: int
    last_updated_ts: int
    last_updated: str  # KST — +09:00 으로 끝난다
    fetched_at: int


class BulkCoin(BaseModel):
    base: str
    scanned: int
    last_ts: int
    kimp: DirectionSummary
    reverse: DirectionSummary
    overall: Overall


class BulkResponse(BaseModel):
    dom: str
    fx: str
    threshold_percent: float
    max_gap_seconds: int
    start_ts: int
    end_ts: int
    coin_count: int
    coins: list[BulkCoin]
    fetched_at: int
