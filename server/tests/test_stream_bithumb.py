"""빗썸 스트림 커넥터 — 고유 quirk(µs·9시간·유령 호가·200+error)와 공통 규칙 (스펙 001 §3.10, §4)."""

import asyncio
import json

import httpx
import pytest

from app.core.errors import ExchangeApiError
from app.core.streams.bithumb import (
    KST_OFFSET_MS,
    MARKETS_PATH,
    REST_URL,
    WS_URL,
    BithumbStream,
)
from tests.conftest import RawLog
from tests.stream_fakes import (
    Clock,
    FakeConnector,
    FakeSocket,
    HandshakeRejected,
    HangingCloseSocket,
    Sleeps,
    store_with_universe,
    until,
)

T0 = 1_787_727_947_000  # ms


def orderbook(code: str = "KRW-BTC", ts_us: int = (T0 - 100) * 1000) -> str:
    return json.dumps(
        {
            "type": "orderbook",
            "code": code,
            "timestamp": ts_us,  # microseconds
            "orderbook_units": [
                {
                    "ask_price": 101.0,
                    "bid_price": 99.0,
                    "ask_size": 0.0,
                    "bid_size": 0.0,
                },
                {
                    "ask_price": 102.0,
                    "bid_price": 98.0,
                    "ask_size": 3.0,
                    "bid_size": 4.0,
                },
                {
                    "ask_price": 103.0,
                    "bid_price": 97.0,
                    "ask_size": 1.0,
                    "bid_size": 1.0,
                },
            ],
            "stream_type": "REALTIME",
        }
    )


def ticker(code: str = "KRW-BTC", price: float = 100.5, ts: int = T0) -> str:
    return json.dumps(
        {
            "type": "ticker",
            "code": code,
            "trade_price": price,
            "trade_timestamp": ts,
            "timestamp": ts + 1,
            "stream_type": "REALTIME",
        }
    )


def build(
    outcomes: list[FakeSocket | BaseException], *, markets: list[str] | None = None
) -> tuple[BithumbStream, FakeConnector, Sleeps, RawLog, Clock, object]:
    store, sink = store_with_universe({"BTC"})
    connector = FakeConnector(outcomes)
    sleeps = Sleeps()
    raw = RawLog()
    clock = Clock(T0)
    stream = BithumbStream(
        store=store, sink=sink, record=raw, connect=connector, sleep=sleeps, clock=clock
    )
    stream.set_markets(markets if markets is not None else ["KRW-BTC", "KRW-USDT"])
    return stream, connector, sleeps, raw, clock, store


async def run_until_exhausted(stream: BithumbStream, connector: FakeConnector) -> None:
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()


async def test_ghost_levels_dropped_and_orderbook_timestamp_is_ms() -> None:
    sock = FakeSocket([orderbook()])
    stream, connector, _, _, _, store = build([sock])
    await run_until_exhausted(stream, connector)
    row = store.get("bithumb", "BTC")
    assert row is not None
    # 잔량 0 단계가 빠지고 최우선 호가도 잔량>0 인 단계
    assert row.asks == [[102.0, 3.0], [103.0, 1.0]]
    assert row.bids == [[98.0, 4.0], [97.0, 1.0]]
    # 체결가가 없으면 호가 메시지의 거래소 시각 — µs 가 ms 로
    assert row.price_timestamp == T0 - 100
    assert row.price == 100.0  # mid
    [sub] = sock.subscriptions()
    assert sub[1] == {"type": "orderbook", "codes": ["KRW-BTC", "KRW-USDT"]}


async def test_trade_timestamp_one_hour_future_is_shifted_back_nine_hours() -> None:
    future = T0 + KST_OFFSET_MS  # 정확히 9시간 미래 — 벽시계 버그
    sock = FakeSocket([orderbook(), ticker(ts=future), ticker(price=101.0, ts=T0 - 5)])
    stream, connector, _, _, _, store = build([sock])
    stream.start()
    await until(sock.drained)
    row = store.get("bithumb", "BTC")
    assert row is not None and row.price == 101.0
    assert row.price_timestamp == T0 - 5  # 정상값은 그대로
    await stream.aclose()


