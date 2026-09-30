"""Slack 알림기 (스펙 025 §3.2·§3.3) — 웹훅 1개·키별 억제·큐 1개·로그 핸들러.

설계 이유:
- `notify()` 는 동기·즉시 반환이다. 틱 루프·스트림 커넥터 같은 자리에서 부르므로 알림이 수집을
  1ms 라도 막으면 안 된다. 실제 전송은 태스크 하나가 큐에서 꺼내 순서대로 한다.
- 억제는 키 단위·메모리 — 같은 자리의 예외가 매초 나도 Slack 에는 600초에 1줄. 집계는 하지 않는다.
- 전송 실패는 버린다(재시도 없음). 알림 실패를 다시 알리면 순환이라, 실패 로그는 `marketlens.notify`
  로거에만 남기고 로그 핸들러는 이 로거를 건너뛴다.
- 웹훅 URL 이 없으면 아무것도 만들지 않는다 — 로컬·테스트 기본 상태.
- 보낸 뒤(2xx 든 실패든) 기록 함수로 JSON 한 줄을 남긴다(034 §3.3 — 보내는 규칙은 그대로). 기록 함수는 lifespan 이
  버스를 만든 뒤 꽂는다 — 그 전에 보낸 알림은 기록하지 않는다. 억제·큐 초과로 안 보낸 알림은 기록도 없다.
  기록은 보내기와 따로 도는 태스크다 — Redis 가 답하지 않으면(연결 2초) 알림마다 다음 전송이 밀리는데, data 박스가
  내려가 Redis 가 사라질 때가 바로 알림이 몰릴 때다. 큐 항목은 기록이 끝난 뒤 끝난 것으로 쳐서 종료 때 5초 대기에 든다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from app.core.redact import redact

logger = logging.getLogger("marketlens.notify")

SUPPRESS_MS = 600_000  # 같은 키는 600초에 1회 (§3.2)
QUEUE_LIMIT = 100  # 큐 상한 — 넘치면 새 알림을 버린다
SEND_TIMEOUT_SEC = 5.0
CLOSE_WAIT_SEC = 5.0  # 종료 시 남은 항목을 기다리는 상한 (§3.7)
LOG_TEXT_LIMIT = 300
LOG_EXC_LIMIT = 200
LOG_KEY_LIMIT = 80
RECORD_TIMEOUT_SEC = 2.0  # 기록 쓰기 제한 — 넘거나 실패하면 버린다 (034 §3.3)


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
        # (notify 가 불린 ms, 키, 머리 없는 문구) — 머리는 보낼 때 붙인다. 기록에는 머리 없는 문구가 들어간다
        self._queue: asyncio.Queue[tuple[int, str, str]] = asyncio.Queue(
            maxsize=QUEUE_LIMIT
        )
        self._task: asyncio.Task[None] | None = None
        # 실패 로그도 10분에 1줄 — 웹훅이 죽으면 매 알림마다 WARNING 이 찍히지 않게
        self._last_warned: dict[str, int] = {}
        # 034 — 보낸 알림 기록 함수(`RedisBus.alert_log_push`). lifespan 이 버스를 만든 뒤 꽂는다
        self.record: Callable[[str], Awaitable[None]] | None = None
        # 도는 기록 태스크 — 저마다 2초 안에 끝나므로 2초 동안 보낸 알림 수를 넘지 않는다
        self._records: set[asyncio.Task[None]] = set()

    # --- 공개 계약 ---

    def notify(self, key: str, text: str) -> None:
        """억제 통과 시 큐에 넣고 즉시 반환. 예외를 내지 않는다."""
        now_ms = int(self._clock() * 1000)
        last = self._last_sent.get(key)
        if last is not None and now_ms - last < SUPPRESS_MS:
            return
        self._last_sent[key] = now_ms
        try:
            self._queue.put_nowait((now_ms, key, text))
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
        # 남은 항목(기록 포함)은 최대 5초만 — 종료가 알림에 잡히면 안 된다
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._queue.join(), CLOSE_WAIT_SEC)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        records = list(self._records)  # 그 안에 못 끝난 기록은 버린다
        for task in records:
            task.cancel()
        await asyncio.gather(*records, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()

    # --- 내부 ---

    async def _run(self) -> None:
        while True:
            at_ms, key, text = await self._queue.get()
            recording = False
            try:
                delivered = await self._send(f"[{self._role}] {text}")
                recording = self._start_record(at_ms, key, text, delivered)
            finally:
                if not recording:
                    self._queue.task_done()

    def _start_record(self, at_ms: int, key: str, text: str, delivered: bool) -> bool:
        """기록을 따로 띄운다 — 다음 전송이 기록(최대 2초)을 기다리지 않게. 이 항목의 task_done 은 기록이 끝난 뒤."""
        if self.record is None:
            return False  # 버스가 꽂히기 전
        task = asyncio.create_task(self._record(at_ms, key, text, delivered))
        self._records.add(task)
        task.add_done_callback(self._record_done)
        return True

    def _record_done(self, task: asyncio.Task[None]) -> None:
        self._records.discard(task)
        self._queue.task_done()

    async def _send(self, text: str) -> bool:
        """Slack 이 2xx 로 받았는지 — 실패는 버리고 False (재시도 없음)."""
        assert self._client is not None
        try:
            resp = await self._client.post(self._url, json={"text": text})
        except Exception as exc:
            self._warn("send_error", "Slack 전송 실패 — 버린다: %r", exc)
            return False
        if resp.status_code // 100 != 2:
            self._warn("send_status", "Slack 응답 %d — 버린다", resp.status_code)
            return False
        return True

    async def _record(self, at_ms: int, key: str, text: str, delivered: bool) -> None:
        """034 §3.3 — 보낸 알림 한 줄. 실패·2초 초과는 버리고 이 로거에 WARNING(Slack 으로 안 간다 — 순환 없음)."""
        record = self.record
        if record is None:
            return  # 버스가 꽂히기 전
        line = json.dumps(
            {
                "at": at_ms,
                "role": self._role,
                # 로그 알림의 키는 로그 템플릿 앞 80자 — 포맷된 문장이나 예외 객체를 넘긴 로그면 ARN 이 섞인다
                "key": redact(key),
                "text": redact(text),
                "delivered": delivered,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            await asyncio.wait_for(record(line), RECORD_TIMEOUT_SEC)
        except Exception as exc:
            # 오류 문장은 싣지 않는다 — 종류만
            self._warn(
                "record_error", "알림 기록 실패 — 버린다: %s", type(exc).__name__
            )

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
