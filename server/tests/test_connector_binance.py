"""바이낸스 커넥터 — 문자열 가격·USDT 접미사·0 가격 처리 (스펙 001 §3.5, §4).

깊이 캐시 주입(012 §3.4~3.5)도 여기서 본다 — Row 의 depth_* 를 채우는 주체가 커넥터다.
"""

import time

import httpx
import pytest

from app.core.connectors.binance import BinanceConnector
from app.core.connectors.binance_depth import DepthCache, now_ms
from app.core.errors import ExchangeApiError


def make_client(
    price_list: list[dict[str, str]], book_list: list[dict[str, str]]
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/ticker/price":
            return httpx.Response(200, json=price_list)
        if request.url.path == "/api/v3/ticker/bookTicker":
            return httpx.Response(200, json=book_list)
        return httpx.Response(404, text="not found")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_string_prices_become_floats_one_level_only() -> None:
    client = make_client(
        [{"symbol": "BTCUSDT", "price": "100.50000000"}],
        [
            {
                "symbol": "BTCUSDT",
                "bidPrice": "99.0",
                "bidQty": "2.0",
                "askPrice": "101.0",
                "askQty": "3.0",
            }
        ],
    )
    result = await BinanceConnector().fetch_rows(client)
    row = result.rows[0]
    assert (row.exchange, row.base, row.quote, row.native_symbol) == (
        "binance",
        "BTC",
        "USDT",
        "BTCUSDT",
    )
    assert row.price == 100.5
    assert row.asks == [[101.0, 3.0]]
    assert row.bids == [[99.0, 2.0]]
    assert result.calls == 2


async def test_non_usdt_suffix_and_zero_price_symbols_are_dropped() -> None:
    client = make_client(
        [
            {"symbol": "BTCUSDT", "price": "100.0"},
            {"symbol": "ETHBTC", "price": "5.0"},  # USDT 로 끝나지 않음
            {"symbol": "DEADUSDT", "price": "0.00000000"},  # 거래 없음
        ],
        [
            {
                "symbol": "BTCUSDT",
                "bidPrice": "99.0",
                "bidQty": "2.0",
                "askPrice": "101.0",
                "askQty": "3.0",
            },
            {
                "symbol": "ETHBTC",
                "bidPrice": "4.9",
                "bidQty": "1.0",
                "askPrice": "5.1",
                "askQty": "1.0",
            },
            {
                "symbol": "DEADUSDT",
                "bidPrice": "0.0",
                "bidQty": "0.0",
                "askPrice": "0.0",
                "askQty": "0.0",
            },
        ],
    )
    result = await BinanceConnector().fetch_rows(client)
    assert [r.base for r in result.rows] == ["BTC"]


async def test_missing_last_price_falls_back_to_mid() -> None:
    client = make_client(
        [],
        [
            {
                "symbol": "NEWUSDT",
                "bidPrice": "10.0",
                "bidQty": "1.0",
                "askPrice": "12.0",
                "askQty": "1.0",
            }
        ],
    )
    row = (await BinanceConnector().fetch_rows(client)).rows[0]
    assert row.price == 11.0


async def test_price_timestamp_is_collection_time_ms() -> None:
    client = make_client(
        [{"symbol": "BTCUSDT", "price": "100.0"}],
        [
            {
                "symbol": "BTCUSDT",
                "bidPrice": "99.0",
                "bidQty": "2.0",
                "askPrice": "101.0",
                "askQty": "3.0",
            }
        ],
    )
    before = int(time.time() * 1000)
    row = (await BinanceConnector().fetch_rows(client)).rows[0]
    after = int(time.time() * 1000)
    assert before <= row.price_timestamp <= after


async def test_zero_bid_or_ask_skips_symbol() -> None:
    client = make_client(
        [{"symbol": "ONEUSDT", "price": "1.0"}, {"symbol": "TWOUSDT", "price": "2.0"}],
        [
            {
                "symbol": "ONEUSDT",
                "bidPrice": "0.0",
                "bidQty": "0.0",
                "askPrice": "1.1",
                "askQty": "1.0",
            },
            {
                "symbol": "TWOUSDT",
                "bidPrice": "1.9",
                "bidQty": "1.0",
                "askPrice": "2.1",
                "askQty": "1.0",
            },
        ],
    )
    result = await BinanceConnector().fetch_rows(client)
    assert [r.base for r in result.rows] == ["TWO"]


# ── 실패 분류 + Retry-After (스펙 011 §3.2, §4) ────────────────────────────────


def _status_client(
    status: int, headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, text='{"code":-1003,"msg":"x"}', headers=headers or {}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_429_with_retry_after_is_rate_limit() -> None:
    with pytest.raises(ExchangeApiError) as info:
        await BinanceConnector().fetch_rows(_status_client(429, {"Retry-After": "10"}))
    assert info.value.kind == "rate_limit"
    assert info.value.retry_after_sec == 10
    assert info.value.status_code == 429


async def test_403_is_banned() -> None:
    with pytest.raises(ExchangeApiError) as info:
        await BinanceConnector().fetch_rows(_status_client(403))
    assert info.value.kind == "banned"
    assert info.value.retry_after_sec is None


async def test_418_is_banned_and_5xx_unavailable() -> None:
    with pytest.raises(ExchangeApiError) as info:
        await BinanceConnector().fetch_rows(_status_client(418, {"Retry-After": "120"}))
    assert (info.value.kind, info.value.retry_after_sec) == ("banned", 120)
    with pytest.raises(ExchangeApiError) as info:
        await BinanceConnector().fetch_rows(_status_client(500))
    assert info.value.kind == "unavailable"


# ── 깊이 캐시 주입 (스펙 012 §3.4~3.5, §4) ─────────────────────────────────────

_BTC_PRICE = [{"symbol": "BTCUSDT", "price": "100.0"}]
_BTC_BOOK = [
    {
        "symbol": "BTCUSDT",
        "bidPrice": "99.0",
        "bidQty": "2.0",
        "askPrice": "101.0",
        "askQty": "3.0",
    }
]


def btc_client() -> httpx.AsyncClient:
    return make_client(_BTC_PRICE, _BTC_BOOK)


async def test_fresh_cache_entry_fills_depth_fields() -> None:
    cache = DepthCache()
    at = now_ms()
    asks = [[101.0, 1.0], [102.0, 2.0]]
    bids = [[99.0, 1.0], [98.0, 2.0]]
    cache.put("BTCUSDT", asks, bids, at)

    row = (await BinanceConnector(depth=cache).fetch_rows(btc_client())).rows[0]
    assert row.depth_asks == asks
    assert row.depth_bids == bids
    assert row.depth_at == at


async def test_entry_older_than_ttl_is_treated_as_absent() -> None:
    cache = DepthCache()
    cache.put("BTCUSDT", [[101.0, 1.0]], [[99.0, 1.0]], now_ms() - 10_001)

    row = (await BinanceConnector(depth=cache).fetch_rows(btc_client())).rows[0]
    assert (row.depth_asks, row.depth_bids, row.depth_at) == ([], [], None)


async def test_symbol_missing_from_cache_keeps_one_level_rest_book() -> None:
    cache = DepthCache()
    cache.put("ETHUSDT", [[3550.0, 1.0]], [[3540.0, 1.0]], now_ms())

    row = (await BinanceConnector(depth=cache).fetch_rows(btc_client())).rows[0]
    assert (row.depth_asks, row.depth_bids, row.depth_at) == ([], [], None)
    assert row.asks == [[101.0, 3.0]] and row.bids == [[99.0, 2.0]]


async def test_depth_is_truncated_at_one_million_usdt() -> None:
    # 한 단계 300,000 USDT → 4단계째에 누적 1,200,000 으로 상한에 도달하고 잘린다
    levels = [[1000.0, 300.0] for _ in range(6)]
    cache = DepthCache()
    cache.put("BTCUSDT", levels, levels, now_ms())

    row = (await BinanceConnector(depth=cache).fetch_rows(btc_client())).rows[0]
    assert len(row.depth_asks) == 4
    assert len(row.depth_bids) == 4


async def test_first_level_over_the_cap_is_still_kept() -> None:
    cache = DepthCache()
    over = [[1000.0, 5000.0], [1001.0, 1.0]]  # 첫 단계가 이미 5,000,000 USDT
    cache.put("BTCUSDT", over, over, now_ms())

    row = (await BinanceConnector(depth=cache).fetch_rows(btc_client())).rows[0]
    assert row.depth_asks == [[1000.0, 5000.0]]
    assert row.depth_bids == [[1000.0, 5000.0]]


async def test_rest_calls_per_cycle_stay_two_with_the_stream_on() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v3/ticker/price":
            return httpx.Response(200, json=_BTC_PRICE)
        return httpx.Response(200, json=_BTC_BOOK)

    cache = DepthCache()
    cache.put("BTCUSDT", [[101.0, 1.0]], [[99.0, 1.0]], now_ms())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await BinanceConnector(depth=cache).fetch_rows(client)

    assert sorted(paths) == ["/api/v3/ticker/bookTicker", "/api/v3/ticker/price"]
    assert result.calls == 2