async def test_future_trade_timestamp_value_is_corrected() -> None:
    future = T0 + KST_OFFSET_MS + 12
    sock = FakeSocket([orderbook(), ticker(ts=future)])
    stream, connector, _, _, _, store = build([sock])
    stream.start()
    await until(sock.drained)
    row = store.get("bithumb", "BTC")
    assert row is not None and row.price_timestamp == T0 + 12
    await stream.aclose()


async def test_frames_recorded_before_interpretation_and_up_not_counted() -> None:
    """모든 프레임이 받은 텍스트 그대로 — 시세는 `종류:심볼` key 와, 나머지는 key=None 으로 (§3.7)."""
    frames = ['{"status":"UP"}', orderbook().encode(), ticker(), "garbage"]
    sock = FakeSocket(frames)
    stream, connector, _, raw, clock, store = build([sock])
    clock.now = T0 + 3
    await run_until_exhausted(stream, connector)
    assert raw.payloads("ws:/websocket/v1") == [
        '{"status":"UP"}',
        orderbook(),
        ticker(),
        "garbage",
    ]
    assert raw.keys("ws:/websocket/v1") == [
        None,
        "orderbook:KRW-BTC",
        "ticker:KRW-BTC",
        None,
    ]
    assert all(e[0] == "bithumb" and e[2] == T0 + 3 for e in raw.entries)
    state = store.stream_state("bithumb")
    assert state is not None and state.last_message_at == T0 + 3
    assert stream.decode_failures == 1


async def test_backoff_and_subscribe_error() -> None:
    sock = FakeSocket(['{"error":{"name":"NO_CODES","message":"x"}}'])
    stream, connector, sleeps, _, _, store = build(
        [OSError("refused"), HandshakeRejected(429, {"Retry-After": "2"}), sock]
    )
    await run_until_exhausted(stream, connector)
    assert sleeps.values[:3] == [1.0, 2.0, 4.0]
    state = store.stream_state("bithumb")
    assert state is not None and state.last_error is not None
    assert state.last_error.kind == "bad_request"
    assert state.url == WS_URL


async def test_handshake_429_is_rate_limit_with_retry_after() -> None:
    stream, connector, _, _, _, store = build(
        [HandshakeRejected(429, {"Retry-After": "2"})]
    )
    await run_until_exhausted(stream, connector)
    err = store.stream_state("bithumb").last_error  # type: ignore[union-attr]
    assert err is not None
    assert (err.kind, err.status_code, err.retry_after_sec, err.url) == (
        "rate_limit",
        429,
        2,
        WS_URL,
    )


async def test_handshake_403_is_bad_request_not_banned() -> None:
    stream, connector, _, _, _, store = build([HandshakeRejected(403)])
    await run_until_exhausted(stream, connector)
    err = store.stream_state("bithumb").last_error  # type: ignore[union-attr]
    assert err is not None and (err.kind, err.status_code) == ("bad_request", 403)


async def test_handshake_rejection_body_is_kept_up_to_500_chars() -> None:
    body = '{"error":{"name":429,"message":"' + "x" * 600 + '"}}'
    stream, connector, _, _, _, store = build(
        [HandshakeRejected(429, body=body.encode())]
    )
    await run_until_exhausted(stream, connector)
    err = store.stream_state("bithumb").last_error  # type: ignore[union-attr]
    assert err is not None and err.body == body[:500]


async def test_handshake_without_body_leaves_body_none() -> None:
    stream, connector, _, _, _, store = build([HandshakeRejected(418)])
    await run_until_exhausted(stream, connector)
    err = store.stream_state("bithumb").last_error  # type: ignore[union-attr]
    assert err is not None and err.body is None


async def test_aclose_finishes_within_budget_when_socket_close_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.streams.bithumb.CLOSE_TIMEOUT", 0.2)
    sock = HangingCloseSocket()
    stream, _, _, _, _, _ = build([sock])
    stream.start()
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    await stream.aclose()
    assert asyncio.get_running_loop().time() - started < 1.0
    assert sock.close_calls == 1


