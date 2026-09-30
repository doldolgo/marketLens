"""AWS 읽기 — 경보·24시간 지표·canary 로그·예산·경보 이력 (스펙 034 §3.2·§3.3).

모든 함수는 동기이고 `feeds.py` 의 전용 실행기(스레드 1개)에서만 돈다 — 클라이언트 생성·호출·응답 풀기까지 한 번에.
수집기의 기본 실행기(1 vCPU 에서 스레드 5개)는 Influx 쓰기·롤업·원문 묶기가 쓰므로, 느린 AWS 호출이 그 줄에 서지 않게
한다. 박스 찾기 캐시(1시간)도 그 스레드만 만진다 — 잠금이 없다.
실패는 예외로 올리고 `classify` 가 부분 상태(unconfigured·denied·error)와 짧은 code 로 바꾼다. 오류 문장은 쓰지 않는다 —
AWS 오류 문장에는 계정 ID 가 든 ARN 이 있다. 사람이 읽는 글(경보 사유·이력 사유·canary 줄)은 core `redact` 를 거친다.
"""

import json
import math
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    NoCredentialsError,
    ReadTimeoutError,
)

from app.core.redact import redact

# 호출마다 연결 2초·읽기 5초, 재시도 없음 (§3.2) — 막힌 API 가 전용 스레드를 오래 잡지 않게
CLIENT_CONFIG = Config(
    connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1}
)
ALARM_PREFIX = "marketlens-"
BOXES = ("collect", "data", "serve")
NAMESPACE = "MarketLens"
CANARY_FUNCTION = "marketlens-smoke"
CANARY_LOG_GROUP = f"/aws/lambda/{CANARY_FUNCTION}"
PERIOD_SEC = 300
WINDOW_SEC = 86_400
DISCOVERY_TTL_SEC = 3_600
CANARY_WINDOW_MS = 11 * 60 * 1000
CANARY_MAX_PAGES = 3
CANARY_MAX_SEC = 2.0
CANARY_LINE_LIMIT = 300
CANARY_MAX_LINES = 20
REASON_LIMIT = 200
HISTORY_WINDOW_SEC = 7 * 86_400
HISTORY_PAGE = 100
HISTORY_MAX_PAGES = 2
LIST_MAX_PAGES = 5  # 경보·지표 목록 쪽 수 상한 — 지금은 한 쪽(경보 18개·지표 13개)

