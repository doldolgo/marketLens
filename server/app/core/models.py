"""메모리 저장소의 자료구조 — 스펙 001 §3.3 계약을 그대로 옮긴 모양.

후속 스펙(003 spreads·009 tick-store·011 health·012 binance-stream)이 이 모양을 읽는다.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.core.networks import Network


@dataclass
class Row:
    """스냅샷 1행 = (exchange, base) 당 1개. 메시지 단위로 통째 교체된다 (§3.5)."""

    exchange: str  # upbit·bithumb·binance
    base: str  # 코인 (예 BTC)
    quote: str  # 국내 KRW, 해외 USDT
    native_symbol: str  # 거래소 원본 심볼 (예 KRW-BTC, BTCUSDT)
    price: float  # 마지막 체결가. 없으면 (bid+ask)/2
    asks: list[list[float]]  # [price, size] 오름차순, 잔량>0 단계만, 누적액 상한까지
    bids: list[list[float]]  # [price, size] 내림차순, 같은 규칙
    price_timestamp: (
        int  # 거래소 체결 시각 epoch ms. 체결가가 없으면 호가 메시지의 거래소 시각
    )
    deposit_enabled: bool | None = (
        None  # 3-state, None=모름 — 006 이 채우고 교체 시 물려받는다
    )
    withdrawal_enabled: bool | None = None  # 3-state — 006
    networks: list[Network] = field(default_factory=list)  # 빈 리스트 = 망 정보 없음
    updated_at: datetime | None = None  # 이 행의 마지막 갱신(수신) 시각, tz-aware UTC


@dataclass
class Rate:
    """USDT 시세 — 국내 거래소 id 당 1개. 바이낸스 시세는 없다 (§3.4)."""

    exchange: str
    ask: float  # USDT 살 때 (KRW-USDT 최우선 매도호가)
    bid: float  # USDT 팔 때 (최우선 매수호가)
    updated_at: datetime


@dataclass(frozen=True)
class StreamError:
    """스트림 실패 1건 — 연결 실패의 분류(§3.8) 또는 정체. 011 추적기가 그대로 기록한다."""

    kind: str  # 실패 종류 8종 (core.errors.FAIL_KINDS)
    message: str
    status_code: int | None  # 핸드셰이크 HTTP 상태. 없으면 None
    url: str | None  # WebSocket URL
    retry_after_sec: int | None = (
        None  # 핸드셰이크 응답의 Retry-After(초 정수). 없으면 None
    )
    body: str | None = (
        None  # 핸드셰이크 거부 응답 본문 앞 500자 — 011 이력의 message 가 된다
    )


@dataclass
class StreamState:
    """거래소별 스트림 상태 — 011·003 이 읽는다 (§3.3). 커넥터가 갱신한다.

    `url`·`connected_since` 는 판정(§3.8)이 쓰는 값이다 — 정체의 `url` 과,
    연결 뒤 첫 시세가 오기 전의 무수신 기준 시각.
    """

    connected: bool = False
    last_message_at: int | None = None  # 마지막 **시세** 메시지 수신 epoch ms
    last_error: StreamError | None = None
    subscribed: int = 0  # 구독 심볼 수
    url: str | None = None
    connected_since: int | None = (
        None  # 이번 연결의 구독 시각 epoch ms. 미연결이면 None
    )


@dataclass(frozen=True)
class TickRow:
    """틱 1행 — 자격을 통과한 (국내, 해외, 코인) 조합의 김프 원값 (009 §3.2)."""

    dom: str
    fx: str
    base: str
    fwd: float
    rev: float
    # 014 §3.2 — 1분 집계기가 읽는 값. 틱을 만드는 순간의 메모리 행에서 온다.
    # Redis 레코드·`premium` 점에는 넣지 않는다(009 모양 불변) — 기본값은 Redis 에서 되읽은 틱과
    # 옛 테스트가 다섯 값만으로 행을 만들 수 있게 둔 것이고, 집계기는 build_tick 이 채운 행만 본다.
    dom_price: float = 0.0  # 국내 `price`
    fx_price: float = 0.0  # 해외 `price`
    rate: float = 0.0  # 그 국내 거래소 USDT 중간값 (ask+bid)/2, 원
    dom_dep: bool | None = None  # 국내 입금 — 3상태 그대로(None = 모름)
    dom_wd: bool | None = None  # 국내 출금
    fx_dep: bool | None = None  # 해외 입금
    fx_wd: bool | None = None  # 해외 출금


@dataclass(frozen=True)
class Tick:
    """매초 하나 — LiveStore 슬롯 → 009 인계로 흐르는 저장 단위 (§3.6)."""

    ts: int  # epoch 초
    rows: tuple[TickRow, ...]
    dw_failed: tuple[str, ...]  # 그 초에 입출금 조회가 실패 상태인 거래소 id
