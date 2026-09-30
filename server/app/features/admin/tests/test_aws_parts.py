"""AWS 부분별 읽기 — 경보·24시간 지표·canary·예산·경보 이력의 모양과 오류 분류 (스펙 034 §3.2·§3.3·§4).

`AwsReader` 를 Stubber 클라이언트로 직접 부른다(스레드·캐시는 test_feeds.py). 응답은 실제 서비스 모델로 검증된다.
"""

import re

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    NoCredentialsError,
    ReadTimeoutError,
)
from botocore.stub import ANY

from app.features.admin.aws import AwsReader, PartialRead, classify
from app.features.admin.tests.aws_fakes import (
    ACCOUNT,
    END,
    IDENTITY,
    IDS,
    NOW,
    REQ_A,
    REQ_B,
    START,
    Clients,
    alarms_response,
    budgets_response,
    canary_run,
    history_item,
    list_metrics_response,
    log_event,
    metric_data_response,
)

ACCOUNT_ID = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")


def reader(clients: Clients, mono: list[float] | None = None) -> AwsReader:
    t = mono if mono is not None else [0.0]
    return AwsReader(clients, clock=lambda: NOW, mono=lambda: t[0])


# --- 오류 분류 (§3.2) ---


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "arn:aws:iam::123456789012:x"}}, "Op"
    )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (NoCredentialsError(), ("unconfigured", "no_credentials")),
        (
            CredentialRetrievalError(provider="imds", error_msg="x"),
            ("unconfigured", "no_credentials"),
        ),
        (
            _client_error("InvalidClientTokenId"),
            ("unconfigured", "InvalidClientTokenId"),
        ),
        (
            _client_error("UnrecognizedClientException"),
            ("unconfigured", "UnrecognizedClientException"),
        ),
        (_client_error("ExpiredToken"), ("unconfigured", "ExpiredToken")),
        (
            _client_error("ExpiredTokenException"),
            ("unconfigured", "ExpiredTokenException"),
        ),  # JSON 프로토콜(Logs·Budgets)의 만료 — canary·예산도 경보와 같은 상태로
        (_client_error("AccessDenied"), ("denied", "AccessDenied")),
        (_client_error("AccessDeniedException"), ("denied", "AccessDeniedException")),
        (_client_error("UnauthorizedOperation"), ("denied", "UnauthorizedOperation")),
        (_client_error("ThrottlingException"), ("error", "ThrottlingException")),
        (
            _client_error("arn:aws:x 123456789012"),
            ("error", "ClientError"),
        ),  # 모양이 이상한 코드는 싣지 않는다
        (ConnectTimeoutError(endpoint_url="https://x"), ("error", "timeout")),
        (ReadTimeoutError(endpoint_url="https://x"), ("error", "timeout")),
        (PartialRead(), ("error", "partial")),
        (KeyError("x"), ("error", "KeyError")),
    ],
)
def test_classify_maps_exceptions_to_state_and_short_code(
    exc: BaseException, expected: tuple[str, str]
) -> None:
    assert classify(exc) == expected


# --- alarms ---


def test_alarms_are_name_ordered_with_counts_and_masked_short_reasons() -> None:
    clients = Clients("cloudwatch")
    clients["cloudwatch"].add_response(
        "describe_alarms",
        alarms_response(),
        {
            "AlarmNamePrefix": "marketlens-",
            "AlarmTypes": ["MetricAlarm"],
            "MaxRecords": 100,
        },
    )
    out = reader(clients).alarms()
    names = [i["name"] for i in out["items"]]
    assert names == sorted(names) and len(names) == 5
    assert out["counts"] == {"ok": 3, "alarm": 1, "insufficientData": 1}
    canary = next(i for i in out["items"] if i["name"] == "marketlens-canary")
    assert canary["state"] == "ALARM"
    assert canary["changedAt"] == int((NOW - 600) * 1000)
    assert len(canary["reason"]) == 200
    assert "arn:aws:" not in canary["reason"] and not ACCOUNT_ID.search(
        canary["reason"]
    )


# --- metrics ---


