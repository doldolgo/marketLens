"""Hyperliquid perp 커넥터 — meta 목록·구독 한 메시지 하나·코인 검증·한도·bbo·activeAssetCtx·시간당 펀딩·JSON ping·샤드 판정·분류·종료 (스펙 047 §3, §4)."""

import asyncio
import json
import logging
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
from app.core.perp import PerpSink, split_multiplier
from app.core.streams.hyperliquid_perp import (
    INFO_URL,
    PING_CHECK_INTERVAL,
    PONG_TIMEOUT,
    SHARDS,
    SUBSCRIBE_INTERVAL,
    SUBSCRIPTION_LIMIT,
    WS_URL,
    HyperliquidPerpStream,
    next_hour_ms,
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

T0 = 1_787_727_947_000  # 2026-08-26 09:05:47 UTC — 정시가 아니다
HOUR_MS = 3_600_000
SERVER_DIR = Path(__file__).resolve().parents[1]
SRC = "hyperliquid_perp"
WS_SOURCE = "ws:/ws"
META_SOURCE = "rest:/info#meta"
PEPE_SHARD = shard_of("kPEPE")
PONG = '{"channel":"pong"}'
SUB_ACK = (
    '{"channel":"subscriptionResponse","data":{"method":"subscribe",'
    '"subscription":{"type":"bbo","coin":"kPEPE"}}}'
)
ERROR = (
    '{"channel":"error","data":"Error parsing JSON into valid websocket request: '
    'missing field `method`"}'
)


def coins_for(shard: int, n: int) -> list[str]:
    """해시가 그 샤드에 떨어지는 가짜 코인 n개 — 배정 규칙(crc32 % 3)을 그대로 쓴다."""
    out: list[str] = []
    i = 0
    while len(out) < n:
        name = f"T{i:04d}"
        i += 1
        if shard_of(name) == shard:
            out.append(name)
    return out


def base_of(name: str) -> str:
    return split_multiplier(name)[0].upper()


def meta_row(name: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "szDecimals": 0,
        "name": name,
        "maxLeverage": 10,
        "marginTableId": 1,
    }
    row.update(over)
    return row


def meta(names: list[str], extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "universe": [meta_row(n) for n in names] + (extra or []),
        "marginTables": [],
    }


def bbo(
    coin: str = "kPEPE",
    bid: tuple[str, str] | None = ("0.003748", "745320.0"),
    ask: tuple[str, str] | None = ("0.003749", "1337612.0"),
    ts: int = T0,
) -> str:
    """`bbo` 프레임 — bbo[0] 이 bid, bbo[1] 이 ask, 한쪽은 null 일 수 있다."""
    levels: list[dict[str, Any] | None] = []
    for level in (bid, ask):
        if level is None:
            levels.append(None)
        else:
            levels.append({"px": level[0], "sz": level[1], "n": 3})
    return json.dumps(
        {"channel": "bbo", "data": {"coin": coin, "time": ts, "bbo": levels}}
    )


def ctx(
    coin: str = "kPEPE", funding: Any = "-0.0000436026", mark: Any = "0.003749"
) -> str:
    """`activeAssetCtx` 프레임 — 실측 모양 그대로, 쓰지 않는 필드는 null 이어도 된다."""
    return json.dumps(
        {
            "channel": "activeAssetCtx",
            "data": {
                "coin": coin,
                "ctx": {
                    "funding": funding,
                    "openInterest": "7259391576.0",
                    "prevDayPx": "0.004045",
                    "dayNtlVlm": "14376828.77",
                    "premium": None,
                    "oraclePx": "0.003753",
                    "markPx": mark,
                    "midPx": None,
                    "impactPxs": None,
                    "dayBaseVlm": "3624749701.0",
                },
            },
        }
    )


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def rest(
    names: list[str],
    extra: list[dict[str, Any]] | None = None,
    calls: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(200, json=meta(names, extra))

    return _client(handler)


class HlSleeps(Sleeps):
    """핑 점검 간격·pong 대기는 표를 줄 때만 진행한다 — 가짜 sleep 이 즉시 돌아오면 핑 루프가 폭주한다."""

    def __init__(self) -> None:
        super().__init__()
        self._ping = asyncio.Semaphore(0)

    def release_ping(self, n: int = 1) -> None:
        for _ in range(n):
            self._ping.release()

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)
        if seconds in (PING_CHECK_INTERVAL, PONG_TIMEOUT):
            await self._ping.acquire()
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
    names: list[str] | None = None,
    universe: set[str] | None = None,
    sleep: Sleeps | None = None,
) -> tuple[HyperliquidPerpStream, FakeConnector, Sleeps, RawLog, Clock, LiveStore]:
    """meta(fake REST) 로 맵을 채우고 우주를 넣은 커넥터 — 배정 있는 샤드만 연결한다."""
    if names is None:
        names = ["kPEPE"]
    store = LiveStore()
    sink = PerpSink(store)
    if universe is None:
        universe = {base_of(n) for n in names}
    sink.set_universe(universe)
    connector = FakeConnector(outcomes)
    sleeps = sleep
    if sleeps is None:
        sleeps = HlSleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = HyperliquidPerpStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleeps, clock=clock
    )
    await stream.refresh(rest(names))
    stream.set_universe(sink.universe)
    return stream, connector, sleeps, raw, clock, store


