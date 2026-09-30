"""`/history/events`·`/history/candles` 공유 캐시 (013 §3.4·014 §3.6, 2026-09-28).

같은 키는 조회 1번·같은 바이트, TTL(사건 60초·열린 봉 청크 30초·닫힌 청크 600초), 조회 중인 키는 같이 기다림,
오류는 담지 않음, gzip 을 받으면 미리 압축한 바이트, 키 수·바이트 상한.
"""

import asyncio
import gzip
import threading
import time

import httpx

from app.core.influx import CandleRow, EventListRow
from app.features.history.cache import (
    CANDLES_CACHE_MAX_BYTES,
    CANDLES_CACHE_MAX_KEYS,
    EVENTS_CACHE_MAX_BYTES,
    Encoded,
    HistoryCache,
    ResponseCache,
    encode_both,
)
from app.features.history.tests.helpers import FakeInfluxReader, make_client

T0 = 1_700_000_000


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class CountingReader(FakeInfluxReader):
    """조회 횟수를 세고, `gate` 가 있으면 열릴 때까지 조회를 붙잡는다(스레드에서 돈다)."""

    def __init__(self) -> None:
        super().__init__()
        self.event_queries = 0
        self.candle_queries = 0
        self.gate: threading.Event | None = None

    def query_premium_events(self, **kw: object) -> list[EventListRow]:  # type: ignore[override]
        self.event_queries += 1
        if self.gate is not None:
            assert self.gate.wait(5)
        return super().query_premium_events(**kw)  # type: ignore[arg-type]

    def query_candles(self, bucket: str, **kw: object) -> list[CandleRow]:  # type: ignore[override]
        self.candle_queries += 1
        return super().query_candles(bucket, **kw)  # type: ignore[arg-type]


def client_with(reader: CountingReader, clock: Clock):  # noqa: ANN201
    client = make_client(reader)
    client.app.state.history_cache = HistoryCache(clock=clock)
    return client


EVENTS = {"start": T0, "end": T0 + 3_600, "dir": "kimp"}


def test_same_event_key_is_built_once_and_served_as_the_same_bytes_for_60s() -> None:
    reader, clock = CountingReader(), Clock()
    reader.seed_event("BTC", T0 + 10, T0 + 100)
    client = client_with(reader, clock)
    first = client.get("/history/events", params=EVENTS)
    clock.now += 59
    # 캐시 동안 바뀐 저장소는 아직 안 보인다 — 최대 60초 늦다
    reader.seed_event("ETH", T0 + 20, T0 + 200)
    second = client.get("/history/events", params=EVENTS)
    assert reader.event_queries == 1
    assert second.content == first.content
    assert second.json()["count"] == 1
    # 다른 키(방향)는 따로 만든다
    client.get("/history/events", params={**EVENTS, "dir": "reverse"})
    assert reader.event_queries == 2
    # 60초가 지나면 새로 조회한다
    clock.now += 2
    third = client.get("/history/events", params=EVENTS)
    assert reader.event_queries == 3 and third.json()["count"] == 2


def test_base_is_part_of_the_key_in_upper_case() -> None:
    reader, clock = CountingReader(), Clock()
    client = client_with(reader, clock)
    client.get("/history/events", params={**EVENTS, "base": "btc"})
    client.get("/history/events", params={**EVENTS, "base": "BTC"})
    assert reader.event_queries == 1


def test_gzip_clients_get_the_precompressed_bytes_and_others_the_raw_bytes() -> None:
    reader, clock = CountingReader(), Clock()
    for i in range(50):
        reader.seed_event(f"C{i}", T0 + i, T0 + 100 + i)
    client = client_with(reader, clock)
    with client.stream(
        "GET", "/history/events", params=EVENTS, headers={"Accept-Encoding": "gzip"}
    ) as res:
        assert res.headers["content-encoding"] == "gzip"
        # CORS 미들웨어(Starlette 1.7+)가 Origin 을 덧붙일 수 있다 — Accept-Encoding 이 들어 있으면 된다
        assert "Accept-Encoding" in [v.strip() for v in res.headers["vary"].split(",")]
        zipped = b"".join(res.iter_raw())
    with client.stream(
        "GET", "/history/events", params=EVENTS, headers={"Accept-Encoding": "identity"}
    ) as res:
        assert "content-encoding" not in res.headers
        plain = b"".join(res.iter_raw())
    assert gzip.decompress(zipped) == plain  # 같은 캐시 칸의 두 모양
    assert reader.event_queries == 1


def test_errors_are_not_cached() -> None:
    reader, clock = CountingReader(), Clock()
    client = client_with(reader, clock)
    reader.fail = True
    assert client.get("/history/events", params=EVENTS).status_code == 503
    reader.fail = False
    assert client.get("/history/events", params=EVENTS).status_code == 200
    assert reader.event_queries == 2


async def test_concurrent_requests_for_one_key_share_one_build() -> None:
    reader, clock = CountingReader(), Clock()
    reader.seed_event("BTC", T0 + 10, T0 + 100)
    reader.gate = threading.Event()
    app = client_with(reader, clock).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        pending = [
            asyncio.ensure_future(client.get("/history/events", params=EVENTS))
            for _ in range(8)
        ]
        await asyncio.sleep(0.2)  # 8건 모두 같은 만들기 태스크를 기다리는 중
        reader.gate.set()
        responses = await asyncio.gather(*pending)
    assert reader.event_queries == 1
    assert len({r.content for r in responses}) == 1
    assert all(r.status_code == 200 for r in responses)


def candle_params(start: int, end: int) -> dict[str, object]:
    return {"base": "BTC", "res": "1m", "start": start, "end": end}


