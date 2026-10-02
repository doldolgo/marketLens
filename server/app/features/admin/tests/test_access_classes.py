"""줄 분류 — 종류 여덟·탐색·운영자 흔적·페이지·JS 신호·5xx 나눔·상위 목록 (스펙 038 §3.4·§3.6·§4)."""

from pathlib import Path

import pytest

from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    DAY,
    GOOGLEBOT,
    HOUR,
    IPHONE,
    NOW,
    START_TS,
    line,
    summary,
    write,
)

REPO = Path(__file__).resolve().parents[5]
ANDROID = "Mozilla/5.0 (Linux; Android 14; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"
KINDS = {
    "search": [
        GOOGLEBOT,
        "Mozilla/5.0 (compatible; Yeti/1.1; +https://naver.me/spd)",
        "Daumoa/4.0",
        "google-site-verification/1.0",
    ],
    "ai": [
        "GPTBot/1.2",
        "Mozilla/5.0 (compatible; ClaudeBot/1.0)",
        "Claude-User/1.0",
        "Mozilla/5.0 (compatible; Bytespider; spider-feedback@x)",
        "meta-externalagent/1.1",
    ],
    "preview": [
        "kakaotalk-scrap/1.0",
        "facebookexternalhit/1.1",
        "Slackbot-LinkExpanding 1.0",
    ],
    "tool": [
        "curl/8.7.1",
        "python-requests/2.32",
        "Go-http-client/2.0",
        "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/129.0 Safari/537.36",
        "Dalvik/2.1.0 (Linux; U; Android 13)",
    ],
    "scanner": [
        "Mozilla/5.0 zgrab/0.x",
        "Mozilla/5.0 (compatible; SemrushBot/7)",
        "https://example.org/x",
        "Mozlila/5.0 (Linux; Android 7.0)",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    ],
    "browser": [
        CHROME,
        IPHONE,
        CHROME + " Whale/3.27",
        ANDROID.replace("Chrome/", "SamsungBrowser/26.0 Chrome/"),
        IPHONE + " KAKAOTALK 10.4.5",
        IPHONE + " NAVER(inapp; search; 2000; 12.1.0)",
        ANDROID + " DaumApps/4.11",
        IPHONE + " Instagram 300.0.0",
        IPHONE + " [FBAN/FBIOS;FBAV/450.0]",
    ],
    "unknown": ["", None],
}


def at(sec: float) -> float:
    return START_TS + 3 * HOUR + sec


def test_each_kind_by_user_agent_and_sums_match_totals(tmp_path: Path) -> None:
    lines = [
        line(at(i), "/", ua=ua)
        for i, (_, uas) in enumerate(KINDS.items())
        for ua in uas
    ]
    lines.append(line(at(50), "/", ua="curl/8.7.1", referer="http://localhost:5173"))
    write(tmp_path, "access.log", lines)
    classes = summary(tmp_path, NOW)["classes"]
    expect = {k: len(v) for k, v in KINDS.items()} | {"operator": 1}
    assert {k: c["requests"] for k, c in classes.items()} == expect
    assert {k: c["pages"] for k, c in classes.items()} == expect


@pytest.mark.parametrize(
    ("kind", "ua"), [(k, ua) for k, uas in KINDS.items() for ua in uas]
)
def test_one_user_agent_lands_in_its_kind(
    tmp_path: Path, kind: str, ua: str | None
) -> None:
    write(
        tmp_path, "access.log", [line(at(0), "/", ua=ua), line(at(1), "/a.js", ua=ua)]
    )
    body = summary(tmp_path, NOW)
    assert body["classes"][kind] == {"requests": 2, "pages": 1}
    assert (
        sum(c["requests"] for c in body["classes"].values())
        == body["totals"]["requests"]
    )
    assert sum(c["pages"] for c in body["classes"].values()) == body["totals"]["pages"]


