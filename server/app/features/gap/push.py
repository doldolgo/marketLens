"""수집 프로세스의 현선갭 표 게시 — spreads 표 다음 자리에서 매 틱 (스펙 048 §3.3).

017 §3.1 을 표 id `gap` 으로: 틱 루프가 `observe(tick)` 를 부르면(동기·무예외) 표를 만들어 큐에 넣고, 별도 태스크가
`PUBLISH gap` + `SET gap:latest EX 10` 한다 — 게시 실패가 틱을 막지 않는다. 큐는 2장 — 밀린 표는 값이 없으므로
오래된 것부터 버린다. spark 조각이 없으므로 보통 인코더(NaN 거부)로 한 번 인코딩한다.
표는 **보는 사람이 있을 때만** 만든다(048 §3.3) — api 의 gap 허브가 접속자가 있는 동안 `gap:want`(TTL 15초)를 쓰고, 여기서
0.5초마다 그 키를 읽는다. 표 1장이 약 3,400행·760KB 라 아무도 안 보는데 매초 만들면 틱이 7~10ms(로컬) 더 걸리고
collect → data 송신이 PUBLISH·SET 두 번으로 약 1.5MB/s 늘었다(2026-10-09 실측). 첫 접속자는 `waiting` 을 받은 뒤
want 가 읽히는 다음 틱(1~2초 안)에 snapshot 을 받는다.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections import deque

from app.core.live_store import LiveStore
from app.core.models import Tick
from app.core.redis_bus import GAP_CHANNEL, RedisBus
from app.features.gap.service import build_gap_table

logger = logging.getLogger("marketlens.gap_push")

# `gap:want` 를 읽는 주기(초) — 첫 접속자가 waiting 을 보는 시간의 대부분이 이 값과 다음 틱까지의 거리다
WANT_POLL_SEC = 0.5
# 한 회차 표 생성 상한 — 넘으면 틱 루프가 그만큼 멈춘 것 (017 §3.1 과 같다)
SLOW_BUILD_WARN_SEC = 0.3
# 같은 원인 경고 간격 — 009 인계 실패·017 게시 실패 로그와 같은 톤
LOG_SUPPRESS_SEC = 60.0
QUEUE_LIMIT = 2


def encode_gap_table(payload: dict[str, object]) -> str:
    """표 dict → 게시 바이트 — camelCase·공백 없음·NaN 금지 (§3.3)."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


class GapPublisher:
    def __init__(self, *, store: LiveStore, bus: RedisBus) -> None:
        self._store = store
        self._bus = bus
        self._queue: deque[str] = deque(maxlen=QUEUE_LIMIT)
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._last_warned: dict[
            str, float
        ] = {}  # 원인(예외 타입 이름) → 마지막 경고 monotonic
        # 보는 사람이 있는가 — want 읽기 태스크만 바꾼다. 기동 직후·Redis 불달이면 False(표를 만들지 않는다)
        self.wanted = False

    @property
    def pending(self) -> int:
        return len(self._queue)

    def observe(self, tick: Tick) -> None:
        """틱 직후 같은 회차 — 보는 사람이 있으면 표 1장을 만들어 큐에. 동기·무예외."""
        if not self.wanted:
            return
        try:
            started = time.perf_counter()
            self._queue.append(encode_gap_table(build_gap_table(self._store)))
            elapsed = time.perf_counter() - started
            if elapsed > SLOW_BUILD_WARN_SEC:
                logger.warning(
                    "갭표 생성 %.0fms — 상한 %.0fms 초과",
                    elapsed * 1000,
                    SLOW_BUILD_WARN_SEC * 1000,
                )
            self._wake.set()
        except Exception:
            logger.exception(
                "갭표 게시 준비 중 예외 — 이 회차는 건너뛴다 ts=%d", tick.ts
            )

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.run_sender_loop(), name="gap_publish"),
            asyncio.create_task(self.run_want_loop(), name="gap_want"),
        ]

    async def run_want_loop(self) -> None:
        """0.5초마다 `gap:want` 를 읽어 `wanted` 를 맞춘다. 실패하면 False(게시도 어차피 실패한다) — 같은 원인 60초에 1줄."""
        while True:
            await self.refresh_want()
            await asyncio.sleep(WANT_POLL_SEC)

    async def refresh_want(self) -> None:
        try:
            self.wanted = await self._bus.wanted(GAP_CHANNEL)
        except Exception as exc:
            self.wanted = False
            self._warn(f"want:{type(exc).__name__}", "gap:want 읽기 실패 — 표를 만들지 않는다: %r", exc)

    async def run_sender_loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            await self.drain()

    async def drain(self) -> int:
        """큐의 표를 순서대로 게시. 실패한 표는 버리고 같은 원인은 60초에 1줄. 보낸 수를 돌려준다."""
        sent = 0
        while self._queue:
            data = self._queue.popleft()
            try:
                await self._bus.publish_table(data, GAP_CHANNEL)
                sent += 1
            except Exception as exc:
                self._warn(type(exc).__name__, "갭표 게시 실패 — 이 회차는 건너뛴다: %r", exc)
        return sent

    def _warn(self, cause: str, msg: str, exc: Exception) -> None:
        """같은 원인 경고는 60초에 1줄."""
        now = time.monotonic()
        if now - self._last_warned.get(cause, -LOG_SUPPRESS_SEC) >= LOG_SUPPRESS_SEC:
            self._last_warned[cause] = now
            logger.warning(msg, exc)

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self._queue.clear()  # 종료 시 남은 표는 값이 없다
