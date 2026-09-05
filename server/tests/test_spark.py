"""spark — 1분 버킷 마지막 값 30개 링버퍼와 기동 복원 (스펙 009 §3.6, §4 "spark")."""

import logging

import fakeredis
import pytest

from app.core.influx import SparkBucketRow
from app.core.live_store import LiveStore
from app.core.models import Tick, TickRow
from app.core.redis_stream import RedisTickStream
from app.core.spark import SPARK_LEN, SparkBuffer, restore_spark
from app.core.tick_store import TickRelay
from tests.conftest import FakeInflux

T0 = 1_787_000_045  # 분 경계가 아닌 시각 (초 = 45)


def tick(ts: int, fwd: float, bases: tuple[str, ...] = ("BTC",)) -> Tick:
    return Tick(
        ts=ts,
        rows=tuple(
            TickRow(dom="upbit", fx="binance", base=b, fwd=fwd, rev=0.0) for b in bases
        ),
        dw_failed=(),
    )


def relay_with_store() -> tuple[TickRelay, LiveStore]:
    store = LiveStore()
    return TickRelay(stream=None, store=store), store


def test_two_buckets_are_oldest_to_newest_and_same_bucket_keeps_last() -> None:
    relay, store = relay_with_store()
    relay(tick(T0, 1.0))
    relay(tick(T0 + 60, 2.0))
    assert store.spark("upbit", "binance", "BTC") == [1.0, 2.0]
    relay(tick(T0 + 61, 2.5))
    relay(tick(T0 + 62, 3.0))
    assert store.spark("upbit", "binance", "btc") == [1.0, 3.0]


def test_length_is_capped_at_30_buckets() -> None:
    relay, store = relay_with_store()
    for i in range(SPARK_LEN + 1):
        relay(tick(T0 + 60 * i, float(i)))
    spark = store.spark("upbit", "binance", "BTC")
    assert len(spark) == SPARK_LEN and spark[0] == 1.0 and spark[-1] == float(SPARK_LEN)


def test_combination_missing_from_a_tick_keeps_its_trend() -> None:
    relay, store = relay_with_store()
    relay(tick(T0, 1.0, ("BTC", "ETH")))
    relay(tick(T0 + 60, 2.0, ("ETH",)))  # BTC 는 이 틱에서 자격 미달(fail 행)
    assert store.spark("upbit", "binance", "BTC") == [1.0]
    assert store.spark("upbit", "binance", "ETH") == [1.0, 2.0]


async def test_spark_fills_even_when_redis_is_down() -> None:
    server = fakeredis.FakeServer()
    server.connected = False
    store = LiveStore()
    relay = TickRelay(
        stream=RedisTickStream(fakeredis.aioredis.FakeRedis(server=server)),
        store=store,
    )
    relay(tick(T0, 1.0))
    relay(tick(T0 + 60, 2.0))
    assert await relay.drain() == 0
    assert store.spark("upbit", "binance", "BTC") == [1.0, 2.0]


async def test_restore_fills_buffer_from_30_minute_aggregate() -> None:
    influx = FakeInflux()
    now = T0
    minute = now // 60 * 60
    influx.spark_rows = [
        SparkBucketRow(
            "upbit", "binance", "BTC", minute - 60 * 29, 0.5
        ),  # 구간 첫 버킷
        SparkBucketRow("upbit", "binance", "BTC", minute - 60, 1.5),
        SparkBucketRow("upbit", "binance", "BTC", minute, 2.5),  # 기동 분
        SparkBucketRow("upbit", "binance", "BTC", minute - 60 * 30, 9.9),  # 구간 밖
        SparkBucketRow("bithumb", "binance", "eth", minute - 120, 7.0),
    ]
    buffer = SparkBuffer()
    store = LiveStore()
    assert await restore_spark(influx, buffer, store, now) == 4
    assert store.spark("upbit", "binance", "BTC") == [0.5, 1.5, 2.5]
    assert store.spark("bithumb", "binance", "ETH") == [7.0]
    # 복원 뒤 같은 분의 틱은 마지막 값으로 덮고, 다음 분은 이어 붙는다
    relay = TickRelay(stream=None, store=store, spark=buffer)
    relay(tick(now + 1, 3.0))
    relay(tick(now + 60, 4.0))
    assert store.spark("upbit", "binance", "BTC") == [0.5, 1.5, 3.0, 4.0]


async def test_restore_starts_empty_without_influx_on_error_or_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = LiveStore()
    with caplog.at_level(logging.WARNING, logger="marketlens.spark"):
        assert await restore_spark(None, SparkBuffer(), store, T0) == 0
        influx = FakeInflux()
        influx.spark_fail = True
        assert await restore_spark(influx, SparkBuffer(), store, T0) == 0
        slow = FakeInflux()
        slow.spark_rows = [
            SparkBucketRow("upbit", "binance", "BTC", T0 // 60 * 60, 1.0)
        ]
        slow.spark_delay_sec = 0.3
        monkeypatch.setattr("app.core.spark.RESTORE_TIMEOUT_SEC", 0.05)
        assert await restore_spark(slow, SparkBuffer(), store, T0) == 0
    assert store.spark("upbit", "binance", "BTC") == []
    assert len(caplog.records) == 3
