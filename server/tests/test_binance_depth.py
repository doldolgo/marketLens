"""바이낸스 깊이 스트림 — 샤딩·구독·정체 감지·장애·회귀 (스펙 012 §4).

네트워크 없음. WS 는 메시지를 주입하는 가짜 소켓, REST 는 httpx MockTransport 다.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.collector import Collector
from app.core.config import Settings
from app.core.connectors import binance_depth
from app.core.connectors.binance import BinanceConnector
from app.core.connectors.binance_depth import (
    BinanceDepthStream,
    DepthCache,
    now_ms,
    shard_of,
)
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.outages import OutageTracker
from app.features.spreads.service import build_spreads
from app.main import create_app
from tests.conftest import FakeConnector, make_row

_BREAK = "__break__"


# ── 가짜 WS ───────────────────────────────────────────────────────────────────


class FakeSocket:
    """가짜 소켓 — 주입한 메시지를 순서대로 내주고 보낸 제어 메시지를 기록한다."""

    def __init__(self, hang_close: bool = False) -> None:
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.broken = False
        self.hang_close = hang_close
        self._inbox: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        if self.broken:
            raise ConnectionResetError("소켓이 끊겼다 (테스트)")
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        if self.broken:
            raise ConnectionResetError("소켓이 끊겼다 (테스트)")
        raw = await self._inbox.get()
        if raw == _BREAK:
            raise ConnectionResetError("소켓이 끊겼다 (테스트)")
        return raw

    async def close(self) -> None:
        if self.hang_close:  # close 프레임을 안 돌려주는 상대
            await asyncio.sleep(3600)
        self.closed = True

    def push(self, raw: str) -> None:
        self._inbox.put_nowait(raw)

    def break_now(self) -> None:
        self.broken = True
        self._inbox.put_nowait(_BREAK)  # 대기 중인 recv 를 깨운다

    def streams(self) -> set[str]:
        """SUBSCRIBE/UNSUBSCRIBE 를 순서대로 반영한 현재 구독 스트림 집합."""
        out: set[str] = set()
        for msg in self.sent:
            params = set(msg["params"])  # type: ignore[arg-type]
            if msg["method"] == "SUBSCRIBE":
                out |= params
            else:
                out -= params
        return out


class FakeWs:
    """연결 요청마다 가짜 소켓을 만든다. `fail` 회만큼은 연결 자체가 실패한다."""

    def __init__(self, fail: int = 0, hang_close: bool = False) -> None:
        self.sockets: list[FakeSocket] = []
        self.attempts = 0
        self.fail = fail
        self.hang_close = hang_close

    async def __call__(self, url: str) -> FakeSocket:
        self.attempts += 1
        if self.fail > 0:
            self.fail -= 1
            raise OSError("연결 실패 (테스트)")
        sock = FakeSocket(hang_close=self.hang_close)
        self.sockets.append(sock)
        return sock


async def until(cond, timeout: float = 2.0) -> bool:
    """조건이 참이 될 때까지 이벤트 루프를 양보하며 기다린다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.005)
    return cond()


def depth_message(symbol: str, asks: list[list[float]], bids: list[list[float]]) -> str:
    """결합 스트림의 partial depth 프레임 — 가격·수량은 문자열로 온다."""
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@depth20",
            "data": {
                "lastUpdateId": 1,
                "asks": [[str(p), str(q)] for p, q in asks],
                "bids": [[str(p), str(q)] for p, q in bids],
            },
        }
    )


def all_streams(ws: FakeWs) -> set[str]:
    out: set[str] = set()
    for sock in ws.sockets:
        out |= sock.streams()
    return out


def socket_for(ws: FakeWs, symbol: str) -> FakeSocket:
    stream = f"{symbol.lower()}@depth20"
    return next(s for s in ws.sockets if stream in s.streams())


