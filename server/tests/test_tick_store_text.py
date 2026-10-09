"""인계 레코드 글 재사용(TickRelay)·flusher 숫자 글자 재사용 — 바꾸기 전 경로와 바이트가 같은지 (009 §3.4·§3.5, 2026-10-09).

- 인계: 값이 직전과 같은 조합은 행 글을 다시 쓰지 않는다 → 큐에 든 바이트가 매 틱 `encode_tick(tick)` 과 같아야 한다.
- flusher: 레코드의 숫자 글자를 float 로 바꾸지 않고 줄에 쓴다 → 배치·줄이 바꾸기 전 규칙(json.loads 기본 →
  float() → `premium_point` → `to_line`)과 같아야 한다. 정수·NaN·±Infinity 처럼 글자가 아닌 값도 섞는다.
- 결합 고정: 인계 레코드의 float 글자가 `repr(float)` 라는 전제(`encode_tick` 의 json.dumps) — 직렬화기를 바꾸면 깨진다.
"""

import gzip
import json
import lzma
import math
import random
import struct
from datetime import timedelta

import fakeredis

from app.core.influx import (
    dw_fail_point,
    premium_head,
    premium_line,
    premium_line_text,
    premium_point,
    to_line,
)
from app.core.live_store import LiveStore
from app.core.models import Tick, TickRow
from app.core.redis_stream import RedisTickStream
from app.core.tick_store import WRITE_BATCH, Flusher, TickRelay, encode_tick
from app.core.ticks import build_tick
from tests.conftest import FakeInflux
from tests.test_real_tables import DATA, _next_second, build_world
from tests.test_real_tables import NOW as REAL_NOW

T0 = 1_787_000_000
EDGE = [1e-05, 1e300, 5e-324, 0.1 + 0.2, -0.0, 0.0, 2.5e-310, 1.2345678901234567, -3.25, 123456.789, 1e16]
ODD_NAMES = ["BTC", "eth", "A,B", "C D", "E=F", "G\\H", '가"\\x\u2028']


def _reference_batches(datas: list[bytes]) -> list[list[str]]:
    """바꾸기 전 flusher 규칙 — json.loads(기본) → float() → 점 → `to_line`, 5,000줄씩."""
    lines: list[str] = []
    for data in datas:
        record = json.loads(gzip.decompress(data))
        ts = int(record["ts"])
        for r in record["rows"]:
            lines.append(
                to_line(premium_point(dom=r["dom"], fx=r["fx"], base=r["base"], ts=ts, fwd=float(r["fwd"]), rev=float(r["rev"])))
            )
        lines.extend(to_line(dw_fail_point(exchange=ex, ts=ts)) for ex in record["dwFailed"])
    return [lines[i : i + WRITE_BATCH] for i in range(0, len(lines), WRITE_BATCH)]


def _tick(ts: int, n: int, rng: random.Random, dw: tuple[str, ...] = ()) -> Tick:
    special = [*EDGE, math.nan, math.inf, -math.inf, 3, 0]  # 정수 3·0 — json 은 "3"·"0" 으로 적는다
    rows = tuple(
        TickRow(
            ("upbit", "bithumb")[i % 2],
            ("binance", "bybit", "okx")[i % 3],
            f"{ODD_NAMES[i % len(ODD_NAMES)]}{i // len(ODD_NAMES)}",
            rng.choice(special) if i % 4 == 0 else rng.uniform(-5, 5),
            rng.choice(special) if i % 6 == 0 else rng.uniform(-5, 5),
        )
        for i in range(n)
    )
    return Tick(ts=ts, rows=rows, dw_failed=dw)


async def test_flusher_lines_match_the_float_round_trip_on_edge_values() -> None:
    rng = random.Random(21)
    ticks = [_tick(T0, 2_600, rng, ("upbit",)), _tick(T0 + 1, 2_600, rng), _tick(T0 + 2, 0, rng, ("bithumb", "okx")), _tick(T0 + 3, 4_999, rng)]
    datas = [encode_tick(t) for t in ticks]
    assert b"NaN" in gzip.decompress(datas[0]) and b"Infinity" in gzip.decompress(datas[0])
    stream = RedisTickStream(fakeredis.aioredis.FakeRedis())
    for t, data in zip(ticks, datas, strict=True):
        await stream.add(t.ts, data)
    influx = FakeInflux()
    assert await Flusher(stream=stream, writer=influx).flush_once() is True
    expected = _reference_batches(datas)
    assert influx.batches == expected
    joined = "\n".join("\n".join(b) for b in expected)
    # 정수 값도 "3.0" 으로(기준은 float() 뒤 repr), NaN·inf 는 repr 그대로
    assert "fwd=3.0," in joined and "=nan" in joined and "=inf" in joined and "=-inf" in joined


