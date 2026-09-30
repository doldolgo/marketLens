"""spark 증분 게시·JSON 조각 (스펙 009 §3.6, 2026-09-28 성능 개선)."""

import json
import math
import random
from collections import deque

from app.core.influx import SparkBucketRow
from app.core.live_store import LiveStore
from app.core.models import Tick, TickRow
from app.core.spark import SPARK_DIGITS, SPARK_LEN, SparkBuffer, restore_spark
from app.core.tick_store import TickRelay
from tests.conftest import FakeInflux

T0 = 1_787_000_045  # 분 경계가 아닌 시각 (초 = 45)
SEP = (",", ":")
KEY = ("upbit", "binance", "BTC")


class FullRebuild:
    """기준 — 틱마다 전 조합을 반올림해 링버퍼에 넣고 게시 맵을 통째로 새로 만든다."""

    def __init__(self) -> None:
        self.buf: dict[tuple[str, str, str], deque[tuple[int, float]]] = {}

    def update(self, tick: Tick) -> None:
        bucket = tick.ts // 60
        for row in tick.rows:
            key = (row.dom, row.fx, row.base.upper())
            value = round(row.fwd, SPARK_DIGITS)
            buf = self.buf.setdefault(key, deque(maxlen=SPARK_LEN))
            if buf and buf[-1][0] == bucket:
                buf[-1] = (bucket, value)
            elif not buf or buf[-1][0] < bucket:
                buf.append((bucket, value))

    def snapshot(self) -> dict[tuple[str, str, str], list[float]]:
        return {k: [v for _, v in b] for k, b in self.buf.items()}


def _tick(ts: int, **fwd: float) -> Tick:
    return Tick(
        ts=ts,
        rows=tuple(TickRow("upbit", "binance", b, v, 0.0) for b, v in fwd.items()),
        dw_failed=(),
    )


def _dumps(values: list[float]) -> str:
    return json.dumps(values, separators=SEP)


def test_incremental_publish_equals_full_rebuild_and_fragments_equal_json_dumps() -> (
    None
):
    rng = random.Random(5)
    store = LiveStore()
    relay = TickRelay(stream=None, store=store)
    ref = FullRebuild()
    keys = [
        ("upbit", "binance", "BTC"),
        ("upbit", "bybit", "ETH"),
        ("bithumb", "binance", "xrp"),
    ]
    published: list[tuple[list[float], str]] = []
    ts = T0
    for _ in range(2_500):
        ts += rng.choice((1, 1, 1, 2, 59, 61, 125))
        rows = []
        for dom, fx, base in keys:
            if rng.random() < 0.1:
                continue  # 결측 — 그 조합은 직전 추이가 남는다
            fwd = rng.choice((1.2345, 1.2345, 0.0, -0.0004, 2.0, rng.uniform(-3, 3)))
            rows.append(TickRow(dom, fx, base, fwd, 0.0))
        tick = Tick(ts=ts, rows=tuple(rows), dw_failed=())
        relay(tick)
        ref.update(tick)
        expected = ref.snapshot()
        fragments = store.spark_json()
        assert set(fragments) == set(expected)
        for key, values in expected.items():
            got = store.spark(*key)
            assert _dumps(got) == _dumps(values)  # −0.0 까지 같다
            assert fragments[key] == _dumps(values)
            published.append((got, _dumps(got)))
    # 게시된 목록은 누구도 제자리에서 고치지 않는다 — 나중에 봐도 게시 때 값 그대로
    assert all(_dumps(obj) == text for obj, text in published)


def test_unchanged_combination_keeps_the_published_list_object() -> None:
    store = LiveStore()
    relay = TickRelay(stream=None, store=store)
    relay(_tick(T0, BTC=1.0, ETH=1.0))
    btc = store.spark("upbit", "binance", "BTC")
    eth = store.spark("upbit", "binance", "ETH")
    relay(_tick(T0 + 1, BTC=1.0, ETH=2.0))
    # 같은 분·같은 원값 — 반올림·링버퍼를 건너뛰고 게시 목록도 그대로
    assert store.spark("upbit", "binance", "BTC") is btc
    # 바뀐 조합만 새 목록으로 갈아 끼우고 옛 목록은 그대로 둔다
    assert store.spark("upbit", "binance", "ETH") == [2.0] and eth == [1.0]


async def test_restore_publishes_lists_and_fragments_before_the_first_tick() -> None:
    influx = FakeInflux()
    minute = T0 // 60 * 60
    influx.spark_rows = [
        SparkBucketRow("upbit", "binance", "BTC", minute - 60, 1.25),
        SparkBucketRow("upbit", "binance", "BTC", minute, -0.0004),
    ]
    buffer = SparkBuffer()
    store = LiveStore()
    assert await restore_spark(influx, buffer, store, T0) == 2
    assert _dumps(store.spark(*KEY)) == "[1.25,-0.0]"
    assert store.spark_json()[KEY] == "[1.25,-0.0]"
    # 복원 분의 끝값과 같은 원값이 첫 틱으로 와도 게시는 이어진다(비지 않는다)
    relay = TickRelay(stream=None, store=store, spark=buffer)
    relay(_tick(T0 + 1, BTC=-0.0004))
    assert store.spark_json()[KEY] == "[1.25,-0.0]"
    relay(_tick(T0 + 60, BTC=0.5))
    assert store.spark_json()[KEY] == "[1.25,-0.0,0.5]"


def test_non_finite_value_has_no_fragment_until_it_leaves_the_window() -> None:
    store = LiveStore()
    relay = TickRelay(stream=None, store=store)
    relay(_tick(T0, BTC=math.nan))
    assert math.isnan(store.spark(*KEY)[-1]) and KEY not in store.spark_json()
    relay(_tick(T0 + 60, BTC=1.0))  # 앞자리에 NaN 이 남아 있다
    assert KEY not in store.spark_json()
    for i in range(2, SPARK_LEN + 1):
        relay(_tick(T0 + 60 * i, BTC=float(i)))
    values = store.spark(*KEY)
    assert len(values) == SPARK_LEN and not any(math.isnan(v) for v in values)
    assert store.spark_json()[KEY] == _dumps(values)
