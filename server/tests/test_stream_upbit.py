"""업비트 스트림 커넥터 — 디코드·원문 싱크·판정·재연결 백오프·마켓 목록 (스펙 001 §3.10·§3.11, §4)."""

import asyncio
import json

import httpx
import pytest

from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.streams.upbit import WS_URL, UpbitStream
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
STALE_LIMIT = 30_000  # §3.8 무수신 상한(ms)


def orderbook(code: str = "KRW-BTC", levels: int = 2, ts: int = T0 - 100) -> str:
    units = [
        {
            "ask_price": 109_950_000.0 + i * 1000,
            "bid_price": 109_880_000.0 - i * 1000,
            "ask_size": 0.01 + i * 0.001,  # 30단계 합이 누적 상한(10억) 아래
            "bid_size": 0.02 + i * 0.001,
        }
        for i in range(levels)
    ]
    return json.dumps(
        {
            "type": "orderbook",
            "code": code,
            "timestamp": ts,
            "orderbook_units": units,
            "stream_type": "SNAPSHOT",
            "level": 0,
        }
    )


def ticker(code: str = "KRW-BTC", price: float = 109_868_000.0, ts: int = T0) -> str:
    return json.dumps(
        {
            "type": "ticker",
            "code": code,
            "trade_price": price,
            "trade_timestamp": ts,
            "timestamp": ts + 2,
            "stream_type": "REALTIME",
        }
    )


UP = '{"status":"UP"}'


def build(
    outcomes: list[FakeSocket | BaseException],
    *,
    universe: set[str] | None = None,
    markets: list[str] | None = None,
) -> tuple[UpbitStream, FakeConnector, Sleeps, RawLog, Clock, object]:
    store, sink = store_with_universe(universe if universe is not None else {"BTC"})
    connector = FakeConnector(outcomes)
    sleeps = Sleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = UpbitStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleeps, clock=clock
    )
    stream.set_markets(markets if markets is not None else ["KRW-BTC", "KRW-USDT"])
    return stream, connector, sleeps, raw, clock, store


async def run_until_exhausted(stream: UpbitStream, connector: FakeConnector) -> None:
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()


async def test_orderbook_replaces_row_levels_and_updated_at() -> None:
    sock = FakeSocket([orderbook(levels=30)])
    stream, connector, _, _, clock, store = build([sock])
    clock.now = T0 + 5
    await run_until_exhausted(stream, connector)
    row = store.get("upbit", "BTC")
    assert row is not None
    assert len(row.asks) == 30 and len(row.bids) == 30
    assert row.asks[0] == [109_950_000.0, 0.01] and row.bids[0] == [109_880_000.0, 0.02]
    assert row.updated_at is not None
    assert int(row.updated_at.timestamp() * 1000) == T0 + 5
    assert row.native_symbol == "KRW-BTC" and row.quote == "KRW"
    # 구독 메시지 1회: ticket·orderbook·ticker·format, codes 대문자
    [sub] = sock.subscriptions()
    assert [list(part)[0] for part in sub] == ["ticket", "type", "type", "format"]
    assert sub[1] == {"type": "orderbook", "codes": ["KRW-BTC", "KRW-USDT"]}
    assert sub[2]["codes"] == ["KRW-BTC", "KRW-USDT"]
    assert sock.closed


async def test_ticker_updates_price_and_is_held_until_orderbook() -> None:
    sock = FakeSocket([ticker(price=1.0, ts=T0 - 9), orderbook(), ticker(price=2.0)])
    stream, connector, _, _, _, store = build([sock])
    await run_until_exhausted(stream, connector)
    row = store.get("upbit", "BTC")
    assert row is not None
    assert (row.price, row.price_timestamp) == (2.0, T0)
    assert len(row.asks) == 2  # 체결가는 호가를 건드리지 않는다


