"""틱 인계기·Redis 스트림·flusher — 스펙 009 §4 "인계"·"flusher". Redis 는 fakeredis, Influx 는 fake."""

import asyncio
import gzip
import json
import logging
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.live_store import LiveStore
from app.core.models import Tick, TickRow
from app.core.redis_stream import PAGE, RedisTickStream, StreamEntry
from app.core.tick_store import (
    QUEUE_LIMIT,
    WRITE_BATCH,
    Flusher,
    TickRelay,
    encode_tick,
)
from app.core.ticks import TickLoop
from app.features.spreads.tests.helpers import make_client, seed_rows
from app.main import create_app
from tests.conftest import FakeInflux, make_row

T0 = 1_787_000_000


def _client() -> httpx.AsyncClient:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"테스트에서 네트워크 호출 발생: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(fail))


def make_stream(connected: bool = True) -> tuple[RedisTickStream, fakeredis.FakeServer]:
    server = fakeredis.FakeServer()
    server.connected = connected
    return RedisTickStream(fakeredis.aioredis.FakeRedis(server=server)), server


def seeded() -> LiveStore:
    """호가를 여러 단계로 시드해 슬리피지가 0 이 아니게 — 원값 ≠ 순값."""
    # 수신 시각은 import 시각이 아니라 호출 시각 — 5초가 지나면 stale 이라, 모듈 상수면 앞 테스트가 길어질 때(CI) 깨진다
    now = datetime.now(UTC)
    store = LiveStore()
    seed_rows(
        store,
        [
            make_row(
                "upbit",
                "BTC",
                bids=[[100_000.0, 0.02], [99_000.0, 5.0]],
                asks=[[100_100.0, 0.02], [101_000.0, 5.0]],
            ),
            make_row(
                "binance",
                "BTC",
                bids=[[70.0, 0.02], [69.0, 500.0]],
                asks=[[71.0, 0.02], [72.0, 500.0]],
            ),
            make_row("upbit", "ETH", bids=[[3_000.0, 10.0]], asks=[[3_010.0, 10.0]]),
            make_row("binance", "ETH", bids=[[2.0, 1000.0]], asks=[[2.1, 1000.0]]),
        ],
        now,
    )
    store.set_rate("upbit", 1400.0, 1390.0, now)
    return store


def tick(ts: int, n_rows: int = 2, dw: tuple[str, ...] = ()) -> Tick:
    rows = tuple(
        TickRow(dom="upbit", fx="binance", base=f"C{i}", fwd=1.0 + i, rev=-1.0 - i)
        for i in range(n_rows)
    )
    return Tick(ts=ts, rows=rows, dw_failed=dw)


def decode(entry: StreamEntry) -> dict[str, Any]:
    return json.loads(gzip.decompress(entry.data))


# ---- 인계 ----


async def test_slot_hands_off_previous_tick_and_last_tick_on_close() -> None:
    stream, _ = make_stream()
    store = seeded()
    relay = TickRelay(stream=stream, store=store)
    loop = TickLoop(store=store, streams=[], client=_client(), handoff=relay)
    loop.tick(T0)
    assert relay.pending == 0  # T1 은 슬롯에 — 인계 없음
    loop.tick(T0 + 1)
    assert relay.pending == 1  # T2 가 들어오며 T1 인계
    await loop.aclose()  # 종료 시 슬롯의 T2 도 인계
    assert relay.pending == 2
    assert await relay.drain() == 2
    entries = await stream.read_all()
    assert [e.ts for e in entries] == [T0, T0 + 1]
    assert [decode(e)["ts"] for e in entries] == [T0, T0 + 1]


async def test_handed_off_tick_matches_raw_premium_of_spreads_row() -> None:
    stream, _ = make_stream()
    store = seeded()
    relay = TickRelay(stream=stream, store=store)
    loop = TickLoop(store=store, streams=[], client=_client(), handoff=relay)
    loop.tick(T0)
    loop.tick(T0 + 1)
    await relay.drain()
    [entry] = await stream.read_all()
    record = decode(entry)
    assert set(record) == {"ts", "rows", "dwFailed"}
    assert record["dwFailed"] == []
    assert entry.id and entry.ts == T0
    by_base = {r["base"]: r for r in record["rows"]}
    assert set(by_base) == {"BTC", "ETH"}
    assert set(by_base["BTC"]) == {"dom", "fx", "base", "fwd", "rev"}
    # 같은 호가의 /spreads 행: 순값 + 차감폭 = 원값
    rows = make_client(store).get("/spreads").json()["rows"]
    for row in rows:
        raw = by_base[row["sym"]]
        assert raw["fwd"] == pytest.approx(row["fwd"] + row["slipFwd"])
        assert raw["rev"] == pytest.approx(row["rev"] + row["slipRev"])
    btc = next(r for r in rows if r["sym"] == "BTC")
    assert btc["slipFwd"] > 0  # 슬리피지가 실제로 있어 두 값이 다르다