def test_closed_candle_chunks_live_600s_and_open_ones_30s() -> None:
    reader, clock = CountingReader(), Clock()
    client = client_with(reader, clock)
    now = int(time.time())
    closed = candle_params(now - 7_200, now - 3_600)  # 끝이 지난 청크
    opened = candle_params(now - 3_600, now + 3_600)  # 끝이 지금 뒤 — 새 봉이 붙는다
    client.get("/history/candles", params=closed)
    client.get("/history/candles", params=opened)
    assert reader.candle_queries == 2
    clock.now += 31
    client.get("/history/candles", params=closed)  # 아직 600초 안
    client.get("/history/candles", params=opened)  # 30초 지남
    assert reader.candle_queries == 3
    clock.now += 570
    client.get("/history/candles", params=closed)
    assert reader.candle_queries == 4


# --- 캐시 칸 규칙 ---


def entry(size: int) -> Encoded:
    return Encoded(raw=b"x" * size, gz=b"")


async def fill(cache: ResponseCache, key: object, value: Encoded, ttl: float) -> None:
    async def make() -> Encoded:
        return value

    assert await cache.get(key, ttl, make) is value
    await asyncio.sleep(0)  # 완료 콜백이 캐시를 채운다


async def test_expired_keys_are_dropped_when_filling() -> None:
    clock = Clock()
    cache = ResponseCache(clock=clock)
    await fill(cache, "a", entry(10), 60)
    clock.now += 61
    await fill(cache, "b", entry(10), 60)
    assert len(cache) == 1 and cache.total_bytes == 10


async def test_candle_cache_keeps_the_256_most_recently_used_keys() -> None:
    assert CANDLES_CACHE_MAX_KEYS == 256
    clock = Clock()
    cache = HistoryCache(clock=clock).candles
    first = entry(1)
    await fill(cache, 0, first, 600)
    for i in range(1, CANDLES_CACHE_MAX_KEYS):
        await fill(cache, i, entry(1), 600)

    async def never() -> Encoded:
        raise AssertionError("캐시에 있어야 한다")

    # 0 을 다시 쓴다 → 가장 오래 안 쓴 키는 1
    assert await cache.get(0, 600, never) is first
    await fill(cache, "new", entry(1), 600)
    assert len(cache) == CANDLES_CACHE_MAX_KEYS
    assert await cache.get(0, 600, never) is first  # 남아 있다
    rebuilt = entry(2)
    # 버려져 새로 만든다
    assert await cache.get(1, 600, lambda: _value(rebuilt)) is rebuilt


async def _value(value: Encoded) -> Encoded:
    return value


async def test_event_cache_bytes_stay_under_the_cap() -> None:
    assert EVENTS_CACHE_MAX_BYTES == 64 * 1024 * 1024
    clock = Clock()
    cache = HistoryCache(clock=clock).events
    third = EVENTS_CACHE_MAX_BYTES // 3
    for key in ("a", "b", "c"):
        await fill(cache, key, entry(third), 60)
    assert len(cache) == 3
    await fill(cache, "d", entry(third), 60)  # 넘친다 → 가장 오래 안 쓴 "a" 부터
    assert len(cache) == 3 and cache.total_bytes <= EVENTS_CACHE_MAX_BYTES
    # 혼자 넘는 응답은 담지 않는다
    await fill(cache, "huge", entry(EVENTS_CACHE_MAX_BYTES + 1), 60)
    assert len(cache) == 3


async def test_candle_cache_bytes_stay_under_32mb_too() -> None:
    """봉 캐시는 키 256개에 더해 원본 + gzip 합 32MB — 넘으면 오래 안 쓴 키부터, 혼자 넘는 응답은 담지 않는다."""
    assert CANDLES_CACHE_MAX_BYTES == 32 * 1024 * 1024
    clock = Clock()
    cache = HistoryCache(clock=clock).candles
    quarter = CANDLES_CACHE_MAX_BYTES // 4
    half = Encoded(raw=b"x" * (quarter // 2), gz=b"z" * (quarter - quarter // 2))
    assert half.size == quarter  # 두 모양을 합쳐 센다
    for key in ("a", "b", "c", "d"):
        await fill(cache, key, half, 600)
    assert len(cache) == 4 and cache.total_bytes == CANDLES_CACHE_MAX_BYTES

    async def never() -> Encoded:
        raise AssertionError("캐시에 있어야 한다")

    # "a" 를 다시 쓴다 → 가장 오래 안 쓴 키는 "b"
    assert await cache.get("a", 600, never) is half
    await fill(cache, "e", entry(1), 600)
    assert len(cache) == 4 and cache.total_bytes <= CANDLES_CACHE_MAX_BYTES
    assert await cache.get("a", 600, never) is half
    rebuilt = entry(2)
    # 버려져 새로 만든다
    assert await cache.get("b", 600, lambda: _value(rebuilt)) is rebuilt
    await asyncio.sleep(0)
    # 혼자 상한을 넘는 응답은 담지 않고, 있던 키도 건드리지 않는다
    before = len(cache)
    await fill(cache, "huge", entry(CANDLES_CACHE_MAX_BYTES + 1), 600)
    assert len(cache) == before
    fresh = entry(3)
    assert await cache.get("huge", 600, lambda: _value(fresh)) is fresh


def test_encode_both_keeps_raw_and_gzip_of_the_same_bytes() -> None:
    raw = b'{"events":[' + b",".join(b'{"a":1}' for _ in range(1000)) + b"]}"
    both = encode_both(raw)
    assert both.raw == raw
    assert gzip.decompress(both.gz) == raw
    assert both.gz[10:] == gzip.compress(raw, 6, mtime=0)[10:]  # 레벨 6
