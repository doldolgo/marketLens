"""메모리 저장소의 자료구조 — 스펙 001 §3.3 계약을 그대로 옮긴 모양.

후속 스펙(003 spreads·009 tick-store·011 health·012 binance-stream)이 이 모양을 읽는다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple

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


class TickRow(NamedTuple):
    """틱 1행 — 자격을 통과한 (국내, 해외, 코인) 조합의 김프 원값 (009 §3.2).

    NamedTuple 이다(001 §3.6, 2026-09-28) — 매초 1,500행 가까이 만들어지므로 필드를 하나씩 넣는 frozen
    dataclass 보다 생성이 약 4배 빠르고 행마다 속성 사전이 없다. 불변·속성 이름·순서·기본값은 그대로다.
    """

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
    # 입출금 4개는 006 §3.7 로 판정한 값(024 §3.3 — spreads 행과 같은 함수), 3상태 그대로(None = 모름)
    dom_dep: bool | None = None  # 국내 입금
    dom_wd: bool | None = None  # 국내 출금
    fx_dep: bool | None = None  # 해외 입금
    fx_wd: bool | None = None  # 해외 출금
    # 024 §3.3 — 판정에 쓴 망 표시명(국내·해외). 모르면 None. Redis 레코드·`premium` 점에는 넣지 않는다
    net_dom: str | None = None
    net_fx: str | None = None


@dataclass(frozen=True)
class Tick:
    """매초 하나 — LiveStore 슬롯 → 009 인계로 흐르는 저장 단위 (§3.6)."""

    ts: int  # epoch 초
    rows: tuple[TickRow, ...]
    dw_failed: tuple[str, ...]  # 그 초에 입출금 조회가 실패 상태인 거래소 id


@dataclass
class PerpRow:
    """perp 행 = (source, base) 당 1개 — 스펙 046 §3.1. 메시지마다 제자리에서 필드만 바뀐다(새 객체 없음, §3.4).

    가격은 원본 ÷ multiplier, 잔량은 원본 × multiplier — 1코인 단위라 현물 행과 바로 비교된다.
    첫 유효 호가(넷 다 > 0·유한)에서 만들어지고, 그 전에 온 펀딩은 PerpSink 가 보류한다.
    """

    source: str  # binance_perp·bybit_perp·bitget_perp(·hyperliquid_perp)
    base: str  # 배수 접두·접미를 뗀 코인 이름 (1000PEPE → PEPE)
    native_symbol: str  # 거래소 원본 심볼 (1000PEPEUSDT)
    multiplier: int  # 1·1000·10000·1000000
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    quote_ts: int  # 호가의 거래소 시각 epoch ms
    mark: float | None = None  # 마크가 ÷ multiplier
    funding_rate: float | None = None  # 한 주기의 비율(소수 — 0.0001 이 0.01%)
    next_funding_ms: int | None = None  # 다음 정산 epoch ms
    funding_interval_h: int | None = None  # 정산 주기(정수 시간 1·2·4·8)
    updated_at: datetime | None = (
        None  # 어느 메시지든 마지막으로 반영된 시각, tz-aware UTC
    )
