"""snapshot 루프 — 키·내용·생략·실패·기동 (스펙 010 §3.4~3.5, §4). S3 는 fake."""

import asyncio
import gzip
import json
import logging
import re
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.collector import Collector
from app.core.config import Settings
from app.core.live_store import LiveStore
from app.core.s3 import S3UnavailableError, S3Uploader
from app.core.snapshot import SnapshotLoop, build_object
from app.features.spreads.service import build_spreads
from app.main import create_app
from tests.conftest import FakeConnector, make_row

FIXED_SEC = 1_700_000_000
FIXED_DT = datetime.fromtimestamp(FIXED_SEC, tz=UTC)

# 한 줄의 키는 정확히 21개 — 행 18키(API 순서) + 최상위 3키 (§3.4)
EXPECTED_KEYS = [
    "sym", "dom", "fx", "fwd", "rev", "usd", "spark", "status", "age",
    "liqDom", "liqFx", "rateAsk", "rateBid", "netDom", "depDom", "wdDom",
    "depFx", "wdFx", "rate", "dataReceivedAt", "warnings",
]  # fmt: skip


class FakeS3:
    """`put(key, body)` 시그니처의 메모리 저장소 — 실제 네트워크 없음 (§4)."""

    def __init__(self) -> None:
        self.objects: list[tuple[str, bytes]] = []
        self.fail = False
        self.raise_once: Exception | None = None

    def put(self, key: str, body: bytes) -> None:
        if self.raise_once is not None:
            exc, self.raise_once = self.raise_once, None
            raise exc
        if self.fail:
            raise S3UnavailableError("업로드 실패 (테스트)")
        self.objects.append((key, body))


def seeded_store() -> LiveStore:
    """upbit(BTC·ETH·USDT) + bithumb(BTC) + binance(BTC·ETH) — 행 3개."""
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit", "BTC", asks=[[101_000_000.0, 1.0]], bids=[[100_000_000.0, 1.0]]
            ),
            make_row(
                "upbit", "ETH", asks=[[5_050_000.0, 1.0]], bids=[[5_000_000.0, 1.0]]
            ),
            make_row("upbit", "USDT", asks=[[1401.0, 1.0]], bids=[[1399.0, 1.0]]),
        ],
        FIXED_DT,
    )
    store.replace_exchange(
        "bithumb",
        [
            make_row(
                "bithumb",
                "BTC",
                asks=[[101_100_000.0, 1.0]],
                bids=[[100_100_000.0, 1.0]],
            )
        ],
        FIXED_DT,
    )
    store.replace_exchange(
        "binance",
        [
            make_row("binance", "BTC", asks=[[71_000.0, 1.0]], bids=[[70_900.0, 1.0]]),
            make_row("binance", "ETH", asks=[[3_550.0, 1.0]], bids=[[3_540.0, 1.0]]),
        ],
        FIXED_DT,
    )
    store.set_rate("upbit", 1401.0, 1399.0, FIXED_DT)
    store.set_rate("bithumb", 1402.0, 1398.0, FIXED_DT)
    store.mark_received(FIXED_SEC)
    return store


def make_loop(
    store: LiveStore, s3: FakeS3, client: httpx.AsyncClient, interval: float = 60.0
) -> SnapshotLoop:
    collector = Collector(
        store=store,
        domestic=[FakeConnector("upbit", [[]]), FakeConnector("bithumb", [[]])],
        foreign=FakeConnector("binance", [[]]),
        client=client,
    )
    return SnapshotLoop(store=store, collector=collector, s3=s3, interval=interval)


def gunzip_lines(body: bytes) -> list[dict[str, object]]:
    text = gzip.decompress(body).decode("utf-8")
    return [json.loads(line) for line in text.splitlines()]


async def test_key_format_matches_data_received_at(
    unused_client: httpx.AsyncClient,
) -> None:
    # 수집 1회 후 회차 → 객체 1개, 키 형식·시각이 dataReceivedAt 의 UTC 와 일치 (§4)
    store = seeded_store()
    s3 = FakeS3()
    loop = make_loop(store, s3, unused_client)
    assert await loop.snapshot_once() == 1
    assert len(s3.objects) == 1
    key, _ = s3.objects[0]
    assert re.fullmatch(
        r"spreads/dt=\d{4}-\d{2}-\d{2}/hh=\d{2}/\d{8}T\d{6}Z\.jsonl\.gz", key
    )
    expected = f"spreads/dt={FIXED_DT:%Y-%m-%d}/hh={FIXED_DT:%H}/{FIXED_DT:%Y%m%dT%H%M%SZ}.jsonl.gz"
    assert key == expected


