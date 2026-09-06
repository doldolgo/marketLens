"""바이낸스 스트림 커넥터 — 행 갱신·심볼 목록·샤딩·구독 한도·샤드 판정·재연결·종료 (스펙 012 §4)."""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.streams.binance import (
    CONTROL_INTERVAL,
    EXCHANGE_INFO_PATH,
    PARAMS_PER_MESSAGE,
    REST_URL,
    SHARDS,
    WS_URL,
    BinanceStream,
    shard_of,
)
from app.main import create_app
from tests.conftest import RawLog
from tests.stream_fakes import (
    Clock,
    FakeConnector,
    FakeSocket,
    GatedSocket,
    HandshakeRejected,
    HangingCloseSocket,
    Sleeps,
    store_with_universe,
    until,
)

T0 = 1_787_727_947_000
STALE_LIMIT = 30_000
SERVER_DIR = Path(__file__).resolve().parents[1]
BTC_SHARD = shard_of("BTCUSDT")

ACK = '{"result":null,"id":1}'
SHUTDOWN = '{"stream":"!serverShutdown"}'
REST_SOURCE = "rest:/api/v3/exchangeInfo"
WS_SOURCE = "ws:/stream"


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


def exchange_info(
    symbols: list[str], extra: list[dict[str, str]] | None = None
) -> dict:  # type: ignore[type-arg]
    rows = [
        {
            "symbol": s,
            "status": "TRADING",
            "baseAsset": base_of(s),
            "quoteAsset": "USDT",
        }
        for s in symbols
    ]
    return {"timezone": "UTC", "symbols": rows + (extra or [])}


def depth(
    symbol: str = "BTCUSDT",
    levels: int = 20,
    price: float = 71_000.0,
    size: float = 0.1,
) -> str:
    asks = [[f"{price + i * 10:.2f}", f"{size}"] for i in range(levels)]
    bids = [[f"{price - 10 - i * 10:.2f}", f"{size}"] for i in range(levels)]
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@depth20",
            "data": {"lastUpdateId": 160, "bids": bids, "asks": asks},
        }
    )


def mini(symbol: str = "BTCUSDT", price: float = 70_995.5, ts: int = T0) -> str:
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@miniTicker",
            "data": {"e": "24hrMiniTicker", "E": ts, "s": symbol, "c": str(price)},
        }
    )


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TicketSleeps(Sleeps):
    """제어 메시지 간격(0.25초)마다 표 하나를 기다린다 — 전송 도중의 시점을 테스트가 고른다."""

    def __init__(self, tickets: int = 0) -> None:
        super().__init__()
        self._tickets = asyncio.Semaphore(tickets)

    def release(self, n: int = 1) -> None:
        for _ in range(n):
            self._tickets.release()

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)
        if seconds == CONTROL_INTERVAL:
            await self._tickets.acquire()
        else:
            await asyncio.sleep(0)


async def build(
    outcomes: list[FakeSocket | BaseException],
    *,
    symbols: list[str] | None = None,
    universe: set[str] | None = None,
    sleep: Sleeps | None = None,
) -> tuple[BinanceStream, FakeConnector, Sleeps, RawLog, Clock, LiveStore]:
    """exchangeInfo(fake REST) 로 심볼 맵을 채우고 우주를 넣은 커넥터 — 배정 있는 샤드만 연결한다."""
    symbols = symbols if symbols is not None else ["BTCUSDT"]
    bases = {base_of(s) for s in symbols}
    store, sink = store_with_universe(universe if universe is not None else bases)
    connector = FakeConnector(outcomes)
    sleeps = sleep if sleep is not None else Sleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = BinanceStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleeps, clock=clock
    )
    await stream.refresh(
        _client(lambda r: httpx.Response(200, json=exchange_info(symbols)))
    )
    stream.set_universe(sink.universe)
    return stream, connector, sleeps, raw, clock, store


async def run_until_exhausted(stream: BinanceStream, connector: FakeConnector) -> None:
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()


def backoffs(sleeps: Sleeps) -> list[float]:
    """제어 메시지 간격을 뺀 나머지 = 재연결 대기."""
    return [v for v in sleeps.values if v != CONTROL_INTERVAL]


