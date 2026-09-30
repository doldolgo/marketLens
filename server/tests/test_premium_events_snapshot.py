"""사건 복원 원천·종료 저장·복원 닫힘 — 스펙 013 §3.3 (2026-09-28 사람 결정).

열린 사건 사본(Redis 키 `premium_events:open`)을 60초 갱신 회차·닫힘 점을 쓴 회차·종료 때 쓰기 태스크가 저장하고,
기동 복원은 사본을 먼저 읽고 없거나 실패하면 Influx 로 되돌아간다. 복원이 닫은 점은 미전송 상한과 별개로 첫
회차에 모두 쓴다.
"""

import asyncio
import json
import logging

import fakeredis
import pytest

from app.core import premium_events
from app.core.premium_events import MAX_GAP_SEC, PENDING_LIMIT, PremiumEventDetector
from app.core.redis_bus import OPEN_EVENTS_KEY, OPEN_EVENTS_TTL_SEC, RedisBus
from tests.premium_event_fakes import (
    T0,
    FakeInflux,
    FakeSnapshots,
    open_one,
    restored_row,
    row,
    tick,
)


def _state(det: PremiumEventDetector) -> list[tuple]:
    return sorted(
        (
            e.dom,
            e.fx,
            e.base,
            e.dir,
            e.start_ts,
            e.max_percent,
            e.max_ts,
            e.last_ts,
            e.samples,
            e.net_dom,
            e.net_fx,
            e.written,
        )
        for e in det.open_events()
    )


async def test_snapshot_is_serialized_on_refresh_and_saved_by_the_writer() -> None:
    snaps = FakeSnapshots()
    det = PremiumEventDetector(writer=FakeInflux(), snapshots=snaps)
    # 첫 틱이 곧 갱신 회차
    det.observe(tick(T0, row(fwd=1.5, net_dom="Ethereum", net_fx=None)))
    assert snaps.saves == []  # 루프는 직렬화만 — 저장은 쓰기 태스크가
    await det.write_round()
    assert len(snaps.saves) == 1
    [saved] = json.loads(snaps.saves[0])
    assert (saved["base"], saved["start_ts"], saved["net_dom"], saved["net_fx"]) == (
        "SOPH",
        T0,
        "Ethereum",
        None,
    )
    det.observe(tick(T0 + 30, row(fwd=1.6)))  # 60초 안 — 새 사본 없음
    await det.write_round()
    assert len(snaps.saves) == 1
    det.observe(tick(T0 + 60, row(fwd=1.7)))  # 다음 갱신 회차
    await det.write_round()
    assert len(snaps.saves) == 2 and json.loads(snaps.saves[1])[0]["samples"] == 3


async def test_restore_from_snapshot_skips_influx_and_rebuilds_open_events() -> None:
    snaps = FakeSnapshots()
    before = PremiumEventDetector(writer=FakeInflux(), snapshots=snaps)
    before.observe(
        tick(
            T0,
            row(fwd=1.5, net_dom="Ethereum", net_fx="ERC20"),
            row(base="BONK", rev=2.0),
        )
    )
    before.observe(
        tick(
            T0 + 70,
            row(fwd=2.5, net_dom="Ethereum", net_fx="ERC20"),
            row(base="BONK", rev=1.2),
        )
    )
    # 열린 지 60초 전 — 점이 없던 사건도 사본엔 있다
    before.observe(tick(T0 + 75, row(base="NEW", fwd=1.1)))
    await before.final_round()

    influx = FakeInflux()
    influx.rows = [restored_row(T0 - 9_000, T0 - 8_000, base="OLD")]  # 읽히면 안 된다
    after = PremiumEventDetector(writer=influx, snapshots=snaps)
    await after.restore(influx, T0 + 80)
    assert influx.query_calls == 0
    assert _state(after) == _state(before)
    assert [e.written for e in sorted(after.open_events(), key=lambda e: e.base)] == [
        True,
        False,
        True,
    ]


async def test_restore_falls_back_to_influx_when_the_snapshot_is_missing_broken_or_unreadable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    for snaps in (FakeSnapshots(None), FakeSnapshots("{not json"), FakeSnapshots("[]")):
        if snaps.data == "[]":
            snaps.load_fail = True
        influx = FakeInflux()
        influx.rows = [restored_row(T0, T0 + 100)]
        det = PremiumEventDetector(writer=influx, snapshots=snaps)
        with caplog.at_level(logging.INFO, logger="marketlens.premium_events"):
            await det.restore(influx, T0 + 200)
        assert influx.query_calls == 1 and influx.query_timeouts == [3.0]
        assert open_one(det).start_ts == T0
    assert sum("사건 복원(Influx)" in r.getMessage() for r in caplog.records) == 3


