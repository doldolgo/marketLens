"""glibc 힙 반환 주기 호출 — 스펙 001 §3.1·§4 (2026-10-17)."""

import asyncio
import sys

import pytest

from app.core.config import HEAP_TRIM_INTERVAL_SEC
from app.core.heap_trim import HeapTrimmer, load_malloc_trim


def test_interval_is_sixty_seconds() -> None:
    assert HEAP_TRIM_INTERVAL_SEC == 60.0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc 전용")
def test_loads_malloc_trim_on_glibc() -> None:
    trim = load_malloc_trim()
    assert trim is not None
    assert trim(0) in (0, 1)  # 돌려준 페이지가 있으면 1


async def test_calls_trim_zero_every_interval() -> None:
    calls: list[int] = []
    trimmer = HeapTrimmer(trim=lambda pad: calls.append(pad) or 0, interval=0.01)
    trimmer.start()
    await asyncio.sleep(0.08)
    await trimmer.aclose()
    assert len(calls) >= 3
    assert set(calls) == {0}
    seen = len(calls)
    await asyncio.sleep(0.03)
    assert len(calls) == seen  # 닫은 뒤엔 부르지 않는다


async def test_failure_is_logged_and_next_round_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    def trim(pad: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("boom")
        return 0

    trimmer = HeapTrimmer(trim=trim, interval=0.01)
    trimmer.start()
    await asyncio.sleep(0.06)
    await trimmer.aclose()
    assert calls >= 2
    assert any("malloc_trim 실패" in r.getMessage() for r in caplog.records)


async def test_without_malloc_trim_starts_nothing() -> None:
    trimmer = HeapTrimmer(trim=None, interval=0.01)
    trimmer.start()
    assert trimmer._task is None
    await trimmer.aclose()


def test_collector_lifespan_starts_and_closes_trimmer() -> None:
    from pathlib import Path

    main = Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text()
    collector = main.split("async def _lifespan(", 1)[1].split("\nasync def ", 1)[0]
    assert "HeapTrimmer(trim=load_malloc_trim())" in collector
    assert "await heap_trim.aclose()" in collector
    api = main.split("async def _api_lifespan(", 1)[1].split("\nasync def ", 1)[0]
    assert "HeapTrimmer" not in api  # 수집기 역할만 (001 §3.1)