async def run_until_exhausted(
    stream: HyperliquidPerpStream, connector: FakeConnector
) -> None:
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()


def backoffs(sleeps: Sleeps) -> list[float]:
    return [
        v
        for v in sleeps.values
        if v not in (SUBSCRIBE_INTERVAL, PING_CHECK_INTERVAL, PONG_TIMEOUT)
    ]


def sent_subs(sock: FakeSocket, method: str = "subscribe") -> list[tuple[str, str]]:
    """보낸 구독·해지 메시지 → (type, coin) 순서대로."""
    return [
        (m["subscription"]["type"], m["subscription"]["coin"])
        for m in sock.subscriptions()
        if m.get("method") == method
    ]


# --- bbo·activeAssetCtx → 행 (§3.4) ---


async def test_bbo_sets_top_of_book_divided_by_k_multiplier_with_interval_one() -> None:
    sock = FakeSocket([SUB_ACK, bbo()])
    stream, connector, _, raw, clock, store = await build([sock])
    clock.now = T0 + 5
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert (row.native_symbol, row.multiplier) == (
        "kPEPE",
        1000,
    )  # 이름 그대로, k = 1,000
    assert (row.bid, row.ask) == (0.003748 / 1000, 0.003749 / 1000)
    assert (row.bid_size, row.ask_size) == (745_320.0 * 1000, 1_337_612.0 * 1000)
    assert row.quote_ts == T0  # `time` 이 호가 시각
    assert row.updated_at == datetime.fromtimestamp((T0 + 5) / 1000, tz=UTC)
    assert row.funding_interval_h == 1  # 목록에 주기가 없다 — 모든 코인 1
    assert connector.urls[0] == WS_URL  # 다 주면 끊기므로 재연결 시도가 하나 더 있다
    assert raw.keys(WS_SOURCE) == [None, "bbo:kPEPE"]  # 구독 응답은 key 없음
    assert sock.closed


async def test_null_side_leaves_the_quote_and_stale_time_is_ignored() -> None:
    frames = [bbo(), bbo(ask=None, ts=T0 + 10), bbo(bid=("0.001", "1"), ts=T0 - 1)]
    sock = FakeSocket(frames)
    stream, connector, _, _, clock, store = await build([sock])
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None and row.quote_ts == T0 and row.bid == 0.003748 / 1000
    state = store.stream_state(SRC)
    assert state is not None and state.last_message_at == T0  # 수신은 셌다
    assert stream.decode_failures == 0


