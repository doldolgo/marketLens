"""Slack 알림기 — 억제·큐·전송 실패·로그 핸들러 (스펙 025 §3.2·§3.3, §4)."""

import asyncio
import logging
import re

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


# --- 보낸 알림 기록 (034 §3.3·§4) ------------------------------------------------------------


class Log:
    """기록 함수 흉내 — `RedisBus.alert_log_push` 시그니처. `fail` 이면 Redis 불달처럼 던진다."""

    def __init__(self, fail: bool = False) -> None:
        self.lines: list[dict] = []
        self.fail = fail

    async def __call__(self, line: str) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        import json

        self.lines.append(json.loads(line))


async def test_sent_alert_is_recorded_once_with_delivery_result() -> None:
    hook, clock, log = Hook(), [T0], Log()
    n = make(hook, clock)
    n.record = log
    n.start()
    n.notify("a", "수집 실패")
    clock[0] = T0 + 5  # 보내는 시각이 아니라 notify 가 불린 시각이 at 이다
    await drain(n)
    await n.aclose()
    assert log.lines == [
        {
            "at": int(T0 * 1000),
            "role": "collector",
            "key": "a",
            "text": "수집 실패",  # `[collector] ` 머리는 뺀다
            "delivered": True,
        }
    ]


@pytest.mark.parametrize("hook", [Hook(status=500), Hook(raise_exc=True)])
async def test_failed_send_is_recorded_as_not_delivered(hook: Hook) -> None:
    clock, log = [T0], Log()
    n = make(hook, clock)
    n.record = log
    n.start()
    n.notify("a", "x")
    await drain(n)
    await n.aclose()
    assert [line["delivered"] for line in log.lines] == [False]


async def test_suppressed_and_overflowed_alerts_leave_no_record() -> None:
    hook, clock, log = Hook(), [T0], Log()
    n = make(hook, clock)
    n.record = log
    n.notify("a", "첫 번째")
    n.notify("a", "억제됨")  # 같은 키 10분 안 — 보내지 않으므로 기록도 없다
    for i in range(QUEUE_LIMIT + 3):  # 큐에 한 칸이 이미 찼다 — 넘친 넷은 버려진다
        n.notify(f"k{i}", f"m{i}")
    n.start()
    await drain(n)
    await n.aclose()
    assert len(log.lines) == len(hook.bodies) == QUEUE_LIMIT
    assert "억제됨" not in {line["text"] for line in log.lines}


async def test_no_recorder_means_send_only() -> None:
    # 버스가 꽂히기 전(lifespan 전)에 보낸 알림은 기록하지 않는다
    hook, clock = Hook(), [T0]
    n = make(hook, clock)
    n.start()
    n.notify("a", "x")
    await drain(n)
    await n.aclose()
    assert len(hook.bodies) == 1


async def test_record_failure_keeps_sending_and_warns_once_off_slack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hook, clock, log = Hook(), [T0], Log(fail=True)
    n = make(hook, clock)
    n.record = log
    sent_to_slack: list[str] = []
    handler = SlackLogHandler(lambda k, t: sent_to_slack.append(k))
    root = logging.getLogger()
    root.addHandler(handler)
    caplog.set_level(logging.WARNING, logger="marketlens.notify")
    try:
        n.start()
        for key in ("a", "b", "c"):
            n.notify(key, key)
        await drain(n)
        await n.aclose()
    finally:
        root.removeHandler(handler)
    assert [b["text"] for b in hook.bodies] == [
        "[collector] a",
        "[collector] b",
        "[collector] c",
    ]
    warns = [r for r in caplog.records if "기록 실패" in r.getMessage()]
    assert len(warns) == 1 and warns[0].levelno == logging.WARNING
    assert "redis down" not in warns[0].getMessage()  # 오류 문장은 싣지 않는다
    assert sent_to_slack == []  # 순환 없음


async def stuck(line: str) -> None:
    await asyncio.Event().wait()  # 답하지 않는 Redis


async def test_record_write_is_capped_at_its_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.notify.RECORD_TIMEOUT_SEC", 0.05)
    hook, clock = Hook(), [T0]
    n = make(hook, clock)
    n.record = stuck
    n.start()
    n.notify("a", "x")
    n.notify("b", "y")
    await drain(
        n
    )  # 1초 안에 둘 다 — 큐는 기록이 끝나야 비는데, 매달린 기록은 제한에서 끊긴다
    await n.aclose()
    assert len(hook.bodies) == 2


async def test_hanging_record_does_not_delay_the_next_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # data 박스가 내려가 Redis 가 답하지 않을 때가 알림이 몰릴 때다 — 기록 대기(최대 2초)만큼 전송이 밀리면 안 된다
    monkeypatch.setattr("app.core.notify.RECORD_TIMEOUT_SEC", 1.0)
    hook, clock = Hook(), [T0]
    n = make(hook, clock)
    n.record = stuck
    n.start()
    for key in ("a", "b", "c"):
        n.notify(key, key)
    for _ in range(50):
        if len(hook.bodies) == 3:
            break
        await asyncio.sleep(0.01)
    assert len(hook.bodies) == 3  # 0.5초 안 — 어느 기록도 아직 제한(1초)에 닿지 않았다
    await drain(n)  # 기록 셋이 제한에서 끊긴 뒤 큐가 빈다
    await n.aclose()


async def test_close_drops_records_still_waiting_after_the_close_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.notify.CLOSE_WAIT_SEC", 0.1)
    hook, clock = Hook(), [T0]
    n = make(hook, clock)
    n.record = stuck  # 기록 제한은 기본 2초
    n.start()
    n.notify("a", "x")
    await asyncio.sleep(0.05)
    t0 = asyncio.get_running_loop().time()
    await n.aclose()
    assert (
        asyncio.get_running_loop().time() - t0 < 1.0
    )  # 종료 5초 상한(여기선 0.1초)에 기록도 든다
    assert not n._records and len(hook.bodies) == 1


async def test_record_key_hides_arns_and_account_ids() -> None:
    # 로그 알림의 키는 로그 템플릿 앞 80자 — 포맷된 문장·예외 객체를 넘긴 ERROR 로그면 ARN 이 키에 섞인다
    hook, clock, log = Hook(), [T0], Log()
    n = make(hook, clock)
    n.record = log
    n.start()
    handler = SlackLogHandler(n.notify)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        lg = logging.getLogger("marketlens.x")
        lg.error("실패 User: arn:aws:sts::123456789012:assumed-role/r/i")
        lg.error(RuntimeError("acct 123456789012 is not authorized"))
    finally:
        root.removeHandler(handler)
    await drain(n)
    await n.aclose()
    keys = [line["key"] for line in log.lines]
    assert keys == [
        "log:marketlens.x:실패 User: [가림]",
        "log:marketlens.x:acct [가림] is not authorized",
    ]


async def test_record_text_hides_arns_and_account_ids() -> None:
    hook, clock, log = Hook(), [T0], Log()
    n = make(hook, clock)
    n.record = log
    n.start()
    n.notify(
        "log:x",
        "⚠️ marketlens.x 실패 — ClientError: User: arn:aws:sts::123456789012:assumed-role/r/i "
        "is not authorized (account 123456789012)",
    )
    await drain(n)
    await n.aclose()
    (line,) = log.lines
    assert "arn:aws:" not in line["text"]
    assert not re.search(r"(?<![0-9])[0-9]{12}(?![0-9])", line["text"])
    assert line["text"].count("[가림]") == 2
