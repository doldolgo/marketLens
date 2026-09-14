"""수집 프로세스의 표 게시 — 틱 직후 $1,000 표를 만들어 Redis 로 (스펙 017 §3.1).

틱 루프가 013·014 다음 자리에서 `observe(tick)` 를 부른다(동기·무예외). 표는 `GET /spreads` 와 같은
함수로 만들어 같은 JSON 을 큐에 넣고, 별도 태스크가 PUBLISH + SET 한다 — 009 의 인계기와 같은 모양이라
게시 실패가 틱을 막지 않는다. "원함"(`spreads:want`) 은 5초마다 Redis 에서 읽어 메모리에 들고,
틱 안에서는 그 메모리 값만 본다 — 아무도 안 보면 표를 안 만들어 수집 프로세스의 일이 오늘과 같다.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections import deque

from app.core.live_store import LiveStore
from app.core.models import Tick
from app.core.redis_bus import RedisBus
from app.core.serialization import camelize_json
from app.features.spreads.service import (
    DEFAULT_NOTIONAL,
    MarketDataNotFoundError,
    SpreadsResponse,
    build_spreads,
)

logger = logging.getLogger("marketlens.spreads_push")

WANT_POLL_SEC = 5.0
# 한 회차 표 생성 상한 — 넘으면 틱 루프가 그만큼 멈춘 것 (§3.1)
SLOW_BUILD_WARN_SEC = 0.3
# 같은 원인 경고 간격 — 009 인계 실패 로그와 같은 톤
LOG_SUPPRESS_SEC = 60.0
# 표는 매초 새로 나오므로 밀린 표는 값이 없다 — 오래된 것부터 버린다 (§3.1)
QUEUE_LIMIT = 2


def encode_table(payload: SpreadsResponse) -> str:
    """`GET /spreads` 응답(JSONResponse)과 같은 바이트 — camelCase·공백 없음·NaN 금지."""
    return json.dumps(
        camelize_json(payload.model_dump()),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


class LogSuppressor:
    """같은 원인의 경고를 60초에 1줄로 — 원인 = 호출자가 주는 문자열(예: 예외 타입 이름)."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}

    def allow(self, cause: str) -> bool:
        now = time.monotonic()
        if now - self._last.get(cause, -LOG_SUPPRESS_SEC) < LOG_SUPPRESS_SEC:
            return False
        self._last[cause] = now
        return True


class SpreadsPublisher:
    def __init__(self, *, store: LiveStore, bus: RedisBus) -> None:
        self._store = store
        self._bus = bus
        self._wanted = False
        self._queue: deque[str] = deque(maxlen=QUEUE_LIMIT)
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._suppress = LogSuppressor()

    @property
    def wanted(self) -> bool:
        return self._wanted

    def set_wanted(self, wanted: bool) -> None:
        """want 루프(·테스트)가 쓴다 — 틱은 이 메모리 값만 본다."""
        self._wanted = wanted

    @property
    def pending(self) -> int:
        return len(self._queue)

    def observe(self, tick: Tick) -> None:
        """틱 직후 같은 회차 — 표 1장을 만들어 큐에. 동기·무예외."""
        if not self._wanted:
            return
        try:
            started = time.perf_counter()
            try:
                payload = build_spreads(self._store, notional=DEFAULT_NOTIONAL)
            except MarketDataNotFoundError:
                return  # 재료가 없는 초 — GET /spreads 가 404 인 상황과 같다, 게시할 표가 없다
            self._queue.append(encode_table(payload))
            elapsed = time.perf_counter() - started
            if elapsed > SLOW_BUILD_WARN_SEC:
                logger.warning(
                    "표 생성 %.0fms — 상한 %.0fms 초과",
                    elapsed * 1000,
                    SLOW_BUILD_WARN_SEC * 1000,
                )
            self._wake.set()
        except Exception:
            logger.exception("표 게시 준비 중 예외 — 이 회차는 건너뛴다 ts=%d", tick.ts)

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.run_sender_loop(), name="spreads_publish"),
            asyncio.create_task(self.run_want_loop(), name="spreads_want"),
        ]

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
                await self._bus.publish_table(data)
                sent += 1
            except Exception as exc:
                if self._suppress.allow(type(exc).__name__):
                    logger.warning("표 게시 실패 — 이 회차는 건너뛴다: %r", exc)
        return sent

    async def run_want_loop(self) -> None:
        """5초마다 `spreads:want` 를 읽어 메모리의 원함 값을 바꾼다 (§3.1)."""
        while True:
            await self.refresh_wanted()
            await asyncio.sleep(WANT_POLL_SEC)

    async def refresh_wanted(self) -> None:
        try:
            self._wanted = await self._bus.wanted()
        except Exception as exc:
            # 못 읽으면 직전 값 유지 — 접속자가 사라졌다면 다음 성공한 읽기에서 멈춘다
            if self._suppress.allow(type(exc).__name__):
                logger.warning("spreads:want 읽기 실패 — 직전 값 유지: %r", exc)

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self._queue.clear()  # 종료 시 남은 표는 값이 없다 — 틱과 달리 보내 보지 않는다