async def test_ctx_sets_hourly_funding_mark_and_next_top_of_hour_and_is_held_before_bbo() -> (
    None
):
    sock = FakeSocket([ctx(), bbo()])  # 펀딩이 호가보다 먼저 — 보류 뒤 첫 bbo 에 실린다
    stream, connector, _, raw, clock, store = await build([sock])
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert row.funding_rate == -0.0000436026  # 시간당 비율 그대로 — 8시간 환산 없음
    assert row.mark == 0.003749 / 1000
    assert row.funding_interval_h == 1
    assert row.next_funding_ms == next_hour_ms(T0) == (T0 // HOUR_MS + 1) * HOUR_MS
    assert raw.keys(WS_SOURCE) == ["activeAssetCtx:kPEPE", "bbo:kPEPE"]


async def test_ctx_null_fields_are_skipped_and_updated_at_moves() -> None:
    sock = FakeSocket([bbo(), ctx(mark=None, funding="0.0000125")])
    stream, connector, _, _, clock, store = await build([sock])
    clock.now = T0 + 1_000
    await run_until_exhausted(stream, connector)
    row = store.perp_row(SRC, "PEPE")
    assert row is not None
    assert row.mark is None and row.funding_rate == 0.0000125
    assert row.updated_at == datetime.fromtimestamp((T0 + 1_000) / 1000, tz=UTC)
    state = store.stream_state(SRC)
    assert state is not None and state.last_message_at == T0 + 1_000  # ctx 도 수신이다


def test_next_hour_is_the_following_boundary_even_at_the_exact_boundary() -> None:
    boundary = (T0 // HOUR_MS + 1) * HOUR_MS
    assert next_hour_ms(boundary - 1) == boundary
    assert next_hour_ms(boundary) == boundary + HOUR_MS  # 경계 정각은 그다음 정시


async def test_frames_for_coins_outside_the_map_are_dropped() -> None:
    sock = FakeSocket([bbo("BTC", ("1", "1"), ("2", "1")), ctx("BTC")])
    stream, connector, _, _, _, store = await build([sock])
    await run_until_exhausted(stream, connector)
    assert store.perp_rows(SRC) == [] and stream.decode_failures == 0


# --- meta (§3.2) ---


async def test_meta_excludes_delisted_keeps_names_and_records_symbols_key() -> None:
    extra = [
        meta_row("LOOM", isDelisted=True),  # 상장폐지 — 구독은 되지만 funding 0 만 온다
        meta_row("kSHIB"),
        meta_row("HYPE", isDelisted=False),
    ]
    calls: list[httpx.Request] = []
    stream, _, _, raw, _, _ = await build([])
    assert await stream.refresh(rest(["kPEPE", "BTC"], extra=extra, calls=calls)) == 1
    assert stream.bases() == {"PEPE", "BTC", "SHIB", "HYPE"}
    assert calls[0].method == "POST" and str(calls[0].url) == INFO_URL
    assert json.loads(calls[0].content) == {"type": "meta"}
    assert calls[0].headers["content-type"] == "application/json"
    assert raw.keys(META_SOURCE) == ["symbols:perp"] * 2


async def test_meta_with_the_same_body_is_not_parsed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream, _, _, raw, _, _ = await build([])
    parsed = 0
    original = httpx.Response.json

    def spy(self: httpx.Response, **kw: Any) -> Any:
        nonlocal parsed
        parsed += 1
        return original(self, **kw)

    monkeypatch.setattr(httpx.Response, "json", spy)
    await stream.refresh(rest(["kPEPE"]))  # build 와 같은 본문
    assert parsed == 0
    await stream.refresh(rest(["kPEPE", "BTC"]))  # 바뀐 본문만 파싱
    assert parsed == 1 and stream.bases() == {"PEPE", "BTC"}
    assert len(raw.keys(META_SOURCE)) == 3  # 원문은 매번


@pytest.mark.parametrize(
    ("status", "kind"),
    [(429, "rate_limit"), (503, "unavailable"), (400, "bad_request")],
)
async def test_meta_failures_are_classified_and_keep_the_list(
    status: int, kind: str
) -> None:
    stream, _, _, _, _, _ = await build([])
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(lambda r: httpx.Response(status, text="no")))
    assert info.value.kind == kind and info.value.status_code == status
    assert stream.bases() == {"PEPE"}


@pytest.mark.parametrize("body", ["nope", "null", '{"marginTables":[]}'])
async def test_meta_without_universe_is_bad_response(body: str) -> None:
    stream, _, _, _, _, _ = await build([])
    with pytest.raises(ExchangeApiError) as info:
        await stream.refresh(_client(lambda r: httpx.Response(200, text=body)))
    assert info.value.kind == "bad_response" and stream.bases() == {"PEPE"}
    with pytest.raises(ExchangeTimeoutError):
        await stream.refresh(
            _client(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("t")))
        )