def subscribe_params(sock: FakeSocket, method: str = "SUBSCRIBE") -> list[list[str]]:
    return [m["params"] for m in sock.subscriptions() if m["method"] == method]


# --- 행 갱신 (§3.4) ---


async def test_depth20_replaces_levels_as_sorted_floats_and_updates_row() -> None:
    sock = FakeSocket([depth(levels=20)])
    stream, connector, _, _, clock, store = await build([sock])
    clock.now = T0 + 5
    await run_until_exhausted(stream, connector)
    row = store.get("binance", "BTC")
    assert row is not None
    assert len(row.asks) == 20 and len(row.bids) == 20
    assert all(isinstance(v, float) for lv in row.asks + row.bids for v in lv)
    assert row.asks == sorted(row.asks) and row.bids == sorted(row.bids, reverse=True)
    assert row.asks[0] == [71_000.0, 0.1] and row.bids[0] == [70_990.0, 0.1]
    assert (row.native_symbol, row.quote) == ("BTCUSDT", "USDT")
    assert row.updated_at is not None
    assert int(row.updated_at.timestamp() * 1000) == T0 + 5
    [sub] = sock.subscriptions()
    assert sub == {
        "method": "SUBSCRIBE",
        "params": ["btcusdt@depth20", "btcusdt@miniTicker"],
        "id": 1,
    }
    assert sock.closed


async def test_depth20_is_cut_at_one_million_usdt_but_keeps_first_level() -> None:
    # 단계당 1,000 × 300 = 300,000 USDT → 4단계에서 누적 1,200,000 도달
    frames = [depth(price=1_000.0, size=300.0), depth(price=100.0, size=20_000.0)]
    stream, connector, _, _, _, store = await build([FakeSocket([frames[0]])])
    await run_until_exhausted(stream, connector)
    row = store.get("binance", "BTC")
    assert row is not None and len(row.asks) == 4 and len(row.bids) == 4
    stream, connector, _, _, _, store = await build([FakeSocket([frames[1]])])
    await run_until_exhausted(stream, connector)
    row = store.get("binance", "BTC")
    assert (
        row is not None and len(row.asks) == 1 and len(row.bids) == 1
    )  # 첫 단계 2,000,000


