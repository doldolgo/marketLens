"""분석 API 6개의 내부 응답 모델 — 스펙 004 §3.2.

Python 필드는 snake_case로 두고 라우터의 HTTP 직렬화 경계에서 camelCase로 변환한다.
"""

from typing import Literal

from pydantic import BaseModel


class OrderbookLevel(BaseModel):
    price: float
    size: float


class OrderbookResponse(BaseModel):
    exchange: str
    symbol: str  # 정규화된 BASE/QUOTE
    base: str
    quote: str
    bids: list[OrderbookLevel]  # 내림차순, depth 단계까지
    asks: list[OrderbookLevel]  # 오름차순, depth 단계까지
    # 001 메모리 계약에는 "호가 시각" 필드가 없어 시세 시각(price_timestamp)을 싣는다 — §7 보고 참고
    timestamp: int  # epoch ms
    data_updated_at: int | None  # 스냅샷 적재 시각 epoch ms
    data_received_at: int | None  # 수집 루프 마지막 교체 시각 epoch ms (§3.0 공통 꼬리)
    fetched_at: int  # 응답 생성 시각 epoch ms


class SlippageResponse(BaseModel):
    exchange: str
    name: str  # 거래소 표시명
    symbol: str
    quote_currency: str
    side: Literal["buy", "sell"]
    requested_amount: float | None  # 안 준 쪽은 null
    requested_quantity: float | None
    best_price: float  # 체결되는 쪽 최우선가
    average_price: float
    quantity: float  # 실제 체결량
    amount: float  # 실제 체결액 (quote 통화)
    slippage_percent: float
    levels_consumed: int
    depth_exhausted: bool
    depth_available: int  # 걷는 목록의 단계 수 (§3.2)
    data_updated_at: int | None
    data_received_at: int | None
    fetched_at: int
    warnings: list[str]


class ArbitrageCandidate(BaseModel):
    exchange: str
    name: str
    best_bid_krw: float
    best_ask_krw: float
    depth_levels: int


class ArbitrageFailure(BaseModel):
    exchange: str
    reason: str


class ArbitrageLeg(BaseModel):
    exchange: str
    name: str
    average_price_krw: float
    amount_krw: float  # 실제 지불/수취 금액 (되맞춘 후)
    slippage_percent: float  # 환산 호가 최우선가 대비
    levels_consumed: int
    depth_exhausted: bool
    data_updated_at: int | None


class ArbitrageResponse(BaseModel):
    sym: str
    input_amount_krw: float
    quantity: float  # 실제 판 수량
    usd_krw_rate: float  # 기준 환율(upbit ask), 표시용
    candidates: list[ArbitrageCandidate]  # 싼 순(best_ask)
    failures: list[ArbitrageFailure]
    buy: ArbitrageLeg
    sell: ArbitrageLeg
    profit_krw: float
    profit_percent: float
    premium_percent: float  # 환산 최우선가 기준
    premium_capture_percent: float
    withdrawal_available: bool | None  # 매수처 출금 상태 — null 은 "모름"
    deposit_available: bool | None  # 매도처 입금 상태
    warnings: list[str]
    data_received_at: int | None
    fetched_at: int


class PremiumDirection(BaseModel):
    usd: float  # 이 방향에서 체결되는 해외 호가 (fwd=ask, rev=bid)
    usd_krw_rate: float  # fwd 는 rate ask, rev 는 rate bid
    rate_updated_at: int | None
    premium_percent: float
    premium_krw: float  # 원화 차액 (판 값 − 산 값)
    profitable: bool  # premium_percent > 0
    data_updated_at: int | None


class PremiumResponse(BaseModel):
    sym: str
    dom: str
    dom_price: float
    fx: str
    fwd: PremiumDirection
    rev: PremiumDirection
    best_direction: Literal["fwd", "rev"]  # 둘 다 손해면 덜 나쁜 쪽
    best_premium_percent: float
    data_received_at: int | None
    fetched_at: int


class ScanItem(BaseModel):
    sym: str
    direction: Literal["fwd", "rev"]
    dom: str
    dom_price: float
    fx: str
    fx_name: str
    usd: float
    premium_percent: float
    premium_krw: float
    liquidity_krw: float  # 양쪽 1단계 체결 가능 금액 중 작은 쪽 (원화)
    suspicious: bool  # |premium_percent| ≥ 5%
    suspicion_reason: str | None


class ScanResponse(BaseModel):
    dom: str
    fx: str
    usd_krw_rate: float  # 표시용 ask
    rate_updated_at: int | None
    scanned_coins: int  # 검사한 국내 코인 수
    scanned_pairs: int  # 국내×해외 짝 수
    excluded_bases: list[str]
    best_fwd: ScanItem | None
    best_rev: ScanItem | None
    top_fwd: list[ScanItem]  # 수익률 내림차순 상위 limit 개
    top_rev: list[ScanItem]
    suspicious_count: int  # 양방향 합
    warnings: list[str]
    data_received_at: int | None
    fetched_at: int


class MatrixDirection(BaseModel):
    buy_exchange: str
    sell_exchange: str
    premium_percent: float  # 1단계 표면 김프 (금액 무관) — 최대 조합 선정 기준
    total_slippage_percent: float  # 표면 김프 − 실효 수익률
    withdrawal_available: bool | None  # 매수처 출금
    deposit_available: bool | None  # 매도처 입금
    depth_exhausted: bool


class MatrixCoin(BaseModel):
    sym: str
    fwd: MatrixDirection | None
    rev: MatrixDirection | None
    suspicious: bool  # fwd ≥ 5%


class MatrixResponse(BaseModel):
    amount_krw: float
    scanned_coins: int  # 행 수
    scanned_combinations: int  # 걸어본 조합 수
    dom_list: list[str]
    fx_list: list[str]
    coins: list[MatrixCoin]  # fwd 김프 내림차순, fwd 없는 행은 맨 뒤
    warnings: list[str]
    data_received_at: int | None
    fetched_at: int