# --- 구독·샤드 (§3.3) ---


async def test_subscribes_one_message_per_subscription_at_twenty_per_second_same_shard() -> (
    None
):
    coins = coins_for(0, 3)
    sock = GatedSocket()
    stream, _, sleeps, _, _, store = await build([sock], names=coins)
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.02)
    expected = [(t, c) for c in sorted(coins) for t in ("bbo", "activeAssetCtx")]
    assert (
        sent_subs(sock) == expected
    )  # 한 메시지에 구독 하나, 코인의 두 구독이 같은 소켓
    assert all(
        len(m) == 2 and set(m) == {"method", "subscription"}
        for m in sock.subscriptions()
    )
    assert sleeps.values.count(SUBSCRIBE_INTERVAL) == 6 and SUBSCRIBE_INTERVAL == 0.05
    state = store.stream_state(SRC)
    assert state is not None and state.subscribed == 3  # 코인 수 = 구독 수의 절반
    await stream.aclose()


async def test_coins_missing_from_meta_are_never_sent_and_names_keep_their_case() -> (
    None
):
    sock = GatedSocket()
    stream, _, _, _, _, _ = await build(
        [sock], names=["kPEPE"], universe={"PEPE", "FOO", "KPEPE", "pepe"}
    )
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.02)
    coins = {c for _, c in sent_subs(sock)}
    assert coins == {
        "kPEPE"
    }  # 목록에 없는 FOO·KPEPE 는 안 보낸다 — 보내면 소켓이 말없이 닫힌다
    await stream.aclose()


async def test_subscription_limit_keeps_the_front_of_the_list_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    limit = SUBSCRIPTION_LIMIT // 2
    names = [f"C{i:04d}" for i in range(limit + 10)]
    socks = [GatedSocket() for _ in range(SHARDS)]
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.hyperliquid_perp"):
        stream, _, _, _, _, store = await build(list(socks), names=names)
        stream.set_universe(
            {base_of(n) for n in names}
        )  # 같은 우주 다시 — 경고는 한 번
    stream.start()
    for s in socks:
        await until(s.subscribed)
    await asyncio.sleep(0.1)
    sent = {c for s in socks for _, c in sent_subs(s)}
    assert sent == set(names[:limit]) and SUBSCRIPTION_LIMIT == 900
    state = store.stream_state(SRC)
    assert state is not None and state.subscribed == limit
    warnings = [r.message for r in caplog.records if "구독 한도" in r.message]
    assert len(warnings) == 1 and "10코인 제외" in warnings[0]
    await stream.aclose()


def test_coins_spread_over_three_shards_and_hash_is_stable_across_processes() -> None:
    coins = [f"T{i:04d}" for i in range(50)] + ["BTC", "kPEPE", "HYPE"]
    assert {shard_of(c) for c in coins} == {0, 1, 2} and SHARDS == 3
    code = (
        "import json, sys; from app.core.streams.hyperliquid_perp import shard_of; "
        "print(json.dumps([shard_of(s) for s in sys.argv[1:]]))"
    )
    runs = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONPATH": str(SERVER_DIR), "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", code, *coins],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=SERVER_DIR,
        )
        runs.append(json.loads(out.stdout))
    assert runs[0] == runs[1] == [shard_of(c) for c in coins]


async def test_rebalance_unsubscribes_dropped_subscribes_new_and_removes_rows() -> None:
    a, b = coins_for(0, 2)
    sock = GatedSocket()
    stream, _, _, _, _, store = await build([sock], names=[a, b], universe={base_of(a)})
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.02)
    sock.push(bbo(a, ("1", "1"), ("2", "1")))
    await until(sock.delivered)
    assert store.perp_row(SRC, base_of(a)) is not None
    stream.set_universe({base_of(b)})
    await asyncio.sleep(0.05)
    assert sent_subs(sock, "unsubscribe") == [("bbo", a), ("activeAssetCtx", a)]
    assert sent_subs(sock)[-2:] == [("bbo", b), ("activeAssetCtx", b)]
    assert store.perp_row(SRC, base_of(a)) is None
    stream.set_universe({base_of(b)})  # 같은 우주 — 전송 0
    await asyncio.sleep(0.02)
    assert len(sock.sent) == 6
    await stream.aclose()


