"""바이낸스 perp 커넥터 — depth5·markPrice 배열·exchangeInfo 필터·fundingInfo 60초·구독 묶음·샤드 판정·펀딩 연결 실패·분류·종료 (스펙 046 §3.5, §4)."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.perp import PerpSink
from app.core.streams.binance_perp import (
    CONTROL_INTERVAL,
    EXCHANGE_INFO_URL,
    FUNDING_INFO_INTERVAL_MS,
    FUNDING_INFO_URL,
    FUNDING_SHARD,
    MARKET_WS_URL,
    PUBLIC_WS_URL,
    SHARDS,
    STREAMS_PER_MESSAGE,
    BinancePerpStream,
    shard_of,
)
from tests.conftest import RawLog
from tests.stream_fakes import (
    Clock,
    FakeConnector,
    FakeSocket,
    GatedSocket,
    HandshakeRejected,
    Sleeps,
    until,
)

T0 = 1_787_727_947_000
STALE_LIMIT = 30_000
SERVER_DIR = Path(__file__).resolve().parents[1]
SRC = "binance_perp"
PEPE_SHARD = shard_of("1000PEPEUSDT")
ACK = '{"result":null,"id":1}'
PUBLIC_SOURCE = "ws:/public/ws"
MARKET_SOURCE = "ws:/market/ws"


def symbols_for(shard: int, n: int) -> list[str]:
    """해시가 그 샤드에 떨어지는 가짜 USDT 심볼 n개 — 배정 규칙(crc32 % 3)을 그대로 쓴다."""
    out: list[str] = []
    i = 0
    while len(out) < n:
        symbol = f"T{i:04d}USDT"
        i += 1
        if shard_of(symbol) == shard:
            out.append(symbol)
    return out


def base_of(symbol: str) -> str:
    return symbol[: -len("USDT")]


def info_row(symbol: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "baseAsset": base_of(symbol),
        "quoteAsset": "USDT",
        "contractType": "PERPETUAL",
        "status": "TRADING",
    }
    row.update(over)
    return row


def exchange_info(
    symbols: list[str], extra: list[dict[str, Any]] | None = None, server_time: int = T0
) -> dict[str, Any]:
    return {
        "timezone": "UTC",
        "serverTime": server_time,
        "symbols": [info_row(s) for s in symbols] + (extra or []),
    }


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


def depth(
    symbol: str = "1000PEPEUSDT",
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
    ts: int = T0,
) -> str:
    """depth5 프레임 — b[0]·a[0] 가 최우선, 5단계 전부 보낸다."""
    if bids is None:
        bids = [[f"{0.0123 - i * 0.0001:.4f}", "500"] for i in range(5)]
    if asks is None:
        asks = [[f"{0.0124 + i * 0.0001:.4f}", "700"] for i in range(5)]
    return json.dumps(
        {
            "e": "depthUpdate",
            "E": ts + 1,
            "T": ts,
            "s": symbol,
            "U": 1,
            "u": 2,
            "pu": 0,
            "b": bids,
            "a": asks,
        }
    )


def mark(items: list[tuple[str, str, str, int]]) -> str:
    """`!markPrice@arr@1s` 프레임 — (심볼, 마크가, 펀딩률, 다음 정산 ms) 배열."""
    return json.dumps(
        [
            {
                "e": "markPriceUpdate",
                "E": T0,
                "s": s,
                "p": p,
                "i": p,
                "P": p,
                "r": r,
                "T": t,
            }
            for s, p, r, t in items
        ]
    )


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def rest(
    symbols: list[str],
    hours: dict[str, int] | None = None,
    extra: list[dict[str, Any]] | None = None,
    calls: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.path == "/fapi/v1/fundingInfo":
            return httpx.Response(200, json=funding_info(hours or {}))
        return httpx.Response(200, json=exchange_info(symbols, extra))

    return _client(handler)


async def build(
    outcomes: list[FakeSocket | BaseException],
    *,
    symbols: list[str] | None = None,
    universe: set[str] | None = None,
    hours: dict[str, int] | None = None,
    sleep: Sleeps | None = None,
) -> tuple[BinancePerpStream, FakeConnector, Sleeps, RawLog, Clock, LiveStore]:
    """exchangeInfo·fundingInfo(fake REST) 로 맵을 채우고 우주를 넣은 커넥터 — 배정 있는 호가 샤드 + 펀딩 샤드가 연결한다."""
    symbols = symbols if symbols is not None else ["1000PEPEUSDT"]
    store = LiveStore()
    sink = PerpSink(store)
    bases = {base_of(s).removeprefix("1000") for s in symbols}
    sink.set_universe(universe if universe is not None else bases)
    connector = FakeConnector(outcomes)
    sleeps = sleep if sleep is not None else Sleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = BinancePerpStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleeps, clock=clock
    )
    await stream.refresh(rest(symbols, hours))
    stream.set_universe(sink.universe)
    return stream, connector, sleeps, raw, clock, store


async def run_until_exhausted(
    stream: BinancePerpStream, connector: FakeConnector
) -> None:
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()


def backoffs(sleeps: Sleeps) -> list[float]:
    return [v for v in sleeps.values if v != CONTROL_INTERVAL]


def params_of(sock: FakeSocket, method: str = "SUBSCRIBE") -> list[list[str]]:
    return [m["params"] for m in sock.subscriptions() if m["method"] == method]


# --- 호가·펀딩 프레임 → 행 (§3.4·§3.5) ---


async def test_depth5_frame_sets_top_of_book_divided_by_multiplier() -> None:
    sock = FakeSocket([ACK, depth()])
    stream, connector, _, raw, clock, store = await build([sock, FakeSocket([])])
    clock.now = T0 + 5
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.native_symbol, row.multiplier) == ("1000PEPEUSDT", 1000)
    assert (row.bid, row.ask) == (0.0123 / 1000, 0.0124 / 1000)
    assert (row.bid_size, row.ask_size) == (500_000.0, 700_000.0)
    assert row.quote_ts == T0  # T 가 호가 시각
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    assert row.funding_interval_h == 8  # fundingInfo 에 없는 심볼은 8
    assert connector.urls[:2] == [PUBLIC_WS_URL, MARKET_WS_URL]
    assert params_of(sock) == [["1000pepeusdt@depth5@500ms"]]  # 소문자 스트림 이름
    assert raw.keys(PUBLIC_SOURCE) == [None, "depth5:1000PEPEUSDT"]
    assert sock.closed


async def test_empty_side_leaves_the_quote_and_counts_the_receive() -> None:
    sock = FakeSocket([depth(), depth(bids=[], ts=T0 + 10)])
    stream, connector, _, _, clock, store = await build([sock, FakeSocket([])])
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.quote_ts == T0 and row.bid == 0.0123 / 1000
    state = store.stream_state(SRC)
    assert state is not None and state.last_message_at == T0
    assert stream.decode_failures == 0


async def test_mark_array_applies_only_universe_symbols_and_is_held_before_quote() -> (
    None
):
    frame = mark(
        [
            ("1000PEPEUSDT", "0.0125", "0.00010000", T0 + 3_600_000),
            ("BTCUSDT", "70000", "0.0001", T0),  # 우주 밖(맵에 없음) — 버린다
        ]
    )
    quote_sock, funding_sock = FakeSocket([depth()]), FakeSocket([ACK, frame])
    stream, connector, _, raw, _, store = await build([quote_sock, funding_sock])
    stream.start()
    await until(funding_sock.drained)
    await until(quote_sock.drained)
    await stream.aclose()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert row.mark == 0.0125 / 1000 and row.funding_rate == 0.0001
    assert row.next_funding_ms == T0 + 3_600_000
    assert store.perp_row(SRC, "BTC") is None
    assert params_of(funding_sock) == [["!markPrice@arr@1s"]]
    assert raw.keys(MARKET_SOURCE) == [None, "markPrice:all"]
    assert raw.payloads(MARKET_SOURCE)[1] == frame  # 배열 프레임 1개 그대로


async def test_mark_with_non_numeric_fields_updates_the_rest() -> None:
    quote_sock = FakeSocket([depth()])
    funding_sock = FakeSocket([mark([("1000PEPEUSDT", "x", "0.0002", T0 + 1)])])
    stream, connector, _, _, _, store = await build([quote_sock, funding_sock])
    stream.start()
    await until(funding_sock.drained)
    await until(quote_sock.drained)
    await stream.aclose()
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (
        row.mark is None
        and row.funding_rate == 0.0002
        and row.next_funding_ms == T0 + 1
    )


async def test_funding_frame_counts_only_for_the_funding_shard() -> None:
    funding_sock = GatedSocket()
    quote_sock = GatedSocket()
    stream, _, _, _, clock, store = await build([quote_sock, funding_sock])
    stream.start()
    await until(quote_sock.subscribed)
    await until(funding_sock.subscribed)
    await asyncio.sleep(0.01)  # 구독 묶음 전송이 끝나 connected_since 가 찍힌 뒤
    clock.now = T0 + 40_000
    funding_sock.push(mark([("1000PEPEUSDT", "0.01", "0.0001", T0)]))
    await until(funding_sock.delivered)
    verdict = stream.judge(clock.now)
    assert verdict is not None and not verdict.ok and verdict.error is not None
    assert verdict.error.message == (
        f"Binance perp 스트림 정체: 샤드 {PEPE_SHARD} (구독 1종목) 30초 이상 무수신"
    )
    assert verdict.error.url == PUBLIC_WS_URL
    await stream.aclose()


# --- exchangeInfo·fundingInfo (§3.5) ---


async def test_exchange_info_keeps_only_perpetual_trading_usdt() -> None:
    extra = [
        info_row("BTCUSDT_261225", contractType="CURRENT_QUARTER", baseAsset="BTC"),
        info_row("TSLAUSDT", contractType="TRADIFI_PERPETUAL"),
        info_row("ETHUSDC", quoteAsset="USDC"),
        info_row("XYZUSDT", status="BREAK"),
        info_row("ABCUSDT", status="SETTLING"),
        info_row("PEPEUSDT", baseAsset="PEPE"),  # 같은 base 둘 — multiplier 1 우선
        info_row("1000SHIBUSDT", baseAsset="1000SHIB"),
        info_row("1MBABYDOGEUSDT", baseAsset="1MBABYDOGE"),
    ]
    calls: list[httpx.Request] = []
    stream, _, _, raw, _, _ = await build([])
    assert (
        await stream.refresh(
            rest(["1000PEPEUSDT", "BTCUSDT"], extra=extra, calls=calls)
        )
        == 1
    )
    assert stream.bases() == {"PEPE", "BTC", "SHIB", "BABYDOGE"}
    assert stream._symbol_of["PEPE"] == "PEPEUSDT"
    assert stream._mult_of["1MBABYDOGEUSDT"] == 1_000_000
    assert str(calls[0].url) == EXCHANGE_INFO_URL
    assert raw.keys("rest:/fapi/v1/exchangeInfo") == ["symbols:perp"] * 2


async def test_first_symbol_wins_when_no_multiplier_one() -> None:
    stream, _, _, _, _, _ = await build([])
    extra = [info_row("10000SATSUSDT", baseAsset="10000SATS")]
    await stream.refresh(rest(["1000SATSUSDT"], extra=extra))
    assert stream._symbol_of["SATS"] == "1000SATSUSDT"


async def test_funding_info_every_sixty_seconds_and_missing_symbol_is_eight() -> None:
    calls: list[httpx.Request] = []
    stream, _, _, raw, clock, store = await build(
        [], symbols=["1000PEPEUSDT", "BTCUSDT"], hours={"BTCUSDT": 4}
    )
    client = rest(
        ["1000PEPEUSDT", "BTCUSDT"], {"BTCUSDT": 4, "1000PEPEUSDT": 1}, calls=calls
    )
    clock.now = T0 + 10_000
    assert await stream.refresh(client) == 1  # 10초 — fundingInfo 는 아직
    clock.now = T0 + FUNDING_INFO_INTERVAL_MS
    assert await stream.refresh(client) == 2
    assert [str(c.url) for c in calls] == [
        EXCHANGE_INFO_URL,
        EXCHANGE_INFO_URL,
        FUNDING_INFO_URL,
    ]
    assert raw.keys("rest:/fapi/v1/fundingInfo") == ["funding:all"] * 2
    sink = stream._sink
    sink.quote(
        source=SRC,
        base="BTC",
        native_symbol="BTCUSDT",
        multiplier=1,
        bid=1.0,
        ask=1.1,
        bid_size=1.0,
        ask_size=1.0,
        quote_ts=1,
        received_at_ms=1,
    )
    sink.quote(
        source=SRC,
        base="PEPE",
        native_symbol="1000PEPEUSDT",
        multiplier=1000,
        bid=1.0,
        ask=1.1,
        bid_size=1.0,
        ask_size=1.0,
        quote_ts=1,
        received_at_ms=1,
    )
    assert store.perp_row(SRC, "BTC").funding_interval_h == 4  # type: ignore[union-attr]
    assert store.perp_row(SRC, "PEPE").funding_interval_h == 1  # type: ignore[union-attr]
    # 응답에서 빠진 심볼은 8 로 — 목록 갱신마다 전 행에 반영
    clock.now = T0 + 2 * FUNDING_INFO_INTERVAL_MS
    await stream.refresh(rest(["1000PEPEUSDT", "BTCUSDT"], {"BTCUSDT": 4}))
    assert store.perp_row(SRC, "PEPE").funding_interval_h == 8  # type: ignore[union-attr]


async def test_exchange_info_that_differs_only_in_server_time_is_not_parsed() -> None:
    stream, _, _, raw, _, _ = await build([])
    bodies = [exchange_info(["1000PEPEUSDT"], server_time=T0 + k) for k in (1, 2)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/fundingInfo":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=bodies.pop(0))

    seen = {}
    original = stream._parse

    def spy(resp: httpx.Response, url: str) -> Any:
        seen[url] = seen.get(url, 0) + 1
        return original(resp, url)

    stream._parse = spy  # type: ignore[method-assign]
    await stream.refresh(_client(handler))
    await stream.refresh(_client(handler))
    assert seen.get(EXCHANGE_INFO_URL, 0) == 0  # build 에서 같은 본문을 이미 파싱했다
    assert len(raw.keys("rest:/fapi/v1/exchangeInfo")) == 3  # 원문은 매번


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
async def test_rest_failures_are_classified_and_keep_the_list(
    status: int, kind: str
) -> None:
    stream, _, _, _, _, _ = await build([])
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
    stream, _, _, _, clock, _ = await build([])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/fundingInfo":
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json=exchange_info(["1000PEPEUSDT", "BTCUSDT"]))

    clock.now = T0 + FUNDING_INFO_INTERVAL_MS
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(handler))
    assert info.value.kind == "rate_limit" and stream.bases() == {"PEPE", "BTC"}
    clock.now += 1000
    with pytest.raises(ExchangeApiError):
        await stream.refresh(_client(handler))  # 실패했으니 다음 회차에 다시


# --- 구독·샤드 (§3.5) ---


async def test_subscribe_messages_carry_at_most_fifty_streams_spaced_and_per_shard() -> (
    None
):
    symbols = symbols_for(0, 120)
    socks = [GatedSocket() for _ in range(2)]
    stream, _, sleeps, _, _, _ = await build(list(socks), symbols=symbols)
    stream.start()
    await until(socks[0].subscribed)
    await asyncio.sleep(0.02)
    batches = params_of(socks[0])
    assert [len(b) for b in batches] == [50, 50, 20]
    assert all(
        n.endswith("@depth5@500ms") and n == n.lower() for b in batches for n in b
    )
    assert sleeps.values.count(CONTROL_INTERVAL) >= 3
    assert STREAMS_PER_MESSAGE == 50 and CONTROL_INTERVAL == 0.2
    assert [m["id"] for m in socks[0].subscriptions()] == [1, 2, 3]
    state = stream._store.stream_state(SRC)
    assert (
        state is not None and state.subscribed == 120
    )  # 펀딩 구독은 심볼 수에 안 든다
    await stream.aclose()


def test_symbols_spread_over_three_shards_and_hash_is_stable_across_processes() -> None:
    symbols = [f"T{i:04d}USDT" for i in range(50)] + ["BTCUSDT", "1000PEPEUSDT"]
    assert {shard_of(s) for s in symbols} == {0, 1, 2} and SHARDS == 3
    code = (
        "import json, sys; from app.core.streams.binance_perp import shard_of; "
        "print(json.dumps([shard_of(s) for s in sys.argv[1:]]))"
    )
    runs = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONPATH": str(SERVER_DIR), "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", code, *symbols],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=SERVER_DIR,
        )
        runs.append(json.loads(out.stdout))
    assert runs[0] == runs[1] == [shard_of(s) for s in symbols]


async def test_rebalance_subscribes_new_unsubscribes_dropped_and_removes_rows() -> None:
    a, b = symbols_for(0, 2)
    sock, funding = GatedSocket(), GatedSocket()
    stream, _, _, _, _, store = await build(
        [sock, funding], symbols=[a, b], universe={base_of(a)}
    )
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    sock.push(depth(a))
    await until(sock.delivered)
    assert store.perp_row(SRC, base_of(a)) is not None
    stream.set_universe({base_of(b)})
    await asyncio.sleep(0.02)
    assert params_of(sock, "UNSUBSCRIBE") == [[f"{a.lower()}@depth5@500ms"]]
    assert params_of(sock)[-1] == [f"{b.lower()}@depth5@500ms"]
    assert store.perp_row(SRC, base_of(a)) is None
    stream.set_universe({base_of(b)})  # 같은 우주 — 전송 0
    await asyncio.sleep(0.02)
    assert len(sock.sent) == 3
    await stream.aclose()


async def test_subscribe_rejection_is_bad_request_and_reconnects() -> None:
    first = FakeSocket(['{"code":2,"msg":"Invalid request"}'], hold=True)
    stream, connector, sleeps, _, _, _ = await build([first, FakeSocket([], hold=True)])
    stream.start()
    await asyncio.sleep(0.02)
    await stream.aclose()
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert (
        verdict.error.kind == "bad_request"
        and "2 Invalid request" in verdict.error.message
    )
    assert first.closed and backoffs(sleeps)[:1] == [1.0]


# --- 판정 (§3.8) ---


async def test_dead_funding_connection_fails_the_source() -> None:
    quote_sock = GatedSocket()
    stream, connector, _, _, clock, store = await build(
        [quote_sock, OSError("refused")]
    )
    stream.start()
    await until(quote_sock.subscribed)
    await asyncio.sleep(0.01)
    quote_sock.push(depth())
    await until(quote_sock.delivered)
    verdict = stream.judge(clock.now + 1000)
    assert verdict is not None and not verdict.ok and verdict.error is not None
    assert (
        verdict.error.kind == "network"
        and f"샤드 {FUNDING_SHARD}" in verdict.error.message
    )
    assert verdict.error.url == MARKET_WS_URL
    state = store.stream_state(SRC)
    assert state is not None and not state.connected and state.subscribed == 1
    await stream.aclose()


async def test_stale_funding_connection_fails_the_source_with_its_label() -> None:
    quote_sock, funding_sock = GatedSocket(), GatedSocket()
    stream, _, _, _, clock, _ = await build([quote_sock, funding_sock])
    stream.start()
    await until(quote_sock.subscribed)
    await until(funding_sock.subscribed)
    await asyncio.sleep(0.01)
    clock.now = T0 + 40_000
    quote_sock.push(depth())
    await until(quote_sock.delivered)
    verdict = stream.judge(clock.now)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.message == (
        f"Binance perp 스트림 정체: 샤드 {FUNDING_SHARD} (펀딩) 30초 이상 무수신"
    )
    funding_sock.push(mark([("1000PEPEUSDT", "0.01", "0.0001", T0)]))
    await until(funding_sock.delivered)
    assert stream.judge(clock.now + 1000).ok  # type: ignore[union-attr]
    await stream.aclose()


async def test_no_assignment_means_no_verdict_even_with_funding_connected() -> None:
    funding_sock = GatedSocket()
    stream, _, _, _, clock, _ = await build([funding_sock], universe=set())
    stream.start()
    await until(funding_sock.subscribed)
    assert stream.judge(clock.now + 1000) is None
    await stream.aclose()


async def test_quietest_shard_is_chosen_and_tie_picks_the_lower() -> None:
    per_shard = [symbols_for(k, 1) for k in range(SHARDS)]
    socks = [GatedSocket() for _ in range(SHARDS + 1)]
    stream, _, _, _, clock, _ = await build(
        list(socks), symbols=[s for g in per_shard for s in g]
    )
    stream.start()
    for s in socks:
        await until(s.subscribed)
    await asyncio.sleep(0.01)
    clock.now = T0 + 10_000
    socks[1].push(depth(per_shard[1][0]))
    await until(socks[1].delivered)
    clock.now = T0 + 20_000
    socks[2].push(depth(per_shard[2][0]))
    socks[3].push(mark([]))
    await until(socks[2].delivered)
    await until(socks[3].delivered)
    clock.now = T0 + 60_000
    verdict = stream.judge(clock.now)
    assert verdict is not None and verdict.error is not None
    assert "샤드 0" in verdict.error.message  # 수신 0 (동률이면 작은 번호)
    await stream.aclose()


# --- 연결 실패·백오프·종료 ---


@pytest.mark.parametrize(
    ("exc", "kind", "status"),
    [
        (HandshakeRejected(418), "banned", 418),
        (HandshakeRejected(403), "banned", 403),
        (HandshakeRejected(429), "rate_limit", 429),
        (HandshakeRejected(503), "unavailable", 503),
        (HandshakeRejected(400), "bad_request", 400),
        (TimeoutError(), "timeout", None),
        (OSError("refused"), "network", None),
    ],
)
async def test_connect_failures_are_classified(
    exc: BaseException, kind: str, status: int | None
) -> None:
    stream, connector, _, _, _, _ = await build([exc, FakeSocket([], hold=True)])
    stream.start()
    await asyncio.sleep(0.02)
    await stream.aclose()
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert (
        verdict.error.kind,
        verdict.error.status_code,
        verdict.error.retry_after_sec,
    ) == (kind, status, None)
    assert (
        f"샤드 {PEPE_SHARD}" in verdict.error.message
        and verdict.error.url == PUBLIC_WS_URL
    )


async def test_backoff_grows_to_thirty_and_resets_after_first_quote() -> None:
    failures: list[FakeSocket | BaseException] = [OSError("x") for _ in range(6)]
    # 우주를 비워 펀딩 연결만 연결하게 — 두 샤드가 번갈아 실패하면 백오프가 섞인다
    stream, connector, sleeps, _, _, _ = await build(
        [*failures, FakeSocket([mark([])])], universe=set()
    )
    await run_until_exhausted(stream, connector)
    assert backoffs(sleeps)[:7] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 1.0]


async def test_aclose_cancels_tasks_and_closes_all_sockets() -> None:
    socks = [GatedSocket() for _ in range(SHARDS + 1)]
    stream, _, _, _, _, store = await build(
        list(socks), symbols=[s for k in range(SHARDS) for s in symbols_for(k, 1)]
    )
    stream.start()
    for s in socks:
        await until(s.subscribed)
    await stream.aclose()
    assert all(s.closed for s in socks)
    state = store.stream_state(SRC)
    assert state is not None and not state.connected and state.subscribed == 0