async def test_empty_tick_is_not_added_but_dw_failed_only_is() -> None:
    stream, _ = make_stream()
    relay = TickRelay(stream=stream, store=LiveStore())
    relay(Tick(ts=T0, rows=(), dw_failed=()))
    assert relay.pending == 0
    relay(Tick(ts=T0 + 1, rows=(), dw_failed=("upbit",)))
    assert relay.pending == 1
    await relay.drain()
    [entry] = await stream.read_all()
    assert decode(entry) == {"ts": T0 + 1, "rows": [], "dwFailed": ["upbit"]}


async def test_redis_down_drops_ticks_with_one_warning_each_and_spreads_keeps_working(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream, server = make_stream(connected=False)
    store = seeded()
    relay = TickRelay(stream=stream, store=store)
    loop = TickLoop(store=store, streams=[], client=_client(), handoff=relay)
    with caplog.at_level(logging.WARNING, logger="marketlens.tick_store"):
        loop.tick(T0)
        loop.tick(T0 + 1)
        loop.tick(T0 + 2)
        assert await relay.drain() == 0
    warnings = [r for r in caplog.records if "Redis 인계 실패" in r.getMessage()]
    assert len(warnings) == 2 and relay.pending == 0
    # 틱 루프·/spreads·spark 는 정상
    assert store.tick is not None and store.tick.ts == T0 + 2
    [btc, _] = make_client(store).get("/spreads").json()["rows"]
    assert btc["status"] == "ok" and len(btc["spark"]) == 1
    # 복구되면 그 뒤 틱부터 흐른다
    server.connected = True
    loop.tick(T0 + 3)
    assert await relay.drain() == 1
    assert [e.ts for e in await stream.read_all()] == [T0 + 2]


async def test_queue_limit_drops_oldest_first() -> None:
    stream, _ = make_stream()
    relay = TickRelay(stream=stream, store=LiveStore())
    for i in range(QUEUE_LIMIT + 5):
        relay(tick(T0 + i))
    assert relay.pending == QUEUE_LIMIT
    assert await relay.drain() == QUEUE_LIMIT
    entries = await stream.read_all()
    assert entries[0].ts == T0 + 5 and entries[-1].ts == T0 + QUEUE_LIMIT + 4


async def test_handoff_never_raises_even_when_fakes_do(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenStore(LiveStore):
        def set_spark(self, spark: dict[Any, list[float]]) -> None:
            raise RuntimeError("게시 실패 (테스트)")

    class BrokenStream(RedisTickStream):
        async def add(self, ts: int, data: bytes) -> str:
            raise RuntimeError("XADD 실패 (테스트)")

    with caplog.at_level(logging.WARNING, logger="marketlens.tick_store"):
        relay = TickRelay(
            stream=BrokenStream(fakeredis.aioredis.FakeRedis()), store=BrokenStore()
        )
        relay(tick(T0))  # 예외 없음
        assert relay.pending == 0
        relay2 = TickRelay(
            stream=BrokenStream(fakeredis.aioredis.FakeRedis()), store=LiveStore()
        )
        relay2(tick(T0))
        assert await relay2.drain() == 0  # 예외 없음
    assert any("이 틱은 버린다" in r.getMessage() for r in caplog.records)
    assert any("Redis 인계 실패" in r.getMessage() for r in caplog.records)


async def test_sender_task_sends_in_order_without_blocking_the_handoff() -> None:
    stream, _ = make_stream()
    relay = TickRelay(stream=stream, store=LiveStore())
    relay.start()
    for i in range(3):
        relay(tick(T0 + i))
    await relay.aclose()
    assert [e.ts for e in await stream.read_all()] == [T0, T0 + 1, T0 + 2]


async def test_close_drains_within_a_total_deadline_and_drops_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class SlowFailingStream(RedisTickStream):
        """무응답 Redis — 틱마다 타임아웃 뒤 실패."""

        async def add(self, ts: int, data: bytes) -> str:
            await asyncio.sleep(0.05)
            raise TimeoutError("무응답 (테스트)")

    relay = TickRelay(
        stream=SlowFailingStream(fakeredis.aioredis.FakeRedis()),
        store=LiveStore(),
        drain_deadline_sec=0.2,
    )
    for i in range(40):  # 틱마다 0.05초 → 한 번씩 다 시도하면 2초
        relay(tick(T0 + i))
    started = asyncio.get_running_loop().time()
    with caplog.at_level(logging.WARNING, logger="marketlens.tick_store"):
        await relay.aclose()
    assert asyncio.get_running_loop().time() - started < 1.0
    assert relay.pending == 0
    [warning] = [r for r in caplog.records if "데드라인" in r.getMessage()]
    assert "남은 틱" in warning.getMessage() and "건을 버린다" in warning.getMessage()
    tried = sum("Redis 인계 실패" in r.getMessage() for r in caplog.records)
    assert 0 < tried < 40


# ---- flusher ----


class CountingStream(RedisTickStream):
    """페이지 읽기·XDEL 호출을 센다 — 읽는 시점의 스트림 길이도 남긴다(페이지 단위 진행 확인용)."""

    def __init__(self) -> None:
        super().__init__(fakeredis.aioredis.FakeRedis())
        self.lengths_at_read: list[int] = []
        self.deletes = 0

    async def read_page(self, after: str | None = None) -> list[StreamEntry]:
        self.lengths_at_read.append(await self.length())
        return await super().read_page(after)

    async def delete(self, ids: list[str]) -> int:
        self.deletes += 1
        return await super().delete(ids)


async def test_empty_stream_skips_the_round() -> None:
    stream = CountingStream()
    influx = FakeInflux()
    flusher = Flusher(stream=stream, writer=influx)
    assert await flusher.flush_once() is True
    assert influx.writes == [] and stream.deletes == 0
    assert flusher.consecutive_failures == 0


async def test_empty_stream_after_a_failed_round_warns_truncation_and_resets_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream, _ = make_stream()
    influx = FakeInflux()
    influx.fail = True
    ids = [await stream.add(T0 + i, encode_tick(tick(T0 + i))) for i in range(2)]
    flusher = Flusher(stream=stream, writer=influx)
    assert await flusher.flush_once() is False and flusher.consecutive_failures == 1
    await stream.delete(ids)  # 실패한 구간이 통째로 잘렸다(MAXLEN)
    with caplog.at_level(logging.WARNING, logger="marketlens.tick_store"):
        assert await flusher.flush_once() is True  # 빈 회차 = 읽기 성공
    assert sum("Redis 스트림 잘림" in r.getMessage() for r in caplog.records) == 1
    assert flusher.consecutive_failures == 0 and influx.writes == []


async def test_points_per_tick_and_dw_fail_points_then_stream_is_emptied() -> None:
    stream, _ = make_stream()
    influx = FakeInflux()
    for t in (
        tick(T0, 3),
        tick(T0 + 1, 2, dw=("upbit", "binance")),
        tick(T0 + 2, 0, dw=("bithumb",)),
    ):
        await stream.add(t.ts, encode_tick(t))
    assert await Flusher(stream=stream, writer=influx).flush_once() is True
    premium = influx.stored("premium")
    assert len(premium) == 5
    assert sorted(p.ts for p in premium) == [T0, T0, T0, T0 + 1, T0 + 1]
    p = next(p for p in premium if p.tags["base"] == "C1" and p.ts == T0)
    assert p.fields == {"fwd": 2.0, "rev": -2.0} and p.tags == {
        "dom": "upbit",
        "fx": "binance",
        "base": "C1",
    }
    dw = sorted((p.tags["exchange"], p.ts) for p in influx.stored("dw_fail"))
    assert dw == [("binance", T0 + 1), ("bithumb", T0 + 2), ("upbit", T0 + 1)]
    assert await stream.length() == 0


async def test_entries_added_after_the_read_survive_the_round() -> None:
    late = tick(T0 + 9)

    class StreamWithLateAdd(RedisTickStream):
        async def read_page(self, after: str | None = None) -> list[StreamEntry]:
            entries = await super().read_page(after)
            await self.add(late.ts, encode_tick(late))  # 페이지를 읽은 뒤 새 엔트리
            return entries

    stream = StreamWithLateAdd(fakeredis.aioredis.FakeRedis())
    await stream.add(T0, encode_tick(tick(T0)))
    influx = FakeInflux()
    assert await Flusher(stream=stream, writer=influx).flush_once() is True
    assert [e.ts for e in await stream.read_all()] == [T0 + 9]
    assert sorted({p.ts for p in influx.stored("premium")}) == [T0]


async def test_failed_round_keeps_stream_and_next_round_resends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream, _ = make_stream()
    influx = FakeInflux()
    influx.fail = True
    for i in range(3):
        await stream.add(T0 + i, encode_tick(tick(T0 + i)))
    flusher = Flusher(stream=stream, writer=influx)
    with caplog.at_level(logging.INFO, logger="marketlens.tick_store"):
        assert await flusher.flush_once() is False
        assert await flusher.flush_once() is False
        assert await stream.length() == 3 and influx.stored("premium") == []
        influx.fail = False
        assert await flusher.flush_once() is True
    msgs = [r.getMessage() for r in caplog.records]
    assert "DB 저장 실패 (연속 1회)" in msgs[0] and "DB 저장 실패 (연속 2회)" in msgs[1]
    assert any("밀린 틱 3건" in m for m in msgs)
    assert await stream.length() == 0 and len(influx.stored("premium")) == 6
    assert flusher.consecutive_failures == 0


async def test_one_failed_batch_fails_the_round_and_big_ranges_load_fully() -> None:
    stream, _ = make_stream()
    per_tick = WRITE_BATCH // 2 + 1  # 3틱 = 배치 상수보다 큰 구간(2배치)
    for i in range(3):
        await stream.add(T0 + i, encode_tick(tick(T0 + i, per_tick)))
    influx = FakeInflux()
    influx.fail_after_batches = 1  # 1번째 배치는 성공, 2번째 실패
    flusher = Flusher(stream=stream, writer=influx)
    assert await flusher.flush_once() is False
    assert await stream.length() == 3  # 아무것도 지우지 않았다
    influx.fail_after_batches = None
    assert await flusher.flush_once() is True
    assert len(influx.stored("premium")) == 3 * per_tick
    assert await stream.length() == 0


async def test_multi_page_range_moves_one_page_at_a_time_and_resumes_at_the_failed_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = CountingStream()
    n = PAGE + 3
    for i in range(n):
        await stream.add(T0 + i, encode_tick(tick(T0 + i, 1)))
    influx = FakeInflux()
    influx.fail_after_batches = 1  # 첫 페이지(1배치)는 성공, 둘째 페이지에서 실패
    flusher = Flusher(stream=stream, writer=influx)
    assert await flusher.flush_once() is False
    # 둘째 페이지를 읽는 시점에 첫 페이지는 이미 쓰고 지웠다 — 메모리는 페이지 크기에 비례
    assert stream.lengths_at_read == [n, 3] and stream.deletes == 1
    assert await stream.length() == 3 and len(influx.stored("premium")) == PAGE
    assert flusher.consecutive_failures == 1
    influx.fail_after_batches = None
    with caplog.at_level(logging.INFO, logger="marketlens.tick_store"):
        assert await flusher.flush_once() is True  # 둘째 페이지부터 다시 — 같은 첫 ID
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("잘림" in m for m in msgs) and any("밀린 틱 3건" in m for m in msgs)
    assert await stream.length() == 0 and len(influx.stored("premium")) == n
    assert stream.lengths_at_read == [n, 3, 3]


async def test_xdel_failure_fails_the_round_but_leaves_no_truncation_reference(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class StreamWithLostDeleteReply(RedisTickStream):
        """XDEL 이 Redis 에 닿았지만 응답을 못 받은 경우 — 지운 뒤 예외."""

        lose_reply = True

        async def delete(self, ids: list[str]) -> int:
            removed = await super().delete(ids)
            if self.lose_reply:
                self.lose_reply = False
                raise TimeoutError("XDEL 응답 없음 (테스트)")
            return removed

    stream = StreamWithLostDeleteReply(fakeredis.aioredis.FakeRedis())
    for i in range(2):
        await stream.add(T0 + i, encode_tick(tick(T0 + i)))
    influx = FakeInflux()
    flusher = Flusher(stream=stream, writer=influx)
    with caplog.at_level(logging.WARNING, logger="marketlens.tick_store"):
        assert await flusher.flush_once() is False
        assert flusher.consecutive_failures == 1 and await stream.length() == 0
        assert await flusher.flush_once() is True  # 빈 스트림이지만 잘림이 아니다
    msgs = [r.getMessage() for r in caplog.records]
    assert any("DB 저장 실패 (연속 1회)" in m for m in msgs)
    assert not any("잘림" in m for m in msgs)
    assert flusher.consecutive_failures == 0 and len(influx.stored("premium")) == 4


async def test_loading_the_same_range_twice_is_idempotent() -> None:
    stream, _ = make_stream()
    influx = FakeInflux()
    ticks = [tick(T0 + i, 4, dw=("upbit",)) for i in range(2)]
    for t in ticks:
        await stream.add(t.ts, encode_tick(t))
    await Flusher(stream=stream, writer=influx).flush_once()
    before = dict(influx.points)
    for t in ticks:
        await stream.add(t.ts, encode_tick(t))
    await Flusher(stream=stream, writer=influx).flush_once()
    assert influx.points == before and len(influx.points) == 10


async def test_truncation_after_a_failed_round_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream, _ = make_stream()
    influx = FakeInflux()
    influx.fail = True
    ids = [await stream.add(T0 + i, encode_tick(tick(T0 + i))) for i in range(3)]
    flusher = Flusher(stream=stream, writer=influx)
    assert await flusher.flush_once() is False
    influx.fail = False
    with caplog.at_level(logging.WARNING, logger="marketlens.tick_store"):
        # 같은 첫 ID 부터 다시 읽히면 경고 없음 — 실패는 구멍이 아니라 지연
        assert await flusher.flush_once() is True
        assert not any("잘림" in r.getMessage() for r in caplog.records)
        # 실패 뒤 오래된 쪽이 잘리면(MAXLEN) 경고
        influx.fail = True
        ids = [
            await stream.add(T0 + 10 + i, encode_tick(tick(T0 + 10 + i)))
            for i in range(3)
        ]
        assert await flusher.flush_once() is False
        await stream.delete([ids[0]])  # MAXLEN 에 잘린 상황
        influx.fail = False
        assert await flusher.flush_once() is True
    assert sum("Redis 스트림 잘림" in r.getMessage() for r in caplog.records) == 1


async def test_redis_read_failure_counts_as_a_failed_round(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream, server = make_stream(connected=False)
    flusher = Flusher(stream=stream, writer=FakeInflux())
    with caplog.at_level(logging.WARNING, logger="marketlens.tick_store"):
        assert await flusher.flush_once() is False
    assert flusher.consecutive_failures == 1
    assert any("DB 저장 실패 (연속 1회)" in r.getMessage() for r in caplog.records)
    server.connected = True
    assert await flusher.flush_once() is True and flusher.consecutive_failures == 0


async def test_read_all_pages_through_more_than_one_page() -> None:
    stream, _ = make_stream()
    n = PAGE + 3
    for i in range(n):
        await stream.add(T0 + i, encode_tick(tick(T0 + i, 1)))
    entries = await stream.read_all()
    assert len(entries) == n and [e.ts for e in entries] == list(range(T0, T0 + n))
    assert await stream.delete([e.id for e in entries]) == n
    assert await stream.length() == 0


async def test_flusher_loop_sleeps_first_then_runs_rounds() -> None:
    stream, _ = make_stream()
    influx = FakeInflux()
    await stream.add(T0, encode_tick(tick(T0)))
    slept: list[float] = []
    done = asyncio.Event()

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 2:
            done.set()
            await asyncio.Event().wait()

    flusher = Flusher(stream=stream, writer=influx, sleep=sleep)
    flusher.start()
    await asyncio.wait_for(done.wait(), 1.0)
    await flusher.aclose()
    assert slept == [60.0, 60.0] and len(influx.stored("premium")) == 2


# ---- 기동·장애 격리 ----


def test_boot_with_redis_refused_logs_one_warning_and_health_is_200(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def refuse(url: str) -> Any:
        raise OSError("refused")

    def rest(request: httpx.Request) -> httpx.Response:
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
        caplog.at_level(logging.WARNING, logger="marketlens.main"),
        TestClient(app) as client,
    ):
        assert client.get("/health").status_code == 200
    warnings = [r for r in caplog.records if "Redis 연결 실패" in r.getMessage()]
    assert len(warnings) == 1