async def test_lines_match_spreads_rows(unused_client: httpx.AsyncClient) -> None:
    # 줄 수 = 행 수, 각 줄이 정확히 21키, 값이 API 응답과 동일 (§4)
    store = seeded_store()
    s3 = FakeS3()
    loop = make_loop(store, s3, unused_client)
    await loop.snapshot_once()
    lines = gunzip_lines(s3.objects[0][1])

    api = build_spreads(store, now=FIXED_DT)
    assert len(lines) == len(api.rows) == 3
    for line, row in zip(lines, api.rows, strict=True):
        assert list(line.keys()) == EXPECTED_KEYS
        assert line["sym"] == row.sym
        assert line["dom"] == row.dom
        assert line["fx"] == row.fx
        assert line["fwd"] == row.fwd
        assert line["rev"] == row.rev
        assert line["liqDom"] == row.liq_dom
        assert line["netDom"] == row.net_dom
        # 최상위 세 값은 모든 줄에서 같고 API 최상위 값과 같다
        assert line["rate"] == api.rate
        assert line["dataReceivedAt"] == api.data_received_at == FIXED_SEC * 1000
        # age·warnings 는 현재 시각 의존이라 루프 회차 값과 다르다 — 모든 줄에서 같은지만 본다
        assert line["warnings"] == lines[0]["warnings"]

    # 같은 now 로 만들면 값이 전부 일치한다 (§4 — 같은 행의 값이 API 응답과 동일)
    _, body = build_object(api)
    for line, row in zip(gunzip_lines(body), api.rows, strict=True):
        assert line["age"] == row.age
        assert line["status"] == row.status
        assert line["warnings"] == api.warnings == []


def test_line_order_and_deterministic_bytes() -> None:
    # 줄 순서 = (sym, dom, fx) 오름차순, 같은 스냅샷은 바이트까지 같다 (§4)
    store = seeded_store()
    payload = build_spreads(store, now=FIXED_DT)
    key1, body1 = build_object(payload)
    key2, body2 = build_object(build_spreads(store, now=FIXED_DT))
    assert key1 == key2
    assert body1 == body2
    lines = gunzip_lines(body1)
    triples = [(ln["sym"], ln["dom"], ln["fx"]) for ln in lines]
    assert triples == sorted(triples)


async def test_same_received_at_skipped_then_new_uploads(
    unused_client: httpx.AsyncClient,
) -> None:
    # 수집 없이 회차 2번 → 객체 1개, 수집이 한 번 더 돈 뒤 회차 → 객체 2개 (§4)
    store = seeded_store()
    s3 = FakeS3()
    loop = make_loop(store, s3, unused_client)
    assert await loop.snapshot_once() == 1
    assert await loop.snapshot_once() == 0
    assert len(s3.objects) == 1
    store.mark_received(FIXED_SEC + 60)  # 수집이 한 번 더 돈 것과 같은 효과
    assert await loop.snapshot_once() == 1
    assert len(s3.objects) == 2


async def test_before_any_collect_uploads_nothing(
    unused_client: httpx.AsyncClient,
) -> None:
    # 수집이 아직 안 돌았을 때 회차 → 아무것도 안 올리고 0 반환 (§4)
    store = LiveStore()
    s3 = FakeS3()
    loop = make_loop(store, s3, unused_client)
    assert await loop.snapshot_once() == 0
    assert s3.objects == []


async def test_no_base_rate_skips_with_warning(
    unused_client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    # 기준 거래소 시세가 없을 때 회차 → 생략·경고 로그, 예외 없음 (§4)
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [make_row("upbit", "BTC", asks=[[101.0, 1.0]], bids=[[100.0, 1.0]])],
        FIXED_DT,
    )
    store.mark_received(FIXED_SEC)  # 수집은 돌았지만 환율 관측이 없던 상태
    s3 = FakeS3()
    loop = make_loop(store, s3, unused_client)
    with caplog.at_level(logging.WARNING, logger="marketlens.snapshot"):
        assert await loop.snapshot_once() == 0
    assert s3.objects == []
    assert any("생략" in r.message for r in caplog.records)


