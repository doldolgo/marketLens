"""OKX 스트림 커넥터 — books5 스냅샷(로컬 북 없음)·체결·instruments·샤딩·구독 예산·수신 기준 핑·샤드 판정·재연결·종료 (스펙 045 §4)."""

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
from app.core.streams.okx import (
    ARGS_BYTES_LIMIT,
    ARGS_PER_MESSAGE,
    CONTROL_INTERVAL,
    INSTRUMENTS_PATH,
    INSTRUMENTS_URL,
    PING_CHECK_INTERVAL,
    PONG_TIMEOUT,
    SHARDS,
    SUBSCRIBE_WARN_AT,
    WS_URL,
    OkxStream,
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
BTC = "BTC-USDT"
BTC_SHARD = shard_of(BTC)

ACK = '{"event":"subscribe","arg":{"channel":"books5","instId":"BTC-USDT"},"connId":"a4d3ae55"}'
PONG = "pong"
REJECT = '{"event":"error","code":"60012","msg":"Invalid request: {\\"op\\":\\"subscribe\\"}","connId":"a4d3ae55"}'
NOTICE = '{"event":"notice","code":"64008","msg":"The connection will soon be closed for a service upgrade. Please reconnect.","connId":"a4d3ae55"}'
REST_SOURCE = "rest:/api/v5/public/instruments"
WS_SOURCE = "ws:/ws/v5/public"
HANDSHAKE_SOURCE = "ws-handshake:/ws/v5/public"


def symbols_for(shard: int, n: int) -> list[str]:
    """해시가 그 샤드에 떨어지는 가짜 USDT instId n개 — 배정 규칙(crc32 % 3)을 그대로 쓴다."""
    out: list[str] = []
    i = 0
    while len(out) < n:
        symbol = f"T{i:04d}-USDT"
        i += 1
        if shard_of(symbol) == shard:
            out.append(symbol)
    return out


def base_of(symbol: str) -> str:
    return symbol.split("-")[0]


def arg(channel: str, symbol: str) -> dict[str, str]:
    return {"channel": channel, "instId": symbol}


def args_of(symbol: str) -> list[dict[str, str]]:
    return [arg("books5", symbol), arg("trades", symbol)]


def instruments_body(
    symbols: list[str], extra: list[dict[str, str]] | None = None
) -> dict:  # type: ignore[type-arg]
    rows = [
        {
            "instId": s,
            "baseCcy": base_of(s),
            "quoteCcy": "USDT",
            "state": "live",
            "instType": "SPOT",
        }
        for s in symbols
    ]
    return {"code": "0", "msg": "", "data": rows + (extra or [])}


def level(price: float, size: float) -> list[str]:
    """books5 원소 — [가격, 잔량, "0"(폐기 필드), 주문 수]."""
    return [f"{price:.2f}", f"{size}", "0", "3"]


def books(
    symbol: str,
    asks: list[list[str]],
    bids: list[list[str]],
    ts: int = T0,
    seq: int = 100,
) -> str:
    return json.dumps(
        {
            "arg": arg("books5", symbol),
            "data": [
                {
                    "asks": asks,
                    "bids": bids,
                    "instId": symbol,
                    "ts": str(ts),
                    "seqId": seq,
                }
            ],
        }
    )


def snapshot(
    symbol: str = BTC,
    levels: int = 5,
    price: float = 71_000.0,
    size: float = 0.1,
    ts: int = T0,
    seq: int = 100,
    ordered: bool = False,
) -> str:
    """books5 스냅샷 — 기본은 일부러 뒤섞은 순서로 보내 정렬이 커넥터 몫임을 본다. ts 는 문자열 ms."""
    asks = [level(price + i * 10, size) for i in range(levels)]
    bids = [level(price - 10 - i * 10, size) for i in range(levels)]
    if not ordered:
        asks, bids = asks[::-1], bids[::-1]
    return books(symbol, asks, bids, ts=ts, seq=seq)


def trade(symbol: str = BTC, trades: list[tuple[float, int]] | None = None) -> str:
    if trades is None:
        trades = [(70_995.5, T0)]
    data = [
        {
            "instId": symbol,
            "tradeId": f"{k}",
            "px": str(p),
            "sz": "0.01",
            "side": "buy",
            "ts": str(t),
            "count": "1",
            "source": "0",
            "seqId": k,
        }
        for k, (p, t) in enumerate(trades)
    ]
    return json.dumps({"arg": arg("trades", symbol), "data": data})


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class OkxSleeps(Sleeps):
    """핑 점검 간격·pong 대기는 표를 줄 때만 진행한다 — 가짜 sleep 이 즉시 돌아오면 핑 루프가 폭주한다.

    구독 요청 간격(0.2초)도 `control_tickets` 를 주면 표 단위로 막을 수 있다.
    """

    def __init__(
        self,
        control_tickets: int | None = None,
        gated: tuple[float, ...] = (PING_CHECK_INTERVAL, PONG_TIMEOUT),
    ) -> None:
        super().__init__()
        self.gated = gated
        self._ping = asyncio.Semaphore(0)
        if control_tickets is None:
            self._control = None
        else:
            self._control = asyncio.Semaphore(control_tickets)

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
    sleep: OkxSleeps | None = None,
) -> tuple[OkxStream, FakeConnector, OkxSleeps, RawLog, Clock, LiveStore]:
    """instruments(fake REST) 로 심볼 맵을 채우고 우주를 넣은 커넥터 — 배정 있는 샤드만 연결한다."""
    if symbols is None:
        symbols = [BTC]
    bases = {base_of(s) for s in symbols}
    if universe is None:
        universe = bases
    store, sink = store_with_universe(universe)
    connector = FakeConnector(outcomes)
    if sleep is None:
        sleep = OkxSleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = OkxStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleep, clock=clock
    )
    await stream.refresh(
        _client(lambda r: httpx.Response(200, json=instruments_body(symbols)))
    )
    stream.set_universe(sink.universe)
    return stream, connector, sleep, raw, clock, store


