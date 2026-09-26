"""바이빗 스트림 커넥터 — 북(스냅샷·델타)·체결·심볼 목록·샤딩·구독 한도·핑·샤드 판정·재연결·종료 (스펙 019 §4)."""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.streams import bybit as bybit_module
from app.core.streams.bybit import (
    ARGS_PER_MESSAGE,
    CONTROL_INTERVAL,
    INSTRUMENTS_PATH,
    INSTRUMENTS_QUERY,
    INSTRUMENTS_URL,
    PING_INTERVAL,
    PONG_TIMEOUT,
    REST_URL,
    SHARDS,
    WS_URL,
    BybitStream,
    shard_of,
)
from app.core.ticks import build_tick
from app.main import create_app
from tests.conftest import RawLog, make_row
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

ACK = '{"success":true,"ret_msg":"subscribe","conn_id":"c1","req_id":"1","op":"subscribe"}'
PONG = '{"success":true,"ret_msg":"pong","conn_id":"c1","op":"ping"}'
REJECT = '{"success":false,"ret_msg":"error:handler not found","conn_id":"c1","req_id":"9","op":"subscribe"}'
REST_SOURCE = "rest:/v5/market/instruments-info"
WS_SOURCE = "ws:/v5/public/spot"
HANDSHAKE_SOURCE = "ws-handshake:/v5/public/spot"


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


def topics(symbol: str) -> list[str]:
    return [f"orderbook.200.{symbol}", f"publicTrade.{symbol}"]


def instruments(symbols: list[str], extra: list[dict[str, str]] | None = None) -> dict:  # type: ignore[type-arg]
    rows = [
        {
            "symbol": s,
            "baseCoin": base_of(s),
            "quoteCoin": "USDT",
            "status": "Trading",
        }
        for s in symbols
    ]
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {"category": "spot", "list": rows + (extra or [])},
        "time": T0,
    }


def snapshot(
    symbol: str = "BTCUSDT",
    levels: int = 20,
    price: float = 71_000.0,
    size: float = 0.1,
    ts: int = T0,
    u: int = 100,
    kind: str = "snapshot",
) -> str:
    """스냅샷 프레임 — 일부러 뒤섞은 순서로 보내 정렬이 커넥터 몫임을 본다."""
    asks = [[f"{price + i * 10:.2f}", f"{size}"] for i in range(levels)]
    bids = [[f"{price - 10 - i * 10:.2f}", f"{size}"] for i in range(levels)]
    return json.dumps(
        {
            "topic": f"orderbook.200.{symbol}",
            "type": kind,
            "ts": ts,
            "data": {"s": symbol, "b": bids[::-1], "a": asks[::-1], "u": u, "seq": 1},
            "cts": ts - 2,
        }
    )


def delta(
    symbol: str = "BTCUSDT",
    asks: list[list[str]] | None = None,
    bids: list[list[str]] | None = None,
    ts: int = T0 + 50,
    u: int = 101,
) -> str:
    return json.dumps(
        {
            "topic": f"orderbook.200.{symbol}",
            "type": "delta",
            "ts": ts,
            "data": {"s": symbol, "b": bids or [], "a": asks or [], "u": u, "seq": 2},
            "cts": ts - 2,
        }
    )


def trade(
    symbol: str = "BTCUSDT", trades: list[tuple[float, int]] | None = None
) -> str:
    trades = trades if trades is not None else [(70_995.5, T0)]
    data = [
        {
            "T": t,
            "s": symbol,
            "S": "Buy",
            "v": "0.01",
            "p": str(p),
            "i": f"i{k}",
            "BT": False,
        }
        for k, (p, t) in enumerate(trades)
    ]
    return json.dumps(
        {"topic": f"publicTrade.{symbol}", "type": "snapshot", "ts": T0, "data": data}
    )


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class BybitSleeps(Sleeps):
    """핑 주기·pong 대기는 표를 줄 때만 진행한다 — 가짜 sleep 이 즉시 돌아오면 핑 루프가 폭주한다.

    호가 발행 주기(0.5초)도 같은 이유로 `release_flush()` 를 부를 때만 한 주기 지난 것으로 친다.
    구독 요청 간격(0.1초)도 `control_tickets` 를 주면 표 단위로 막을 수 있다.
    """

    def __init__(self, control_tickets: int | None = None) -> None:
        super().__init__()
        self._ping = asyncio.Semaphore(0)
        self._flush = asyncio.Semaphore(0)
        self._control = (
            asyncio.Semaphore(control_tickets) if control_tickets is not None else None
        )

    def release_ping(self, n: int = 1) -> None:
        for _ in range(n):
            self._ping.release()

    def release_flush(self, n: int = 1) -> None:
        for _ in range(n):
            self._flush.release()

    def release_control(self, n: int = 1) -> None:
        assert self._control is not None
        for _ in range(n):
            self._control.release()

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)
        if seconds in (PING_INTERVAL, PONG_TIMEOUT):
            await self._ping.acquire()
        elif seconds == bybit_module.PUBLISH_INTERVAL_MS / 1000:
            # 모듈 속성으로 비교 — 테스트가 간격을 0 으로 patch 해도 루프가 폭주하지 않게
            await self._flush.acquire()
        elif seconds == CONTROL_INTERVAL and self._control is not None:
            await self._control.acquire()
        else:
            await asyncio.sleep(0)