async def test_miniticker_updates_price_and_is_held_until_depth() -> None:
    sock = FakeSocket([mini(price=1.0, ts=T0 - 9), depth(), mini(price=2.0)])
    stream, connector, _, _, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    row = store.get("binance", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (2.0, T0)
    assert len(row.asks) == 20  # 체결가는 호가를 건드리지 않는다


async def test_held_trade_is_applied_with_first_depth_and_mid_without_trade() -> None:
    stream, connector, _, _, _, store = await build(
        [FakeSocket([mini(price=7.0, ts=T0 - 9), depth()])]
    )
    await run_until_exhausted(stream, connector)
    row = store.get("binance", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (7.0, T0 - 9)
    stream, connector, _, _, clock, store = await build([FakeSocket([depth()])])
    clock.now = T0 + 3
    await run_until_exhausted(stream, connector)
    row = store.get("binance", "BTC")
    assert row is not None
    assert row.price == (71_000.0 + 70_990.0) / 2  # 체결가 없으면 mid, 시각은 수신 시각
    assert row.price_timestamp == T0 + 3


# --- 심볼 목록 (§3.3) ---


async def test_exchange_info_keeps_only_trading_usdt_symbols() -> None:
    store, sink = store_with_universe({"BTC", "ETH", "SOL", "XRP"})
    raw = RawLog()
    stream = BinanceStream(store=store, sink=sink, record=raw, clock=Clock(T0))
    body = exchange_info(
        ["BTCUSDT"],
        [
            {
                "symbol": "ETHUSDT",
                "status": "BREAK",
                "baseAsset": "ETH",
                "quoteAsset": "USDT",
            },
            {
                "symbol": "SOLBTC",
                "status": "TRADING",
                "baseAsset": "SOL",
                "quoteAsset": "BTC",
            },
            {
                "symbol": "XRPUSDT",
                "status": "TRADING",
                "baseAsset": "XRP",
                "quoteAsset": "USDT",
            },
        ],
    )
    assert await stream.refresh(_client(lambda r: httpx.Response(200, json=body))) == 1
    assert stream.bases() == {"BTC", "XRP"}
    [entry] = raw.entries
    assert entry[:3] == ("binance", REST_SOURCE, T0)
    assert json.loads(entry[3]) == body


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
async def test_exchange_info_non_200_is_classified_by_binance_rule(
    status: int, kind: str
) -> None:
    stream = BinanceStream(store=LiveStore(), sink=store_with_universe(set())[1])
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(
            _client(
                lambda r: httpx.Response(
                    status, text="x" * 600, headers={"Retry-After": "7"}
                )
            )
        )
    exc = info.value
    assert (exc.kind, exc.status_code, exc.http_status, exc.retry_after_sec) == (
        kind,
        status,
        502,
        7,
    )
    assert exc.body is not None and len(exc.body) == 500
    assert exc.url == REST_URL + EXCHANGE_INFO_PATH
    assert stream.bases() == set()  # 실패 시 직전 목록 유지


async def test_exchange_info_connect_error_is_network() -> None:
    # httpx 전송 예외(DNS·연결 거부) → network, status_code 없음, url 은 REST URL (011 §3.2·§4)
    stream = BinanceStream(store=LiveStore(), sink=store_with_universe(set())[1])

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(down))
    exc = info.value
    assert (exc.kind, exc.status_code, exc.body, exc.url) == (
        "network",
        None,
        None,
        REST_URL + EXCHANGE_INFO_PATH,
    )
    assert stream.bases() == set()


async def test_exchange_info_429_without_retry_after_leaves_it_null() -> None:
    # 헤더 `Retry-After` 가 없으면 retry_after_sec 는 null 이다 (011 §3.2·§4)
    stream = BinanceStream(store=LiveStore(), sink=store_with_universe(set())[1])
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(
            _client(lambda r: httpx.Response(429, json={"code": -1003, "msg": "x"}))
        )
    assert (info.value.kind, info.value.retry_after_sec) == ("rate_limit", None)


async def test_exchange_info_timeout_and_bad_json() -> None:
    stream = BinanceStream(store=LiveStore(), sink=store_with_universe(set())[1])

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ExchangeTimeoutError) as info:
        await stream.refresh(_client(slow))
    assert (info.value.kind, info.value.http_status, info.value.url) == (
        "timeout",
        504,
        REST_URL + EXCHANGE_INFO_PATH,
    )
    with pytest.raises(ExchangeApiError) as bad:
        await stream.refresh(_client(lambda r: httpx.Response(200, text="<html>")))
    assert bad.value.kind == "bad_response"
    with pytest.raises(ExchangeApiError) as shape:
        await stream.refresh(
            _client(lambda r: httpx.Response(200, json={"symbols": 1}))
        )
    assert shape.value.kind == "bad_response"


async def test_messages_outside_universe_or_symbol_map_are_dropped() -> None:
    # ETH 는 맵에 있지만 우주 밖, ZZZ 는 맵에 없다 — 둘 다 행이 없고 ZZZ 는 시세로도 안 센다
    sock = FakeSocket([depth("ZZZUSDT"), depth("ETHUSDT")])
    stream, connector, _, _, _, store = await build(
        [sock], symbols=["BTCUSDT", "ETHUSDT"], universe={"BTC"}
    )
    stream.start()
    await until(sock.drained)
    assert store.get("binance", "ETH") is None and store.get("binance", "ZZZ") is None
    assert stream.decode_failures == 0
    await stream.aclose()


# --- 샤딩과 구독 (§3.3) ---


def test_symbols_spread_over_three_shards_and_sibling_streams_share_one() -> None:
    symbols = [f"T{i:04d}USDT" for i in range(300)]
    counts = [sum(1 for s in symbols if shard_of(s) == k) for k in range(SHARDS)]
    assert sum(counts) == 300 and all(c > 50 for c in counts)
    assert all(shard_of(s) == shard_of(s.lower()) for s in symbols)