async def build_three(
    outcomes: list[FakeSocket | BaseException],
) -> tuple[OkxStream, list[list[str]], Clock, LiveStore, FakeConnector]:
    """샤드마다 심볼 2개씩 배정 — 세 샤드가 전부 연결을 시도한다(연결 순서 = 샤드 번호)."""
    per_shard = [symbols_for(k, 2) for k in range(SHARDS)]
    stream, connector, _, _, clock, store = await build(
        outcomes, symbols=[s for g in per_shard for s in g]
    )
    return stream, per_shard, clock, store, connector


async def run_until_exhausted(stream: OkxStream, connector: FakeConnector) -> None:
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()


def backoffs(sleeps: OkxSleeps) -> list[float]:
    """제어 메시지·핑 간격을 뺀 나머지 = 재연결 대기."""
    return [v for v in sleeps.values if v not in (CONTROL_INTERVAL, *sleeps.gated)]


def sent_ops(sock: FakeSocket, op: str) -> list[list[dict[str, str]]]:
    return [m["args"] for m in sock.subscriptions() if m["op"] == op]


async def _row_after(frames: list[str]) -> Any:
    stream, connector, _, _, _, store = await build([FakeSocket(frames)])
    await run_until_exhausted(stream, connector)
    return store.get("okx", "BTC")


# --- 행 갱신 (§3.4) ---


async def test_snapshot_becomes_sorted_float_levels_and_updates_row() -> None:
    sock = FakeSocket([snapshot(levels=5)])
    stream, connector, _, _, clock, store = await build([sock])
    clock.now = T0 + 5
    await run_until_exhausted(stream, connector)
    row = store.get("okx", "BTC")
    assert row is not None
    assert len(row.asks) == 5 and len(row.bids) == 5  # books5 — 최대 5단계
    assert all(isinstance(v, float) for lv in row.asks + row.bids for v in lv)
    assert all(len(lv) == 2 for lv in row.asks + row.bids)  # 원소 4개 중 앞 둘만
    assert row.asks == sorted(row.asks) and row.bids == sorted(row.bids, reverse=True)
    assert row.asks[0] == [71_000.0, 0.1] and row.bids[0] == [70_990.0, 0.1]
    assert (row.native_symbol, row.quote) == (BTC, "USDT")
    assert row.price_timestamp == T0  # 호가 시각 = data[0].ts 를 정수로
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    [sub] = sock.subscriptions()
    assert sub == {"op": "subscribe", "args": args_of(BTC)}
    assert sock.closed


async def test_snapshot_is_cut_at_one_million_usdt_but_keeps_first_level() -> None:
    # 단계당 1,000 × 300 = 300,000 USDT → 4단계에서 누적 1,200,000 도달
    row = await _row_after([snapshot(price=1_000.0, size=300.0)])
    assert row is not None and len(row.asks) == 4 and len(row.bids) == 4
    row = await _row_after([snapshot(price=100.0, size=20_000.0)])
    assert (
        row is not None and len(row.asks) == 1 and len(row.bids) == 1
    )  # 첫 단계 2,000,000


async def test_ordered_snapshot_gives_the_same_row_as_a_shuffled_one() -> None:
    """정렬돼 온 스냅샷은 정렬 없이 받은 목록 그대로 행이 된다 — 뒤섞인 스냅샷을 정렬한 것과 같다."""
    ordered = await _row_after([snapshot(ordered=True)])
    shuffled = await _row_after([snapshot()])
    assert ordered is not None and shuffled is not None
    assert ordered.asks == shuffled.asks and ordered.bids == shuffled.bids
    assert len(ordered.asks) == 5 and ordered.asks[0] == [71_000.0, 0.1]


