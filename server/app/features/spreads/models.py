"""GET /spreads·POST /refresh 응답 모델 — 스펙 003 §3.2·§3.3.

BE 내부 필드명은 snake_case 로 두고 HTTP 직렬화 경계에서 camelCase 로 변환한다.
"""

from typing import Literal

from pydantic import BaseModel


class SpreadRow(BaseModel):
    """(국내 거래소 × 해외 거래소 × 코인) 페어 1행. 키 순서는 스펙 §3.2 예시와 같다.

    선언 순서가 곧 아카이브 스키마다 — 010 의 S3 줄이 이 순서를 그대로 쓴다.
    """

    sym: str
    dom: str
    fx: str
    fwd: float  # 슬리피지 차감 후 순값 (원값은 fwd + slip_fwd)
    rev: float
    usd: float
    spark: list[float]
    status: Literal["ok", "stale", "fail"]
    age: float
    slip_fwd: float  # 그 방향에서 차감된 폭(%p, 양수)
    slip_rev: float
    krw: float  # 이 행 국내 거래소의 최우선 매수호가(KRW)
    net_dom: str | None
    dep_dom: bool | None
    wd_dom: bool | None
    dep_fx: bool | None
    wd_fx: bool | None


class SpreadsResponse(BaseModel):
    rate: float
    notional: float  # 이 응답의 슬리피지가 계산된 체결 규모(USD) — 010 이 줄마다 싣는다
    rows: list[SpreadRow]
    warnings: list[str]  # USDT 시세 미갱신 경고 — 없으면 빈 배열 (스펙 008)
    data_received_at: int | None  # 저장소 마지막 수신 시각 epoch ms, 스냅샷 없으면 null
    fetched_at: int  # 응답 시각 epoch ms


class RefreshSnapshot(BaseModel):
    """거래소당 1항목."""

    exchange: str
    saved: int  # 이번 실행에서 저장된 행 수 (실패 거래소는 0)
    calls: int  # 이번 실행에서 나간 HTTP 호출 수 (실패 거래소는 0)
    # 입출금 조회 성공 여부 — 바이낸스 항목에도 붙는다 (006 §2)
    wallet_status_available: bool


class RefreshRate(BaseModel):
    """이번 실행에 관측된 국내 거래소의 KRW-USDT 환율."""

    exchange: str
    ask: float
    bid: float


class RefreshFailure(BaseModel):
    exchange: str
    error_code: str
    message: str


class RefreshResponse(BaseModel):
    """001 수집 서비스 1회 실행 결과 — 003 의 키 나열 + 001 요약의 호출 수·소요 ms."""

    snapshots: list[RefreshSnapshot]
    usdkrw: list[RefreshRate]
    total_saved: int
    failures: list[RefreshFailure]
    warnings: list[str]
    duration_ms: float
    fetched_at: int  # 실행 시작 시각 epoch ms
