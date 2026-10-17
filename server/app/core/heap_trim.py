"""glibc 힙 반환 주기 호출 — 스펙 001 §3.1 (2026-10-17).

수집기는 매초 MB 단위 REST 본문(바이낸스 exchangeInfo 2.5MB 등 초당 ≈7.4MB)과 표 텍스트를 메인 스레드에서
만들고 버린다. glibc 는 큰 블록을 한 번 mmap 으로 내주고 돌려받으면 mmap 문턱을 그 크기로 올려 다음부터
brk 힙에서 내주고, 힙 중간의 빈칸은 꼭대기가 비지 않는 한 운영체제에 돌려주지 않는다. 그래서 살아 있는
객체는 그대로인데 RSS 만 가동 시간에 따라 오른다. `malloc_trim(0)` 은 모든 아레나의 빈 페이지를 돌려준다.

glibc 가 아니면(로컬 macOS) 함수가 없으므로 기동 때 INFO 한 줄을 남기고 아무것도 하지 않는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
from collections.abc import Callable

from app.core.config import HEAP_TRIM_INTERVAL_SEC

logger = logging.getLogger("marketlens.heap_trim")


def load_malloc_trim() -> Callable[[int], int] | None:
    """libc 의 `malloc_trim` — glibc 가 아니면 None."""
    try:
        return ctypes.CDLL("libc.so.6").malloc_trim
    except (OSError, AttributeError):
        return None


class HeapTrimmer:
    """`interval` 초마다 `trim(0)` 을 기본 실행기 스레드에서 부른다 — 루프는 그동안 다른 태스크를 돈다."""

    def __init__(
        self,
        *,
        trim: Callable[[int], int] | None,
        interval: float = HEAP_TRIM_INTERVAL_SEC,
    ) -> None:
        self._trim = trim
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._trim is None:
            logger.info("malloc_trim 없음(glibc 아님) — 힙 반환 주기 호출을 끈다")
            return
        self._task = asyncio.create_task(self._run(), name="heap_trim")

    async def _run(self) -> None:
        assert self._trim is not None
        while True:
            await asyncio.sleep(self._interval)
            try:
                await asyncio.to_thread(self._trim, 0)
            except Exception as exc:
                # 반환 실패는 수집에 영향이 없다 — 다음 회차에 다시
                logger.warning("malloc_trim 실패 — 다음 회차에 다시: %r", exc)

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
