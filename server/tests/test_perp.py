"""perp 공통 규칙 — 배수 정규화·행 갱신·펀딩 보류·우주 수식·현물 맵과의 분리 (스펙 046 §3.2~3.4, §4)."""

import math
from datetime import UTC, datetime

import pytest

from app.core.live_store import LiveStore
from app.core.perp import PerpSink, perp_universe, split_multiplier
from tests.conftest import make_row

T0 = 1_787_727_947_000
SRC = "binance_perp"


# --- 배수 심볼 정규화 (§3.3) ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1000PEPE", ("PEPE", 1000)),
        ("10000NEX", ("NEX", 10000)),
        ("1000000MOG", ("MOG", 1000000)),
        ("1MBABYDOGE", ("BABYDOGE", 1000000)),
        ("SHIB1000", ("SHIB", 1000)),
        ("kPEPE", ("PEPE", 1000)),
        ("1INCH", ("1INCH", 1)),
        ("0G", ("0G", 1)),
        ("2Z", ("2Z", 1)),
        ("4", ("4", 1)),
        ("42", ("42", 1)),
        ("BTC", ("BTC", 1)),
        ("1000", ("1000", 1)),
        ("1000SATS", ("SATS", 1000)),
        ("1000000BABYDOGE", ("BABYDOGE", 1000000)),
    ],
)
def test_split_multiplier(raw: str, expected: tuple[str, int]) -> None:
    assert split_multiplier(raw) == expected


# --- 행 갱신 (§3.4) ---


def build(universe: set[str] | None = None) -> tuple[LiveStore, PerpSink]:
    store = LiveStore()
    sink = PerpSink(store)
    sink.set_universe(universe if universe is not None else {"PEPE", "BTC"})
    return store, sink


def quote(
    sink: PerpSink,
    *,
    base: str = "PEPE",
    bid: float | None = 0.0123,
    ask: float | None = 0.0124,
    bid_size: float | None = 500.0,
    ask_size: float | None = 700.0,
    ts: int = T0,
    at: int = T0 + 5,
    multiplier: int = 1000,
    source: str = SRC,
) -> None:
    sink.quote(
        source=source,
        base=base,
        native_symbol=f"1000{base}USDT",
        multiplier=multiplier,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        quote_ts=ts,
        received_at_ms=at,
    )


def test_quote_creates_row_with_prices_divided_and_sizes_multiplied() -> None:
    store, sink = build()
    quote(sink)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.bid, row.ask) == (0.0123 / 1000, 0.0124 / 1000)
    assert (row.bid_size, row.ask_size) == (500_000.0, 700_000.0)
    assert (row.native_symbol, row.multiplier, row.quote_ts) == (
        "1000PEPEUSDT",
        1000,
        T0,
    )
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    assert row.mark is None and row.funding_rate is None
    assert row.next_funding_ms is None and row.funding_interval_h is None


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_invalid_quote_field_leaves_prices_but_counts_the_receive(
    bad: float,
) -> None:
    store, sink = build()
    quote(sink)
    for field in ("bid", "ask", "bid_size", "ask_size"):
        quote(sink, ts=T0 + 100, at=T0 + 200, **{field: bad})
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.bid, row.ask) == (0.0123 / 1000, 0.0124 / 1000)
    assert row.quote_ts == T0  # 호가는 그대로
    assert row.updated_at == datetime.fromtimestamp((T0 + 200) / 1000, tz=UTC)


def test_invalid_first_quote_does_not_create_a_row() -> None:
    store, sink = build()
    quote(sink, ask=0.0)
    quote(sink, bid_size=None)
    assert store.perp_rows() == []


def test_partial_fields_are_merged_and_checked_together() -> None:
    store, sink = build()
    quote(sink)
    # 델타 — ask 쪽만 왔다: 온 것만 바뀌고 bid 는 그대로, 넷의 검사는 합친 결과로 통과
    quote(sink, bid=None, bid_size=None, ask=0.0130, ask_size=900.0, ts=T0 + 100)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.bid, row.bid_size) == (0.0123 / 1000, 500_000.0)
    assert (row.ask, row.ask_size) == (0.0130 / 1000, 900_000.0)
    assert row.quote_ts == T0 + 100
    # 델타의 온 필드가 0 이면 합친 결과가 무효 — 호가 불변
    quote(sink, bid=0.0, ask=None, bid_size=None, ask_size=None, ts=T0 + 200)
    assert row.bid == 0.0123 / 1000 and row.quote_ts == T0 + 100


def test_partial_first_message_cannot_create_a_row() -> None:
    store, sink = build()
    quote(sink, bid=None, bid_size=None)
    assert store.perp_rows() == []


def test_older_quote_than_current_is_dropped() -> None:
    store, sink = build()
    quote(sink, ts=T0 + 100)
    quote(sink, bid=0.0200, ask=0.0201, ts=T0 + 50, at=T0 + 999)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert row.bid == 0.0123 / 1000 and row.quote_ts == T0 + 100
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)


