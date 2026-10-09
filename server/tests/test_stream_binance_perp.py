"""바이낸스 perp 원천 — bookTicker·premiumIndex 전체 매초 폴링(한 회차 병렬)·exchangeInfo 필터·fundingInfo 60초·실패 분류·정체 판정·종료 (스펙 046 §3.5, §4)."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.perp import PerpSink
from app.core.streams.binance_perp import (
    BOOK_TICKER_URL,
    EXCHANGE_INFO_URL,
    FUNDING_INFO_INTERVAL_MS,
    FUNDING_INFO_URL,
    POLL_INTERVAL,
    PREMIUM_INDEX_URL,
    BinancePerpStream,
)
from tests.conftest import RawLog
from tests.stream_fakes import Clock, Sleeps

T0 = 1_787_727_947_000
SRC = "binance_perp"
INFO_SOURCE = "rest:/fapi/v1/exchangeInfo"
FUNDING_SOURCE = "rest:/fapi/v1/fundingInfo"
BOOK_SOURCE = "rest:/fapi/v1/ticker/bookTicker"
PREMIUM_SOURCE = "rest:/fapi/v1/premiumIndex"


def base_of(symbol: str) -> str:
    return symbol[: -len("USDT")].removeprefix("1000")


def info_row(symbol: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "baseAsset": symbol[: -len("USDT")],
        "quoteAsset": "USDT",
        "contractType": "PERPETUAL",
        "status": "TRADING",
    }
    row.update(over)
    return row


def exchange_info(rows: list[dict[str, Any]], server_time: int = T0) -> dict[str, Any]:
    return {"timezone": "UTC", "serverTime": server_time, "symbols": rows}


def funding_info(hours: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": s,
            "fundingIntervalHours": h,
            "adjustedFundingRateCap": "0.02",
            "adjustedFundingRateFloor": "-0.02",
            "updateTime": T0,
        }
        for s, h in hours.items()
    ]


def book(symbol: str = "1000PEPEUSDT", ts: int = T0, **fields: Any) -> dict[str, Any]:
    """bookTicker 항목 — 문서에 없는 lastUpdateId 도 실응답엔 온다."""
    item: dict[str, Any] = {
        "symbol": symbol,
        "bidPrice": "0.0123",
        "bidQty": "500",
        "askPrice": "0.0124",
        "askQty": "700",
        "time": ts,
        "lastUpdateId": 1,
    }
    item.update(fields)
    return item


def premium(symbol: str = "1000PEPEUSDT", **fields: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "symbol": symbol,
        "markPrice": "0.01235",
        "indexPrice": "0.01235",
        "estimatedSettlePrice": "0.01235",
        "lastFundingRate": "0.0001",
        "interestRate": "0.0001",
        "nextFundingTime": T0 + 3_600_000,
        "time": T0,
    }
    item.update(fields)
    return item


class Rest:
    """가짜 REST — 목록·주기·두 티커 응답을 따로 두고 호출을 기록한다. 티커는 회차마다 순서대로(마지막 반복)."""

    def __init__(
        self,
        symbols: list[str],
        books: list[httpx.Response | Exception] | None = None,
        premiums: list[httpx.Response | Exception] | None = None,
        hours: dict[str, int] | None = None,
    ) -> None:
        self.symbols: list[dict[str, Any]] = [info_row(s) for s in symbols]
        self.hours = dict(hours) if hours is not None else {}
        self.books = list(books) if books is not None else []
        self.premiums = list(premiums) if premiums is not None else []
        self.calls: list[httpx.Request] = []

    @staticmethod
    def _next(
        queue: list[httpx.Response | Exception], default: list[dict[str, Any]]
    ) -> httpx.Response:
        if not queue:
            return httpx.Response(200, json=default)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/fapi/v1/ticker/bookTicker":
            return self._next(self.books, [book()])
        if path == "/fapi/v1/premiumIndex":
            return self._next(self.premiums, [premium()])
        if path == "/fapi/v1/fundingInfo":
            return httpx.Response(200, json=funding_info(self.hours))
        return httpx.Response(200, json=exchange_info(self.symbols))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def calls_to(self, path: str) -> int:
        return sum(1 for c in self.calls if c.url.path == path)


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def build(
    rest: Rest | None = None,
    *,
    universe: set[str] | None = None,
) -> tuple[BinancePerpStream, Rest, Sleeps, RawLog, Clock, LiveStore]:
    rest = rest if rest is not None else Rest(["1000PEPEUSDT"])
    store = LiveStore()
    sink = PerpSink(store)
    bases = {base_of(r["symbol"]) for r in rest.symbols}
    sink.set_universe(universe if universe is not None else bases)
    sleeps = Sleeps()
    raw = RawLog()
    clock = Clock(T0)
    client = rest.client()
    stream = BinancePerpStream(
        store=store, sink=sink, client=client, record=raw, sleep=sleeps, clock=clock
    )
    await stream.refresh(client)
    stream.set_universe(sink.universe)
    return stream, rest, sleeps, raw, clock, store


# --- 티커 회차 → 행 (§3.4·§3.5) ---


async def test_poll_sets_quote_from_book_ticker_and_funding_from_premium_index() -> (
    None
):
    stream, rest, _, raw, clock, store = await build()
    clock.now = T0 + 5
    await stream.poll()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.native_symbol, row.multiplier) == ("1000PEPEUSDT", 1000)
    assert (row.bid, row.ask) == (0.0123 / 1000, 0.0124 / 1000)
    assert (row.bid_size, row.ask_size) == (500_000.0, 700_000.0)
    assert row.mark == 0.01235 / 1000 and row.funding_rate == 0.0001
    assert row.next_funding_ms == T0 + 3_600_000
    assert row.funding_interval_h == 8  # fundingInfo 에 없는 심볼은 8
    assert row.quote_ts == T0  # bookTicker 항목의 time 이 호가 시각
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    urls = {str(c.url) for c in rest.calls[-2:]}
    assert urls == {BOOK_TICKER_URL, PREMIUM_INDEX_URL}
    assert raw.keys(BOOK_SOURCE) == ["bookTicker:all"]
    assert raw.keys(PREMIUM_SOURCE) == ["premiumIndex:all"]
    state = store.stream_state(SRC)
    assert state is not None and state.connected and state.last_message_at == T0 + 5
    assert state.connected_since == T0 + 5 and state.url == BOOK_TICKER_URL
    assert state.subscribed == 1  # 우주 안 심볼 수


async def test_symbols_outside_the_map_or_universe_are_dropped() -> None:
    rest = Rest(
        ["1000PEPEUSDT", "SOLUSDT"],
        books=[httpx.Response(200, json=[book(), book("SOLUSDT"), book("ETHUSDT")])],
        premiums=[
            httpx.Response(
                200,
                json=[
                    premium(),
                    premium("SOLUSDT"),
                    premium("BTCUSDC"),  # USDC — 목록 필터로 맵에 없다
                    premium("BTCUSDT_261225"),  # 기간물 — 맵에 없다
                ],
            )
        ],
    )
    stream, _, _, _, _, store = await build(rest, universe={"PEPE"})
    await stream.poll()
    assert [r.base for r in store.perp_rows()] == ["PEPE"]
    assert stream.decode_failures == 0


async def test_missing_quote_field_is_invalid_and_funding_fields_are_optional() -> None:
    rest = Rest(
        ["1000PEPEUSDT"],
        books=[
            httpx.Response(200, json=[book()]),
            httpx.Response(200, json=[book(ts=T0 + 1000, askQty="")]),
        ],
        premiums=[
            httpx.Response(200, json=[premium()]),
            httpx.Response(
                200, json=[premium(lastFundingRate="0.0002", markPrice="x")]
            ),
        ],
    )
    stream, _, _, _, clock, store = await build(rest)
    await stream.poll()
    clock.now = T0 + 1000
    await stream.poll()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert row.quote_ts == T0 and row.ask_size == 700_000.0  # 호가 불변
    assert row.funding_rate == 0.0002 and row.mark == 0.01235 / 1000  # 펀딩은 온 것만
    assert row.updated_at == datetime.fromtimestamp((T0 + 1000) / 1000, tz=UTC)


async def test_universe_change_removes_rows_and_counts_subscribed() -> None:
    rest = Rest(
        ["1000PEPEUSDT", "SOLUSDT"],
        books=[httpx.Response(200, json=[book(), book("SOLUSDT")])],
        premiums=[httpx.Response(200, json=[premium(), premium("SOLUSDT")])],
    )
    stream, _, _, _, _, store = await build(rest)
    await stream.poll()
    assert {r.base for r in store.perp_rows()} == {"PEPE", "SOL"}
    stream.set_universe({"SOL"})
    assert [r.base for r in store.perp_rows()] == ["SOL"]
    assert store.stream_state(SRC).subscribed == 1  # type: ignore[union-attr]
    stream.set_universe({"SOL", "BTC"})  # 맵에 없는 BTC 는 무시
    assert store.stream_state(SRC).subscribed == 1  # type: ignore[union-attr]


# --- 회차·실패·판정 (§3.5·§3.8) ---


async def test_both_endpoints_are_fetched_in_parallel_within_one_round() -> None:
    """한 회차의 두 요청이 겹쳐 나간다 — 각 요청이 상대가 도착할 때까지 기다리므로 차례로 보내면 영원히 안 끝난다."""
    arrived = 0
    both = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal arrived
        if request.url.path == "/fapi/v1/ticker/bookTicker":
            body: Any = [book()]
        else:
            body = [premium()]
        arrived += 1
        if arrived == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 1.0)
        return httpx.Response(200, json=body)

    stream, _, _, _, _, store = await build()
    stream._client = _client(handler)
    await asyncio.wait_for(stream.poll(), 1.0)
    assert store.perp_row(SRC, "PEPE") is not None


async def test_polls_every_second_after_each_round() -> None:
    stream, rest, sleeps, _, _, _ = await build()
    gate = asyncio.Event()

    async def sleep(seconds: float) -> None:
        sleeps.values.append(seconds)
        if len(sleeps.values) >= 3:
            gate.set()
            await asyncio.Event().wait()
        await asyncio.sleep(0)

    stream._sleep = sleep  # type: ignore[assignment]
    stream.start()
    await asyncio.wait_for(gate.wait(), 1.0)
    await stream.aclose()
    assert sleeps.values == [POLL_INTERVAL] * 3 and POLL_INTERVAL == 1.0
    assert rest.calls_to("/fapi/v1/ticker/bookTicker") == 3
    assert rest.calls_to("/fapi/v1/premiumIndex") == 3


@pytest.mark.parametrize(
    ("response", "kind", "status"),
    [
        (httpx.Response(418, text="banned"), "banned", 418),
        (httpx.Response(403, text="blocked"), "banned", 403),
        (httpx.Response(429, text="slow"), "rate_limit", 429),
        (httpx.Response(502, text="bad gateway"), "unavailable", 502),
        (httpx.Response(404, text="nope"), "bad_request", 404),
        (httpx.Response(200, text="not json"), "bad_response", None),
        (httpx.Response(200, json={"code": -1}), "bad_response", None),
        (httpx.ReadTimeout("t"), "timeout", None),
        (httpx.ConnectError("refused"), "network", None),
    ],
)
async def test_failed_round_leaves_rows_and_is_classified(
    response: httpx.Response | Exception,
    kind: str,
    status: int | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    rest = Rest(
        ["1000PEPEUSDT"],
        books=[httpx.Response(200, json=[book()]), response],
    )
    stream, _, _, raw, clock, store = await build(rest)
    await stream.poll()
    clock.now = T0 + 1000
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.binance_perp"):
        await stream.poll()
        clock.now = T0 + 2000
        await stream.poll()  # 같은 원인 — 60초 안이라 로그 1줄
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.quote_ts == T0  # 실패 회차는 행을 건드리지 않는다
    verdict = stream.judge(clock.now)
    assert verdict is not None and verdict.error is not None
    assert (verdict.error.kind, verdict.error.status_code, verdict.error.url) == (
        kind,
        status,
        BOOK_TICKER_URL,
    )
    assert "티커 조회 실패" in verdict.error.message
    assert len([r for r in caplog.records if "티커 조회 실패" in r.getMessage()]) == 1
    state = store.stream_state(SRC)
    assert state is not None and not state.connected and state.last_message_at == T0
    if isinstance(response, httpx.Response):
        assert raw.keys(BOOK_SOURCE) == ["bookTicker:all"] * 3  # 실패 본문도 원문
    # 다음 회차가 성공하면 바로 회복
    rest.books = [httpx.Response(200, json=[book(ts=T0 + 3000)])]
    clock.now = T0 + 3000
    await stream.poll()
    assert stream.judge(clock.now).ok and state.connected  # type: ignore[union-attr]
    assert state.connected_since == T0 + 3000


async def test_premium_index_failure_fails_the_round_and_keeps_the_quote() -> None:
    """펀딩 쪽만 죽어도 원천 실패 — 낡은 펀딩이 갭 표에 실리지 않게. 성공한 호가도 그 회차엔 싣지 않는다."""
    rest = Rest(
        ["1000PEPEUSDT"],
        books=[
            httpx.Response(200, json=[book()]),
            httpx.Response(200, json=[book(ts=T0 + 1000, bidPrice="0.0200")]),
        ],
        premiums=[
            httpx.Response(200, json=[premium()]),
            httpx.Response(429, text="slow"),
        ],
    )
    stream, _, _, _, clock, store = await build(rest)
    await stream.poll()
    clock.now = T0 + 1000
    await stream.poll()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.quote_ts == T0 and row.bid == 0.0123 / 1000
    verdict = stream.judge(clock.now)
    assert verdict is not None and verdict.error is not None
    assert (verdict.error.kind, verdict.error.url) == ("rate_limit", PREMIUM_INDEX_URL)


async def test_no_verdict_before_the_first_round_and_stale_after_thirty_seconds() -> (
    None
):
    stream, _, _, _, _, _ = await build()
    assert stream.judge(T0) is None
    await stream.poll()
    assert stream.judge(T0 + 29_999).ok  # type: ignore[union-attr]
    verdict = stream.judge(T0 + 30_000)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "stale_stream"
    assert verdict.error.message == "Binance perp 티커 정체: 30초 이상 성공한 조회 없음"


# --- exchangeInfo·fundingInfo (§3.5) ---


async def test_exchange_info_keeps_only_perpetual_trading_usdt() -> None:
    rest = Rest([])
    rest.symbols = [
        info_row("1000PEPEUSDT"),
        info_row("BTCUSDT"),
        info_row("BTCUSDT_261225", contractType="CURRENT_QUARTER", baseAsset="BTC"),
        info_row("TSLAUSDT", contractType="TRADIFI_PERPETUAL"),
        info_row("ETHUSDC", quoteAsset="USDC"),
        info_row("XYZUSDT", status="BREAK"),
        info_row("ABCUSDT", status="SETTLING"),
        info_row("PEPEUSDT", baseAsset="PEPE"),  # 같은 base 둘 — multiplier 1 우선
        info_row("1000SHIBUSDT", baseAsset="1000SHIB"),
        info_row("1MBABYDOGEUSDT", baseAsset="1MBABYDOGE"),
    ]
    stream, _, _, raw, _, store = await build(
        rest, universe={"PEPE", "BTC", "SHIB", "BABYDOGE"}
    )
    assert stream.bases() == {"PEPE", "BTC", "SHIB", "BABYDOGE"}
    assert str(rest.calls[0].url) == EXCHANGE_INFO_URL
    assert raw.keys(INFO_SOURCE) == ["symbols:perp"]
    rest.books = [
        httpx.Response(
            200,
            json=[
                book("PEPEUSDT"),
                book("1000PEPEUSDT"),
                book("1MBABYDOGEUSDT"),
                book("1000SHIBUSDT"),
            ],
        )
    ]
    await stream.poll()
    assert store.perp_row(SRC, "PEPE").native_symbol == "PEPEUSDT"  # type: ignore[union-attr]
    assert store.perp_row(SRC, "BABYDOGE").multiplier == 1_000_000  # type: ignore[union-attr]
    assert store.perp_row(SRC, "SHIB").multiplier == 1000  # type: ignore[union-attr]


async def test_first_symbol_wins_when_no_multiplier_one() -> None:
    rest = Rest([])
    rest.symbols = [
        info_row("1000SATSUSDT", baseAsset="1000SATS"),
        info_row("10000SATSUSDT", baseAsset="10000SATS"),
    ]
    stream, _, _, _, _, store = await build(rest, universe={"SATS"})
    rest.books = [httpx.Response(200, json=[book("1000SATSUSDT")])]
    await stream.poll()
    assert store.perp_row(SRC, "SATS").native_symbol == "1000SATSUSDT"  # type: ignore[union-attr]


async def test_funding_info_every_sixty_seconds_and_missing_symbol_is_eight() -> None:
    rest = Rest(["1000PEPEUSDT", "BTCUSDT"], hours={"BTCUSDT": 4})
    stream, _, _, raw, clock, store = await build(rest)
    rest.hours = {"BTCUSDT": 4, "1000PEPEUSDT": 1}
    client = rest.client()
    clock.now = T0 + 10_000
    assert await stream.refresh(client) == 1  # 10초 — fundingInfo 는 아직
    clock.now = T0 + FUNDING_INFO_INTERVAL_MS
    assert await stream.refresh(client) == 2
    assert [str(c.url) for c in rest.calls[-3:]] == [
        EXCHANGE_INFO_URL,
        EXCHANGE_INFO_URL,
        FUNDING_INFO_URL,
    ]
    assert raw.keys(FUNDING_SOURCE) == ["funding:all"] * 2
    rest.books = [httpx.Response(200, json=[book(), book("BTCUSDT")])]
    await stream.poll()
    assert store.perp_row(SRC, "BTC").funding_interval_h == 4  # type: ignore[union-attr]
    assert store.perp_row(SRC, "PEPE").funding_interval_h == 1  # type: ignore[union-attr]
    # 응답에서 빠진 심볼은 8 로 — 목록 갱신마다 전 행에 반영
    rest.hours = {"BTCUSDT": 4}
    clock.now = T0 + 2 * FUNDING_INFO_INTERVAL_MS
    await stream.refresh(client)
    assert store.perp_row(SRC, "PEPE").funding_interval_h == 8  # type: ignore[union-attr]


async def test_exchange_info_that_differs_only_in_server_time_is_not_parsed() -> None:
    stream, _, _, raw, _, _ = await build()
    bodies = [
        exchange_info([info_row("1000PEPEUSDT")], server_time=T0 + k) for k in (1, 2)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/fundingInfo":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=bodies.pop(0))

    seen: dict[str, int] = {}
    original = stream._parse

    def spy(resp: httpx.Response, url: str) -> Any:
        seen[url] = seen.get(url, 0) + 1
        return original(resp, url)

    stream._parse = spy  # type: ignore[method-assign]
    await stream.refresh(_client(handler))
    await stream.refresh(_client(handler))
    assert seen.get(EXCHANGE_INFO_URL, 0) == 0  # build 에서 같은 본문을 이미 파싱했다
    assert len(raw.keys(INFO_SOURCE)) == 3  # 원문은 매번


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, "rate_limit"),
        (418, "banned"),
        (403, "banned"),
        (503, "unavailable"),
        (400, "bad_request"),
    ],
)
async def test_list_failures_are_classified_and_keep_the_list(
    status: int, kind: str
) -> None:
    stream, _, _, _, _, _ = await build()
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(lambda r: httpx.Response(status, text="no")))
    assert info.value.kind == kind and info.value.status_code == status
    assert stream.bases() == {"PEPE"}
    with pytest.raises(ExchangeTimeoutError):
        await stream.refresh(
            _client(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("t")))
        )
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(lambda r: httpx.Response(200, text="nope")))
    assert info.value.kind == "bad_response"


async def test_funding_info_failure_keeps_symbols_and_retries_next_round() -> None:
    stream, _, _, _, clock, _ = await build()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/fundingInfo":
            return httpx.Response(429, text="slow down")
        return httpx.Response(
            200, json=exchange_info([info_row("1000PEPEUSDT"), info_row("BTCUSDT")])
        )

    clock.now = T0 + FUNDING_INFO_INTERVAL_MS
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(handler))
    assert info.value.kind == "rate_limit" and stream.bases() == {"PEPE", "BTC"}
    clock.now += 1000
    with pytest.raises(ExchangeApiError):
        await stream.refresh(_client(handler))  # 실패했으니 다음 회차에 다시


# --- 종료 ---


async def test_aclose_stops_the_poll_loop() -> None:
    stream, rest, _, _, _, _ = await build()
    stream.start()
    await asyncio.sleep(0.01)
    await stream.aclose()
    n = rest.calls_to("/fapi/v1/ticker/bookTicker")
    await asyncio.sleep(0.01)
    assert rest.calls_to("/fapi/v1/ticker/bookTicker") == n and stream._task is None