def test_kind_is_judged_on_the_first_1024_characters(tmp_path: Path) -> None:
    # 판정은 앞 1,024자 — 그 뒤의 봇 낱말은 보지 않고, 그 안의 것은 본다 (038 §3.4)
    pad = " " + "x" * (1_024 - len(CHROME) - 1)
    assert len(CHROME + pad) == 1_024
    lines = [
        line(at(0), "/", ua=CHROME + pad + " Googlebot/2.1"),
        line(at(1), "/", ua=CHROME + pad[:-20] + " Googlebot/2.1" + "y" * 900),
    ]
    write(tmp_path, "access.log", lines)
    classes = summary(tmp_path, NOW)["classes"]
    assert (classes["browser"]["pages"], classes["search"]["pages"]) == (1, 1)


PROBES = "wp- wordpress xmlrpc .php .env .git .aws .ssh cgi-bin phpmyadmin actuator /admin /login /config /vendor/ /boaform /hnap1 /owa/ /autodiscover /server-status /solr /console .ini .sql .bak /backup /shell /setup /install /debug .ds_store".split()


def test_every_probe_word_marks_a_path(tmp_path: Path) -> None:
    paths = [
        f"/x{w}y" if w.startswith(".") else f"{w if w.startswith('/') else '/' + w}z"
        for w in PROBES
    ]
    write(
        tmp_path,
        "access.log",
        [line(at(i), p.upper(), status=404) for i, p in enumerate(paths)],
    )
    assert summary(tmp_path, NOW)["totals"]["probes"] == len(PROBES)


def test_our_paths_and_public_files_are_never_probes(tmp_path: Path) -> None:
    ours = [
        "/",
        "/app/",
        "/privacy",
        "/clarity.js",
        "/app/clarity.js",
        "/assets/index-a1.js",
        "/landing/x.webp",
        "/api/ws/spreads",
        "/api/history/events",
        "/api/history/candles",
        "/robots.txt",
        "/sitemap.xml",
        "/favicon.ico",
        "/fonts/a.woff2",
    ]
    public = REPO / "web" / "public"
    files = [
        "/" + p.relative_to(public).as_posix() for p in public.rglob("*") if p.is_file()
    ]
    assert "/privacy-20261001.html" in files
    write(
        tmp_path,
        "access.log",
        [
            line(at(i % 3000), p)
            for i, p in enumerate(ours + files + ["/app" + f for f in files])
        ],
    )
    assert summary(tmp_path, NOW)["totals"]["probes"] == 0


def test_a_probing_browser_is_a_scanner_and_its_pair_skips_that_day(
    tmp_path: Path,
) -> None:
    yesterday, today = AFTER - DAY, AFTER - 600
    write(
        tmp_path,
        "access.log",
        [
            line(yesterday, "/"),
            line(today, "/"),
            line(today + 1, "/wp-login.php", status=404),
        ],
    )
    body = summary(tmp_path, AFTER, "7d")
    assert body["classes"]["scanner"] == {"requests": 1, "pages": 0}
    assert body["totals"]["probes"] == 1
    assert [d["shaped"] for d in body["visitors"]["days"]][-2:] == [
        1,
        0,
    ]  # 탐색한 날만 빠진다


OPERATOR = [
    "http://localhost:5173",
    "http://kimptrack.localhost",
    "https://a.test",
    "http://127.0.0.1:8090",
    "http://[::1]:8000",
    "http://10.1.2.3",
    "http://172.20.0.1",
    "http://192.168.0.1",
    "http://203.0.113.7",
    "https://clarity.microsoft.com",
]


