"""메모리 저장소 계약 (스펙 001 §3.3) — 행 단위 쓰기·물려받기·스트림 상태·틱 슬롯."""

from datetime import UTC, datetime, timedelta

from app.core.live_store import LiveStore
from app.core.models import Tick, TickRow
from app.core.networks import Network
from tests.conftest import make_row

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)


def _filled_store() -> LiveStore:
    store = LiveStore()
    store.put_rows([make_row("upbit", "BTC"), make_row("upbit", "ETH")], NOW)
    store.put_rows([make_row("binance", "BTC")], NOW)
    store.set_rate("upbit", 1385.0, 1384.0, NOW)
    return store


def test_get_all_and_filters() -> None:
    store = _filled_store()
    assert len(store.get_all()) == 3
    assert {r.base for r in store.get_all(exchange="upbit")} == {"BTC", "ETH"}
    assert [r.exchange for r in store.get_all(base="btc")] == ["upbit", "binance"]
    assert store.get_all(exchange="upbit", base="eth")[0].base == "ETH"


def test_get_single_is_case_insensitive_and_none_when_missing() -> None:
    store = _filled_store()
    assert store.get("upbit", "btc") is not None
    assert store.get("upbit", "XRP") is None
    assert store.get("bithumb", "BTC") is None


def test_rates_copy_and_per_exchange() -> None:
    store = _filled_store()
    rate = store.get_rate("upbit")
    assert rate is not None and (rate.ask, rate.bid) == (1385.0, 1384.0)
    assert store.get_rate("bithumb") is None
    rates = store.rates()
    rates.clear()  # 사본이므로 원본에 영향 없음
    assert store.get_rate("upbit") is not None


def test_put_row_replaces_only_that_row_and_sets_utc_updated_at() -> None:
    store = _filled_store()
    later = NOW + timedelta(seconds=1)
    store.put_row(make_row("upbit", "BTC", asks=[[102.0, 1.0]]), later)
    btc = store.get("upbit", "BTC")
    eth = store.get("upbit", "ETH")
    assert btc is not None and btc.asks == [[102.0, 1.0]] and btc.updated_at == later
    assert eth is not None and eth.updated_at == NOW  # 다른 행은 그대로
    assert btc.updated_at.utcoffset() == timedelta(0)


def test_put_row_inherits_wallet_fields_from_previous_row() -> None:
    store = LiveStore()
    first = make_row("upbit", "BTC")
    first.deposit_enabled, first.withdrawal_enabled = True, False
    first.networks = [Network(code="BTC", name="Bitcoin", dep=True, wd=False)]
    store.put_row(first, NOW)
    store.put_row(make_row("upbit", "BTC", asks=[[103.0, 1.0]]), NOW)
    row = store.get("upbit", "BTC")
    assert row is not None
    assert (row.deposit_enabled, row.withdrawal_enabled) == (True, False)
    assert [n.code for n in row.networks] == ["BTC"]


def test_remove_and_retain_bases() -> None:
    store = _filled_store()
    store.remove_row("upbit", "eth")
    assert store.get("upbit", "ETH") is None
    store.put_rows([make_row("bithumb", "XRP")], NOW)
    assert store.retain_bases({"btc"}) == 1  # bithumb XRP 만 지워진다
    assert {(r.exchange, r.base) for r in store.get_all()} == {
        ("upbit", "BTC"),
        ("binance", "BTC"),
    }


def test_received_at_and_emptiness() -> None:
    store = LiveStore()
    assert store.is_empty()
    assert store.received_at is None
    store.mark_received(1_756_000_000)
    assert store.received_at == 1_756_000_000
    store.put_rows([make_row("upbit", "BTC")], NOW)
    assert not store.is_empty()
    store.remove_row("upbit", "BTC")
    assert store.is_empty()


def test_stream_state_is_created_on_demand_and_read_without_creating() -> None:
    store = LiveStore()
    assert store.stream_state("upbit") is None
    state = store.stream("upbit")
    assert state.connected is False and state.last_message_at is None
    assert state.last_error is None and state.subscribed == 0
    state.last_message_at = 1_700_000_000_000
    assert store.stream_state("upbit") is state
    assert set(store.streams()) == {"upbit"}


def test_tick_slot_returns_previous_tick() -> None:
    store = LiveStore()
    assert store.tick is None
    t1 = Tick(ts=1, rows=(), dw_failed=())
    t2 = Tick(ts=2, rows=(TickRow("upbit", "binance", "BTC", 1.0, -1.0),), dw_failed=())
    assert store.push_tick(t1) is None
    assert store.push_tick(t2) is t1
    assert store.tick is t2


def test_spark_map_is_published_and_read_per_combination() -> None:
    store = LiveStore()
    assert store.spark("upbit", "binance", "BTC") == []
    store.set_spark({("upbit", "binance", "BTC"): [1.0, 2.0]})
    assert store.spark("upbit", "binance", "btc") == [1.0, 2.0]
