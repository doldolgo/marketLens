"""바이빗 perp 원천 — 전체 티커 매초 폴링·instruments-info(linear) 필터·커서·주기·실패 분류·정체 판정·종료 (스펙 046 §3.6, §4)."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.errors import ExchangeApiError
from app.core.live_store import LiveStore
from app.core.perp import PerpSink
from app.core.streams.bybit_perp import (
    INSTRUMENTS_URL,
    POLL_INTERVAL,
    TICKERS_URL,
    BybitPerpStream,
)
from tests.conftest import RawLog
from tests.stream_fakes import Clock, Sleeps

T0 = 1_787_727_947_000
SRC = "bybit_perp"
REST_SOURCE = "rest:/v5/market/instruments-info"
TICKERS_SOURCE = "rest:/v5/market/tickers"


def base_of(symbol: str) -> str:
    return symbol[: -len("USDT")].removeprefix("1000")


def inst(symbol: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "contractType": "LinearPerpetual",
        "status": "Trading",
        "baseCoin": symbol[: -len("USDT")],
        "quoteCoin": "USDT",
        "fundingInterval": 480,
    }
    row.update(over)
    return row


def envelope(
    rows: list[dict[str, Any]], cursor: str = "", ret_code: int = 0, time_ms: int = T0
) -> dict[str, Any]:
    return {
        "retCode": ret_code,
        "retMsg": "OK" if ret_code == 0 else "Too many visits!",
        "result": {"category": "linear", "list": rows, "nextPageCursor": cursor},
        "retExtInfo": {},
        "time": time_ms,
    }


def tick(symbol: str = "1000PEPEUSDT", **fields: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "symbol": symbol,
        "bid1Price": "0.0123",
        "bid1Size": "500",
        "ask1Price": "0.0124",
        "ask1Size": "700",
        "markPrice": "0.01235",
        "fundingRate": "0.0001",
        "nextFundingTime": str(T0 + 3_600_000),
        "fundingIntervalHour": "8",
        "lastPrice": "0.01235",
    }
    item.update(fields)
    return item


class Rest:
    """가짜 REST — 목록·티커 응답을 따로 두고 호출을 기록한다. 티커는 회차마다 순서대로(마지막 반복)."""

    def __init__(
        self,
        symbols: list[str],
        tickers: list[httpx.Response | Exception] | None = None,
    ) -> None:
        self.instruments: list[dict[str, Any]] = [inst(s) for s in symbols]
        self.tickers = list(tickers) if tickers is not None else []
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/v5/market/tickers":
            if not self.tickers:
                return httpx.Response(200, json=envelope([tick()]))
            item = self.tickers.pop(0) if len(self.tickers) > 1 else self.tickers[0]
            if isinstance(item, Exception):
                raise item
            return item
        return httpx.Response(200, json=envelope(self.instruments))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def ticker_calls(self) -> int:
        return sum(1 for c in self.calls if c.url.path == "/v5/market/tickers")


async def build(
    rest: Rest | None = None,
    *,
    universe: set[str] | None = None,
) -> tuple[BybitPerpStream, Rest, Sleeps, RawLog, Clock, LiveStore]:
    rest = rest if rest is not None else Rest(["1000PEPEUSDT"])
    store = LiveStore()
    sink = PerpSink(store)
    bases = {base_of(r["symbol"]) for r in rest.instruments}
    sink.set_universe(universe if universe is not None else bases)
    sleeps = Sleeps()
    raw = RawLog()
    clock = Clock(T0)
    client = rest.client()
    stream = BybitPerpStream(
        store=store, sink=sink, client=client, record=raw, sleep=sleeps, clock=clock
    )
    await stream.refresh(client)
    stream.set_universe(sink.universe)
    return stream, rest, sleeps, raw, clock, store


# --- 티커 회차 → 행 (§3.4·§3.6) ---


async def test_poll_sets_quote_mark_funding_and_interval_from_one_response() -> None:
    stream, rest, _, raw, clock, store = await build()
    clock.now = T0 + 5
    await stream.poll()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.native_symbol, row.multiplier) == ("1000PEPEUSDT", 1000)
    assert (row.bid, row.ask) == (0.0123 / 1000, 0.0124 / 1000)
    assert (row.bid_size, row.ask_size) == (500_000.0, 700_000.0)
    assert row.mark == 0.01235 / 1000 and row.funding_rate == 0.0001
    assert row.next_funding_ms == T0 + 3_600_000 and row.funding_interval_h == 8
    assert row.quote_ts == T0  # 봉투 time 이 호가 시각
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    assert str(rest.calls[-1].url) == TICKERS_URL
    assert raw.keys(TICKERS_SOURCE) == ["tickers:all"]
    state = store.stream_state(SRC)
    assert state is not None and state.connected and state.last_message_at == T0 + 5
    assert state.connected_since == T0 + 5 and state.url == TICKERS_URL
    assert state.subscribed == 1  # 우주 안 심볼 수


async def test_symbols_outside_the_map_or_universe_are_dropped() -> None:
    rest = Rest(
        ["1000PEPEUSDT", "SOLUSDT"],
        [
            httpx.Response(
                200, json=envelope([tick(), tick("SOLUSDT"), tick("ETHUSDT")])
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
        [
            httpx.Response(200, json=envelope([tick()])),
            httpx.Response(
                200,
                json=envelope(
                    [tick(ask1Size="", fundingRate="0.0002", markPrice="x")],
                    time_ms=T0 + 1000,
                ),
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
        [httpx.Response(200, json=envelope([tick(), tick("SOLUSDT")]))],
    )
    stream, _, _, _, _, store = await build(rest)
    await stream.poll()
    assert {r.base for r in store.perp_rows()} == {"PEPE", "SOL"}
    stream.set_universe({"SOL"})
    assert [r.base for r in store.perp_rows()] == ["SOL"]
    assert store.stream_state(SRC).subscribed == 1  # type: ignore[union-attr]
    stream.set_universe({"SOL", "BTC"})  # 맵에 없는 BTC 는 무시
    assert store.stream_state(SRC).subscribed == 1  # type: ignore[union-attr]


# --- 회차·실패·판정 (§3.6·§3.8) ---


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
    assert rest.ticker_calls() == 3


@pytest.mark.parametrize(
    ("response", "kind", "status"),
    [
        (httpx.Response(200, json=envelope([], ret_code=10006)), "rate_limit", 200),
        (httpx.Response(200, json=envelope([], ret_code=10001)), "bad_response", 200),
        (httpx.Response(403, text="blocked"), "banned", 403),
        (httpx.Response(429, text="slow"), "rate_limit", 429),
        (httpx.Response(502, text="bad gateway"), "unavailable", 502),
        (httpx.Response(404, text="nope"), "bad_request", 404),
        (httpx.Response(200, text="not json"), "bad_response", None),
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
        ["1000PEPEUSDT"], [httpx.Response(200, json=envelope([tick()])), response]
    )
    stream, _, _, raw, clock, store = await build(rest)
    await stream.poll()
    clock.now = T0 + 1000
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.bybit_perp"):
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
        TICKERS_URL,
    )
    assert "티커 조회 실패" in verdict.error.message
    assert len([r for r in caplog.records if "티커 조회 실패" in r.getMessage()]) == 1
    state = store.stream_state(SRC)
    assert state is not None and not state.connected and state.last_message_at == T0
    if isinstance(response, httpx.Response):
        assert raw.keys(TICKERS_SOURCE) == ["tickers:all"] * 3  # 실패 본문도 원문
    # 다음 회차가 성공하면 바로 회복
    rest.tickers = [httpx.Response(200, json=envelope([tick()], time_ms=T0 + 3000))]
    clock.now = T0 + 3000
    await stream.poll()
    assert stream.judge(clock.now).ok and state.connected  # type: ignore[union-attr]
    assert state.connected_since == T0 + 3000


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
    assert verdict.error.message == "Bybit perp 티커 정체: 30초 이상 성공한 조회 없음"


# --- 목록 (§3.6) ---


async def test_list_keeps_only_linear_perpetual_trading_usdt_and_maps_interval() -> (
    None
):
    rest = Rest([])
    rest.instruments = [
        inst("1000PEPEUSDT", fundingInterval=240),
        inst("BTCPERP", quoteCoin="USDC", baseCoin="BTC"),
        inst("ETHUSDT-26DEC25", contractType="LinearFutures", baseCoin="ETH"),
        inst("XYZUSDT", status="PreLaunch"),
        inst("SHIB1000USDT", baseCoin="SHIB1000", fundingInterval=60),
        inst("SOLUSDT"),
    ]
    stream, _, _, raw, _, store = await build(rest, universe={"PEPE", "SHIB", "SOL"})
    assert stream.bases() == {"PEPE", "SHIB", "SOL"}
    assert str(rest.calls[0].url) == INSTRUMENTS_URL
    assert raw.keys(REST_SOURCE) == ["symbols:perp"]
    rest.tickers = [
        httpx.Response(
            200,
            json=envelope(
                [
                    tick(fundingIntervalHour=""),
                    tick("SHIB1000USDT", fundingIntervalHour=""),
                    tick("SOLUSDT", fundingIntervalHour=""),
                ]
            ),
        )
    ]
    await stream.poll()
    assert store.perp_row(SRC, "PEPE").funding_interval_h == 4  # type: ignore[union-attr]
    assert store.perp_row(SRC, "SHIB").funding_interval_h == 1  # type: ignore[union-attr]
    assert store.perp_row(SRC, "SOL").funding_interval_h == 8  # type: ignore[union-attr]
    assert store.perp_row(SRC, "SHIB").multiplier == 1000  # type: ignore[union-attr]


async def test_next_page_cursor_is_followed_and_unchanged_pages_are_not_parsed() -> (
    None
):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if b"cursor=" in request.url.query:
            return httpx.Response(
                200, json=envelope([inst("SOLUSDT")], time_ms=len(calls))
            )
        return httpx.Response(
            200, json=envelope([inst("BTCUSDT")], cursor="p2", time_ms=len(calls))
        )

    stream, _, _, raw, _, _ = await build()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await stream.refresh(client) == 2
    assert stream.bases() == {"BTC", "SOL"}
    assert calls[1].url.query.endswith(b"cursor=p2")
    parsed = 0
    original = stream._parse_page

    def spy(resp: httpx.Response, url: str) -> Any:
        nonlocal parsed
        parsed += 1
        return original(resp, url)

    stream._parse_page = spy  # type: ignore[method-assign]
    assert await stream.refresh(client) == 2  # 시각만 다른 두 페이지 — 파싱 0
    assert parsed == 0 and stream.bases() == {"BTC", "SOL"}
    assert raw.keys(REST_SOURCE) == ["symbols:perp"] * 5


async def test_list_failures_keep_the_previous_list() -> None:
    stream, _, _, _, _, _ = await build()
    for response, kind in (
        (httpx.Response(200, json=envelope([], ret_code=10006)), "rate_limit"),
        (httpx.Response(200, json=envelope([], ret_code=10001)), "bad_response"),
        (httpx.Response(403, text="blocked"), "banned"),
    ):
        with pytest.raises(ExchangeApiError) as info:
            await stream.refresh(
                httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda r, x=response: x)
                )
            )
        assert info.value.kind == kind
    assert stream.bases() == {"PEPE"}


async def test_aclose_stops_the_poll_loop() -> None:
    stream, rest, _, _, _, _ = await build()
    stream.start()
    await asyncio.sleep(0.01)
    await stream.aclose()
    n = rest.ticker_calls()
    await asyncio.sleep(0.01)
    assert rest.ticker_calls() == n and stream._task is None
