"""`/admin/clarity` 기본 요약의 정규화 — `summary`·`countries`·`metrics`·대시보드 주소의 탭 (스펙 040 §3.3·§4).

가짜 Data Export 의 응답을 바꿔 가며 피드를 부르고 응답 값을 본다.
"""

from typing import Any

import pytest

from app.features.admin.tests.clarity_fakes import (
    SESSIONS_EXPORT,
    SESSIONS_SUMMARY,
    Clarity,
    World,
    documented,
    signal,
)

NULL_SIGNAL = {"sessions": None, "sessionPct": None, "pageViews": None, "count": None}


async def base(export: Any) -> dict[str, Any]:
    return await World(Clarity((200, export))).get()


@pytest.mark.parametrize("spelling", ["real", "documented"])
async def test_summary_reads_real_and_documented_names_alike(spelling: str) -> None:
    export = SESSIONS_EXPORT if spelling == "real" else documented(SESSIONS_EXPORT)
    body = await base(export)
    assert body["state"] == "ok" and body["summary"] == SESSIONS_SUMMARY
    assert body["traffic"] == {
        "sessions": 42,
        "botSessions": 3,
        "users": 37,
        "pagesPerSession": 1.8571,  # 035 그대로 — 반올림 없음
    }
    assert body["countries"] == [["South Korea", 30], ["Canada", 5], ["Japan", 5]]


async def test_a_missing_metric_leaves_its_cells_null_and_the_shape_whole() -> None:
    export = [
        m
        for m in SESSIONS_EXPORT
        if m["metricName"] not in ("RageClickCount", "EngagementTime")
    ]
    summary = (await base(export))["summary"]
    assert summary == {
        **SESSIONS_SUMMARY,
        "totalSec": None,
        "activeSec": None,
        "signals": {**SESSIONS_SUMMARY["signals"], "rageClick": NULL_SIGNAL},
    }
    assert list(summary["signals"]) == [
        "deadClick",
        "rageClick",
        "excessiveScroll",
        "quickback",
        "scriptError",
        "errorClick",
    ]


async def test_empty_answer_keeps_the_summary_shape_with_nulls() -> None:
    body = await base([])
    assert body["summary"] == {
        "scrollDepth": None,
        "totalSec": None,
        "activeSec": None,
        "signals": dict.fromkeys(SESSIONS_SUMMARY["signals"], NULL_SIGNAL),
    }
    assert body["countries"] is None and body["metrics"] == []


@pytest.mark.parametrize("bad", [2.5, "2.5", "abc", "NaN", "Infinity", True, None, [1]])
async def test_integer_cells_take_integers_only(bad: Any) -> None:
    export = [
        {"metricName": "DeadClickCount", "information": [signal(bad, bad, bad, bad)]}
    ]
    dead = (await base(export))["summary"]["signals"]["deadClick"]
    expected_pct = 2.5 if bad in (2.5, "2.5") else None  # 실수 칸은 유한한 수면 쓴다
    assert dead == {**NULL_SIGNAL, "sessionPct": expected_pct}


async def test_number_strings_and_integral_floats_read_as_numbers() -> None:
    export = [
        {
            "metricName": "ScrollDepth",
            "information": [{"averageScrollDepth": "33.336"}],
        },
        {
            "metricName": "DeadClickCount",
            "information": [signal(" 7 ", "1e1", 4.0, "0")],
        },
    ]
    summary = (await base(export))["summary"]
    assert summary["scrollDepth"] == 33.34
    assert summary["signals"]["deadClick"] == {
        "sessions": 7,
        "sessionPct": 10.0,
        "pageViews": 4,
        "count": 0,
    }


@pytest.mark.parametrize("junk", [{}, "x", b"<html>not json</html>", b"[1, 2"])
async def test_wrong_bodies_are_bad_data_and_keep_the_last_success(junk: Any) -> None:
    w = World(Clarity((200, SESSIONS_EXPORT), (200, junk)))
    ok = await w.get()
    w.advance(4)
    body = await w.get()
    assert (body["state"], body["code"]) == ("error", "bad_data")
    assert body["fetchedAt"] == ok["fetchedAt"]
    for key in ("traffic", "summary", "countries", "metrics"):
        assert body[key] == ok[key], key
    assert w.stored()["values"]["summary"] == SESSIONS_SUMMARY
