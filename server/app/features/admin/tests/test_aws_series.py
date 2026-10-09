"""AWS 시계열 읽기 — 창·주기·격자·풀기·차원·상자 (스펙 063 §3.1·§3.2·§3.4·§4).

`AwsReader.series` 를 Stubber 클라이언트로 직접 부른다(질의 나눔·쪽 넘김·캐시 공유는 test_aws_series_calls.py,
창 고르기·캐시·스레드·HTTP 는 test_series_feed.py). 응답은 실제 서비스 모델로 검증된다.
"""

import pytest

from app.features.admin.aws import AwsReader
from app.features.admin.tests.aws_fakes import (
    END,
    IDS,
    NOW,
    SERIES_KEYS,
    START,
    Clients,
    alarm,
    alarms_response,
    bounds,
    capture_metric_data,
    dt,
    list_metrics_response,
    series_data_response,
    series_ids,
    stub_discovery,
)


def reader(clients: Clients) -> AwsReader:
    return AwsReader(clients, clock=lambda: NOW, mono=lambda: 0.0)


def test_24h_has_three_boxes_and_twelve_series_on_a_288_point_grid() -> None:
    clients = Clients("cloudwatch")
    stub_discovery(clients)
    ids = series_ids()
    clients["cloudwatch"].add_response(
        "get_metric_data", series_data_response(ids, START, END, 300)
    )
    captured = capture_metric_data(clients)
    out = reader(clients).series(86_400, 300)
    assert (out["periodSec"], out["startTs"], out["endTs"]) == (300, START, END)
    assert [b["box"] for b in out["boxes"]] == ["collect", "data", "serve"]
    assert [b["instanceId"] for b in out["boxes"]] == list(IDS.values())
    collect, data, serve = (b["series"] for b in out["boxes"])
    assert list(data) == SERIES_KEYS
    cpu = data["cpu"]
    assert len(cpu) == 288 and all(p[0] == START + i * 300 for i, p in enumerate(cpu))
    assert cpu[1] == [START + 300, None]  # 자료 없는 구간 — 격자는 null 로 채운다
    first = {k: v[0][1] for k, v in data.items()}
    assert first == {
        "cpu": 45.68,
        "netIn": 1234,  # 합 1234.4 × 300 ÷ 300 — 바이트/초 정수
        "netOut": 2000,
        "ebsRead": 11,
        "ebsWrite": 0,
        "creditBalance": 123.46,
        "creditUsage": 0.12,
        "surplusCharged": 0.0,  # 0 은 자료다 — null 이 아니다
        "statusFailed": 1,
        "mem": 87.65,  # 100 − 가용 12.3456
        "disk": 45.68,
        "swap": 3.33,
    }
    for key in ("netIn", "netOut", "ebsRead", "ebsWrite", "statusFailed"):
        assert type(first[key]) is int, key
    assert data["mem"][-1] == [END - 300, 87.65]
    # c7g 는 크레딧 자료가 없고, swap 은 data 만 보낸다(ListMetrics 에 차원이 없으면 null)
    for key in ("creditBalance", "creditUsage", "surplusCharged"):
        assert collect[key] is None and serve[key] is not None, key
    assert collect["swap"] is None and serve["swap"] is None
    assert out["wsClients"][-1] == [END - 300, 3]
    assert type(out["wsClients"][0][1]) is int
    assert out["canary"]["errors"][0] == [START, 1]
    assert out["canary"]["durationMs"][0] == [START, 1523]
    # 질의 한 번 — 상자마다 EC2 아홉 + 에이전트(차원이 있는 것만) + WS + canary 둘
    ((params,),) = [captured]
    queries = {q["Id"]: q["MetricStat"] for q in params["MetricDataQueries"]}
    assert list(queries) == ids and len(ids) == 37
    assert {q["Period"] for q in queries.values()} == {300}
    assert {k: q["Stat"] for k, q in queries.items() if k.startswith("serve_")} == {
        "serve_cpu": "Average",
        "serve_netIn": "Sum",
        "serve_netOut": "Sum",
        "serve_ebsRead": "Sum",
        "serve_ebsWrite": "Sum",
        "serve_creditBalance": "Minimum",
        "serve_creditUsage": "Sum",
        "serve_surplusCharged": "Sum",
        "serve_statusFailed": "Maximum",
        "serve_mem": "Minimum",
        "serve_disk": "Maximum",
    }
    assert queries["data_netOut"]["Metric"] == {
        "Namespace": "AWS/EC2",
        "MetricName": "NetworkOut",
        "Dimensions": [{"Name": "InstanceId", "Value": IDS["data"]}],
    }
    # 에이전트 지표는 ListMetrics 의 차원을 그대로
    assert queries["data_disk"]["Metric"]["Dimensions"][1:] == [
        {"Name": "path", "Value": "/"},
        {"Name": "fstype", "Value": "ext4"},
    ]
    assert queries["ws"]["Metric"]["Dimensions"][0] == {
        "Name": "InstanceId",
        "Value": IDS["serve"],
    }
    assert (queries["canary_errors"]["Stat"], queries["canary_duration"]["Stat"]) == (
        "Sum",
        "Maximum",
    )
    assert (params["StartTime"], params["EndTime"]) == (dt(START), dt(END))


