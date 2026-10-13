"""`/admin/aws/series` 피드 — 창 고르기·창마다 캐시 주기·상태·전용 스레드·경고·`/admin/aws` 그대로 (스펙 063 §3.1·§3.3·§4).

피드를 테스트 루프에서 직접 부른다(test_feeds.py 와 같은 방식) — 시계(벽·단조)는 손으로 돌린다.
"""

import asyncio
import json
import logging
import re
import threading
from collections.abc import Callable
from typing import Any

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

from app.features.admin.feeds import AdminFeeds
from app.features.admin.tests.aws_fakes import (
    ARN_TEXT,
    IDS,
    NOW,
    Clients,
    Raising,
    alarms_response,
    bounds,
    capture_metric_data,
    list_metrics_response,
    metric_data_response,
    series_data_response,
    series_ids,
    stub_discovery,
)

ACCOUNT_ID = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
KEYS = [
    "state",
    "code",
    "fetchedAt",
    "refreshSec",
    "range",
    "periodSec",
    "startTs",
    "endTs",
    "boxes",
    "wsClients",
    "canary",
]
QUERY_IDS_034 = [
    f"{b}_{k}" for b in IDS for k in ("mem", "disk", "swap", "cpu", "credit")
] + ["ws", "canary_runs", "canary_errors", "canary_duration"]