def test_shard_hash_is_stable_across_processes() -> None:
    symbols = [f"T{i:04d}USDT" for i in range(50)] + ["BTCUSDT", "ETHUSDT"]
    code = (
        "import json, sys; from app.core.streams.binance import shard_of; "
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


async def test_subscribe_messages_are_chunked_and_rate_limited() -> None:
    symbols = symbols_for(BTC_SHARD, 120)  # 240 스트림 → 100·100·40
    sock = FakeSocket([], hold=True)
    stream, _, sleeps, _, _, _ = await build([sock], symbols=symbols)
    stream.start()
    await asyncio.sleep(0.01)
    subs = sock.subscriptions()
    assert [len(m["params"]) for m in subs] == [100, 100, 40]
    assert [m["id"] for m in subs] == [1, 2, 3]
    assert all(len(m["params"]) <= PARAMS_PER_MESSAGE for m in subs)
    sent = [p for m in subs for p in m["params"]]
    for s in symbols:
        assert f"{s.lower()}@depth20" in sent and f"{s.lower()}@miniTicker" in sent
    # 제어 메시지마다 0.25초 — 초당 4개 이하
    assert sleeps.values.count(CONTROL_INTERVAL) >= len(subs)
    await stream.aclose()


async def test_every_symbol_is_subscribed_on_the_socket_of_its_shard() -> None:
    per_shard = [symbols_for(k, 3) for k in range(SHARDS)]
    socks = [FakeSocket([], hold=True) for _ in range(SHARDS)]
    stream, _, _, _, _, _ = await build(
        list(socks), symbols=[s for g in per_shard for s in g]
    )
    stream.start()
    await asyncio.sleep(0.01)
    for k in range(SHARDS):
        [params] = subscribe_params(socks[k])
        expected = sorted(
            n
            for s in per_shard[k]
            for n in (f"{s.lower()}@depth20", f"{s.lower()}@miniTicker")
        )
        assert sorted(params) == expected  # 심볼의 두 스트림이 같은 샤드에
    await stream.aclose()


async def test_rebalance_subscribes_new_unsubscribes_dropped_and_removes_rows() -> None:
    a, b, c = symbols_for(BTC_SHARD, 3)
    sock = GatedSocket()
    stream, _, _, _, _, store = await build(
        [sock], symbols=[a, b, c], universe={base_of(a), base_of(b)}
    )
    stream.start()
    sock.push(depth(a))
    sock.push(depth(b))
    await until(sock.delivered)
    assert store.get("binance", base_of(a)) is not None
    before = len(sock.sent)
    stream.set_universe({base_of(b), base_of(c)})  # a 상폐, c 상장
    await asyncio.sleep(0.01)
    assert store.get("binance", base_of(a)) is None  # 빠진 심볼의 행은 지운다
    assert store.get("binance", base_of(b)) is not None  # 나머지 배정은 그대로
    new = [json.loads(s) for s in sock.sent[before:]]
    assert [(m["method"], m["params"]) for m in new] == [
        ("UNSUBSCRIBE", [f"{a.lower()}@depth20", f"{a.lower()}@miniTicker"]),
        ("SUBSCRIBE", [f"{c.lower()}@depth20", f"{c.lower()}@miniTicker"]),
    ]
    stream.set_universe({base_of(b), base_of(c)})  # 같은 우주 — 보내지 않는다
    await asyncio.sleep(0.01)
    assert len(sock.sent) == before + 2
    await stream.aclose()


async def test_rebalance_in_flight_does_not_outlive_the_socket_it_was_sent_on() -> None:
    # 재조정이 c 를 구독하고 0.25초 쉬는 사이 serverShutdown → 즉시 재연결.
    # 죽은 소켓에 보낸 구독을 새 소켓 것으로 세면 새 소켓은 아무것도 구독하지 않는다 (§3.3)
    a, b, c = symbols_for(BTC_SHARD, 3)
    first, second = GatedSocket(), GatedSocket()
    sleeps = TicketSleeps(tickets=1)  # 첫 소켓의 SUBSCRIBE(a·b) 한 묶음만 통과
    stream, connector, _, _, _, store = await build(
        [first, second],
        symbols=[a, b, c],
        universe={base_of(a), base_of(b)},
        sleep=sleeps,
    )
    stream.start()
    await until(first.subscribed)
    await asyncio.sleep(0.01)
    stream.set_universe({base_of(a), base_of(b), base_of(c)})  # c 상장 → 재조정
    await asyncio.sleep(0.01)
    assert subscribe_params(first)[-1] == [
        f"{c.lower()}@depth20",
        f"{c.lower()}@miniTicker",
    ]
    first.push(SHUTDOWN)  # 재조정이 쉬는 동안 첫 소켓이 끊긴다
    await asyncio.sleep(0.01)
    assert connector.urls == [WS_URL, WS_URL] and first.closed
    assert second.sent == []  # 새 소켓의 구독은 재조정이 끝나길 기다린다
    sleeps.release(2)  # 죽은 소켓의 재조정 → 새 소켓의 SUBSCRIBE
    await until(second.subscribed)
    await asyncio.sleep(0.01)
    [params] = subscribe_params(second)
    assert sorted(params) == sorted(
        n
        for s in (a, b, c)
        for n in (f"{s.lower()}@depth20", f"{s.lower()}@miniTicker")
    )
    state = store.stream_state("binance")
    assert state is not None and state.connected and state.subscribed == 3
    await stream.aclose()


# --- 판정 (§3.5) ---


async def build_three(
    outcomes: list[FakeSocket | BaseException],
) -> tuple[BinanceStream, list[list[str]], Clock, LiveStore, FakeConnector]:
    """샤드마다 심볼 2개씩 배정 — 세 샤드가 전부 연결을 시도한다(연결 순서 = 샤드 번호)."""
    per_shard = [symbols_for(k, 2) for k in range(SHARDS)]
    stream, connector, _, _, clock, store = await build(
        outcomes, symbols=[s for g in per_shard for s in g]
    )
    return stream, per_shard, clock, store, connector


async def test_only_the_silent_shard_fails_the_tick_and_recovers_on_message() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, per_shard, clock, store, _ = await build_three(list(socks))
    assert stream.judge(T0) is None  # 연결 시도 전
    stream.start()
    await asyncio.sleep(0.01)
    assert stream.judge(T0 + STALE_LIMIT - 1).ok  # type: ignore[union-attr]  # 30초 미만은 성공
    clock.now = T0 + 40_000
    for k in (0, 1):
        socks[k].push(depth(per_shard[k][0]))
        await until(socks[k].delivered)
    verdict = stream.judge(clock.now)
    assert verdict is not None and not verdict.ok and verdict.error is not None
    assert verdict.error.kind == "stale_stream"
    assert (
        verdict.error.message
        == "바이낸스 스트림 정체: 샤드 2 (구독 2종목) 30초 이상 무수신"
    )
    assert (verdict.error.url, verdict.error.status_code) == (WS_URL, None)
    state = store.stream_state("binance")
    assert state is not None and state.last_error is verdict.error
    assert (
        state.connected and state.last_message_at == clock.now and state.subscribed == 6
    )
    socks[2].push(depth(per_shard[2][0]))  # 메시지가 오면 다음 틱은 성공
    await until(socks[2].delivered)
    assert stream.judge(clock.now + 1000).ok  # type: ignore[union-attr]
    assert state.last_error is None
    await stream.aclose()


async def test_connected_since_is_stamped_when_the_subscribe_batch_is_sent() -> None:
    sock = FakeSocket([], hold=True)
    sleeps = TicketSleeps()
    stream, _, _, _, clock, store = await build([sock], sleep=sleeps)
    stream.start()
    await until(sock.subscribed)
    state = store.stream_state("binance")
    assert state is not None and state.connected
    assert state.connected_since == T0  # 보내는 동안은 소켓이 열린 시각
    clock.now = T0 + 700
    sleeps.release()
    await asyncio.sleep(0.01)
    assert state.connected_since == T0 + 700  # 구독 시각 = 묶음을 다 보낸 시각
    assert stream.judge(T0 + 700 + STALE_LIMIT - 1).ok  # type: ignore[union-attr]
    verdict = stream.judge(T0 + 700 + STALE_LIMIT)  # 정체 30초는 여기서부터
    assert verdict is not None and not verdict.ok
    await stream.aclose()


async def test_shard_without_assignment_is_ignored() -> None:
    per_shard = [symbols_for(k, 1) for k in (0, 1)]
    socks = [GatedSocket(), GatedSocket()]
    stream, _, _, _, clock, store = await build(
        list(socks), symbols=[s for g in per_shard for s in g]
    )
    stream.start()
    await asyncio.sleep(0.01)
    clock.now = T0 + 40_000
    for k in (0, 1):
        socks[k].push(depth(per_shard[k][0]))
        await until(socks[k].delivered)
    assert stream.judge(clock.now).ok  # type: ignore[union-attr]  # 배정 0 인 샤드 2 는 판정 밖
    assert store.stream_state("binance").connected  # type: ignore[union-attr]
    await stream.aclose()


async def test_disconnected_shard_fails_with_its_error_kind_and_others_keep_updating() -> (
    None
):
    socks = [GatedSocket(), GatedSocket()]
    stream, per_shard, clock, store, _ = await build_three(
        [socks[0], OSError("refused"), socks[1]]
    )
    stream.start()
    await asyncio.sleep(0.01)
    socks[0].push(depth(per_shard[0][0]))
    socks[1].push(depth(per_shard[2][0]))
    await until(socks[0].delivered)
    await until(socks[1].delivered)
    verdict = stream.judge(T0 + 1000)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "network" and "샤드 1" in verdict.error.message
    assert store.get("binance", base_of(per_shard[0][0])) is not None
    assert store.get("binance", base_of(per_shard[2][0])) is not None
    state = store.stream_state("binance")
    assert state is not None and not state.connected and state.subscribed == 4
    await stream.aclose()


async def test_quietest_shard_is_chosen_when_several_are_bad() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, per_shard, clock, _, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    clock.now = T0 + 20_000
    socks[0].push(depth(per_shard[0][0]))
    await until(socks[0].delivered)
    clock.now = T0 + 40_000
    socks[2].push(depth(per_shard[2][0]))
    await until(socks[2].delivered)
    verdict = stream.judge(T0 + 55_000)  # 샤드 0 은 35초, 샤드 1 은 55초 조용
    assert verdict is not None and verdict.error is not None
    assert "샤드 1" in verdict.error.message
    await stream.aclose()


async def test_tie_picks_the_lower_shard_and_disconnected_beats_stale() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, _, _, _, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    verdict = stream.judge(T0 + STALE_LIMIT)  # 셋 다 구독 시각부터 30초 무수신 — 동률
    assert verdict is not None and verdict.error is not None
    assert "샤드 0" in verdict.error.message
    await stream.aclose()
    stream, _, _, _, _ = await build_three(
        [GatedSocket(), OSError("dns"), GatedSocket()]
    )
    stream.start()
    await asyncio.sleep(0.01)
    verdict = stream.judge(
        T0 + STALE_LIMIT
    )  # 한 번도 못 받은 미연결 샤드가 가장 조용하다
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "network" and "샤드 1" in verdict.error.message
    await stream.aclose()


# --- 원문 싱크·프레임 분류 (§3.2) ---


async def test_every_frame_and_exchange_info_body_are_recorded_verbatim() -> None:
    frames = [
        ACK,
        depth(),
        "not json",
        mini(),
        '{"error":{"code":2,"msg":"bad"},"id":3}',
    ]
    sock = FakeSocket(frames)
    stream, connector, _, raw, clock, store = await build([sock])
    clock.now = T0 + 1
    await run_until_exhausted(stream, connector)
    assert raw.payloads(WS_SOURCE) == frames
    assert raw.keys(WS_SOURCE) == [
        None,
        "depth20:BTCUSDT",
        None,
        "miniTicker:BTCUSDT",
        None,
    ]
    assert all(
        e[0] == "binance" and e[2] == T0 + 1 for e in raw.entries if e[1] == WS_SOURCE
    )
    assert json.loads(raw.payloads(REST_SOURCE)[0])["symbols"][0]["symbol"] == "BTCUSDT"
    assert raw.keys(REST_SOURCE) == [None]
    verdict = stream.judge(T0)  # 마지막 프레임의 에러 응답 = 구독 거부
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "bad_request" and "샤드" in verdict.error.message


async def test_shutdown_and_unknown_symbol_frames_get_expected_keys() -> None:
    """`!serverShutdown` 은 key=None, 맵에 없는 심볼의 시세 프레임도 종류:심볼 key 로 기록된다 (§3.1)."""
    second = FakeSocket([], hold=True)
    stream, _, _, raw, _, store = await build(
        [FakeSocket([depth("ETHUSDT"), SHUTDOWN], hold=True), second]
    )
    stream.start()
    await until(second.subscribed)  # serverShutdown 뒤 재연결까지
    await stream.aclose()
    assert raw.keys(WS_SOURCE) == ["depth20:ETHUSDT", None]
    assert store.get("binance", "ETH") is None


async def test_acks_and_invalid_frames_do_not_count_as_quotes() -> None:
    sock = FakeSocket(
        [ACK, "not json", b"\xff\xfe", "[1]", '{"stream":"btcusdt@trade","data":{}}']
    )
    stream, _, _, _, _, store = await build([sock])
    stream.start()
    await until(sock.drained)
    state = store.stream_state("binance")
    assert state is not None and state.last_message_at is None
    assert stream.decode_failures == 3
    await stream.aclose()


# --- 재연결 (§3.3) ---


async def test_server_shutdown_reconnects_immediately_without_backoff() -> None:
    first = FakeSocket([depth(), SHUTDOWN], hold=True)
    second = FakeSocket([], hold=True)
    stream, connector, sleeps, _, _, _ = await build([first, second])
    stream.start()
    await until(second.subscribed)
    assert connector.urls == [WS_URL, WS_URL]
    assert backoffs(sleeps) == []  # 백오프 대기 없이 곧바로 붙었다
    assert first.closed
    await stream.aclose()


async def test_backoff_grows_to_thirty_and_resets_after_first_quote() -> None:
    failures: list[FakeSocket | BaseException] = [OSError("refused")] * 7
    good = FakeSocket([depth()])
    stream, connector, sleeps, _, _, store = await build(
        [*failures, good, OSError("again")]
    )
    await run_until_exhausted(stream, connector)
    assert backoffs(sleeps)[:9] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 1.0, 2.0]
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "network"
    assert store.get("binance", "BTC") is not None  # 정체 중에도 행은 남는다


