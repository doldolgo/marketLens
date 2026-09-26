"""수집기 심장박동 (스펙 025 §3.4) — 매 틱 끝에 Redis `collect:heartbeat` 에 틱 시각을 쓴다.

틱 루프는 동기라 여기서 태스크 하나를 띄우고 돌아온다. 직전 쓰기가 아직 안 끝났으면(Redis 가
느림) 이번 초는 건너뛴다 — 겹쳐 쌓이면 Redis 가 죽었을 때 태스크가 무한히 늘어난다.
쓰기 실패는 틱을 막지 않고 WARNING 1줄(10분 억제)만 남긴다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from app.core.models import Tick
from app.core.redis_bus import RedisBus

logger = logging.getLogger("marketlens.heartbeat")

WARN_SUPPRESS_MS = 600_000


class HeartbeatSink:
    def __init__(
        self, *, bus: RedisBus, clock: Callable[[], float] = time.time
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._last_warned: int | None = None

    def observe(self, tick: Tick) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._write(tick.ts * 1000), name="heartbeat")

    async def _write(self, ts_ms: int) -> None:
        try:
            await self._bus.set_heartbeat(ts_ms)
        except Exception as exc:
            now_ms = int(self._clock() * 1000)
            if (
                self._last_warned is not None
                and now_ms - self._last_warned < WARN_SUPPRESS_MS
            ):
                return
            self._last_warned = now_ms
            logger.warning("심장박동 쓰기 실패 — 다음 틱에 다시: %r", exc)

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
