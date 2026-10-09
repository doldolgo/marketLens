"""gap 테스트 공용 도구 — 네트워크 없음, 저장소에 직접 시드 (스펙 048 §4)."""

from datetime import UTC, datetime

import fakeredis

from app.core.live_store import LiveStore
from app.core.models import PerpRow, Row
from app.core.redis_bus import RedisBus

T0 = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)


def make_bus() -> tuple[RedisBus, fakeredis.FakeServer]:
    """fakeredis 위의 버스 — 같은 server 로 두 번째 클라이언트를 만들면 구독·게시 양쪽을 흉내낼 수 있다."""
    server = fakeredis.FakeServer()
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server)), server


def spot(
    store: LiveStore,
    exchange: str,
    base: str,
    *,
    ask: list[float] | None = None,
    bid: list[float] | None = None,
    price: float = 100.0,
    now: datetime = T0,
    quote: str = "USDT",
) -> Row:
    """현물 행 1개 시드 — 그 거래소 스트림의 수신 시각도 `now` 로 둔다(런타임에서 행은 스트림 메시지로만 생긴다)."""
    if ask is None:
        ask = [100.0, 1.0]
    if bid is None:
        bid = [99.0, 1.0]
    row = Row(
        exchange=exchange,
        base=base,
        quote=quote,
        native_symbol=f"{base}USDT",
        price=price,
        asks=[ask],
        bids=[bid],
        price_timestamp=int(now.timestamp() * 1000),
    )
    store.put_row(row, now)
    store.stream(exchange).last_message_at = int(now.timestamp() * 1000)
    return row


def perp(
    store: LiveStore,
    source: str,
    base: str,
    *,
    bid: float = 100.5,
    ask: float = 100.6,
    bid_size: float = 1.0,
    ask_size: float = 1.0,
    funding_rate: float | None = 0.0001,
    next_funding_ms: int | None = 1_791_475_200_000,
    funding_interval_h: int | None = 8,
    now: datetime = T0,
) -> PerpRow:
    """perp 행 1개 시드 — 원천 스트림의 수신 시각도 `now`. 가격·잔량은 1코인 단위(046 이 이미 나눈 값)."""
    row = PerpRow(
        source=source,
        base=base,
        native_symbol=f"{base}USDT",
        multiplier=1,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        quote_ts=int(now.timestamp() * 1000),
        funding_rate=funding_rate,
        next_funding_ms=next_funding_ms,
        funding_interval_h=funding_interval_h,
        updated_at=now,
    )
    store.put_perp_row(row)
    store.stream(source).last_message_at = int(now.timestamp() * 1000)
    return row


def gap_table(rows: list[dict]) -> dict:
    return {"rows": rows, "warnings": [], "dataReceivedAt": 1, "fetchedAt": 2}


def gap_row(sym: str, **over: object) -> dict:
    base = {
        "sym": sym,
        "spot": "binance",
        "perp": "bybit_perp",
        "spotPrice": 100.0,
        "entry": 0.5,
        "exit": -0.1,
        "funding": 0.01,
        "intervalH": 8,
        "nextFundingTs": 1_791_475_200,
        "status": "ok",
        "age": 0.5,
    }
    return {**base, **over}
