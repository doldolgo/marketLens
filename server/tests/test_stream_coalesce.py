"""호가 반영 미루기(스펙 001 §3.5-5) — 업비트·빗썸·비트겟·OKX 커넥터의 빠른 길이 받은 자리에서 파싱하던 길과 같은 결과를 내는가.

거래소가 실제로 보내는 꼴(공백 없는 JSON)만 빠른 길로 간다. 같은 프레임을 공백 있는 꼴로 보내면 파싱하는 길로 가므로,
두 꼴의 결과(행·시세·원문 key·수신 시각·실패 수)를 견주면 빠른 길이 기준과 같은지 볼 수 있다. 커넥터 펌프는 가짜 소켓으로
직접 돌린다 — 소켓이 프레임을 다 주면 끊김 예외로 펌프가 끝난다.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosedOK

from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.quotes import QuoteSink
from app.core.streams import bitget, bithumb, okx, upbit
from tests.conftest import RawLog

T0 = 1_787_727_947_000
COMPACT = (",", ":")


class Sock:
    """프레임 (수신 ms, 글) 을 차례로 준다. `between` 은 프레임 사이에 부를 함수(틱 자리 흉내)."""

    def __init__(
        self, frames: list[tuple[int, str]], clock: "Clock", between: Any = None
    ) -> None:
        self.frames = list(frames)
        self.clock = clock
        self.between = between

    async def recv(self) -> str:
        if not self.frames:
            raise ConnectionClosedOK(None, None)
        at, text = self.frames.pop(0)
        if self.between is not None:
            self.between(at)
        self.clock.now = at
        return text

    async def send(self, data: str) -> None:
        pass


class Clock:
    now = T0

    def __call__(self) -> int:
        return self.now


def pump(stream: Any, sock: Sock, shard: Any = None) -> None:
    async def go() -> None:
        try:
            if shard is None:
                await stream._pump(sock)
            else:
                await stream._pump(shard, sock)
        except ConnectionClosedOK:
            pass

    asyncio.run(go())


def rows(store: LiveStore, exchange: str) -> list[tuple[Any, ...]]:
    return sorted(
        (
            r.base,
            r.quote,
            r.native_symbol,
            r.price,
            r.asks,
            r.bids,
            r.price_timestamp,
            r.updated_at,
        )
        for r in store.get_all(exchange=exchange)
    )


def rates(store: LiveStore) -> dict[str, tuple[float, float, Any]]:
    return {k: (v.ask, v.bid, v.updated_at) for k, v in store.rates().items()}


# --- 업비트·빗썸 (같은 꼴, 코드는 따로) ---


def kr_book(
    code: str,
    ask: float,
    bid: float,
    ts: int,
    *,
    sep: tuple[str, str] = COMPACT,
    levels: int = 3,
) -> str:
    units = [
        {
            "ask_price": ask + i,
            "bid_price": bid - i,
            "ask_size": 1.0 + i,
            "bid_size": 2.0 + i,
        }
        for i in range(levels)
    ]
    return json.dumps(
        {
            "type": "orderbook",
            "code": code,
            "timestamp": ts,
            "total_ask_size": 6.0,
            "orderbook_units": units,
        },
        separators=sep,
    )


def kr_ticker(
    code: str, price: float, ts: int, *, sep: tuple[str, str] = COMPACT
) -> str:
    return json.dumps(
        {
            "type": "ticker",
            "code": code,
            "trade_price": price,
            "trade_timestamp": ts,
            "timestamp": ts + 1,
        },
        separators=sep,
    )


def kr_frames(sep: tuple[str, str]) -> list[tuple[int, str]]:
    """BTC 호가 셋(뒤가 앞을 덮는다)·체결 둘·USDT 시세 둘·우주 밖 코인·상태 프레임·JSON 아닌 프레임."""
    return [
        (T0 + 1, kr_book("KRW-BTC", 100.0, 99.0, T0, sep=sep)),
        (T0 + 2, kr_ticker("KRW-BTC", 99.5, T0 + 1, sep=sep)),
        (T0 + 3, kr_book("KRW-USDT", 1400.0, 1399.0, T0, sep=sep)),
        (T0 + 4, kr_book("KRW-BTC", 101.0, 100.0, T0 + 2, sep=sep)),
        (T0 + 5, kr_book("KRW-ZZZ", 5.0, 4.0, T0, sep=sep)),
        (T0 + 6, kr_ticker("KRW-BTC", 100.5, T0 + 3, sep=sep)),
        (T0 + 7, kr_book("KRW-USDT", 1401.0, 1400.0, T0 + 1, sep=sep)),
        (T0 + 8, kr_book("KRW-BTC", 102.0, 101.0, T0 + 4, sep=sep)),
        (T0 + 9, '{"status":"UP"}'),
        (T0 + 10, "not json"),
    ]


@pytest.mark.parametrize(
    "module,cls", [(upbit, "UpbitStream"), (bithumb, "BithumbStream")]
)
def test_domestic_fast_path_matches_parsing_path(module: Any, cls: str) -> None:
    out = []
    for sep in (COMPACT, (", ", ": ")):
        store = LiveStore()
        sink = QuoteSink(store)
        sink.set_universe({"BTC", "ETH"})
        raw = RawLog()
        clock = Clock()
        stream = getattr(module, cls)(store=store, sink=sink, record=raw, clock=clock)
        pump(stream, Sock(kr_frames(sep), clock))
        out.append(
            (
                rows(store, stream.id),
                rates(store),
                stream._state.last_message_at,
                stream.decode_failures,
                [(c[0], c[1], c[2], c[4]) for c in raw.entries],
            )
        )
    fast, slow = out
    assert (
        fast[0] == slow[0] and fast[0][0][3] == 100.5
    )  # 마지막 체결가가 마지막 호가 행에 실린다
    assert fast[1] == slow[1] and fast[1][stream.id][:2] == (1401.0, 1400.0)
    assert fast[2] == slow[2]
    assert fast[3] == slow[3] == 1
    assert fast[4] == slow[4]  # 원문 기록: 같은 순서·source·시각·key


@pytest.mark.parametrize(
    "module,cls", [(upbit, "UpbitStream"), (bithumb, "BithumbStream")]
)
def test_domestic_broken_frame_with_a_valid_head_is_counted_at_settle(
    module: Any, cls: str
) -> None:
    """머리는 맞는데 깨진 프레임(§3.5-5) — 원문은 같은 key 로 남고 수신으로 센다. 그 심볼의 마지막 프레임이면 반영 때
    무효 1건이 되고, 그 초에 앞서 온 정상 프레임은 이미 건너뛰었으므로 행은 직전 반영 상태로 남는다(파싱하는 길은 앞
    프레임을 싣는다 — 받은 자리와 반영이 갈리는 유일한 경우다).
    """
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe({"BTC"})
    raw = RawLog()
    clock = Clock()
    stream = getattr(module, cls)(store=store, sink=sink, record=raw, clock=clock)
    good = kr_book("KRW-BTC", 100.0, 99.0, T0)
    broken = kr_book("KRW-BTC", 101.0, 100.0, T0 + 1)[:-5]
    pump(stream, Sock([(T0 + 1, good), (T0 + 2, broken)], clock))
    assert raw.keys() == ["orderbook:KRW-BTC", "orderbook:KRW-BTC"]
    assert stream._state.last_message_at == T0 + 2
    assert store.get(stream.id, "BTC") is None and stream.decode_failures == 1


@pytest.mark.parametrize(
    "module,cls", [(upbit, "UpbitStream"), (bithumb, "BithumbStream")]
)
def test_domestic_superseded_frames_are_never_parsed(
    module: Any, cls: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe({"BTC"})
    clock = Clock()
    stream = getattr(module, cls)(store=store, sink=sink, clock=clock)
    decoded: list[str] = []
    real = module._decode
    monkeypatch.setattr(
        module, "_decode", lambda text: decoded.append(text) or real(text)
    )
    frames = [
        (T0 + i, kr_book("KRW-BTC", 100.0 + i, 99.0 + i, T0 + i)) for i in range(20)
    ]
    frames.append(
        (T0 + 30, kr_book("KRW-ZZZ", 1.0, 0.5, T0))
    )  # 우주 밖 — 미루지도 파싱하지도 않는다
    pump(stream, Sock(frames, clock))
    assert decoded == []  # 아직 아무도 저장소를 읽지 않았다
    row = store.get(stream.id, "BTC")  # 읽기 = 미룬 것 반영
    assert len(decoded) == 1 and row is not None and row.asks[0][0] == 119.0
    assert (
        stream._state.last_message_at == T0 + 30
    )  # 우주 밖 호가도 수신은 센다(파싱하는 길과 같다)


def test_universe_change_settles_pending_frames_with_the_old_universe() -> None:
    """받은 순간의 우주로 거르던 것과 같게 — 빠지는 코인의 미룬 호가는 지워지고, 새로 드는 코인의 옛 호가는 행이 되지 않는다."""
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe({"BTC"})
    clock = Clock()
    stream = upbit.UpbitStream(store=store, sink=sink, clock=clock)
    pump(
        stream,
        Sock(
            [
                (T0 + 1, kr_book("KRW-BTC", 100.0, 99.0, T0)),
                (T0 + 2, kr_book("KRW-ETH", 10.0, 9.0, T0)),
            ],
            clock,
        ),
    )
    sink.set_universe({"ETH"})
    assert (
        store.get_all() == []
    )  # BTC 는 반영 뒤 지워졌고 ETH 는 우주 밖이던 때 받아 버려졌다


def test_trade_on_pending_book_lands_on_the_row_built_at_settle() -> None:
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe({"BTC"})
    clock = Clock()
    stream = upbit.UpbitStream(store=store, sink=sink, clock=clock)
    pump(stream, Sock([(T0 + 1, kr_book("KRW-BTC", 100.0, 99.0, T0))], clock))
    sink.trade(
        exchange="upbit", base="BTC", price=99.7, price_timestamp=T0 + 5
    )  # 다른 길(예: 슬로 경로) 체결
    row = store.get("upbit", "BTC")
    assert row is not None and (row.price, row.price_timestamp) == (99.7, T0 + 5)


# --- 비트겟 ---


def bg_snapshot(
    symbol: str,
    price: float,
    seq: int,
    *,
    action: str = "snapshot",
    sep: tuple[str, str] = COMPACT,
) -> str:
    asks = [[f"{price + i}", "1.5"] for i in range(3)]
    bids = [[f"{price - 1 - i}", "2.5"] for i in range(3)]
    return json.dumps(
        {
            "action": action,
            "arg": {"instType": "SPOT", "channel": "books15", "instId": symbol},
            "data": [
                {
                    "asks": asks,
                    "bids": bids,
                    "checksum": 0,
                    "seq": seq,
                    "ts": str(T0 + seq),
                }
            ],
            "ts": T0 + seq,
        },
        separators=sep,
    )


def bg_stream(
    store: LiveStore, sink: QuoteSink, raw: RawLog, clock: Clock
) -> bitget.BitgetStream:
    stream = bitget.BitgetStream(store=store, sink=sink, record=raw, clock=clock)
    stream._base_of = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}
    stream._symbol_of = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
    return stream


def bg_frames(sep: tuple[str, str]) -> list[tuple[int, str]]:
    return [
        (T0 + 1, bg_snapshot("BTCUSDT", 100.0, 1, sep=sep)),
        (T0 + 2, bg_snapshot("ETHUSDT", 10.0, 2, sep=sep)),
        (T0 + 3, bg_snapshot("BTCUSDT", 101.0, 3, sep=sep)),
        (
            T0 + 4,
            bg_snapshot("XYZUSDT", 1.0, 4, sep=sep),
        ),  # 맵에 없다 — 시세로 세지 않는다
        (T0 + 5, "not json"),
        # update 는 빠른 길이 아니다 — 미룬 스냅샷을 먼저 반영한 북 위에 얹힌다
        (
            T0 + 6,
            json.dumps(
                {
                    "action": "update",
                    "arg": {
                        "instType": "SPOT",
                        "channel": "books15",
                        "instId": "BTCUSDT",
                    },
                    "data": [
                        {
                            "asks": [["101.0", "0"]],
                            "bids": [],
                            "checksum": 0,
                            "seq": 6,
                            "ts": str(T0 + 6),
                        }
                    ],
                    "ts": T0 + 6,
                },
                separators=sep,
            ),
        ),
    ]


def test_bitget_fast_path_matches_parsing_path_and_keeps_update_order() -> None:
    out = []
    for sep in (COMPACT, (", ", ": ")):
        store = LiveStore()
        sink = QuoteSink(store)
        sink.set_universe({"BTC", "ETH"})
        raw = RawLog()
        clock = Clock()
        stream = bg_stream(store, sink, raw, clock)
        shard = stream._shards[0]
        pump(stream, Sock(bg_frames(sep), clock), shard)
        out.append(
            (
                rows(store, "bitget"),
                stream._state.last_message_at,
                shard.state.last_message_at,
                stream.decode_failures,
                [(c[0], c[1], c[2], c[4]) for c in raw.entries],
            )
        )
    fast, slow = out
    assert fast == slow
    btc = fast[0][0]
    assert (
        btc[0] == "BTC" and btc[4][0][0] == 102.0
    )  # 101.0 단계는 update 로 지워졌다 — 스냅샷 3(101.0 시작) 위에
    assert fast[3] == 1


def test_bitget_reconnect_settles_pending_snapshot_before_clearing_books() -> None:
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe({"BTC"})
    clock = Clock()
    stream = bg_stream(store, sink, RawLog(), clock)
    shard = stream._shards[0]
    pump(stream, Sock([(T0 + 1, bg_snapshot("BTCUSDT", 100.0, 1))], clock), shard)
    assert stream._books_pending
    stream._settle()  # 재연결 자리(북 비우기 직전)가 부르는 것과 같다
    assert not stream._books_pending and store.get("bitget", "BTC") is not None


# --- OKX ---


def okx_book(
    symbol: str,
    price: float,
    ts: int,
    *,
    empty: str = "",
    sep: tuple[str, str] = COMPACT,
) -> str:
    asks = (
        []
        if empty in ("both", "asks")
        else [[f"{price + i}", "1.5", "0", "1"] for i in range(3)]
    )
    bids = (
        []
        if empty == "both"
        else [[f"{price - 1 - i}", "2.5", "0", "1"] for i in range(3)]
    )
    return json.dumps(
        {
            "arg": {"channel": "books5", "instId": symbol},
            "data": [
                {
                    "asks": asks,
                    "bids": bids,
                    "instId": symbol,
                    "ts": str(ts),
                    "seqId": ts,
                }
            ],
        },
        separators=sep,
    )


def okx_frames(sep: tuple[str, str]) -> list[tuple[int, str]]:
    return [
        (T0 + 1, okx_book("BTC-USDT", 100.0, T0 + 10, sep=sep)),
        (
            T0 + 2,
            okx_book("BTC-USDT", 0.0, T0 + 11, empty="both", sep=sep),
        ),  # 빈 북 하트비트 — 행을 안 바꾼다
        (T0 + 3, okx_book("BTC-USDT", 90.0, T0 + 5, sep=sep)),  # 오래된 스냅샷 — 버린다
        (T0 + 4, okx_book("ETH-USDT", 10.0, T0 + 10, sep=sep)),
        (
            T0 + 5,
            okx_book("ETH-USDT", 11.0, T0 + 10, sep=sep),
        ),  # 같은 시각 — 뒤 것이 이긴다
        (
            T0 + 6,
            okx_book("SOL-USDT", 5.0, T0 + 10, empty="asks", sep=sep),
        ),  # 한쪽만 빈 북 — 행을 지운다
        (T0 + 7, okx_book("XYZ-USDT", 1.0, T0 + 10, sep=sep)),  # 맵에 없다
        (T0 + 8, "not json"),
    ]


def test_okx_fast_path_matches_parsing_path() -> None:
    out = []
    for sep in (COMPACT, (", ", ": ")):
        store = LiveStore()
        sink = QuoteSink(store)
        sink.set_universe({"BTC", "ETH", "SOL"})
        raw = RawLog()
        clock = Clock()
        stream = okx.OkxStream(store=store, sink=sink, record=raw, clock=clock)
        stream._base_of = {"BTC-USDT": "BTC", "ETH-USDT": "ETH", "SOL-USDT": "SOL"}
        stream._symbol_of = {v: k for k, v in stream._base_of.items()}
        # 행이 있던 SOL — 한쪽만 빈 북이 지우는 것까지 본다
        store.put_row(
            Row("okx", "SOL", "USDT", "SOL-USDT", 5.0, [[5.0, 1.0]], [[4.0, 1.0]], T0),
            datetime.fromtimestamp(T0 / 1000, tz=UTC),
        )
        shard = stream._shards[0]
        pump(stream, Sock(okx_frames(sep), clock), shard)
        out.append(
            (
                rows(store, "okx"),
                stream._state.last_message_at,
                stream.decode_failures,
                dict(stream._book_ts),
                [(c[0], c[1], c[2], c[4]) for c in raw.entries],
            )
        )
    fast, slow = out
    assert fast == slow
    by_base = {r[0]: r for r in fast[0]}
    assert (
        by_base["BTC"][4][0][0] == 100.0
        and by_base["ETH"][4][0][0] == 11.0
        and "SOL" not in by_base
    )


def test_okx_unparseable_ts_takes_the_parsing_path_after_settling() -> None:
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe({"BTC"})
    clock = Clock()
    stream = okx.OkxStream(store=store, sink=sink, clock=clock)
    stream._base_of = {"BTC-USDT": "BTC"}
    stream._symbol_of = {"BTC": "BTC-USDT"}
    odd = okx_book("BTC-USDT", 90.0, T0 + 5).replace(f'"ts":"{T0 + 5}"', '"ts":"x"')
    pump(
        stream,
        Sock([(T0 + 1, okx_book("BTC-USDT", 100.0, T0 + 10)), (T0 + 2, odd)], clock),
        stream._shards[0],
    )
    row = store.get("okx", "BTC")
    assert row is not None and row.asks[0][0] == 100.0 and stream.decode_failures == 1


# --- 저장소 ---


def test_store_reads_settle_but_the_sink_row_lookup_does_not() -> None:
    store = LiveStore()
    calls: list[int] = []
    store.defer(lambda: calls.append(1))
    assert store.row("upbit", "BTC") is None and calls == []
    assert store.get_all() == [] and calls == [1]
    for read in (store.get_all, store.rates, store.is_empty, store.take_new_rows):
        store.defer(lambda: calls.append(2))
        read()
    assert calls == [1, 2, 2, 2, 2]
    store.defer(
        lambda: store.defer(lambda: calls.append(3))
    )  # 반영 중 다시 미룬 것도 그 자리에서 비운다
    store.get("upbit", "BTC")
    assert calls[-1] == 3
