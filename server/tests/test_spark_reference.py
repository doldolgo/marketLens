"""spark 버퍼 구조 변경(2026-10-09) — 끝 버킷 + 게시 목록만 드는 버퍼가 옛 (버킷, 값) 링버퍼와 같은 목록·조각을 내는지 (009 §3.6).

기준은 바꾸기 전 코드(9b13b2c)의 `SparkBuffer` 를 그대로 옮긴 `_DequeSpark` 다. 같은 틱 열을 두 버퍼에 넣고 매 틱
게시 목록(−0.0 부호·NaN 포함)과 JSON 조각 맵을 견준다 — 같은 분 반복·다음 분·분 건너뛰기·지난 분·NaN·inf·
−0.0·1e16·소문자 코인·조합이 빠졌다 다시 나옴·seed 뒤 이어 넣기·복원(_adopt) 뒤 이어 넣기.
"""

import math
import random
from collections import deque
from collections.abc import Iterable

from app.core.influx import SparkBucketRow
from app.core.live_store import LiveStore, SparkKey
from app.core.models import Tick, TickRow
from app.core.spark import (
    BUCKET_SEC,
    SPARK_DIGITS,
    SPARK_LEN,
    SparkBuffer,
    restore_spark,
)

T0 = 1_791_480_000  # 분 경계


