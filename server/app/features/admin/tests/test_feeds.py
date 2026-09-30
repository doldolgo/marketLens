"""`/admin/aws` 피드 — 부분별 state·캐시 주기·3초 기다림·전용 스레드·로그·새지 않음 (스펙 034 §3.1·§3.2·§4).

피드를 테스트 루프에서 직접 부른다 — 뒤에서 마저 도는 갱신이 다음 요청까지 같은 루프에 살아 있어야 하기 때문이다.
시계(벽·단조)는 손으로 돌리고, 3초 기다림은 0.2초로 줄인다.
"""

import asyncio
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

from app.core.notify import SlackLogHandler
from app.features.admin import aws as aws_module
from app.features.admin.feeds import AdminFeeds
from app.features.admin.tests.aws_fakes import (
    ACCOUNT,
    ARN_TEXT,
    IDENTITY,
    IDS,
    NOW,
    REQ_A,
    Clients,
    Raising,
    alarms_response,
    budgets_response,
    canary_run,
    list_metrics_response,
    metric_data_response,
)

ACCOUNT_ID = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
QUERY_IDS = [
    f"{b}_{k}" for b in IDS for k in ("mem", "disk", "swap", "cpu", "credit")
] + [
    "ws",
    "canary_runs",
    "canary_errors",
    "canary_duration",
]


class World:
    """시계 둘과 피드 — `mono`·`wall` 을 같이 민다."""

    def __init__(
        self, clients: Any, *, region: str | None = "ap-northeast-2", wait: float = 0.2
    ) -> None:
        self.t = 0.0
        self.feeds = AdminFeeds(
            region=region,
            client=clients,
            clock=lambda: NOW + self.t,
            mono=lambda: self.t,
            wait_sec=wait,
        )

    def advance(self, sec: float) -> None:
        self.t += sec


def stub_alarms(c: Clients) -> None:
    c["cloudwatch"].add_response("describe_alarms", alarms_response())


def stub_metrics(c: Clients, *, discover: bool = True) -> None:
    if discover:
        c["cloudwatch"].add_response("describe_alarms", alarms_response())
        c["cloudwatch"].add_response("list_metrics", list_metrics_response())
    c["cloudwatch"].add_response("get_metric_data", metric_data_response(QUERY_IDS))


def stub_canary(c: Clients) -> None:
    c["logs"].add_response(
        "filter_log_events", {"events": canary_run(REQ_A, int(NOW * 1000) - 60_000)}
    )


def stub_budget(c: Clients) -> None:
    c["sts"].add_response("get_caller_identity", IDENTITY)
    c["budgets"].add_response("describe_budgets", budgets_response())


def all_ok() -> Clients:
    c = Clients("cloudwatch", "logs", "sts", "budgets")
    # 전용 스레드는 제출 순서대로 하나씩 — alarms → metrics → canary → budget
    stub_alarms(c)
    stub_metrics(c)
    stub_canary(c)
    stub_budget(c)
    return c


def denied(code: str = "AccessDenied") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": ARN_TEXT}}, "Op")


async def test_no_region_means_four_unconfigured_parts_and_no_aws_or_thread() -> None:
    made: list[str] = []
    w = World(lambda s: made.append(s), region=None)
    body = await w.feeds.aws()
    assert list(body) == ["alarms", "metrics", "canary", "budget"]
    for name, part in body.items():
        assert part["state"] == "unconfigured" and part["code"] is None, name
        assert part["fetchedAt"] is None
    assert body["alarms"] == {
        "state": "unconfigured",
        "code": None,
        "fetchedAt": None,
        "refreshSec": 60,
        "items": None,
        "counts": None,
    }
    assert [p["refreshSec"] for p in body.values()] == [60, 300, 60, 21600]
    assert made == []
    assert not [t for t in threading.enumerate() if t.name.startswith("admin-aws")]


