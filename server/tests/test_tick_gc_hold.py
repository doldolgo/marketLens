"""틱 회차 동안 자동 GC 보류와 세대별 GC 수집 로그 (스펙 001 §3.6, 2026-10-09).

보류는 TickLoop.run 이 tick() 둘레에서 한다 — tick() 을 직접 부르는 테스트에는 영향이 없다. 세대 임계·얼리기(001 §3.1)는
그대로이고, 틱 끝 + GC_HOLD_SEC 에 다시 켜진다. 예외로 끝난 틱·이어지는 틱·aclose 어느 경우에도 GC 가 꺼진 채 남지 않는다.
"""

import asyncio
import gc
import itertools
import logging
import time

import pytest

import app.core.ticks as ticks_mod
from app.core.config import GC_THRESHOLDS
from app.core.models import Tick
from app.core.ticks import (
    GC_HOLD_CARRY_MAX,
    GC_HOLD_SEC,
    STAGE_REPORT_SEC,
    TickLoop,
    _GcStats,
)
from tests.test_ticks import T0, _client, seeded
from tests.test_ticks_hot_path import _has_stage, _summaries


class GcProbe:
    """틱 안(표 게시 자리)에서 GC 가 켜져 있는지·세대 임계를 적는 가짜 관측자."""

    def __init__(self, *, fail: bool = False, collect: int | None = None) -> None:
        self.enabled: list[bool] = []
        self.thresholds: list[tuple[int, int, int]] = []
        self.fail = fail
        self.collect = collect

    def observe(self, tick: Tick) -> None:
        self.enabled.append(gc.isenabled())
        self.thresholds.append(gc.get_threshold())
        if self.collect is not None:
            gc.collect(self.collect)
        if self.fail:
            raise RuntimeError("관측자 버그")


async def _wait_enabled(limit: float = 3.0) -> bool:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if gc.isenabled():
            return True
        await asyncio.sleep(0.02)
    return gc.isenabled()


def _driver(ticks: int, yields: int = 1):  # noqa: ANN202
    """run() 에 넣는 가짜 시계·sleep — ticks 번 틱을 돌린 뒤 멈추고, 각 틱 직후(첫 await 전) GC 상태를 적는다.

    sleep 은 곧바로 돌아온다(이벤트 루프를 yields 번 돌린다) — 틱이 초 경계마다 실제로 1초씩 기다리지 않는다.
    """
    times = (T0 + 0.5 + i for i in itertools.count())
    after_tick: list[bool] = []
    done = asyncio.Event()
    slept = 0

    async def sleep(seconds: float) -> None:
        nonlocal slept
        slept += 1
        if slept > 1:
            # run() 이 tick() 을 끝내고 다음 sleep 을 부른 자리 — 이벤트 루프가 한 번도 돌기 전이다
            after_tick.append(gc.isenabled())
        if slept > ticks:
            done.set()
            await asyncio.Event().wait()
        for _ in range(yields):
            await asyncio.sleep(0)

    return (lambda: next(times)), sleep, after_tick, done


async def test_run_holds_gc_during_the_tick_and_turns_it_back_on_after_the_hold() -> None:
    gc.enable()
    gc.set_threshold(*GC_THRESHOLDS)
    probe = GcProbe()
    clock, sleep, after_tick, done = _driver(1)
    loop = TickLoop(store=seeded(), streams=[], client=_client(), spreads=probe, clock=clock, sleep=sleep)
    loop.start()
    await asyncio.wait_for(done.wait(), 2.0)
    assert probe.enabled == [False]  # 틱 안에서는 자동 수집이 꺼져 있다
    assert probe.thresholds == [GC_THRESHOLDS]  # 세대 임계는 그대로(001 §3.1 — test_gc_settle 단언이 보류 창에도 맞는다)
    assert after_tick == [False]  # 틱 직후에도 아직 보류 창 안이다
    assert await _wait_enabled()  # 틱 끝 + GC_HOLD_SEC 에 다시 켜진다
    await loop.aclose()
    assert gc.isenabled()


async def test_next_tick_before_the_resume_keeps_holding_and_never_leaves_gc_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 다시 켜기 예약이 돌기 전에 다음 틱이 오는 경우 — sleep 이 곧바로 돌아오고 보류 창을 길게 잡아 매번 이어진다
    monkeypatch.setattr(ticks_mod, "GC_HOLD_SEC", 30.0)
    gc.enable()
    probe = GcProbe()
    clock, sleep, after_tick, done = _driver(GC_HOLD_CARRY_MAX + 3)
    loop = TickLoop(store=seeded(), streams=[], client=_client(), spreads=probe, clock=clock, sleep=sleep)
    loop.start()
    await asyncio.wait_for(done.wait(), 2.0)
    # 이어서 보류하다가 상한(GC_HOLD_CARRY_MAX)을 넘은 틱 하나는 보류하지 않고 켠 채로 돈다 — 그 뒤 다시 보류
    assert probe.enabled == [False] * (GC_HOLD_CARRY_MAX + 1) + [True, False]
    assert not gc.isenabled()  # 마지막 틱의 다시 켜기 예약이 걸려 있다
    await loop.aclose()
    assert gc.isenabled()  # aclose 가 예약을 거두고 바로 켠다(30초를 기다리지 않는다)


async def test_tick_that_raises_still_turns_gc_back_on() -> None:
    gc.enable()
    probe = GcProbe(fail=True)
    clock, sleep, after_tick, done = _driver(2)
    loop = TickLoop(store=seeded(), streams=[], client=_client(), spreads=probe, clock=clock, sleep=sleep)
    loop.start()
    await asyncio.wait_for(done.wait(), 2.0)
    assert probe.enabled == [False, False]  # 예외로 끝난 틱 다음 틱도 같은 규칙
    assert await _wait_enabled()
    await loop.aclose()


