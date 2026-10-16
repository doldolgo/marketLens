"""Clarity 녹화 목록 값 다듬기 (스펙 077 §3.1·§3.2·§4 'server') — 길이·시각·링크·주소·상한·요청 몸통.

녹화 하나의 모양은 2026-10-16 운영 토큰으로 받은 응답의 키·값 꼴을 따랐다(값은 지어냈다). 피드 쪽은 test_clarity_recordings_feed.py.
"""

import json
from typing import Any

import pytest

from app.features.admin import clarity_recordings as rec

LINK = "https://clarity.microsoft.com/player/abc123/9f8e7d/1x2y3z?date=2026-10-15"
PART = ["state", "code", "fetchedAt", "refreshSec", "rows", "days"]
ROW = ["startedAt", "totalSec", "activeSec", "pages", "clicks", "link", "timeline"]


def recording(**over: Any) -> dict[str, Any]:
    raw = {
        "link": LINK,
        "timestamp": "2026-10-15 03:04:05",
        "totalDuration": "1 minute 3 seconds",
        "activeDuration": "52 seconds",
        "pagesCount": 2,
        "sessionClickCount": 4,
        "device": "PC",  # 모르는 키 — 버린다
        "timeline": [
            {
                "url": "https://kimptrack.com/app/?tab=history&sym=BTC#top",
                "displayTitle": "KimpTrack — 김프 대시보드",
                "duration": 41000,
                "start": "00:00",
                "pageLoadTime": 812,
                "referrerUrl": "https://www.google.com/search?q=%EA%B9%80%ED%94%84",
                "timelineEvents": [
                    {"eventtype": "Click", "text": "기록", "start": "00:07"},
                    {"eventtype": "Scroll", "start": "00:12", "extra": 1},
                ],
            },
            {
                "url": "https://kimptrack.com/privacy?from=footer",
                "displayTitle": "개인정보 처리방침",
                "duration": 22000,
                "start": "00:41",
                "pageLoadTime": 300,
                "referrerUrl": "",
                "timelineEvents": [],
            },
        ],
    }
    raw.update(over)
    return raw


# --- 값 다듬기 ---


@pytest.mark.parametrize(
    ("text", "sec"),
    [
        ("52 seconds", 52),
        ("1 second", 1),
        ("1 minute 3 seconds", 63),
        ("2 hours 5 minutes", 7500),
        ("1 hour, 0 minutes, 9 seconds", 3609),
        ("0 seconds", 0),
        ("", None),
        ("soon", None),
        ("52 seconds later", None),
        ("52", None),
        (52, None),
        (None, None),
    ],
)
def test_duration_text_becomes_seconds_or_null(text: Any, sec: int | None) -> None:
    assert rec.seconds_of(text) == sec


@pytest.mark.parametrize(
    ("text", "sec"),
    [
        ("00:07", 7),
        ("12:34", 754),
        ("1:02:03", 3723),
        ("0:60", None),
        ("7", None),
        (7, None),
    ],
)
def test_clock_text_becomes_seconds(text: Any, sec: int | None) -> None:
    assert rec.clock_of(text) == sec


def test_timestamp_is_read_as_utc_and_bad_ones_are_null() -> None:
    assert rec.stamp_ms("2026-10-15 03:04:05") == 1_792_033_445_000
    assert rec.stamp_ms("2026-10-15T03:04:05") == 1_792_033_445_000
    for bad in ("2026-13-01 00:00:00", "15/10/2026 03:04", "", None, 1_792_033_445):
        assert rec.stamp_ms(bad) is None, bad


@pytest.mark.parametrize(
    "link",
    [
        LINK,
        "https://clarity.microsoft.com/player/abc/def/ghi",
        "https://clarity.microsoft.com/projects/view/abc/impressions?date=Last%203%20days",
    ],
)
def test_clarity_links_pass(link: str) -> None:
    assert rec.link_of(link) == link


@pytest.mark.parametrize(
    "link",
    [
        "http://clarity.microsoft.com/player/abc",
        "https://clarity.microsoft.com.evil.example/player",
        "https://evil.example/https://clarity.microsoft.com/",
        "https://clarity.microsoft.com@evil.example/",
        "https://clarity.microsoft.com/player/a b",
        'https://clarity.microsoft.com/player/"onmouseover=x',
        "https://clarity.microsoft.com/player/<script>",
        "https://clarity.microsoft.com/player#frag",
        "javascript:alert(1)",
        "https://clarity.microsoft.com/" + "a" * 471,  # 501자
        "",
        None,
        42,
    ],
)
def test_other_links_are_dropped(link: Any) -> None:
    assert rec.link_of(link) is None


def test_link_limit_is_five_hundred_characters() -> None:
    edge = "https://clarity.microsoft.com/" + "a" * 470
    assert len(edge) == 500 and rec.link_of(edge) == edge


