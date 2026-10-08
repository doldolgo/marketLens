"""비트겟 perp 원천 — 전체 티커 매초 폴링·contracts 필터·주기·다음 정산 계산·실패 분류·정체 판정·종료 (스펙 046 §3.7, §4)."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.errors import ExchangeApiError
from app.core.live_store import LiveStore
from app.core.perp import PerpSink
from app.core.streams.bitget_perp import (
    CONTRACTS_URL,
    POLL_INTERVAL,
    TICKERS_URL,
    BitgetPerpStream,
    next_funding_ms,
)
from tests.conftest import RawLog
from tests.stream_fakes import Clock, Sleeps

T0 = 1_787_727_947_000
SRC = "bitget_perp"
REST_SOURCE = "rest:/api/v2/mix/market/contracts"
TICKERS_SOURCE = "rest:/api/v2/mix/market/tickers"
HOUR = 3_600_000


def base_of(symbol: str) -> str:
    return symbol[: -len("USDT")].removeprefix("1000")


def contract(symbol: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "baseCoin": symbol[: -len("USDT")],
        "quoteCoin": "USDT",
        "symbolType": "perpetual",
        "symbolStatus": "normal",
        "fundInterval": "8",
    }
    row.update(over)
    return row


def envelope(
    rows: list[dict[str, Any]], code: str = "00000", request_time: int = T0
) -> dict[str, Any]:
    return {
        "code": code,
        "msg": "success" if code == "00000" else "fail",
        "requestTime": request_time,
        "data": rows,
    }


def tick(symbol: str = "1000PEPEUSDT", ts: int = T0, **fields: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "symbol": symbol,
        "lastPr": "0.01235",
        "bidPr": "0.0123",
        "askPr": "0.0124",
        "bidSz": "500",
        "askSz": "700",
        "markPrice": "0.01235",
        "indexPrice": "0.01235",
        "fundingRate": "0.0001",
        "ts": str(ts),
    }
    item.update(fields)
    return item


class Rest:
    def __init__(
        self,
        symbols: list[str],
        tickers: list[httpx.Response | Exception] | None = None,
    ) -> None:
        self.contracts: list[dict[str, Any]] = [contract(s) for s in symbols]
        self.tickers = list(tickers) if tickers is not None else []
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/api/v2/mix/market/tickers":
            if not self.tickers:
                return httpx.Response(200, json=envelope([tick()]))
            item = self.tickers.pop(0) if len(self.tickers) > 1 else self.tickers[0]
            if isinstance(item, Exception):
                raise item
            return item
        return httpx.Response(200, json=envelope(self.contracts))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def ticker_calls(self) -> int:
        return sum(1 for c in self.calls if c.url.path == "/api/v2/mix/market/tickers")


async def build(
    rest: Rest | None = None,
    *,
    universe: set[str] | None = None,
) -> tuple[BitgetPerpStream, Rest, Sleeps, RawLog, Clock, LiveStore]:
    rest = rest if rest is not None else Rest(["1000PEPEUSDT"])
    store = LiveStore()
    sink = PerpSink(store)
    bases = {base_of(r["symbol"]) for r in rest.contracts}
    sink.set_universe(universe if universe is not None else bases)
    sleeps = Sleeps()
    raw = RawLog()
    clock = Clock(T0)
    client = rest.client()
    stream = BitgetPerpStream(
        store=store, sink=sink, client=client, record=raw, sleep=sleeps, clock=clock
    )
    await stream.refresh(client)
    stream.set_universe(sink.universe)
    return stream, rest, sleeps, raw, clock, store


# --- 티커 회차 → 행 (§3.4·§3.7) ---


async def test_poll_sets_quote_mark_funding_and_computed_next_funding() -> None:
    stream, rest, _, raw, clock, store = await build()
    clock.now = T0 + 5
    await stream.poll()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.native_symbol, row.multiplier) == ("1000PEPEUSDT", 1000)
    assert (row.bid, row.ask) == (0.0123 / 1000, 0.0124 / 1000)
    assert (row.bid_size, row.ask_size) == (500_000.0, 700_000.0)
    assert row.mark == 0.01235 / 1000 and row.funding_rate == 0.0001
    assert row.funding_interval_h == 8
    assert row.next_funding_ms == next_funding_ms(
        T0 + 5, 8
    )  # 응답에 없다 — 주기의 다음 배수
    assert row.quote_ts == T0  # 항목 ts 가 호가 시각
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    assert str(rest.calls[-1].url) == TICKERS_URL
    assert raw.keys(TICKERS_SOURCE) == ["tickers:all"]
    state = store.stream_state(SRC)
    assert state is not None and state.connected and state.last_message_at == T0 + 5
    assert state.subscribed == 1 and state.url == TICKERS_URL


def test_next_funding_is_the_next_multiple_of_the_interval_from_utc_midnight() -> None:
    day = 1_787_702_400_000  # T0 의 날 00:00 UTC
    assert day % (24 * HOUR) == 0
    assert next_funding_ms(day + 3 * HOUR + 1, 4) == day + 4 * HOUR
    assert (
        next_funding_ms(day + 4 * HOUR, 4) == day + 8 * HOUR
    )  # 경계 그 순간은 다음 배수
    assert next_funding_ms(day + 10 * HOUR, 8) == day + 16 * HOUR
    assert next_funding_ms(day + 30 * 60_000, 1) == day + HOUR


async def test_unknown_interval_leaves_next_funding_null() -> None:
    rest = Rest([])
    rest.contracts = [contract("1000PEPEUSDT", fundInterval="")]
    stream, _, _, _, _, store = await build(rest)
    await stream.poll()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert row.next_funding_ms is None and row.funding_interval_h is None


async def test_symbols_outside_map_or_universe_dropped_and_missing_quote_invalid() -> (
    None
):
    rest = Rest(
        ["1000PEPEUSDT", "SOLUSDT"],
        [
            httpx.Response(
                200, json=envelope([tick(), tick("SOLUSDT"), tick("ETHUSDT")])
            ),
            httpx.Response(
                200,
                json=envelope([tick(ts=T0 + 10, askSz=None, fundingRate="0.0002")]),
            ),
        ],
    )
    stream, _, _, _, clock, store = await build(rest, universe={"PEPE"})
    await stream.poll()
    assert [r.base for r in store.perp_rows()] == ["PEPE"]
    clock.now = T0 + 10
    await stream.poll()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.quote_ts == T0 and row.funding_rate == 0.0002
    assert stream.decode_failures == 0


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


# --- 회차·실패·판정 (§3.7·§3.8) ---


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
        (httpx.Response(200, json=envelope([], code="40001")), "bad_response", 200),
        (httpx.Response(429, text="slow"), "rate_limit", 429),
        (httpx.Response(403, text="blocked"), "banned", 403),
        (httpx.Response(503, text="down"), "unavailable", 503),
        (httpx.Response(400, text="nope"), "bad_request", 400),
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
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.bitget_perp"):
        await stream.poll()
        clock.now = T0 + 2000
        await stream.poll()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.quote_ts == T0
    verdict = stream.judge(clock.now)
    assert verdict is not None and verdict.error is not None
    assert (verdict.error.kind, verdict.error.status_code, verdict.error.url) == (
        kind,
        status,
        TICKERS_URL,
    )
    assert len([r for r in caplog.records if "티커 조회 실패" in r.getMessage()]) == 1
    state = store.stream_state(SRC)
    assert state is not None and not state.connected and state.last_message_at == T0
    if isinstance(response, httpx.Response):
        assert raw.keys(TICKERS_SOURCE) == ["tickers:all"] * 3
    rest.tickers = [httpx.Response(200, json=envelope([tick(ts=T0 + 3000)]))]
    clock.now = T0 + 3000
    await stream.poll()
    assert stream.judge(clock.now).ok and state.connected  # type: ignore[union-attr]


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
    assert verdict.error.message == "Bitget perp 티커 정체: 30초 이상 성공한 조회 없음"


# --- 목록 (§3.7) ---


async def test_contracts_keeps_perpetual_normal_usdt_and_maps_interval() -> None:
    rest = Rest([])
    rest.contracts = [
        contract("1000BONKUSDT", baseCoin="1000BONK", fundInterval="4"),
        contract(
            "PEPEUSDT", baseCoin="PEPE", fundInterval="1"
        ),  # 비트겟은 배수 없이 낸다
        contract("BTCUSDT-DELIVERY", symbolType="delivery", baseCoin="BTC"),
        contract("XYZUSDT", symbolStatus="maintain"),
        contract("ETHUSDC", quoteCoin="USDC", baseCoin="ETH"),
        contract("TSLAUSDT"),  # 토큰화 주식 — 거르지 않는다
    ]
    stream, _, _, raw, _, store = await build(rest, universe={"BONK", "PEPE"})
    assert stream.bases() == {"BONK", "PEPE", "TSLA"}
    assert str(rest.calls[0].url) == CONTRACTS_URL
    assert raw.keys(REST_SOURCE) == ["symbols:perp"]
    rest.tickers = [
        httpx.Response(200, json=envelope([tick("1000BONKUSDT"), tick("PEPEUSDT")]))
    ]
    await stream.poll()
    bonk = store.perp_row(SRC, "BONK")
    pepe = store.perp_row(SRC, "PEPE")
    assert bonk is not None and pepe is not None
    assert bonk.funding_interval_h == 4
    assert bonk.next_funding_ms == next_funding_ms(T0, 4)
    assert pepe.funding_interval_h == 1 and pepe.multiplier == 1


async def test_contracts_that_differ_only_in_request_time_are_not_parsed() -> None:
    stream, _, _, raw, _, _ = await build()
    bodies = [
        envelope([contract("1000PEPEUSDT"), contract("SOLUSDT")], request_time=T0 + k)
        for k in (1, 2)
    ]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=bodies.pop(0)))
    )
    await stream.refresh(client)
    symbol_map = stream._symbol_of
    assert stream.bases() == {"PEPE", "SOL"}
    await stream.refresh(client)  # requestTime 만 다르다 — 원문은 기록, 맵은 그대로
    assert stream._symbol_of is symbol_map
    assert raw.keys(REST_SOURCE) == ["symbols:perp"] * 3


async def test_list_failures_keep_the_previous_list() -> None:
    stream, _, _, _, _, _ = await build()
    for response, kind in (
        (httpx.Response(200, json=envelope([], code="40001")), "bad_response"),
        (httpx.Response(429, text="slow"), "rate_limit"),
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
