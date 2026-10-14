"""IP 하나 단위 집계 — 가린 줄/전체 줄 판별(시작 시각 전후·`.0` 진짜 주소)·행 값·판정 신호 수·파일당 상한
(스펙 069 §3.2·§4 '집계')."""

import time
from pathlib import Path
from typing import Any

import pytest

from app.features.admin import access_addrs
from app.features.admin.access_addrs_page import addrs_query
from app.features.admin.access_cache import AccessLog
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    GATE,
    GOOGLEBOT,
    HOUR,
    line,
    rotated,
    write,
)

T = AFTER - 6 * HOUR  # 24시간 창 안
RELOAD = T + 2 * HOUR  # 첫 '끝 옥텟이 0 이 아닌 IPv4' 줄


def addrs(
    directory: Path, now: float = AFTER, window: str = "24h", **params: str
) -> dict[str, Any]:
    return AccessLog(str(directory), GATE).addrs(window, now, addrs_query(**params))[0]


def rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["ip"]: row for row in body["rows"]}


def test_lines_before_the_first_full_ipv4_are_bands_and_after_are_addresses(
    tmp_path: Path,
) -> None:
    rotated(
        tmp_path,
        [
            line(T, "/", ip="203.0.113.0"),  # 가린 줄
            line(T + 60, "/", ip="2001:db8:1::"),  # 가린 IPv6
            line(T + 120, "/", ip="198.51.100.0"),
        ],
    )
    write(
        tmp_path,
        "access.log",
        [
            line(
                RELOAD - 30, "/", ip="203.0.113.0"
            ),  # 같은 파일이어도 시작 시각 앞이면 가린 줄
            line(RELOAD, "/", ip="203.0.113.77"),
            line(
                RELOAD + 60, "/", ip="203.0.113.0"
            ),  # 시작 시각 뒤의 `.0` 은 진짜 주소
            line(
                RELOAD + 120, "/", ip="2001:db8:1::"
            ),  # 뒤가 0 인 IPv6 도 시작 시각 뒤면 진짜 주소
            line(RELOAD + 180, "/", ip="2001:db8:1:42::9"),
            line(RELOAD + 240, "/", ip=None),
            line(RELOAD + 300, "/", ip="not-an-ip"),
        ],
    )
    body = addrs(tmp_path)
    assert body["since"] == int(RELOAD)
    got = {ip: (row["masked"], row["requests"]) for ip, row in rows(body).items()}
    assert got == {
        "203.0.113.0/24": (True, 2),
        "2001:db8:1::/48": (True, 1),
        "198.51.100.0/24": (True, 1),
        "203.0.113.77": (False, 1),
        "203.0.113.0": (False, 1),
        "2001:db8:1::": (False, 1),
        "2001:db8:1:42::9": (False, 1),
        "(없음)": (False, 1),
        "(기타)": (False, 1),
    }
    assert rows(body)["(없음)"]["verdict"] == "unknown"


def test_without_a_full_ipv4_line_every_line_is_masked(tmp_path: Path) -> None:
    write(
        tmp_path,
        "access.log",
        [line(T, "/", ip="203.0.113.0"), line(T + 5, "/", ip="2001:db8:1::")],
    )
    body = addrs(tmp_path)
    assert body["since"] is None
    assert {row["ip"] for row in body["rows"]} == {"203.0.113.0/24", "2001:db8:1::/48"}


def test_an_earlier_file_moves_the_start_for_a_later_masked_shape_file(
    tmp_path: Path,
) -> None:
    """뒤 파일에 `.0` 줄만 있어도 앞 파일이 시작 시각을 알려 주면 진짜 주소다."""
    rotated(
        tmp_path, [line(T, "/", ip="203.0.113.0"), line(RELOAD, "/", ip="198.51.100.4")]
    )
    write(tmp_path, "access.log", [line(RELOAD + HOUR, "/", ip="192.0.2.0")])
    assert set(rows(addrs(tmp_path))) == {"203.0.113.0/24", "198.51.100.4", "192.0.2.0"}