async def test_empty_snapshot_means_nothing_open_without_asking_influx() -> None:
    influx = FakeInflux()
    influx.rows = [restored_row(T0, T0 + 100)]
    det = PremiumEventDetector(writer=influx, snapshots=FakeSnapshots("[]"))
    await det.restore(influx, T0 + 200)
    assert det.open_events() == [] and influx.query_calls == 0


async def test_restore_rules_apply_to_the_snapshot_too() -> None:
    """사본에서 온 사건도 마지막 관측이 600초를 넘었으면 `last_ts` 로 닫아 쓴다."""
    snaps = FakeSnapshots()
    before = PremiumEventDetector(writer=FakeInflux(), snapshots=snaps)
    before.observe(tick(T0, row(fwd=1.5), row(base="BONK", fwd=1.5)))
    before.observe(tick(T0 + 61, row(fwd=1.5)))  # BONK 는 T0 에서 멈춤
    await before.final_round()
    influx = FakeInflux()
    after = PremiumEventDetector(writer=influx, snapshots=snaps)
    await after.restore(influx, T0 + 650)
    assert [e.base for e in after.open_events()] == ["SOPH"]
    await after.flush()
    [closed] = influx.data.values()
    assert (closed["end_ts"], closed["last_ts"]) == (T0, T0)


async def test_final_round_saves_snapshot_and_writes_inside_the_failure_gate() -> None:
    influx = FakeInflux()
    snaps = FakeSnapshots()
    now = [1_000.0]
    det = PremiumEventDetector(writer=influx, clock=lambda: now[0], snapshots=snaps)
    det.observe(tick(T0, row(fwd=1.5)))
    influx.fail = True
    det.observe(tick(T0 + 61, row(fwd=1.5)))
    await det.write_round()  # 실패 — 60초 게이트가 걸린다
    influx.fail = False
    det.observe(tick(T0 + 62, row(fwd=3.0)))
    await det.final_round()  # 게이트 무시, 사본은 지금 상태로
    assert influx.only()["end_ts"] == 0
    assert json.loads(snaps.data)[0]["max_percent"] == 3.0


async def test_final_round_is_bounded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Stuck:
        async def open_events_load(self) -> str | None:
            return None

        async def open_events_save(self, data: str) -> None:
            await asyncio.sleep(10)

    monkeypatch.setattr(premium_events, "FINAL_TIMEOUT_SEC", 0.05)
    det = PremiumEventDetector(writer=FakeInflux(), snapshots=Stuck())
    det.observe(tick(T0, row(fwd=1.5)))
    with caplog.at_level(logging.WARNING, logger="marketlens.premium_events"):
        await asyncio.wait_for(det.final_round(), timeout=1)
    assert "종료 사건 저장이" in caplog.text


async def test_restore_closes_are_all_written_beyond_the_pending_cap_with_no_drop_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    influx = FakeInflux()
    now = T0 + 10_000
    n = PENDING_LIMIT + 500  # 고아 1,500건 — 미전송 상한보다 많다
    influx.rows = [restored_row(T0 + i, T0 + i + 10, base=f"C{i}") for i in range(n)]
    det = PremiumEventDetector(writer=influx)
    with caplog.at_level(logging.WARNING, logger="marketlens.premium_events"):
        await det.restore(influx, now)
        await det.flush()
    assert len(influx.data) == n and all(p["end_ts"] != 0 for p in influx.data.values())
    assert "버림" not in caplog.text
    assert influx.write_calls == 1  # 첫 회차 한 번에


async def test_runtime_drops_are_reported_once_per_round(
    caplog: pytest.LogCaptureFixture,
) -> None:
    influx = FakeInflux()
    influx.fail = True
    det = PremiumEventDetector(writer=influx)
    rows = [row(base=f"C{i}", fwd=1.5) for i in range(PENDING_LIMIT + 5)]
    det.observe(tick(T0, *rows))
    det.observe(tick(T0 + 61, *rows))
    influx.fail = False
    with caplog.at_level(logging.WARNING, logger="marketlens.premium_events"):
        await det.flush()
    drops = [r.getMessage() for r in caplog.records if "버림" in r.getMessage()]
    assert drops == ["premium_event 미전송 1000건 초과 — 오래된 것부터 5건 버림"]


async def _until(cond, what: str) -> None:  # noqa: ANN001
    """쓰기 태스크(스레드 쓰기 포함)가 한 바퀴 돌 때까지 — 2초 안에 안 되면 실패."""
    for _ in range(400):
        if cond():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{what} 이 일어나지 않았다")


def _saved_bases(snaps: FakeSnapshots) -> list[str]:
    return sorted(d["base"] for d in json.loads(snaps.data or "[]"))