def binance_client(symbols: tuple[str, ...] = ("BTCUSDT",)) -> httpx.AsyncClient:
    prices = [{"symbol": s, "price": "100.0"} for s in symbols]
    books = [
        {
            "symbol": s,
            "bidPrice": "99.0",
            "bidQty": "2.0",
            "askPrice": "101.0",
            "askQty": "3.0",
        }
        for s in symbols
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/ticker/price":
            return httpx.Response(200, json=prices)
        if request.url.path == "/api/v3/ticker/bookTicker":
            return httpx.Response(200, json=books)
        return httpx.Response(404, text="not found")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── 샤딩 (§3.3, §4) ───────────────────────────────────────────────────────────


def partition(symbols: list[str]) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {0: set(), 1: set(), 2: set()}
    for s in symbols:
        out[shard_of(s)].add(s)
    return out


def test_300_symbols_spread_over_three_shards() -> None:
    symbols = [f"C{i:03d}USDT" for i in range(300)]
    buckets = partition(symbols)
    assert sum(len(v) for v in buckets.values()) == 300
    assert all(len(v) > 0 for v in buckets.values())  # 세 샤드 모두 쓴다
    # 한 심볼은 정확히 한 샤드에만 있다
    assert buckets[0] & buckets[1] == buckets[1] & buckets[2] == set()


def test_shard_assignment_is_stable_across_processes() -> None:
    # 프로세스마다 값이 달라지는 hash() 를 쓰면 재기동 때 전 종목이 재배정된다
    code = (
        "from app.core.connectors.binance_depth import shard_of;"
        "print([shard_of(f'C{i}USDT') for i in range(30)])"
    )
    server_dir = Path(__file__).resolve().parents[1]
    outputs = set()
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(server_dir)}
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=server_dir,
            env=env,
            check=True,
        )
        outputs.add(proc.stdout.strip())
    assert len(outputs) == 1


def test_adding_or_removing_symbols_does_not_move_the_others() -> None:
    base = [f"C{i:03d}USDT" for i in range(300)]
    before = {s: shard_of(s) for s in base}
    changed = [s for s in base if s != "C007USDT"] + ["NEWUSDT", "ALSONEWUSDT"]
    for symbol in changed:
        if symbol in before:
            assert shard_of(symbol) == before[symbol]


# ── 구독 (§3.3, §4) ───────────────────────────────────────────────────────────


async def test_subscribe_message_carries_at_most_100_streams() -> None:
    symbols = {f"C{i:03d}USDT" for i in range(450)}
    ws = FakeWs()
    stream = BinanceDepthStream(
        cache=DepthCache(), symbols=lambda: set(symbols), connect=ws
    )
    stream.start()
    try:
        assert await until(
            lambda: all_streams(ws) == {f"{s.lower()}@depth20" for s in symbols}
        )
        sent = [m for sock in ws.sockets for m in sock.sent]
        assert len(sent) > 3  # 샤드당 150종목이라 한 메시지로는 못 보낸다
        assert all(m["method"] == "SUBSCRIBE" for m in sent)
        assert all(len(m["params"]) <= 100 for m in sent)  # type: ignore[arg-type]
    finally:
        await stream.aclose()


async def test_rebalance_subscribes_new_and_unsubscribes_gone_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binance_depth, "REBALANCE_INTERVAL", 0.02)
    symbols = {"AAAUSDT", "BBBUSDT", "CCCUSDT"}
    ws = FakeWs()
    cache = DepthCache()
    stream = BinanceDepthStream(cache=cache, symbols=lambda: set(symbols), connect=ws)
    stream.start()
    try:
        assert await until(lambda: len(all_streams(ws)) == 3)
        socket_for(ws, "AAAUSDT").push(
            depth_message("AAAUSDT", [[10.0, 1.0]], [[9.0, 1.0]])
        )
        assert await until(lambda: cache.get("AAAUSDT", now_ms()) is not None)

        symbols.discard("AAAUSDT")  # 상폐
        symbols.add("DDDUSDT")  # 신규 상장
        assert await until(
            lambda: (
                all_streams(ws)
                == {"bbbusdt@depth20", "cccusdt@depth20", "dddusdt@depth20"}
            )
        )
        # 구독을 끊은 심볼의 낡은 북은 캐시에서도 사라진다
        assert cache.get("AAAUSDT", now_ms()) is None
    finally:
        await stream.aclose()


# ── watchdog (§3.6, §4) ───────────────────────────────────────────────────────


def usdt_row(exchange: str) -> Row:
    return make_row(exchange, "USDT", asks=[[1385.0, 1000.0]], bids=[[1384.0, 900.0]])


def build_collector(
    store: LiveStore, cache: DepthCache, tracker: OutageTracker
) -> Collector:
    return Collector(
        store=store,
        domestic=[
            FakeConnector("upbit", [[make_row("upbit", "BTC"), usdt_row("upbit")]]),
            FakeConnector("bithumb", [[make_row("bithumb", "BTC")]]),
        ],
        foreign=BinanceConnector(depth=cache),
        client=binance_client(),
        outages=tracker,
    )