def test_row_values_follow_the_067_cells(tmp_path: Path) -> None:
    ip = "203.0.113.9"
    write(
        tmp_path,
        "access.log",
        [
            line(T, "/", ip=ip),
            line(T + 1, "/clarity.js", ip=ip),
            line(T + 2, "/api/ws/spreads", status=101, ip=ip, duration=30.0),
            line(T + HOUR, "/app/?tab=gap", ip=ip),
            line(T + HOUR + 1, "/missing", status=404, ip=ip),
            line(T + HOUR + 2, "/", status=500, ip=ip),
            line(T + HOUR + 3, "/", ua=GOOGLEBOT, ip=ip),
        ],
    )
    row = rows(addrs(tmp_path))[ip]
    assert (row["requests"], row["pages"], row["js"], row["ws"]) == (7, 2, 1, 1)
    assert row["kinds"]["browser"] == 6 and row["kinds"]["search"] == 1
    assert row["status"] == {"2xx": 4, "3xx": 0, "4xx": 1, "5xx": 1}
    assert (row["first"], row["last"]) == (int(T), int(T + HOUR + 3))
    assert sum(row["spark"]) == 7 and len(row["spark"]) == 24
    assert row["topPaths"][0] == ["/", 3]
    assert row["topAgents"][0] == ["desktop·windows·chrome", 6]
    assert set(row) >= {
        "country",
        "region",
        "city",
        "asn",
        "org",
        "net",
        "score",
        "verdict",
        "reasons",
    }


def test_signal_counts_reach_the_reasons(tmp_path: Path) -> None:
    ip = "198.51.100.23"
    lines = [
        line(T + i, "/.env", status=404, ua="curl/8.7.1", ip=ip) for i in range(12)
    ]
    lines += [line(T + 20, "/robots.txt", ua="curl/8.7.1", ip=ip)]
    lines += [
        line(T + 30 + i, f"/p{i}", status=404, ua=f"curl/{i}", ip=ip) for i in range(5)
    ]
    write(tmp_path, "access.log", lines)
    row = rows(addrs(tmp_path))[ip]
    reasons = {r["code"]: r["text"] for r in row["reasons"]}
    assert reasons["probe"] == "탐색 경로 12번(/.env)"
    assert reasons["many_404"] == "404 17번(요청의 94%)"
    assert reasons["robots"] == "robots.txt 1번"
    assert reasons["ua_many"] == "서로 다른 UA 6개"
    assert reasons["ua_tool"] == "도구·스캐너 모양 요청 18번"
    assert row["verdict"] == "bot" and row["score"] == 60 + 50 + 15 + 10 + 10


def test_burst_assets_search_and_bot_names(tmp_path: Path) -> None:
    fast, human, bot = "192.0.2.10", "192.0.2.20", "192.0.2.30"
    lines = [line(T + i * 0.5, "/", ip=fast) for i in range(60)]  # 30초 안 60번
    lines += [
        line(T + 100, "/", ip=human, referer="https://www.google.com"),
        line(T + 101, "/assets/index-abc.js", ip=human),
        line(T + 102, "/landing/fonts/a.woff2", ip=human),
        line(T + 103, "/favicon.svg", ip=human),
        line(
            T + 200,
            "/",
            ua="Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
            ip=bot,
        ),
    ]
    write(tmp_path, "access.log", lines)
    got = rows(addrs(tmp_path))
    assert "burst" in {r["code"] for r in got[fast]["reasons"]}
    texts = {r["code"]: r["text"] for r in got[human]["reasons"]}
    assert (
        texts["assets"] == "정적 자원 3번"
        and texts["search_ref"] == "검색 엔진에서 들어온 페이지 1번"
    )
    bot_texts = {r["code"]: r["text"] for r in got[bot]["reasons"]}
    assert bot_texts["ua_bot"] == "봇 이름을 밝힌 요청 1번(AhrefsBot)"


def test_a_long_alphanumeric_user_agent_is_read_in_linear_time(tmp_path: Path) -> None:
    """봇 이름 찾기가 긴 영숫자 UA 에서 되짚지 않는다 — 1,024자 × 2,000 UA (되짚으면 수십 초)."""
    tail = "a1" * 600
    write(
        tmp_path,
        "access.log",
        [
            line(T + i, "/", ua=f"{CHROME} {i} {tail}", ip="192.0.2.1")
            for i in range(2_000)
        ],
    )
    began = time.perf_counter()
    addrs(tmp_path)
    assert time.perf_counter() - began < 5.0


def test_a_file_keeps_50000_rows_and_puts_the_rest_in_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert access_addrs.ROW_CAP == 50_000
    monkeypatch.setattr(access_addrs, "ROW_CAP", 2)
    rotated(tmp_path, [line(T + i, "/", ip=f"10.0.0.{i + 1}") for i in range(4)])
    write(
        tmp_path, "access.log", [line(AFTER - 60, "/", ip="10.0.0.3")]
    )  # 파일마다 따로 센다
    got = {ip: row["requests"] for ip, row in rows(addrs(tmp_path)).items()}
    assert got == {"10.0.0.1": 1, "10.0.0.2": 1, "(기타)": 2, "10.0.0.3": 1}