async def test_snapshot_with_a_duplicate_price_keeps_the_later_one() -> None:
    row = await _row_after(
        [
            books(
                BTC,
                asks=[
                    level(71_000, 0.1),
                    level(71_010, 0.2),
                    level(71_010, 0.3),
                    level(71_020, 0.4),
                ],
                bids=[level(70_990, 0.1), level(70_980, 0.2)],
            )
        ]
    )
    assert row is not None
    assert row.asks == [[71_000.0, 0.1], [71_010.0, 0.3], [71_020.0, 0.4]]
    assert row.bids == [[70_990.0, 0.1], [70_980.0, 0.2]]


async def test_older_book_timestamp_is_dropped_but_recorded_and_counted() -> None:
    frames = [
        snapshot(ts=T0 + 100, seq=100),
        snapshot(ts=T0 + 50, seq=200, price=80_000.0),  # 더 오래된 호가 — 버린다
        snapshot(ts=T0 + 100, seq=90, price=72_000.0),  # 같은 시각은 반영한다
    ]
    stream, connector, _, raw, clock, store = await build([FakeSocket(frames)])
    clock.now = T0 + 9
    await run_until_exhausted(stream, connector)
    row = store.get("okx", "BTC")
    assert row is not None and row.asks[0] == [72_000.0, 0.1]
    assert row.price_timestamp == T0 + 100
    assert raw.keys(WS_SOURCE) == [f"orderbook:{BTC}"] * 3  # 원문에는 남는다
    state = store.stream_state("okx")
    assert state is not None and state.last_message_at == T0 + 9  # 수신 시각에는 센다


async def test_seq_id_going_backwards_is_applied_when_ts_is_newer() -> None:
    row = await _row_after(
        [snapshot(ts=T0, seq=100), snapshot(ts=T0 + 1, seq=50, price=80_000.0)]
    )
    assert row is not None and row.asks[0] == [80_000.0, 0.1]  # seqId 는 쓰지 않는다


async def test_empty_book_keeps_the_row_but_one_sided_book_removes_it() -> None:
    frames = [snapshot(), books(BTC, asks=[], bids=[], ts=T0 + 1)]
    stream, connector, _, _, clock, store = await build([FakeSocket(frames)])
    clock.now = T0 + 7
    await run_until_exhausted(stream, connector)
    row = store.get("okx", "BTC")
    assert row is not None and row.asks[0] == [71_000.0, 0.1]  # 빈 북은 무시
    state = store.stream_state("okx")
    assert state is not None and state.last_message_at == T0 + 7  # 수신 시각에는 센다
    row = await _row_after(
        [snapshot(), books(BTC, asks=[level(71_000, 0.1)], bids=[], ts=T0 + 1)]
    )
    assert row is None  # 한쪽만 비면 있던 행을 지운다 (001)


async def test_reconnect_keeps_the_old_row_until_a_new_snapshot() -> None:
    first = FakeSocket([snapshot()])  # 다 주고 끊긴다
    second = GatedSocket()
    stream, _, _, _, _, store = await build([first, second])
    stream.start()
    await until(second.subscribed)
    row = store.get("okx", "BTC")
    assert row is not None and row.asks[0] == [71_000.0, 0.1]  # 행은 메시지로만 바뀐다
    second.push(snapshot(price=72_000.0, ts=T0 + 1))
    await until(second.delivered)
    row = store.get("okx", "BTC")
    assert row is not None and row.asks[0] == [72_000.0, 0.1]
    await stream.aclose()


async def test_trade_picks_the_latest_fill_and_is_held_until_orderbook() -> None:
    frames = [
        trade(trades=[(1.0, T0 - 9)]),  # 호가 전 — 보류
        snapshot(),
        trade(trades=[(2.0, T0 + 3), (3.0, T0 + 9), (2.5, T0 + 5)]),  # ts 최대 = 3.0
    ]
    row = await _row_after(frames)
    assert row is not None and (row.price, row.price_timestamp) == (3.0, T0 + 9)
    assert len(row.asks) == 5  # 체결가는 호가를 건드리지 않는다


async def test_older_trade_than_current_is_ignored_and_same_ts_updates() -> None:
    frames = [
        snapshot(),
        trade(trades=[(3.0, T0 + 9)]),
        trade(trades=[(2.0, T0 + 3)]),  # 엄격히 오래된 체결 — 값이 뒤로 가지 않는다
        trade(trades=[(4.0, T0 + 9)]),  # 같은 시각은 최신으로 본다
    ]
    row = await _row_after(frames)
    assert row is not None and (row.price, row.price_timestamp) == (4.0, T0 + 9)


