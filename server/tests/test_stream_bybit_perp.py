"""바이빗 perp 커넥터 — tickers snapshot/delta·instruments-info(linear) 필터·커서·주기·거부 유지·JSON 핑·샤드 판정·분류·종료 (스펙 046 §3.6, §4)."""

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

from app.core.errors import ExchangeApiError
from app.core.live_store import LiveStore
from app.core.perp import PerpSink
from app.core.streams.bybit_perp import (
    ARGS_PER_MESSAGE,
    CONTROL_INTERVAL,
    INSTRUMENTS_URL,
    PING_INTERVAL,
    PONG_TIMEOUT,
    SHARDS,
    WS_URL,
    BybitPerpStream,
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
SERVER_DIR = Path(__file__).resolve().parents[1]
SRC = "bybit_perp"
PEPE_SHARD = shard_of("1000PEPEUSDT")
ACK = '{"success":true,"ret_msg":"subscribe","conn_id":"c","op":"subscribe"}'
PONG = '{"success":true,"ret_msg":"pong","conn_id":"c","op":"ping"}'
REJECT = '{"success":false,"ret_msg":"error:handler not found,topic:tickers.NOPEUSDT","conn_id":"c","op":"subscribe"}'
REST_SOURCE = "rest:/v5/market/instruments-info"
WS_SOURCE = "ws:/v5/public/linear"


def symbols_for(shard: int, n: int) -> list[str]:
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


def inst(symbol: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "contractType": "LinearPerpetual",
        "status": "Trading",
        "baseCoin": base_of(symbol),
        "quoteCoin": "USDT",
        "fundingInterval": 480,
    }
    row.update(over)
    return row


def body(
    rows: list[dict[str, Any]], cursor: str = "", ret_code: int = 0, time_ms: int = T0
) -> dict[str, Any]:
    return {
        "retCode": ret_code,
        "retMsg": "OK" if ret_code == 0 else "Too many visits!",
        "result": {"category": "linear", "list": rows, "nextPageCursor": cursor},
        "retExtInfo": {},
        "time": time_ms,
    }


def ticker(
    symbol: str = "1000PEPEUSDT",
    kind: str = "snapshot",
    ts: int = T0,
    **fields: Any,
) -> str:
    data: dict[str, Any] = {"symbol": symbol}
    if kind == "snapshot":
        data.update(
            {
                "bid1Price": "0.0123",
                "bid1Size": "500",
                "ask1Price": "0.0124",
                "ask1Size": "700",
                "markPrice": "0.01235",
                "fundingRate": "0.0001",
                "nextFundingTime": str(T0 + 3_600_000),
                "fundingIntervalHour": "8",
            }
        )
    data.update(fields)
    return json.dumps(
        {"topic": f"tickers.{symbol}", "type": kind, "ts": ts, "cs": 1, "data": data}
    )


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def rest(
    rows: list[dict[str, Any]], calls: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(200, json=body(rows))

    return _client(handler)


class PerpSleeps(Sleeps):
    """핑 주기·pong 대기는 표를 줄 때만 진행한다 — 가짜 sleep 이 즉시 돌아오면 핑 루프가 폭주한다."""

    def __init__(self) -> None:
        super().__init__()
        self.gated = (PING_INTERVAL, PONG_TIMEOUT)
        self._ping = asyncio.Semaphore(0)

    def release_ping(self, n: int = 1) -> None:
        for _ in range(n):
            self._ping.release()

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)
        if seconds in self.gated:
            await self._ping.acquire()
        else:
            await asyncio.sleep(0)


class ClosableGatedSocket(GatedSocket):
    async def close(self) -> None:
        self.closed = True
        self._queue.put_nowait(OSError("closed by client"))  # type: ignore[arg-type]

    async def recv(self) -> str | bytes:
        frame = await self._queue.get()
        if isinstance(frame, BaseException):
            raise frame
        self.delivered.set()
        return frame


async def build(
    outcomes: list[FakeSocket | BaseException],
    *,
    symbols: list[str] | None = None,
    universe: set[str] | None = None,
    sleep: Sleeps | None = None,
) -> tuple[BybitPerpStream, FakeConnector, Sleeps, RawLog, Clock, LiveStore]:
    symbols = symbols if symbols is not None else ["1000PEPEUSDT"]
    store = LiveStore()
    sink = PerpSink(store)
    bases = {base_of(s).removeprefix("1000") for s in symbols}
    sink.set_universe(universe if universe is not None else bases)
    connector = FakeConnector(outcomes)
    sleeps = sleep if sleep is not None else PerpSleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = BybitPerpStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleeps, clock=clock
    )
    await stream.refresh(rest([inst(s) for s in symbols]))
    stream.set_universe(sink.universe)
    return stream, connector, sleeps, raw, clock, store