async def test_subscribe_rejection_does_not_reset_backoff() -> None:
    sock = FakeSocket(['{"error":{"code":2,"msg":"Invalid request"},"id":1}'])
    stream, connector, sleeps, _, _, _ = await build([OSError("x"), OSError("x"), sock])
    await run_until_exhausted(stream, connector)
    assert backoffs(sleeps)[:3] == [1.0, 2.0, 4.0]


@pytest.mark.parametrize(
    ("exc", "kind", "status", "retry"),
    [
        (OSError("dns"), "network", None, None),
        (TimeoutError(), "timeout", None, None),
        (HandshakeRejected(429, {"Retry-After": "3"}), "rate_limit", 429, 3),
        (HandshakeRejected(418), "banned", 418, None),
        (HandshakeRejected(403), "banned", 403, None),  # WAF — 바이낸스만 banned
        (HandshakeRejected(503), "unavailable", 503, None),
        (HandshakeRejected(400), "bad_request", 400, None),
        (HandshakeRejected(302), "bad_response", 302, None),
        (RuntimeError("weird"), "bad_response", None, None),
    ],
)
async def test_connect_failures_are_classified(
    exc: BaseException, kind: str, status: int | None, retry: int | None
) -> None:
    stream, connector, _, _, _, store = await build([exc])
    await run_until_exhausted(stream, connector)
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    err = verdict.error
    assert (err.kind, err.status_code, err.url, err.retry_after_sec) == (
        kind,
        status,
        WS_URL,
        retry,
    )
    assert f"샤드 {BTC_SHARD}" in err.message
    assert store.stream_state("binance").connected is False  # type: ignore[union-attr]