async def test_held_trade_is_applied_with_first_snapshot_and_mid_without_trade() -> (
    None
):
    row = await _row_after([trade(trades=[(7.0, T0 - 9)]), snapshot()])
    assert row is not None and (row.price, row.price_timestamp) == (7.0, T0 - 9)
    row = await _row_after([snapshot(ts=T0 + 1)])
    assert row is not None
    assert row.price == (71_000.0 + 70_990.0) / 2  # 체결가 없으면 mid
    assert row.price_timestamp == T0 + 1  # 이때 시각은 호가 시각 data[0].ts (§3.4)


async def test_tick_pairs_okx_with_every_domestic_exchange() -> None:
    """틱은 국내×해외 전 조합이라 (upbit, okx)·(bithumb, okx) 행이 나온다 (§3.7)."""
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
    for ex in ("binance", "bybit", "bitget", "okx"):
        store.put_row(
            make_row(
                ex, "BTC", quote="USDT", asks=[[67_000.0, 1.0]], bids=[[66_900.0, 1.0]]
            ),
            now,
        )
    tick = build_tick(store, T0 // 1000, [])
    assert sorted((r.dom, r.fx) for r in tick.rows) == [
        ("bithumb", "binance"),
        ("bithumb", "bitget"),
        ("bithumb", "bybit"),
        ("bithumb", "okx"),
        ("upbit", "binance"),
        ("upbit", "bitget"),
        ("upbit", "bybit"),
        ("upbit", "okx"),
    ]


# --- 심볼 목록 (§3.3) ---


async def test_instruments_keep_only_live_usdt_symbols() -> None:
    stream, _, _, raw, _, _ = await build([])
    extra = [
        {"instId": "ETH-BTC", "baseCcy": "ETH", "quoteCcy": "BTC", "state": "live"},
        {
            "instId": "XYZ-USDT",
            "baseCcy": "XYZ",
            "quoteCcy": "USDT",
            "state": "suspend",
        },
        {
            "instId": "ABC-USDT",
            "baseCcy": "ABC",
            "quoteCcy": "USDT",
            "state": "preopen",
        },
        {"instId": "DEF-USDT", "baseCcy": "DEF", "quoteCcy": "USDT", "state": "test"},
        {
            "instId": "GHI-USDT",
            "baseCcy": "GHI",
            "quoteCcy": "USDT",
            "state": "post_only",
        },
        {
            "instId": "NEW-USDT",
            "baseCcy": "",
            "quoteCcy": "",
            "state": "preopen",
        },  # 상장 공지 직후의 빈 행
        {"instId": "SOL-USDT", "baseCcy": "SOL", "quoteCcy": "USDT", "state": "live"},
        {"instId": "SOL2-USDT", "baseCcy": "SOL", "quoteCcy": "USDT", "state": "live"},
    ]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=instruments_body([BTC], extra))

    assert await stream.refresh(_client(handler)) == 1
    assert stream.bases() == {"BTC", "SOL"}
    assert str(calls[0].url) == INSTRUMENTS_URL
    assert calls[0].url.query == b"instType=SPOT"
    assert raw.keys(REST_SOURCE) == ["symbols:all", "symbols:all"]


async def test_instruments_code_failure_keeps_previous_list() -> None:
    stream, _, _, _, _, _ = await build([])
    body = {"code": "51001", "msg": "Instrument ID does not exist", "data": []}
    with pytest.raises(ExchangeApiError) as exc_info:
        await stream.refresh(_client(lambda r: httpx.Response(200, json=body)))
    err = exc_info.value
    assert (err.kind, err.status_code) == ("bad_response", 200)
    assert err.body is not None and "does not exist" in err.body
    assert stream.bases() == {"BTC"}  # 직전 목록 유지


async def test_instruments_200_with_code_50011_is_rate_limit() -> None:
    stream, _, _, _, _, _ = await build([])
    body = {"code": "50011", "msg": "Rate limit reached", "data": []}
    with pytest.raises(ExchangeApiError) as exc_info:
        await stream.refresh(_client(lambda r: httpx.Response(200, json=body)))
    err = exc_info.value
    assert (err.kind, err.status_code, err.retry_after_sec) == ("rate_limit", 200, None)
    assert stream.bases() == {"BTC"}


@pytest.mark.parametrize(
    ("status", "kind"),
    [(403, "banned"), (429, "rate_limit"), (503, "unavailable"), (400, "bad_request")],
)
async def test_instruments_non_200_is_classified_by_okx_rule(
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


async def test_instruments_timeout_network_and_bad_json() -> None:
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
            _client(lambda r: httpx.Response(200, json={"code": "0", "msg": ""}))
        )
    assert m.value.kind == "bad_response"  # data 없음


