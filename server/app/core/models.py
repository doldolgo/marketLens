"""메모리 스냅샷의 행·환율 모델 — 스펙 001 §3.3 계약을 그대로 옮긴 자료구조.

후속 스펙(003 spreads 등)이 이 모양을 읽는다.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.core.networks import Network


@dataclass
class Row:
    """스냅샷 1행 = (exchange, base) 당 1개."""

    exchange: str  # upbit·bithumb·binance
    base: str  # 코인 (예 BTC)
    quote: str  # 국내 KRW, 해외 USDT
    native_symbol: str  # 거래소 원본 심볼 (예 KRW-BTC, BTCUSDT)
    price: float  # 마지막 체결가. 없으면 (bid+ask)/2
    asks: list[
        list[float]
    ]  # [price, size] 오름차순, 누적액 상한까지 (바이낸스는 1단계)
    bids: list[list[float]]  # [price, size] 내림차순, 같은 규칙
    price_timestamp: int  # 거래소 시세 시각 epoch ms (바이낸스는 수집 시각)
    deposit_enabled: bool | None = None  # 3-state, None=모름 — 006 이 채운다
    withdrawal_enabled: bool | None = None  # 3-state — 006 이 채운다
    networks: list[Network] = field(default_factory=list)  # 빈 리스트 = 망 정보 없음
    updated_at: datetime | None = None  # 적재 시각(tz-aware UTC). 저장소가 채운다


@dataclass
class Rate:
    """환율 — 국내 거래소 id 당 1개. 바이낸스 환율은 없다."""

    exchange: str
    ask: float  # USDT 살 때 (최우선 매도호가)
    bid: float  # USDT 팔 때 (최우선 매수호가)
    updated_at: datetime