def _metrics_stubs(
    clients: Clients, *, with_serve: bool = True, captured: list | None = None
) -> None:
    cw = clients["cloudwatch"]
    cw.add_response("describe_alarms", alarms_response(with_serve=with_serve))
    cw.add_response(
        "list_metrics", list_metrics_response(), {"Namespace": "MarketLens"}
    )
    ids = [f"{b}_{k}" for b in IDS for k in ("mem", "disk", "swap", "cpu", "credit")]
    ids += ["ws", "canary_runs", "canary_errors", "canary_duration"]
    cw.add_response("get_metric_data", metric_data_response(ids))
    if captured is not None:
        clients.clients["cloudwatch"].meta.events.register(
            "provide-client-params.cloudwatch.GetMetricData",
            lambda params, **kw: captured.append(params),
        )


def test_metrics_boxes_come_from_memory_alarm_names_on_a_288_point_grid() -> None:
    clients, captured = Clients("cloudwatch"), []
    _metrics_stubs(clients, captured=captured)
    out = reader(clients).metrics()
    assert (out["startTs"], out["endTs"], out["periodSec"]) == (START, END, 300)
    assert [b["box"] for b in out["boxes"]] == ["collect", "data", "serve"]
    assert [b["instanceId"] for b in out["boxes"]] == list(IDS.values())
    data = out["boxes"][1]
    assert len(data["mem"]) == 288
    assert data["mem"][0] == [START, 12.35] and data["mem"][-1] == [END - 300, 45.68]
    assert data["mem"][1] == [START + 300, None]  # 자료 없는 구간
    assert all(p[0] == START + i * 300 for i, p in enumerate(data["mem"]))
    collect, serve = out["boxes"][0], out["boxes"][2]
    assert collect["credit"] is None  # c7g — 크레딧 자료 없음
    assert (
        data["swap"] is not None and collect["swap"] is None and serve["swap"] is None
    )
    assert out["wsClients"][-1] == [END - 300, 3] and isinstance(
        out["wsClients"][0][1], int
    )
    assert out["canary"]["durationMs"][0] == [START, 12]
    # 질의 한 번 — 에이전트 차원을 ListMetrics 에서 그대로, 통계는 지표마다
    ((params,),) = [captured]
    queries = {q["Id"]: q["MetricStat"] for q in params["MetricDataQueries"]}
    assert len(queries) == 17
    assert queries["data_disk"]["Metric"]["Dimensions"][1:] == [
        {"Name": "path", "Value": "/"},
        {"Name": "fstype", "Value": "ext4"},
    ]
    assert {k: q["Stat"] for k, q in queries.items() if k.startswith("serve_")} == {
        "serve_mem": "Minimum",
        "serve_disk": "Maximum",
        "serve_cpu": "Average",
        "serve_credit": "Minimum",
    }
    assert queries["ws"]["Metric"]["Dimensions"][1] == {
        "Name": "metric_type",
        "Value": "gauge",
    }
    assert (
        queries["canary_runs"]["Stat"] == "Sum"
        and queries["canary_duration"]["Stat"] == "Maximum"
    )
    assert all(q["Period"] == 300 for q in queries.values())


def _ws_query(*, with_serve: bool) -> tuple[dict, dict]:
    """ListMetrics 맨 앞에 바꾸기 전 serve 의 게이지를 둔 채 지표를 읽는다 — (응답, 질의 Id → 질의)."""
    old_serve = {
        "Namespace": "MarketLens",
        "MetricName": "marketlens_ws_clients",
        "Dimensions": [
            {"Name": "InstanceId", "Value": "i-0aaaaaaaaaaaaaaaa"},
            {"Name": "metric_type", "Value": "gauge"},
        ],
    }
    clients, captured = Clients("cloudwatch"), []
    cw = clients["cloudwatch"]
    cw.add_response("describe_alarms", alarms_response(with_serve=with_serve))
    listed = list_metrics_response()
    listed["Metrics"].insert(0, old_serve)
    cw.add_response("list_metrics", listed)
    ids = [f"{b}_{k}" for b in IDS for k in ("mem", "disk", "cpu", "credit")]
    cw.add_response("get_metric_data", metric_data_response([*ids, "ws"]))
    clients.clients["cloudwatch"].meta.events.register(
        "provide-client-params.cloudwatch.GetMetricData",
        lambda params, **kw: captured.append(params),
    )
    out = reader(clients).metrics()
    return out, {q["Id"]: q for q in captured[0]["MetricDataQueries"]}