async def test_silence_over_30s_fails_the_cycle_but_keeps_the_snapshot() -> None:
    store = LiveStore()
    cache = DepthCache()
    tracker = OutageTracker()
    collector = build_collector(store, cache, tracker)

    await collector.run_cycle()  # 아직 구독 전이라 성공
    kept = store.get("binance", "BTC")
    assert kept is not None
    updated_at = kept.updated_at

    cache.set_subscribed(0, 5, now_ms() - 31_000)  # 구독은 있는데 31초 무수신
    result = await collector.run_cycle()

    assert [f["exchange"] for f in result.failures] == ["binance"]
    open_outage = tracker.open_outage("binance")
    assert open_outage is not None
    assert open_outage.kind == "stale_stream"
    assert (open_outage.status_code, open_outage.url) == (None, None)
    # REST 결과는 유효하므로 표에서 행이 사라지면 안 된다 — 직전 스냅샷이 그대로 남는다
    still = store.get("binance", "BTC")
    assert still is not None and still.updated_at == updated_at


async def test_silence_under_30s_is_not_a_failure() -> None:
    store = LiveStore()
    cache = DepthCache()
    tracker = OutageTracker()
    cache.set_subscribed(0, 5, now_ms() - 29_000)
    result = await build_collector(store, cache, tracker).run_cycle()

    assert result.failures == []
    assert tracker.open_outage("binance") is None
    assert store.get("binance", "BTC") is not None


async def test_no_subscription_means_no_failure() -> None:
    # 기동 직후 — 구독 대상이 0개라 무수신이어도 정체가 아니다
    store = LiveStore()
    cache = DepthCache()
    tracker = OutageTracker()
    result = await build_collector(store, cache, tracker).run_cycle()

    assert result.failures == []
    assert cache.is_stalled(now_ms()) is False
    assert tracker.open_outage("binance") is None


async def test_messages_resume_and_close_the_outage() -> None:
    store = LiveStore()
    cache = DepthCache()
    tracker = OutageTracker()
    collector = build_collector(store, cache, tracker)

    cache.set_subscribed(0, 5, now_ms() - 31_000)
    await collector.run_cycle()
    assert tracker.open_outage("binance") is not None

    cache.note_message(now_ms())  # 스트림 복구
    for _ in range(3):  # 연속 성공 3회에 구간이 닫힌다 (011 §3.3)
        await collector.run_cycle()

    assert tracker.open_outage("binance") is None
    closed = [o for o in tracker.outages() if o.exchange == "binance"]
    assert len(closed) == 1 and closed[0].ended_at is not None


# ── 장애 격리 (§3.7, §4) ──────────────────────────────────────────────────────


def test_startup_without_stream_still_serves(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _fail(url: str) -> object:
        raise OSError("연결 실패 (테스트)")

    async def _noop(self: Collector) -> None:
        return None

    # 수집 루프는 막고(네트워크 금지) 소켓만 실패시킨다
    monkeypatch.setattr(Collector, "run_loop", _noop)
    monkeypatch.setattr(binance_depth, "open_socket", _fail)
    app = create_app()
    app.state.settings = Settings(_env_file=None, influx_token=None, s3_bucket=None)

    with caplog.at_level(logging.WARNING, logger="marketlens.binance_depth"):
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            store = app.state.live_store
            seed_store(store)
            body = client.get("/spreads").json()
            assert len(body["rows"]) == 1
            assert all("depth" not in key.lower() for key in body["rows"][0])
    assert any("연결 실패" in r.getMessage() for r in caplog.records)


async def test_reconnect_backoff_then_cache_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binance_depth, "BACKOFF_START", 0.05)
    ws = FakeWs(fail=3)  # 샤드 3개의 첫 연결이 모두 실패한다
    cache = DepthCache()
    stream = BinanceDepthStream(cache=cache, symbols=lambda: {"BTCUSDT"}, connect=ws)
    started = time.monotonic()
    stream.start()
    try:
        assert await until(lambda: len(all_streams(ws)) == 1)
        # 실패한 3회 + 성공한 3회. 백오프만큼은 기다렸다 다시 붙는다
        assert ws.attempts >= 6
        assert time.monotonic() - started >= 0.05
        socket_for(ws, "BTCUSDT").push(
            depth_message("BTCUSDT", [[101.0, 1.0]], [[99.0, 1.0]])
        )
        assert await until(lambda: cache.get("BTCUSDT", now_ms()) is not None)
    finally:
        await stream.aclose()


