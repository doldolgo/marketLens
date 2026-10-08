"""비트겟 perp 커넥터 — ticker 스냅샷·contracts 필터·주기·숫자 code 거부 유지·문자열 핑·구독 예산·샤드 판정·분류·종료 (스펙 046 §3.7, §4)."""

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
from app.core.streams.bitget_perp import (
    ARGS_PER_MESSAGE,
    CONTRACTS_URL,
    CONTROL_INTERVAL,
    PING_INTERVAL,
    PONG_TIMEOUT,
    SHARDS,
    SUBSCRIBE_WARN_AT,
    WS_URL,
    BitgetPerpStream,
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
SRC = "bitget_perp"
PEPE_SHARD = shard_of("1000PEPEUSDT")
ACK = '{"event":"subscribe","arg":{"instType":"USDT-FUTURES","channel":"ticker","instId":"1000PEPEUSDT"}}'
REJECT = '{"event":"error","arg":{"instType":"USDT-FUTURES","channel":"ticker","instId":"NOPEUSDT"},"code":30001,"msg":"instId:NOPEUSDT doesn\'t exist"}'
REST_SOURCE = "rest:/api/v2/mix/market/contracts"
WS_SOURCE = "ws:/v2/ws/public"


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


def contract(symbol: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "baseCoin": base_of(symbol),
        "quoteCoin": "USDT",
        "symbolType": "perpetual",
        "symbolStatus": "normal",
        "fundInterval": "8",
    }
    row.update(over)
    return row


def body(
    rows: list[dict[str, Any]], code: str = "00000", request_time: int = T0
) -> dict[str, Any]:
    return {
        "code": code,
        "msg": "success" if code == "00000" else "fail",
        "requestTime": request_time,
        "data": rows,
    }


def arg(symbol: str) -> dict[str, str]:
    return {"instType": "USDT-FUTURES", "channel": "ticker", "instId": symbol}


def ticker(symbol: str = "1000PEPEUSDT", ts: int = T0, **fields: Any) -> str:
    item: dict[str, Any] = {
        "instId": symbol,
        "lastPr": "0.01235",
        "bidPr": "0.0123",
        "askPr": "0.0124",
        "bidSz": "500",
        "askSz": "700",
        "markPrice": "0.01235",
        "indexPrice": "0.01235",
        "fundingRate": "0.0001",
        "nextFundingTime": str(T0 + 3_600_000),
        "symbolType": "1",
        "ts": str(ts),
    }
    item.update(fields)
    return json.dumps(
        {"action": "snapshot", "arg": arg(symbol), "data": [item], "ts": ts}
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
    def __init__(
        self,
        control_tickets: int | None = None,
        gated: tuple[float, ...] = (PING_INTERVAL, PONG_TIMEOUT),
    ) -> None:
        super().__init__()
        self.gated = (
            gated  # 표로 막는 초 값 — 백오프 상한 30초와 겹치면 테스트가 바꾼다
        )
        self._ping = asyncio.Semaphore(0)
        self._control = (
            asyncio.Semaphore(control_tickets) if control_tickets is not None else None
        )

    def release_ping(self, n: int = 1) -> None:
        for _ in range(n):
            self._ping.release()

    def release_control(self, n: int = 1) -> None:
        assert self._control is not None
        for _ in range(n):
            self._control.release()

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)
        if seconds in self.gated:
            await self._ping.acquire()
        elif seconds == CONTROL_INTERVAL and self._control is not None:
            await self._control.acquire()
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
) -> tuple[BitgetPerpStream, FakeConnector, Sleeps, RawLog, Clock, LiveStore]:
    symbols = symbols if symbols is not None else ["1000PEPEUSDT"]
    store = LiveStore()
    sink = PerpSink(store)
    bases = {base_of(s).removeprefix("1000") for s in symbols}
    sink.set_universe(universe if universe is not None else bases)
    connector = FakeConnector(outcomes)
    sleeps = sleep if sleep is not None else PerpSleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = BitgetPerpStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleeps, clock=clock
    )
    await stream.refresh(rest([contract(s) for s in symbols]))
    stream.set_universe(sink.universe)
    return stream, connector, sleeps, raw, clock, store


