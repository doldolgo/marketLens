"""업비트 커넥터 — raw → 스냅샷 변환·청크·캐시·에러 변환 (스펙 001 §3.5, §4)."""

import httpx
import pytest

from app.core.connectors.upbit import UpbitConnector
from app.core.errors import ExchangeApiError, ExchangeTimeoutError


class UpbitFake:
    """업비트 public API 흉내. 요청을 기록해 청크·호출 수를 검증한다."""

    def __init__(self, markets: list[str]) -> None:
        self.markets = markets
        self.requests: list[httpx.Request] = []
        self.orderbooks: dict[str, dict[str, object]] = {}
        self.tickers: dict[str, dict[str, object]] = {}

    def set_orderbook(
        self,
        market: str,
        units: list[dict[str, float]],
        timestamp: int = 1_700_000_000_000,
    ) -> None:
        self.orderbooks[market] = {
            "market": market,
            "timestamp": timestamp,
            "orderbook_units": units,
        }

    def set_ticker(self, market: str, trade_price: float, trade_timestamp: int) -> None:
        self.tickers[market] = {
            "market": market,
            "trade_price": trade_price,
            "trade_timestamp": trade_timestamp,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/market/all":
            return httpx.Response(
                200, json=[{"market": m, "korean_name": m} for m in self.markets]
            )
        wanted = request.url.params["markets"].split(",")
        if path == "/v1/orderbook":
            return httpx.Response(
                200, json=[self.orderbooks[m] for m in wanted if m in self.orderbooks]
            )
        if path == "/v1/ticker":
            return httpx.Response(
                200, json=[self.tickers[m] for m in wanted if m in self.tickers]
            )
        return httpx.Response(404, text="not found")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def paths(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]


UNITS = [
    {"ask_price": 101.0, "bid_price": 99.0, "ask_size": 1.0, "bid_size": 2.0},
    {"ask_price": 102.0, "bid_price": 98.0, "ask_size": 3.0, "bid_size": 4.0},
]


async def test_raw_becomes_snapshot_shape() -> None:
    fake = UpbitFake(["KRW-BTC", "BTC-ETH"])  # KRW- 마켓만 대상
    fake.set_orderbook("KRW-BTC", UNITS)
    fake.set_ticker("KRW-BTC", 100.5, 1_700_000_000_123)
    result = await UpbitConnector().fetch_rows(fake.client())

    assert [r.native_symbol for r in result.rows] == ["KRW-BTC"]
    row = result.rows[0]
    assert (row.exchange, row.base, row.quote) == ("upbit", "BTC", "KRW")
    assert row.price == 100.5
    assert row.asks == [[101.0, 1.0], [102.0, 3.0]]
    assert row.bids == [[99.0, 2.0], [98.0, 4.0]]
    assert row.price_timestamp == 1_700_000_000_123
    assert (
        row.deposit_enabled is None
        and row.withdrawal_enabled is None
        and row.networks == []
    )
    assert result.calls == 3  # market/all 1 + 호가 1 + 티커 1


async def test_101_markets_split_into_2_chunks() -> None:
    markets = [f"KRW-C{i:03d}" for i in range(101)]
    fake = UpbitFake(markets)
    for m in markets:
        fake.set_orderbook(m, UNITS)
        fake.set_ticker(m, 100.0, 1_700_000_000_000)
    result = await UpbitConnector().fetch_rows(fake.client())

    ob_sizes = sorted(
        len(r.url.params["markets"].split(",")) for r in fake.paths("/v1/orderbook")
    )
    tk_sizes = sorted(
        len(r.url.params["markets"].split(",")) for r in fake.paths("/v1/ticker")
    )
    assert ob_sizes == [1, 100]
    assert tk_sizes == [1, 100]
    assert result.calls == 5  # market/all 1 + 호가 2 + 티커 2
    assert len(result.rows) == 101


async def test_market_list_cached_for_10_minutes() -> None:
    fake = UpbitFake(["KRW-BTC"])
    fake.set_orderbook("KRW-BTC", UNITS)
    fake.set_ticker("KRW-BTC", 100.0, 1_700_000_000_000)
    connector = UpbitConnector()
    client = fake.client()
    first = await connector.fetch_rows(client)
    second = await connector.fetch_rows(client)
    assert first.calls == 3
    assert second.calls == 2  # market/all 은 캐시에서
    assert len(fake.paths("/v1/market/all")) == 1


async def test_missing_ticker_falls_back_to_mid_and_orderbook_timestamp() -> None:
    fake = UpbitFake(["KRW-BTC"])
    fake.set_orderbook("KRW-BTC", UNITS, timestamp=1_700_000_000_777)
    result = await UpbitConnector().fetch_rows(fake.client())
    row = result.rows[0]
    assert row.price == 100.0  # (99+101)/2
    assert row.price_timestamp == 1_700_000_000_777


async def test_empty_orderbook_skips_coin() -> None:
    # 체결가도 호가도 없으면 그 코인은 저장되지 않는다
    fake = UpbitFake(["KRW-BTC", "KRW-ETH"])
    fake.set_orderbook("KRW-BTC", UNITS)
    fake.set_ticker("KRW-BTC", 100.0, 1_700_000_000_000)
    fake.set_orderbook("KRW-ETH", [])
    result = await UpbitConnector().fetch_rows(fake.client())
    assert [r.base for r in result.rows] == ["BTC"]


async def test_notional_cap_truncates_levels() -> None:
    fake = UpbitFake(["KRW-BTC"])
    fake.set_orderbook(
        "KRW-BTC",
        [
            {
                "ask_price": 1_000_000_000.0,
                "bid_price": 400_000_000.0,
                "ask_size": 1.5,
                "bid_size": 1.0,
            },
            {
                "ask_price": 1_100_000_000.0,
                "bid_price": 300_000_000.0,
                "ask_size": 1.0,
                "bid_size": 1.0,
            },
        ],
    )
    fake.set_ticker("KRW-BTC", 1_000_000_000.0, 1_700_000_000_000)
    row = (await UpbitConnector().fetch_rows(fake.client())).rows[0]
    assert len(row.asks) == 1  # 1단계에서 이미 10억 도달
    assert len(row.bids) == 2  # 4억+3억 < 10억 → 끝까지


async def test_non_200_raises_exchange_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server broke " + "y" * 600)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ExchangeApiError) as exc_info:
        await UpbitConnector().fetch_rows(client)
    err = exc_info.value
    assert err.status_code == 500
    assert err.body is not None and len(err.body) == 500
    assert err.exchange == "upbit"


async def test_timeout_raises_exchange_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ExchangeTimeoutError):
        await UpbitConnector().fetch_rows(client)


