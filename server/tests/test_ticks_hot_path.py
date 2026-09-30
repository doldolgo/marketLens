"""틱 경로 — TickRow 모양과 단계별 소요 로그 (스펙 001 §3.6, 2026-09-28 성능 개선).

틱 값·행 순서가 바꾸기 전 규칙과 비트까지 같은지는 실데이터 시험(test_real_tables.py)이 본다.
"""

import asyncio
import itertools
import logging
import re

import pytest

from app.core.models import Tick, TickRow
from app.core.ticks import STAGE_REPORT_SEC, TickLoop
from tests.test_ticks import T0, _client, seeded


class Sink:
    def __init__(self) -> None:
        self.seen = 0

    def observe(self, tick: Tick) -> None:
        self.seen += 1


def _summaries(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "marketlens.ticks" and "단계별 ms" in r.getMessage()
    ]


def _has_stage(line: str, stage: str) -> bool:
    return re.search(rf"(: |· ){stage} -?[\d.]+/-?[\d.]+/-?[\d.]+", line) is not None


def test_tick_row_is_an_immutable_tuple_with_the_same_fields_and_defaults() -> None:
    row = TickRow("upbit", "binance", "BTC", 1.0, -1.0)
    assert TickRow._fields == (
        "dom", "fx", "base", "fwd", "rev", "dom_price", "fx_price", "rate",
        "dom_dep", "dom_wd", "fx_dep", "fx_wd", "net_dom", "net_fx",
    )  # fmt: skip
    assert (row.dom_price, row.fx_price, row.rate) == (0.0, 0.0, 0.0)
    assert (row.dom_dep, row.dom_wd, row.fx_dep, row.fx_wd) == (None, None, None, None)
    assert (row.net_dom, row.net_fx) == (None, None)
    assert row == TickRow(dom="upbit", fx="binance", base="BTC", fwd=1.0, rev=-1.0)
    with pytest.raises(AttributeError):
        row.fwd = 2.0  # type: ignore[misc]


def test_stage_times_are_logged_once_per_60_seconds_without_changing_ticks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="marketlens.ticks")
    handed: list[Tick] = []
    events, candles, spreads = Sink(), Sink(), Sink()
    loop = TickLoop(
        store=seeded(),
        streams=[],
        client=_client(),
        handoff=handed.append,
        events=events,
        candles=candles,
        spreads=spreads,
    )
    ticks = [loop.tick(T0 + i) for i in range(STAGE_REPORT_SEC)]
    assert _summaries(caplog) == []
    ticks.append(loop.tick(T0 + STAGE_REPORT_SEC))
    [line] = _summaries(caplog)
    assert f"틱 {STAGE_REPORT_SEC + 1}번" in line
    for stage in ("틱", "인계", "판정", "사건", "봉", "표", "합계"):
        assert _has_stage(line, stage), stage
    # 붙지 않은 단계는 재지 않는다
    for stage in ("입출금", "기준가", "심장박동", "깨어남"):
        assert not _has_stage(line, stage), stage
    # 로그만 더했다 — 틱·인계·관측 횟수는 그대로
    assert handed == ticks[:-1]
    assert events.seen == candles.seen == spreads.seen == STAGE_REPORT_SEC + 1
    for i in range(1, STAGE_REPORT_SEC + 1):
        loop.tick(T0 + STAGE_REPORT_SEC + i)
    assert len(_summaries(caplog)) == 2


async def test_run_records_how_late_it_woke_after_the_second_boundary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="marketlens.ticks")
    times = (T0 + 0.5 + i for i in itertools.count())
    stop = asyncio.Event()
    slept = 0

    async def sleep(seconds: float) -> None:
        nonlocal slept
        slept += 1
        if slept > STAGE_REPORT_SEC + 1:
            stop.set()
            await asyncio.Event().wait()

    loop = TickLoop(
        store=seeded(),
        streams=[],
        client=_client(),
        clock=lambda: next(times),
        sleep=sleep,
    )
    loop.start()
    await asyncio.wait_for(stop.wait(), 5.0)
    await loop.aclose()
    [line] = _summaries(caplog)
    assert _has_stage(line, "깨어남") and _has_stage(line, "합계")