class ClosableGatedSocket(GatedSocket):
    """close() 가 recv() 를 끊는 소켓 — pong 없음으로 커넥터가 닫을 때 펌프가 풀려야 한다."""

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
    sleep: BybitSleeps | None = None,
) -> tuple[BybitStream, FakeConnector, BybitSleeps, RawLog, Clock, LiveStore]:
    """instruments-info(fake REST) 로 심볼 맵을 채우고 우주를 넣은 커넥터 — 배정 있는 샤드만 연결한다."""
    symbols = symbols if symbols is not None else ["BTCUSDT"]
    bases = {base_of(s) for s in symbols}
    store, sink = store_with_universe(universe if universe is not None else bases)
    connector = FakeConnector(outcomes)
    sleeps = sleep if sleep is not None else BybitSleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = BybitStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleeps, clock=clock
    )
    await stream.refresh(
        _client(lambda r: httpx.Response(200, json=instruments(symbols)))
    )
    stream.set_universe(sink.universe)
    return stream, connector, sleeps, raw, clock, store


async def build_three(
    outcomes: list[FakeSocket | BaseException],
) -> tuple[BybitStream, list[list[str]], Clock, LiveStore, FakeConnector]:
    """샤드마다 심볼 2개씩 배정 — 세 샤드가 전부 연결을 시도한다(연결 순서 = 샤드 번호)."""
    per_shard = [symbols_for(k, 2) for k in range(SHARDS)]
    stream, connector, _, _, clock, store = await build(
        outcomes, symbols=[s for g in per_shard for s in g]
    )
    return stream, per_shard, clock, store, connector


async def run_until_exhausted(stream: BybitStream, connector: FakeConnector) -> None:
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()


def backoffs(sleeps: Sleeps) -> list[float]:
    """제어 메시지·핑·발행 주기를 뺀 나머지 = 재연결 대기."""
    return [
        v
        for v in sleeps.values
        if v
        not in (
            CONTROL_INTERVAL,
            PING_INTERVAL,
            PONG_TIMEOUT,
            bybit_module.PUBLISH_INTERVAL_MS / 1000,
        )
    ]


def sent_ops(sock: FakeSocket, op: str) -> list[list[str]]:
    return [m["args"] for m in sock.subscriptions() if m["op"] == op]


# --- 북과 행 갱신 (§3.4) ---


async def test_snapshot_replaces_levels_as_sorted_floats_and_updates_row() -> None:
    sock = FakeSocket([snapshot(levels=20)])
    stream, connector, _, _, clock, store = await build([sock])
    clock.now = T0 + 5
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert row is not None
    assert len(row.asks) == 20 and len(row.bids) == 20
    assert all(isinstance(v, float) for lv in row.asks + row.bids for v in lv)
    assert row.asks == sorted(row.asks) and row.bids == sorted(row.bids, reverse=True)
    assert row.asks[0] == [71_000.0, 0.1] and row.bids[0] == [70_990.0, 0.1]
    assert (row.native_symbol, row.quote) == ("BTCUSDT", "USDT")
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    [sub] = sock.subscriptions()
    assert sub == {"req_id": "1", "op": "subscribe", "args": topics("BTCUSDT")}
    assert sock.closed


async def test_snapshot_is_cut_at_one_million_usdt_but_keeps_first_level() -> None:
    # 단계당 1,000 × 300 = 300,000 USDT → 4단계에서 누적 1,200,000 도달
    stream, connector, _, _, _, store = await build(
        [FakeSocket([snapshot(price=1_000.0, size=300.0)])]
    )
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert row is not None and len(row.asks) == 4 and len(row.bids) == 4
    stream, connector, _, _, _, store = await build(
        [FakeSocket([snapshot(price=100.0, size=20_000.0)])]
    )
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert (
        row is not None and len(row.asks) == 1 and len(row.bids) == 1
    )  # 첫 단계 2,000,000


async def test_delta_deletes_inserts_and_replaces_then_rebuilds_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 스냅샷 직후의 델타는 발행 제한(§3.2)에 걸려 다음 주기까지 행에 안 나온다 — 여기서는 북 반영 규칙만
    # 보므로 간격을 0 으로 두고 즉시 발행시킨다. 발행 제한 자체는 test_delta_within_interval_* 가 본다.
    monkeypatch.setattr("app.core.streams.bybit.PUBLISH_INTERVAL_MS", 0)
    frames = [
        snapshot(levels=3),  # asks 71000·71010·71020, bids 70990·70980·70970
        delta(
            asks=[["71000.00", "0"], ["71005.00", "0.5"], ["71010.00", "0.7"]],
            bids=[["70990.00", "0"]],
            ts=T0 + 50,
        ),
    ]
    stream, connector, _, _, clock, store = await build([FakeSocket(frames)])
    clock.now = T0 + 60
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert row is not None
    assert row.asks == [
        [71_005.0, 0.5],
        [71_010.0, 0.7],
        [71_020.0, 0.1],
    ]  # 삭제·삽입·교체
    assert row.bids == [[70_980.0, 0.1], [70_970.0, 0.1]]
    assert row.updated_at == datetime.fromtimestamp((T0 + 60) / 1000, tz=UTC)
    assert row.price_timestamp == T0 + 50  # 체결가 없으면 mid, 시각은 프레임 ts


