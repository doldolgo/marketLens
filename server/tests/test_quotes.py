"""메시지 → 행 갱신 규칙 (스펙 001 §3.4·§3.5, §4) — 거래소와 무관한 공통 규칙."""

import dataclasses
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.quotes import QuoteSink
from app.core.rows import NOTIONAL_CAP_KRW, NOTIONAL_CAP_USDT, clean_levels

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


def test_zero_nan_and_infinite_price_levels_are_dropped() -> None:
    """§3.5-1: 가격 > 0 이고 유한, 잔량 > 0 이고 유한한 단계만 남는다 — 최우선도 그 단계가 된다."""
    store, sink = _sink()
    nan, inf = math.nan, math.inf
    _book(
        sink,
        exchange="binance",
        asks=[[0.0, 5.0], [nan, 1.0], [inf, 1.0], [101.0, nan], [102.0, 3.0]],
        bids=[[nan, 2.0], [-1.0, 1.0], [99.0, inf], [98.0, 4.0], [0.0, 9.0]],
    )
    row = store.get("binance", "BTC")
    assert row is not None
    assert row.asks == [[102.0, 3.0]] and row.bids == [[98.0, 4.0]]
    # 걸러낸 뒤 한쪽이 비면 행을 저장하지 않는다(있던 행은 지운다)
    _book(sink, exchange="binance", asks=[[0.0, 1.0], [nan, 1.0]], bids=[[98.0, 4.0]])
    assert store.get("binance", "BTC") is None


def test_usdt_rate_skips_zero_and_nan_top_levels() -> None:
    """KRW-USDT 시세도 같은 거르기를 거친 최우선 — 가격 0·NaN 단계는 시세가 되지 않는다 (§3.4)."""
    store, sink = _sink()
    _book(
        sink,
        "USDT",
        asks=[[math.nan, 1.0], [1401.0, 10.0]],
        bids=[[0.0, 1.0], [1399.0, 10.0]],
    )
    rate = store.get_rate("upbit")
    assert rate is not None and (rate.ask, rate.bid) == (1401.0, 1399.0)