async def test_messages_outside_universe_or_symbol_map_are_dropped() -> None:
    sock = FakeSocket([snapshot("ETH-USDT"), snapshot("SOL-USDT"), snapshot(BTC)])
    stream, connector, _, _, _, store = await build(
        [sock], symbols=[BTC, "SOL-USDT"], universe={"BTC"}
    )
    await run_until_exhausted(stream, connector)
    assert store.get("okx", "ETH") is None  # 맵에 없다
    assert store.get("okx", "SOL") is None  # 우주 밖
    assert store.get("okx", "BTC") is not None


# --- 매초 목록 건너뛰기 (§3.3) ---


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """응답 본문 JSON 파싱 횟수 — 건너뛰었는지는 이 수로만 보인다."""
    calls = [0]
    real = httpx.Response.json

    def counting(self: httpx.Response, **kw: Any) -> Any:
        calls[0] += 1
        return real(self, **kw)

    monkeypatch.setattr(httpx.Response, "json", counting)
    return calls


def _instruments_bytes(symbols: list[str], code: str = "0") -> bytes:
    body = instruments_body(symbols)
    body["code"] = code
    return json.dumps(body, separators=(",", ":")).encode()


async def test_same_instruments_body_is_recorded_but_not_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parses = _count_parses(monkeypatch)
    responses = [
        httpx.Response(200, content=_instruments_bytes([BTC])),
        httpx.Response(200, content=_instruments_bytes([BTC])),  # 같은 본문
        httpx.Response(429, content=b"too frequent"),  # 실패는 직전 목록 유지
        httpx.Response(200, content=_instruments_bytes([BTC])),
        httpx.Response(200, content=_instruments_bytes([BTC, "ETH-USDT"])),
    ]
    store, sink = store_with_universe(set())
    raw = RawLog()
    stream = OkxStream(store=store, sink=sink, record=raw)
    client = _client(lambda r: responses.pop(0))
    assert await stream.refresh(client) == 1 and parses[0] == 1
    assert await stream.refresh(client) == 1 and parses[0] == 1  # 파싱·맵 재생성 없음
    assert stream.bases() == {"BTC"}
    with pytest.raises(ExchangeApiError):
        await stream.refresh(client)
    assert (
        await stream.refresh(client) == 1 and parses[0] == 1
    )  # 사이에 실패가 있어도 같다
    assert (
        await stream.refresh(client) == 1 and parses[0] == 2
    )  # 목록이 바뀐 초에만 파싱
    assert stream.bases() == {"BTC", "ETH"}
    assert raw.keys(REST_SOURCE) == ["symbols:all"] * 5  # 원문 기록은 매 응답


async def test_a_code_failure_body_is_not_remembered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """code ≠ "0" 본문은 맵을 만들지 못했으니 기억하지 않는다 — 같은 본문이 다시 와도 다시 실패다."""
    parses = _count_parses(monkeypatch)
    bad = _instruments_bytes([BTC], code="50013")
    store, sink = store_with_universe(set())
    stream = OkxStream(store=store, sink=sink, record=RawLog())
    client = _client(lambda r: httpx.Response(200, content=bad))
    for expected in (1, 2):
        with pytest.raises(ExchangeApiError):
            await stream.refresh(client)
        assert parses[0] == expected


# --- 샤딩·구독 (§3.3) ---


def test_symbols_spread_over_three_shards_and_sibling_channels_share_one() -> None:
    symbols = [f"T{i:04d}-USDT" for i in range(300)]
    counts = [sum(1 for s in symbols if shard_of(s) == k) for k in range(SHARDS)]
    assert sum(counts) == 300 and all(c > 50 for c in counts)
    assert all(shard_of(s) == shard_of(s.lower()) for s in symbols)