async def test_all_ok_fills_every_part_with_values() -> None:
    c = all_ok()
    w = World(c, wait=5.0)
    body = await w.feeds.aws()
    assert {n: p["state"] for n, p in body.items()} == dict.fromkeys(body, "ok")
    assert all(
        p["code"] is None and p["fetchedAt"] == int(NOW * 1000) for p in body.values()
    )
    assert body["alarms"]["counts"] == {"ok": 3, "alarm": 1, "insufficientData": 1}
    assert len(body["metrics"]["boxes"]) == 3
    assert body["canary"]["ok"] is True
    assert body["budget"]["items"][0]["limit"] == 130.0
    for s in c.stubs.values():
        s.assert_no_pending_responses()
    # 클라이언트는 첫 요청 때 전용 스레드에서 만든다
    assert c.threads == {"admin-aws_0"}
    json.dumps(body)  # 응답 그대로 직렬화된다


async def test_one_denied_part_does_not_spill_into_the_others() -> None:
    c = Clients("cloudwatch", "logs", "sts", "budgets")
    stub_alarms(c)
    stub_metrics(c)
    stub_canary(c)
    c["sts"].add_response("get_caller_identity", IDENTITY)
    c["budgets"].add_client_error(
        "describe_budgets", "AccessDeniedException", ARN_TEXT, 403
    )
    body = await World(c, wait=5.0).feeds.aws()
    assert body["budget"] == {
        "state": "denied",
        "code": "AccessDeniedException",
        "fetchedAt": None,
        "refreshSec": 21600,
        "items": None,
    }
    assert [body[n]["state"] for n in ("alarms", "metrics", "canary")] == [
        "ok",
        "ok",
        "ok",
    ]


@pytest.mark.parametrize(
    ("exc", "state", "code"),
    [
        (NoCredentialsError(), "unconfigured", "no_credentials"),
        (denied("InvalidClientTokenId"), "unconfigured", "InvalidClientTokenId"),
        (denied("AccessDenied"), "denied", "AccessDenied"),
        (ReadTimeoutError(endpoint_url="https://monitoring"), "error", "timeout"),
        (
            RuntimeError("arn:aws:boom"),
            "error",
            "RuntimeError",
        ),  # 처리기 안 예외도 500 이 아니라 그 부분 error
    ],
)
async def test_failures_become_part_states_without_messages(
    exc: BaseException, state: str, code: str
) -> None:
    fake = Raising(exc)
    body = await World(lambda s: fake, wait=5.0).feeds.aws()
    for part in body.values():
        assert (part["state"], part["code"], part["fetchedAt"]) == (state, code, None)
    raw = json.dumps(body)
    assert "arn:aws:" not in raw and not ACCOUNT_ID.search(raw)


async def test_missing_credentials_are_looked_up_again_on_the_next_refresh() -> None:
    # 자격증명 없이 만든 클라이언트는 역할이 붙어도 계속 없다 — 버리고 다음 주기에 다시 만든다
    good = Clients("cloudwatch")
    made: list[Any] = [Raising(NoCredentialsError())]
    w = World(lambda s: made.pop(0) if made else good.clients[s], wait=5.0)
    first = await w.feeds.alerts(bus=None, slack_configured=False)
    assert first["alarms"]["code"] == "no_credentials"
    good["cloudwatch"].add_response("describe_alarm_history", {"AlarmHistoryItems": []})
    w.advance(60)
    second = await w.feeds.alerts(bus=None, slack_configured=False)
    assert second["alarms"]["state"] == "ok"


class Session:
    """boto3 세션 흉내 — 자격증명 조회(메타데이터 끝점이 답하지 않으면 한 번에 약 2초)를 센다."""

    creds: Any = None
    lookups = 0
    delay = 0.0

    def get_credentials(self) -> Any:
        type(self).lookups += 1
        time.sleep(self.delay)
        return self.creds

    def client(self, service: str, **kwargs: Any) -> Any:
        return Raising(RuntimeError("부르지 않는다"))


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> type[Session]:
    monkeypatch.setattr(Session, "creds", None)
    monkeypatch.setattr(Session, "lookups", 0)
    monkeypatch.setattr(Session, "delay", 0.0)
    monkeypatch.setattr(aws_module.boto3.session, "Session", Session)
    return Session


