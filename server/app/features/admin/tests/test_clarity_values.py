"""`/admin/clarity` 기본 요약의 정규화 — `summary`·`countries`·`metrics`·대시보드 주소의 탭 (스펙 040 §3.3·§4).

가짜 Data Export 의 응답을 바꿔 가며 피드를 부르고 응답 값을 본다.
"""

import json
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


# --- countries ---


def countries(rows: list[Any], name: str = "Country") -> list[dict[str, Any]]:
    return [{"metricName": name, "information": rows}]


@pytest.mark.parametrize(
    ("metric", "key"),
    [
        ("Country", "Country"),
        ("Country/Region", "countryRegion"),
        ("Country", "name"),
        ("CountryRegion", "country"),
    ],
)
async def test_country_names_come_from_any_known_key(metric: str, key: str) -> None:
    rows = [
        {key: "Korea", "sessionsCount": "4"},
        {key: "Japan", "totalSessionCount": 9},
    ]
    assert (await base(countries(rows, metric)))["countries"] == [
        ["Japan", 9],
        ["Korea", 4],
    ]


async def test_country_rows_with_unknown_keys_need_exactly_one_text_value() -> None:
    rows = [
        {"region": "Korea", "sessions": "6"},  # 글자 값 하나 — 그 값
        {"region": "Japan", "code": "JP", "count": 3},  # 글자 값 둘 — 버림
        {"label": "Canada", "visits": "2", "sessionsCount": "2"},  # 숫자 글자는 수
        {
            "country": 7,
            "sessionsCount": 1,
        },  # 아는 키가 글자가 아니고 다른 글자 값도 없음 — 버림
        {"country": "France"},  # 세션이 없음 — 버림
        "not a row",
    ]
    assert (await base(countries(rows)))["countries"] == [["Korea", 6], ["Canada", 2]]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [([], []), ([{"a": "x", "b": "y"}, {"country": "Korea"}], None)],
)
async def test_country_rows_none_known_is_null_and_no_rows_is_empty(
    rows: list[Any], expected: Any
) -> None:
    assert (await base(countries(rows)))["countries"] == expected


async def test_countries_keep_twenty_by_sessions_then_name_and_long_names_cut() -> None:
    rows = [{"name": f"C{i:02d}", "sessionsCount": str(i % 5)} for i in range(25)]
    rows.append({"name": "Z" * 300, "sessionsCount": "9"})
    got = (await base(countries(rows)))["countries"]
    assert len(got) == 20
    assert got[0] == ["Z" * 200, 9]
    assert got[1:6] == [["C04", 4], ["C09", 4], ["C14", 4], ["C19", 4], ["C24", 4]]
    assert [s for _, s in got] == sorted((s for _, s in got), reverse=True)


async def test_no_country_metric_is_null() -> None:
    assert (await base(SESSIONS_EXPORT[:3]))["countries"] is None


# --- metrics ---


async def test_metrics_keep_the_normalized_metrics_as_received() -> None:
    body = await base(SESSIONS_EXPORT)
    names = [m["name"] for m in body["metrics"]]
    assert names == [m["metricName"] for m in SESSIONS_EXPORT[1:]]  # Traffic 만 빠진다
    scroll = body["metrics"][0]
    assert scroll["rows"] == [{"averageScrollDepth": 57.456}]  # 받은 키·값 그대로
    pages = body["metrics"][-1]
    assert pages["rows"] == [{"url": "https://kimptrack.com/app/?tab=gap"}]


ADDRESSES = [
    (
        "https://kimptrack.com/app/?tab=history&h.dir=reverse&sym=ETH#x",
        "https://kimptrack.com/app/?tab=history",
    ),
    (
        "HTTPS://WWW.KimpTrack.com./app/?sym=BTC&tab=gap",
        "https://WWW.KimpTrack.com./app/?tab=gap",
    ),
    ("https://kimptrack.com/app/?tab=zzz", "https://kimptrack.com/app/"),
    ("https://kimptrack.com/app/?tab=", "https://kimptrack.com/app/"),
    ("https://kimptrack.com/app/?TAB=gap", "https://kimptrack.com/app/"),
    ("https://kimptrack.com/app/?tab=Gap", "https://kimptrack.com/app/"),
    ("https://kimptrack.com/app/?tab=g%61p", "https://kimptrack.com/app/"),
    ("https://kimptrack.com/app/?tab=zzz&tab=gap", "https://kimptrack.com/app/"),
    ("https://kimptrack.com/app/#x?tab=gap", "https://kimptrack.com/app/"),
    ("https://evil.example/app/?tab=history", "https://evil.example/app/"),
    (
        "https://kimptrack.com.evil.example/app/?tab=pp",
        "https://kimptrack.com.evil.example/app/",
    ),
    (
        "https://kimptrack.com/app/index.html?tab=pp",
        "https://kimptrack.com/app/index.html",
    ),
    ("https://kimptrack.com/app?tab=pp", "https://kimptrack.com/app"),
    ("https://kimptrack.com/?tab=gap", "https://kimptrack.com/"),
    (
        "https://u:p@kimptrack.com:443/app/?tab=health&x=1",
        "https://kimptrack.com:443/app/?tab=health",
    ),
    ("kimptrack.com/app/?tab=flow&x=1", "kimptrack.com/app/?tab=flow"),
    ("www.kimptrack.com./app/?tab=pp&s=1", "www.kimptrack.com./app/?tab=pp"),
    ("/app/?tab=gap&sym=BTC", "/app/"),  # 호스트가 없으면 탭도 남기지 않는다
]


async def test_dashboard_addresses_keep_only_an_allowed_tab_id() -> None:
    rows = [{"url": raw} for raw, _ in ADDRESSES] + [
        {"https://kimptrack.com/app/?tab=spread&sym=SECRET": "1"}
    ]
    export = [{"metricName": "PopularPages", "information": rows}]
    w = World(Clarity((200, export)))
    body = await w.get()
    got = body["metrics"][0]["rows"]
    assert got[:-1] == [{"url": want} for _, want in ADDRESSES]
    assert got[-1] == {"https://kimptrack.com/app/?tab=spread": "1"}  # 키에 든 주소도
    stored = json.dumps(w.stored())
    for leak in ("sym=", "SECRET", "h.dir", "&x=1", "&s=1", "u:p@"):
        assert leak not in stored and leak not in json.dumps(body), leak


async def test_referrer_addresses_become_origins_even_for_the_dashboard() -> None:
    rows = [
        {"url": "https://kimptrack.com/app/?tab=history"},
        {"url": "kimptrack.com/app/?tab=flow"},
    ]
    body = await base([{"metricName": "ReferrerUrl", "information": rows}])
    assert body["metrics"][0]["rows"] == [
        {"url": "https://kimptrack.com"},
        {"url": "kimptrack.com"},
    ]