async def test_delta_within_interval_is_applied_to_the_book_but_not_published() -> None:
    """같은 심볼의 행은 500ms 에 1번 — 두 번째 델타는 북에만 쌓이고 다음 주기에 묶여 나온다 (§3.2)."""
    sock = GatedSocket()
    stream, _, sleeps, _, clock, store = await build([sock])
    sink = stream._sink
    calls: list[int] = []
    original = sink.orderbook

    def counting(**kwargs: Any) -> None:
        calls.append(kwargs["received_at_ms"])
        original(**kwargs)

    sink.orderbook = counting  # type: ignore[method-assign]
    stream.start()
    await until(sock.subscribed)
    sock.push(snapshot(levels=3))  # asks 71000·71010·71020
    await until(sock.delivered)
    assert calls == [T0]
    clock.now = T0 + 600  # 마지막 발행(T0) 뒤 500ms 지남 → 첫 델타는 바로
    sock.push(delta(asks=[["71005.00", "0.5"]], ts=T0 + 590))
    await until(sock.delivered)
    assert calls == [T0, T0 + 600]
    clock.now = T0 + 700  # 100ms 뒤 두 번째 델타 → 북에만, sink 호출 없음
    sock.push(delta(asks=[["71000.00", "0"]], ts=T0 + 690))
    await until(sock.delivered)
    assert calls == [T0, T0 + 600]
    row = store.get("bybit", "BTC")
    assert row is not None and row.asks[0] == [71_000.0, 0.1]  # 행은 아직 첫 델타 상태
    assert sleeps.values.count(0.5) == 1  # 주기 대기 중
    clock.now = T0 + 1_100
    sleeps.release_flush()  # 한 주기 지남 → 묶인 델타가 행으로
    await asyncio.sleep(0.01)
    assert calls == [T0, T0 + 600, T0 + 700]  # 수신 시각은 마지막 델타의 것
    row = store.get("bybit", "BTC")
    assert row is not None
    assert row.asks == [[71_005.0, 0.5], [71_010.0, 0.1], [71_020.0, 0.1]]
    assert row.updated_at == datetime.fromtimestamp((T0 + 700) / 1000, tz=UTC)
    assert row.price_timestamp == T0 + 690  # 호가 시각 = 마지막 델타의 ts
    await stream.aclose()


async def test_dirty_symbol_is_published_within_one_interval_after_its_last_delta() -> (
    None
):
    """델타가 끊긴 심볼도 마지막 델타 뒤 한 주기(500ms) 안에는 반드시 나온다 — 발행 누락 없음 (§3.2)."""
    sock = GatedSocket()
    stream, _, sleeps, _, clock, store = await build([sock])
    stream.start()
    await until(sock.subscribed)
    sock.push(snapshot(levels=3))
    await until(sock.delivered)
    # 스냅샷 발행 100ms 뒤 델타 하나 → dirty 로만 남고 이후 조용하다
    clock.now = T0 + 100
    sock.push(delta(asks=[["71005.00", "0.5"]], ts=T0 + 90))
    await until(sock.delivered)
    row = store.get("bybit", "BTC")
    assert row is not None and len(row.asks) == 3  # 아직 스냅샷 상태
    # 주기 태스크는 실제 시간 0.5초를 자므로 그 한 주기가 지나면 밀려 나온다 — 상한 = 주기 = 500ms
    assert sleeps.values.count(0.5) == 1
    clock.now = T0 + 500
    sleeps.release_flush()
    await asyncio.sleep(0.01)
    row = store.get("bybit", "BTC")
    assert row is not None and len(row.asks) == 4 and row.asks[1] == [71_005.0, 0.5]
    assert row.updated_at == datetime.fromtimestamp((T0 + 100) / 1000, tz=UTC)
    assert row.price_timestamp == T0 + 90
    sleeps.release_flush()  # 다음 주기 — dirty 가 없으니 다시 내보내지 않는다
    await asyncio.sleep(0.01)
    assert store.get("bybit", "BTC").updated_at == datetime.fromtimestamp(  # type: ignore[union-attr]
        (T0 + 100) / 1000, tz=UTC
    )
    await stream.aclose()


async def test_snapshot_is_published_immediately_regardless_of_interval() -> None:
    """스냅샷은 북 교체 직후 상태를 간격과 무관하게 바로 내보낸다 (§3.2)."""
    sock = GatedSocket()
    stream, _, _, _, clock, store = await build([sock])
    stream.start()
    await until(sock.subscribed)
    sock.push(snapshot(levels=3))
    await until(sock.delivered)
    clock.now = T0 + 100
    sock.push(delta(asks=[["71005.00", "0.5"]], ts=T0 + 90))  # dirty 로 남는다
    await until(sock.delivered)
    clock.now = T0 + 200  # 마지막 발행 뒤 200ms — 델타라면 묶였을 시점
    sock.push(snapshot(levels=2, price=80_000.0, ts=T0 + 190))
    await until(sock.delivered)
    row = store.get("bybit", "BTC")
    assert row is not None and row.asks == [[80_000.0, 0.1], [80_010.0, 0.1]]
    assert row.updated_at == datetime.fromtimestamp((T0 + 200) / 1000, tz=UTC)
    assert row.price_timestamp == T0 + 190
    await stream.aclose()