def test_ws_gauge_is_the_serve_instance_s_even_when_an_old_one_is_listed_first() -> (
    None
):
    # ListMetrics 는 2주 안에 자료가 있던 지표를 순서 없이 준다 — 바꾸기 전 serve 의 게이지가 앞에 올 수 있다
    out, queries = _ws_query(with_serve=True)
    dims = queries["ws"]["MetricStat"]["Metric"]["Dimensions"]
    assert dims[0] == {"Name": "InstanceId", "Value": IDS["serve"]}
    assert out["wsClients"] is not None
    out, queries = _ws_query(with_serve=False)
    assert "ws" not in queries and out["wsClients"] is None  # serve 가 빠지면 null


def test_metrics_drop_a_box_without_its_memory_alarm_and_cache_discovery_for_an_hour() -> (
    None
):
    clients, mono = Clients("cloudwatch"), [0.0]
    _metrics_stubs(clients, with_serve=False)
    r = reader(clients, mono)
    assert [b["box"] for b in r.metrics()["boxes"]] == ["collect", "data"]
    # 1시간 안 — 박스 찾기·차원은 캐시, GetMetricData 만
    mono[0] = 3_599
    clients["cloudwatch"].add_response("get_metric_data", {"MetricDataResults": []})
    assert r.metrics()["boxes"][0]["mem"] is None  # 결과가 없는 질의는 null
    mono[0] = 3_600
    _metrics_stubs(clients)
    assert len(r.metrics()["boxes"]) == 3
    clients["cloudwatch"].assert_no_pending_responses()


# --- canary ---

T_A = int(NOW * 1000) - 300_000  # 5분 전 실행
T_B = int(NOW * 1000) - 5_000  # 방금 시작해 아직 안 끝난 실행


def _logs(clients: Clients, *pages: tuple[list[dict], str | None]) -> None:
    for i, (events, token) in enumerate(pages):
        resp: dict = {"events": events, "searchedLogStreams": []}
        if token:
            resp["nextToken"] = token
        expected = {
            "logGroupName": "/aws/lambda/marketlens-smoke",
            "startTime": int(NOW * 1000) - 660_000,
            "startFromHead": False,
        }
        if i:
            expected["nextToken"] = ANY
        clients["logs"].add_response("filter_log_events", resp, expected)


def test_canary_reads_newest_first_and_keeps_only_the_last_finished_run() -> None:
    clients = Clients("logs")
    running = [
        log_event(
            T_B + 10, f"2026-10-01T00:05:00.010Z\t{REQ_B}\tINFO\t1단계 통과 (90ms)\n"
        ),
        log_event(T_B, f"START RequestId: {REQ_B} Version: $LATEST\n"),
    ]
    _logs(clients, (running + canary_run(REQ_A, T_A), "more"))
    out = reader(clients).canary()
    assert out == {
        "lastRunAt": T_A,
        "durationMs": 1523,
        "ok": True,
        "lines": [
            "1단계 통과 (100ms)",
            "2단계 통과 (80ms)",
            "3단계 통과 (90ms)",
            "4단계 통과 (1200ms)",
        ],
    }
    clients["logs"].assert_no_pending_responses()  # START 를 찾아 토큰이 남아도 멈췄다


def test_canary_keeps_only_the_later_of_two_finished_runs() -> None:
    clients = Clients("logs")
    t_later = T_A + 60_000
    # 최신부터 — 뒤 실행(성공)이 먼저, 앞 실행(실패)이 뒤에 온다. 토큰이 남아도 뒤 실행의 START 를 찾으면 멈춘다
    _logs(
        clients,
        (canary_run(REQ_B, t_later) + canary_run(REQ_A, T_A, fail=True), "more"),
    )
    out = reader(clients).canary()
    assert out["lastRunAt"] == t_later and out["ok"] is True
    assert out["lines"] == [
        "1단계 통과 (100ms)",
        "2단계 통과 (80ms)",
        "3단계 통과 (90ms)",
        "4단계 통과 (1200ms)",
    ]  # 앞 실행의 `3단계 실패` 줄은 없다
    clients["logs"].assert_no_pending_responses()


