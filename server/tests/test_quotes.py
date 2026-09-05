"""메시지 → 행 갱신 규칙 (스펙 001 §3.4·§3.5, §4) — 거래소와 무관한 공통 규칙."""

import math

import pytest

from app.core.live_store import LiveStore
from app.core.quotes import QuoteSink
from app.core.rows import NOTIONAL_CAP_KRW

T0 = 1_700_000_000_000


def _sink(universe: set[str] | None = None) -> tuple[LiveStore, QuoteSink]:
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe(universe if universe is not None else {"BTC", "ETH"})
    return store, sink


def _book(
    sink: QuoteSink,
    base: str = "BTC",
    *,
    exchange: str = "upbit",
    asks: list[list[float]] | None = None,
    bids: list[list[float]] | None = None,
    at: int = T0,
    ts: int = T0 - 5,
) -> None:
    sink.orderbook(
        exchange=exchange,
        base=base,
        quote="KRW",
        native_symbol=f"KRW-{base}",
        asks=asks if asks is not None else [[101.0, 1.0], [102.0, 2.0]],
        bids=bids if bids is not None else [[99.0, 1.0], [98.0, 2.0]],
        timestamp_ms=ts,
        received_at_ms=at,
    )


def test_orderbook_replaces_levels_and_updated_at_is_receive_time() -> None:
    store, sink = _sink()
    _book(sink)
    _book(sink, asks=[[105.0, 1.0]], bids=[[95.0, 1.0]], at=T0 + 1000)
    row = store.get("upbit", "BTC")
    assert row is not None
    assert row.asks == [[105.0, 1.0]] and row.bids == [[95.0, 1.0]]
    assert row.updated_at is not None
    assert int(row.updated_at.timestamp() * 1000) == T0 + 1000
    assert row.quote == "KRW" and row.native_symbol == "KRW-BTC"


def test_all_thirty_levels_are_kept_below_cap() -> None:
    store, sink = _sink()
    asks = [[100.0 + i, 1.0] for i in range(30)]
    bids = [[99.0 - i, 1.0] for i in range(30)]
    _book(sink, asks=asks, bids=bids)
    row = store.get("upbit", "BTC")
    assert row is not None and len(row.asks) == 30 and len(row.bids) == 30


def test_trade_updates_price_only_and_is_held_until_orderbook() -> None:
    store, sink = _sink()
    # 호가 전에 온 체결가는 보류됐다가 호가와 함께 실린다
    sink.trade(exchange="upbit", base="BTC", price=100.5, price_timestamp=T0 - 1)
    assert store.get("upbit", "BTC") is None
    _book(sink)
    row = store.get("upbit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (100.5, T0 - 1)
    # 행이 있으면 price·price_timestamp 만 바뀌고 호가는 그대로
    sink.trade(exchange="upbit", base="btc", price=101.0, price_timestamp=T0 + 7)
    row = store.get("upbit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (101.0, T0 + 7)
    assert row.asks == [[101.0, 1.0], [102.0, 2.0]]


def test_price_falls_back_to_mid_and_orderbook_timestamp_without_trade() -> None:
    store, sink = _sink()
    _book(sink, ts=T0 - 5)
    row = store.get("upbit", "BTC")
    assert row is not None and row.price == 100.0 and row.price_timestamp == T0 - 5
    sink.trade(exchange="upbit", base="BTC", price=0.0, price_timestamp=T0)
    assert store.get("upbit", "BTC").price == 100.0  # type: ignore[union-attr]


def test_zero_size_levels_are_dropped_so_best_has_size() -> None:
    store, sink = _sink()
    _book(
        sink,
        exchange="bithumb",
        asks=[[101.0, 0.0], [102.0, 3.0]],
        bids=[[99.0, 0.0], [98.0, 4.0], [97.0, -1.0]],
    )
    row = store.get("bithumb", "BTC")
    assert row is not None
    assert row.asks == [[102.0, 3.0]] and row.bids == [[98.0, 4.0]]


def test_levels_are_truncated_at_cumulative_cap() -> None:
    store, sink = _sink()
    half = NOTIONAL_CAP_KRW / 2
    asks = [[100.0, half / 100], [101.0, half / 101], [102.0, 1.0]]
    _book(sink, asks=asks, bids=[[99.0, 1.0]])
    row = store.get("upbit", "BTC")
    assert row is not None and len(row.asks) == 2  # 2단계에서 누적 상한 도달
    assert math.isinf(math.inf)


def test_empty_side_removes_existing_row() -> None:
    store, sink = _sink()
    _book(sink)
    _book(sink, asks=[], bids=[[99.0, 1.0]])
    assert store.get("upbit", "BTC") is None


def test_rows_outside_universe_are_dropped_and_removed_on_shrink() -> None:
    store, sink = _sink({"BTC"})
    _book(sink, "ETH")
    assert store.get("upbit", "ETH") is None
    _book(sink, "BTC")
    assert store.get("upbit", "BTC") is not None
    assert (
        sink.set_universe({"ETH"}) == 1
    )  # 우주에서 빠진 BTC 행이 그 자리에서 사라진다
    assert store.get("upbit", "BTC") is None
    assert sink.universe == {"ETH"}


def test_usdt_orderbook_updates_rate_and_never_stores_a_row() -> None:
    store, sink = _sink()
    _book(sink, "USDT", asks=[[1401.0, 10.0]], bids=[[1399.0, 10.0]])
    rate = store.get_rate("upbit")
    assert rate is not None and (rate.ask, rate.bid) == (1401.0, 1399.0)
    assert int(rate.updated_at.timestamp() * 1000) == T0
    assert store.get("upbit", "USDT") is None
    # 한쪽이 비면(잔량 0 만 있어도) 직전 시세 유지
    _book(sink, "USDT", asks=[[1402.0, 0.0]], bids=[[1400.0, 10.0]], at=T0 + 1)
    rate = store.get_rate("upbit")
    assert rate is not None and (rate.ask, rate.bid) == (1401.0, 1399.0)
    _book(sink, "USDT", asks=[[1405.0, 1.0]], bids=[[1403.0, 1.0]], at=T0 + 2)
    assert store.get_rate("upbit").ask == 1405.0  # type: ignore[union-attr]


def test_wallet_fields_survive_row_replacement() -> None:
    store, sink = _sink()
    _book(sink)
    row = store.get("upbit", "BTC")
    assert row is not None
    row.deposit_enabled, row.withdrawal_enabled = True, True
    _book(sink, at=T0 + 1)
    row = store.get("upbit", "BTC")
    assert row is not None and row.deposit_enabled is True and row.withdrawal_enabled


@pytest.mark.parametrize("base", ["SOL", "sol"])
def test_trade_outside_universe_is_not_held(base: str) -> None:
    store, sink = _sink({"BTC"})
    sink.trade(exchange="upbit", base=base, price=1.0, price_timestamp=T0)
    sink.set_universe({"BTC", "SOL"})
    _book(sink, "SOL")
    assert store.get("upbit", "SOL").price == 100.0  # type: ignore[union-attr]