async def test_delta_before_snapshot_is_dropped() -> None:
    stream, connector, _, _, clock, store = await build(
        [FakeSocket([delta(asks=[["71000.00", "1"]], bids=[["70990.00", "1"]])])]
    )
    clock.now = T0 + 7
    await run_until_exhausted(stream, connector)
    assert store.get("bybit", "BTC") is None
    state = store.stream_state("bybit")
    assert state is not None and state.last_message_at == T0 + 7  # 시세 프레임이긴 하다


async def test_new_snapshot_and_u1_replace_the_book() -> None:
    frames = [
        snapshot(levels=3),
        delta(asks=[["71005.00", "0.5"]]),
        snapshot(levels=2, price=80_000.0),  # 새 스냅샷 → 통째 교체
    ]
    stream, connector, _, _, _, store = await build([FakeSocket(frames)])
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert row is not None and row.asks == [[80_000.0, 0.1], [80_010.0, 0.1]]
    frames = [
        snapshot(levels=3),
        snapshot(
            levels=2, price=90_000.0, u=1, kind="delta"
        ),  # u=1 은 서비스 재시작 스냅샷
    ]
    stream, connector, _, _, _, store = await build([FakeSocket(frames)])
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert row is not None and row.asks == [[90_000.0, 0.1], [90_010.0, 0.1]]


async def test_reconnect_starts_a_fresh_book_and_keeps_the_old_row_until_snapshot() -> (
    None
):
    first = FakeSocket([snapshot(levels=3)])  # 다 주고 끊긴다
    second = GatedSocket()
    stream, _, _, _, _, store = await build([first, second])
    stream.start()
    await until(second.subscribed)
    second.push(
        delta(asks=[["99999.00", "1"]])
    )  # 새 소켓의 첫 델타 — 스냅샷 전이라 버린다
    await until(second.delivered)
    row = store.get("bybit", "BTC")
    assert row is not None and row.asks[0] == [71_000.0, 0.1]  # 행은 메시지로만 바뀐다
    second.push(snapshot(levels=2, price=72_000.0))
    await until(second.delivered)
    row = store.get("bybit", "BTC")
    assert row is not None and row.asks == [[72_000.0, 0.1], [72_010.0, 0.1]]
    await stream.aclose()


async def test_trade_picks_the_latest_fill_and_is_held_until_orderbook() -> None:
    frames = [
        trade(trades=[(1.0, T0 - 9)]),  # 호가 전 — 보류
        snapshot(),
        trade(trades=[(2.0, T0 + 3), (3.0, T0 + 9), (2.5, T0 + 5)]),  # T 최대 = 3.0
    ]
    stream, connector, _, _, _, store = await build([FakeSocket(frames)])
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (3.0, T0 + 9)
    assert len(row.asks) == 20  # 체결가는 호가를 건드리지 않는다


async def test_held_trade_is_applied_with_first_snapshot_and_mid_without_trade() -> (
    None
):
    stream, connector, _, _, _, store = await build(
        [FakeSocket([trade(trades=[(7.0, T0 - 9)]), snapshot()])]
    )
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (7.0, T0 - 9)
    stream, connector, _, _, clock, store = await build(
        [FakeSocket([snapshot(ts=T0 + 1)])]
    )
    clock.now = T0 + 3
    await run_until_exhausted(stream, connector)
    row = store.get("bybit", "BTC")
    assert row is not None
    assert row.price == (71_000.0 + 70_990.0) / 2  # 체결가 없으면 mid
    assert row.price_timestamp == T0 + 1  # 프레임 ts (§3.4)


async def test_tick_pairs_bybit_with_every_domestic_exchange() -> None:
    """우주 합집합 뒤 틱은 국내×해외 전 조합이라 (upbit, bybit)·(bithumb, bybit) 행이 나온다 (§3.7)."""
    store = LiveStore()
    now = datetime.fromtimestamp(T0 / 1000, tz=UTC)
    for ex in ("upbit", "bithumb"):
        store.put_row(
            make_row(
                ex, "BTC", asks=[[101_000_000.0, 1.0]], bids=[[100_000_000.0, 1.0]]
            ),
            now,
        )
        store.set_rate(ex, 1500.0, 1499.0, now)
    for ex in ("binance", "bybit"):
        store.put_row(
            make_row(
                ex, "BTC", quote="USDT", asks=[[67_000.0, 1.0]], bids=[[66_900.0, 1.0]]
            ),
            now,
        )
    tick = build_tick(store, T0 // 1000, [])
    assert sorted((r.dom, r.fx) for r in tick.rows) == [
        ("bithumb", "binance"),
        ("bithumb", "bybit"),
        ("upbit", "binance"),
        ("upbit", "bybit"),
    ]


# --- 심볼 목록 (§3.3) ---


async def test_instruments_info_keeps_only_trading_usdt_symbols() -> None:
    stream, _, _, raw, _, _ = await build([])
    extra = [
        {
            "symbol": "ETHBTC",
            "baseCoin": "ETH",
            "quoteCoin": "BTC",
            "status": "Trading",
        },
        {
            "symbol": "XYZUSDT",
            "baseCoin": "XYZ",
            "quoteCoin": "USDT",
            "status": "PendingOpen",
        },
        {
            "symbol": "SOLUSDT",
            "baseCoin": "SOL",
            "quoteCoin": "USDT",
            "status": "Trading",
        },
        {
            "symbol": "SOLUSDT2",
            "baseCoin": "SOL",
            "quoteCoin": "USDT",
            "status": "Trading",
        },
    ]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=instruments(["BTCUSDT"], extra))

    assert await stream.refresh(_client(handler)) == 1
    assert stream.bases() == {"BTC", "SOL"}
    assert (
        str(calls[0].url)
        == INSTRUMENTS_URL
        == REST_URL + INSTRUMENTS_PATH + INSTRUMENTS_QUERY
    )
    assert (
        calls[0].url.params["category"] == "spot"
        and calls[0].url.params["status"] == "Trading"
    )
    assert raw.keys(REST_SOURCE) == [
        "symbols:all",
        "symbols:all",
    ]  # 질의 없는 경로가 source


