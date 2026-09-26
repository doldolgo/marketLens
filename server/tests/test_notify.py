"""Slack 알림기 — 억제·큐·전송 실패·로그 핸들러 (스펙 025 §3.2·§3.3, §4)."""

import asyncio
import logging

import httpx
import pytest

from app.core.notify import QUEUE_LIMIT, SUPPRESS_MS, Notifier, SlackLogHandler

T0 = 1_700_000_000.0  # epoch 초
URL = "https://hooks.slack.com/services/T/B/x"


class Hook:
    """웹훅 흉내 — 받은 본문을 쌓고, 지정한 상태로 답한다."""

    def __init__(self, status: int = 200, raise_exc: bool = False) -> None:
        self.bodies: list[dict] = []
        self.status = status
        self.raise_exc = raise_exc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.raise_exc:
            raise httpx.ConnectError("refused")
        import json

        self.bodies.append(json.loads(request.content))
        return httpx.Response(self.status)


def make(hook: Hook, clock: list[float]) -> Notifier:
    return Notifier(
        webhook_url=URL,
        role="collector",
        client=httpx.AsyncClient(transport=httpx.MockTransport(hook)),
        clock=lambda: clock[0],
    )


async def drain(n: Notifier) -> None:
    await asyncio.wait_for(n._queue.join(), 1.0)


async def test_same_key_is_sent_once_per_600s_and_other_keys_independently() -> None:
    hook, clock = Hook(), [T0]
    n = make(hook, clock)
    n.start()
    n.notify("a", "첫 번째")
    n.notify("a", "억제됨")
    n.notify("b", "다른 키")
    clock[0] = T0 + SUPPRESS_MS / 1000 - 1
    n.notify("a", "아직 억제")
    clock[0] = T0 + SUPPRESS_MS / 1000
    n.notify("a", "다시")
    await drain(n)
    await n.aclose()
    assert [b["text"] for b in hook.bodies] == [
        "[collector] 첫 번째",
        "[collector] 다른 키",
        "[collector] 다시",
    ]
    assert all(set(b) == {"text"} for b in hook.bodies)  # 본문은 text 하나


async def test_queue_overflow_drops_new_alerts_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hook, clock = Hook(), [T0]
    n = make(hook, clock)  # start 전 — 아무도 꺼내지 않아 큐가 찬다
    caplog.set_level(logging.WARNING, logger="marketlens.notify")
    for i in range(QUEUE_LIMIT + 5):
        n.notify(f"k{i}", f"m{i}")
    assert n._queue.qsize() == QUEUE_LIMIT
    warns = [r for r in caplog.records if "가득" in r.getMessage()]
    assert len(warns) == 1  # 5번 넘쳤지만 경고는 10분에 1줄
    n.start()
    await drain(n)
    await n.aclose()
    assert len(hook.bodies) == QUEUE_LIMIT


@pytest.mark.parametrize("hook", [Hook(status=500), Hook(raise_exc=True)])
async def test_send_failure_is_dropped_and_never_raises(
    hook: Hook, caplog: pytest.LogCaptureFixture
) -> None:
    clock = [T0]
    n = make(hook, clock)
    n.start()
    caplog.set_level(logging.WARNING, logger="marketlens.notify")
    n.notify("a", "x")
    n.notify("b", "y")
    await drain(n)
    await n.aclose()
    assert n._queue.qsize() == 0  # 실패한 항목은 큐에서 빠진다
    warns = [r for r in caplog.records if r.name == "marketlens.notify"]
    assert len(warns) == 1  # 두 번 실패, 경고는 1줄(억제)


def test_log_handler_filters_and_keys_by_template() -> None:
    sent: list[tuple[str, str]] = []
    handler = SlackLogHandler(lambda k, t: sent.append((k, t)))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        lg = logging.getLogger("marketlens.ticks")
        lg.warning("경고는 안 간다")
        lg.error("틱 실패 %s", "A")
        lg.error("틱 실패 %s", "B")  # 인자만 다르다 → 같은 키
        logging.getLogger("httpx").error("라이브러리는 안 간다")
        logging.getLogger("marketlens.notify").error("자기 자신은 안 간다")
        try:
            raise ValueError("bad value")
        except ValueError:
            lg.exception("루프 예외")
    finally:
        root.removeHandler(handler)
    assert [k for k, _ in sent] == [
        "log:marketlens.ticks:틱 실패 %s",
        "log:marketlens.ticks:틱 실패 %s",
        "log:marketlens.ticks:루프 예외",
    ]
    assert sent[0][1] == "⚠️ marketlens.ticks 틱 실패 A"
    assert sent[2][1] == "⚠️ marketlens.ticks 루프 예외 — ValueError: bad value"


async def test_handler_plus_notifier_sends_same_site_once() -> None:
    # 핸들러 키 + 알림기 억제 → 매초 도는 같은 자리의 예외는 1줄
    hook, clock = Hook(), [T0]
    n = make(hook, clock)
    n.start()
    handler = SlackLogHandler(n.notify)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        for i in range(5):
            logging.getLogger("marketlens.ticks").error("틱 실패 %d", i)
    finally:
        root.removeHandler(handler)
    await drain(n)
    await n.aclose()
    assert len(hook.bodies) == 1