async def test_handshake_rejection_body_is_kept() -> None:
    body = b'{"code":-1003,"msg":"Too much request weight used"}'
    stream, connector, _, _, _, _ = await build(
        [HandshakeRejected(429, {"Retry-After": "10"}, body=body)]
    )
    await run_until_exhausted(stream, connector)
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    err = verdict.error
    assert (err.body, err.retry_after_sec) == (body.decode(), 10)
    assert f"샤드 {BTC_SHARD}" in err.message  # 커넥터 message 는 그대로 샤드를 말한다


async def test_handshake_body_is_cut_at_500_chars_and_none_when_absent() -> None:
    stream, connector, _, _, _, _ = await build(
        [HandshakeRejected(503, body=b"y" * 600)]
    )
    await run_until_exhausted(stream, connector)
    assert stream.judge(T0).error.body == "y" * 500  # type: ignore[union-attr]
    stream, connector, _, _, _, _ = await build([HandshakeRejected(418)])
    await run_until_exhausted(stream, connector)
    assert stream.judge(T0).error.body is None  # type: ignore[union-attr]


# --- 장애 격리·종료 (§3.6) ---


async def test_boot_without_any_connection_logs_one_warning_per_shard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream, _, _, store, connector = await build_three([OSError("down")] * SHARDS)
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.binance"):
        await run_until_exhausted(stream, connector)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == SHARDS
    verdict = stream.judge(T0)
    assert (
        verdict is not None
        and verdict.error is not None
        and verdict.error.kind == "network"
    )
    assert store.get_all(exchange="binance") == []