async def test_instruments_info_ret_code_failure_keeps_previous_list() -> None:
    stream, _, _, _, _, _ = await build([])
    body = {"retCode": 10001, "retMsg": "params error", "result": {}, "time": T0}
    with pytest.raises(ExchangeApiError) as exc_info:
        await stream.refresh(_client(lambda r: httpx.Response(200, json=body)))
    err = exc_info.value
    assert (err.kind, err.status_code) == ("bad_response", 200)
    assert err.body is not None and "params error" in err.body
    assert stream.bases() == {"BTC"}  # 직전 목록 유지
    with pytest.raises(ExchangeApiError) as exc_info:
        await stream.refresh(
            _client(lambda r: httpx.Response(200, json={**body, "retCode": 10006}))
        )
    assert exc_info.value.kind == "rate_limit"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(403, "banned"), (429, "rate_limit"), (503, "unavailable"), (400, "bad_request")],
)
async def test_instruments_info_non_200_is_classified_by_bybit_rule(
    status: int, kind: str
) -> None:
    stream, _, _, raw, _, _ = await build([])
    with pytest.raises(ExchangeApiError) as exc_info:
        await stream.refresh(_client(lambda r: httpx.Response(status, text="nope")))
    err = exc_info.value
    assert (err.kind, err.status_code, err.body, err.retry_after_sec) == (
        kind,
        status,
        "nope",
        None,
    )
    assert raw.payloads(REST_SOURCE)[-1] == "nope"  # 비-200 본문도 원문


async def test_instruments_info_timeout_network_and_bad_json() -> None:
    stream, _, _, _, _, _ = await build([])

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ExchangeTimeoutError) as t:
        await stream.refresh(_client(timeout))
    assert t.value.kind == "timeout"
    with pytest.raises(ExchangeApiError) as n:
        await stream.refresh(_client(refused))
    assert n.value.kind == "network"
    with pytest.raises(ExchangeApiError) as b:
        await stream.refresh(_client(lambda r: httpx.Response(200, text="<html>")))
    assert b.value.kind == "bad_response"
    with pytest.raises(ExchangeApiError) as m:
        await stream.refresh(
            _client(lambda r: httpx.Response(200, json={"retCode": 0, "result": {}}))
        )
    assert m.value.kind == "bad_response"


async def test_messages_outside_universe_or_symbol_map_are_dropped() -> None:
    sock = FakeSocket([snapshot("ETHUSDT"), snapshot("SOLUSDT"), snapshot("BTCUSDT")])
    stream, connector, _, _, _, store = await build(
        [sock], symbols=["BTCUSDT", "SOLUSDT"], universe={"BTC"}
    )
    await run_until_exhausted(stream, connector)
    assert store.get("bybit", "ETH") is None  # 맵에 없다
    assert store.get("bybit", "SOL") is None  # 우주 밖
    assert store.get("bybit", "BTC") is not None


# --- 샤딩·구독 (§3.3) ---


def test_symbols_spread_over_three_shards_and_sibling_topics_share_one() -> None:
    symbols = [f"T{i:04d}USDT" for i in range(300)]
    counts = [sum(1 for s in symbols if shard_of(s) == k) for k in range(SHARDS)]
    assert sum(counts) == 300 and all(c > 50 for c in counts)
    assert all(shard_of(s) == shard_of(s.lower()) for s in symbols)