async def test_upload_failure_logs_and_retries_same_snapshot(
    unused_client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    # 실패 회차 → 0 반환·연속 횟수 로그, 다음 회차에 같은 dataReceivedAt 재시도 (§4)
    store = seeded_store()
    s3 = FakeS3()
    loop = make_loop(store, s3, unused_client)
    s3.fail = True
    with caplog.at_level(logging.ERROR, logger="marketlens.snapshot"):
        assert await loop.snapshot_once() == 0
        assert await loop.snapshot_once() == 0
    messages = [r.getMessage() for r in caplog.records]
    assert any("연속 1회" in m for m in messages)
    assert any("연속 2회" in m for m in messages)

    s3.fail = False
    caplog.clear()
    assert await loop.snapshot_once() == 1  # 실패했으니 같은 시각이어도 다시 올린다
    lines = gunzip_lines(s3.objects[0][1])
    assert lines[0]["dataReceivedAt"] == FIXED_SEC * 1000

    # 성공하면 연속 횟수가 0 으로 돌아간다 — 다음 실패는 다시 "연속 1회"
    store.mark_received(FIXED_SEC + 60)
    s3.fail = True
    with caplog.at_level(logging.ERROR, logger="marketlens.snapshot"):
        assert await loop.snapshot_once() == 0
    assert any("연속 1회" in r.getMessage() for r in caplog.records)


async def test_loop_survives_unexpected_exception(
    unused_client: httpx.AsyncClient,
) -> None:
    # fake 가 (계약 밖) 예외를 던져도 루프가 다음 회차를 이어간다 (§4)
    store = seeded_store()
    s3 = FakeS3()
    s3.raise_once = ValueError("버그 (테스트)")
    loop = make_loop(store, s3, unused_client, interval=0.01)
    task = asyncio.create_task(loop.run_loop())
    try:
        # 첫 회차는 예외로 끝나고, 이어지는 회차가 업로드에 성공할 때까지 기다린다
        for _ in range(200):
            if s3.objects:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert len(s3.objects) == 1


# --- 기동 (lifespan) — 수집 루프는 no-op 으로 막아 네트워크를 차단한다 ---


def _startup_app(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    async def _noop(self: Collector) -> None:
        return None

    # lifespan 이 수집 루프를 켜지만 실제 거래소 호출은 테스트에서 금지다
    monkeypatch.setattr(Collector, "run_loop", _noop)
    app = create_app()
    app.state.settings = settings
    return app


def test_startup_without_bucket_disables_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # S3_BUCKET 없이 기동 → 루프 비활성·경고 로그, /health 200, /spreads 정상 (§4)
    settings = Settings(_env_file=None, influx_token=None, s3_bucket=None)
    app = _startup_app(monkeypatch, settings)
    with caplog.at_level(logging.WARNING, logger="marketlens.main"):
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            # 메모리를 직접 채우면 /spreads 도 정상 동작한다
            seeded = seeded_store()
            app.state.live_store._snapshots = seeded._snapshots  # noqa: SLF001
            app.state.live_store._rates = seeded._rates  # noqa: SLF001
            app.state.live_store.mark_received(FIXED_SEC)
            resp = client.get("/spreads")
            assert resp.status_code == 200
            assert len(resp.json()["rows"]) == 3
    assert any("S3_BUCKET" in r.getMessage() for r in caplog.records)


def test_startup_with_bucket_but_no_credentials(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # S3_BUCKET 은 있는데 자격증명이 없어도 기동 성공(/health 200) — 실패는 로그로만 (§4)
    monkeypatch.setattr(S3Uploader, "head_bucket", lambda self: False)
    settings = Settings(_env_file=None, influx_token=None, s3_bucket="no-such-bucket")
    app = _startup_app(monkeypatch, settings)
    with caplog.at_level(logging.ERROR, logger="marketlens.main"):
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
    assert any("S3 버킷 접근 확인 실패" in r.getMessage() for r in caplog.records)