def test_boot_with_every_connection_failing_keeps_health_200(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """lifespan 을 실제로 돌린다 — 소켓 3종은 거부, REST 는 마켓 목록만 성공(우주 = BTC)."""

    async def refuse(url: str) -> Any:
        raise OSError("refused")

    def rest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/market/all":
            return httpx.Response(200, json=[{"market": "KRW-BTC"}])
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=exchange_info(["BTCUSDT"]))
        raise httpx.ConnectError("down", request=request)

    real_client = httpx.AsyncClient
    for module in ("upbit", "bithumb", "binance"):
        monkeypatch.setattr(f"app.core.streams.{module}.open_socket", refuse)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(rest), **kw),
    )
    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(_env_file=None, redis_url="redis://127.0.0.1:1/0"),
    )
    app = create_app()
    with (
        caplog.at_level(logging.WARNING, logger="marketlens.stream.binance"),
        TestClient(app) as client,
    ):
        resp = client.get("/health")
        assert resp.status_code == 200 and resp.json()["status"] == "ok"
        for _ in range(100):  # 우주 확정 → BTC 샤드 연결 시도 1회 → 경고 1줄
            if any(r.name == "marketlens.stream.binance" for r in caplog.records):
                break
            time.sleep(0.02)
        assert client.get("/health").status_code == 200
        state = app.state.live_store.stream_state("binance")
        assert (
            state is not None and not state.connected
        )  # last_error 는 틱의 judge 가 채운다
    warnings = [r for r in caplog.records if r.name == "marketlens.stream.binance"]
    assert (
        len(warnings) == 1 and f"샤드 {BTC_SHARD} 연결 실패" in warnings[0].getMessage()
    )