def test_missing_credentials_are_looked_up_once_a_minute(
    session: type[Session],
) -> None:
    t = [0.0]
    make = aws_module.client_factory("ap-northeast-2", lambda: t[0])
    for service in ("cloudwatch", "logs", "sts", "cloudwatch"):
        with pytest.raises(NoCredentialsError):
            make(service)
    assert session.lookups == 1  # 1분 안 — 부분마다 다시 찾지 않는다
    t[0] = 60.0
    session.creds = object()  # 역할이 붙었다
    assert isinstance(make("cloudwatch"), Raising)
    assert session.lookups == 2


async def test_slow_credential_lookup_still_answers_all_four_parts_in_time(
    session: type[Session],
) -> None:
    # 밖으로 나가는 망은 있고 메타데이터 끝점이 없는 호스트 — 첫 요청이 3초 안에 네 부분 모두 unconfigured
    session.delay = 0.3
    feeds = AdminFeeds(region="ap-northeast-2", wait_sec=1.0)
    try:
        body = await feeds.aws()
    finally:
        feeds.close()
    assert {(p["state"], p["code"]) for p in body.values()} == {
        ("unconfigured", "no_credentials")
    }
    assert session.lookups == 1


async def test_metrics_discovery_denied_marks_metrics_denied_even_when_alarms_are_ok() -> (
    None
):
    c = Clients("cloudwatch", "logs", "sts", "budgets")
    stub_alarms(c)
    c["cloudwatch"].add_client_error(
        "describe_alarms", "AccessDenied", ARN_TEXT, 403
    )  # metrics 의 박스 찾기
    stub_canary(c)
    stub_budget(c)
    body = await World(c, wait=5.0).feeds.aws()
    assert body["alarms"]["state"] == "ok"
    assert (body["metrics"]["state"], body["metrics"]["code"]) == (
        "denied",
        "AccessDenied",
    )
    assert body["metrics"]["boxes"] is None


async def test_parts_refresh_on_their_own_periods_and_failures_are_cached_too() -> None:
    c = Clients("cloudwatch", "logs", "sts", "budgets")
    stub_alarms(c)
    stub_metrics(c)
    c["logs"].add_client_error(
        "filter_log_events", "AccessDeniedException", ARN_TEXT, 400
    )
    stub_budget(c)
    w = World(c, wait=5.0)
    await w.feeds.aws()
    w.advance(59)
    body = await w.feeds.aws()  # 60초 안 — 호출 한 벌(스텁이 비었는데도 답한다)
    assert body["canary"]["state"] == "denied"  # 실패도 주기만큼 캐시
    w.advance(1)  # 60초 — alarms·canary 만 다시
    stub_alarms(c)
    stub_canary(c)
    body = await w.feeds.aws()
    assert body["canary"]["state"] == "ok" and body["metrics"]["fetchedAt"] == int(
        NOW * 1000
    )
    w.advance(240)  # 300초 — metrics 도(박스 찾기는 1시간 캐시)
    stub_alarms(c)
    stub_metrics(c, discover=False)
    stub_canary(c)
    body = await w.feeds.aws()
    assert body["metrics"]["fetchedAt"] == int((NOW + 300) * 1000)
    assert body["budget"]["fetchedAt"] == int(NOW * 1000)  # 6시간 캐시
    for s in c.stubs.values():
        s.assert_no_pending_responses()


class Slow:
    """부를 때마다 게이트가 열릴 때까지 매달리는 가짜 — 느리거나 막힌 AWS. 동시에 몇 개가 도는지 센다."""

    def __init__(self, result: Any) -> None:
        self.gate = threading.Event()
        self.calls = 0
        self.running = 0
        self.max_running = 0
        self.threads: set[str] = set()
        self._lock = threading.Lock()
        self._result = result

    def __getattr__(self, name: str) -> Any:
        def call(**kwargs: Any) -> Any:
            with self._lock:
                self.calls += 1
                self.running += 1
                self.max_running = max(self.max_running, self.running)
            self.threads.add(threading.current_thread().name)
            try:
                self.gate.wait(5)
                return self._result
            finally:
                with self._lock:
                    self.running -= 1

        return call


