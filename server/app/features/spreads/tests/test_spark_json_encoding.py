"""표 게시 인코딩 — spark 자리에 009 의 JSON 조각을 끼워도 `encode_table` 과 같은 바이트 (017 §3.1, 003 §3.2)."""

import json
import math
from datetime import UTC, datetime

import pytest

from app.core.influx import SparkBucketRow
from app.core.live_store import LiveStore
from app.core.models import Tick
from app.core.spark import SparkBuffer
from app.features.spreads.push import (
    SpreadsPublisher,
    encode_table,
    encode_table_with_spark_json,
)
from app.features.spreads.service import build_table
from app.features.spreads.tests.helpers import make_bus, make_row, seed_rows

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def _store() -> LiveStore:
    store = LiveStore()
    seed_rows(
        store,
        [
            make_row(
                "upbit",
                "BTC",
                price=150_000_000,
                asks=[[150_000_100, 1]],
                bids=[[150_000_000, 2]],
            ),
            make_row(
                "binance",
                "BTC",
                price=100_000,
                asks=[[100_001, 1.5]],
                bids=[[99_999, 1.5]],
            ),
            make_row(
                "upbit",
                "SHIB",
                price=0.0187,
                asks=[[0.0188, 5e8]],
                bids=[[0.0187, 5e8]],
            ),
            make_row(
                "binance",
                "SHIB",
                price=1.25e-05,
                asks=[[1.26e-05, 5e8]],
                bids=[[1.25e-05, 5e8]],
            ),
            make_row(
                "upbit", "XRP", price=800.0, asks=[], bids=[[799.0, 10.0]]
            ),  # fail 행
            make_row("binance", "XRP", price=0.55),
        ],
        NOW,
    )
    store.set_rate("upbit", 1450, 1449, NOW)
    store.mark_received(int(NOW.timestamp()))
    return store


def _publish(store: LiveStore, sparks: dict[str, list[float]]) -> None:
    minute = int(NOW.timestamp()) // 60
    buffer = SparkBuffer()
    buffer.seed(
        SparkBucketRow("upbit", "binance", base, (minute - len(values) + 1 + i) * 60, v)
        for base, values in sparks.items()
        for i, v in enumerate(values)
    )
    buffer.publish(store)


def test_fragment_encoding_is_byte_identical_and_leaves_the_payload_as_it_was() -> None:
    store = _store()
    # −0.0004 → −0.0, 긴 소수 → 3자리, 지수 표기, fail 행의 추이, 추이 없는 조합(SHIB)
    _publish(store, {"BTC": [1.23456789, -0.0004, 1e16, 2.0], "XRP": [1e-05, -3.5]})
    payload = build_table(store, now=NOW)
    rows = payload["rows"]
    assert isinstance(rows, list)
    sparks = [row["spark"] for row in rows]
    encoded = encode_table_with_spark_json(payload, store.spark_json())
    assert encoded == encode_table(payload)
    assert '"spark":[1.235,-0.0,1e+16,2.0]' in encoded
    assert '"spark":[0.0,-3.5]' in encoded and '"spark":[]' in encoded
    assert all(row["spark"] is spark for row, spark in zip(rows, sparks, strict=True))


def test_rows_without_fragments_fall_back_to_encoding_the_list() -> None:
    store = _store()
    store.set_spark({("upbit", "binance", "BTC"): [0.5, 0.25]})  # 조각 없이 게시된 맵
    payload = build_table(store, now=NOW)
    encoded = encode_table_with_spark_json(payload, store.spark_json())
    assert encoded == encode_table(payload) and '"spark":[0.5,0.25]' in encoded


def test_non_finite_spark_is_rejected_exactly_like_encode_table() -> None:
    store = _store()
    _publish(store, {"BTC": [1.0, math.nan]})
    assert ("upbit", "binance", "BTC") not in store.spark_json()  # 조각이 없다
    payload = build_table(store, now=NOW)
    with pytest.raises(ValueError):
        encode_table(payload)
    with pytest.raises(ValueError):
        encode_table_with_spark_json(payload, store.spark_json())


async def test_publisher_encodes_in_the_same_sync_section_as_the_table() -> None:
    """조각은 표를 만든 그 동기 구간에서 읽는다 — 보내기 전에 009 가 다시 게시해도 큐의 표는 그때의 추이다 (§3.1)."""
    store = _store()
    _publish(store, {"BTC": [1.0, 2.0]})
    bus, _ = make_bus()
    publisher = SpreadsPublisher(store=store, bus=bus)
    publisher.observe(Tick(ts=int(NOW.timestamp()), rows=(), dw_failed=()))
    _publish(store, {"BTC": [7.0, 8.0, 9.0]})  # 보내기 태스크가 돌기 전의 다음 게시
    assert await publisher.drain() == 1
    latest = await bus.latest()
    assert latest is not None
    rows = {row["sym"]: row for row in json.loads(latest)["rows"]}
    assert rows["BTC"]["spark"] == [1.0, 2.0]