async def test_held_trade_is_applied_with_first_orderbook() -> None:
    sock = FakeSocket([ticker(price=7.0, ts=T0 - 9), orderbook()])
    stream, connector, _, _, _, store = build([sock])
    await run_until_exhausted(stream, connector)
    row = store.get("upbit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (7.0, T0 - 9)


async def test_binary_frames_are_decoded_and_usdt_feeds_rate() -> None:
    usdt = orderbook("KRW-USDT").encode()
    sock = FakeSocket([usdt, orderbook().encode()])
    stream, connector, _, _, _, store = build([sock])
    await run_until_exhausted(stream, connector)
    rate = store.get_rate("upbit")
    assert rate is not None and (rate.ask, rate.bid) == (109_950_000.0, 109_880_000.0)
    assert store.get("upbit", "USDT") is None
    assert store.get("upbit", "BTC") is not None


async def test_every_frame_is_recorded_verbatim_before_interpretation() -> None:
    frames = [UP, orderbook(), '{"error":{"name":"NO_TICKET","message":"x"}}']
    sock = FakeSocket(frames)
    stream, connector, _, raw, clock, _ = build([sock])
    clock.now = T0 + 1
    await run_until_exhausted(stream, connector)
    assert raw.payloads("ws:/websocket/v1") == frames
    assert all(e[0] == "upbit" and e[2] == T0 + 1 for e in raw.entries)


async def test_status_up_and_invalid_frames_do_not_count_as_quotes() -> None:
    sock = FakeSocket([UP, "not json", b"\xff\xfe", "[1,2]", '{"type":"trade"}'])
    stream, connector, _, _, _, store = build([sock])
    stream.start()
    await until(sock.drained)
    state = store.stream_state("upbit")
    assert state is not None and state.last_message_at is None
    assert stream.decode_failures == 3
    await stream.aclose()


async def test_quote_frame_sets_last_message_at_and_subscribed() -> None:
    sock = FakeSocket([orderbook()])
    stream, connector, _, _, clock, store = build([sock])
    clock.now = T0 + 42
    stream.start()
    await until(sock.drained)
    state = store.stream_state("upbit")
    assert state is not None and state.last_message_at == T0 + 42
    assert state.url == WS_URL
    await stream.aclose()


async def test_subscribe_error_response_is_bad_request() -> None:
    sock = FakeSocket(['{"error":{"name":"WRONG_FORMAT","message":"bad"}}'])
    stream, connector, sleeps, _, _, store = build([sock])
    await run_until_exhausted(stream, connector)
    state = store.stream_state("upbit")
    assert state is not None and state.last_error is not None
    assert state.last_error.kind == "bad_request"
    assert "WRONG_FORMAT" in state.last_error.message
    assert state.connected is False and sleeps.values[:1] == [1.0]


async def test_backoff_is_not_reset_by_subscribe_before_first_quote() -> None:
    sock = FakeSocket(['{"error":{"name":"NO_CODES","message":"x"}}'])
    stream, connector, sleeps, _, _, store = build(
        [OSError("refused"), OSError("refused"), sock]
    )
    await run_until_exhausted(stream, connector)
    # 구독 메시지를 보낸 것만으로는 성공이 아니다 — 거부 응답 뒤 세 번째 대기는 1 이 아니라 4 (§3.11)
    assert sleeps.values[:3] == [1.0, 2.0, 4.0]
    state = store.stream_state("upbit")
    assert state is not None and state.last_error is not None
    assert state.last_error.kind == "bad_request"


async def test_backoff_grows_to_thirty_and_resets_after_first_quote() -> None:
    failures: list[FakeSocket | BaseException] = [OSError("refused")] * 7
    good = FakeSocket([orderbook()])
    stream, connector, sleeps, _, _, store = build([*failures, good, OSError("again")])
    await run_until_exhausted(stream, connector)
    # 1·2·4·8·16·30·30 으로 자라고, 시세를 받은 뒤 끊기면 1 로 돌아온다
    assert sleeps.values[:7] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert sleeps.values[7] == 1.0
    assert sleeps.values[8] == 2.0  # 곧바로 또 실패하면 1 에서 다시 자란다
    state = store.stream_state("upbit")
    assert state is not None and state.last_error is not None
    assert state.last_error.kind == "network"


@pytest.mark.parametrize(
    ("exc", "kind", "status", "retry"),
    [
        (OSError("dns"), "network", None, None),
        (TimeoutError(), "timeout", None, None),
        (HandshakeRejected(429, {"Retry-After": "3"}), "rate_limit", 429, 3),
        (HandshakeRejected(418), "banned", 418, None),
        (HandshakeRejected(403), "bad_request", 403, None),  # 403 banned 는 바이낸스만
        (HandshakeRejected(503), "unavailable", 503, None),
        (HandshakeRejected(400), "bad_request", 400, None),
        (HandshakeRejected(302), "bad_response", 302, None),
        (RuntimeError("weird"), "bad_response", None, None),
    ],
)
async def test_connect_failures_are_classified(
    exc: BaseException, kind: str, status: int | None, retry: int | None
) -> None:
    stream, connector, _, _, _, store = build([exc])
    await run_until_exhausted(stream, connector)
    state = store.stream_state("upbit")
    assert state is not None and state.last_error is not None
    err = state.last_error
    assert (err.kind, err.status_code, err.url, err.retry_after_sec) == (
        kind,
        status,
        WS_URL,
        retry,
    )
    assert state.connected is False


async def test_judge_pending_then_ok_then_stale() -> None:
    hold = asyncio.Event()

    class Slow(FakeSocket):
        async def recv(self) -> str | bytes:
            await hold.wait()
            return await super().recv()

    sock = Slow([orderbook()], hold=True)
    stream, connector, _, _, clock, store = build([sock])
    assert stream.judge(T0) is None  # 연결 시도 전 — 판정 대상 아님
    stream.start()
    await asyncio.sleep(0.01)
    # 연결·구독은 됐는데 아직 시세가 없다 — 구독 시각부터 센다
    verdict = stream.judge(T0 + 1000)
    assert verdict is not None and verdict.ok
    hold.set()
    await until(sock.drained)
    assert stream.judge(T0 + 29_999).ok  # type: ignore[union-attr]
    stale = stream.judge(T0 + 30_000)
    assert stale is not None and not stale.ok and stale.error is not None
    assert (stale.error.kind, stale.error.url, stale.error.status_code) == (
        "stale_stream",
        WS_URL,
        None,
    )
    await stream.aclose()


async def test_stale_stream_recovers_when_a_quote_frame_returns() -> None:
    sock = GatedSocket()
    stream, _, _, _, clock, _ = build([sock])
    stream.start()
    sock.push(orderbook())
    await until(sock.delivered)
    assert stream.judge(T0 + 1000).ok  # type: ignore[union-attr]
    clock.now = T0 + 40_000  # 40초 무수신 → 정체
    stale = stream.judge(clock.now)
    assert stale is not None and stale.error is not None
    assert stale.error.kind == "stale_stream"
    sock.push(orderbook(ts=clock.now - 50))  # 시세 프레임이 다시 오면 성공으로 돌아온다
    await until(sock.delivered)
    assert stream.judge(clock.now + 1000).ok  # type: ignore[union-attr]
    await stream.aclose()


async def test_reconnect_after_long_outage_is_ok_before_first_frame() -> None:
    first = FakeSocket([orderbook()])  # 시세 1건 뒤 서버가 끊는다
    second = FakeSocket([], hold=True)  # 재연결 — 아직 프레임이 없다
    stream, _, _, _, clock, store = build([first, second])
    stream.start()
    await until(first.drained)
    clock.now = T0 + 60_000  # 60초 끊겨 있었다
    await until(second.subscribed)
    state = store.stream_state("upbit")
    assert state is not None and state.connected and state.last_message_at is not None
    assert (
        state.last_message_at < clock.now - STALE_LIMIT
    )  # 직전 연결의 수신 시각은 오래됐다
    verdict = stream.judge(clock.now + 1000)  # 구독 시각부터 세므로 정체가 아니다
    assert verdict is not None and verdict.ok
    stale = stream.judge(clock.now + STALE_LIMIT)  # 재구독 뒤에도 30초 무수신이면 정체
    assert stale is not None and stale.error is not None
    assert stale.error.kind == "stale_stream"
    await stream.aclose()


async def test_aclose_finishes_within_budget_when_socket_close_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.streams.upbit.CLOSE_TIMEOUT", 0.2)  # 실제 2초 대신
    sock = HangingCloseSocket()
    stream, _, _, _, _, store = build([sock])
    stream.start()
    await asyncio.sleep(0.01)  # 연결·구독까지
    started = asyncio.get_running_loop().time()
    await stream.aclose()
    assert asyncio.get_running_loop().time() - started < 1.0
    assert sock.close_calls == 1
    assert store.stream_state("upbit").connected is False  # type: ignore[union-attr]


async def test_set_markets_while_connected_resubscribes_full_list() -> None:
    hold = asyncio.Event()

    class Slow(FakeSocket):
        async def recv(self) -> str | bytes:
            await hold.wait()
            return await super().recv()

    sock = Slow([orderbook()], hold=True)
    stream, connector, _, _, _, _ = build([sock])
    stream.start()
    await asyncio.sleep(0.01)
    stream.set_markets(["KRW-BTC", "KRW-USDT", "krw-eth"])
    stream.set_markets(["KRW-ETH", "KRW-BTC", "KRW-USDT"])  # 같은 목록 — 재구독 없음
    await asyncio.sleep(0.01)
    subs = sock.subscriptions()
    assert len(subs) == 2
    assert subs[1][1]["codes"] == ["KRW-BTC", "KRW-ETH", "KRW-USDT"]
    hold.set()
    await stream.aclose()


async def test_no_connection_without_markets() -> None:
    stream, connector, sleeps, _, _, _ = build([FakeSocket([])], markets=[])
    stream.start()
    await asyncio.sleep(0.01)
    assert connector.urls == [] and sleeps.values and set(sleeps.values) == {1.0}
    await stream.aclose()


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_markets_filters_krw_and_records_body() -> None:
    body = [{"market": "KRW-BTC"}, {"market": "BTC-ETH"}, {"market": "KRW-USDT"}]
    stream, _, _, raw, _, _ = build([])
    client = _client(lambda r: httpx.Response(200, json=body))
    assert await stream.fetch_markets(client) == ["KRW-BTC", "KRW-USDT"]
    [entry] = raw.entries
    assert entry[:2] == ("upbit", "rest:/v1/market/all")
    assert json.loads(entry[3]) == body


@pytest.mark.parametrize(
    ("status", "kind"),
    [(429, "rate_limit"), (418, "banned"), (503, "unavailable"), (400, "bad_request")],
)
async def test_fetch_markets_non_200_is_exchange_api_error(
    status: int, kind: str
) -> None:
    stream, _, _, _, _, _ = build([])
    client = _client(
        lambda r: httpx.Response(status, text="x" * 600, headers={"Retry-After": "7"})
    )
    with pytest.raises(ExchangeApiError) as info:
        await stream.fetch_markets(client)
    exc = info.value
    assert (exc.kind, exc.status_code, exc.http_status, exc.code) == (
        kind,
        status,
        502,
        "exchange_api_error",
    )
    assert exc.body is not None and len(exc.body) == 500
    assert exc.retry_after_sec == 7


async def test_fetch_markets_timeout_and_bad_json() -> None:
    stream, _, _, _, _, _ = build([])

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ExchangeTimeoutError) as info:
        await stream.fetch_markets(_client(slow))
    assert (info.value.kind, info.value.http_status) == ("timeout", 504)
    with pytest.raises(ExchangeApiError) as bad:
        await stream.fetch_markets(
            _client(lambda r: httpx.Response(200, text="<html>"))
        )
    assert bad.value.kind == "bad_response"
