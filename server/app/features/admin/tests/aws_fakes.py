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