@pytest.mark.parametrize(
    ("window", "period", "points"),
    [(21_600, 300, 72), (604_800, 3_600, 168), (2_592_000, 10_800, 240)],
)
def test_each_window_floors_its_end_to_the_period_and_divides_bytes_by_it(
    window: int, period: int, points: int
) -> None:
    start, end = bounds(window, period)
    clients = Clients("cloudwatch")
    stub_discovery(clients)
    clients["cloudwatch"].add_response(
        "get_metric_data", series_data_response(series_ids(), start, end, period)
    )
    captured = capture_metric_data(clients)
    out = reader(clients).series(window, period)
    assert (out["periodSec"], out["startTs"], out["endTs"]) == (period, start, end)
    assert end % period == 0 and end <= NOW < end + period
    serve = out["boxes"][2]["series"]
    assert len(serve["netOut"]) == points
    assert serve["netIn"][0] == [start, 1234]  # 합 1234.4 × 주기 ÷ 주기
    assert serve["netIn"][-1] == [end - period, 1234]
    assert serve["cpu"][1] == [start + period, None]
    sent = captured[0]
    assert {q["MetricStat"]["Period"] for q in sent["MetricDataQueries"]} == {period}
    assert (sent["StartTime"], sent["EndTime"]) == (dt(start), dt(end))


def test_agent_series_without_listed_dimensions_are_null_and_not_queried() -> None:
    clients = Clients("cloudwatch")
    listed = list_metrics_response()
    listed["Metrics"] = [
        m
        for m in listed["Metrics"]
        if {"Name": "InstanceId", "Value": IDS["collect"]} not in m["Dimensions"]
    ]
    clients["cloudwatch"].add_response("describe_alarms", alarms_response())
    clients["cloudwatch"].add_response("list_metrics", listed)
    ids = [i for i in series_ids() if i not in ("collect_mem", "collect_disk")]
    clients["cloudwatch"].add_response(
        "get_metric_data", series_data_response(ids, START, END, 300)
    )
    captured = capture_metric_data(clients)
    collect = reader(clients).series(86_400, 300)["boxes"][0]["series"]
    assert (collect["mem"], collect["disk"], collect["swap"]) == (None, None, None)
    assert collect["cpu"] is not None  # EC2 지표는 그대로
    assert [q["Id"] for q in captured[0]["MetricDataQueries"]] == ids


def test_no_box_alarm_means_an_empty_box_list_but_the_canary_is_still_read() -> None:
    clients = Clients("cloudwatch")
    stub_discovery(
        clients, {"MetricAlarms": [alarm("marketlens-canary"), alarm("x-memory")]}
    )
    ids = ["canary_errors", "canary_duration"]
    clients["cloudwatch"].add_response(
        "get_metric_data", series_data_response(ids, START, END, 300)
    )
    captured = capture_metric_data(clients)
    out = reader(clients).series(86_400, 300)
    assert out["boxes"] == [] and out["wsClients"] is None  # serve 가 빠지면 WS 도 null
    assert out["canary"]["errors"][0] == [START, 1]
    assert [q["Id"] for q in captured[0]["MetricDataQueries"]] == ids