async def test_subscription_response_pong_and_error_are_not_quotes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sock = GatedSocket()
    stream, connector, _, raw, clock, store = await build([sock])
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    with caplog.at_level(logging.WARNING, logger="marketlens.stream.hyperliquid_perp"):
        for frame in (SUB_ACK, PONG, ERROR, "not json", '{"channel":"x","data":{}}'):
            sock.push(frame)
            await until(sock.delivered)
    await asyncio.sleep(0.01)
    assert raw.keys(WS_SOURCE) == [None] * 5
    assert raw.payloads(WS_SOURCE)[2] == ERROR  # 거부 프레임도 원문 그대로
    state = store.stream_state(SRC)
    assert state is not None and state.last_message_at is None and state.connected
    assert stream.decode_failures == 2  # JSON 아님·모르는 채널
    assert not sock.closed and connector.urls == [WS_URL]  # error 는 연결 유지
    assert any(
        "요청 거부" in r.message and "missing field" in r.message
        for r in caplog.records
    )
    assert stream.judge(T0 + 1_000).ok  # type: ignore[union-attr]
    await stream.aclose()


# --- 핑 (§3.3) ---


async def test_ping_goes_out_thirty_seconds_after_the_last_receive_and_pong_clears() -> (
    None
):
    sock = ClosableGatedSocket()
    sleeps = HlSleeps()
    stream, connector, _, raw, clock, store = await build([sock], sleep=sleeps)
    stream.start()
    await until(sock.subscribed)
    await asyncio.sleep(0.01)
    assert sleeps.values.count(PING_CHECK_INTERVAL) == 1  # 첫 점검을 기다리는 중
    clock.now = T0 + 25_000
    sleeps.release_ping()  # 25초 조용 — 아직 핑이 아니다
    await asyncio.sleep(0.01)
    assert not any("ping" in s for s in sock.sent)
    sock.push(bbo(ts=T0 + 25_000))  # 시세가 흐르면 타이머가 다시 센다
    await until(sock.delivered)
    clock.now = T0 + 50_000
    sleeps.release_ping()  # 마지막 수신(25초)에서 25초 — 아직 아니다
    await asyncio.sleep(0.01)
    assert not any("ping" in s for s in sock.sent)
    clock.now = T0 + 56_000
    sleeps.release_ping()  # 마지막 수신에서 31초 → ping
    await asyncio.sleep(0.01)
    assert json.loads(sock.sent[-1]) == {"method": "ping"}
    sock.push(PONG)
    await until(sock.delivered)
    sleeps.release_ping()  # pong 대기 10초 지남 — 이미 받았으니 끊지 않는다
    await asyncio.sleep(0.01)
    assert not sock.closed and connector.urls == [WS_URL]
    state = store.stream_state(SRC)
    assert (
        state is not None and state.last_message_at == T0 + 25_000
    )  # pong 은 시세가 아니다
    assert raw.payloads(WS_SOURCE)[-1] == PONG and raw.keys(WS_SOURCE)[-1] is None
    await stream.aclose()


async def test_missing_pong_closes_and_reconnects_with_timeout_kind() -> None:
    first, second = ClosableGatedSocket(), GatedSocket()
    sleeps = HlSleeps()
    stream, connector, _, _, clock, store = await build([first, second], sleep=sleeps)
    stream.start()
    await until(first.subscribed)
    await asyncio.sleep(0.01)
    clock.now = T0 + 31_000
    sleeps.release_ping()  # 31초 조용 → ping 전송
    await asyncio.sleep(0.01)
    assert json.loads(first.sent[-1]) == {"method": "ping"}
    sleeps.release_ping()  # pong 없이 10초 → 끊는다
    await until(second.subscribed)
    assert first.closed and connector.urls == [WS_URL, WS_URL]
    state = store.stream_state(SRC)
    assert state is not None and state.connected
    await stream.aclose()
    verdict = stream.judge(T0)  # 종료 뒤 미연결 — 마지막 오류가 pong 없음
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "timeout" and "pong" in verdict.error.message