def test_shard_hash_is_stable_across_processes() -> None:
    symbols = [f"T{i:04d}USDT" for i in range(50)] + ["BTCUSDT", "ETHUSDT"]
    code = (
        "import json, sys; from app.core.streams.bybit import shard_of; "
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


async def test_subscribe_messages_carry_at_most_ten_args_and_are_spaced() -> None:
    symbols = symbols_for(BTC_SHARD, 12)  # 24 토픽 → 10·10·4
    sock = FakeSocket([], hold=True)
    stream, _, sleeps, _, _, _ = await build([sock], symbols=symbols)
    stream.start()
    await asyncio.sleep(0.01)
    subs = sock.subscriptions()
    assert [len(m["args"]) for m in subs] == [10, 10, 4]
    assert [m["req_id"] for m in subs] == ["1", "2", "3"]
    assert all(
        len(m["args"]) <= ARGS_PER_MESSAGE and m["op"] == "subscribe" for m in subs
    )
    sent = [a for m in subs for a in m["args"]]
    for s in symbols:
        assert f"orderbook.200.{s}" in sent and f"publicTrade.{s}" in sent
    assert sleeps.values.count(CONTROL_INTERVAL) == 3  # 요청마다 0.1초
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
        [args] = sent_ops(socks[k], "subscribe")
        assert sorted(args) == sorted(t for s in per_shard[k] for t in topics(s))
    await stream.aclose()


async def test_rebalance_subscribes_new_unsubscribes_dropped_and_removes_rows() -> None:
    a, b, c = symbols_for(BTC_SHARD, 3)
    sock = GatedSocket()
    stream, _, _, _, _, store = await build(
        [sock], symbols=[a, b, c], universe={base_of(a), base_of(b)}
    )
    stream.start()
    sock.push(snapshot(a))
    sock.push(snapshot(b))
    await until(sock.delivered)
    assert store.get("bybit", base_of(a)) is not None
    before = len(sock.sent)
    stream.set_universe({base_of(b), base_of(c)})  # a 상폐, c 상장
    await asyncio.sleep(0.01)
    assert store.get("bybit", base_of(a)) is None  # 빠진 심볼의 행은 지운다
    assert store.get("bybit", base_of(b)) is not None
    new = [json.loads(s) for s in sock.sent[before:]]
    assert [(m["op"], m["args"]) for m in new] == [
        ("unsubscribe", topics(a)),
        ("subscribe", topics(c)),
    ]
    stream.set_universe({base_of(b), base_of(c)})  # 같은 우주 — 보내지 않는다
    await asyncio.sleep(0.01)
    assert len(sock.sent) == before + 2
    stream.set_universe(
        {base_of(b), base_of(c), "OTHERFX"}
    )  # 다른 해외에만 있는 코인 — 무시
    await asyncio.sleep(0.01)
    assert len(sock.sent) == before + 2
    await stream.aclose()


# --- 핑 (§3.2) ---


async def test_ping_every_interval_and_pong_clears_the_wait() -> None:
    sock = ClosableGatedSocket()
    sleeps = BybitSleeps()
    stream, connector, _, _, _, store = await build([sock], sleep=sleeps)
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    assert sleeps.values.count(PING_INTERVAL) == 1  # 첫 주기를 기다리는 중
    sleeps.release_ping()  # 20초 지남 → ping 전송
    await asyncio.sleep(0.01)
    assert json.loads(sock.sent[-1]) == {"req_id": "2", "op": "ping"}
    sock.push(PONG)
    await until(sock.delivered)
    sleeps.release_ping()  # pong 대기 20초 지남 — 이미 받았으니 끊지 않는다
    await asyncio.sleep(0.01)
    assert not sock.closed and connector.urls == [WS_URL]
    state = store.stream_state("bybit")
    assert state is not None and state.last_message_at is None  # pong 은 시세가 아니다
    await stream.aclose()


async def test_missing_pong_closes_and_reconnects_with_timeout_kind() -> None:
    first, second = ClosableGatedSocket(), GatedSocket()
    sleeps = BybitSleeps()
    stream, connector, _, _, _, store = await build([first, second], sleep=sleeps)
    stream.start()
    await until(first.subscribed)
    await asyncio.sleep(0.01)
    sleeps.release_ping()  # ping 전송
    await asyncio.sleep(0.01)
    sleeps.release_ping()  # pong 없이 20초 → 끊는다
    await until(second.subscribed)
    assert first.closed and connector.urls == [WS_URL, WS_URL]
    state = store.stream_state("bybit")
    assert state is not None and state.connected
    await stream.aclose()
    verdict = stream.judge(T0)  # 종료 뒤 미연결 — 마지막 오류가 pong 없음
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "timeout" and "pong" in verdict.error.message


# --- 판정 (§3.5) ---


async def test_only_the_silent_shard_fails_the_tick_and_recovers_on_message() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, per_shard, clock, store, _ = await build_three(list(socks))
    assert stream.judge(T0) is None  # 연결 시도 전
    stream.start()
    await asyncio.sleep(0.01)
    assert stream.judge(T0 + STALE_LIMIT - 1).ok  # type: ignore[union-attr]
    clock.now = T0 + 40_000
    for k in (0, 1):
        socks[k].push(snapshot(per_shard[k][0]))
        await until(socks[k].delivered)
    socks[2].push(PONG)  # pong 만 오는 샤드도 정체다
    await until(socks[2].delivered)
    verdict = stream.judge(clock.now)
    assert verdict is not None and not verdict.ok and verdict.error is not None
    assert verdict.error.kind == "stale_stream"
    assert (
        verdict.error.message
        == "바이빗 스트림 정체: 샤드 2 (구독 2종목) 30초 이상 무수신"
    )
    assert (verdict.error.url, verdict.error.status_code) == (WS_URL, None)
    state = store.stream_state("bybit")
    assert state is not None and state.last_error is verdict.error
    assert (
        state.connected and state.last_message_at == clock.now and state.subscribed == 6
    )
    socks[2].push(snapshot(per_shard[2][0]))  # 메시지가 오면 다음 틱은 성공
    await until(socks[2].delivered)
    assert stream.judge(clock.now + 1000).ok  # type: ignore[union-attr]
    assert state.last_error is None
    await stream.aclose()


async def test_connected_since_is_stamped_when_the_subscribe_batch_is_sent() -> None:
    sock = FakeSocket([], hold=True)
    sleeps = BybitSleeps(control_tickets=0)
    stream, _, _, _, clock, store = await build([sock], sleep=sleeps)
    stream.start()
    await until(sock.subscribed)
    state = store.stream_state("bybit")
    assert state is not None and state.connected
    assert state.connected_since == T0  # 보내는 동안은 소켓이 열린 시각
    clock.now = T0 + 700
    sleeps.release_control()
    await asyncio.sleep(0.01)
    assert state.connected_since == T0 + 700  # 구독 시각 = 묶음을 다 보낸 시각
    assert stream.judge(T0 + 700 + STALE_LIMIT - 1).ok  # type: ignore[union-attr]
    verdict = stream.judge(T0 + 700 + STALE_LIMIT)
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
        socks[k].push(snapshot(per_shard[k][0]))
        await until(socks[k].delivered)
    assert stream.judge(clock.now).ok  # type: ignore[union-attr]  # 배정 0 인 샤드 2 는 판정 밖
    assert store.stream_state("bybit").connected  # type: ignore[union-attr]
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
    socks[0].push(snapshot(per_shard[0][0]))
    socks[1].push(snapshot(per_shard[2][0]))
    await until(socks[0].delivered)
    await until(socks[1].delivered)
    verdict = stream.judge(T0 + 1000)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "network" and "샤드 1" in verdict.error.message
    assert store.get("bybit", base_of(per_shard[0][0])) is not None
    assert store.get("bybit", base_of(per_shard[2][0])) is not None
    state = store.stream_state("bybit")
    assert state is not None and not state.connected and state.subscribed == 4
    await stream.aclose()


async def test_quietest_shard_is_chosen_and_tie_picks_the_lower() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, per_shard, clock, _, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    clock.now = T0 + 20_000
    socks[0].push(snapshot(per_shard[0][0]))
    await until(socks[0].delivered)
    clock.now = T0 + 40_000
    socks[2].push(snapshot(per_shard[2][0]))
    await until(socks[2].delivered)
    verdict = stream.judge(T0 + 55_000)  # 샤드 0 은 35초, 샤드 1 은 55초 조용
    assert verdict is not None and verdict.error is not None
    assert "샤드 1" in verdict.error.message
    await stream.aclose()
    stream, _, _, _, _ = await build_three([GatedSocket() for _ in range(SHARDS)])
    stream.start()
    await asyncio.sleep(0.01)
    verdict = stream.judge(T0 + STALE_LIMIT)  # 셋 다 동률 → 샤드 0
    assert verdict is not None and verdict.error is not None
    assert "샤드 0" in verdict.error.message
    await stream.aclose()


# --- 원문 싱크·프레임 분류 (§3.1·§3.2) ---


async def test_every_frame_and_instruments_body_are_recorded_verbatim() -> None:
    frames = [ACK, snapshot(), "not json", trade(), PONG, REJECT]
    sock = FakeSocket(frames)
    stream, connector, _, raw, clock, _ = await build([sock])
    clock.now = T0 + 1
    await run_until_exhausted(stream, connector)
    assert raw.payloads(WS_SOURCE) == frames
    assert raw.keys(WS_SOURCE) == [
        None,
        "orderbook:BTCUSDT",
        None,
        "trade:BTCUSDT",
        None,
        None,
    ]
    assert all(
        e[0] == "bybit" and e[2] == T0 + 1 for e in raw.entries if e[1] == WS_SOURCE
    )
    body = json.loads(raw.payloads(REST_SOURCE)[0])
    assert body["result"]["list"][0]["symbol"] == "BTCUSDT"
    assert raw.keys(REST_SOURCE) == ["symbols:all"]
    verdict = stream.judge(T0)  # 마지막 프레임 success:false = 구독 거부
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "bad_request" and "샤드" in verdict.error.message
    assert "handler not found" in verdict.error.message


async def test_unknown_symbol_frames_keep_their_key_but_do_not_count() -> None:
    sock = FakeSocket(
        [
            snapshot("ETHUSDT"),
            ACK,
            "[1]",
            b"\xff\xfe",
            '{"topic":"kline.1.BTCUSDT","data":{}}',
        ]
    )
    stream, connector, _, raw, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    assert raw.keys(WS_SOURCE) == ["orderbook:ETHUSDT", None, None, None]
    assert store.get("bybit", "ETH") is None
    state = store.stream_state("bybit")
    assert state is not None and state.last_message_at is None
    assert stream.decode_failures == 2


# --- 재연결·분류 (§3.2·§3.8) ---


async def test_backoff_grows_to_thirty_and_resets_after_first_quote() -> None:
    failures: list[FakeSocket | BaseException] = [OSError("refused")] * 7
    good = FakeSocket([snapshot()])
    stream, connector, sleeps, _, _, store = await build(
        [*failures, good, OSError("again")]
    )
    await run_until_exhausted(stream, connector)
    assert backoffs(sleeps)[:9] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 1.0, 2.0]
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "network"
    assert store.get("bybit", "BTC") is not None  # 정체 중에도 행은 남는다