async def run_until_exhausted(
    stream: BybitPerpStream, connector: FakeConnector
) -> None:
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()


def backoffs(sleeps: Sleeps) -> list[float]:
    return [
        v
        for v in sleeps.values
        if v not in (CONTROL_INTERVAL, PING_INTERVAL, PONG_TIMEOUT)
    ]


def sent_ops(sock: FakeSocket, op: str) -> list[list[str]]:
    return [m["args"] for m in sock.subscriptions() if m["op"] == op]


# --- 프레임 → 행 (§3.4·§3.6) ---


async def test_snapshot_sets_quote_mark_and_funding_at_once() -> None:
    sock = FakeSocket([ACK, ticker()])
    stream, connector, _, raw, clock, store = await build([sock])
    clock.now = T0 + 5
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.native_symbol, row.multiplier) == ("1000PEPEUSDT", 1000)
    assert (row.bid, row.ask) == (0.0123 / 1000, 0.0124 / 1000)
    assert (row.bid_size, row.ask_size) == (500_000.0, 700_000.0)
    assert row.mark == 0.01235 / 1000 and row.funding_rate == 0.0001
    assert row.next_funding_ms == T0 + 3_600_000 and row.funding_interval_h == 8
    assert row.quote_ts == T0
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    assert sent_ops(sock, "subscribe") == [["tickers.1000PEPEUSDT"]]
    assert raw.keys(WS_SOURCE) == [None, "tickers:1000PEPEUSDT"]
    assert connector.urls[0] == WS_URL


async def test_delta_changes_only_the_fields_it_carries() -> None:
    frames = [
        ticker(),
        ticker(kind="delta", ts=T0 + 100, ask1Price="0.0130", fundingRate="0.0002"),
        ticker(
            kind="delta", ts=T0 + 200, markPrice="0.0140"
        ),  # 호가 필드 없음 — 호가 시각 그대로
        ticker(kind="delta", ts=T0 + 300, bid1Size="0"),  # 합친 결과가 무효 — 호가 불변
    ]
    sock = FakeSocket(frames)
    stream, connector, _, _, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.bid, row.ask) == (0.0123 / 1000, 0.0130 / 1000)
    assert (row.bid_size, row.ask_size) == (500_000.0, 700_000.0)
    assert row.quote_ts == T0 + 100
    assert row.funding_rate == 0.0002 and row.mark == 0.0140 / 1000
    assert stream.decode_failures == 0


async def test_delta_before_snapshot_cannot_create_a_row_but_holds_funding() -> None:
    sock = FakeSocket(
        [ticker(kind="delta", fundingRate="0.0003"), ticker(ts=T0 + 1, fundingRate="")]
    )
    stream, connector, _, _, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert (
        row is not None and row.funding_rate == 0.0003
    )  # 보류했다가 첫 스냅샷에 실렸다


async def test_snapshot_missing_a_quote_field_is_invalid() -> None:
    sock = FakeSocket([ticker(), ticker(ts=T0 + 50, ask1Size=None)])
    stream, connector, _, _, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.quote_ts == T0


# --- 목록 (§3.6) ---


async def test_list_keeps_only_linear_perpetual_trading_usdt_and_maps_interval() -> (
    None
):
    rows = [
        inst("1000PEPEUSDT", fundingInterval=240),
        inst("BTCPERP", quoteCoin="USDC", baseCoin="BTC"),
        inst("ETHUSDT-26DEC25", contractType="LinearFutures", baseCoin="ETH"),
        inst("XYZUSDT", status="PreLaunch"),
        inst("SHIB1000USDT", baseCoin="SHIB1000", fundingInterval=60),
        inst("SOLUSDT"),
    ]
    calls: list[httpx.Request] = []
    stream, _, _, raw, _, store = await build([])
    assert await stream.refresh(rest(rows, calls)) == 1
    assert stream.bases() == {"PEPE", "SHIB", "SOL"}
    assert str(calls[0].url) == INSTRUMENTS_URL
    assert raw.keys(REST_SOURCE) == ["symbols:perp"] * 2
    sink = stream._sink
    sink.set_universe({"PEPE", "SHIB", "SOL"})
    for base, symbol, mult in (
        ("PEPE", "1000PEPEUSDT", 1000),
        ("SHIB", "SHIB1000USDT", 1000),
        ("SOL", "SOLUSDT", 1),
    ):
        sink.quote(
            source=SRC,
            base=base,
            native_symbol=symbol,
            multiplier=mult,
            bid=1.0,
            ask=1.1,
            bid_size=1.0,
            ask_size=1.0,
            quote_ts=1,
            received_at_ms=1,
        )
    assert store.perp_row(SRC, "PEPE").funding_interval_h == 4  # type: ignore[union-attr]
    assert store.perp_row(SRC, "SHIB").funding_interval_h == 1  # type: ignore[union-attr]
    assert store.perp_row(SRC, "SOL").funding_interval_h == 8  # type: ignore[union-attr]