UNCONFIGURED_CODES = {
    "InvalidClientTokenId",
    "UnrecognizedClientException",
    "ExpiredToken",
}
DENIED_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
_CODE_SHAPE = re.compile(r"[A-Za-z0-9_.]{1,64}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DURATION = re.compile(r"\tDuration: ([0-9.]+) ms")
_CONTROL = ("START RequestId:", "END RequestId:", "REPORT RequestId:", "INIT_")


class PartialRead(Exception):
    """canary 로그를 한도(3쪽·2초)까지 읽었는데 끝난 실행을 못 찾음 — `error`·`partial`."""


def classify(exc: BaseException) -> tuple[str, str]:
    """예외 → (state, code). code 는 AWS 오류 코드·`no_credentials`·`timeout`·`partial`·예외 이름뿐이다."""
    if isinstance(exc, NoCredentialsError | CredentialRetrievalError):
        return "unconfigured", "no_credentials"
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code") or "")
        if not _CODE_SHAPE.fullmatch(code):
            code = "ClientError"  # 모양이 이상하면 싣지 않는다 — 어떤 글도 새지 않게
        if code in UNCONFIGURED_CODES:
            return "unconfigured", code
        if code in DENIED_CODES:
            return "denied", code
        return "error", code
    if isinstance(exc, ConnectTimeoutError | ReadTimeoutError):
        return "error", "timeout"
    if isinstance(exc, PartialRead):
        return "error", "partial"
    return "error", type(exc).__name__


def client_factory(region: str) -> Callable[[str], Any]:
    """서비스 이름 → boto3 클라이언트. 자격증명은 SDK 기본 탐색(EC2 는 인스턴스 역할 — 010). 세션도 첫 호출 때 만든다."""
    session: boto3.session.Session | None = None

    def make(service: str) -> Any:
        nonlocal session
        if session is None:
            session = boto3.session.Session()
        # budgets 는 리전과 무관하게 전역 끝점(budgets.amazonaws.com)으로 풀린다
        return session.client(service, region_name=region, config=CLIENT_CONFIG)

    return make


class AwsReader:
    """부분마다 읽기 함수 하나. `client(service)` 는 스레드 안에서 처음 쓸 때 클라이언트를 만든다."""

    def __init__(
        self,
        client: Callable[[str], Any],
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._clock = clock
        self._mono = mono
        # 박스 찾기(경보 차원)·MarketLens 지표 차원 — 1시간 캐시 (§3.2)
        self._boxes: tuple[float, dict[str, str]] | None = None
        self._dims: tuple[float, list[dict[str, Any]]] | None = None

    # --- alarms ---

    def alarms(self) -> dict[str, Any]:
        items = [
            {
                "name": a["AlarmName"],
                "state": a.get("StateValue"),
                "changedAt": _ms(a.get("StateTransitionedTimestamp")),
                "reason": _text(a.get("StateReason"), REASON_LIMIT),
            }
            for a in self._describe_alarms()
        ]
        items.sort(key=lambda i: i["name"])
        states = [i["state"] for i in items]
        counts = {
            "ok": states.count("OK"),
            "alarm": states.count("ALARM"),
            "insufficientData": states.count("INSUFFICIENT_DATA"),
        }
        return {"items": items, "counts": counts}

    def _describe_alarms(self) -> list[dict[str, Any]]:
        cw = self._client("cloudwatch")
        alarms: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(LIST_MAX_PAGES):
            kwargs: dict[str, Any] = {
                "AlarmNamePrefix": ALARM_PREFIX,
                "AlarmTypes": ["MetricAlarm"],
                "MaxRecords": 100,
            }
            if token:
                kwargs["NextToken"] = token
            resp = cw.describe_alarms(**kwargs)
            alarms.extend(resp.get("MetricAlarms", []))
            token = resp.get("NextToken")
            if not token:
                break
        return alarms

    # --- metrics ---

    def metrics(self) -> dict[str, Any]:
        end = int(self._clock()) // PERIOD_SEC * PERIOD_SEC
        start = end - WINDOW_SEC
        boxes = self._box_ids()
        dims = self._metric_dims()
        queries: list[dict[str, Any]] = []

        def add(qid: str, ns: str, name: str, dimensions: list, stat: str) -> str:
            metric = {"Namespace": ns, "MetricName": name, "Dimensions": dimensions}
            queries.append(
                {
                    "Id": qid,
                    "MetricStat": {
                        "Metric": metric,
                        "Period": PERIOD_SEC,
                        "Stat": stat,
                    },
                    "ReturnData": True,
                }
            )
            return qid

        plan: list[tuple[str, str, dict[str, str | None]]] = []
        for box in BOXES:
            iid = boxes.get(box)
            if iid is None:
                continue  # 메모리 경보가 없는 박스는 빠진다 (§3.2 — 027 경보 이름 규칙)
            ec2 = [{"Name": "InstanceId", "Value": iid}]
            ids: dict[str, str | None] = {}
            for key, name, stat in (
                ("mem", "mem_available_percent", "Minimum"),
                ("disk", "disk_used_percent", "Maximum"),
                ("swap", "swap_used_percent", "Maximum"),
            ):
                found = _find_dims(dims, name, iid)
                ids[key] = (
                    None
                    if found is None
                    else add(f"{box}_{key}", NAMESPACE, name, found, stat)
                )
            ids["cpu"] = add(f"{box}_cpu", "AWS/EC2", "CPUUtilization", ec2, "Average")
            ids["credit"] = add(
                f"{box}_credit", "AWS/EC2", "CPUCreditBalance", ec2, "Minimum"
            )
            plan.append((box, iid, ids))
        ws = next((m for m in dims if m["MetricName"] == "marketlens_ws_clients"), None)
        ws_id = (
            None
            if ws is None
            else add(
                "ws", NAMESPACE, "marketlens_ws_clients", ws["Dimensions"], "Maximum"
            )
        )
        fn = [{"Name": "FunctionName", "Value": CANARY_FUNCTION}]
        runs = add("canary_runs", "AWS/Lambda", "Invocations", fn, "Sum")
        errors = add("canary_errors", "AWS/Lambda", "Errors", fn, "Sum")
        duration = add("canary_duration", "AWS/Lambda", "Duration", fn, "Maximum")

        resp = self._client("cloudwatch").get_metric_data(
            MetricDataQueries=queries,
            StartTime=datetime.fromtimestamp(start, UTC),
            EndTime=datetime.fromtimestamp(end, UTC),
            ScanBy="TimestampAscending",
        )
        raw = {r["Id"]: r for r in resp.get("MetricDataResults", [])}

        def series(qid: str | None, digits: int | None) -> list[list[Any]] | None:
            if qid is None or qid not in raw:
                return None
            r = raw[qid]
            by_ts = {
                int(ts.timestamp()): v
                for ts, v in zip(
                    r.get("Timestamps", []), r.get("Values", []), strict=False
                )
            }
            points = [
                [ts, _round(by_ts.get(ts), digits)]
                for ts in range(start, end, PERIOD_SEC)
            ]
            return None if all(v is None for _, v in points) else points

        return {
            "endTs": end,
            "startTs": start,
            "periodSec": PERIOD_SEC,
            "boxes": [
                {
                    "box": box,
                    "instanceId": iid,
                    "mem": series(ids["mem"], 2),
                    "disk": series(ids["disk"], 2),
                    "cpu": series(ids["cpu"], 2),
                    "credit": series(ids["credit"], 2),
                    "swap": series(ids["swap"], 2),
                }
                for box, iid, ids in plan
            ],
            "wsClients": series(ws_id, None),
            "canary": {
                "runs": series(runs, None),
                "errors": series(errors, None),
                "durationMs": series(duration, None),
            },
        }

    def _box_ids(self) -> dict[str, str]:
        """경보 `marketlens-<박스>-memory` 의 InstanceId 차원 — 경보 부분 결과와 따로 부른다(1시간 캐시)."""
        now = self._mono()
        if self._boxes is not None and now - self._boxes[0] < DISCOVERY_TTL_SEC:
            return self._boxes[1]
        by_name = {a["AlarmName"]: a for a in self._describe_alarms()}
        found: dict[str, str] = {}
        for box in BOXES:
            alarm = by_name.get(f"{ALARM_PREFIX}{box}-memory")
            dims = {d["Name"]: d["Value"] for d in (alarm or {}).get("Dimensions", [])}
            if "InstanceId" in dims:
                found[box] = dims["InstanceId"]
        self._boxes = (now, found)
        return found

    def _metric_dims(self) -> list[dict[str, Any]]:
        """네임스페이스 MarketLens 의 지표·차원 목록 — 에이전트가 붙이는 차원을 그대로 쓰려고 (1시간 캐시)."""
        now = self._mono()
        if self._dims is not None and now - self._dims[0] < DISCOVERY_TTL_SEC:
            return self._dims[1]
        cw = self._client("cloudwatch")
        metrics: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(LIST_MAX_PAGES):
            kwargs: dict[str, Any] = {"Namespace": NAMESPACE}
            if token:
                kwargs["NextToken"] = token
            resp = cw.list_metrics(**kwargs)
            metrics.extend(resp.get("Metrics", []))
            token = resp.get("NextToken")
            if not token:
                break
        self._dims = (now, metrics)
        return metrics

    # --- 경보 이력 (§3.3) ---

    def alarm_history(self) -> dict[str, Any]:
        """최근 7일 StateUpdate, 최신순 100건씩 두 쪽까지 — 이름 접두 조회가 없어 이름 없이 부르고 접두로 거른다."""
        cw = self._client("cloudwatch")
        end = self._clock()
        items: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(HISTORY_MAX_PAGES):
            kwargs: dict[str, Any] = {
                "HistoryItemType": "StateUpdate",
                "StartDate": datetime.fromtimestamp(end - HISTORY_WINDOW_SEC, UTC),
                "EndDate": datetime.fromtimestamp(end, UTC),
                "MaxRecords": HISTORY_PAGE,
                "ScanBy": "TimestampDescending",
            }
            if token:
                kwargs["NextToken"] = token
            resp = cw.describe_alarm_history(**kwargs)
            for h in resp.get("AlarmHistoryItems", []):
                name = str(h.get("AlarmName", ""))
                if not name.startswith(ALARM_PREFIX):
                    continue
                data = _json_object(h.get("HistoryData"))
                old = (
                    data.get("oldState")
                    if isinstance(data.get("oldState"), dict)
                    else {}
                )
                new = (
                    data.get("newState")
                    if isinstance(data.get("newState"), dict)
                    else {}
                )
                items.append(
                    {
                        "at": _ms(h.get("Timestamp")),
                        "alarm": name,
                        "fromState": old.get("stateValue"),
                        "toState": new.get("stateValue"),
                        "text": _text(new.get("stateReason"), REASON_LIMIT),
                    }
                )
            token = resp.get("NextToken")
            if not token:
                break
        return {"items": items}


# --- 풀기 도움 ---


def _ms(value: Any) -> int | None:
    return int(value.timestamp() * 1000) if isinstance(value, datetime) else None


def _text(value: Any, limit: int) -> str | None:
    """가린 뒤 자른다 — 먼저 자르면 잘린 계정 ID 조각이 12자리가 아니게 되어 남는다."""
    if not isinstance(value, str):
        return None
    return redact(value)[:limit]


def _round(value: float | None, digits: int | None) -> float | int | None:
    # 유한하지 않은 값은 JSON 이 못 싣는다(응답이 500 이 된다) — 자료 없음과 같게
    if value is None or not math.isfinite(value):
        return None
    return round(value) if digits is None else round(value, digits)


def _money(amount: Any) -> float | None:
    try:
        return _round(float(amount), 2)
    except (TypeError, ValueError):
        return None


def _find_dims(
    metrics: list[dict[str, Any]], name: str, instance_id: str
) -> list | None:
    for m in metrics:
        if m.get("MetricName") != name:
            continue
        dims = m.get("Dimensions", [])
        if {"Name": "InstanceId", "Value": instance_id} in dims:
            return dims
    return None


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _request_id(message: str) -> str | None:
    """Lambda 텍스트 형식 — START/END/REPORT 는 `RequestId: <id>`, 앱 줄은 `시각⇥요청ID⇥레벨⇥메시지`."""
    if message.startswith(_CONTROL[:3]):
        m = _UUID.search(message)
        return None if m is None else m.group(0)
    parts = message.split("\t", 3)
    if len(parts) == 4 and _UUID.fullmatch(parts[1]):
        return parts[1]
    m = _UUID.search(message)  # 시간 초과 줄처럼 탭이 없는 줄
    return None if m is None else m.group(0)


def _latest_report(
    events: list[tuple[int, str, str | None]],
) -> tuple[int, str, str] | None:
    reports = [e for e in events if e[1].startswith("REPORT RequestId:") and e[2]]
    if not reports:
        return None
    ts, msg, rid = max(reports, key=lambda e: e[0])
    assert rid is not None
    return ts, msg, rid


def _has_start(events: list[tuple[int, str, str | None]], rid: str) -> bool:
    return any(e[2] == rid and e[1].startswith("START RequestId:") for e in events)


def _message(line: str) -> str:
    """텍스트 형식의 메시지 칸, JSON 오류 줄은 `errorMessage` 만."""
    parts = line.split("\t", 3)
    msg = parts[3] if len(parts) == 4 else line
    brace = msg.find("{")
    if brace >= 0:
        data = _json_object(msg[brace:].strip())
        if isinstance(data.get("errorMessage"), str):
            return data["errorMessage"]
    return msg.strip()