async def test_subscribe_rejection_does_not_reset_backoff() -> None:
    stream, connector, sleeps, _, _, _ = await build(
        [OSError("x"), OSError("x"), FakeSocket([REJECT])]
    )
    await run_until_exhausted(stream, connector)
    assert backoffs(sleeps)[:3] == [1.0, 2.0, 4.0]


@pytest.mark.parametrize(
    ("exc", "kind", "status"),
    [
        (OSError("dns"), "network", None),
        (TimeoutError(), "timeout", None),
        (HandshakeRejected(403), "banned", 403),  # "access too frequent"
        (HandshakeRejected(429, {"Retry-After": "3"}), "rate_limit", 429),
        (HandshakeRejected(503), "unavailable", 503),
        (HandshakeRejected(400), "bad_request", 400),
        (HandshakeRejected(302), "bad_response", 302),
        (RuntimeError("weird"), "bad_response", None),
    ],
)
async def test_connect_failures_are_classified(
    exc: BaseException, kind: str, status: int | None
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
        None,  # Retry-After 는 문서에 없다 — 항상 null
    )
    assert f"샤드 {BTC_SHARD}" in err.message
    assert store.stream_state("bybit").connected is False  # type: ignore[union-attr]


async def test_handshake_rejection_body_is_kept_cut_and_recorded_verbatim() -> None:
    body = b'{"retCode":10006,"retMsg":"' + b"z" * 600 + b'"}'
    stream, connector, _, raw, _, _ = await build([HandshakeRejected(403, body=body)])
    await run_until_exhausted(stream, connector)
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.body is not None and len(verdict.error.body) == 500
    assert raw.payloads(HANDSHAKE_SOURCE) == [body.decode()]  # 원문은 전문
    assert raw.keys(HANDSHAKE_SOURCE) == [None]
    stream, connector, _, raw, _, _ = await build([HandshakeRejected(503)])
    await run_until_exhausted(stream, connector)
    assert raw.payloads(HANDSHAKE_SOURCE) == []


