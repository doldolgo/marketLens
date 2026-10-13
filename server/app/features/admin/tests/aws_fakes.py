"""관리자 피드 테스트의 가짜 AWS — botocore Stubber 로 응답 모양을 실제 서비스 모델로 검증한다 (스펙 034 §4).

네트워크 없음: 클라이언트는 가짜 자격증명으로 만들고 Stubber 가 호출 전에 응답을 돌려준다. 오류 문장에는 계정 ID 가 든
ARN 을 일부러 넣는다 — 응답·로그에 새지 않는지 보려고.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.stub import Stubber

NOW = 1_790_812_345.0  # 2026-10-01 — 벽시계(초)
END = int(NOW) // 300 * 300
START = END - 86_400
ACCOUNT = "123456789012"
IDENTITY = {
    "Account": ACCOUNT,
    "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/r/i",
    "UserId": "AROAEXAMPLE:i",
}
ARN_TEXT = f"User: arn:aws:sts::{ACCOUNT}:assumed-role/marketlens-s3-snapshot/i-0abc is not authorized"
IDS = {
    "collect": "i-0c0c0c0c0c0c0c0c0",
    "data": "i-0d0d0d0d0d0d0d0d0",
    "serve": "i-05e5e5e5e5e5e5e5e",
}
REQ_A = "11111111-aaaa-4bbb-8ccc-000000000001"
REQ_B = "22222222-aaaa-4bbb-8ccc-000000000002"


def dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def stubbed(service: str) -> tuple[Any, Stubber]:
    session = boto3.session.Session(
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="ap-northeast-2",
    )
    client = session.client(service)
    stub = Stubber(client)
    stub.activate()
    return client, stub


class Clients:
    """서비스 이름 → 스텁 클라이언트. `made` 는 만든 순서(피드가 만들기를 미루는지 본다)."""

    def __init__(self, *services: str) -> None:
        self.made: list[str] = []
        self.threads: set[str] = set()
        self.clients: dict[str, Any] = {}
        self.stubs: dict[str, Stubber] = {}
        for s in services:
            self.clients[s], self.stubs[s] = stubbed(s)

    def __call__(self, service: str) -> Any:
        self.made.append(service)
        self.threads.add(threading.current_thread().name)
        return self.clients[service]

    def __getitem__(self, service: str) -> Stubber:
        return self.stubs[service]


class Raising:
    """모든 메서드가 `exc` 를 던지는 가짜 클라이언트 — Stubber 가 흉내 못 내는 자격증명·시간 초과용."""

    def __init__(self, exc: BaseException | Callable[[], BaseException]) -> None:
        self._exc = exc
        self.calls = 0

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def call(**kwargs: Any) -> Any:
            self.calls += 1
            raise self._exc() if callable(self._exc) else self._exc

        return call


# --- 응답 조각 ---


def alarm(
    name: str,
    state: str = "OK",
    reason: str = "Threshold Crossed",
    iid: str | None = None,
) -> dict:
    a: dict[str, Any] = {
        "AlarmName": name,
        "StateValue": state,
        "StateReason": reason,
        "StateTransitionedTimestamp": dt(NOW - 600),
    }
    if iid is not None:
        a["Dimensions"] = [{"Name": "InstanceId", "Value": iid}]
    return a


def alarms_response(*, with_serve: bool = True) -> dict:
    boxes = ("collect", "data", "serve") if with_serve else ("collect", "data")
    items = [alarm(f"marketlens-{b}-memory", iid=IDS[b]) for b in boxes]
    items.append(alarm("marketlens-canary", "ALARM", ARN_TEXT + " " + "x" * 300))
    items.append(alarm("marketlens-data-disk", "INSUFFICIENT_DATA"))
    return {"MetricAlarms": items}


def list_metrics_response() -> dict:
    """027 에이전트 설정 그대로 — swap 은 data 만, ws 게이지는 serve 의 StatsD."""
    metrics = []
    for iid in IDS.values():
        metrics.append(
            {
                "Namespace": "MarketLens",
                "MetricName": "mem_available_percent",
                "Dimensions": [{"Name": "InstanceId", "Value": iid}],
            }
        )
        metrics.append(
            {
                "Namespace": "MarketLens",
                "MetricName": "disk_used_percent",
                "Dimensions": [
                    {"Name": "InstanceId", "Value": iid},
                    {"Name": "path", "Value": "/"},
                    {"Name": "fstype", "Value": "ext4"},
                ],
            }
        )
    metrics.append(
        {
            "Namespace": "MarketLens",
            "MetricName": "swap_used_percent",
            "Dimensions": [{"Name": "InstanceId", "Value": IDS["data"]}],
        }
    )
    metrics.append(
        {
            "Namespace": "MarketLens",
            "MetricName": "marketlens_ws_clients",
            "Dimensions": [
                {"Name": "InstanceId", "Value": IDS["serve"]},
                {"Name": "metric_type", "Value": "gauge"},
            ],
        }
    )
    return {"Metrics": metrics}


def metric_data_response(ids: list[str]) -> dict:
    """질의마다 두 점 — 첫 구간과 마지막 구간. c7g(collect)의 크레딧은 자료 없음."""
    results = []
    for qid in ids:
        if qid == "collect_credit":
            results.append(
                {"Id": qid, "Timestamps": [], "Values": [], "StatusCode": "Complete"}
            )
            continue
        results.append(
            {
                "Id": qid,
                "Timestamps": [dt(START), dt(END - 300)],
                "Values": [
                    12.3456,
                    3.0 if qid.startswith(("ws", "canary")) else 45.678,
                ],
                "StatusCode": "Complete",
            }
        )
    return {"MetricDataResults": results, "Messages": []}


# --- 063 시계열 ---

SERIES_KEYS = [
    "cpu",
    "netIn",
    "netOut",
    "ebsRead",
    "ebsWrite",
    "creditBalance",
    "creditUsage",
    "surplusCharged",
    "statusFailed",
    "mem",
    "disk",
    "swap",
]
RATE_KEYS = {"netIn", "netOut", "ebsRead", "ebsWrite"}
CREDIT_KEYS = {"creditBalance", "creditUsage", "surplusCharged"}
# 원값(풀기 전) — 바이트 지표는 초당 값이고 응답에는 '× 주기' 한 합이 실린다
SERIES_RAW = {
    "cpu": 45.678,
    "netIn": 1234.4,
    "netOut": 2000.0,
    "ebsRead": 10.6,
    "ebsWrite": 0.4,
    "creditBalance": 123.456,
    "creditUsage": 0.1234,
    "surplusCharged": 0.0,
    "statusFailed": 1.0,
    "mem": 12.3456,
    "disk": 45.678,
    "swap": 3.333,
    "ws": 3.0,
    "errors": 1.0,
    "duration": 1523.46,
}


def series_ids() -> list[str]:
    """상자 셋·`list_metrics_response` 일 때 시계열이 만드는 질의 Id — 질의 순서 그대로(swap 은 data 만)."""
    ids: list[str] = []
    for box in IDS:
        ids += [f"{box}_{k}" for k in SERIES_KEYS if k not in ("mem", "disk", "swap")]
        ids += [f"{box}_mem", f"{box}_disk"] + (["data_swap"] if box == "data" else [])
    return ids + ["ws", "canary_errors", "canary_duration"]


def bounds(window: int, period: int) -> tuple[int, int]:
    """창의 (시작, 끝) — 끝은 NOW 를 주기로 내린 값 (063 §3.1)."""
    end = int(NOW) // period * period
    return end - window, end


def stub_discovery(clients: Clients, alarms: dict | None = None) -> None:
    """박스 찾기(DescribeAlarms)·차원(ListMetrics) — 1시간 캐시가 빈 읽기의 앞 두 호출."""
    clients["cloudwatch"].add_response("describe_alarms", alarms or alarms_response())
    clients["cloudwatch"].add_response(
        "list_metrics", list_metrics_response(), {"Namespace": "MarketLens"}
    )


def capture_metric_data(clients: Clients) -> list[dict]:
    """GetMetricData 에 실제로 넘긴 인자 — 부를 때마다 하나씩 쌓인다."""
    captured: list[dict] = []
    clients.clients["cloudwatch"].meta.events.register(
        "provide-client-params.cloudwatch.GetMetricData",
        lambda params, **kw: captured.append(params),
    )
    return captured


def series_data_response(
    ids: list[str],
    start: int,
    end: int,
    period: int,
    *,
    first: bool = True,
    last: bool = True,
) -> dict:
    """질의마다 첫 구간·마지막 구간 두 점(고르면 하나). c7g(collect)의 크레딧 셋은 자료 없음."""
    results = []
    for qid in ids:
        key = qid if qid == "ws" else qid.split("_", 1)[1]
        stamps = [ts for ts, on in ((start, first), (end - period, last)) if on]
        if qid.startswith("collect_") and key in CREDIT_KEYS:
            stamps = []
        value = SERIES_RAW[key] * (period if key in RATE_KEYS else 1)
        results.append(
            {
                "Id": qid,
                "Timestamps": [dt(ts) for ts in stamps],
                "Values": [value for _ in stamps],
                "StatusCode": "Complete",
            }
        )
    return {"MetricDataResults": results, "Messages": []}


def log_event(ts_ms: int, message: str) -> dict:
    return {
        "logStreamName": "2026/10/01/[$LATEST]abc",
        "timestamp": ts_ms,
        "message": message,
        "ingestionTime": ts_ms,
        "eventId": str(ts_ms),
    }


def canary_run(req: str, t0_ms: int, *, fail: bool = False) -> list[dict]:
    """한 실행의 줄 — 최신부터(startFromHead=false 순서)."""
    lines = [
        log_event(t0_ms, f"START RequestId: {req} Version: $LATEST\n"),
        log_event(
            t0_ms + 100, f"2026-10-01T00:00:00.100Z\t{req}\tINFO\t1단계 통과 (100ms)\n"
        ),
        log_event(
            t0_ms + 200, f"2026-10-01T00:00:00.200Z\t{req}\tINFO\t2단계 통과 (80ms)\n"
        ),
    ]
    if fail:
        err = (
            '{"errorType":"Error","errorMessage":"3단계 실패: '
            + ARN_TEXT
            + '","stack":["Error: x"]}'
        )
        lines.append(
            log_event(
                t0_ms + 300,
                f"2026-10-01T00:00:00.300Z\t{req}\tERROR\tInvoke Error \t{err}\n",
            )
        )
    else:
        lines.append(
            log_event(
                t0_ms + 300,
                f"2026-10-01T00:00:00.300Z\t{req}\tINFO\t3단계 통과 (90ms)\n",
            )
        )
        lines.append(
            log_event(
                t0_ms + 400,
                f"2026-10-01T00:00:00.400Z\t{req}\tINFO\t4단계 통과 (1200ms)\n",
            )
        )
    lines.append(log_event(t0_ms + 500, f"END RequestId: {req}\n"))
    lines.append(
        log_event(
            t0_ms + 501,
            f"REPORT RequestId: {req}\tDuration: 1523.46 ms\tBilled Duration: 1524 ms\tMemory Size: 128 MB\tMax Memory Used: 80 MB\t\n",
        )
    )
    return list(reversed(lines))


def budgets_response() -> dict:
    return {
        "Budgets": [
            {
                "BudgetName": "marketlens-monthly",
                "BudgetLimit": {"Amount": "130.0", "Unit": "USD"},
                "TimeUnit": "MONTHLY",
                "BudgetType": "COST",
                "CalculatedSpend": {
                    "ActualSpend": {"Amount": "41.23456", "Unit": "USD"},
                    "ForecastedSpend": {"Amount": "88.1", "Unit": "USD"},
                },
            },
            {
                "BudgetName": "usage",
                "BudgetLimit": {"Amount": "10", "Unit": "GB"},
                "TimeUnit": "MONTHLY",
                "BudgetType": "USAGE",
            },
        ]
    }


def history_item(
    name: str, ts: float, old: str, new: str, reason: str = "Threshold Crossed"
) -> dict:
    import json

    data = {
        "version": "1.0",
        "oldState": {"stateValue": old, "stateReason": "x"},
        "newState": {"stateValue": new, "stateReason": reason},
    }
    return {
        "AlarmName": name,
        "AlarmType": "MetricAlarm",
        "Timestamp": dt(ts),
        "HistoryItemType": "StateUpdate",
        "HistorySummary": f"Alarm updated from {old} to {new}",
        "HistoryData": json.dumps(data),
    }