async def test_aclose_cancels_tasks_and_closes_all_sockets() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, _, _, _, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    await stream.aclose()
    assert all(s.closed for s in socks)
    await asyncio.sleep(0.01)  # 취소된 태스크가 남긴 예외가 없다
    assert stream.judge(T0) is None


async def test_aclose_closes_sockets_concurrently_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.streams.binance.CLOSE_TIMEOUT", 0.2)  # 실제 2초 대신
    socks = [HangingCloseSocket() for _ in range(SHARDS)]
    stream, _, _, store, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    await stream.aclose()
    assert asyncio.get_running_loop().time() - started < 1.0
    assert [s.close_calls for s in socks] == [1, 1, 1]  # 셋을 동시에 닫는다
    assert store.stream_state("binance").connected is False  # type: ignore[union-attr]


async def test_handshake_rejection_body_is_recorded_verbatim() -> None:
    """거부 응답 본문은 이력의 500자 절단과 별개로 전문이 원문 싱크에 남는다 (001 §3.7)."""
    body = b'{"code":-1003,"msg":"' + b"z" * 600 + b'"}'
    stream, connector, _, raw, _, _ = await build(
        [HandshakeRejected(429, {"Retry-After": "10"}, body=body)]
    )
    await run_until_exhausted(stream, connector)
    assert raw.payloads("ws-handshake:/stream") == [body.decode()]
    assert raw.keys("ws-handshake:/stream") == [None]
    assert [e[0] for e in raw.entries if e[1].startswith("ws-handshake")] == ["binance"]


async def test_handshake_rejection_without_body_records_nothing() -> None:
    stream, connector, _, raw, _, _ = await build([HandshakeRejected(418)])
    await run_until_exhausted(stream, connector)
    assert raw.payloads("ws-handshake:/stream") == []