class _DequeSpark:
    """바꾸기 전 `SparkBuffer`(deque[(버킷, 값)] 링버퍼 + (버킷, 원값) 튜플) — 대조 기준. 고치지 않는다."""

    def __init__(self) -> None:
        self._buf: dict[SparkKey, deque[tuple[int, float]]] = {}
        self._raw: dict[SparkKey, tuple[int, float]] = {}
        self._lists: dict[SparkKey, list[float]] = {}
        self._json: dict[SparkKey, str] = {}
        self._heads: dict[SparkKey, str | None] = {}

    def update(self, tick: Tick) -> None:
        bucket = tick.ts // BUCKET_SEC
        raw = self._raw
        for row in tick.rows:
            key = (row.dom, row.fx, row.base.upper())
            fwd = row.fwd
            prev = raw.get(key)
            if prev is not None and prev[0] == bucket and prev[1] == fwd:
                continue
            raw[key] = (bucket, fwd)
            self._put(key, bucket, fwd)

    def seed(self, rows: Iterable[SparkBucketRow]) -> None:
        for r in sorted(rows, key=lambda r: r.bucket_ts):
            key = (r.dom, r.fx, r.base.upper())
            self._raw.pop(key, None)
            self._put(key, r.bucket_ts // BUCKET_SEC, r.fwd)

    def _put(self, key: SparkKey, bucket: int, value: float) -> None:
        value = round(value, SPARK_DIGITS)
        buf = self._buf.get(key)
        if buf is None:
            buf = deque(maxlen=SPARK_LEN)
            self._buf[key] = buf
        if buf and buf[-1][0] == bucket:
            buf[-1] = (bucket, value)
            values = self._lists[key].copy()
            values[-1] = value
            head = self._heads[key]
        elif not buf or buf[-1][0] < bucket:
            dropped = len(buf) == SPARK_LEN
            buf.append((bucket, value))
            values = [v for _, v in buf]
            head = self._next_head(key, buf, dropped)
            self._heads[key] = head
        else:
            return
        self._lists[key] = values
        if head is not None and math.isfinite(value):
            self._json[key] = head + repr(value) + "]"
        else:
            self._json.pop(key, None)

    def _next_head(
        self, key: SparkKey, buf: deque[tuple[int, float]], dropped: bool
    ) -> str | None:
        prev = self._json.get(key)
        if prev is None:
            before = [v for _, v in buf][:-1]
            if not all(math.isfinite(v) for v in before):
                return None
            return "[" + "".join(repr(v) + "," for v in before)
        if dropped:
            cut = prev.find(",")
            rest = prev[cut + 1 : -1] if cut >= 0 else ""
            return "[" + rest + "," if rest else "["
        return prev[:-1] + ","

    def snapshot(self) -> dict[SparkKey, list[float]]:
        return dict(self._lists)

    def fragments(self) -> dict[SparkKey, str]:
        return dict(self._json)


def _same_lists(a: dict[SparkKey, list[float]], b: dict[SparkKey, list[float]]) -> bool:
    """값·순서·−0.0 부호까지 같음(NaN 은 NaN 끼리 같다고 본다)."""
    if a.keys() != b.keys():
        return False
    return all(
        len(a[k]) == len(b[k])
        and all(
            (x == y or (x != x and y != y)) and math.copysign(1.0, x) == math.copysign(1.0, y)
            for x, y in zip(a[k], b[k], strict=True)
        )
        for k in a
    )


def _assert_same(new: SparkBuffer, ref: _DequeSpark) -> None:
    assert _same_lists(new.snapshot(), ref.snapshot())
    assert new.fragments() == ref.fragments()


def _random_ticks(n: int, seed: int) -> list[Tick]:
    rng = random.Random(seed)
    keys = [("upbit", ("binance", "bybit")[i % 2], ("BTC", "eth", "Xrp", f"C{i}")[i % 4]) for i in range(40)]
    ts = T0
    out = []
    for _ in range(n):
        ts += rng.choice((0, 1, 1, 1, 1, 7, 61, 300, -60))  # 같은 초·다음 초·분 건너뛰기·지난 분
        rows = []
        for dom, fx, base in rng.sample(keys, rng.randint(0, 40)):
            fwd = rng.choice(
                (rng.uniform(-3, 3), 0.0, -0.0, 1e-4, -4e-4, math.nan, math.inf, -math.inf, 1e16, round(rng.uniform(-1, 1), 3), 2.0)
            )
            rows.append(TickRow(dom, fx, base, fwd, 0.0))
        out.append(Tick(ts=ts, rows=tuple(rows), dw_failed=()))
    return out


def test_publishes_the_same_lists_and_fragments_as_the_ring_buffer_on_random_ticks() -> None:
    for seed in (1, 2, 3):
        new, ref = SparkBuffer(), _DequeSpark()
        for tick in _random_ticks(1_500, seed):
            new.update(tick)
            ref.update(tick)
            _assert_same(new, ref)


def test_seed_then_ticks_match_including_a_full_window_rollover() -> None:
    minute = T0 // 60
    rows = [
        SparkBucketRow("upbit", "binance", base, (minute - SPARK_LEN + 1 + i) * 60, v)
        for base in ("btc", "ETH")
        for i, v in enumerate([0.1 * i - 1.0 for i in range(SPARK_LEN)])
    ] + [SparkBucketRow("bithumb", "okx", "SOL", (minute - 3) * 60, math.nan)]
    new, ref = SparkBuffer(), _DequeSpark()
    new.seed(rows)
    ref.seed(rows)
    _assert_same(new, ref)
    # 같은 분 → 다음 분(가득 찬 창에서 맨 앞이 빠진다) → 분 건너뛰기 → 지난 분(무시)
    for ts, fwd in ((T0 + 5, 0.5), (T0 + 6, 0.5), (T0 + 60, -0.0), (T0 + 61, 0.25), (T0 + 300, 1e16), (T0 + 200, 9.0)):
        tick = Tick(ts=ts, rows=(TickRow("upbit", "binance", "BTC", fwd, 0.0), TickRow("bithumb", "okx", "sol", fwd, 0.0)), dw_failed=())
        new.update(tick)
        ref.update(tick)
        _assert_same(new, ref)
    btc = new.snapshot()[("upbit", "binance", "BTC")]
    assert len(btc) == SPARK_LEN and btc[-1] == 1e16
    # 고정 기대값 — NaN 이 창에서 빠지기 전까지 SOL 은 조각이 없다
    assert ("bithumb", "okx", "SOL") not in new.fragments()
    assert new.fragments()[("upbit", "binance", "ETH")] == "[" + ",".join(repr(round(0.1 * i - 1.0, 3)) for i in range(SPARK_LEN)) + "]"


async def test_restore_adopts_every_field_so_the_next_ticks_continue_the_same_way() -> None:
    """복원(스레드에서 채운 버퍼 → _adopt) 뒤 첫 틱부터 기준과 같다 — 끝 버킷·직전 원값을 빠짐없이 넘겨받는다."""
    minute = T0 // 60

    class Reader:
        def query_spark(self, *, start: int, stop: int, timeout_sec: float | None = None) -> list[SparkBucketRow]:
            return [SparkBucketRow("upbit", "binance", "BTC", (minute - 2 + i) * 60, v) for i, v in enumerate([1.0, 2.0, 3.0])]

    new = SparkBuffer()
    store = LiveStore()
    assert await restore_spark(Reader(), new, store, T0 + 30) == 3
    ref = _DequeSpark()
    ref.seed(Reader().query_spark(start=0, stop=0))
    _assert_same(new, ref)
    for tick in _random_ticks(300, 9):
        new.update(tick)
        ref.update(tick)
        _assert_same(new, ref)
