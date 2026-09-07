"""사건 감지기 — 열기·유지·닫기·버리기·결측·조합 독립 (스펙 013 §3.2, §4)."""

from app.core.premium_events import PremiumEventDetector
from tests.premium_event_fakes import (
    T0,
    FakeInflux,
    feed,
    open_one,
    row,
    tick,
)

# ── 열기·유지·닫기 (§3.2) ────────────────────────────────────────────────────


def test_opens_at_exactly_enter_but_not_below() -> None:
    det = PremiumEventDetector()
    det.observe(tick(T0, row(fwd=0.99)))
    assert det.open_events() == []
    det.observe(tick(T0 + 1, row(fwd=1.0)))
    ev = open_one(det)
    assert (ev.start_ts, ev.max_percent, ev.max_ts, ev.samples, ev.dir) == (
        T0 + 1,
        1.0,
        T0 + 1,
        1,
        "kimp",
    )


async def test_hysteresis_keeps_until_exit_inclusive() -> None:
    influx = FakeInflux()
    det = PremiumEventDetector(writer=influx)
    feed(
        det, [1.2, 0.8, 0.6, 0.51, 0.5], step=100
    )  # 0.51 까지 이어지고 0.5 에서 닫힌다
    assert det.open_events() == []
    await det.flush()
    p = influx.only()
    assert (p["end_ts"], p["duration_seconds"], p["samples"], p["max_percent"]) == (
        T0 + 400,
        400,
        4,
        1.2,
    )


async def test_drop_below_exit_and_back_makes_two_events() -> None:
    influx = FakeInflux()
    det = PremiumEventDetector(writer=influx)
    feed(det, [1.2, 0.4, 1.2], step=100)
    await det.flush()
    assert len(influx.data) == 1 and influx.only()["end_ts"] == T0 + 100
    assert open_one(det).start_ts == T0 + 200


async def test_spike_of_60s_is_discarded_but_61s_is_kept() -> None:
    influx = FakeInflux()
    det = PremiumEventDetector(writer=influx)
    det.observe(tick(T0, row(fwd=1.5)))
    det.observe(tick(T0 + 60, row(fwd=0.1)))  # duration 60 → 없던 것
    await det.flush()
    assert influx.data == {} and influx.write_calls == 0

    det.observe(tick(T0 + 100, row(fwd=1.5)))
    det.observe(tick(T0 + 161, row(fwd=0.1)))  # duration 61 → 기록
    await det.flush()
    assert influx.only()["duration_seconds"] == 61


def test_max_and_samples_track_observed_ticks() -> None:
    det = PremiumEventDetector()
    feed(det, [1.0, 1.7, 2.3, 0.9, 1.1])
    ev = open_one(det)
    assert (ev.max_percent, ev.max_ts, ev.samples, ev.last_ts) == (
        2.3,
        T0 + 2,
        5,
        T0 + 4,
    )


# ── 결측 (§3.2) ──────────────────────────────────────────────────────────────


def test_gap_of_599s_continues_and_601s_closes_at_last_ts() -> None:
    det = PremiumEventDetector()
    det.observe(tick(T0, row(fwd=1.5)))
    det.observe(tick(T0 + 599))  # 행 없음
    det.observe(tick(T0 + 599, row(fwd=1.5)))
    assert open_one(det).samples == 2

    det2 = PremiumEventDetector()
    det2.observe(tick(T0, row(fwd=1.5)))
    det2.observe(tick(T0 + 601))
    assert det2.open_events() == []
    det2.observe(tick(T0 + 602, row(fwd=1.5)))  # 닫힌 뒤 돌아온 값 ≥ 1.0 → 새 사건
    assert open_one(det2).start_ts == T0 + 602


async def test_gap_close_keeps_short_event_that_was_already_written() -> None:
    influx = FakeInflux()
    det = PremiumEventDetector(writer=influx)
    det.observe(tick(T0, row(fwd=1.5)))
    det.observe(tick(T0 + 30, row(fwd=1.5)))  # 마지막 관측
    det.observe(tick(T0 + 61))  # 열린 지 60초 경과 → 첫 점
    await det.flush()
    assert influx.only()["end_ts"] == 0
    det.observe(tick(T0 + 631))  # 601초 결측 → last_ts 로 닫는다
    await det.flush()
    assert (influx.only()["end_ts"], influx.only()["duration_seconds"]) == (T0 + 30, 30)


def test_missing_row_is_not_zero() -> None:
    det = PremiumEventDetector()
    det.observe(tick(T0, row(fwd=1.5)))
    det.observe(tick(T0 + 1))  # 행 없음 — 0 이 아니라 값 없음이라 닫히지 않는다
    assert open_one(det).samples == 1


# ── 조합·방향 독립 ───────────────────────────────────────────────────────────


def test_combinations_and_directions_are_independent() -> None:
    det = PremiumEventDetector()
    det.observe(
        tick(
            T0,
            row(fwd=1.5, dom="upbit"),
            row(fwd=1.5, dom="bithumb"),
            row(base="BONK", rev=1.2),
        )
    )
    keys = sorted(e.key for e in det.open_events())
    assert keys == [
        ("bithumb", "binance", "SOPH", "kimp"),
        ("upbit", "binance", "BONK", "reverse"),
        ("upbit", "binance", "SOPH", "kimp"),
    ]
    det.observe(tick(T0 + 100, row(fwd=0.1, dom="upbit"), row(fwd=1.5, dom="bithumb")))
    assert sorted(e.dom for e in det.open_events()) == ["bithumb", "upbit"]