async def test_a_closed_event_is_not_reopened_after_an_abnormal_exit() -> None:
    """닫힘 점을 쓴 회차가 사본도 다시 저장한다 — 60초 갱신 사이에 닫힌 사건이 비정상 종료(쓰기 태스크 취소,
    종료 1회 없음) 뒤 600초 안의 재기동에서 다시 열려 Influx 의 닫힘 점을 덮지 않는다. 버린 스파이크도 같다."""
    influx = FakeInflux()
    snaps = FakeSnapshots()
    det = PremiumEventDetector(writer=influx, snapshots=snaps)
    writer = asyncio.create_task(det.run_writer_loop())
    try:
        # 첫 틱은 갱신 회차 — 사본에 둘
        det.observe(tick(T0, row(fwd=1.5), row(base="BONK", fwd=1.5)))
        det.observe(tick(T0 + 61, row(fwd=1.6), row(base="BONK", fwd=1.5)))
        await _until(lambda: len(influx.data) == 2, "첫 점 쓰기")
        # 60초를 못 넘길 사건 — 점은 없다
        det.observe(tick(T0 + 100, row(base="NEW", fwd=1.2)))
        det.observe(
            tick(
                T0 + 121,
                row(fwd=1.6),
                row(base="BONK", fwd=1.5),
                row(base="NEW", fwd=1.2),
            )
        )
        await _until(
            lambda: _saved_bases(snaps) == ["BONK", "NEW", "SOPH"], "갱신 사본"
        )
        saves = len(snaps.saves)
        # 갱신 회차가 아닌 틱에서 SOPH 는 닫히고(종료 이하) NEW 는 1분을 못 넘긴 스파이크로 버려진다
        det.observe(
            tick(
                T0 + 130,
                row(fwd=0.3),
                row(base="BONK", fwd=1.5),
                row(base="NEW", fwd=0.2),
            )
        )
        await _until(lambda: _saved_bases(snaps) == ["BONK"], "닫힘 회차 사본")
    finally:
        writer.cancel()  # 비정상 종료 — final_round 없이 태스크만 멈춘다
        with pytest.raises(asyncio.CancelledError):
            await writer
    assert len(snaps.saves) > saves
    closed = next(p for p in influx.data.values() if p["end_ts"])
    assert (closed["end_ts"], closed["last_ts"]) == (T0 + 130, T0 + 121)

    after = PremiumEventDetector(writer=influx, snapshots=snaps)
    await after.restore(influx, T0 + 200)  # 600초 안의 재기동 — 사본을 읽는다
    assert [e.base for e in after.open_events()] == ["BONK"]
    after.observe(
        tick(
            T0 + 201, row(fwd=0.3), row(base="BONK", fwd=1.5), row(base="NEW", fwd=0.2)
        )
    )
    await after.flush()
    closed = [p for p in influx.data.values() if p["end_ts"]]
    assert [(p["end_ts"], p["last_ts"]) for p in closed] == [(T0 + 130, T0 + 121)]
    assert influx.query_calls == 0


async def test_snapshot_keeps_a_closed_event_until_its_close_is_written() -> None:
    """닫힘 쓰기가 먼저, 사본이 다음 — 닫힘 점 쓰기가 실패한 회차는 사본을 다시 만들지 않는다."""
    influx = FakeInflux()
    snaps = FakeSnapshots()
    det = PremiumEventDetector(writer=influx, snapshots=snaps)
    det.observe(tick(T0, row(fwd=1.5)))
    det.observe(tick(T0 + 61, row(fwd=1.6)))
    await det.write_round()
    assert _saved_bases(snaps) == ["SOPH"]
    saves = len(snaps.saves)
    influx.fail = True
    det.observe(tick(T0 + 70, row(fwd=0.3)))  # 닫힘
    await det.write_round()
    assert len(snaps.saves) == saves and _saved_bases(snaps) == ["SOPH"]
    influx.fail = False
    await det.flush()
    assert influx.only()["end_ts"] == T0 + 70
    assert len(snaps.saves) == saves + 1 and _saved_bases(snaps) == []


async def test_redis_snapshot_key_expires_after_the_gap_limit() -> None:
    """사본 키는 결측 허용(600초)만 산다 — 오래 멈췄거나 되돌렸다 온 기동은 낡은 사본 대신 Influx 로 복원한다 (013 §3.3)."""
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    await bus.open_events_save("[]")
    ttl = fakeredis.FakeRedis(server=server).ttl(OPEN_EVENTS_KEY)
    assert OPEN_EVENTS_TTL_SEC == MAX_GAP_SEC and 0 < ttl <= OPEN_EVENTS_TTL_SEC
    assert await bus.open_events_load() == "[]"
