"""수집 프로세스의 표 게시 — 틱 직후 $1,000 표를 만들어 Redis 로 (스펙 017 §3.1).

틱 루프가 013·014 다음 자리에서 `observe(tick)` 를 부른다(동기·무예외). 표는 `GET /spreads` 와 같은
함수로 만들어 같은 JSON 을 큐에 넣고, 별도 태스크가 PUBLISH + SET 한다 — 009 의 인계기와 같은 모양이라
게시 실패가 틱을 막지 않는다. 표는 접속자 유무와 무관하게 매 틱 만든다(2026-09-26 결정) — 첫 접속자가
`spreads:latest` 로 즉시 snapshot 을 받게 하기 위해서다. `spreads:want` 는 더 이상 읽지 않는다.
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
from app.features.spreads.service import (
    DEFAULT_NOTIONAL,
    MarketDataNotFoundError,
    build_table,
)

logger = logging.getLogger("marketlens.spreads_push")

# 한 회차 표 생성 상한 — 넘으면 틱 루프가 그만큼 멈춘 것 (§3.1)
SLOW_BUILD_WARN_SEC = 0.3
# 같은 원인 경고 간격 — 009 인계 실패 로그와 같은 톤
LOG_SUPPRESS_SEC = 60.0
# 표는 매초 새로 나오므로 밀린 표는 값이 없다 — 오래된 것부터 버린다 (§3.1)
QUEUE_LIMIT = 2


def encode_table(payload: dict[str, object]) -> str:
    """`build_table` 의 dict 를 표 바이트로 — camelCase·공백 없음·NaN 금지 (018 `GET /spreads` 가 그대로 답한다)."""
    return json.dumps(
        payload,
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
        self._queue: deque[str] = deque(maxlen=QUEUE_LIMIT)
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._suppress = LogSuppressor()

    @property
    def pending(self) -> int:
        return len(self._queue)

    def observe(self, tick: Tick) -> None:
        """틱 직후 같은 회차 — 표 1장을 만들어 큐에. 동기·무예외."""
        try:
            started = time.perf_counter()
            try:
                payload = build_table(self._store, notional=DEFAULT_NOTIONAL)
            except MarketDataNotFoundError:
                # 환율 없음·국내/해외 스냅샷 없음 — 이 회차는 표를 만들지 않는다, 경고 없음(기동 직후 정상) (018 §3.2)
                return
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

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self._queue.clear()  # 종료 시 남은 표는 값이 없다 — 틱과 달리 보내 보지 않는다