def test_levels_are_truncated_at_cumulative_cap() -> None:
    store, sink = _sink()
    half = NOTIONAL_CAP_KRW / 2
    asks = [[100.0, half / 100], [101.0, half / 101], [102.0, 1.0]]
    _book(sink, asks=asks, bids=[[99.0, 1.0]])
    row = store.get("upbit", "BTC")
    assert row is not None and len(row.asks) == 2  # 2단계에서 누적 상한 도달
    assert clean_levels(asks, math.inf) == asks  # 상한이 inf 면 전부 남는다


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


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 0.0, -1.0])
def test_zero_nan_and_infinite_trade_prices_leave_the_row_price_alone(
    bad: float,
) -> None:
    """체결가도 호가 정리와 같은 기준 — 0 이하·NaN·inf 체결가는 행 가격도 보류값도 바꾸지 않는다 (§3.5-2)."""
    store, sink = _sink()
    # 행이 없을 때 — 보류하지 않으므로 호가가 오면 mid 다
    sink.trade(exchange="upbit", base="BTC", price=bad, price_timestamp=T0 - 3)
    _book(sink)
    row = store.get("upbit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (100.0, T0 - 5)
    # 행이 있을 때 — 직전 체결가가 그대로 남고, 다음 호가도 그 값을 싣는다
    sink.trade(exchange="upbit", base="BTC", price=100.5, price_timestamp=T0 + 1)
    sink.trade(exchange="upbit", base="BTC", price=bad, price_timestamp=T0 + 2)
    row = store.get("upbit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (100.5, T0 + 1)
    _book(sink, at=T0 + 3)
    row = store.get("upbit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (100.5, T0 + 1)


# --- 호가 경로 순서·수신 시각 메모·위치 인자 Row (2026-10-09) 가 기존 구현과 같은지 ---


def test_row_field_order_matches_the_positional_construction_in_orderbook() -> None:
    """QuoteSink.orderbook 은 Row 를 위치 인자로 만든다 — 앞 8필드 순서가 바뀌면 값이 엇갈려 들어간다."""
    assert [f.name for f in dataclasses.fields(Row)][:8] == [
        "exchange",
        "base",
        "quote",
        "native_symbol",
        "price",
        "asks",
        "bids",
        "price_timestamp",
    ]
    store, sink = _sink()
    sink.trade(exchange="upbit", base="BTC", price=100.5, price_timestamp=T0 - 9)
    _book(sink, at=T0 + 3, ts=T0 - 7)
    row = store.get("upbit", "BTC")
    assert row is not None
    assert (row.exchange, row.base, row.quote, row.native_symbol) == (
        "upbit",
        "BTC",
        "KRW",
        "KRW-BTC",
    )
    assert (row.price, row.price_timestamp) == (100.5, T0 - 9)
    assert row.asks == [[101.0, 1.0], [102.0, 2.0]]
    assert row.bids == [[99.0, 1.0], [98.0, 2.0]]


def test_updated_at_is_the_receive_time_for_same_and_alternating_ms() -> None:
    """같은 ms 의 호가는 같은 수신 시각(tz-aware UTC)이고, ms 가 오가도(A·B·A) 한 칸 메모가 옛 값을 내지 않는다."""
    store, sink = _sink()
    expected = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)  # T0 = 1_700_000_000_000
    _book(sink, "BTC", at=T0)
    _book(sink, "ETH", at=T0)
    _book(sink, "USDT", asks=[[1401.0, 1.0]], bids=[[1399.0, 1.0]], at=T0)
    btc, eth = store.get("upbit", "BTC"), store.get("upbit", "ETH")
    rate = store.get_rate("upbit")
    assert btc is not None and eth is not None and rate is not None
    for got in (btc.updated_at, eth.updated_at, rate.updated_at):
        assert got == expected and got is not None and got.utcoffset() == timedelta(0)
    for at in (T0 + 1, T0 + 2, T0 + 1, T0 + 1, T0, T0 + 1500):
        _book(sink, "BTC", at=at)
        row = store.get("upbit", "BTC")
        assert row is not None and row.updated_at == expected + timedelta(
            milliseconds=at - T0
        )
    _book(sink, "ETH", at=T0 + 250.0)  # type: ignore[arg-type]  # float ms 도 같은 시각
    eth = store.get("upbit", "ETH")
    assert eth is not None and eth.updated_at == expected + timedelta(milliseconds=250)


def test_malformed_levels_in_the_universe_still_raise() -> None:
    """우주 안 호가는 기존처럼 정리에서 예외가 난다 — 커넥터가 디코드 실패로 센다."""
    _, sink = _sink()
    with pytest.raises(ValueError):
        _book(sink, asks=[[101.0, 1.0, 3.0]])
    with pytest.raises(ValueError):
        _book(sink, "USDT", bids=[[1399.0]])


def _clean_before(levels: list[list[float]], cap: float) -> list[list[float]]:
    """2026-10-09 이전 clean_levels 를 그대로 옮긴 대조용."""
    out: list[list[float]] = []
    cum = 0.0
    for level in levels:
        price, size = level
        if not (0.0 < price < math.inf and 0.0 < size < math.inf):
            continue
        out.append(level)
        cum += price * size
        if cum >= cap:
            break
    return out


class _SinkBefore(QuoteSink):
    """2026-10-09 이전 QuoteSink.orderbook 을 그대로 옮긴 대조용 — 정리 → 수신 시각 → USDT → 우주 순, 키워드 Row."""

    def orderbook(
        self,
        *,
        exchange: str,
        base: str,
        quote: str,
        native_symbol: str,
        asks: list[list[float]],
        bids: list[list[float]],
        timestamp_ms: int,
        received_at_ms: int,
    ) -> None:
        key = base.upper()
        cap = NOTIONAL_CAP_KRW if quote == "KRW" else NOTIONAL_CAP_USDT
        asks = _clean_before(asks, cap)
        bids = _clean_before(bids, cap)
        now = datetime.fromtimestamp(received_at_ms / 1000, tz=UTC)
        if key == "USDT" and quote == "KRW":
            if asks and bids and asks[0][0] > 0 and bids[0][0] > 0:
                self._store.set_rate(exchange, asks[0][0], bids[0][0], now)
            return
        if key not in self._universe:
            return
        if not asks or not bids:
            self._store.remove_row(exchange, key)
            return
        trade = self._trades.get((exchange, key))
        if trade is not None and trade[0] > 0:
            price, price_ts = trade
        else:
            price, price_ts = (bids[0][0] + asks[0][0]) / 2, timestamp_ms
        self._store.put_row(
            Row(
                exchange=exchange,
                base=key,
                quote=quote,
                native_symbol=native_symbol,
                price=price,
                asks=asks,
                bids=bids,
                price_timestamp=price_ts,
            ),
            now,
        )


def _row_sig(row: Row | None, nets: dict[int, str]) -> tuple[Any, ...] | None:
    """행 전 필드 — 단계는 받은 [p, s] 객체 id(두 싱크에 같은 목록을 넣는다), 망 목록은 심은 객체 이름."""
    if row is None:
        return None
    return (
        row.exchange,
        row.base,
        row.quote,
        row.native_symbol,
        row.price,
        type(row.price),
        row.price_timestamp,
        [id(lv) for lv in row.asks],
        [id(lv) for lv in row.bids],
        row.deposit_enabled,
        row.withdrawal_enabled,
        nets.get(id(row.networks), "새 목록"),
        row.updated_at,
        None if row.updated_at is None else row.updated_at.utcoffset(),
    )


def _rate_sig(store: LiveStore, exchange: str) -> tuple[Any, ...] | None:
    rate = store.get_rate(exchange)
    return None if rate is None else (rate.ask, rate.bid, rate.updated_at)


def test_orderbook_matches_the_previous_implementation_on_random_streams() -> None:
    """무작위 호가·체결·우주 변경 2만 건에서 행 전 필드(물려받은 입출금·망 목록 객체 포함)·USDT 시세가 기존 구현과 같다."""
    rng = random.Random(20261009)
    nan, inf = math.nan, math.inf
    venues = [
        ("upbit", "KRW"),
        ("bithumb", "KRW"),
        ("binance", "USDT"),
        ("bybit", "USDT"),
    ]
    bases = ["BTC", "ETH", "XRP", "SOL", "DOGE", "btc", "USDT", "usdt"]
    universe = {"BTC", "ETH", "XRP", "SOL"}

    stores = (LiveStore(), LiveStore())
    sinks = (_SinkBefore(stores[0]), QuoteSink(stores[1]))
    # 입출금 3필드 물려받기를 덮으려고 행을 먼저 심는다 — 단계 [p, s] 객체는 두 저장소가 같은 것을 갖고,
    # 망 목록은 저장소마다 새 객체라 이름으로 비교한다. 행이 지워져도 id 가 재사용되지 않게 목록을 붙잡아 둔다
    nets: dict[int, str] = {}
    held: list[list[Any]] = []
    seeds = [
        (ex, quote, b, (True, False, None)[(i + j) % 3], [2.0, 1.0], [1.0, 1.0])
        for i, (ex, quote) in enumerate(venues)
        for j, b in enumerate(sorted(universe))
    ]
    for store in stores:
        for ex, quote, b, dep, ask, bid in seeds:
            net_list: list[Any] = []
            nets[id(net_list)] = f"{ex}:{b}"
            held.append(net_list)
            row = Row(ex, b, quote, b, 1.0, [ask], [bid], 0, dep, dep, net_list)
            store.put_row(row, datetime(2026, 10, 9, tzinfo=UTC))
    for sink in sinks:
        sink.set_universe(universe)

    def side(n: int, price0: float, step: float) -> list[list[float]]:
        out = []
        for k in range(n):
            p, s = price0 + k * step, rng.uniform(0.01, 5_000.0)
            if rng.random() < 0.05:
                p = rng.choice([0.0, -1.0, nan, inf])
            if rng.random() < 0.05:
                s = rng.choice([0.0, -2.0, nan, inf])
            out.append([float(p), float(s)])
        return out

    at = T0
    for step in range(20_000):
        r = rng.random()
        ex, quote = rng.choice(venues)
        base = rng.choice(bases)
        if r < 0.005:
            new_universe = set(rng.sample(["BTC", "ETH", "XRP", "SOL", "DOGE"], 3))
            removed = [sink.set_universe(new_universe) for sink in sinks]
            assert removed[0] == removed[1]
            continue
        if r < 0.2:
            price = rng.choice([rng.uniform(1, 1e5), 0.0, -1.0, nan, inf])
            for sink in sinks:
                sink.trade(exchange=ex, base=base, price=price, price_timestamp=at - 3)
        else:
            # 같은 ms 반복(소켓 한 번 읽기)·전진·역행 — 한 칸 메모가 어느 쪽에서도 같은 시각을 내는지
            jump = rng.choice([0, 0, 0, rng.randint(1, 900), -rng.randint(1, 50)])
            at += jump
            price0 = 1_400.0 if base.upper() == "USDT" else rng.uniform(1.0, 1e8)
            tick = price0 * 1e-4
            asks = side(rng.choice([0, 1, 15, 30, 200]), price0, tick)
            bids = side(rng.choice([0, 1, 15, 30, 200]), price0 - tick, -tick)
            for sink in sinks:
                sink.orderbook(
                    exchange=ex,
                    base=base,
                    quote=quote,
                    native_symbol=f"{base}{quote}",
                    asks=asks,
                    bids=bids,
                    timestamp_ms=at - 7,
                    received_at_ms=at,
                )
        key = base.upper()
        before = (_row_sig(stores[0].get(ex, key), nets), _rate_sig(stores[0], ex))
        after = (_row_sig(stores[1].get(ex, key), nets), _rate_sig(stores[1], ex))
        assert before == after, step
    # 끝 상태 — 전 행(호출마다 본 것 밖의 행도)
    ends = [sorted(str(_row_sig(x, nets)) for x in store.get_all()) for store in stores]
    assert ends[0] == ends[1]
    for ex, _ in venues:
        assert _rate_sig(stores[0], ex) == _rate_sig(stores[1], ex)
