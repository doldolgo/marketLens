"""접속 요약 세기 — 창·페이지 판정·utm·출처·탭·기기·브라우저·WS·5xx (스펙 035 §3.2·§4).

`summarize` 를 테스트가 만든 로그 디렉터리에 직접 부른다(파일·한도는 test_access_files.py, 피드·HTTP 는 test_visits.py).
"""

import json
from pathlib import Path

from app.features.admin.access import summarize
from app.features.admin.tests.access_fakes import (
    CHROME,
    EDGE,
    END_TS,
    FIREFOX,
    GOOGLEBOT,
    HOUR,
    IPHONE,
    NOW,
    SAMSUNG,
    START_TS,
    at,
    line,
    write,
)


def run(tmp_path: Path, lines: list[str]) -> dict:
    write(tmp_path, "access.log", lines)
    return summarize(str(tmp_path), NOW)


def test_window_is_the_last_24_hours_and_lines_outside_are_dropped_unparsed(
    tmp_path: Path,
) -> None:
    body = run(
        tmp_path,
        [
            line(START_TS - 0.001),  # 창 바로 앞
            line(at(0)),
            line(at(5, 10)),
            line(at(5, 20), "/assets/a.js"),
            line(at(23, 1799)),
            line(START_TS + 24 * HOUR),  # 지금이 든 시의 끝 — 창 밖
        ],
    )
    assert (body["startTs"], body["endTs"]) == (START_TS, END_TS)
    assert body["firstTs"] == START_TS
    assert body["totals"] == {"requests": 4, "pages": 3, "ws": 0, "skipped": 0}
    hourly = body["hourly"]
    assert [h["ts"] for h in hourly] == [START_TS + i * HOUR for i in range(24)]
    assert hourly[5] == {
        "ts": START_TS + 5 * HOUR,
        "requests": 2,
        "pages": 1,
        "errors": 0,
    }
    assert sum(h["requests"] for h in hourly) == 4


def test_first_ts_is_the_earliest_line_read_or_null(tmp_path: Path) -> None:
    body = run(tmp_path, [line(at(20, 5.7)), line(at(12, 3.2))])
    assert body["firstTs"] == int(at(12, 3.2))
    empty = run(tmp_path, [line(START_TS - 10)])
    assert empty["firstTs"] is None and empty["totals"]["requests"] == 0


def test_page_requests_are_get_200_without_a_dot_in_the_last_segment(
    tmp_path: Path,
) -> None:
    body = run(
        tmp_path,
        [
            line(at(1), "/"),
            line(at(1), "/app/?tab=history"),
            line(at(1), "/privacy"),
            line(at(1), "/assets/index-abc.js"),
            line(at(1), "/wp-login.php"),
            line(at(1), "/?tab=history", status=301),  # 022 — /app/ 로 301
            line(at(1), "/nope", status=404),
            line(at(1), "/", method="HEAD"),
        ],
    )
    assert body["totals"]["pages"] == 3
    assert body["paths"] == [["/", 1], ["/app/", 1], ["/privacy", 1]]
    assert body["status"] == {"2xx": 6, "3xx": 1, "4xx": 1, "5xx": 0}


def test_utm_source_counts_once_lowercased_and_cut(tmp_path: Path) -> None:
    long = "X" * 80
    body = run(
        tmp_path,
        [
            # 022 의 /?쿼리 301 줄은 페이지가 아니다 — 같은 방문을 두 번 세지 않는다
            line(at(2), "/?utm_source=Naver&tab=history", status=301),
            line(at(2), "/app/?tab=history&utm_source=Naver"),
            line(at(2), f"/?utm_source={long}"),
            line(at(2), "/?utm_source="),
        ],
    )
    assert body["utmSources"] == [["naver", 1], ["x" * 50, 1]]


def test_referrers_exclude_own_hosts_and_empty(tmp_path: Path) -> None:
    body = run(
        tmp_path,
        [
            line(at(3), referer="https://kimptrack.com"),
            line(at(3), referer="https://www.kimptrack.com"),
            line(at(3), referer="https://admin.kimptrack.com"),
            line(at(3), referer=""),
            line(at(3), referer="https://www.google.com"),
            line(at(3), referer="https://www.google.com"),
            line(at(3), referer="https://search.naver.com:443"),
            line(at(3), "/x.png", referer="https://t.co"),  # 자산 — 페이지만 센다
        ],
    )
    assert body["referrers"] == [
        ["https://www.google.com", 2],
        ["https://search.naver.com:443", 1],
    ]