async def test_next_page_cursor_is_followed_and_pages_count_as_calls() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if b"cursor=" in request.url.query:
            return httpx.Response(200, json=body([inst("SOLUSDT")]))
        return httpx.Response(200, json=body([inst("BTCUSDT")], cursor="p2"))

    stream, _, _, raw, _, _ = await build([])
    assert await stream.refresh(_client(handler)) == 2
    assert stream.bases() == {"BTC", "SOL"}
    assert calls[1].url.query.endswith(b"cursor=p2")
    assert raw.keys(REST_SOURCE) == ["symbols:perp"] * 3
    # 같은 두 페이지가 다시 오면(시각만 다름) 파싱 없이 맵 그대로
    parsed = 0
    original = stream._parse_page

    def spy(resp: httpx.Response, url: str) -> Any:
        nonlocal parsed
        parsed += 1
        return original(resp, url)

    stream._parse_page = spy  # type: ignore[method-assign]
    assert await stream.refresh(_client(handler)) == 2
    assert parsed == 0 and stream.bases() == {"BTC", "SOL"}


async def test_rest_ret_code_and_status_failures_keep_the_list() -> None:
    stream, _, _, _, _, _ = await build([])
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(
            _client(lambda r: httpx.Response(200, json=body([], ret_code=10006)))
        )
    assert info.value.kind == "rate_limit"
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(
            _client(lambda r: httpx.Response(200, json=body([], ret_code=10001)))
        )
    assert info.value.kind == "bad_response"
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(lambda r: httpx.Response(403, text="blocked")))
    assert info.value.kind == "banned"
    assert stream.bases() == {"PEPE"}


# --- 구독·거부·핑 (§3.6) ---


async def test_subscribe_batches_fifty_args_with_control_interval() -> None:
    symbols = symbols_for(0, 120)
    sock = GatedSocket()
    stream, _, sleeps, _, _, _ = await build([sock], symbols=symbols)
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.02)
    batches = sent_ops(sock, "subscribe")
    assert [len(b) for b in batches] == [50, 50, 20] and ARGS_PER_MESSAGE == 50
    assert all(a.startswith("tickers.") for b in batches for a in b)
    assert sleeps.values.count(CONTROL_INTERVAL) == 3 and CONTROL_INTERVAL == 0.2
    await stream.aclose()


async def test_rejection_is_recorded_but_the_connection_stays() -> None:
    sock = GatedSocket()
    stream, _, _, _, clock, store = await build([sock])
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    sock.push(REJECT)
    await until(sock.delivered)
    sock.push(ticker())
    await until(sock.delivered)
    assert not sock.closed
    state = store.stream_state(SRC)
    assert state is not None and state.connected and state.last_message_at == T0
    assert stream.judge(clock.now + 1000).ok  # type: ignore[union-attr]
    await stream.aclose()
    verdict = stream.judge(clock.now)  # 종료 뒤 — 마지막 기록이 거부
    assert verdict is not None and verdict.error is not None
    assert (
        verdict.error.kind == "bad_request"
        and "handler not found" in verdict.error.message
    )


async def test_ping_every_twenty_seconds_and_pong_clears_the_wait() -> None:
    sock = ClosableGatedSocket()
    sleeps = PerpSleeps()
    stream, _, _, raw, _, store = await build([sock], sleep=sleeps)
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    sleeps.release_ping()
    await asyncio.sleep(0.01)
    assert json.loads(sock.sent[-1]) == {"op": "ping"} and PING_INTERVAL == 20.0
    sock.push(PONG)
    await until(sock.delivered)
    sleeps.release_ping()
    await asyncio.sleep(0.01)
    assert not sock.closed
    state = store.stream_state(SRC)
    assert state is not None and state.last_message_at is None  # pong 은 시세가 아니다
    assert raw.keys(WS_SOURCE) == [None]
    await stream.aclose()