async def test_one_dead_shard_keeps_the_other_shards_depth() -> None:
    symbols = {f"C{i:03d}USDT" for i in range(30)}
    ws = FakeWs()
    cache = DepthCache()
    stream = BinanceDepthStream(cache=cache, symbols=lambda: symbols, connect=ws)
    stream.start()
    try:
        assert await until(lambda: len(all_streams(ws)) == len(symbols))
        dead, alive = ws.sockets[0], ws.sockets[1:]
        dead.break_now()
        # 살아 있는 두 샤드는 계속 깊이를 채운다
        fed: list[str] = []
        for sock in alive:
            symbol = next(iter(sock.streams())).split("@")[0].upper()
            sock.push(depth_message(symbol, [[10.0, 1.0]], [[9.0, 1.0]]))
            fed.append(symbol)
        assert await until(lambda: all(cache.get(s, now_ms()) is not None for s in fed))
    finally:
        await stream.aclose()


async def test_shutdown_cancels_tasks_and_closes_sockets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = FakeWs()
    stream = BinanceDepthStream(
        cache=DepthCache(), symbols=lambda: {"BTCUSDT", "ETHUSDT"}, connect=ws
    )
    with caplog.at_level(logging.WARNING, logger="marketlens.binance_depth"):
        stream.start()
        assert await until(lambda: len(ws.sockets) == 3)
        await stream.aclose()
    assert all(sock.closed for sock in ws.sockets)
    assert caplog.records == []  # 종료 로그에 잔여 예외 없음


async def test_shutdown_does_not_wait_forever_on_a_socket_that_never_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binance_depth, "CLOSE_TIMEOUT", 0.05)
    ws = FakeWs(hang_close=True)
    stream = BinanceDepthStream(
        cache=DepthCache(), symbols=lambda: {"BTCUSDT"}, connect=ws
    )
    stream.start()
    assert await until(lambda: len(ws.sockets) == 3)

    started = time.monotonic()
    await stream.aclose()
    assert time.monotonic() - started < 1.0  # 상한 안에 끝난다 (§3.4)


# ── 회귀 — HTTP 계약 무변경 (§2, §4) ──────────────────────────────────────────


def seed_store(store: LiveStore, *, depth: bool = False) -> LiveStore:
    """upbit(BTC·USDT) + binance(BTC) — /spreads 행 1개. 지금 수집한 것으로 둔다."""
    now = datetime.now(UTC)
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit", "BTC", asks=[[101_000_000.0, 1.0]], bids=[[100_000_000.0, 1.0]]
            ),
            make_row("upbit", "USDT", asks=[[1401.0, 1.0]], bids=[[1399.0, 1.0]]),
        ],
        now,
    )
    binance_btc = make_row(
        "binance", "BTC", asks=[[71_000.0, 1.0]], bids=[[70_900.0, 1.0]]
    )
    if depth:
        binance_btc.depth_asks = [[71_000.0, 1.0], [71_010.0, 2.0]]
        binance_btc.depth_bids = [[70_900.0, 1.0], [70_890.0, 2.0]]
        binance_btc.depth_at = 1_757_000_000_000
    store.replace_exchange("binance", [binance_btc], now)
    store.set_rate("upbit", 1401.0, 1399.0, now)
    store.mark_received(int(now.timestamp()))
    return store


def normalize(payload: dict[str, object]) -> dict[str, object]:
    """응답 시각과 age 는 벽시계라 호출마다 다르다 — 계약 비교에서 뺀다."""
    payload.pop("fetched_at")
    for row in payload["rows"]:  # type: ignore[union-attr]
        row.pop("age")
    return payload


def test_spreads_payload_is_identical_with_and_without_depth() -> None:
    plain = normalize(build_spreads(seed_store(LiveStore())).model_dump())
    with_depth = normalize(
        build_spreads(seed_store(LiveStore(), depth=True)).model_dump()
    )
    assert plain == with_depth
    assert all("depth" not in key for key in with_depth["rows"][0])  # type: ignore[index]