async def test_slow_refresh_answers_pending_then_the_same_refresh_fills_it() -> None:
    slow = Slow(alarms_response())
    w = World(lambda s: slow, wait=0.2)
    first, second = await asyncio.gather(
        w.feeds.aws(), w.feeds.aws()
    )  # 동시 요청 = 갱신 하나
    assert first["alarms"] == second["alarms"]
    assert first["alarms"]["state"] == "pending" and first["alarms"]["code"] is None
    assert first["alarms"]["items"] is None
    slow.gate.set()
    for _ in range(100):
        await asyncio.sleep(0.02)
        body = await w.feeds.aws()
        if body["alarms"]["state"] != "pending":
            break
    assert body["alarms"]["state"] == "ok"
    # alarms 한 번 + 나머지 셋이 한 번씩 — 뒤에서 마저 돈 갱신이 캐시를 채웠고 다시 부르지 않았다
    assert (
        slow.calls == 1 + 3 + 1 + 1
    )  # alarms · metrics(박스 찾기·지표 목록·지표) · canary · budget(sts·예산)
    assert slow.max_running == 1  # 전용 스레드 하나에서 한 번에 하나


async def test_aws_calls_never_touch_the_default_executor_or_block_the_loop() -> None:
    loop = asyncio.get_running_loop()
    # c7g.medium(1 vCPU)의 기본 실행기 — 스레드 5개. AWS 가 여기서 돌면 아래 다섯이 줄을 선다
    default = ThreadPoolExecutor(max_workers=5)
    loop.set_default_executor(default)
    slow = Slow(alarms_response())
    w = World(lambda s: slow, wait=0.1)
    try:
        body = await w.feeds.aws()  # 네 부분 모두 느린 가짜(최대 5초)에 걸린다
        assert {p["state"] for p in body.values()} == {"pending"}

        async def timed(job: Any) -> float:
            t0 = time.monotonic()
            await job
            return time.monotonic() - t0

        spent = await asyncio.gather(
            *(timed(asyncio.to_thread(time.sleep, 0.05)) for _ in range(5)),
            timed(asyncio.sleep(0.01)),
        )
        assert max(spent) < 0.5
        assert slow.running == 1 and slow.threads == {"admin-aws_0"}
    finally:
        slow.gate.set()
        w.feeds.close()


async def test_denied_budget_polled_for_ten_minutes_logs_one_warning_and_sends_no_slack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    c = Clients("cloudwatch", "logs", "sts", "budgets")
    sent: list[str] = []
    handler = SlackLogHandler(lambda k, t: sent.append(t))
    logging.getLogger().addHandler(handler)
    caplog.set_level(logging.INFO, logger="marketlens")
    fake_ok = Clients("cloudwatch", "logs")
    w = World(lambda s: fake_ok.clients.get(s) or c.clients[s], wait=5.0)
    try:
        for minute in range(10):
            stub_alarms(fake_ok)
            if minute == 0:
                stub_metrics(fake_ok)
                c["sts"].add_response("get_caller_identity", IDENTITY)
                c["budgets"].add_client_error(
                    "describe_budgets", "AccessDeniedException", ARN_TEXT, 403
                )
            if minute == 5:
                stub_metrics(fake_ok, discover=False)  # 300초 주기
            stub_canary(fake_ok)
            body = await w.feeds.aws()
            assert body["budget"]["state"] == "denied"
            w.advance(60)
    finally:
        logging.getLogger().removeHandler(handler)
    warns = [r for r in caplog.records if r.name == "marketlens.admin"]
    assert [r.getMessage() for r in warns] == [
        "관리자 피드 aws.budget 실패 — AccessDeniedException"
    ]
    assert warns[0].levelno == logging.WARNING
    assert sent == []  # ERROR 가 아니므로 Slack 으로 가지 않는다


async def test_a_part_failing_every_minute_warns_once_per_ten_minutes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = Raising(lambda: denied("AccessDenied"))
    w = World(lambda s: fake, wait=5.0)
    caplog.set_level(logging.WARNING, logger="marketlens.admin")
    for _ in range(11):  # 0~10분
        await w.feeds.aws()
        w.advance(60)
    alarms = [r for r in caplog.records if "aws.alarms" in r.getMessage()]
    assert len(alarms) == 2  # 0분·10분
    assert all(
        "arn:aws:" not in r.getMessage() and ACCOUNT not in r.getMessage()
        for r in caplog.records
    )
