"""analysis 테스트 공용 도구 — 네트워크 없음, 저장소에 직접 시드 (스펙 004 §4).

표준 시드: 시각은 모두 1700000000000(ms). 호가 5단계,
i단계 ask = 가격×(1+0.0005×i), bid = 가격×(1−0.0005×i),
단계마다 원화 환산 체결 가능 금액 300만원 (USDT 마켓은 가격×1,400 환산).
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.live_store import LiveStore
from app.core.models import Row
from app.main import create_app

FIXED_MS = 1_700_000_000_000
FIXED_SEC = 1_700_000_000
FIXED_DT = datetime.fromtimestamp(FIXED_SEC, tz=UTC)
SEED_RATE = 1400.0  # 표준 시드 환율 — ask = bid = 1,400
LEVEL_KRW = 3_000_000.0  # 단계당 원화 환산 체결 가능 금액


def seed_levels(
    price: float, quote: str
) -> tuple[list[list[float]], list[list[float]]]:
    """표준 시드 호가 5단계 — (asks, bids)."""
    asks: list[list[float]] = []
    bids: list[list[float]] = []
    krw_per_quote = SEED_RATE if quote == "USDT" else 1.0
    for i in range(1, 6):
        ask_price = price * (1 + 0.0005 * i)
        bid_price = price * (1 - 0.0005 * i)
        asks.append([ask_price, LEVEL_KRW / (ask_price * krw_per_quote)])
        bids.append([bid_price, LEVEL_KRW / (bid_price * krw_per_quote)])
    return asks, bids


def make_row(
    exchange: str,
    base: str,
    *,
    quote: str | None = None,
    price: float = 100.0,
    asks: list[list[float]] | None = None,
    bids: list[list[float]] | None = None,
    dep: bool | None = None,
    wd: bool | None = None,
) -> Row:
    if quote is None:
        quote = "USDT" if exchange == "binance" else "KRW"
    native = f"{base}USDT" if exchange == "binance" else f"KRW-{base}"
    return Row(
        exchange=exchange,
        base=base,
        quote=quote,
        native_symbol=native,
        price=price,
        asks=asks if asks is not None else [[101.0, 1.0]],
        bids=bids if bids is not None else [[99.0, 2.0]],
        price_timestamp=FIXED_MS,
        deposit_enabled=dep,
        withdrawal_enabled=wd,
    )


def seeded_row(
    exchange: str,
    base: str,
    price: float,
    *,
    quote: str | None = None,
    dep: bool | None = None,
    wd: bool | None = None,
) -> Row:
    """표준 시드 호가 5단계가 붙은 행."""
    if quote is None:
        quote = "USDT" if exchange == "binance" else "KRW"
    asks, bids = seed_levels(price, quote)
    return make_row(
        exchange, base, quote=quote, price=price, asks=asks, bids=bids, dep=dep, wd=wd
    )


def standard_store(
    *,
    extra: dict[str, list[Row]] | None = None,
    bithumb_dep: bool | None = False,
    bithumb_wd: bool | None = False,
    binance_dep: bool | None = True,
    binance_wd: bool | None = True,
) -> LiveStore:
    """스펙 §4 표준 시드. extra 로 거래소별 행을 추가하고 입출금 상태를 바꿀 수 있다."""
    rows: dict[str, list[Row]] = {
        "upbit": [
            seeded_row("upbit", "BTC", 100_000_000, dep=True, wd=True),
            seeded_row("upbit", "ETH", 5_000_000, dep=True, wd=True),
            seeded_row("upbit", "XRP", 1_400, dep=True, wd=True),
        ],
        "bithumb": [
            seeded_row("bithumb", "BTC", 100_100_000, dep=bithumb_dep, wd=bithumb_wd),
            seeded_row("bithumb", "XRP", 1_402, dep=bithumb_dep, wd=bithumb_wd),
        ],
        "binance": [
            seeded_row("binance", "BTC", 71_000, dep=binance_dep, wd=binance_wd),
            seeded_row("binance", "ETH", 3_550, dep=binance_dep, wd=binance_wd),
            seeded_row("binance", "XRP", 0.99, dep=binance_dep, wd=binance_wd),
            # SOL 은 국내 미상장 — 격자에서 빠지는지 확인용
            seeded_row("binance", "SOL", 150, dep=binance_dep, wd=binance_wd),
        ],
    }
    if extra:
        for exchange, extra_rows in extra.items():
            rows.setdefault(exchange, []).extend(extra_rows)
    store = LiveStore()
    for exchange, exchange_rows in rows.items():
        store.replace_exchange(exchange, exchange_rows, FIXED_DT)
    store.set_rate("upbit", SEED_RATE, SEED_RATE, FIXED_DT)
    store.set_rate("bithumb", SEED_RATE, SEED_RATE, FIXED_DT)
    store.mark_received(FIXED_SEC)
    return store


def make_app(store: LiveStore | None = None) -> FastAPI:
    """lifespan 없이 앱을 만들고 상태를 직접 채운다 — 수집 루프·네트워크가 돌지 않는다."""
    app = create_app()
    app.state.live_store = store if store is not None else LiveStore()
    app.state.settings = SimpleNamespace(refresh_token=None)
    return app


def make_client(store: LiveStore | None = None) -> TestClient:
    return TestClient(make_app(store))