def test_operator_referrers_whatever_the_user_agent(tmp_path: Path) -> None:
    lines = [line(AFTER - 900 + i, "/", referer=r) for i, r in enumerate(OPERATOR)]
    lines += [
        line(AFTER - 100, "/", referer="https://example.com"),
        line(AFTER - 99, "/", referer="https://localhost.example.com", ua=IPHONE),
    ]
    write(tmp_path, "access.log", lines)
    body = summary(tmp_path, AFTER)
    assert body["classes"]["operator"] == {"requests": 10, "pages": 10}
    assert body["classes"]["browser"] == {"requests": 2, "pages": 2}
    # CHROME 짝은 운영자 흔적이 있어 그날 빠지고, 남은 iPhone 짝의 채널은 referral
    assert body["visitors"]["shaped"] == 1 and body["visitors"]["channels"] == [
        ["referral", 0, 1]
    ]
    assert body["referrers"] == [
        ["https://example.com", 1],
        ["https://localhost.example.com", 1],
    ]


def test_pages_include_304_and_js_views_are_browser_clarity_loads(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "access.log",
        [
            line(at(1), "/", status=304),
            line(at(2), "/assets/a.js"),
            line(at(3), "/x.php"),
            line(at(4), "/?tab=gap", status=301),
            line(at(5), "/clarity.js"),
            line(at(6), "/app/clarity.js", status=304),
            line(at(7), "/clarity.js", status=404),
            line(at(8), "/clarity.js", ua="curl/8"),
            line(at(9), "/api/ws/spreads", status=101),
        ],
    )
    totals = summary(tmp_path, NOW)["totals"]
    assert (totals["pages"], totals["humanPages"], totals["jsViews"], totals["ws"]) == (
        1,
        1,
        2,
        1,
    )


def test_websocket_5xx_is_kept_apart_from_other_5xx(tmp_path: Path) -> None:
    write(
        tmp_path,
        "access.log",
        [line(at(1), "/api/ws/spreads", status=502), line(at(2), "/?x=1", status=500)],
    )
    body = summary(tmp_path, NOW)
    assert body["status"] == {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 1, "ws5xx": 1}
    assert (body["hourly"][3]["errors"], body["hourly"][3]["wsErrors"]) == (1, 1)
    assert body["ws"]["errors"] == 1 and body["ws"]["count"] == 0
    assert body["recent5xx"] == [{"ts": int(at(2)), "path": "/", "status": 500}]


def test_top_lists_count_human_browser_pages_only(tmp_path: Path) -> None:
    lines = [
        line(
            at(1),
            "/app/?tab=history&utm_source=Bot",
            ua=GOOGLEBOT,
            referer="https://bot.example",
        ),
        line(at(2), "/admin", ua=CHROME, status=200),  # 탐색 — scanner
        line(at(3), "/app/", referer="http://localhost:5173"),  # 운영자
        line(
            at(4),
            "/app/?tab=history&utm_source=News",
            ua=ANDROID.replace("Mobile ", ""),
            referer="https://www.google.com",
        ),
        line(at(5), "/privacy", ua=IPHONE, status=304),
    ]
    write(tmp_path, "access.log", lines)
    body = summary(tmp_path, NOW)
    assert body["paths"] == [["/app/", 1], ["/privacy", 1]]
    assert body["tabs"] == [["history", 1]]
    assert body["utmSources"] == [["news", 1]]
    assert body["referrers"] == [["https://www.google.com", 1]]
    assert body["devices"] == [["mobile", 1], ["tablet", 1]]
    assert body["browsers"] == [["chrome", 1], ["safari", 1]]


def test_a_new_31st_path_in_one_hour_counts_as_other(tmp_path: Path) -> None:
    lines = [line(at(i), f"/p{i:02d}") for i in range(33)] + [line(at(40), "/p00")]
    lines += [line(at(HOUR + i), "/p32") for i in range(3)]  # 다음 시는 새 칸
    write(tmp_path, "access.log", lines)
    paths = dict(summary(tmp_path, NOW)["paths"])
    assert paths["/p00"] == 2 and paths["(기타)"] == 3 and paths["/p32"] == 3
    assert "/p31" not in paths and "/p30" not in paths
