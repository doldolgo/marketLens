"""거래소 원문 아카이브 — 스펙 010 §4. S3 는 `put(key, body)` fake, 시계는 주입, 네트워크 없음."""

import asyncio
import gzip
import json
import logging
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import raw_archive
from app.core.config import Settings
from app.core.raw_archive import RawArchive, format_line, pack
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

T0 = 1_788_600_000_123  # 2026-09-05T09:20:00.123Z
WS = "ws:/websocket/v1"
MINUTE = 60_000


class FakeS3:
    """`put(key, body)` 만 있는 S3 흉내 — 실패 스위치 하나."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes]] = []
        self.fail = False

    def put(self, key: str, body: bytes) -> None:
        if self.fail:
            raise RuntimeError("s3 down")
        self.puts.append((key, body))


def build(**kw: Any) -> tuple[RawArchive, FakeS3, Clock]:
    s3 = FakeS3()
    clock = Clock(T0)
    return RawArchive(uploader=s3, clock=clock, sleep=Sleeps(), **kw), s3, clock


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
    archive, _, _ = build()
    archive.record("upbit", WS, T0, '{"type":"orderbook"}')
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
    ],
)
def test_non_json_payload_is_wrapped_as_string_verbatim(payload: str) -> None:
    line = format_line("upbit", "rest:/v1/market/all", T0, payload)
    assert line.count(b"\n") == 1 and line.endswith(b"\n")
    parsed = json.loads(line)
    assert isinstance(parsed["raw"], str) and parsed["raw"] == payload


# --- 버퍼와 객체 (§3.5) ---


async def test_buffer_closes_after_sixty_seconds_with_key_from_first_line() -> None:
    archive, s3, clock = build()
    archive.record("binance", "ws:/stream", T0, "{}")
    clock.now = T0 + MINUTE - 1
    assert await archive.run_once() == 0 and s3.puts == []
    clock.now = T0 + MINUTE
    assert await archive.run_once() == 1
    [(key, body)] = s3.puts
    assert (
        key == "raw/exchange=binance/dt=2026-09-05/hh=09/20260905T092000.123Z.jsonl.gz"
    )
    assert archive.buffered("binance") == 0
    assert json.loads(lines_of(body)[0])["receivedAt"] == T0


async def test_uncompressed_size_limit_closes_before_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(raw_archive, "CLOSE_AT_BYTES", 300)
    archive, s3, clock = build()
    big = json.dumps({"pad": "x" * 200})
    archive.record("upbit", WS, T0, big)
    archive.record("upbit", WS, T0 + 5, big)  # 두 줄로 300 바이트를 넘는다
    clock.now = T0 + 10
    assert await archive.run_once() == 1
    archive.record("upbit", WS, T0 + 20, big)
    archive.record("upbit", WS, T0 + 25, big)
    clock.now = T0 + 30
    assert await archive.run_once() == 1
    keys = [k for k, _ in s3.puts]
    assert len(set(keys)) == 2 and keys[0].endswith("T092000.123Z.jsonl.gz")
    assert keys[1].endswith("T092000.143Z.jsonl.gz")  # 같은 분 — ms 로 구분된다


async def test_exchanges_get_separate_objects_and_empty_buffers_make_none() -> None:
    archive, s3, clock = build()
    archive.record("upbit", WS, T0, "{}")
    archive.record("bithumb", WS, T0 + 1, "{}")
    clock.now = T0 + MINUTE + 1
    assert await archive.run_once() == 2
    assert [k.split("/")[1] for k, _ in s3.puts] == [
        "exchange=upbit",
        "exchange=bithumb",
    ]
    clock.now = T0 + 3 * MINUTE
    assert await archive.run_once() == 0  # 기록이 없던 회차 — 빈 객체는 없다
    assert await archive.run_once(force_close=True) == 0
    assert len(s3.puts) == 2


async def test_gunzip_line_count_and_order_match_calls_and_pack_is_deterministic() -> (
    None
):
    archive, s3, clock = build()
    payloads = [json.dumps({"i": i}) for i in range(50)]
    for i, p in enumerate(payloads):
        archive.record("upbit", WS, T0 + i, p)
    clock.now = T0 + MINUTE
    await archive.run_once()
    [(_, body)] = s3.puts
    lines = lines_of(body)
    assert len(lines) == 50
    assert [json.loads(line)["raw"]["i"] for line in lines] == list(range(50))
    assert [json.loads(line)["receivedAt"] for line in lines] == [
        T0 + i for i in range(50)
    ]
    again = pack([format_line("upbit", WS, T0 + i, p) for i, p in enumerate(payloads)])
    assert again == body and pack([ln + b"\n" for ln in lines]) == body


# --- 업로드 루프 (§3.6) ---


async def test_failed_upload_stays_at_head_and_is_retried_with_same_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    archive, s3, clock = build()
    archive.record("upbit", WS, T0, "{}")
    clock.now = T0 + MINUTE
    s3.fail = True
    with caplog.at_level(logging.WARNING, logger="marketlens.raw_archive"):
        assert await archive.run_once() == 0
        assert await archive.run_once() == 0
    msgs = [r.getMessage() for r in caplog.records]
    assert any("S3 원문 업로드 실패 (연속 1회)" in m for m in msgs)
    assert any("S3 원문 업로드 실패 (연속 2회)" in m for m in msgs)
    assert archive.pending == 1 and archive.consecutive_failures == 2
    archive.record(
        "bithumb", WS, T0 + MINUTE, "{}"
    )  # 실패 중 닫힌 다음 객체는 뒤에서 기다린다
    clock.now = T0 + 2 * MINUTE
    s3.fail = False
    assert await archive.run_once() == 2
    assert archive.pending == 0 and archive.consecutive_failures == 0
    assert [k.split("/")[1] for k, _ in s3.puts] == [
        "exchange=upbit",
        "exchange=bithumb",
    ]
    assert s3.puts[0][0].endswith("T092000.123Z.jsonl.gz")


async def test_queue_over_limit_drops_oldest_and_logs_its_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    archive, s3, clock = build()
    s3.fail = True
    archive.record("upbit", WS, T0, "{}")
    clock.now = T0 + MINUTE
    await archive.run_once()
    first = pack([format_line("upbit", WS, T0, "{}")])
    monkeypatch.setattr(raw_archive, "QUEUE_LIMIT_BYTES", len(first) * 2 + 10)
    for n in range(1, 4):
        archive.record("upbit", WS, T0 + n * MINUTE, "{}")
        clock.now = T0 + (n + 1) * MINUTE
        with caplog.at_level(logging.ERROR, logger="marketlens.raw_archive"):
            await archive.run_once()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert (
        len(errors) == 2 and "T092000.123Z" in errors[0] and "T092100.123Z" in errors[1]
    )
    assert archive.pending == 2
    s3.fail = False
    clock.now = T0 + 10 * MINUTE
    assert await archive.run_once() == 2
    assert [k[-21:] for k, _ in s3.puts] == [
        "T092200.123Z.jsonl.gz",
        "T092300.123Z.jsonl.gz",
    ]


async def test_uploader_exception_never_reaches_record_and_loop_continues() -> None:
    class Exploding:
        calls = 0

        def put(self, key: str, body: bytes) -> None:
            self.calls += 1
            raise ValueError("boom")

    s3 = Exploding()
    clock = Clock(T0)
    sleeps = Sleeps()
    archive = RawArchive(uploader=s3, clock=clock, sleep=sleeps)
    archive.record("upbit", WS, T0, "{}")
    clock.now = T0 + MINUTE
    archive.start()
    for _ in range(100):
        if s3.calls >= 2:
            break
        await asyncio.sleep(0.005)
    archive.record("upbit", WS, clock.now, "{}")  # 실패 중에도 기록은 즉시 돌아온다
    assert s3.calls >= 2 and archive.buffered("upbit") == 1
    await archive.aclose()


async def test_aclose_closes_open_buffers_and_uploads_once() -> None:
    archive, s3, clock = build()
    archive.start()
    archive.record("upbit", WS, T0, "{}")
    archive.record("binance", "ws:/stream", T0 + 1, "{}")
    await archive.aclose()  # 60초가 안 지났어도 닫는다
    assert len(s3.puts) == 2 and archive.pending == 0
    assert archive.buffered("upbit") == 0 and archive.buffered("binance") == 0


async def test_aclose_gives_up_after_deadline() -> None:
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
    clock.now = T0 + MINUTE
    await archive.run_once()
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
    clock.now = T0 + MINUTE
    await archive.run_once()
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


# --- 기동 (§3.2·§3.3) ---


def _boot(monkeypatch: pytest.MonkeyPatch, **settings: Any) -> Any:
    """lifespan 을 실제로 돌린다 — 소켓·REST 전부 거부, Redis 불달. 원문 아카이브 배선만 본다."""

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
    return create_app()


def test_boot_without_bucket_disables_archive_and_keeps_health_200(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class NeverBuilt:
        def __init__(self, **kw: Any) -> None:
            raise AssertionError("S3_BUCKET 없이 S3 클라이언트가 만들어졌다")

    monkeypatch.setattr("app.main.S3Uploader", NeverBuilt)
    app = _boot(monkeypatch)
    with (
        caplog.at_level(logging.WARNING, logger="marketlens.main"),
        TestClient(app) as client,
    ):
        assert client.get("/health").status_code == 200
    assert any("S3_BUCKET 이 없어" in r.getMessage() for r in caplog.records)


def test_boot_with_bucket_but_no_credentials_still_starts(
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
    app = _boot(monkeypatch, s3_bucket="marketlens-test-bucket")
    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        assert client.get("/health").status_code == 200
        time.sleep(0.05)
        assert client.get("/health").status_code == 200
    assert any(
        "S3 버킷 marketlens-test-bucket 접근 실패" in r.getMessage()
        for r in caplog.records
    )