def test_record_digits_are_float_repr_so_the_text_line_is_the_float_line() -> None:
    """`encode_tick` 의 숫자 글자 = repr(float) — 무작위 비트 패턴(부정규·큰 지수 포함)과 김프 모양 값에서."""
    rng = random.Random(5)
    values = [struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))[0] for _ in range(20_000)]
    values += [rng.uniform(-10, 10) for _ in range(20_000)] + EDGE
    values = [v for v in values if math.isfinite(v)]
    head = premium_head("upbit", "binance", "btc")
    rows = tuple(TickRow("upbit", "binance", "BTC", v, -v) for v in values)
    record = json.loads(gzip.decompress(encode_tick(Tick(ts=T0, rows=rows, dw_failed=()))), parse_float=str)
    for v, r in zip(values, record["rows"], strict=True):
        fwd, rev = r["fwd"], r["rev"]
        if type(fwd) is not str:  # 정수 글자로 적힌 float 는 없다 — float 는 늘 소수점이나 지수가 붙는다
            raise AssertionError(fwd)
        assert fwd == repr(v) and rev == repr(-v)
        assert premium_line_text(head, fwd, rev, f" {T0}") == premium_line(head, v, -v, T0)


async def test_relay_bytes_equal_encode_tick_tick_by_tick() -> None:
    """같은 조합의 값 되풀이·0.0↔−0.0·NaN·inf 연속·float→int·bool·조합이 빠졌다 다시 나옴·dw 2개·이스케이프 이름."""
    k = ("upbit", "binance", "BTC")
    o = ("bithumb", "bybit", '가"\\x\u2028')

    def t(ts: int, rows: list[tuple], dw: tuple[str, ...] = ()) -> Tick:
        return Tick(ts=ts, rows=tuple(TickRow(*r) for r in rows), dw_failed=dw)

    seq = [
        t(1, [(*k, 1.25, 2.5), (*o, -0.0, 0.0)]),
        t(2, [(*k, 1.25, 2.5), (*o, 0.0, -0.0)]),  # 값은 == 이지만 부호가 다르다
        t(3, [(*k, 1.25, 2.5), (*o, 0.0, -0.0)], ("bithumb", "upbit")),
        t(4, [(*k, math.nan, 2.5)]),
        t(5, [(*k, math.nan, 2.5)]),
        t(6, [(*k, math.inf, -math.inf)]),
        t(7, [(*k, 2.0, 1.0)]),
        t(8, [(*k, 2, 1.0)]),  # float 2.0 다음 int 2 — json 은 "2"
        t(9, [(*k, True, 1.0)]),  # bool — json 은 "true"
        t(10, [(*k, 2.0, 1.0)]),
        t(11, []),
        t(12, [], ("okx",)),
        t(13, [(*k, 2.0, 1.0), (*o, 5e-324, 1e300)]),  # 빠졌다가 같은 값으로 다시
        t(2**62, [(*k, 2.0, 1.0)]),
    ]
    relay = TickRelay(stream=None, store=LiveStore())
    for tick in seq:
        relay(tick)
        if not tick.rows and not tick.dw_failed:
            assert relay.pending == 0
            continue
        ts, data = relay._queue.pop()
        assert ts == tick.ts and data == encode_tick(tick), tick


async def test_relay_bytes_equal_encode_tick_on_real_ticks() -> None:
    """실데이터 표로 만든 틱 4개(매초 행 절반이 바뀐다) — 직전 글을 다시 쓰는 조합이 섞여도 바이트가 같다."""
    table = json.loads(lzma.decompress(DATA.read_bytes()))[0]
    store, _, buffer = build_world(table, seed=7)
    rng = random.Random(8)
    relay = TickRelay(stream=None, store=store, spark=buffer)
    now = REAL_NOW
    reused = 0
    for _ in range(4):
        tick = build_tick(store, int(now.timestamp()), ["upbit"])
        before = dict(relay._row_texts)
        relay(tick)
        assert relay._queue.pop()[1] == encode_tick(tick)
        reused += sum(1 for r in tick.rows if before.get((r.dom, r.fx, r.base)) is relay._row_texts.get((r.dom, r.fx, r.base)))
        now += timedelta(seconds=1)
        _next_second(store, rng, now)
    assert reused > 1_000  # 값이 그대로인 조합은 실제로 직전 글을 다시 썼다
