"""사건 감지기 — Influx 쓰기 시점·재시도·복원·틱 루프 연결 (스펙 013 §3.3, §4)."""

import asyncio

import httpx

from app.core.influx import premium_event_point, to_line
from app.core.live_store import LiveStore
from app.core.models import Tick
from app.core.premium_events import PENDING_LIMIT, PremiumEventDetector
from app.core.ticks import TickLoop
from tests.conftest import FakeStream
from tests.premium_event_fakes import (
    T0,
    FakeInflux,
    feed,
    open_one,
    restored_row,
    row,
    tick,
)

# ── Influx 쓰기 시점 (§3.3) ──────────────────────────────────────────────────


async def test_writes_first_point_after_60s_then_refreshes_then_closes() -> None:
    influx = FakeInflux()
    det = PremiumEventDetector(writer=influx)
    feed(det, [1.5] * 60)  # T0 .. T0+59 — 열린 지 60초 전
    await det.flush()
    assert influx.write_calls == 0
    det.observe(tick(T0 + 61, row(fwd=1.5)))
    await det.flush()
    p = influx.only()
    assert (p["end_ts"], p["duration_seconds"], p["samples"], p["enter_percent"]) == (
        0,
        0,
        61,
        1.0,
    )
    det.observe(tick(T0 + 90, row(fwd=2.0)))
    await det.flush()
    assert influx.only()["samples"] == 61  # 60초 갱신 전엔 그대로
    det.observe(tick(T0 + 121, row(fwd=2.0)))
    await det.flush()
    assert (influx.only()["samples"], influx.only()["max_percent"]) == (63, 2.0)
    det.observe(tick(T0 + 130, row(fwd=0.2)))
    await det.flush()
    assert len(influx.data) == 1  # 같은 키 덮어쓰기
    assert (influx.only()["end_ts"], influx.only()["duration_seconds"]) == (
        T0 + 130,
        130,
    )


async def test_write_failure_retries_next_round_not_before_60s() -> None:
    influx = FakeInflux()
    now = [1_000.0]
    det = PremiumEventDetector(writer=influx, clock=lambda: now[0])
    det.observe(tick(T0, row(fwd=1.5)))
    influx.fail = True
    det.observe(tick(T0 + 61, row(fwd=1.5)))
    await det.write_round()
    assert influx.write_calls == 1 and influx.data == {}
    influx.fail = False
    det.observe(tick(T0 + 70, row(fwd=0.1)))  # 닫힘 — 실패 뒤 60초 안이라 아직 안 쓴다
    now[0] += 30
    await det.write_round()
    assert influx.write_calls == 1
    now[0] += 31
    await det.write_round()
    assert influx.write_calls == 2 and influx.only()["end_ts"] == T0 + 70


async def test_pending_is_capped_at_limit() -> None:
    influx = FakeInflux()
    influx.fail = True
    det = PremiumEventDetector(writer=influx)
    rows = [row(base=f"C{i}", fwd=1.5) for i in range(PENDING_LIMIT + 5)]
    det.observe(tick(T0, *rows))
    det.observe(tick(T0 + 61, *rows))
    await det.write_round()
    influx.fail = False
    await det.flush()
    assert len(influx.data) == PENDING_LIMIT
    assert all(
        ("base", f"C{i}") not in key[1] for key in influx.data for i in range(5)
    )  # 오래된 것(C0~C4)부터 버렸다


def test_point_shape_matches_db_md() -> None:
    line = to_line(premium_event_point(restored_row(T0, T0 + 10, end_ts=T0 + 100)))
    assert line.startswith("premium_event,base=SOPH,dir=kimp,dom=upbit,fx=binance ")
    assert "end_ts=1700000100i" in line and "max_percent=1.5" in line
    assert line.endswith(f" {T0}")


# ── 복원 (§3.3) ──────────────────────────────────────────────────────────────


async def test_restore_closes_stale_and_keeps_recent() -> None:
    influx = FakeInflux()
    now = T0 + 10_000
    influx.rows = [
        restored_row(T0, now - 601),
        restored_row(T0 + 1_000, now - 300, base="BONK"),
        restored_row(
            T0 + 2_000, now - 100, end_ts=T0 + 2_500, base="ETH"
        ),  # 닫힌 건 무시
    ]
    det = PremiumEventDetector(writer=influx)
    await det.restore(influx, now)
    assert [e.base for e in det.open_events()] == ["BONK"]
    await det.flush()
    p = influx.only()
    assert (p["end_ts"], p["duration_seconds"]) == (now - 601, now - 601 - T0)


async def test_restore_keeps_only_latest_open_per_combination() -> None:
    influx = FakeInflux()
    now = T0 + 10_000
    influx.rows = [restored_row(T0 + 500, now - 100), restored_row(T0, now - 100)]
    det = PremiumEventDetector(writer=influx)
    await det.restore(influx, now)
    assert open_one(det).start_ts == T0 + 500
    await det.flush()
    assert influx.only()["end_ts"] == now - 100  # 옛것은 last_ts 로 닫아 썼다


async def test_restore_failure_or_timeout_starts_empty() -> None:
    influx = FakeInflux()
    influx.rows = [restored_row(T0, T0 + 100)]
    influx.query_fail = True
    det = PremiumEventDetector(writer=influx)
    await det.restore(influx, T0 + 200)
    assert det.open_events() == []

    influx.query_fail = False
    influx.query_delay = 3.5
    det2 = PremiumEventDetector(writer=influx)
    await asyncio.wait_for(det2.restore(influx, T0 + 200), timeout=5)
    assert det2.open_events() == []


async def test_without_influx_detection_still_runs() -> None:
    det = PremiumEventDetector(writer=None)
    await det.restore(None, T0)
    det.observe(tick(T0, row(fwd=1.5)))
    det.observe(tick(T0 + 61, row(fwd=1.5)))
    await det.flush()
    assert open_one(det).written is True


# ── 틱 루프 연결 (§2 바꾸는 것 1) ───────────────────────────────────────────


def test_tick_loop_passes_current_tick_to_detector() -> None:
    seen: list[int] = []

    class Sink:
        def observe(self, tick: Tick) -> None:
            seen.append(tick.ts)

    loop = TickLoop(
        store=LiveStore(),
        streams=[FakeStream("upbit")],
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: None)),
        events=Sink(),
    )
    loop.tick(T0)
    loop.tick(T0 + 1)
    assert seen == [T0, T0 + 1]
