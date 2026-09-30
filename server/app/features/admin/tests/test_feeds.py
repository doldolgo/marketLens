"""`/admin/aws` 피드 — 부분별 state·캐시 주기·3초 기다림·전용 스레드·로그·새지 않음 (스펙 034 §3.1·§3.2·§4).

피드를 테스트 루프에서 직접 부른다 — 뒤에서 마저 도는 갱신이 다음 요청까지 같은 루프에 살아 있어야 하기 때문이다.
시계(벽·단조)는 손으로 돌리고, 3초 기다림은 0.2초로 줄인다.
"""

import json
import re
import threading
from typing import Any

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

from app.features.admin.feeds import AdminFeeds
from app.features.admin.tests.aws_fakes import (
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