def test_funding_before_the_first_quote_is_held_then_loaded() -> None:
    store, sink = build()
    sink.funding(
        source=SRC,
        base="PEPE",
        multiplier=1000,
        received_at_ms=T0,
        funding_rate=0.0001,
        next_funding_ms=T0 + 3_600_000,
        mark=0.0125,
    )
    assert store.perp_rows() == []  # 행이 없으면 만들지 않는다
    sink.funding(
        source=SRC, base="PEPE", multiplier=1000, received_at_ms=T0, mark=0.0126
    )  # 보류끼리는 합친다
    quote(sink)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert row.funding_rate == 0.0001 and row.next_funding_ms == T0 + 3_600_000
    assert row.mark == 0.0126 / 1000


def test_funding_updates_only_what_came_and_bumps_updated_at() -> None:
    store, sink = build()
    quote(sink)
    sink.funding(
        source=SRC,
        base="PEPE",
        multiplier=1000,
        received_at_ms=T0 + 50,
        funding_rate=0.0002,
        mark=0.0125,
    )
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert row.funding_rate == 0.0002 and row.mark == 0.0125 / 1000
    assert row.next_funding_ms is None
    assert row.updated_at == datetime.fromtimestamp((T0 + 50) / 1000, tz=UTC)
    sink.funding(
        source=SRC,
        base="PEPE",
        multiplier=1000,
        received_at_ms=T0 + 60,
        next_funding_ms=T0 + 1,
        funding_interval_h=4,
    )
    assert row.funding_rate == 0.0002 and row.next_funding_ms == T0 + 1
    assert row.funding_interval_h == 4
    assert row.bid == 0.0123 / 1000  # 호가는 건드리지 않는다


def test_interval_from_the_list_lands_on_existing_and_future_rows() -> None:
    store, sink = build()
    quote(sink)
    sink.set_interval(SRC, "PEPE", 8)
    sink.set_interval(SRC, "BTC", 4)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.funding_interval_h == 8
    quote(sink, base="BTC", multiplier=1)
    btc = store.perp_row(SRC, "BTC")
    assert btc is not None and btc.funding_interval_h == 4
    sink.set_interval(SRC, "PEPE", None)
    assert row.funding_interval_h is None


def test_messages_outside_the_universe_are_dropped() -> None:
    store, sink = build({"BTC"})
    quote(sink)
    sink.funding(
        source=SRC, base="PEPE", multiplier=1000, received_at_ms=T0, funding_rate=0.1
    )
    assert store.perp_rows() == []
    sink.set_universe({"PEPE"})
    quote(sink)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.funding_rate is None  # 우주 밖 펀딩은 보류도 안 한다


def test_universe_change_removes_rows_and_held_funding_outside_it() -> None:
    store, sink = build({"PEPE", "BTC", "ETH"})
    quote(sink)
    quote(sink, base="BTC", multiplier=1)
    sink.funding(
        source=SRC, base="ETH", multiplier=1, received_at_ms=T0, funding_rate=0.1
    )
    assert sink.set_universe({"BTC"}) == 1
    assert [r.base for r in store.perp_rows()] == ["BTC"]
    sink.set_universe({"BTC", "ETH"})
    quote(sink, base="ETH", multiplier=1)
    eth = store.perp_row(SRC, "ETH")
    assert eth is not None and eth.funding_rate is None  # 보류도 지워졌다


def test_perp_rows_are_separate_from_spot_rows_and_per_source() -> None:
    store, sink = build()
    now = datetime.now(UTC)
    store.put_rows([make_row("binance", "BTC"), make_row("upbit", "BTC")], now)
    quote(sink, base="BTC", multiplier=1)
    quote(sink, base="BTC", multiplier=1, source="bybit_perp")
    assert store.get("binance_perp", "BTC") is None
    assert len(store.get_all(base="BTC")) == 2
    assert {r.source for r in store.perp_rows()} == {"binance_perp", "bybit_perp"}
    assert [r.source for r in store.perp_rows("bybit_perp")] == ["bybit_perp"]
    assert store.perp_row("bybit_perp", "btc") is not None  # base 조회는 대소문자 무시
    store.remove_perp_row("bybit_perp", "BTC")
    assert store.perp_rows("bybit_perp") == []
    assert not store.is_empty()  # 현물 행 기준 — perp 는 세지 않는다


def test_same_message_updates_in_place_without_a_new_object() -> None:
    store, sink = build()
    quote(sink)
    row = store.perp_row(SRC, "PEPE")
    quote(sink, bid=0.0200, ts=T0 + 1)
    assert store.perp_row(SRC, "PEPE") is row


# --- perp 우주 (§3.2) ---


def test_perp_universe_two_sources_or_kimp_and_one_source() -> None:
    kimp = {"BTC", "ETH", "XRP"}
    per_source = [{"BTC", "PEPE", "MOG"}, {"BTC", "PEPE", "ETH"}, {"SOL"}]
    assert perp_universe(kimp, per_source) == {"BTC", "PEPE", "ETH"}
    assert perp_universe(set(), per_source) == {"BTC", "PEPE"}
    assert perp_universe({"SOL"}, per_source) == {"BTC", "PEPE", "SOL"}
    assert perp_universe(kimp, []) == set()