def test_canary_failure_keeps_the_error_message_masked() -> None:
    clients = Clients("logs")
    _logs(clients, (canary_run(REQ_A, T_A, fail=True), None))
    out = reader(clients).canary()
    assert out["ok"] is False
    last = out["lines"][-1]
    assert last.startswith("3단계 실패: User: [가림]")  # JSON 오류 줄은 errorMessage 만
    assert "errorType" not in last and ACCOUNT not in last


def test_canary_follows_an_empty_first_page_with_a_token() -> None:
    clients = Clients("logs")
    _logs(
        clients,
        ([], "t1"),
        (canary_run(REQ_A, T_A)[:1], "t2"),
        (canary_run(REQ_A, T_A)[1:], None),
    )
    assert reader(clients).canary()["lastRunAt"] == T_A
    clients["logs"].assert_no_pending_responses()


def test_canary_three_empty_pages_with_a_token_left_is_partial() -> None:
    clients = Clients("logs")
    _logs(clients, ([], "t1"), ([], "t2"), ([], "t3"))
    with pytest.raises(PartialRead):
        reader(clients).canary()
    assert classify(PartialRead()) == ("error", "partial")


def test_canary_without_a_finished_run_in_eleven_minutes_is_ok_and_empty() -> None:
    clients = Clients("logs")
    _logs(
        clients,
        ([log_event(T_B, f"START RequestId: {REQ_B} Version: $LATEST\n")], None),
    )
    assert reader(clients).canary() == {
        "lastRunAt": None,
        "durationMs": None,
        "ok": None,
        "lines": [],
    }


# --- budget ---


def test_budget_lists_cost_budgets_without_the_account_id() -> None:
    clients = Clients("sts", "budgets")
    clients["sts"].add_response("get_caller_identity", IDENTITY)
    clients["budgets"].add_response(
        "describe_budgets",
        budgets_response(),
        {"AccountId": ACCOUNT, "MaxResults": 100},
    )
    out = reader(clients).budget()
    assert out == {
        "items": [
            {
                "name": "marketlens-monthly",
                "unit": "USD",
                "limit": 130.0,
                "actual": 41.23,
                "forecast": 88.1,
                "timeUnit": "MONTHLY",
            }
        ]
    }
    assert ACCOUNT not in repr(out)


# --- 경보 이력 (§3.3) ---


def test_alarm_history_keeps_marketlens_state_updates_over_two_pages_at_most() -> None:
    clients = Clients("cloudwatch")
    cw = clients["cloudwatch"]
    first = [
        history_item(
            "marketlens-canary",
            NOW - 60,
            "OK",
            "ALARM",
            ARN_REASON
            := "Threshold Crossed on arn:aws:lambda:ap-northeast-2:123456789012:function:x",
        ),
        history_item("someone-else", NOW - 120, "OK", "ALARM"),
    ]
    expected = {
        "HistoryItemType": "StateUpdate",
        "StartDate": ANY,
        "EndDate": ANY,
        "MaxRecords": 100,
        "ScanBy": "TimestampDescending",
    }
    cw.add_response(
        "describe_alarm_history",
        {"AlarmHistoryItems": first, "NextToken": "n1"},
        expected,
    )
    second = [history_item("marketlens-data-disk", NOW - 3_600, "ALARM", "OK")]
    cw.add_response(
        "describe_alarm_history",
        {"AlarmHistoryItems": second, "NextToken": "n2"},
        {**expected, "NextToken": "n1"},
    )
    items = reader(clients).alarm_history()["items"]
    cw.assert_no_pending_responses()  # 셋째 쪽은 부르지 않는다
    assert [(i["alarm"], i["fromState"], i["toState"]) for i in items] == [
        ("marketlens-canary", "OK", "ALARM"),
        ("marketlens-data-disk", "ALARM", "OK"),
    ]
    assert items[0]["at"] == int((NOW - 60) * 1000)
    assert "arn:aws:" not in items[0]["text"] and ARN_REASON != items[0]["text"]