async def test_gc_turned_off_elsewhere_is_left_alone() -> None:
    gc.disable()
    probe = GcProbe()
    clock, sleep, after_tick, done = _driver(2)
    loop = TickLoop(store=seeded(), streams=[], client=_client(), spreads=probe, clock=clock, sleep=sleep)
    loop.start()
    await asyncio.wait_for(done.wait(), 2.0)
    await asyncio.sleep(GC_HOLD_SEC + 0.1)
    assert probe.enabled == [False, False]
    assert not gc.isenabled()  # 이 루프가 끈 것이 아니라 켜지 않는다
    await loop.aclose()
    assert not gc.isenabled()


def test_direct_tick_calls_do_not_touch_gc() -> None:
    gc.enable()
    probe = GcProbe()
    loop = TickLoop(store=seeded(), streams=[], client=_client(), spreads=probe)
    loop.tick(T0)
    loop.tick(T0 + 1)
    assert probe.enabled == [True, True] and gc.isenabled()


async def test_run_registers_gc_callback_only_while_running_and_logs_gc_per_generation(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="marketlens.ticks")
    # 다시 켜기를 곧바로(다음 루프 회전) 돌려 틱마다 보류가 끝나게 한다 — 보류가 이어지다 상한을 넘어 켠 채 도는
    # 틱이 끼면 틱 안 자동 수집이 생겨 아래 단언이 우연에 기댄다
    monkeypatch.setattr(ticks_mod, "GC_HOLD_SEC", 0.0)
    gc.enable()
    # 틱마다 관측자가 1세대 수집을 한 번 부른다 — 틱 안 1세대 횟수가 틱 수와 같아야 한다
    probe = GcProbe(collect=1)
    clock, sleep, after_tick, done = _driver(STAGE_REPORT_SEC + 1, yields=2)
    loop = TickLoop(store=seeded(), streams=[], client=_client(), spreads=probe, clock=clock, sleep=sleep)
    callback = loop._gc_callback
    assert callback not in gc.callbacks
    loop.start()
    await asyncio.wait_for(done.wait(), 5.0)
    assert callback in gc.callbacks
    [line] = _summaries(caplog)
    # 단계 칸은 그대로 읽힌다
    assert _has_stage(line, "깨어남") and _has_stage(line, "합계") and _has_stage(line, "표")
    gc_part = line.split("GC 틱 안/밖: ", 1)[1]
    gen0, gen1, gen2, last = gc_part.split(" · ")
    # 틱 안에서는 자동 수집이 꺼져 있어 0세대·2세대 수집이 없다. 1세대는 관측자가 부른 61번이 전부 틱 안
    assert gen0.startswith("0세대 0/")
    assert gen1.startswith(f"1세대 {STAGE_REPORT_SEC + 1}/")
    assert gen2.startswith("2세대 0/")
    assert last == "2세대 수집 아직 없음" or last.startswith("마지막 2세대 수집 뒤 ")
    assert all(not on for on in probe.enabled)  # 61틱 모두 보류 안에서 돌았다
    await loop.aclose()
    assert callback not in gc.callbacks


def test_gc_part_is_only_added_while_run_is_registered(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="marketlens.ticks")
    loop = TickLoop(store=seeded(), streams=[], client=_client())
    for i in range(STAGE_REPORT_SEC + 1):
        loop.tick(T0 + i)
    [line] = _summaries(caplog)
    assert "GC" not in line


class _Seq:
    """정해 둔 값을 차례로 내는 가짜 시계 — 다 쓰면 마지막 값을 계속 낸다(다른 호출자가 끼어도 깨지지 않게)."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_gc_stats_counts_generations_inside_and_outside_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    stats = _GcStats()
    monkeypatch.setattr(
        ticks_mod.time, "perf_counter", _Seq(10.000, 10.002, 20.000, 20.005, 30.000, 30.010, 40.000, 40.0015)
    )
    monkeypatch.setattr(ticks_mod.time, "monotonic", _Seq(100.0, 104.4, 200.0))
    stats.in_tick = True
    stats.callback("start", {"generation": 0, "collected": 0, "uncollectable": 0})
    stats.callback("stop", {"generation": 0, "collected": 3, "uncollectable": 0})
    stats.in_tick = False
    stats.callback("start", {"generation": 1, "collected": 0, "uncollectable": 0})
    stats.callback("stop", {"generation": 1, "collected": 0, "uncollectable": 0})
    stats.callback("start", {"generation": 2, "collected": 0, "uncollectable": 0})
    stats.callback("stop", {"generation": 2, "collected": 7, "uncollectable": 0})  # monotonic 100.0
    stats.callback("start", {"generation": 0, "collected": 0, "uncollectable": 0})
    stats.callback("stop", {"generation": 0, "collected": 1, "uncollectable": 0})
    assert stats.summary() == (  # monotonic 104.4
        "GC 틱 안/밖: 0세대 1/1번 2.0/1.5ms 거둔 4개 · 1세대 0/1번 0.0/5.0ms 거둔 0개 · "
        "2세대 0/1번 0.0/10.0ms 거둔 7개 · 마지막 2세대 수집 뒤 4초"
    )
    # 요약을 내면 창을 비운다 — 마지막 2세대 시각만 이어진다
    assert stats.summary() == (  # monotonic 200.0
        "GC 틱 안/밖: 0세대 0/0번 0.0/0.0ms 거둔 0개 · 1세대 0/0번 0.0/0.0ms 거둔 0개 · "
        "2세대 0/0번 0.0/0.0ms 거둔 0개 · 마지막 2세대 수집 뒤 100초"
    )
