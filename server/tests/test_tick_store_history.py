"""틱 → Redis → flusher → Influx → /history/* — 005 의 조회가 009 가 쓴 점으로 동작한다 (009 §4 "응답·회귀")."""

import fakeredis

from app.core.live_store import LiveStore
from app.core.models import Tick, TickRow
from app.core.redis_stream import RedisTickStream
from app.core.tick_store import Flusher, TickRelay
from app.features.history.tests.helpers import make_client
from tests.conftest import FakeInflux

# 2026-08-26 (수) 00:00:00 UTC — 이 주의 월요일은 08-24
T0 = 1_787_702_400


def tick(ts: int, fwd: float, rev: float) -> Tick:
    return Tick(
        ts=ts,
        rows=(TickRow(dom="upbit", fx="binance", base="BTC", fwd=fwd, rev=rev),),
        dw_failed=("bithumb",) if ts % 2 else (),
    )


async def test_history_premium_reads_points_written_by_the_flusher() -> None:
    stream = RedisTickStream(fakeredis.aioredis.FakeRedis())
    influx = FakeInflux()
    relay = TickRelay(stream=stream, store=LiveStore())
    for i, (fwd, rev) in enumerate([(1.0, -1.0), (2.0, -2.0), (0.5, -0.5)]):
        relay(tick(T0 + i, fwd, rev))
    await relay.drain()
    assert await Flusher(stream=stream, writer=influx).flush_once() is True

    resp = make_client(influx).get(
        "/history/premium", params={"base": "BTC", "unit": "week", "date": "2026-08-26"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3 and body["firstTs"] == T0
    assert [e["dt"] for e in body["events"]] == [0, 1, 1]
    assert [e["fwd"] for e in body["events"]] == [1.0, 2.0, 0.5]
    assert body["summary"] == {
        "firstFwd": 1.0,
        "lastFwd": 0.5,
        "minFwd": 0.5,
        "maxFwd": 2.0,
    }
    assert len(influx.stored("dw_fail")) == 1  # ts 홀수인 틱 1건


async def test_history_streaks_counts_segments_over_flusher_points() -> None:
    stream = RedisTickStream(fakeredis.aioredis.FakeRedis())
    influx = FakeInflux()
    relay = TickRelay(stream=stream, store=LiveStore())
    for i, fwd in enumerate([3.0, 3.5, 0.1, 4.0]):
        relay(tick(T0 + i, fwd, 0.0))
    await relay.drain()
    await Flusher(stream=stream, writer=influx).flush_once()
    resp = make_client(influx).get(
        "/history/streaks",
        params={"base": "BTC", "threshold": 3.0, "start": T0, "end": T0 + 10},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scanned"] == 4 and body["kimp"]["count"] == 2
    assert body["kimp"]["segments"][0]["samples"] == 2
