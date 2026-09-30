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
    NOW,
    Clients,
    alarms_response,
    history_item,
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