def test_a_row_keeps_known_keys_reduces_addresses_and_drops_the_rest() -> None:
    body = rec.parse(json.dumps([recording()]).encode())
    (row,) = body["rows"]
    assert list(row) == ROW
    assert row["startedAt"] == 1_792_033_445_000
    assert (row["totalSec"], row["activeSec"], row["pages"], row["clicks"]) == (
        63,
        52,
        2,
        4,
    )
    assert row["link"] == LINK
    first, second = row["timeline"]
    assert (
        first
        == {
            "url": "https://kimptrack.com/app/?tab=history",  # 040 — 대시보드는 탭만, 해시 뗌
            "title": "KimpTrack — 김프 대시보드",
            "atSec": 0,
            "stayMs": 41000,
            "loadMs": 812,
            "referrer": "https://www.google.com",  # 출처만 — 검색어 없음
            "events": [
                {"type": "Click", "text": "기록", "atSec": 7},
                {"type": "Scroll", "text": None, "atSec": 12},
            ],
        }
    )
    assert second["url"] == "https://kimptrack.com/privacy"
    assert second["referrer"] is None and second["events"] == []
    assert "PC" not in json.dumps(body) and "%EA%B9%80" not in json.dumps(body)


def test_wrong_values_become_null_without_dropping_the_row() -> None:
    raw = recording(
        timestamp="어제",
        totalDuration=63,
        activeDuration=None,
        pagesCount=True,  # 불은 정수가 아니다
        sessionClickCount=-1,
        link="https://evil.example/",
        timeline="no",
    )
    (row,) = rec.parse(json.dumps([raw]).encode())["rows"]
    assert row == dict.fromkeys(ROW[:-1]) | {"timeline": []}


def test_text_is_cleaned_and_cut() -> None:
    event = {
        "eventtype": "C" * 40,
        "text": "a\u0000b\n\tc  d" + "x" * 80,
        "start": "00:01",
    }
    page = {
        "displayTitle": "t" * 150,
        "timelineEvents": [event],
        "url": "https://kimptrack.com/",
    }
    (row,) = rec.parse(json.dumps([recording(timeline=[page])]).encode())["rows"]
    (out,) = row["timeline"][0]["events"]
    assert out["type"] == "C" * 30
    assert out["text"].startswith("a b c d") and len(out["text"]) == 60
    assert row["timeline"][0]["title"] == "t" * 100


def test_limits_rows_pages_and_events() -> None:
    event = {"eventtype": "Click", "text": "x", "start": "00:01"}
    page = {"url": "https://kimptrack.com/", "timelineEvents": [event] * 70}
    many = [
        recording(timestamp=f"2026-10-15 {h:02d}:{m:02d}:00", timeline=[page] * 40)
        for h in range(3)
        for m in range(50)
    ]
    rows = rec.parse(json.dumps(many).encode())["rows"]
    assert len(rows) == rec.COUNT == 100
    assert all(len(r["timeline"]) == 30 for r in rows)
    assert all(len(p["events"]) == 50 for p in rows[0]["timeline"])
    # 시작 최근 순 — 마지막 행이 가장 오래된 것보다는 늦다
    starts = [r["startedAt"] for r in rows]
    assert starts == sorted(starts, reverse=True)
    assert starts[0] == rec.stamp_ms("2026-10-15 02:49:00")


def test_rows_without_a_start_go_last_and_non_objects_are_skipped() -> None:
    raw = [recording(timestamp=None), "x", 3, recording()]
    rows = rec.parse(json.dumps(raw).encode())["rows"]
    assert [r["startedAt"] for r in rows] == [1_792_033_445_000, None]


def test_a_wrapped_list_is_accepted_and_other_shapes_are_bad_data() -> None:
    assert (
        len(rec.parse(json.dumps({"recordings": [recording()]}).encode())["rows"]) == 1
    )
    for bad in (b"{}", b'{"a": [], "b": []}', b'"x"', b"null", b"<html>"):
        with pytest.raises(ValueError):
            rec.parse(bad)


def test_request_body_is_the_window_newest_first_and_a_hundred() -> None:
    body = rec.request_body(1_792_033_445_123, 3)
    assert body == {
        "sortBy": 0,
        "start": "2026-10-12T03:04:05.123Z",
        "end": "2026-10-15T03:04:05.123Z",
        "filters": {
            "date": {
                "start": "2026-10-12T03:04:05.123Z",
                "end": "2026-10-15T03:04:05.123Z",
            }
        },
        "count": 100,
    }


@pytest.mark.parametrize(
    ("value", "days"),
    [
        ("1", 1),
        ("3", 3),
        ("7", 7),
        ("30", 30),
        (None, 3),
        ("", 3),
        ("2", 3),
        ("90", 3),
        ("x", 3),
        ("-1", 3),
    ],
)
def test_days_outside_the_list_are_three(value: str | None, days: int) -> None:
    assert rec.days_of(value) == days
