"""계산 결과 공개 동작 중 HTTP 로 시드할 수 없는 것 — 제외 코인 목록 (스펙 003 §3.2-3)."""

from datetime import UTC, datetime

import pytest

from app.core.live_store import LiveStore
from app.core.premium import premium_percent
from app.features.spreads.service import build_spreads
from app.features.spreads.tests.helpers import make_row

NOW = datetime.now(UTC)


def seed_two_coins() -> LiveStore:
    store = LiveStore()
    store.replace_exchange(
        "upbit", [make_row("upbit", "BTC"), make_row("upbit", "XRP")], NOW
    )
    store.replace_exchange(
        "binance", [make_row("binance", "BTC"), make_row("binance", "XRP")], NOW
    )
    store.set_rate("upbit", 1400.0, 1390.0, NOW)
    return store


def test_excluded_coin_has_no_row() -> None:
    result = build_spreads(seed_two_coins(), excluded={"XRP"})
    assert [r.sym for r in result.rows] == ["BTC"]


def test_excluded_coin_is_case_insensitive() -> None:
    result = build_spreads(seed_two_coins(), excluded={"xrp"})
    assert [r.sym for r in result.rows] == ["BTC"]


def test_default_excluded_list_is_empty() -> None:
    result = build_spreads(seed_two_coins())
    assert [r.sym for r in result.rows] == ["BTC", "XRP"]


def test_premium_percent_is_sell_over_buy() -> None:
    assert premium_percent(buy_krw=100.0, sell_krw=103.0) == pytest.approx(3.0)
    assert premium_percent(buy_krw=200.0, sell_krw=190.0) == pytest.approx(-5.0)
