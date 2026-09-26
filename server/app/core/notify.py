"""Slack 알림기 (스펙 025 §3.2·§3.3) — 웹훅 1개·키별 억제·큐 1개·로그 핸들러.

설계 이유:
- `notify()` 는 동기·즉시 반환이다. 틱 루프·스트림 커넥터 같은 자리에서 부르므로 알림이 수집을
  1ms 라도 막으면 안 된다. 실제 전송은 태스크 하나가 큐에서 꺼내 순서대로 한다.
- 억제는 키 단위·메모리 — 같은 자리의 예외가 매초 나도 Slack 에는 600초에 1줄. 집계는 하지 않는다.
- 전송 실패는 버린다(재시도 없음). 알림 실패를 다시 알리면 순환이라, 실패 로그는 `marketlens.notify`
  로거에만 남기고 로그 핸들러는 이 로거를 건너뛴다.
- 웹훅 URL 이 없으면 아무것도 만들지 않는다 — 로컬·테스트 기본 상태.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger("marketlens.notify")

SUPPRESS_MS = 600_000  # 같은 키는 600초에 1회 (§3.2)
QUEUE_LIMIT = 100  # 큐 상한 — 넘치면 새 알림을 버린다
SEND_TIMEOUT_SEC = 5.0
CLOSE_WAIT_SEC = 5.0  # 종료 시 남은 항목을 기다리는 상한 (§3.7)
LOG_TEXT_LIMIT = 300
LOG_EXC_LIMIT = 200
LOG_KEY_LIMIT = 80


class Notifier:
    """웹훅 하나로 보내는 알림기. `notify(key, text)` 만 공개 계약이다 (§3.2)."""

    def __init__(
        self,
        *,
        webhook_url: str,
        role: str,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._url = webhook_url
        self._role = role
        self._client = client
        self._clock = clock
        self._last_sent: dict[str, int] = {}  # 키 → 마지막 전송 ms (억제용)
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._task: asyncio.Task[None] | None = None
        # 실패 로그도 10분에 1줄 — 웹훅이 죽으면 매 알림마다 WARNING 이 찍히지 않게
        self._last_warned: dict[str, int] = {}

    # --- 공개 계약 ---

    def notify(self, key: str, text: str) -> None:
        """억제 통과 시 큐에 넣고 즉시 반환. 예외를 내지 않는다."""
        now_ms = int(self._clock() * 1000)
        last = self._last_sent.get(key)
        if last is not None and now_ms - last < SUPPRESS_MS:
            return
        self._last_sent[key] = now_ms
        try:
            self._queue.put_nowait(f"[{self._role}] {text}")
        except asyncio.QueueFull:
            self._warn("queue_full", "알림 큐가 가득 차 버렸다 (상한 %d)", QUEUE_LIMIT)

    # --- 수명 ---

    def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=SEND_TIMEOUT_SEC)
        self._task = asyncio.create_task(self._run(), name="notify_sender")

    async def aclose(self) -> None:
        if self._task is None:
            return
        # 남은 항목은 최대 5초만 — 종료가 알림에 잡히면 안 된다
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._queue.join(), CLOSE_WAIT_SEC)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        if self._client is not None:
            await self._client.aclose()

    # --- 내부 ---

    async def _run(self) -> None:
        while True:
            text = await self._queue.get()
            try:
                await self._send(text)
            finally:
                self._queue.task_done()

    async def _send(self, text: str) -> None:
        assert self._client is not None
        try:
            resp = await self._client.post(self._url, json={"text": text})
        except Exception as exc:
            self._warn("send_error", "Slack 전송 실패 — 버린다: %r", exc)
            return
        if resp.status_code // 100 != 2:
            self._warn("send_status", "Slack 응답 %d — 버린다", resp.status_code)

    def _warn(self, key: str, msg: str, *args: object) -> None:
        now_ms = int(self._clock() * 1000)
        last = self._last_warned.get(key)
        if last is not None and now_ms - last < SUPPRESS_MS:
            return
        self._last_warned[key] = now_ms
        logger.warning(msg, *args)


class SlackLogHandler(logging.Handler):
    """ERROR 이상·`marketlens.*` 로거만 Slack 으로 (§3.3). 키는 포맷 전 템플릿이라 같은 코드 자리는 한 키다."""

    def __init__(self, notify: Callable[[str, str], None]) -> None:
        super().__init__(level=logging.ERROR)
        self._notify = notify

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("marketlens."):
            return
        if record.name == logger.name:
            return  # 알림 실패를 다시 알리는 순환 방지
        try:
            template = str(record.msg)[:LOG_KEY_LIMIT]
            key = f"log:{record.name}:{template}"
            text = f"⚠️ {record.name} {record.getMessage()[:LOG_TEXT_LIMIT]}"
            if record.exc_info is not None and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                text += f" — {type(exc).__name__}: {str(exc)[:LOG_EXC_LIMIT]}"
            self._notify(key, text)
        except Exception:
            # 로깅 경로에서 예외를 내면 원래 로그까지 잃는다 — 핸들러 표준대로 삼킨다
            self.handleError(record)
