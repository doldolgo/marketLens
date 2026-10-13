"""AWS 시계열 호출 — 질의 500개마다 나눔·nextToken 쪽 넘김·쪽 수 상한·034 지표와 1시간 캐시 공유 (스펙 063 §3.2·§4)."""

from typing import Any

import pytest
from botocore.stub import ANY

from app.features.admin import aws as aws_module
from app.features.admin.aws import AwsReader, PartialRead, classify
from app.features.admin.tests.aws_fakes import (
    END,
    IDS,
    NOW,
    START,
    Clients,
    bounds,
    capture_metric_data,
    metric_data_response,
    series_data_response,
    series_ids,
    stub_discovery,
)

QUERY_IDS_034 = [
    f"{b}_{k}" for b in IDS for k in ("mem", "disk", "swap", "cpu", "credit")
] + ["ws", "canary_runs", "canary_errors", "canary_duration"]


def reader(clients: Clients, mono: list[float] | None = None) -> AwsReader:
    t = mono if mono is not None else [0.0]
    return AwsReader(clients, clock=lambda: NOW, mono=lambda: t[0])


def test_more_queries_than_the_limit_are_split_and_merged_into_the_same_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = series_ids()
    single = Clients("cloudwatch")
    stub_discovery(single)
    single["cloudwatch"].add_response(
        "get_metric_data", series_data_response(ids, START, END, 300)
    )
    whole = reader(single).series(86_400, 300)
    # 실제 한도는 500 — 상자 셋의 질의는 37개라 한도를 줄여 나눔을 본다
    monkeypatch.setattr(aws_module, "METRIC_QUERY_LIMIT", 10)
    clients = Clients("cloudwatch")
    stub_discovery(clients)
    chunks = [ids[i : i + 10] for i in range(0, len(ids), 10)]
    for chunk in chunks:
        clients["cloudwatch"].add_response(
            "get_metric_data", series_data_response(chunk, START, END, 300)
        )
    captured = capture_metric_data(clients)
    assert reader(clients).series(86_400, 300) == whole
    sent = [[q["Id"] for q in p["MetricDataQueries"]] for p in captured]
    assert sent == chunks and [len(c) for c in sent] == [10, 10, 10, 7]
    clients["cloudwatch"].assert_no_pending_responses()


def test_next_token_pages_are_followed_and_merged_per_query() -> None:
    ids = series_ids()
    clients = Clients("cloudwatch")
    stub_discovery(clients)
    cw = clients["cloudwatch"]
    page1 = series_data_response(ids, START, END, 300, last=False)
    for r in page1["MetricDataResults"]:
        r["StatusCode"] = "PartialData"
    cw.add_response("get_metric_data", {**page1, "NextToken": "p2"})
    expected: dict[str, Any] = {
        "MetricDataQueries": ANY,
        "StartTime": ANY,
        "EndTime": ANY,
        "ScanBy": "TimestampAscending",
        "NextToken": "p2",
    }
    cw.add_response(
        "get_metric_data",
        series_data_response(ids, START, END, 300, first=False),
        expected,
    )
    out = reader(clients).series(86_400, 300)
    cw.assert_no_pending_responses()
    data = out["boxes"][1]["series"]
    assert data["netOut"][0] == [START, 2000]
    assert data["netOut"][-1] == [END - 300, 2000]
    assert out["canary"]["durationMs"][-1] == [END - 300, 1523]


def test_a_token_left_after_the_page_limit_is_partial() -> None:
    clients = Clients("cloudwatch")
    stub_discovery(clients)
    for _ in range(5):
        clients["cloudwatch"].add_response(
            "get_metric_data", {"MetricDataResults": [], "NextToken": "again"}
        )
    with pytest.raises(PartialRead) as caught:
        reader(clients).series(86_400, 300)
    assert classify(caught.value) == ("error", "partial")
    clients["cloudwatch"].assert_no_pending_responses()  # 여섯째 쪽은 부르지 않는다


def test_series_and_the_24h_metrics_share_the_hourly_box_and_dimension_lookups() -> (
    None
):
    clients, mono = Clients("cloudwatch"), [0.0]
    cw = clients["cloudwatch"]
    stub_discovery(clients)
    cw.add_response("get_metric_data", metric_data_response(QUERY_IDS_034))
    r = reader(clients, mono)
    r.metrics()
    # 1시간 안 — 시계열은 GetMetricData 만 (박스 찾기·차원은 034 metrics 가 채운 캐시)
    mono[0] = 3_599
    start, end = bounds(604_800, 3_600)
    cw.add_response(
        "get_metric_data", series_data_response(series_ids(), start, end, 3_600)
    )
    boxes = r.series(604_800, 3_600)["boxes"]
    assert [b["box"] for b in boxes] == ["collect", "data", "serve"]
    cw.assert_no_pending_responses()
    # 1시간 — 시계열이 다시 찾고, 그 캐시를 metrics 가 쓴다
    mono[0] = 3_600
    stub_discovery(clients)
    cw.add_response(
        "get_metric_data", series_data_response(series_ids(), START, END, 300)
    )
    r.series(86_400, 300)
    cw.add_response("get_metric_data", metric_data_response(QUERY_IDS_034))
    assert len(r.metrics()["boxes"]) == 3
    cw.assert_no_pending_responses()
