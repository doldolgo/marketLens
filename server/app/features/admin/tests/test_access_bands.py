"""IP 대역별 목록의 집계 — 대역 글자·종류·상태·botPct·페이지·짝·spark·경로·상위 자르기·파일당 상한 (스펙 067 §3.1·§3.2·§4)."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.features.admin import access_bands
from app.features.admin.access_bands import ips_query
from app.features.admin.access_cache import AccessLog
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    DAY,
    EDGE,
    FIREFOX,
    GATE,
    GOOGLEBOT,
    HOUR,
    IPHONE,
    KAKAO,
    SAMSUNG,
    line,
    rotated,
    write,
)

START = AFTER - AFTER % HOUR - 23 * HOUR  # 24시간 창의 시작 — KST 어제 11:00
DAY_A = GATE + 9 * DAY  # KST 10-20 00:00
# KST 10-21 10:30 — 24시간 창은 KST 10-20 11:00 부터, 7일 창은 KST 10-14 11:00 부터(8칸)
NOW7 = GATE + 10 * DAY + 10 * HOUR + 1800
START7 = NOW7 - NOW7 % HOUR - 167 * HOUR
# KST 11-11 10:30 — 30일 창은 KST 10-12 11:00 부터(31칸)
LATE = GATE + 31 * DAY + 10 * HOUR + 1800
CURL = "curl/8.7.1"


def ips(
    directory: Path, now: float = AFTER, window: str = "24h", **params: str
) -> dict[str, Any]:
    """새 캐시 하나로 창 하나의 목록."""
    return AccessLog(str(directory), GATE).ips(window, now, ips_query(**params))[0]


def by_band(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["band"]: row for row in body["bands"]}


# --- 대역 (§3.1) ----------------------------------------------------------------------


def test_bands_are_the_masked_ip_with_its_length_and_lines_without_ip_are_none(
    tmp_path: Path,
) -> None:
    t = AFTER - 2 * HOUR
    write(
        tmp_path,
        "access.log",
        [
            line(t, "/", ip="203.0.113.0"),
            line(
                t + 1, "/", ip="203.0.113.77"
            ),  # 가려지지 않은 줄도 같은 대역으로 접힌다
            line(t + 2, "/", ip="2001:db8:1::"),
            line(t + 3, "/", ip="2001:db8:1:ff::7"),
            line(t + 4, "/", ip=None),
            line(t + 5, "/", ip="not-an-ip"),
        ],
    )
    body = ips(tmp_path)
    assert (body["window"], body["total"]) == ("24h", 4)
    rows = by_band(body)
    assert {band: row["requests"] for band, row in rows.items()} == {
        "203.0.113.0/24": 2,
        "2001:db8:1::/48": 2,
        "(없음)": 1,
        "(기타)": 1,
    }
    assert rows["(없음)"]["pairs"] == 0  # IP 가 없으면 짝도 없다(038)
    text = json.dumps(body)
    for unmasked in ("203.0.113.77", "ff::7", "not-an-ip"):
        assert unmasked not in text


def test_kinds_status_and_bot_share_follow_the_038_line_rules(tmp_path: Path) -> None:
    t = AFTER - 2 * HOUR
    write(
        tmp_path,
        "access.log",
        [
            line(t, "/", ua=CHROME),
            line(
                t + 1, "/wp-login.php", ua=CHROME, status=404
            ),  # 탐색한 브라우저 → scanner
            line(t + 2, "/", ua=GOOGLEBOT),
            line(t + 3, "/", ua=CURL, status=301),
            line(t + 4, "/", ua="GPTBot/1.0"),
            line(t + 5, "/", ua="kakaotalk-scrap/1.0"),
            line(t + 6, "/", ua=CHROME, referer="http://localhost:5173"),  # 운영자
            line(t + 7, "/", ua=""),
            line(t + 8, "/api/ws/spreads", ua=CHROME, status=101),  # 상태 칸 밖
            line(
                t + 9, "/api/ws/spreads", ua=CHROME, status=502
            ),  # WS 5xx 는 5xx 칸 밖(038)
            line(t + 10, "/", ua=CHROME, status=500),
            line(t + 11, "/", ua=CHROME, status=999),  # 칸 밖
        ],
    )
    (row,) = ips(tmp_path)["bands"]
    assert row["requests"] == 12
    assert row["kinds"] == {
        "browser": 5,
        "search": 1,
        "ai": 1,
        "preview": 1,
        "tool": 1,
        "scanner": 1,
        "operator": 1,
        "unknown": 1,
    }
    assert row["status"] == {"2xx": 6, "3xx": 1, "4xx": 1, "5xx": 1}
    # (scanner + tool + ai + search) ÷ 요청 × 100 — preview·operator·unknown 은 분자에 없다
    assert row["botPct"] == round(4 / 12 * 100, 1) == 33.3


def test_pages_are_human_shaped_js_is_the_browser_loader_and_ws_any_upgrade(
    tmp_path: Path,
) -> None:
    t = AFTER - 2 * HOUR
    write(
        tmp_path,
        "access.log",
        [
            line(t, "/", ua=IPHONE),
            line(t + 1, "/privacy", ua=IPHONE, status=304),  # 304 도 페이지(038)
            line(t + 2, "/", ua=GOOGLEBOT),  # 봇의 페이지
            line(t + 3, "/assets/a.js", ua=IPHONE),
            line(t + 4, "/clarity.js", ua=IPHONE),
            line(t + 5, "/app/clarity.js", ua=CURL),  # 도구가 받은 로더
            line(t + 6, "/api/ws/spreads", ua=IPHONE, status=101),
            line(t + 7, "/api/ws/spreads", ua=CURL, status=101),
        ],
    )
    (row,) = ips(tmp_path)["bands"]
    assert (row["pages"], row["js"], row["ws"]) == (2, 1, 2)


def test_pairs_are_distinct_user_agents_per_kst_day_inside_the_window(
    tmp_path: Path,
) -> None:
    ua1, ua2, ua3 = (f"{CHROME} u{i}" for i in (1, 2, 3))
    rotated(
        tmp_path,
        [
            line(DAY_A + 8 * HOUR, "/", ua=ua3),  # 24시간 창 앞(같은 KST 날)
            line(DAY_A + 15 * HOUR, "/", ua=ua1),
            line(DAY_A + 15 * HOUR + 60, "/clarity.js", ua=ua1),  # 같은 짝 두 줄 = 1
            line(DAY_A + 16 * HOUR, "/", ua=ua2),
        ],
    )
    write(
        tmp_path,
        "access.log",
        [
            line(DAY_A + 20 * HOUR, "/", ua=ua1),  # 같은 날을 두 파일이 나눔 = 1
            line(DAY_A + DAY + 9 * HOUR, "/", ua=ua1),  # 다른 날 = 날마다 따로
        ],
    )
    assert ips(tmp_path, NOW7)["bands"][0]["pairs"] == 3  # ua1·ua2(어제) + ua1(오늘)
    assert ips(tmp_path, NOW7, "7d")["bands"][0]["pairs"] == 4  # + ua3


def test_a_band_with_no_line_in_the_window_is_not_listed(tmp_path: Path) -> None:
    # 같은 회전 파일의 창 앞 줄 — 7일 창에는 들고 24시간 창에는 없다
    rotated(
        tmp_path,
        [line(DAY_A + 8 * HOUR, "/", ip="10.0.7.0"), line(DAY_A + 15 * HOUR, "/")],
    )
    assert [r["band"] for r in ips(tmp_path, NOW7)["bands"]] == ["203.0.113.0/24"]
    week = ips(tmp_path, NOW7, "7d")
    assert week["total"] == 2 and by_band(week)["10.0.7.0/24"]["requests"] == 1


def test_spark_is_hourly_for_24h_and_kst_days_for_7d_and_30d(tmp_path: Path) -> None:
    rotated(
        tmp_path,
        [
            line(LATE - 40 * DAY, "/"),  # 어느 창에도 없다
            line(START7 - 2 * HOUR, "/"),  # 7일 창 앞
            line(START7 + 60, "/"),
            line(START7 + 120, "/"),
            line(DAY_A + 15 * HOUR, "/"),
        ],
    )
    write(tmp_path, "access.log", [line(NOW7 - 60, "/")])
    (week,) = ips(tmp_path, NOW7, "7d")["bands"]
    assert week["spark"] == [2, 0, 0, 0, 0, 0, 1, 1]  # KST 10-14 … 10-21
    assert (week["requests"], week["first"], week["last"]) == (
        4,
        START7 + 60,
        NOW7 - 60,
    )
    (day,) = ips(tmp_path, NOW7, "24h")["bands"]
    assert len(day["spark"]) == 24 and sum(day["spark"]) == day["requests"] == 2
    assert day["spark"][23] == 1 and day["first"] == DAY_A + 15 * HOUR
    write(tmp_path, "access.log", [line(LATE - 60, "/")])
    (month,) = ips(tmp_path, LATE, "30d")["bands"]
    assert len(month["spark"]) == 31 and month["spark"][-1] == 1  # KST 10-12 … 11-11
    assert sum(month["spark"]) == month["requests"] == 5


def test_paths_drop_the_query_and_the_tops_keep_three_by_count(tmp_path: Path) -> None:
    t = AFTER - 2 * HOUR
    uris = ["/app/?tab=history&fbclid=SECRET"] * 3 + ["/", "/"] + ["/privacy?x=1"] * 2
    uas = [IPHONE] * 4 + [CHROME] * 2 + [FIREFOX]
    long = "/" + "x" * 150 + "?q=1"
    lines = [
        line(t + i, uri, ua=ua)
        for i, (uri, ua) in enumerate(zip(uris, uas, strict=True))
    ]
    write(tmp_path, "access.log", [*lines, line(t + 9, long, ip="10.0.9.0")])
    rows = by_band(ips(tmp_path))
    row = rows["203.0.113.0/24"]
    assert row["topPaths"] == [["/app/", 3], ["/", 2], ["/privacy", 2]]
    assert row["topAgents"] == [
        ["mobile·ios·safari", 4],
        ["desktop·windows·chrome", 2],
        ["desktop·macos·firefox", 1],
    ]
    assert rows["10.0.9.0/24"]["topPaths"] == [["/" + "x" * 99, 1]]  # 경로 100자
    assert "SECRET" not in json.dumps(rows) and "?" not in json.dumps(rows)
    assert row["last"] == t + 6


def test_a_file_keeps_the_top_ten_paths_and_five_browser_names_then_other(
    tmp_path: Path,
) -> None:
    t = AFTER - 3 * HOUR
    # 한 번씩 열둘 + 네 번 하나 — 파일을 다 읽으면 수가 큰 순(같으면 이름순) 열 개, 나머지 셋은 (기타)
    paths = [line(t + i, f"/p{i}") for i in range(12)]
    paths += [line(AFTER - 60 - i, "/p12") for i in range(4)]  # 늦게 와도 수로 고른다
    agents = [CHROME, EDGE, SAMSUNG, IPHONE, FIREFOX, CURL, KAKAO]
    names = [
        line(t + 20 + i, "/", ua=ua, ip="198.51.100.0") for i, ua in enumerate(agents)
    ]
    write(tmp_path, "access.log", paths + names)
    rows = by_band(ips(tmp_path))
    assert rows["203.0.113.0/24"]["topPaths"] == [
        ["/p12", 4],
        ["(기타)", 3],
        ["/p0", 1],
    ]
    assert rows["203.0.113.0/24"]["requests"] == 16
    # 다섯 뒤 둘(이름순으로 뒤인 mobile·ios 둘)은 (기타)
    assert rows["198.51.100.0/24"]["topAgents"] == [
        ["(기타)", 2],
        ["desktop·macos·firefox", 1],
        ["desktop·other·other", 1],
    ]


def test_tops_come_from_whole_files_that_have_lines_in_the_window(
    tmp_path: Path,
) -> None:
    # 회전 파일 하나가 24시간 창 경계를 걸친다 — 수는 창 안 줄만, 상위 목록은 그 파일 것(근사값 — 상세가 다시 읽는다)
    rotated(tmp_path, [line(START - HOUR, "/before"), line(START + HOUR, "/inside")])
    (row,) = ips(tmp_path)["bands"]
    assert row["requests"] == 1
    assert row["topPaths"] == [["/before", 1], ["/inside", 1]]


def test_a_file_keeps_50000_bands_and_puts_the_rest_in_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert access_bands.BAND_CAP == 50_000
    monkeypatch.setattr(access_bands, "BAND_CAP", 2)
    t = AFTER - 3 * HOUR
    rotated(tmp_path, [line(t + i, "/", ip=f"10.0.{i}.0") for i in range(4)])
    write(
        tmp_path, "access.log", [line(AFTER - 60, "/", ip="10.0.2.0")]
    )  # 파일마다 따로 센다
    rows = by_band(ips(tmp_path))
    assert {band: row["requests"] for band, row in rows.items()} == {
        "10.0.0.0/24": 1,
        "10.0.1.0/24": 1,
        "(기타)": 2,
        "10.0.2.0/24": 1,
    }
    assert (rows["(기타)"]["country"], rows["(기타)"]["net"]) == (None, None)