class World:
    """시계 둘과 피드 — `mono`·`wall` 을 같이 민다."""

    def __init__(
        self,
        clients: Callable[[str], Any],
        *,
        region: str | None = "ap-northeast-2",
        wait: float = 5.0,
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


def denied(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": ARN_TEXT}}, "Op")


def admin_threads() -> set[threading.Thread]:
    return {t for t in threading.enumerate() if t.name.startswith("admin-aws")}


@pytest.mark.parametrize(
    ("asked", "name", "refresh"),
    [
        (None, "24h", 300),
        ("6h", "6h", 300),
        ("24h", "24h", 300),
        ("7d", "7d", 1_800),
        ("30d", "30d", 3_600),
        ("90d", "24h", 300),
        ("7D", "24h", 300),
        ("", "24h", 300),
    ],
)
async def test_no_region_answers_unconfigured_with_the_chosen_range_and_calls_nothing(
    asked: str | None, name: str, refresh: int
) -> None:
    made: list[str] = []
    before = admin_threads()  # 앞선 테스트의 피드가 남긴 스레드는 빼고 센다
    body = await World(lambda s: made.append(s), region=None).feeds.aws_series(asked)
    assert list(body) == KEYS
    assert body == {
        **dict.fromkeys(KEYS),
        "state": "unconfigured",
        "refreshSec": refresh,
        "range": name,
    }
    assert made == [] and admin_threads() - before == set()


@pytest.mark.parametrize(
    ("name", "window", "period", "refresh", "points"),
    [
        ("6h", 21_600, 300, 300, 72),
        ("24h", 86_400, 300, 300, 288),
        ("7d", 604_800, 3_600, 1_800, 168),
        ("30d", 2_592_000, 10_800, 3_600, 240),
    ],
)
async def test_each_range_reads_its_grid_and_keeps_it_for_its_refresh_period(
    name: str, window: int, period: int, refresh: int, points: int
) -> None:
    c = Clients("cloudwatch")
    start, end = bounds(window, period)
    stub_discovery(c)
    c["cloudwatch"].add_response(
        "get_metric_data", series_data_response(series_ids(), start, end, period)
    )
    w = World(c)
    body = await w.feeds.aws_series(name)
    assert list(body) == KEYS
    assert (body["state"], body["code"], body["fetchedAt"]) == (
        "ok",
        None,
        int(NOW * 1000),
    )
    assert (body["range"], body["refreshSec"], body["periodSec"]) == (
        name,
        refresh,
        period,
    )
    assert (body["startTs"], body["endTs"]) == (start, end)
    assert len(body["boxes"][1]["series"]["cpu"]) == points
    assert set(body["canary"]) == {"errors", "durationMs"}
    json.dumps(body)  # 응답 그대로 직렬화된다
    w.advance(refresh - 1)
    assert (
        await w.feeds.aws_series(name) == body
    )  # 주기 안 — 부르지 않는다(스텁이 비었다)
    w.advance(1)
    if refresh >= 3_600:
        stub_discovery(c)  # 박스 찾기·차원의 1시간 캐시도 지났다
    c["cloudwatch"].add_response("get_metric_data", {"MetricDataResults": []})
    again = await w.feeds.aws_series(name)
    assert again["fetchedAt"] == int((NOW + refresh) * 1000)
    assert again["endTs"] == int(NOW + refresh) // period * period
    c["cloudwatch"].assert_no_pending_responses()


async def test_ranges_are_separate_caches_on_the_one_thread_and_admin_aws_is_unchanged() -> (
    None
):
    c = Clients("cloudwatch")
    blocked = Raising(denied("AccessDenied"))  # canary·예산 — 이 테스트와 무관
    captured = capture_metric_data(c)
    stub_discovery(c)
    for window, period in ((86_400, 300), (604_800, 3_600)):
        c["cloudwatch"].add_response(
            "get_metric_data",
            series_data_response(series_ids(), *bounds(window, period), period),
        )
    w = World(lambda s: c(s) if s == "cloudwatch" else blocked)
    day, week = await w.feeds.aws_series("24h"), await w.feeds.aws_series("7d")
    assert (day["periodSec"], week["periodSec"]) == (300, 3_600)
    assert [p["MetricDataQueries"][0]["MetricStat"]["Period"] for p in captured] == [
        300,
        3_600,
    ]
    # /admin/aws — 경보 다음 metrics 는 시계열이 채운 1시간 캐시로 박스를 찾고, 034 그대로 24시간·17질의를 읽는다
    c["cloudwatch"].add_response("describe_alarms", alarms_response())
    c["cloudwatch"].add_response("get_metric_data", metric_data_response(QUERY_IDS_034))
    metrics = (await w.feeds.aws())["metrics"]  # 부분의 키는 test_feeds_http.py 가 본다
    assert (metrics["state"], metrics["periodSec"]) == ("ok", 300)
    assert (
        len(metrics["boxes"][0]["mem"]) == 288 and "series" not in metrics["boxes"][0]
    )
    assert len(captured[-1]["MetricDataQueries"]) == 17
    c["cloudwatch"].assert_no_pending_responses()
    assert c.threads == {"admin-aws_0"}


@pytest.mark.parametrize(
    ("exc", "state", "code"),
    [
        (NoCredentialsError(), "unconfigured", "no_credentials"),
        (denied("InvalidClientTokenId"), "unconfigured", "InvalidClientTokenId"),
        (denied("AccessDenied"), "denied", "AccessDenied"),  # 박스 찾기에서 막힘
        (ReadTimeoutError(endpoint_url="https://monitoring"), "error", "timeout"),
        (RuntimeError("arn:aws:boom"), "error", "RuntimeError"),
    ],
)
async def test_failures_become_states_with_null_values_and_the_range_kept(
    exc: BaseException, state: str, code: str
) -> None:
    body = await World(lambda s: Raising(exc)).feeds.aws_series("7d")
    assert body == {
        **dict.fromkeys(KEYS),
        "state": state,
        "code": code,
        "refreshSec": 1_800,
        "range": "7d",
    }
    raw = json.dumps(body)
    assert "arn:aws:" not in raw and not ACCOUNT_ID.search(raw)


async def test_get_metric_data_denied_after_discovery_is_denied() -> None:
    c = Clients("cloudwatch")
    stub_discovery(c)
    c["cloudwatch"].add_client_error("get_metric_data", "AccessDenied", ARN_TEXT, 403)
    body = await World(c).feeds.aws_series("30d")
    assert (body["state"], body["code"], body["boxes"]) == (
        "denied",
        "AccessDenied",
        None,
    )


async def test_a_denied_series_is_cached_for_its_period_and_warns_once_in_ten_minutes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = Raising(lambda: denied("AccessDenied"))
    w = World(lambda s: fake)
    caplog.set_level(logging.WARNING, logger="marketlens.admin")
    for _ in range(10):  # 0~9분, 창을 번갈아
        for name in ("24h", "7d"):
            assert (await w.feeds.aws_series(name))["state"] == "denied"
        w.advance(60)
    warns = [r for r in caplog.records if r.name == "marketlens.admin"]
    assert [r.getMessage() for r in warns] == [
        "관리자 피드 aws.series 실패 — AccessDenied"
    ]
    assert warns[0].levelno == logging.WARNING
    # 실패도 주기만큼 캐시 — 24h 는 0·5분 두 번, 7d 는 30분에 한 번
    assert fake.calls == 2 + 1


async def test_a_slow_read_answers_pending_and_the_same_refresh_fills_it() -> None:
    gate, calls, threads = threading.Event(), [], set()
    answers = {
        "describe_alarms": alarms_response(),
        "list_metrics": list_metrics_response(),
    }

    class Hanging:
        def __getattr__(self, name: str) -> Callable[..., Any]:
            def call(**kwargs: Any) -> Any:
                calls.append(name)
                threads.add(threading.current_thread().name)
                gate.wait(5)
                return answers.get(name, {"MetricDataResults": []})

            return call

    w = World(lambda s: Hanging(), wait=0.2)
    try:
        # 같은 창의 동시 요청 = 갱신 하나 (없음도 24h)
        first, second = await asyncio.gather(
            w.feeds.aws_series("24h"), w.feeds.aws_series(None)
        )
        assert first == second
        assert (first["state"], first["code"], first["range"]) == (
            "pending",
            None,
            "24h",
        )
        assert first["boxes"] is None
        gate.set()
        for _ in range(100):
            await asyncio.sleep(0.02)
            body = await w.feeds.aws_series("24h")
            if body["state"] != "pending":
                break
        assert body["state"] == "ok"
        assert [b["box"] for b in body["boxes"]] == ["collect", "data", "serve"]
        assert calls == ["describe_alarms", "list_metrics", "get_metric_data"]
        assert threads == {"admin-aws_0"}
    finally:
        gate.set()
        w.feeds.close()