# ── 실패 분류 (스펙 011 §3.2, §4) ─────────────────────────────────────────────


def _status_client(
    status: int, body: str = "", headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, headers=headers or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _raising_client(exc: Exception) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "kind"),
    [(429, "rate_limit"), (418, "banned"), (503, "unavailable"), (400, "bad_request")],
)
async def test_http_status_is_classified_by_upbit_rules(status: int, kind: str) -> None:
    body = '{"error":{"name":' + str(status) + ',"message":"x"}}'
    with pytest.raises(ExchangeApiError) as info:
        await UpbitConnector().fetch_rows(_status_client(status, body))
    exc = info.value
    assert exc.kind == kind
    assert exc.status_code == status
    assert exc.body == body
    assert exc.url == "https://api.upbit.com/v1/market/all"
    assert exc.retry_after_sec is None


async def test_retry_after_seconds_header_is_kept() -> None:
    with pytest.raises(ExchangeApiError) as info:
        await UpbitConnector().fetch_rows(
            _status_client(429, headers={"Retry-After": "3"})
        )
    assert info.value.retry_after_sec == 3


async def test_timeout_is_timeout_kind() -> None:
    with pytest.raises(ExchangeTimeoutError) as info:
        await UpbitConnector().fetch_rows(_raising_client(httpx.ReadTimeout("slow")))
    assert info.value.kind == "timeout"
    assert info.value.status_code is None


async def test_connect_error_is_network_kind() -> None:
    with pytest.raises(ExchangeApiError) as info:
        await UpbitConnector().fetch_rows(
            _raising_client(httpx.ConnectError("refused"))
        )
    assert info.value.kind == "network"


async def test_non_json_is_bad_response_kind() -> None:
    with pytest.raises(ExchangeApiError) as info:
        await UpbitConnector().fetch_rows(_status_client(200, "<html>oops</html>"))
    assert info.value.kind == "bad_response"


async def test_domestic_row_never_carries_depth_fields() -> None:
    # 국내는 asks/bids 가 이미 깊어 깊이 스트림을 쓰지 않는다 (스펙 012 §3.5, §4)
    fake = UpbitFake(["KRW-BTC"])
    fake.set_orderbook("KRW-BTC", UNITS)
    fake.set_ticker("KRW-BTC", 100.5, 1_700_000_000_123)
    row = (await UpbitConnector().fetch_rows(fake.client())).rows[0]
    assert (row.depth_asks, row.depth_bids, row.depth_at) == ([], [], None)