# --- 판정 (§3.5) ---


async def test_silent_close_is_network_and_reconnects_with_backoff() -> None:
    # 없는 코인을 구독했을 때처럼 에러 프레임 없이 서버가 닫는다 — FakeSocket 은 다 주면 ConnectionClosedOK
    first = FakeSocket([bbo()])
    stream, connector, sleeps, _, _, _ = await build(
        [first, FakeSocket([]), FakeSocket([], hold=True)]
    )
    stream.start()
    await asyncio.sleep(0.05)
    await stream.aclose()
    assert connector.urls == [WS_URL] * 3
    assert backoffs(sleeps)[:2] == [1.0, 2.0]  # 첫 시세로 1 복귀 뒤 다시 1·2
    verdict = stream.judge(T0)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "network" and "샤드" in verdict.error.message


async def test_only_the_silent_shard_fails_and_pong_only_is_still_stale() -> None:
    per_shard = [coins_for(k, 2) for k in range(SHARDS)]
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, _, _, _, clock, _ = await build(
        list(socks), names=[c for g in per_shard for c in g]
    )
    assert stream.judge(T0) is None  # 연결 시도 전
    stream.start()
    for s in socks:
        await until(s.subscribed)
    await asyncio.sleep(0.05)
    assert stream.judge(T0 + 29_999).ok  # type: ignore[union-attr]
    clock.now = T0 + 40_000
    for k in (0, 1):
        socks[k].push(bbo(per_shard[k][0], ("1", "1"), ("2", "1")))
        await until(socks[k].delivered)
    socks[2].push(PONG)  # pong 만 오는 샤드도 정체다
    await until(socks[2].delivered)
    verdict = stream.judge(clock.now)
    assert verdict is not None and not verdict.ok and verdict.error is not None
    assert verdict.error.kind == "stale_stream"
    assert (
        verdict.error.message
        == "Hyperliquid 스트림 정체: 샤드 2 (구독 2종목) 30초 이상 무수신"
    )
    assert verdict.error.url == WS_URL
    socks[2].push(
        ctx(per_shard[2][0])
    )  # activeAssetCtx 도 시세다 — 조용한 코인을 살린다
    await until(socks[2].delivered)
    assert stream.judge(clock.now + 1_000).ok  # type: ignore[union-attr]
    await stream.aclose()


async def test_no_assignment_means_no_verdict_and_no_connection() -> None:
    stream, connector, _, _, _, _ = await build([GatedSocket()], universe=set())
    stream.start()
    await asyncio.sleep(0.02)
    assert stream.judge(T0 + 1_000) is None and connector.urls == []
    await stream.aclose()


# --- 연결 실패·백오프·종료 ---


@pytest.mark.parametrize(
    ("exc", "kind", "status"),
    [
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
    assert f"샤드 {PEPE_SHARD}" in verdict.error.message and verdict.error.url == WS_URL


async def test_backoff_grows_to_thirty_and_resets_after_first_quote() -> None:
    failures: list[FakeSocket | BaseException] = [OSError("x") for _ in range(6)]
    stream, connector, sleeps, _, _, _ = await build([*failures, FakeSocket([bbo()])])
    await run_until_exhausted(stream, connector)
    assert backoffs(sleeps)[:7] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 1.0]


async def test_aclose_cancels_tasks_and_closes_all_sockets() -> None:
    socks = [GatedSocket() for _ in range(SHARDS)]
    stream, _, _, _, _, store = await build(
        list(socks), names=[c for k in range(SHARDS) for c in coins_for(k, 1)]
    )
    stream.start()
    for s in socks:
        await until(s.subscribed)
    await stream.aclose()
    assert all(s.closed for s in socks)
    state = store.stream_state(SRC)
    assert state is not None and not state.connected and state.subscribed == 0
