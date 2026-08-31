"""메모리 저장소 계약 (스펙 001 §3.3)."""

from datetime import UTC, datetime, timedelta

from app.core.live_store import LiveStore
from tests.conftest import make_row

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)


def _filled_store() -> LiveStore:
    store = LiveStore()
    store.replace_exchange(
        "upbit", [make_row("upbit", "BTC"), make_row("upbit", "ETH")], NOW
    )
    store.replace_exchange("binance", [make_row("binance", "BTC")], NOW)
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


def test_received_at_and_emptiness() -> None:
    store = LiveStore()
    assert store.is_empty()
    assert store.received_at is None
    store.mark_received(1_756_000_000)
    assert store.received_at == 1_756_000_000
    store.replace_exchange("upbit", [make_row("upbit", "BTC")], NOW)
    assert not store.is_empty()
    store.replace_exchange("upbit", [], NOW)
    assert store.is_empty()


def test_replace_sets_tz_aware_utc_updated_at() -> None:
    store = LiveStore()
    now = datetime.now(UTC)
    store.replace_exchange("upbit", [make_row("upbit", "BTC")], now)
    row = store.get("upbit", "BTC")
    assert row is not None
    assert row.updated_at is not None
    assert row.updated_at.utcoffset() == timedelta(0)