async def run_until_exhausted(
    stream: BitgetPerpStream, connector: FakeConnector
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


def sent_ops(sock: FakeSocket, op: str) -> list[list[dict[str, str]]]:
    return [m["args"] for m in sock.subscriptions() if m["op"] == op]


# --- 프레임 → 행 (§3.4·§3.7) ---


async def test_ticker_snapshot_sets_quote_mark_and_funding_at_once() -> None:
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
    assert sent_ops(sock, "subscribe") == [[arg("1000PEPEUSDT")]]
    assert raw.keys(WS_SOURCE) == [None, "ticker:1000PEPEUSDT"]
    assert connector.urls[0] == WS_URL


async def test_symbol_type_two_is_dropped_and_missing_quote_is_invalid() -> None:
    sock = FakeSocket(
        [
            ticker(),
            ticker(ts=T0 + 10, symbolType="2", bidPr="9"),
            ticker(ts=T0 + 20, askSz=None),
        ]
    )
    stream, connector, _, _, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.quote_ts == T0 and row.bid == 0.0123 / 1000
    assert stream.decode_failures == 0


async def test_funding_fields_that_are_not_numbers_are_left_alone() -> None:
    sock = FakeSocket(
        [
            ticker(),
            ticker(
                ts=T0 + 10, fundingRate="", markPrice="x", nextFundingTime=str(T0 + 9)
            ),
        ]
    )
    stream, connector, _, _, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (
        row.funding_rate == 0.0001
        and row.mark == 0.01235 / 1000
        and row.next_funding_ms == T0 + 9
    )


# --- 목록 (§3.7) ---


async def test_contracts_keeps_perpetual_normal_usdt_and_maps_interval() -> None:
    rows = [
        contract("1000BONKUSDT", baseCoin="1000BONK", fundInterval="4"),
        contract(
            "PEPEUSDT", baseCoin="PEPE", fundInterval="1"
        ),  # 비트겟은 배수 없이 낸다
        contract("BTCUSDT-DELIVERY", symbolType="delivery", baseCoin="BTC"),
        contract("XYZUSDT", symbolStatus="maintain"),
        contract("ETHUSDC", quoteCoin="USDC", baseCoin="ETH"),
        contract("TSLAUSDT"),  # 토큰화 주식 — 거르지 않는다
    ]
    calls: list[httpx.Request] = []
    stream, _, _, raw, _, store = await build([])
    assert await stream.refresh(rest(rows, calls)) == 1
    assert stream.bases() == {"BONK", "PEPE", "TSLA"}
    assert str(calls[0].url) == CONTRACTS_URL
    assert raw.keys(REST_SOURCE) == ["symbols:perp"] * 2
    sink = stream._sink
    sink.set_universe({"BONK", "PEPE"})
    for base, symbol, mult in (("BONK", "1000BONKUSDT", 1000), ("PEPE", "PEPEUSDT", 1)):
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
    assert store.perp_row(SRC, "BONK").funding_interval_h == 4  # type: ignore[union-attr]
    assert store.perp_row(SRC, "PEPE").funding_interval_h == 1  # type: ignore[union-attr]


async def test_contracts_that_differ_only_in_request_time_are_not_parsed() -> None:
    stream, _, _, raw, _, _ = await build([])
    bodies = [body([contract("1000PEPEUSDT")], request_time=T0 + k) for k in (1, 2)]
    stream._symbols_body = None  # build 의 응답과 바이트가 같으므로 한 번은 파싱하게
    seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bodies.pop(0))

    original = json.loads
    await stream.refresh(_client(handler))
    before = len(raw.keys(REST_SOURCE))
    # 두 번째: requestTime 만 다르다 — 원문은 기록되지만 맵은 다시 만들지 않는다(심볼 맵 객체 동일)
    symbol_map = stream._symbol_of
    await stream.refresh(_client(handler))
    assert stream._symbol_of is symbol_map
    assert len(raw.keys(REST_SOURCE)) == before + 1
    assert original is json.loads and seen == 0


async def test_rest_code_and_status_failures_keep_the_list() -> None:
    stream, _, _, _, _, _ = await build([])
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(
            _client(lambda r: httpx.Response(200, json=body([], code="40001")))
        )
    assert info.value.kind == "bad_response"
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(lambda r: httpx.Response(429, text="slow")))
    assert info.value.kind == "rate_limit"
    assert stream.bases() == {"PEPE"}


# --- 구독·거부·핑·예산 (§3.7) ---


async def test_subscribe_batches_fifty_args_under_4096_bytes() -> None:
    symbols = symbols_for(0, 120)
    sock = GatedSocket()
    stream, _, sleeps, _, _, _ = await build([sock], symbols=symbols)
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.02)
    batches = sent_ops(sock, "subscribe")
    assert [len(b) for b in batches] == [50, 50, 20] and ARGS_PER_MESSAGE == 50
    assert all(len(json.dumps(b)) < 4096 for b in batches)
    assert batches[0][0] == arg(sorted(symbols)[0])
    assert sleeps.values.count(CONTROL_INTERVAL) == 3
    await stream.aclose()


