"""pp 테스트 공용 도구 — 네트워크 없음, 저장소에 직접 시드 (스펙 049 §4)."""

from datetime import UTC, datetime

import fakeredis

from app.core.live_store import LiveStore
from app.core.models import PerpRow
from app.core.redis_bus import RedisBus

T0 = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)

ROW_KEYS = [
    "sym",
    "a",
    "b",
    "longA",
    "longB",
    "fundA",
    "intA",
    "nextA",
    "fundB",
    "intB",
    "nextB",
    "status",
    "age",
]


def make_bus() -> tuple[RedisBus, fakeredis.FakeServer]:
    """fakeredis 위의 버스 — 같은 server 로 두 번째 클라이언트를 만들면 구독·게시 양쪽을 흉내낼 수 있다."""
    server = fakeredis.FakeServer()
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server)), server


def perp(
    store: LiveStore,
    source: str,
    base: str,
    *,
    bid: float = 100.0,
    ask: float = 100.1,
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


def pp_table(rows: list[dict]) -> dict:
    return {"rows": rows, "warnings": [], "dataReceivedAt": 1, "fetchedAt": 2}


def pp_row(sym: str, **over: object) -> dict:
    base = {
        "sym": sym,
        "a": "binance_perp",
        "b": "bybit_perp",
        "longA": -0.02,
        "longB": -0.03,
        "fundA": 0.01,
        "intA": 8,
        "nextA": 1_791_475_200,
        "fundB": 0.005,
        "intB": 4,
        "nextB": 1_791_464_400,
        "status": "ok",
        "age": 0.5,
    }
    return {**base, **over}
