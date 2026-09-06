"""거래소 원문 아카이브 — 스펙 010 §4. S3 는 `put(key, body)` fake, 시계는 주입, 네트워크 없음."""

import asyncio
import gzip
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import raw_archive
from app.core.config import Settings
from app.core.contracts import noop_record
from app.core.raw_archive import WORKER_NAME, RawArchive, format_line, pack
from app.core.streams.binance import BinanceStream
from app.core.streams.upbit import UpbitStream
from app.main import create_app
from tests.stream_fakes import (
    Clock,
    FakeConnector,
    FakeSocket,
    Sleeps,
    store_with_universe,
    until,
)
from tests.test_stream_binance import depth, exchange_info
from tests.test_stream_upbit import orderbook

T0 = 1_788_600_000_123  # 2026-09-05T09:20:00.123Z — 창 20260905T092000Z 의 123ms 지점
WS = "ws:/websocket/v1"
MINUTE = 60_000
NEXT = (
    T0 - 123 + MINUTE
)  # 다음 분 창의 첫 ms(09:21:00.000) — 여기부터 닫기 회차가 앞 창을 닫는다
OB_BTC = "orderbook:KRW-BTC"


class FakeS3:
    """`put(key, body)` 만 있는 S3 흉내 — 실패 스위치 하나. 워커 스레드가 부르므로 목록은 잠금 아래."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes]] = []
        self.attempts = 0  # 실패한 시도까지 센다
        self.fail = False
        self._lock = threading.Lock()

    def put(self, key: str, body: bytes) -> None:
        with self._lock:
            self.attempts += 1
        if self.fail:
            raise RuntimeError("s3 down")
        with self._lock:
            self.puts.append((key, body))


def build(**kw: Any) -> tuple[RawArchive, FakeS3, Clock]:
    s3 = FakeS3()
    clock = Clock(T0)
    kw.setdefault("retry_interval_sec", 0.01)
    return RawArchive(uploader=s3, clock=clock, sleep=Sleeps(), **kw), s3, clock


async def settled(pred: Callable[[], bool], timeout: float = 2.0) -> None:
    """워커 스레드의 결과를 기다린다 — 조건이 참이 될 때까지 폴링."""
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "워커가 기대한 상태에 이르지 않았다"
        await asyncio.sleep(0.005)


def lines_of(body: bytes) -> list[bytes]:
    text = gzip.decompress(body)
    assert text.endswith(b"\n")
    return text[:-1].split(b"\n")


def raw_part(line: bytes) -> bytes:
    """줄에서 `raw` 값의 바이트 — 메타 3키 뒤 `,"raw":` 부터 닫는 `}` 전까지."""
    head = json.loads(line)
    prefix = (
        json.dumps(
            {k: head[k] for k in ("exchange", "source", "receivedAt")},
            separators=(",", ":"),
        )[:-1]
        + ',"raw":'
    )
    assert line.startswith(prefix.encode())
    return line[len(prefix) : -1]


# --- 레코드 한 줄 (§3.4) ---


def test_one_record_is_one_line_with_four_keys_in_order() -> None:
    """`key` 는 줄에 쓰지 않는다 — 표본화에만 쓴다 (§4)."""
    archive, _, _ = build()
    archive.record("upbit", WS, T0, '{"type":"orderbook"}', OB_BTC)
    assert archive.buffered("upbit") == 1
    line = format_line("upbit", WS, T0, '{"type":"orderbook"}')
    parsed = json.loads(line)
    assert list(parsed) == ["exchange", "source", "receivedAt", "raw"]
    assert (parsed["exchange"], parsed["source"], parsed["receivedAt"]) == (
        "upbit",
        WS,
        T0,
    )


@pytest.mark.parametrize(
    "payload",
    [
        '{"a": 1.50, "b":[1.0, 2.00e3], "c" : "x"}',  # 재직렬화하면 공백·숫자 표기가 바뀌는 입력
        '[{"ask_price":1.50}, {"bid_price": 0.90}]',
    ],
)
def test_json_payload_is_embedded_byte_for_byte(payload: str) -> None:
    line = format_line("upbit", WS, T0, payload)
    assert isinstance(json.loads(line)["raw"], dict | list)
    assert raw_part(line[:-1]) == payload.encode()


@pytest.mark.parametrize(
    "payload",
    [
        "<html><body>502 Bad Gateway</body></html>",
        "",
        "123",  # 스칼라 JSON 은 객체·배열이 아니다 — 문자열로 감싼다
        '"quoted"',
        '{\n  "pretty": true\n}',  # 줄바꿈이 든 JSON — 한 레코드 = 한 줄을 지키려 감싼다
        '{"a": NaN, "b": Infinity}',  # 비표준 상수 — 그대로 붙이면 줄 전체가 표준 파서에서 깨진다
    ],
)
def test_non_json_payload_is_wrapped_as_string_verbatim(payload: str) -> None:
    line = format_line("upbit", "rest:/v1/market/all", T0, payload)
    assert line.count(b"\n") == 1 and line.endswith(b"\n")
    parsed = json.loads(line, parse_constant=_reject_constant)  # 표준 파서처럼 읽는다
    assert isinstance(parsed["raw"], str) and parsed["raw"] == payload


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


# --- 분 창·표본화·객체 (§3.5) ---


async def test_window_closes_when_the_next_minute_begins_and_key_is_window_start() -> (
    None
):
    archive, s3, clock = build()
    archive.record("binance", "ws:/stream", T0, "{}")
    clock.now = NEXT - 1  # 같은 창 안에서는 59초가 지나도 닫히지 않는다
    assert await archive.run_once() == 0 and s3.puts == []
    clock.now = NEXT
    assert await archive.run_once() == 1
    await settled(lambda: archive.pending == 0)
    [(key, body)] = s3.puts
    assert key == "raw/exchange=binance/dt=2026-09-05/hh=09/20260905T092000Z.jsonl.gz"
    assert archive.buffered("binance") == 0
    assert json.loads(lines_of(body)[0])["receivedAt"] == T0
    await archive.aclose()


async def test_same_source_and_key_keeps_only_the_last_line_in_a_window() -> None:
    archive, s3, clock = build()
    for i in range(3):
        archive.record("upbit", WS, T0 + i, json.dumps({"n": i}), OB_BTC)
    archive.record("upbit", WS, T0 + 3, '{"t":1}', "ticker:KRW-BTC")
    archive.record("upbit", WS, T0 + 4, '{"e":1}', "orderbook:KRW-ETH")
    assert archive.buffered("upbit") == 3
    clock.now = NEXT
    await archive.run_once()
    await settled(lambda: archive.pending == 0)
    [(_, body)] = s3.puts
    lines = [json.loads(ln) for ln in lines_of(body)]
    assert [(ln["receivedAt"], ln["raw"]) for ln in lines] == [
        (T0 + 2, {"n": 2}),  # 마지막 1건만, receivedAt 도 마지막 것
        (T0 + 3, {"t": 1}),
        (T0 + 4, {"e": 1}),
    ]
    assert all(list(ln) == ["exchange", "source", "receivedAt", "raw"] for ln in lines)
    await archive.aclose()


async def test_lines_without_key_are_all_kept_even_when_identical() -> None:
    archive, s3, clock = build()
    for i in range(3):
        archive.record("upbit", WS, T0 + i, '{"status":"UP"}')
    archive.record("upbit", "rest:/v1/market/all", T0 + 3, "[]")
    archive.record("upbit", "rest:/v1/market/all", T0 + 4, "[]")
    assert archive.buffered("upbit") == 5
    clock.now = NEXT
    await archive.run_once()
    await settled(lambda: archive.pending == 0)
    [(_, body)] = s3.puts
    assert len(lines_of(body)) == 5
    await archive.aclose()


async def test_window_is_decided_by_received_at_not_by_the_closing_clock() -> None:
    archive, s3, clock = build()
    archive.record("upbit", WS, T0, '{"n":0}', OB_BTC)
    archive.record("upbit", WS, NEXT + 5, '{"n":1}', OB_BTC)  # 시계는 아직 앞 창
    assert archive.buffered("upbit") == 2  # 창이 달라 같은 키라도 둘 다 산다
    assert await archive.run_once() == 0  # clock == T0 — 어느 창도 지나지 않았다
    clock.now = NEXT
    assert await archive.run_once() == 1  # 앞 창만
    clock.now = NEXT + MINUTE
    assert await archive.run_once() == 1
    await settled(lambda: archive.pending == 0)
    assert [k[-17:] for k, _ in s3.puts] == ["T092000Z.jsonl.gz", "T092100Z.jsonl.gz"]
    assert [len(lines_of(b)) for _, b in s3.puts] == [1, 1]
    await archive.aclose()


async def test_exchanges_get_separate_objects_and_empty_buffers_make_none() -> None:
    archive, s3, clock = build()
    archive.record("upbit", WS, T0, "{}")
    archive.record("bithumb", WS, T0 + 1, "{}")
    clock.now = NEXT + 1
    assert await archive.run_once() == 2
    await settled(lambda: archive.pending == 0)
    assert [k.split("/")[1] for k, _ in s3.puts] == [
        "exchange=bithumb",
        "exchange=upbit",
    ]
    clock.now = NEXT + 3 * MINUTE
    assert await archive.run_once() == 0  # 기록이 없던 회차 — 빈 객체는 없다
    assert await archive.run_once(force_close=True) == 0
    assert len(s3.puts) == 2
    await archive.aclose()


async def test_gunzip_lines_are_sorted_by_received_at_and_pack_is_deterministic() -> (
    None
):
    archive, s3, clock = build()
    archive.record("upbit", WS, T0 + 1, '{"ob":0}', OB_BTC)  # 나중에 교체된다
    plain = [json.dumps({"i": i}) for i in range(20)]
    for p in plain:
        archive.record("upbit", WS, T0 + 10, p)  # 같은 receivedAt — 기록 순
    archive.record("upbit", WS, T0 + 40, '{"ob":1}', OB_BTC)
    clock.now = NEXT
    await archive.run_once()
    await settled(lambda: archive.pending == 0)
    [(_, body)] = s3.puts
    lines = lines_of(body)
    parsed = [json.loads(ln) for ln in lines]
    assert [ln["receivedAt"] for ln in parsed] == [T0 + 10] * 20 + [T0 + 40]
    assert [ln["raw"]["i"] for ln in parsed[:20]] == list(range(20))
    assert parsed[-1]["raw"] == {"ob": 1}
    expected = [format_line("upbit", WS, T0 + 10, p) for p in plain] + [
        format_line("upbit", WS, T0 + 40, '{"ob":1}')
    ]
    assert pack(expected) == body and pack([ln + b"\n" for ln in lines]) == body
    await archive.aclose()


# --- 닫기 회차와 업로드 워커 (§3.6) ---


async def test_failed_upload_stays_at_head_and_is_retried_with_same_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    archive, s3, clock = build()
    archive.record("upbit", WS, T0, "{}")
    clock.now = NEXT
    s3.fail = True
    with caplog.at_level(logging.WARNING, logger="marketlens.raw_archive"):
        assert await archive.run_once() == 1
        await settled(lambda: archive.consecutive_failures >= 2)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("S3 원문 업로드 실패 (연속 1회)" in m for m in msgs)
    assert any("S3 원문 업로드 실패 (연속 2회)" in m for m in msgs)
    assert all("T092000Z" in m for m in msgs if "업로드 실패" in m)  # 같은 키
    assert archive.pending == 1
    archive.record(
        "bithumb", WS, NEXT, "{}"
    )  # 실패 중 닫힌 다음 객체는 뒤에서 기다린다
    clock.now = NEXT + MINUTE
    assert await archive.run_once() == 1
    assert archive.pending == 2 and s3.puts == []
    s3.fail = False
    await settled(lambda: archive.pending == 0)
    assert archive.consecutive_failures == 0
    assert [k.split("/")[1] for k, _ in s3.puts] == [
        "exchange=upbit",
        "exchange=bithumb",
    ]
    assert s3.puts[0][0].endswith("T092000Z.jsonl.gz")
    await archive.aclose()


async def test_queue_over_limit_drops_oldest_and_logs_its_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    archive, s3, clock = build()
    s3.fail = True
    archive.record("upbit", WS, T0, "{}")
    clock.now = NEXT
    await archive.run_once()
    first = pack([format_line("upbit", WS, T0, "{}")])
    monkeypatch.setattr(raw_archive, "QUEUE_LIMIT_BYTES", len(first) * 2 + 10)
    for n in range(1, 4):
        archive.record("upbit", WS, T0 + n * MINUTE, "{}")
        clock.now = NEXT + n * MINUTE
        with caplog.at_level(logging.ERROR, logger="marketlens.raw_archive"):
            await archive.run_once()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2 and "T092000Z" in errors[0] and "T092100Z" in errors[1]
    assert archive.pending == 2
    # 워커가 지금의 머리(T092200)로 한 번 더 실패한 뒤에 S3 를 살린다 — 버려진 객체를 들고 있지 않다
    seen = archive.consecutive_failures
    await settled(lambda: archive.consecutive_failures > seen)
    s3.fail = False
    await settled(lambda: archive.pending == 0)
    assert [k[-17:] for k, _ in s3.puts] == ["T092200Z.jsonl.gz", "T092300Z.jsonl.gz"]
    await archive.aclose()


async def test_closing_keeps_running_while_upload_is_stuck() -> None:
    """업로드가 멈춰 있어도 닫기 회차는 돈다 — 창이 지난 버퍼가 그 회차에 닫혀 대기열로 간다."""

    class Stuck:
        def __init__(self) -> None:
            self.release = threading.Event()
            self.entered = threading.Event()
            self.puts: list[str] = []

        def put(self, key: str, body: bytes) -> None:
            self.entered.set()
            assert self.release.wait(2.0)
            self.puts.append(key)

    s3 = Stuck()
    clock = Clock(T0)
    archive = RawArchive(
        uploader=s3, clock=clock, sleep=Sleeps(), retry_interval_sec=0.01
    )
    archive.record("upbit", WS, T0, "{}")
    clock.now = NEXT
    assert await archive.run_once() == 1
    await asyncio.to_thread(s3.entered.wait, 2.0)  # 워커가 put 안에서 멈춰 있다
    archive.record("bithumb", WS, clock.now, "{}", OB_BTC)
    archive.record("bithumb", WS, clock.now + 1, "{}", OB_BTC)
    clock.now = NEXT + MINUTE
    assert await archive.run_once() == 1  # 업로드와 무관하게 닫힌다
    assert archive.pending == 2 and archive.buffered("bithumb") == 0
    s3.release.set()
    await settled(lambda: archive.pending == 0)
    assert [k.split("/")[1] for k in s3.puts] == ["exchange=upbit", "exchange=bithumb"]
    await archive.aclose()


async def test_uploader_exception_never_reaches_record_and_loop_continues() -> None:
    class Exploding:
        calls = 0

        def put(self, key: str, body: bytes) -> None:
            self.calls += 1
            raise ValueError("boom")

    s3 = Exploding()
    clock = Clock(T0)
    archive = RawArchive(
        uploader=s3,
        clock=clock,
        sleep=Sleeps(),
        retry_interval_sec=0.01,
        drain_deadline_sec=0.05,
    )
    archive.record("upbit", WS, T0, "{}")
    clock.now = NEXT
    archive.start()
    await settled(lambda: s3.calls >= 2)
    archive.record("upbit", WS, clock.now, "{}")  # 실패 중에도 기록은 즉시 돌아온다
    assert archive.buffered("upbit") == 1
    await archive.aclose()


async def test_loop_survives_a_round_exception_and_keeps_closing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """닫기 회차 한 번이 예외로 끝나도(gzip 스레드 실행 실패) 로그 1줄 뒤 다음 회차가 돈다 (§3.6)."""
    real_to_thread = asyncio.to_thread
    calls = 0

    async def flaky(fn: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("cannot schedule new futures after shutdown")
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(raw_archive.asyncio, "to_thread", flaky)
    archive, s3, clock = build()
    archive.record("upbit", WS, T0, "{}")
    archive.record("upbit", WS, T0 + 1, "{}")
    archive.record("bithumb", WS, T0 + 2, "{}")
    clock.now = NEXT + 2
    with caplog.at_level(logging.ERROR, logger="marketlens.raw_archive"):
        archive.start()
        await settled(lambda: calls >= 1)
        await asyncio.sleep(0.02)  # 예외 뒤에도 회차가 몇 번 더 돈다
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        "원문 닫기 회차 예외 — 닫힌 객체 2개(3줄)를 잃는다, 다음 회차를 이어간다"
    ]
    assert archive.buffered("upbit") == 0 and archive.buffered("bithumb") == 0
    archive.record("upbit", WS, clock.now, "{}")  # 다음 분 창
    clock.now += MINUTE
    await settled(lambda: len(s3.puts) == 1)  # 살아 있는 회차가 닫아 올렸다
    assert s3.puts[0][0].endswith("T092100Z.jsonl.gz")
    await archive.aclose()


async def test_retry_waits_the_full_interval_even_when_new_objects_arrive() -> None:
    """실패 재시도 간격 안에 다른 객체가 들어와도 머리 객체의 재시도는 앞당겨지지 않는다 (§3.6)."""
    archive, s3, clock = build(retry_interval_sec=0.5)
    s3.fail = True
    archive.record("upbit", WS, T0, "{}")
    clock.now = NEXT
    assert await archive.run_once() == 1
    await settled(lambda: s3.attempts == 1)
    first_attempt = time.monotonic()
    for _ in range(3):  # 0.5초 안에 다른 거래소 객체 3개가 대기열에 들어온다
        archive.record("bithumb", WS, clock.now, "{}")
        clock.now += MINUTE
        assert await archive.run_once() == 1
        await asyncio.sleep(0.04)
    assert archive.pending == 4 and s3.attempts == 1
    await settled(lambda: s3.attempts >= 2)
    assert time.monotonic() - first_attempt >= 0.4
    s3.fail = False
    await settled(lambda: archive.pending == 0)
    assert [k.split("/")[1] for k, _ in s3.puts][0] == "exchange=upbit"
    await archive.aclose()


async def test_aclose_closes_open_buffers_and_uploads_once() -> None:
    archive, s3, clock = build()
    archive.start()
    archive.record("upbit", WS, T0, "{}")
    archive.record("binance", "ws:/stream", T0 + 1, "{}")
    await archive.aclose()  # 60초가 안 지났어도 닫는다
    assert len(s3.puts) == 2 and archive.pending == 0
    assert archive.buffered("upbit") == 0 and archive.buffered("binance") == 0


async def test_aclose_during_slow_upload_uploads_every_object_exactly_once() -> None:
    """진행 중이던 업로드와 종료 시 닫힌 객체가 각각 정확히 1회 — 대기열을 만지는 손은 워커 하나다."""

    class Slow:
        def __init__(self) -> None:
            self.puts: list[str] = []
            self.threads: set[int] = set()
            self.entered = threading.Event()
            self._lock = threading.Lock()

        def put(self, key: str, body: bytes) -> None:
            self.entered.set()
            self.threads.add(threading.get_ident())
            time.sleep(0.2)
            with self._lock:
                self.puts.append(key)

    s3 = Slow()
    clock = Clock(T0)
    archive = RawArchive(uploader=s3, clock=clock, sleep=Sleeps())
    archive.record("upbit", WS, T0, "{}")
    clock.now = NEXT
    archive.start()
    await asyncio.to_thread(s3.entered.wait, 2.0)  # 워커가 upbit 객체를 올리는 중
    archive.record("bithumb", WS, clock.now, "{}")  # 종료 시 강제로 닫힐 객체
    await archive.aclose()
    assert [k.split("/")[1] for k in s3.puts] == ["exchange=upbit", "exchange=bithumb"]
    assert archive.pending == 0 and len(s3.threads) == 1


async def test_aclose_gives_up_after_deadline_and_leaves_only_a_daemon_thread() -> None:
    class Hanging:
        def put(self, key: str, body: bytes) -> None:
            time.sleep(0.5)

    archive = RawArchive(
        uploader=Hanging(), clock=Clock(T0), sleep=Sleeps(), drain_deadline_sec=0.05
    )
    archive.record("upbit", WS, T0, "{}")
    started = time.monotonic()
    await archive.aclose()
    assert time.monotonic() - started < 0.4
    assert archive.pending == 0  # 못 올린 객체는 버렸다
    workers = [t for t in threading.enumerate() if t.name == WORKER_NAME]
    assert workers and all(
        t.daemon for t in workers
    )  # 남은 put 이 종료를 붙들지 않는다


# --- 재생 가능성 (§3.7) ---


async def _upbit_row(frame: str, record: Any) -> Any:
    store, sink = store_with_universe({"BTC"})
    connector = FakeConnector([FakeSocket([frame])])
    stream = UpbitStream(
        store=store,
        sink=sink,
        record=record,
        connect=connector,
        sleep=Sleeps(),
        clock=Clock(T0),
    )
    stream.set_markets(["KRW-BTC"])
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()
    return store.get("upbit", "BTC")


async def test_replaying_an_upbit_line_rebuilds_the_same_row() -> None:
    archive, s3, clock = build()
    original = await _upbit_row(orderbook(levels=3), archive.record)
    clock.now = NEXT
    await archive.run_once()
    await settled(lambda: archive.pending == 0)
    [(_, body)] = s3.puts
    line = next(
        ln for ln in lines_of(body) if json.loads(ln)["raw"].get("type") == "orderbook"
    )
    replayed = await _upbit_row(raw_part(line).decode(), lambda *a: None)
    assert original is not None and replayed is not None
    assert (replayed.asks, replayed.bids, replayed.price, replayed.price_timestamp) == (
        original.asks,
        original.bids,
        original.price,
        original.price_timestamp,
    )
    assert replayed.native_symbol == original.native_symbol == "KRW-BTC"
    await archive.aclose()


async def _binance_row(frame: str, record: Any) -> Any:
    store, sink = store_with_universe({"BTC"})
    connector = FakeConnector([FakeSocket([frame])])
    stream = BinanceStream(
        store=store,
        sink=sink,
        record=record,
        connect=connector,
        sleep=Sleeps(),
        clock=Clock(T0),
    )
    await stream.refresh(
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=exchange_info(["BTCUSDT"]))
            )
        )
    )
    stream.set_universe({"BTC"})
    stream.start()
    await until(connector.exhausted)
    await stream.aclose()
    return store.get("binance", "BTC")


async def test_replaying_a_binance_depth_line_rebuilds_the_same_row() -> None:
    archive, s3, clock = build()
    original = await _binance_row(depth(levels=20), archive.record)
    clock.now = NEXT
    await archive.run_once()
    await settled(lambda: archive.pending == 0)
    [(_, body)] = s3.puts
    line = next(
        ln
        for ln in lines_of(body)
        if json.loads(ln)["source"] == "ws:/stream"
        and "depth20" in json.loads(ln)["raw"].get("stream", "")
    )
    replayed = await _binance_row(raw_part(line).decode(), lambda *a: None)
    assert original is not None and replayed is not None
    assert (replayed.asks, replayed.bids, replayed.price) == (
        original.asks,
        original.bids,
        original.price,
    )
    assert len(replayed.asks) == 20 and replayed.native_symbol == "BTCUSDT"
    await archive.aclose()


# --- 기동 (§3.2·§3.3) ---


def _boot(
    monkeypatch: pytest.MonkeyPatch, **settings: Any
) -> tuple[Any, dict[str, Any]]:
    """lifespan 을 실제로 돌린다 — 소켓·REST 전부 거부, Redis 불달. 원문 아카이브 배선만 본다.

    앱과 함께, 스트림에 넘겨진 기록 함수(`wired["record"]`)를 돌려준다.
    """
    wired: dict[str, Any] = {}

    class CapturingUpbit(UpbitStream):
        def __init__(self, **kw: Any) -> None:
            wired["record"] = kw["record"]
            super().__init__(**kw)

    monkeypatch.setattr("app.main.UpbitStream", CapturingUpbit)

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
        lambda: Settings(_env_file=None, redis_url="redis://127.0.0.1:1/0", **settings),
    )
    return create_app(), wired


def test_boot_without_bucket_disables_archive_and_keeps_health_200(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class NeverBuilt:
        def __init__(self, **kw: Any) -> None:
            raise AssertionError("S3_BUCKET 없이 S3 클라이언트가 만들어졌다")

    monkeypatch.setattr("app.main.S3Uploader", NeverBuilt)
    app, wired = _boot(monkeypatch)
    with (
        caplog.at_level(logging.WARNING, logger="marketlens.main"),
        TestClient(app) as client,
    ):
        assert client.get("/health").status_code == 200
    assert any("S3_BUCKET 이 없어" in r.getMessage() for r in caplog.records)
    assert wired["record"] is noop_record  # 스트림에는 무동작 기록 함수가 꽂힌다


def test_boot_with_bucket_but_no_credentials_still_starts_with_one_error_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Any
) -> None:
    """실제 boto3 로 HeadBucket 을 시도하되 자격증명 탐색을 전부 막는다 — 네트워크 없이 즉시 실패한다."""
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    app, wired = _boot(monkeypatch, s3_bucket="marketlens-test-bucket")
    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        assert client.get("/health").status_code == 200
        time.sleep(0.05)
        assert client.get("/health").status_code == 200
    errors = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.ERROR and r.name == "marketlens.main"
    ]
    assert len(errors) == 1 and "S3 버킷 marketlens-test-bucket 접근 실패" in errors[0]
    assert (
        wired["record"] is not noop_record
    )  # 아카이브는 켜져 있다 — 실패는 워커 로그로만


def test_blank_region_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None, s3_region="").s3_region == "ap-northeast-2"
    assert Settings(_env_file=None, s3_region="  ").s3_region == "ap-northeast-2"
    assert Settings(_env_file=None, s3_region="us-east-1").s3_region == "us-east-1"

    built: dict[str, Any] = {}

    class Recording:
        def __init__(self, **kw: Any) -> None:
            built.update(kw)

        def head_bucket(self) -> None:
            return None

        def put(self, key: str, body: bytes) -> None:
            return None

    monkeypatch.setattr("app.main.S3Uploader", Recording)
    app, wired = _boot(monkeypatch, s3_bucket="b", s3_region="")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert built == {"bucket": "b", "region": "ap-northeast-2"}
    assert wired["record"] is not noop_record


def test_boot_survives_s3_client_construction_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken:
        def __init__(self, **kw: Any) -> None:
            raise ValueError("Invalid endpoint")

    monkeypatch.setattr("app.main.S3Uploader", Broken)
    app, wired = _boot(monkeypatch, s3_bucket="b")
    with caplog.at_level(logging.ERROR, logger="marketlens.main"), TestClient(app) as c:
        assert c.get("/health").status_code == 200
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "S3 클라이언트 생성 실패" in errors[0]
    assert wired["record"] is noop_record