async def test_numeric_code_rejection_is_recorded_and_the_connection_stays() -> None:
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
    assert stream.judge(clock.now + 1000).ok  # type: ignore[union-attr]
    state = store.stream_state(SRC)
    assert state is not None and state.connected
    await stream.aclose()
    verdict = stream.judge(clock.now)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "bad_request" and "code 30001" in verdict.error.message


async def test_string_ping_every_thirty_seconds_and_pong_within_ten() -> None:
    sock = ClosableGatedSocket()
    sleeps = PerpSleeps()
    stream, _, _, raw, _, store = await build([sock], sleep=sleeps)
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    sleeps.release_ping()
    await asyncio.sleep(0.01)
    assert sock.sent[-1] == "ping" and PING_INTERVAL == 30.0 and PONG_TIMEOUT == 10.0
    sock.push("pong")
    await until(sock.delivered)
    sleeps.release_ping()
    await asyncio.sleep(0.01)
    assert not sock.closed
    state = store.stream_state(SRC)
    assert state is not None and state.last_message_at is None
    assert raw.payloads(WS_SOURCE) == ["pong"] and raw.keys(WS_SOURCE) == [None]
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


async def test_subscribe_budget_defers_the_193rd_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sock = GatedSocket()
    stream, _, _, _, clock, _ = await build([sock], symbols=["1000PEPEUSDT"])
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    shard = stream._shards[PEPE_SHARD]
    shard.requests_at.extend(
        [clock.now] * (SUBSCRIBE_WARN_AT - 1)
    )  # 1 + 191 = 192회째까지 보냈다
    sent = len(sock.sent)
    stream.set_universe(set())
    with caplog.at_level("WARNING", logger="marketlens.stream.bitget_perp"):
        await asyncio.sleep(0.02)
    assert len(sock.sent) == sent  # 193회째 — 미룬다
    assert sum("80%" in r.getMessage() for r in caplog.records) == 1
    clock.now += 3_600_000  # 창이 지나면 보낸다
    stream._wake.set()
    await asyncio.sleep(0.02)
    assert len(sock.sent) == sent + 1 and sent_ops(sock, "unsubscribe") == [
        [arg("1000PEPEUSDT")]
    ]
    await stream.aclose()


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
    socks[2].push("pong")
    await until(socks[2].delivered)
    verdict = stream.judge(clock.now)
    assert verdict is not None and verdict.error is not None
    assert (
        verdict.error.message
        == "Bitget perp 스트림 정체: 샤드 2 (구독 2종목) 30초 이상 무수신"
    )
    state = store.stream_state(SRC)
    assert state is not None and state.subscribed == 6
    socks[2].push(ticker(per_shard[2][0]))
    await until(socks[2].delivered)
    assert stream.judge(clock.now + 1000).ok  # type: ignore[union-attr]
    await stream.aclose()


def test_shard_hash_is_stable_across_processes() -> None:
    symbols = [f"T{i:04d}USDT" for i in range(50)] + ["BTCUSDT", "1000PEPEUSDT"]
    code = (
        "import json, sys; from app.core.streams.bitget_perp import shard_of; "
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
        (HandshakeRejected(503), "unavailable", 503),
        (HandshakeRejected(400), "bad_request", 400),
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
    # 핑 주기 30초가 백오프 상한과 같다 — pong 대기만 막고 백오프 30 은 지나가게
    sleeps = PerpSleeps(gated=(PONG_TIMEOUT,))
    stream, connector, _, _, _, _ = await build(
        [*failures, FakeSocket([ticker()])], sleep=sleeps
    )
    await run_until_exhausted(stream, connector)
    waits = [v for v in sleeps.values if v not in (CONTROL_INTERVAL, PONG_TIMEOUT)]
    assert waits[:6] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]
    # 연결 뒤에는 핑 주기(30)가 섞인다 — 첫 시세 뒤 재연결 대기가 1 로 돌아온 것만 본다
    assert 1.0 in waits[6:]


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
    assert sent_ops(sock, "unsubscribe") == [[arg(a)]]
    assert sent_ops(sock, "subscribe")[-1] == [arg(b)]
    assert store.perp_row(SRC, base_of(a)) is None
    await stream.aclose()
    assert sock.closed
    state = store.stream_state(SRC)
    assert state is not None and not state.connected and state.subscribed == 0