async def test_missing_pong_closes_and_reconnects_with_timeout_kind() -> None:
    first, second = ClosableGatedSocket(), GatedSocket()
    sleeps = PerpSleeps()
    stream, connector, _, _, _, _ = await build([first, second], sleep=sleeps)
    stream.start()
    await until(first.subscribed)
    await asyncio.sleep(0.01)
    sleeps.release_ping()
    await asyncio.sleep(0.01)
    sleeps.release_ping()
    await until(second.subscribed)
    assert first.closed and connector.urls == [WS_URL, WS_URL]
    await stream.aclose()
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "timeout" and "pong" in verdict.error.message


# --- 판정·분류·종료 ---


async def test_only_the_silent_shard_fails_and_recovers() -> None:
    per_shard = [symbols_for(k, 2) for k in range(SHARDS)]
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, _, _, _, clock, store = await build(
        list(socks), symbols=[s for g in per_shard for s in g]
    )
    stream.start()
    for s in socks:
        await until(s.subscribed)
    await asyncio.sleep(0.01)
    clock.now = T0 + 40_000
    for k in (0, 1):
        socks[k].push(ticker(per_shard[k][0]))
        await until(socks[k].delivered)
    socks[2].push(PONG)
    await until(socks[2].delivered)
    verdict = stream.judge(clock.now)
    assert verdict is not None and verdict.error is not None
    assert (
        verdict.error.message
        == "Bybit perp 스트림 정체: 샤드 2 (구독 2종목) 30초 이상 무수신"
    )
    assert (verdict.error.kind, verdict.error.url) == ("stale_stream", WS_URL)
    state = store.stream_state(SRC)
    assert (
        state is not None
        and state.subscribed == 6
        and state.last_error is verdict.error
    )
    socks[2].push(ticker(per_shard[2][0]))
    await until(socks[2].delivered)
    assert stream.judge(clock.now + 1000).ok  # type: ignore[union-attr]
    await stream.aclose()


def test_shard_hash_is_stable_across_processes() -> None:
    symbols = [f"T{i:04d}USDT" for i in range(50)] + ["BTCUSDT", "1000PEPEUSDT"]
    code = (
        "import json, sys; from app.core.streams.bybit_perp import shard_of; "
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


@pytest.mark.parametrize(
    ("exc", "kind", "status"),
    [
        (HandshakeRejected(403), "banned", 403),
        (HandshakeRejected(429), "rate_limit", 429),
        (HandshakeRejected(502), "unavailable", 502),
        (HandshakeRejected(404), "bad_request", 404),
        (TimeoutError(), "timeout", None),
        (OSError("refused"), "network", None),
    ],
)
async def test_connect_failures_are_classified(
    exc: BaseException, kind: str, status: int | None
) -> None:
    stream, connector, _, _, _, _ = await build([exc])
    await run_until_exhausted(stream, connector)
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert (verdict.error.kind, verdict.error.status_code, verdict.error.url) == (
        kind,
        status,
        WS_URL,
    )
    assert f"샤드 {PEPE_SHARD}" in verdict.error.message


async def test_backoff_grows_to_thirty_and_resets_after_first_quote() -> None:
    failures: list[FakeSocket | BaseException] = [OSError("x") for _ in range(6)]
    stream, connector, sleeps, _, _, _ = await build(
        [*failures, FakeSocket([ticker()])]
    )
    await run_until_exhausted(stream, connector)
    assert backoffs(sleeps)[:7] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 1.0]


async def test_rebalance_and_aclose() -> None:
    a, b = symbols_for(0, 2)
    sock = GatedSocket()
    stream, _, _, _, _, store = await build(
        [sock], symbols=[a, b], universe={base_of(a)}
    )
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    sock.push(ticker(a))
    await until(sock.delivered)
    assert store.perp_row(SRC, base_of(a)) is not None
    stream.set_universe({base_of(b)})
    await asyncio.sleep(0.02)
    assert sent_ops(sock, "unsubscribe") == [[f"tickers.{a}"]]
    assert sent_ops(sock, "subscribe")[-1] == [f"tickers.{b}"]
    assert store.perp_row(SRC, base_of(a)) is None
    await stream.aclose()
    assert sock.closed
    state = store.stream_state(SRC)
    assert state is not None and not state.connected and state.subscribed == 0
