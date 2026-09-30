"""flusher 흘려 쓰기·인계 gzip·스트림 상한 — 스펙 009 §3.4·§3.5 (2026-09-28 성능 개선 규칙).

점 객체를 만들지 않고 엔트리에서 바로 만든 줄이, 페이지 전체를 점으로 펼쳐 5,000점씩 `to_line` 한 것과
배치 경계·순서·바이트까지 같은지 본다. Redis 는 fakeredis, Influx 는 줄을 그대로 남기는 fake.
"""

import gzip
import json
import random

import fakeredis

from app.core import tick_store
from app.core.influx import InfluxPoint, dw_fail_point, premium_point, to_line
from app.core.models import Tick, TickRow
from app.core.redis_stream import MAXLEN, RedisTickStream
from app.core.tick_store import WRITE_BATCH, Flusher, encode_tick
from tests.conftest import FakeInflux

T0 = 1_787_000_000
# 태그 이스케이프가 걸리는 이름(콤마·공백·등호·역슬래시)과 소문자 코인(premium 태그는 대문자)을 섞는다
BASES = ["BTC", "eth", "A,B", "C D", "E=F", "G\\H", "1000SATS"]
VALUES = [0.0, -0.0, 1e-7, -3.25, 1.2345678901234567, 123456.789, 2.5e-310]


def _tick(ts: int, n_rows: int, rng: random.Random, dw: tuple[str, ...] = ()) -> Tick:
    rows = tuple(
        TickRow(
            dom=("upbit", "bithumb")[i % 2],
            fx=("binance", "bybit", "bitget")[i % 3],
            base=f"{BASES[i % len(BASES)]}{i // len(BASES)}",
            fwd=rng.choice(VALUES) if i % 5 == 0 else rng.uniform(-5, 5),
            rev=rng.choice(VALUES) if i % 7 == 0 else rng.uniform(-5, 5),
        )
        for i in range(n_rows)
    )
    return Tick(ts=ts, rows=rows, dw_failed=dw)


def _reference_batches(ticks: list[Tick]) -> list[list[str]]:
    """페이지를 점 목록으로 펼쳐 5,000점씩 자른 뒤 `to_line` — 흘려 쓰기가 같아야 하는 기준."""
    points: list[InfluxPoint] = []
    for t in ticks:
        points.extend(
            premium_point(
                dom=r.dom, fx=r.fx, base=r.base, ts=t.ts, fwd=r.fwd, rev=r.rev
            )
            for r in t.rows
        )
        points.extend(dw_fail_point(exchange=ex, ts=t.ts) for ex in t.dw_failed)
    return [
        [to_line(p) for p in points[i : i + WRITE_BATCH]]
        for i in range(0, len(points), WRITE_BATCH)
    ]


def _stream() -> RedisTickStream:
    return RedisTickStream(fakeredis.aioredis.FakeRedis())


async def test_streamed_lines_match_point_serialization_batch_for_batch() -> None:
    """배치 경계·순서·줄 바이트가 점을 만들어 5,000점씩 쓴 것과 같다(틱을 걸치는 배치 포함)."""
    rng = random.Random(7)
    ticks = [
        _tick(T0, 2_600, rng, dw=("upbit",)),
        _tick(T0 + 1, 2_600, rng),
        _tick(T0 + 2, 0, rng, dw=("bithumb", "binance")),
        _tick(T0 + 3, 4_999, rng, dw=("bybit",)),
        _tick(T0 + 4, 1, rng),
    ]
    stream = _stream()
    for t in ticks:
        await stream.add(t.ts, encode_tick(t))
    influx = FakeInflux()
    assert await Flusher(stream=stream, writer=influx).flush_once() is True
    expected = _reference_batches(ticks)
    assert influx.batches == expected
    # 틱 경계와 무관한 5,000줄 배치
    assert [len(b) for b in expected] == [5_000, 5_000, 204]
    assert await stream.length() == 0


async def test_failed_first_batch_stops_before_unpacking_the_rest_of_the_page(
    monkeypatch,
) -> None:
    """Influx 가 막혀 있으면 첫 배치에서 실패하고 나머지 틱은 풀지 않는다 — 메모리는 배치 하나 크기."""
    rng = random.Random(1)
    stream = _stream()
    for i in range(10):
        await stream.add(T0 + i, encode_tick(_tick(T0 + i, 2_600, rng)))
    unpacked: list[int] = []
    real = gzip.decompress

    def counting(data: bytes) -> bytes:
        unpacked.append(1)
        return real(data)

    monkeypatch.setattr(tick_store.gzip, "decompress", counting)
    influx = FakeInflux()
    influx.fail = True
    assert await Flusher(stream=stream, writer=influx).flush_once() is False
    # 5,000줄이 차는 둘째 틱에서 첫 쓰기 → 실패, 셋째 틱부터는 풀지 않았다
    assert len(unpacked) == 2
    assert await stream.length() == 10  # 페이지는 지우지 않는다


def test_encoded_tick_is_gzip_level_6_with_zero_mtime_and_stable_bytes() -> None:
    rng = random.Random(3)
    t = _tick(T0, 1_458, rng, dw=("upbit",))
    first = encode_tick(t)
    assert first == encode_tick(t)  # 같은 틱은 언제 인코딩해도 같은 바이트
    assert first[4:8] == b"\x00\x00\x00\x00"  # gzip 머리의 시각 0
    body = json.dumps(
        {
            "ts": t.ts,
            "rows": [
                {"dom": r.dom, "fx": r.fx, "base": r.base, "fwd": r.fwd, "rev": r.rev}
                for r in t.rows
            ],
            "dwFailed": ["upbit"],
        },
        separators=(",", ":"),
    ).encode()
    assert gzip.decompress(first) == body
    assert first == gzip.compress(body, compresslevel=6, mtime=0)


async def test_entries_from_before_the_level_change_flush_to_the_same_lines() -> None:
    """배포 때 스트림에 남아 있던 옛 엔트리(레벨 9·기록 시각 있음)도 같은 줄로 옮겨진다."""
    rng = random.Random(5)
    t = _tick(T0, 300, rng, dw=("upbit",))
    old = gzip.compress(gzip.decompress(encode_tick(t)))  # 레벨 9·지금 시각 — 옛 인코딩
    assert old != encode_tick(t)
    batches: list[list[list[str]]] = []
    for data in (old, encode_tick(t)):
        stream = _stream()
        await stream.add(t.ts, data)
        influx = FakeInflux()
        assert await Flusher(stream=stream, writer=influx).flush_once() is True
        batches.append(influx.batches)
    assert batches[0] == batches[1] == _reference_batches([t])


async def test_stream_add_trims_at_three_hours() -> None:
    """`XADD ticks MAXLEN ~ 10800` — 3시간 안전 상한(틱 ≈41KB 면 ≈0.44GB)."""
    seen: dict[str, object] = {}

    class Recorder(fakeredis.aioredis.FakeRedis):
        async def xadd(self, name, fields, **kw):  # noqa: ANN001, ANN003, ANN201
            seen.update(kw)
            return await super().xadd(name, fields, **kw)

    await RedisTickStream(Recorder()).add(T0, b"x")
    assert MAXLEN == 10_800
    assert seen["maxlen"] == 10_800 and seen["approximate"] is True