def test_referrers_are_rebuilt_as_origins_without_the_caddy_map(
    tmp_path: Path,
) -> None:
    # 027 caddy 의 출처 줄이기가 빠진 줄 — 경로·쿼리·사용자 정보는 요약에서도 버린다
    body = run(
        tmp_path,
        [
            line(at(3), referer="https://evil.example/search?q=secret-term&e=a@b.c"),
            line(at(3), referer="HTTPS://User:pw@Evil.Example:8443/x#frag"),
            line(at(3), referer="https://kimptrack.com./app/"),  # 끝 점 — 자기 호스트
            line(at(3), referer="android-app://com.google.android.gm/p?token=abc"),
            line(at(3), referer="https://[2001:db8::1]:8443/p?q=1"),
            line(at(3), referer="https://bad:port/"),
        ],
    )
    assert body["referrers"] == [
        ["https://[2001:db8::1]:8443", 1],
        ["https://evil.example", 1],
        ["https://evil.example:8443", 1],
    ]
    raw = json.dumps(body)
    for leak in ("secret-term", "a@b.c", "User", "pw@", "frag", "token", "android"):
        assert leak not in raw, leak


def test_tabs_count_dashboard_page_entries_only(tmp_path: Path) -> None:
    body = run(
        tmp_path,
        [
            line(at(4), "/app/"),
            line(at(4), "/app/?utm_source=x"),
            line(at(4), "/app/?tab=history"),
            line(at(4), "/app/?tab=health&sym=BTC"),
            line(at(4), "/app/?tab=evil"),
            line(at(4), "/app/?tab="),
            line(at(4), "/?tab=history"),  # /app/ 밖 페이지는 안 센다
            line(at(4), "/privacy?tab=flow"),
            line(at(4), "/app/?tab=gap", status=304),  # 페이지가 아님
        ],
    )
    assert body["tabs"] == [["(기타)", 2], ["spread", 2], ["health", 1], ["history", 1]]


def test_devices_and_browsers_by_user_agent(tmp_path: Path) -> None:
    body = run(
        tmp_path,
        [
            line(at(6), ua=EDGE),
            line(at(6), ua=CHROME),
            line(at(6), ua=FIREFOX),
            line(at(6), ua=SAMSUNG),
            line(at(6), ua=IPHONE),
            line(at(6), ua=GOOGLEBOT),
            line(at(6), ua="curl/8.7.1"),
            line(at(6), ua="Mozilla/5.0 HeadlessChrome/129.0"),
            line(at(6), ua=""),
        ],
    )
    assert body["devices"] == [
        ["bot", 3],
        ["desktop", 3],
        ["mobile", 2],
        ["unknown", 1],
    ]
    # bot 은 브라우저에서 뺀다 — 빈 UA 는 other
    assert body["browsers"] == [
        ["chrome", 1],
        ["edge", 1],
        ["firefox", 1],
        ["other", 1],
        ["safari", 1],
        ["samsung", 1],
    ]


def test_ws_lines_and_duration_buckets_at_the_boundaries(tmp_path: Path) -> None:
    durations = [9.999, 10, 59.99, 60, 599.9, 600, 3599.9, 3600, 86_400]
    lines = [line(at(7), "/api/ws/spreads", status=101, duration=d) for d in durations]
    lines.append(line(at(7), "/api/ws/spreads", status=404, duration=1))
    lines.append(line(at(7), "/api/ws/other", status=101, duration=1))
    body = run(tmp_path, lines)
    assert body["ws"] == {
        "count": 9,
        "durations": {"lt10s": 1, "lt1m": 2, "lt10m": 2, "lt1h": 2, "ge1h": 2},
    }
    assert body["totals"]["ws"] == 9 and body["totals"]["requests"] == 11
    assert body["totals"]["pages"] == 0
    assert body["status"]["4xx"] == 1  # 101 은 네 칸 어디에도 없다


def test_recent_5xx_are_the_latest_20_paths_without_query(tmp_path: Path) -> None:
    lines = [line(at(8, i), f"/api/x{i}", status=502) for i in range(25)]
    lines.append(line(at(9), "/app/?tab=history&utm_source=leak", status=500))
    lines.append(line(at(9, 1), "/" + "p" * 150, status=503))
    body = run(tmp_path, lines)
    recent = body["recent5xx"]
    assert len(recent) == 20
    assert recent[0] == {"ts": int(at(9, 1)), "path": "/" + "p" * 99, "status": 503}
    assert recent[1] == {"ts": int(at(9)), "path": "/app/", "status": 500}
    assert [r["path"] for r in recent[2:]] == [f"/api/x{i}" for i in range(24, 6, -1)]
    assert body["status"]["5xx"] == 27
    assert body["hourly"][8]["errors"] == 25 and body["hourly"][9]["errors"] == 2
    assert "leak" not in json.dumps(body)