async def test_judge_stale_after_thirty_seconds() -> None:
    sock = FakeSocket([orderbook()], hold=True)
    stream, connector, _, _, clock, _ = build([sock])
    stream.start()
    await until(sock.drained)
    assert stream.judge(T0 + 1000).ok  # type: ignore[union-attr]
    verdict = stream.judge(T0 + 30_000)
    assert verdict is not None and verdict.error is not None
    assert verdict.error.kind == "stale_stream" and verdict.error.url == WS_URL
    await stream.aclose()


async def test_reconnect_after_long_outage_is_ok_before_first_frame() -> None:
    first = FakeSocket([orderbook()])  # 시세 1건 뒤 서버가 끊는다
    second = FakeSocket([], hold=True)  # 재연결 — 아직 프레임이 없다
    stream, _, _, _, clock, store = build([first, second])
    stream.start()
    await until(first.drained)
    clock.now = T0 + 60_000  # 60초 끊겨 있었다
    await until(second.subscribed)
    state = store.stream_state("bithumb")
    assert state is not None and state.connected and state.last_message_at is not None
    assert (
        state.last_message_at < clock.now - 30_000
    )  # 직전 연결의 수신 시각은 오래됐다
    verdict = stream.judge(clock.now + 1000)  # 구독 시각부터 세므로 정체가 아니다
    assert verdict is not None and verdict.ok
    await stream.aclose()


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_markets_krw_filter_and_raw_record() -> None:
    body = [{"market": "KRW-BTC"}, {"market": "BTC-ETH"}]
    stream, _, _, raw, _, _ = build([])
    assert await stream.fetch_markets(
        _client(lambda r: httpx.Response(200, json=body))
    ) == ["KRW-BTC"]
    assert raw.entries[0][:2] == ("bithumb", "rest:/v1/market/all")
    assert raw.entries[0][4] is None  # REST 본문은 key 없이 전량


async def test_fetch_markets_200_with_error_body_is_failure() -> None:
    stream, _, _, _, _, _ = build([])
    err = {"error": {"name": 429, "message": "too many"}}
    with pytest.raises(ExchangeApiError) as info:
        await stream.fetch_markets(_client(lambda r: httpx.Response(200, json=err)))
    assert (info.value.kind, info.value.status_code) == ("rate_limit", 200)
    text_err = {"error": {"name": "NOPE", "message": "x"}}
    with pytest.raises(ExchangeApiError) as info2:
        await stream.fetch_markets(
            _client(lambda r: httpx.Response(200, json=text_err))
        )
    assert info2.value.kind == "bad_response"
    assert info.value.url == info2.value.url == REST_URL + MARKETS_PATH


async def test_fetch_markets_connect_error_is_network() -> None:
    # httpx 전송 예외(DNS·연결 거부) → network, status_code 없음, url 은 REST URL (011 §3.2·§4)
    stream, _, _, raw, _, _ = build([])

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ExchangeApiError) as info:
        await stream.fetch_markets(_client(down))
    exc = info.value
    assert (exc.kind, exc.status_code, exc.body, exc.url) == (
        "network",
        None,
        None,
        REST_URL + MARKETS_PATH,
    )
    assert raw.entries == []


async def test_handshake_rejection_body_is_recorded_verbatim() -> None:
    """거부 응답 본문은 이력의 500자 절단과 별개로 전문이 원문 싱크에 남는다 (001 §3.7)."""
    body = b'{"error":{"name":429,"message":"' + b"y" * 600 + b'"}}'
    stream, connector, _, raw, _, _ = build([HandshakeRejected(429, body=body)])
    await run_until_exhausted(stream, connector)
    assert raw.payloads("ws-handshake:/websocket/v1") == [body.decode()]
    assert raw.keys("ws-handshake:/websocket/v1") == [None]
    assert [e[0] for e in raw.entries if e[1].startswith("ws-handshake")] == ["bithumb"]


async def test_handshake_rejection_without_body_records_nothing() -> None:
    stream, connector, _, raw, _, _ = build([HandshakeRejected(418), OSError("dns")])
    await run_until_exhausted(stream, connector)
    assert raw.payloads("ws-handshake:/websocket/v1") == []
