"""빗썸 커넥터 — 유령 호가 제거·trade_timestamp 보정 (스펙 001 §3.5, §4)."""

import time

import httpx

from app.core.connectors.bithumb import BithumbConnector

_KST_OFFSET_MS = 32_400_000


class BithumbFake:
    """빗썸 public API 흉내 — 경로·형태는 업비트 v1 과 같다."""

    def __init__(self, markets: list[str]) -> None:
        self.markets = markets
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


async def test_ghost_levels_with_zero_size_are_dropped() -> None:
    fake = BithumbFake(["KRW-BTC"])
    fake.set_orderbook(
        "KRW-BTC",
        [
            {
                "ask_price": 100.0,
                "bid_price": 99.0,
                "ask_size": 0.0,
                "bid_size": 1.0,
            },  # ask 유령
            {
                "ask_price": 101.0,
                "bid_price": 98.0,
                "ask_size": 2.0,
                "bid_size": 0.0,
            },  # bid 유령
            {"ask_price": 102.0, "bid_price": 97.0, "ask_size": 3.0, "bid_size": 4.0},
        ],
    )
    fake.set_ticker("KRW-BTC", 100.0, 1_700_000_000_000)
    row = (await BithumbConnector().fetch_rows(fake.client())).rows[0]
    # 최우선 호가도 잔량>0 인 첫 단계가 된다
    assert row.asks == [[101.0, 2.0], [102.0, 3.0]]
    assert row.bids == [[99.0, 1.0], [97.0, 4.0]]


async def test_kst_future_trade_timestamp_is_shifted_back_9h() -> None:
    fake = BithumbFake(["KRW-BTC"])
    fake.set_orderbook(
        "KRW-BTC",
        [{"ask_price": 101.0, "bid_price": 99.0, "ask_size": 1.0, "bid_size": 1.0}],
    )
    buggy_ts = (
        int(time.time() * 1000) + _KST_OFFSET_MS
    )  # KST 벽시계를 epoch 처럼 찍은 값
    fake.set_ticker("KRW-BTC", 100.0, buggy_ts)
    row = (await BithumbConnector().fetch_rows(fake.client())).rows[0]
    assert row.price_timestamp == buggy_ts - _KST_OFFSET_MS


async def test_normal_trade_timestamp_is_kept() -> None:
    fake = BithumbFake(["KRW-BTC"])
    fake.set_orderbook(
        "KRW-BTC",
        [{"ask_price": 101.0, "bid_price": 99.0, "ask_size": 1.0, "bid_size": 1.0}],
    )
    normal_ts = (
        int(time.time() * 1000) - 5_000
    )  # 빗썸이 버그를 고치면 자동 통과해야 한다
    fake.set_ticker("KRW-BTC", 100.0, normal_ts)
    row = (await BithumbConnector().fetch_rows(fake.client())).rows[0]
    assert row.price_timestamp == normal_ts


async def test_all_ghost_side_skips_coin() -> None:
    # 잔량 있는 ask 가 하나도 없으면 그 코인은 저장되지 않는다 (§3.4 한쪽 호가 빔)
    fake = BithumbFake(["KRW-BTC"])
    fake.set_orderbook(
        "KRW-BTC",
        [{"ask_price": 100.0, "bid_price": 99.0, "ask_size": 0.0, "bid_size": 1.0}],
    )
    fake.set_ticker("KRW-BTC", 100.0, 1_700_000_000_000)
    result = await BithumbConnector().fetch_rows(fake.client())
    assert result.rows == []
