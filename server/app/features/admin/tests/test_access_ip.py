"""IP 대역 하나의 상세 — 창 안 그 대역 줄만·시간별·종류·상태·이름·짝 흐름(062)·최근 100줄·partial·60초 캐시·나라와 망·내지 않는 것 (스펙 067 §3.3·§4)."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.features.admin import access_ip
from app.features.admin.access_bands import ips_query
from app.features.admin.access_cache import AccessLog, raw_size
from app.features.admin.geo_fetch import utc_month
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    DAY,
    GATE,
    GOOGLEBOT,
    HOUR,
    IPHONE,
    KAKAO,
    Feeds,
    line,
    rotated,
    write,
)
from app.features.admin.tests.geo_fakes import KR_TELECOM, DbIp, inline

BAND = "203.0.113.0/24"  # access_fakes 의 기본 IP 대역
START = AFTER - AFTER % HOUR - 23 * HOUR
CURL = "curl/8.7.1"
VALUE_KEYS = (
    "partial",
    "country",
    "net",
    "hourly",
    "kinds",
    "status",
    "agents",
    "paths",
    "pairs",
    "recent",
)


async def detail(
    directory: Path, band: str = BAND, window: str = "24h", now: float = AFTER
) -> dict[str, Any]:
    body = await Feeds(directory, now).ip(band, window)
    assert (body["state"], body["code"]) == ("ok", None), body
    return body


async def test_counts_only_the_band_lines_inside_the_window_like_the_list(
    tmp_path: Path,
) -> None:
    t = AFTER - 2 * HOUR
    band = [
        line(t, "/?utm_source=x", ua=IPHONE),
        line(t + 1, "/clarity.js", ua=IPHONE),
        line(t + 2, "/wp-login.php", ua=CHROME, status=404),
        line(t + 3, "/", ua=GOOGLEBOT, status=500),
        line(t + 4, "/api/ws/spreads", ua=CHROME, status=502),  # WS 5xx — 5xx·errors 밖
        line(t + 5, "/", ua=CHROME, referer="http://localhost:5173"),
        line(START - HOUR, "/old", ua=IPHONE),  # 24시간 창 앞
    ]
    others = [
        line(t + 6, "/", ua=CHROME, ip="203.0.11.0"),
        line(
            t + 7, "/", ua='x "203.0.113.9" y', ip="198.51.100.0"
        ),  # 글자만 같은 다른 대역
    ]
    write(tmp_path, "access.log", band + others)
    body = await detail(tmp_path)
    assert list(body) == ["state", "code", "window", "band", *VALUE_KEYS]
    assert (body["band"], body["partial"], body["country"], body["net"]) == (
        BAND,
        False,
        None,
        None,
    )
    hourly = body["hourly"]
    assert [h["ts"] for h in hourly] == [START + i * HOUR for i in range(24)]
    assert hourly[21] == {
        "ts": START + 21 * HOUR,
        "requests": 6,
        "pages": 1,
        "js": 1,
        "errors": 1,
    }
    assert sum(h["requests"] for h in hourly) == 6
    assert body["kinds"] == {
        "browser": 3,
        "search": 1,
        "ai": 0,
        "preview": 0,
        "tool": 0,
        "scanner": 1,
        "operator": 1,
        "unknown": 0,
    }
    assert body["status"] == {"2xx": 3, "3xx": 0, "4xx": 1, "5xx": 1}
    assert body["paths"][0] == ["/", 3] and ["/old", 1] not in body["paths"]
    assert body["agents"][0] == ["desktop·windows·chrome", 3]
    # 목록의 같은 대역 행과 같은 규칙(038 §3.4)
    (row,) = AccessLog(str(tmp_path), GATE).ips("24h", AFTER, ips_query(q="203.0.113"))[
        0
    ]["bands"]
    assert (row["requests"], row["pages"], row["js"], row["kinds"], row["status"]) == (
        6,
        1,
        1,
        body["kinds"],
        body["status"],
    )
    assert (await detail(tmp_path, window="7d"))["kinds"][
        "browser"
    ] == 4  # 창 앞 줄도 든다


async def test_recent_is_the_latest_100_lines_newest_first_with_origin_only(
    tmp_path: Path,
) -> None:
    t = AFTER - 3 * HOUR
    lines = [line(t + i, f"/p{i}?q=secret", ua=IPHONE) for i in range(119)]
    lines.append(
        line(
            t + 118,
            "/same",
            ua=IPHONE,
            referer="https://www.google.com/search?q=secret",
        )
    )
    write(tmp_path, "access.log", lines)
    body = await detail(tmp_path)
    recent = body["recent"]
    assert len(recent) == 100
    assert recent[0] == {
        "ts": int(t + 118),
        "path": "/same",  # 같은 시각이면 뒤 줄이 먼저
        "status": 200,
        "kind": "browser",
        "browser": "safari",
        "os": "ios",
        "device": "mobile",
        "referer": "https://www.google.com",
    }
    assert [r["path"] for r in recent[1:3]] == ["/p118", "/p117"]
    assert recent[-1]["path"] == "/p20" and all(
        r["referer"] is None for r in recent[1:]
    )
    assert "secret" not in json.dumps(body)


async def test_pairs_are_the_day_flows_of_the_band_without_user_agents(
    tmp_path: Path,
) -> None:
    t = AFTER - 3 * HOUR
    write(
        tmp_path,
        "access.log",
        [
            line(t, "/", ua=IPHONE, referer="https://www.google.com"),
            line(t + 5, "/", ua=CURL),
            line(t + 10, "/privacy", ua=IPHONE),
            line(t + 20, "/app/?tab=history", ua=IPHONE),
            line(t + 25, "/clarity.js", ua=CHROME),  # 페이지 줄 없는 짝 — 빠진다
            line(t + 26, "/api/ws/spreads", ua=CHROME, status=101),
            line(t + 30, "/kimp-chart", ua=KAKAO),
            line(t - DAY + HOUR, "/", ua=IPHONE),  # 어제 — 다른 짝
        ],
    )
    body = await detail(tmp_path, window="7d")
    assert body["pairs"] == [
        {"entry": "kimp-chart", "exit": "kimp-chart", "npages": 1, "device": "mobile", "os": "ios", "browser": "inapp", "channel": "inapp"},
        {"entry": "landing", "exit": "app-history", "npages": 3, "device": "mobile", "os": "ios", "browser": "safari", "channel": "search"},
        {"entry": "landing", "exit": "landing", "npages": 1, "device": "desktop", "os": "other", "browser": "other", "channel": "direct"},
        {"entry": "landing", "exit": "landing", "npages": 1, "device": "mobile", "os": "ios", "browser": "safari", "channel": "direct"},
    ]  # fmt: skip
    text = json.dumps(body)
    for ua in (IPHONE, CHROME, KAKAO, CURL):
        assert ua not in text


async def test_pairs_keep_the_latest_fifty(tmp_path: Path) -> None:
    t = AFTER - 3 * HOUR
    write(
        tmp_path,
        "access.log",
        [line(t + i, "/", ua=f"{CHROME} n{i}") for i in range(55)],
    )
    assert len((await detail(tmp_path))["pairs"]) == 50


async def test_partial_when_the_newest_100_mib_leave_older_files_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = rotated(tmp_path, [line(AFTER - 10 * HOUR, "/old")])
    new = rotated(tmp_path, [line(AFTER - 5 * HOUR, "/new")])
    current = write(tmp_path, "access.log", [line(AFTER - 60, "/now")])
    whole = await detail(tmp_path)
    assert whole["partial"] is False and len(whole["recent"]) == 3
    sizes = {
        p.name: raw_size(str(p), p.stat().st_size) or 0 for p in (old, new, current)
    }
    monkeypatch.setattr(
        access_ip, "BUDGET_BYTES", sizes[current.name] + sizes[new.name]
    )
    cut = await detail(tmp_path)
    assert cut["partial"] is True
    assert [r["path"] for r in cut["recent"]] == ["/now", "/new"]


async def test_a_broken_rotated_file_is_partial_with_one_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lines = [line(AFTER - 5 * HOUR + i, "/", ua=IPHONE) for i in range(50)]
    broken = rotated(tmp_path, lines)
    broken.write_bytes(broken.read_bytes()[:-12])  # 꼬리를 자른 gz — 읽은 데까지
    write(tmp_path, "access.log", [line(AFTER - 60, "/now")])
    caplog.set_level(logging.INFO)
    body = await detail(tmp_path)
    assert body["partial"] is True and body["recent"][0]["path"] == "/now"
    assert 1 < len(body["recent"]) <= 51
    records = [r for r in caplog.records if r.name == "marketlens.admin"]
    assert [r.getMessage() for r in records] == [
        "관리자 피드 access.ip 실패 — EOFError"
    ]
    assert "203.0.113" not in caplog.text


async def test_detail_is_cached_60_seconds_per_band_and_window(tmp_path: Path) -> None:
    t = AFTER - 2 * HOUR
    write(tmp_path, "access.log", [line(t, "/")])
    f = Feeds(tmp_path, AFTER)
    assert len((await f.ip(BAND))["recent"]) == 1
    write(tmp_path, "access.log", [line(t, "/"), line(t + 1, "/")])
    f.t = 10
    assert len((await f.ip(BAND, "7d"))["recent"]) == 2  # 창마다 따로
    f.t = 59
    assert len((await f.ip(BAND))["recent"]) == 1
    f.t = 60
    assert len((await f.ip(BAND))["recent"]) == 2


async def test_country_and_network_kind_come_from_the_band_address(
    tmp_path: Path,
) -> None:
    t = AFTER - 2 * HOUR
    write(
        tmp_path,
        "access.log",
        [line(t, "/", ip=KR_TELECOM), line(t + 1, "/", ip="2001:db8:1:2::9")],
    )
    dbip = DbIp().serve(utc_month(AFTER))
    f = Feeds(tmp_path, AFTER, geo_transport=dbip.transport, geo_start=inline)
    f.feeds.geo.ensure()
    v4 = await f.ip(BAND)
    assert (v4["country"], v4["net"]) == ("KR", "telecom_kr")
    v6 = await f.ip("2001:db8:1::/48")
    assert (v6["country"], v6["net"], len(v6["recent"])) == (None, "ipv6", 1)