# --- 장애 격리·종료 (§3.6 계열) ---


async def test_boot_without_any_connection_logs_one_warning_per_shard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream, _, _, store, connector = await build_three([OSError("down")] * SHARDS)
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.bybit"):
        await run_until_exhausted(stream, connector)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == SHARDS
    verdict = stream.judge(T0)
    assert (
        verdict is not None
        and verdict.error is not None
        and verdict.error.kind == "network"
    )
    assert store.get_all(exchange="bybit") == []


def test_boot_with_every_connection_failing_keeps_health_200(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """lifespan 을 실제로 돌린다 — 소켓 4종은 거부, REST 는 목록만 성공(우주 = BTC, 바이빗에만 있는 SOL 포함)."""

    async def refuse(url: str) -> Any:
        raise OSError("refused")

    def rest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/market/all":
            return httpx.Response(
                200, json=[{"market": "KRW-BTC"}, {"market": "KRW-SOL"}]
            )
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "TRADING",
                            "baseAsset": "BTC",
                            "quoteAsset": "USDT",
                        }
                    ]
                },
            )
        if request.url.path == INSTRUMENTS_PATH:
            return httpx.Response(200, json=instruments(["BTCUSDT", "SOLUSDT"]))
        raise httpx.ConnectError("down", request=request)

    real_client = httpx.AsyncClient
    for module in ("upbit", "bithumb", "binance", "bybit", "bitget"):
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
        caplog.at_level(logging.WARNING, logger="marketlens.stream.bybit"),
        TestClient(app) as client,
    ):
        resp = client.get("/health")
        # 025 — 첫 틱 전엔 starting(503). 앱이 JSON 으로 답하면 떠 있는 것이다
        assert resp.json()["status"] in ("ok", "starting")
        for _ in range(100):  # 우주 확정 → 배정 있는 샤드 연결 시도 → 경고
            if any(r.name == "marketlens.stream.bybit" for r in caplog.records):
                break
            time.sleep(0.02)
        assert client.get("/health").json()["status"] in ("ok", "starting")
        health = client.get("/health/collect").json()
        assert [e["exchange"] for e in health["exchanges"]] == [
            "upbit",
            "bithumb",
            "binance",
            "bybit",
            "bitget",
        ]
        state = app.state.live_store.stream_state("bybit")
        assert state is not None and not state.connected
    warnings = [r for r in caplog.records if r.name == "marketlens.stream.bybit"]
    assert warnings and all("연결 실패" in r.getMessage() for r in warnings)
    shards = {
        shard_of("BTCUSDT"),
        shard_of("SOLUSDT"),
    }  # 우주 = 국내 ∩ (바이낸스 ∪ 바이빗) = {BTC, SOL}
    assert {
        int(r.getMessage().split("샤드 ")[1].split(" ")[0]) for r in warnings
    } == shards


async def test_aclose_cancels_tasks_and_closes_all_sockets() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, _, _, _, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    before = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    assert len(before) == SHARDS * 3 + 1  # 샤드 3 + 핑 3 + 발행 주기 3 + 재조정 1
    await stream.aclose()
    assert all(s.closed for s in socks)
    await asyncio.sleep(0.01)  # 취소된 태스크가 남긴 예외가 없다
    assert all(t.done() for t in before)
    assert stream.judge(T0) is None


async def test_aclose_closes_sockets_concurrently_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.streams.bybit.CLOSE_TIMEOUT", 0.2)  # 실제 2초 대신
    socks = [HangingCloseSocket() for _ in range(SHARDS)]
    stream, _, _, store, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    await stream.aclose()
    assert asyncio.get_running_loop().time() - started < 1.0
    assert [s.close_calls for s in socks] == [1, 1, 1]  # 셋을 동시에 닫는다
    assert store.stream_state("bybit").connected is False  # type: ignore[union-attr]
