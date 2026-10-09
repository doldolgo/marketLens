"""관리자 화면 v3 의 계산 — 상태 띠 규칙·큰 수·흐름 그림·바이트 단위·툴팁 글자 (스펙 064 §4 node).

`web/admin` 의 모듈을 node 에 가짜 document 와 싣고(보이지 않는 탭 — 요청 0) 공개한 계산 함수만 부른다.
그리기 모양·폭·대비는 설계 세션이 브라우저로 본다(§4 마지막 줄).
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from tests.admin_dom import run_admin

NOW = int(datetime(2026, 10, 9, 6, 0, 10, tzinfo=UTC).timestamp() * 1000)
END = NOW // 1000 // 300 * 300  # 시계열 끝(주기로 내림)


def _pts(value: float | None, step: int = 300) -> list[list[float | None]]:
    """주기 격자의 끝 세 칸이 value, 그 앞 칸은 99 — 끝 앞 15분(주기가 길면 마지막 칸) 밖이라 평균에 들면 안 된다."""
    return [[END - 4 * step, 99.0]] + [[END - k * step, value] for k in (3, 2, 1)]


def _series(box: str = "data", period: int = 300, **values: Any) -> dict:
    """입력 하나 — AWS 시계열(063) 결과만 있는 페이지."""
    series = {k: _pts(v, period) for k, v in values.items()}
    body = {
        "state": "ok",
        "endTs": END,
        "periodSec": period,
        "boxes": [{"box": box, "series": series}],
    }
    return {"series": {"body": body}}


def _access(window: str = "24h", **extra: Any) -> dict:
    """입력 하나 — 접속 요약(038) 결과만 있는 페이지."""
    body = {
        "state": "ok",
        "window": window,
        "endTs": NOW // 1000,
        "recent5xx": [],
        "status": {},
        "hourly": [],
        **extra,
    }
    return {"access": {"body": body}}


def _fives(n: int, age: int = 60) -> list[dict]:
    return [{"ts": NOW // 1000 - age - i, "path": "/", "status": 502} for i in range(n)]


OK = {"body": {"status": "ok"}}
# 경우마다 [입력, 기대 칩 [[색, 이름, 값]]] — 없는 키는 판정하지 않는다
CASES: list[tuple[dict, list[list[str]]]] = [
    (
        {
            "collector": OK,
            "api": OK,
            "status": {"body": {"redis": "ok", "influx": "ok"}},
        },
        [],
    ),
    ({}, []),
    ({"collector": {"body": {"status": "stale"}}}, [["bad", "수집 멈춤", "stale"]]),
    ({"collector": {"why": "불러오지 못함"}}, [["bad", "수집 멈춤", "응답 없음"]]),
    ({"api": {"body": {"status": "redis_down"}}}, [["bad", "API 이상", "redis_down"]]),
    (
        {"status": {"body": {"redis": "down", "influx": "down"}}},
        [["bad", "Redis 끊김", "down"], ["bad", "Influx 끊김", "down"]],
    ),
    # 거래소 — 열린 실패 구간은 빨강(성공률 주황은 겹쳐 내지 않는다), 99% 미만 주황, 99.0 은 정상(경계)
    (
        {
            "collect": {
                "body": {
                    "exchanges": [
                        {
                            "exchange": "upbit",
                            "openOutage": {"startedAt": NOW - 185_000},
                            "successRate1h": 90,
                        },
                        {"exchange": "okx", "successRate1h": 98.9},
                        {"exchange": "bithumb", "successRate1h": 99.0},
                        {"exchange": "constructor", "successRate1h": 50},
                    ]
                }
            }
        },
        [
            ["bad", "업비트 끊김", "3분"],
            ["warn", "OKX 성공률", "98.9%"],
            ["warn", "constructor 성공률", "50.0%"],
        ],
    ),
    # 경보·점검 — counts.alarm, ok 가 false 일 때만(null = 11분 안 실행 없음은 아니다), 연결 안 됨은 판정 밖
    (
        {
            "aws": {
                "body": {
                    "alarms": {"state": "ok", "counts": {"alarm": 2}, "items": []},
                    "canary": {"state": "ok", "ok": False, "lastRunAt": NOW - 120_000},
                }
            }
        },
        [["bad", "경보", "2"], ["bad", "점검 실패", "2분 전"]],
    ),
    (
        {
            "aws": {
                "body": {
                    "alarms": {"state": "unconfigured", "counts": None},
                    "canary": {"state": "ok", "ok": None, "lastRunAt": None},
                }
            }
        },
        [],
    ),
    # 상자 — 최근 15분 평균, 문턱 그대로(이상이면 그 색). 15분 밖의 99 는 평균에 들지 않는다
    (_series(cpu=90), [["bad", "data CPU", "90.0%"]]),
    (_series(cpu=89.9), [["warn", "data CPU", "89.9%"]]),
    (_series(cpu=75), [["warn", "data CPU", "75.0%"]]),
    (_series(cpu=74.9), []),
    (
        _series(mem=90, disk=85),
        [["bad", "data 메모리", "90.0%"], ["bad", "data 디스크", "85.0%"]],
    ),
    (
        _series(mem=80, disk=75),
        [["warn", "data 메모리", "80.0%"], ["warn", "data 디스크", "75.0%"]],
    ),
    (_series(mem=79.9, disk=74.9), []),
    (_series(creditBalance=9.9), [["bad", "data 크레딧", "9.9"]]),
    (_series(creditBalance=10), [["warn", "data 크레딧", "10.0"]]),
    (_series(creditBalance=29.9), [["warn", "data 크레딧", "29.9"]]),
    (_series(creditBalance=30, surplusCharged=0, statusFailed=0), []),
    (_series(creditBalance=None), []),
    (_series(surplusCharged=0.4), [["warn", "data 초과 과금", "0.4"]]),
    (_series(statusFailed=1), [["bad", "data 상태 검사", "실패"]]),
    # 주기가 15분보다 길면(7일 = 3600초) 마지막 칸으로 판정
    (_series(period=3600, cpu=95), [["bad", "data CPU", "95.0%"]]),
    # 접속 — 최근 1시간 5xx(recent5xx 의 ts) ≥ 10 빨강·≥ 1 주황, 1시간 밖은 세지 않음. WS 재접속 실패 ≥ 1 주황
    (_access(recent5xx=_fives(10)), [["bad", "5xx", "10"]]),
    (_access(recent5xx=_fives(9)), [["warn", "5xx", "9"]]),
    (_access(recent5xx=_fives(3, age=3601)), []),
    (_access(status={"ws5xx": 1}), [["warn", "WS 재접속 실패", "1"]]),
    (_access(status={"ws5xx": 0}), []),
    # 7·30일 창은 마지막 24시간 칸의 wsErrors 합(머리의 점이 창에 따라 바뀌지 않게)
    (
        _access(
            "7d",
            status={"ws5xx": 9},
            hourly=[
                {"ts": NOW // 1000 - 3 * 86_400, "wsErrors": 5},
                {"ts": NOW // 1000 - 3600, "wsErrors": 2},
            ],
        ),
        [["warn", "WS 재접속 실패", "2"]],
    ),
    # 예산 — 실제 ≥ 100% 빨강, 예측 ≥ 100% 주황, 한도 0 은 건너뜀
    (
        {
            "aws": {
                "body": {
                    "budget": {
                        "state": "ok",
                        "items": [
                            {"limit": 130, "actual": 130, "forecast": 200},
                            {"limit": 100, "actual": 50, "forecast": 100},
                            {"limit": 100, "actual": 50, "forecast": 99},
                            {"limit": 0, "actual": 5},
                        ],
                    }
                }
            }
        },
        [["bad", "예산 넘음", "100%"], ["warn", "예산 예측 넘음", "100%"]],
    ),
    # 최근 1시간 Slack 알림 ≥ 1 주황(경보 이력 줄은 세지 않는다)
    (
        {
            "alerts": {
                "body": {
                    "items": [
                        {"source": "slack", "at": NOW - 10_000},
                        {"source": "slack", "at": NOW - 3_700_000},
                        {"source": "alarm", "at": NOW - 10_000},
                    ]
                }
            }
        },
        [["warn", "Slack 알림", "1"]],
    ),
    # 심각한 순 — 빨강이 먼저(같은 색은 규칙 순서)
    (
        {
            "alerts": {"body": {"items": [{"source": "slack", "at": NOW - 1000}]}},
            "collector": {"body": {"status": "starting"}},
        },
        [["bad", "수집 멈춤", "starting"], ["warn", "Slack 알림", "1"]],
    ),
]

SCENARIO = r"""
setup({ visible: false, respond: () => [404, {}] })
const common = await page('common.js')
out.chips = input.cases.map((f) => common.problems(f, input.now).map((c) => [c.tone, c.name, c.value]))
out.recent = [
  common.recentAvg([[100, 1], [1000, 4], [1100, null], [1200, 8]], 1500, 300),
  common.recentAvg([], 1500, 300),
  common.recentAvg([[100, 1]], null, 300),
]
"""


@pytest.fixture(scope="module")
def got() -> dict[str, Any]:
    return run_admin(SCENARIO, {"now": NOW, "cases": [c for c, _ in CASES]})


def test_status_band_rules_red_amber_boundaries_and_missing_values(
    got: dict[str, Any],
) -> None:
    """§3.2 1 — 규칙마다 빨강·주황·경계값·값 없음, 칩 = [색, 짧은 이름, 값], 빨강 먼저."""
    for (case, expected), chips in zip(CASES, got["chips"], strict=True):
        assert chips == expected, (case, chips)


def test_recent_average_uses_only_the_last_fifteen_minutes(got: dict[str, Any]) -> None:
    """끝 1500 초 앞 15분 = 600 ≤ ts < 1500 의 값만(null 은 뺀다), 점이나 끝이 없으면 null."""
    assert got["recent"] == [6, None, None]