def test_shard_hash_is_stable_across_processes() -> None:
    symbols = [f"T{i:04d}-USDT" for i in range(50)] + [BTC, "ETH-USDT"]
    code = (
        "import json, sys; from app.core.streams.okx import shard_of; "
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


async def test_subscribe_messages_carry_at_most_fifty_args_spaced_and_under_64kb() -> (
    None
):
    symbols = symbols_for(BTC_SHARD, 30)  # 60 args → 50·10
    sock = FakeSocket([], hold=True)
    stream, _, sleeps, _, _, store = await build([sock], symbols=symbols)
    stream.start()
    await asyncio.sleep(0.01)
    subs = sock.subscriptions()
    assert [len(m["args"]) for m in subs] == [50, 10]
    assert all(
        len(m["args"]) <= ARGS_PER_MESSAGE and m["op"] == "subscribe" for m in subs
    )
    assert all(
        len(s.encode()) <= ARGS_BYTES_LIMIT for s in sock.sent
    )  # 요청 직렬화 길이
    sent = [a for m in subs for a in m["args"]]
    for s in symbols:
        assert arg("books5", s) in sent and arg("trades", s) in sent
    assert all(set(a) == {"channel", "instId"} for a in sent)  # instType 없음
    assert sleeps.values.count(CONTROL_INTERVAL) == 2  # 요청마다 0.2초
    assert store.stream_state("okx").subscribed == 30  # type: ignore[union-attr]
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
        assert sorted(args, key=str) == sorted(
            (a for s in per_shard[k] for a in args_of(s)), key=str
        )
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
    assert store.get("okx", base_of(a)) is not None
    before = len(sock.sent)
    stream.set_universe({base_of(b), base_of(c)})  # a 상폐, c 상장
    await asyncio.sleep(0.01)
    assert store.get("okx", base_of(a)) is None  # 빠진 심볼의 행은 지운다
    assert store.get("okx", base_of(b)) is not None
    new = [json.loads(s) for s in sock.sent[before:]]
    assert [(m["op"], m["args"]) for m in new] == [
        ("unsubscribe", args_of(a)),
        ("subscribe", args_of(c)),
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


async def test_subscribe_budget_defers_the_385th_request_to_the_next_round(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """한 소켓에 시간당 384회를 보내면 385회째는 보내지 않고 경고 1줄, 다음 회차(1시간 뒤)에 보낸다 (§3.3)."""
    monkeypatch.setattr("app.core.streams.okx.REBALANCE_INTERVAL", 0.01)
    per_request = ARGS_PER_MESSAGE // 2
    symbols = symbols_for(BTC_SHARD, per_request * (SUBSCRIBE_WARN_AT + 1))
    sock = FakeSocket([], hold=True)
    stream, _, _, _, clock, store = await build([sock], symbols=symbols)
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.okx"):
        stream.start()
        await asyncio.sleep(0.05)
        assert len(sock.sent) == SUBSCRIBE_WARN_AT  # 384 보내고 멈춘다
        warnings = [r for r in caplog.records if "다음 회차" in r.getMessage()]
        assert len(warnings) == 1
        assert store.stream_state("okx").subscribed == per_request * SUBSCRIBE_WARN_AT  # type: ignore[union-attr]
        clock.now = T0 + 3_600_001  # 1시간이 지나면 예산이 돌아온다
        await asyncio.sleep(0.05)
    assert len(sock.sent) == SUBSCRIBE_WARN_AT + 1
    assert len(json.loads(sock.sent[-1])["args"]) == ARGS_PER_MESSAGE
    assert store.stream_state("okx").subscribed == len(symbols)  # type: ignore[union-attr]
    await stream.aclose()


async def test_subscribe_ack_per_arg_is_not_a_quote_and_error_rejects() -> None:
    ack_trade = json.dumps(
        {"event": "subscribe", "arg": arg("trades", BTC), "connId": "a4d3ae55"}
    )
    stream, connector, _, raw, clock, store = await build(
        [FakeSocket([ACK, ack_trade, REJECT])]
    )
    clock.now = T0 + 1
    await run_until_exhausted(stream, connector)
    assert raw.keys(WS_SOURCE) == [None, None, None]
    state = store.stream_state("okx")
    assert state is not None and state.last_message_at is None
    assert stream.decode_failures == 0
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "bad_request" and "샤드" in verdict.error.message
    assert (
        "60012" in verdict.error.message and "Invalid request" in verdict.error.message
    )
    assert connector.urls == [WS_URL, WS_URL]  # 거부 뒤 재연결


async def test_notice_is_one_warning_and_neither_quote_nor_decode_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sock = GatedSocket()
    stream, _, _, raw, _, store = await build([sock])
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.okx"):
        stream.start()
        await until(sock.subscribed)
        sock.push(NOTICE)
        await until(sock.delivered)
        await asyncio.sleep(0.01)
    notices = [r for r in caplog.records if "64008" in r.getMessage()]
    assert len(notices) == 1 and "서비스 통지" in notices[0].getMessage()
    assert not sock.closed  # 통지만으로 끊지 않는다 — 끊기면 보통의 재연결
    assert raw.payloads(WS_SOURCE) == [NOTICE] and raw.keys(WS_SOURCE) == [None]
    state = store.stream_state("okx")
    assert state is not None and state.last_message_at is None
    assert stream.decode_failures == 0
    await stream.aclose()


# --- 핑 (§3.2) ---


async def test_ping_goes_out_twenty_seconds_after_the_last_receive_and_pong_clears() -> (
    None
):
    sock = ClosableGatedSocket()
    sleeps = OkxSleeps()
    stream, connector, _, raw, clock, store = await build([sock], sleep=sleeps)
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    assert sleeps.values.count(PING_CHECK_INTERVAL) == 1  # 첫 점검을 기다리는 중
    clock.now = T0 + 15_000
    sleeps.release_ping()  # 15초 조용 — 아직 핑이 아니다
    await asyncio.sleep(0.01)
    assert "ping" not in sock.sent and sleeps.values.count(PING_CHECK_INTERVAL) == 2
    sock.push(snapshot(ts=T0 + 15_000))  # 시세가 흐르면 타이머가 다시 센다
    await until(sock.delivered)
    clock.now = T0 + 30_000
    sleeps.release_ping()  # 마지막 수신(15초)에서 15초 — 아직 아니다
    await asyncio.sleep(0.01)
    assert "ping" not in sock.sent
    clock.now = T0 + 36_000
    sleeps.release_ping()  # 마지막 수신에서 21초 → ping
    await asyncio.sleep(0.01)
    assert sock.sent[-1] == "ping"  # 문자열, JSON 아님
    sock.push(PONG)
    await until(sock.delivered)
    sleeps.release_ping()  # pong 대기 10초 지남 — 이미 받았으니 끊지 않는다
    await asyncio.sleep(0.01)
    assert not sock.closed and connector.urls == [WS_URL]
    state = store.stream_state("okx")
    assert (
        state is not None and state.last_message_at == T0 + 15_000
    )  # pong 은 시세가 아니다
    assert raw.payloads(WS_SOURCE)[-1] == "pong" and raw.keys(WS_SOURCE)[-1] is None
    assert stream.decode_failures == 0  # 디코드 실패도 아니다
    await stream.aclose()


async def test_missing_pong_closes_and_reconnects_with_timeout_kind() -> None:
    first, second = ClosableGatedSocket(), GatedSocket()
    sleeps = OkxSleeps()
    stream, connector, _, _, clock, store = await build([first, second], sleep=sleeps)
    stream.start()
    await until(first.subscribed)
    await asyncio.sleep(0.01)
    clock.now = T0 + 21_000
    sleeps.release_ping()  # 21초 조용 → ping 전송
    await asyncio.sleep(0.01)
    assert first.sent[-1] == "ping"
    sleeps.release_ping()  # pong 없이 10초 → 끊는다
    await until(second.subscribed)
    assert first.closed and connector.urls == [WS_URL, WS_URL]
    state = store.stream_state("okx")
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
        verdict.error.message == "OKX 스트림 정체: 샤드 2 (구독 2종목) 30초 이상 무수신"
    )
    assert (verdict.error.url, verdict.error.status_code) == (WS_URL, None)
    state = store.stream_state("okx")
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
    sleeps = OkxSleeps(control_tickets=0)
    stream, _, _, _, clock, store = await build([sock], sleep=sleeps)
    stream.start()
    await until(sock.subscribed)
    state = store.stream_state("okx")
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
    assert store.stream_state("okx").connected  # type: ignore[union-attr]
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
    assert store.get("okx", base_of(per_shard[0][0])) is not None
    assert store.get("okx", base_of(per_shard[2][0])) is not None
    state = store.stream_state("okx")
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


async def test_quote_frames_only_raise_last_message_at_and_leave_the_rest() -> None:
    """시세 프레임은 집계의 last_message_at 만 올린다 — 시계가 뒤로 간 프레임이 집계를 내리지 않고,
    연결·구독 수는 연결·구독 변경 때 정해진 값 그대로다."""
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, per_shard, clock, store, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    state = store.stream_state("okx")
    assert state is not None and state.connected and state.subscribed == 6
    clock.now = T0 + 5_000
    socks[0].push(snapshot(per_shard[0][0], ordered=True))
    await until(socks[0].delivered)
    assert state.last_message_at == T0 + 5_000
    clock.now = T0 + 3_000  # 시계가 뒤로 간 프레임
    socks[1].push(trade(per_shard[1][0]))
    await until(socks[1].delivered)
    assert state.last_message_at == T0 + 5_000
    assert state.connected and state.subscribed == 6
    clock.now = T0 + 9_000
    socks[2].push(snapshot(per_shard[2][0], ordered=True))
    await until(socks[2].delivered)
    assert state.last_message_at == T0 + 9_000
    await stream.aclose()
    assert not state.connected and state.subscribed == 0


# --- 원문 싱크·프레임 분류 (§3.1·§3.2) ---


async def test_every_frame_and_instruments_body_are_recorded_verbatim() -> None:
    frames = [ACK, snapshot(), "not json", trade(), PONG, NOTICE, REJECT]
    sock = FakeSocket(frames)
    stream, connector, _, raw, clock, _ = await build([sock])
    clock.now = T0 + 1
    await run_until_exhausted(stream, connector)
    assert raw.payloads(WS_SOURCE) == frames
    assert raw.keys(WS_SOURCE) == [
        None,
        f"orderbook:{BTC}",
        None,
        f"trade:{BTC}",
        None,
        None,
        None,
    ]
    assert all(
        e[0] == "okx" and e[2] == T0 + 1 for e in raw.entries if e[1] == WS_SOURCE
    )
    body = json.loads(raw.payloads(REST_SOURCE)[0])
    assert body["data"][0]["instId"] == BTC
    assert raw.keys(REST_SOURCE) == ["symbols:all"]
    verdict = stream.judge(T0)  # 마지막 프레임 event:error = 구독 거부
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "bad_request" and "샤드" in verdict.error.message


async def test_unknown_symbol_frames_keep_their_key_but_do_not_count() -> None:
    other_channel = json.dumps({"arg": arg("tickers", BTC), "data": []})
    no_data = json.dumps({"arg": arg("books5", BTC)})
    sock = FakeSocket(
        [snapshot("ETH-USDT"), ACK, "[1]", b"\xff\xfe", other_channel, no_data]
    )
    stream, connector, _, raw, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    assert raw.keys(WS_SOURCE) == [
        "orderbook:ETH-USDT",
        None,
        None,
        None,
        None,
    ]  # 디코드 불가 바이트는 텍스트가 없어 원문에도 없다
    assert store.get("okx", "ETH") is None
    state = store.stream_state("okx")
    assert state is not None and state.last_message_at is None
    assert stream.decode_failures == 3  # "[1]"·깨진 바이트·data 없는 프레임


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
    assert store.get("okx", "BTC") is not None  # 정체 중에도 행은 남는다


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
        (HandshakeRejected(403), "banned", 403),
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
    assert store.stream_state("okx").connected is False  # type: ignore[union-attr]


async def test_handshake_rejection_body_is_kept_cut_and_recorded_verbatim() -> None:
    body = b'{"code":"50013","msg":"' + b"z" * 600 + b'"}'
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


# --- 장애 격리·종료 ---


async def test_boot_without_any_connection_logs_one_warning_per_shard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream, _, _, store, connector = await build_three([OSError("down")] * SHARDS)
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.okx"):
        await run_until_exhausted(stream, connector)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == SHARDS
    verdict = stream.judge(T0)
    assert (
        verdict is not None
        and verdict.error is not None
        and verdict.error.kind == "network"
    )
    assert store.get_all(exchange="okx") == []


def test_boot_with_every_connection_failing_keeps_health_200(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """lifespan 을 실제로 돌린다 — 소켓 6종은 거부, REST 는 목록만 성공(우주 = BTC + OKX 에만 있는 SOL)."""

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
            return httpx.Response(200, json=instruments_body([BTC, "SOL-USDT"]))
        raise httpx.ConnectError("down", request=request)

    real_client = httpx.AsyncClient
    for module in ("upbit", "bithumb", "binance", "bybit", "bitget", "okx"):
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
        caplog.at_level(logging.WARNING, logger="marketlens.stream.okx"),
        TestClient(app) as client,
    ):
        resp = client.get("/health")
        # 025 — 첫 틱 전엔 starting(503). 앱이 JSON 으로 답하면 떠 있는 것이다
        assert resp.json()["status"] in ("ok", "starting")
        for _ in range(100):  # 우주 확정 → 배정 있는 샤드 연결 시도 → 경고
            if any(r.name == "marketlens.stream.okx" for r in caplog.records):
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
            "okx",
        ]
        state = app.state.live_store.stream_state("okx")
        assert state is not None and not state.connected
    warnings = [r for r in caplog.records if r.name == "marketlens.stream.okx"]
    assert warnings and all("연결 실패" in r.getMessage() for r in warnings)
    shards = {
        shard_of(BTC),
        shard_of("SOL-USDT"),
    }  # 우주 = 국내 ∩ (바이낸스 ∪ 바이빗 ∪ 비트겟 ∪ OKX) = {BTC, SOL}
    assert {
        int(r.getMessage().split("샤드 ")[1].split(" ")[0]) for r in warnings
    } == shards


async def test_aclose_cancels_tasks_and_closes_all_sockets() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, _, _, _, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    before = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    assert len(before) == SHARDS * 2 + 1  # 샤드 3 + 핑 3 + 재조정 1
    await stream.aclose()
    assert all(s.closed for s in socks)
    await asyncio.sleep(0.01)  # 취소된 태스크가 남긴 예외가 없다
    assert all(t.done() for t in before)
    assert stream.judge(T0) is None


async def test_aclose_closes_sockets_concurrently_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.streams.okx.CLOSE_TIMEOUT", 0.2)  # 실제 2초 대신
    socks = [HangingCloseSocket() for _ in range(SHARDS)]
    stream, _, _, store, _ = await build_three(list(socks))
    stream.start()
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    await stream.aclose()
    assert asyncio.get_running_loop().time() - started < 1.0
    assert [s.close_calls for s in socks] == [1, 1, 1]  # 셋을 동시에 닫는다
    assert store.stream_state("okx").connected is False  # type: ignore[union-attr]
