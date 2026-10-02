"""날마다 센 방문자 — 짝 게이트·확인/브라우저 모양·다시 온·WS 짝·상한·열쇠·채널·기기 (스펙 038 §3.2·§3.5·§4)."""

from pathlib import Path

import pytest

from app.features.admin import access_hours
from app.features.admin.access_cache import AccessLog
from app.features.admin.access_pairs import pair_hash
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    DAY,
    GATE,
    HOUR,
    IPHONE,
    KAKAO,
    NOW,
    line,
    rotated,
    summary,
    write,
)

NULL_KEYS = "sinceTs confirmed shaped returning capped days channels devices os browsers inApp".split()
T0 = AFTER - 2 * HOUR  # 오늘(KST) 08:30


@pytest.fixture
def hashes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bytes, str]]:
    """짝 해시를 부를 때마다 (열쇠, IP) 를 적는다."""
    calls: list[tuple[bytes, str]] = []

    def watched(key: bytes, ip: str, ua: str) -> bytes:
        calls.append((key, ip))
        return pair_hash(key, ip, ua)

    monkeypatch.setattr(access_hours, "pair_hash", watched)
    return calls


@pytest.mark.parametrize("window", ["24h", "7d", "30d"])
def test_before_the_gate_no_pair_is_hashed_and_visitors_wait(
    tmp_path: Path, hashes: list, window: str
) -> None:
    write(
        tmp_path,
        "access.log",
        [
            line(NOW - 60, "/"),
            line(NOW - 50, "/clarity.js"),
            line(NOW - 40, "/api/ws/spreads", status=101),
        ],
    )
    body = summary(tmp_path, NOW, window)
    assert body["visitors"] == {
        "state": "unconfigured",
        "code": "before_gate",
        **dict.fromkeys(NULL_KEYS),
    }
    assert body["ws"]["pairs"] is None and hashes == []
    assert (
        body["totals"]["humanPages"] == 1
        and body["classes"]["browser"]["requests"] == 3
    )
    assert (
        body["paths"] == [["/", 1]] and sum(h["requests"] for h in body["hourly"]) == 3
    )


def test_a_24h_window_over_the_gate_counts_lines_but_pairs_only_after(
    tmp_path: Path, hashes: list
) -> None:
    now = GATE + 5 * HOUR
    lines = [
        line(GATE - 3 * HOUR, "/"),
        line(GATE - 3 * HOUR + 1, "/clarity.js"),
        line(GATE + HOUR, "/"),
    ]
    write(tmp_path, "access.log", lines + [line(GATE - HOUR, "/", ua=IPHONE)])
    body = summary(tmp_path, now)
    assert body["totals"]["requests"] == 4 and body["classes"]["browser"]["pages"] == 3
    people = body["visitors"]
    assert (
        people["state"],
        people["sinceTs"],
        people["shaped"],
        people["confirmed"],
    ) == ("ok", GATE, 1, 0)
    assert people["days"] == [{"ts": GATE, "confirmed": 0, "shaped": 1, "returning": 0}]
    assert len(hashes) == 1  # 게이트 뒤 줄 하나만


def test_visitors_count_shaped_confirmed_and_ten_pages_once(tmp_path: Path) -> None:
    lines = [line(T0 + i, "/", ip="198.51.100.0") for i in range(10)]
    lines += [
        line(T0, "/app/", ip="192.0.2.0"),
        line(T0 + 1, "/app/clarity.js", ip="192.0.2.0"),
    ]
    write(tmp_path, "access.log", lines)
    people = summary(tmp_path, AFTER)["visitors"]
    assert (people["shaped"], people["confirmed"], people["returning"]) == (2, 1, 0)
    assert people["devices"] == [["desktop", 1, 2]] and people["channels"] == [
        ["direct", 1, 2]
    ]


def test_the_same_pair_on_two_days_counts_twice(tmp_path: Path) -> None:
    write(tmp_path, "access.log", [line(T0 - DAY, "/"), line(T0, "/")])
    people = summary(tmp_path, AFTER, "7d")["visitors"]
    assert people["shaped"] == 2
    assert [(d["ts"], d["shaped"]) for d in people["days"]] == [
        (GATE, 0),
        (GATE + DAY, 1),
        (GATE + 2 * DAY, 1),
    ]


def test_one_day_split_over_two_files_counts_once_with_the_earlier_channel(
    tmp_path: Path,
) -> None:
    rotated(tmp_path, [line(T0 - HOUR, "/", referer="https://www.google.com")])
    write(
        tmp_path,
        "access.log",
        [line(T0, "/clarity.js"), line(T0 + 1, "/", referer="https://x.com")],
    )
    people = summary(tmp_path, AFTER)["visitors"]
    assert (people["shaped"], people["confirmed"]) == (1, 1)
    assert people["channels"] == [["search", 1, 1]]


@pytest.mark.parametrize(
    ("first", "returning"),
    [
        ([("/", 304)], 1),
        ([("/privacy", 304)], 1),
        ([("/", 200), ("/", 304)], 0),
        ([("/app/", 304), ("/", 304)], 0),
    ],
)
def test_returning_is_a_confirmed_first_page_304(
    tmp_path: Path, first: list, returning: int
) -> None:
    lines = [
        line(T0 + i, path, status=status) for i, (path, status) in enumerate(first)
    ]
    write(tmp_path, "access.log", [*lines, line(T0 + 9, "/clarity.js")])
    assert summary(tmp_path, AFTER)["visitors"]["returning"] == returning


def test_signal_hours_must_fall_inside_the_window(tmp_path: Path) -> None:
    early = (
        AFTER - 30 * HOUR
    )  # 24시간 창 앞, 7일 창 안 — 같은 KST 날의 페이지는 24시간 창 안
    write(
        tmp_path,
        "access.log",
        [line(early, "/clarity.js"), line(early + 8 * HOUR, "/")],
    )
    day = summary(tmp_path, AFTER)["visitors"]
    assert (day["shaped"], day["confirmed"]) == (1, 0)
    assert summary(tmp_path, AFTER, "7d")["visitors"]["confirmed"] == 1


def test_ws_pairs_are_shaped_pairs_with_an_upgrade(tmp_path: Path) -> None:
    lines = [
        line(T0, "/app/"),
        line(T0 + 5, "/api/ws/spreads", status=101, duration=900),
    ]
    lines += [
        line(T0, "/", ua=IPHONE),
        line(T0 + 3, "/api/ws/spreads", ua=IPHONE, status=101),
    ]
    lines.append(line(T0 + 4, "/api/ws/spreads", ua="curl/8", status=101))
    write(tmp_path, "access.log", lines)
    body = summary(tmp_path, AFTER)
    assert body["ws"]["count"] == 3 and body["ws"]["pairs"] == 2
    assert body["visitors"]["confirmed"] == 2  # WS 101 도 JS 신호다


def test_the_1001st_pair_is_not_recorded_but_lines_still_count(tmp_path: Path) -> None:
    lines = [line(T0, "/", ip=f"10.{i // 256}.{i % 256}.0") for i in range(1_001)]
    write(tmp_path, "access.log", lines + [line(T0 + 1, "/", ip=None)])
    body = summary(tmp_path, AFTER)
    assert body["totals"]["requests"] == 1_002
    assert (body["visitors"]["shaped"], body["visitors"]["capped"]) == (1_000, True)


def test_a_line_without_ip_makes_no_pair(tmp_path: Path, hashes: list) -> None:
    write(
        tmp_path,
        "access.log",
        [line(T0, "/", ip=None), line(T0 + 1, "/clarity.js", ip=None)],
    )
    body = summary(tmp_path, AFTER)
    assert body["visitors"]["shaped"] == 0 and hashes == []
    assert body["totals"]["humanPages"] == 1


def test_a_fixed_key_hashes_by_kst_day_and_merges_files(
    tmp_path: Path, hashes: list
) -> None:
    rotated(tmp_path, [line(T0 - DAY, "/"), line(T0 - HOUR, "/")])
    write(tmp_path, "access.log", [line(T0, "/")])
    log = AccessLog(str(tmp_path), GATE, urandom=lambda n: b"k" * n)
    assert log.summary("7d", AFTER)[0]["visitors"]["shaped"] == 2
    keys = [key for key, _ in hashes]
    assert (
        keys[0] == b"k" * 16 + b"2026-10-12"
        and keys[1] == keys[2] == b"k" * 16 + b"2026-10-13"
    )
    assert pair_hash(keys[0], "203.0.113.0", CHROME) != pair_hash(
        keys[1], "203.0.113.0", CHROME
    )


@pytest.mark.parametrize(
    ("uri", "referer", "ua", "expect"),
    [
        ("/?utm_source=news", "https://www.google.com", CHROME, "campaign"),
        ("/", "https://www.kimptrack.com", CHROME, "internal"),
        ("/", "https://chatgpt.com", CHROME, "ai"),
        ("/", "https://m.blog.naver.com", CHROME, "social"),
        ("/", "https://m.search.naver.com", CHROME, "search"),
        ("/", "https://www.google.co.kr", CHROME, "search"),
        ("/", "https://spacex.com", CHROME, "referral"),
        ("/", "https://example.com", CHROME, "referral"),
        ("/", "", KAKAO, "inapp"),
        ("/", "", CHROME, "direct"),
        ("/api/ws/spreads", "", CHROME, "unknown"),
    ],
)
def test_channel_of_the_first_page_line(
    tmp_path: Path, uri: str, referer: str, ua: str, expect: str
) -> None:
    status = 101 if uri.startswith("/api/ws") else 200
    write(
        tmp_path, "access.log", [line(T0, uri, referer=referer, ua=ua, status=status)]
    )
    assert summary(tmp_path, AFTER)["visitors"]["channels"] == [
        [expect, 1 if status == 101 else 0, 1]
    ]


IPAD = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Safari/604.1"
TABLET = "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15"
CROS = "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"
WEBVIEW = "Mozilla/5.0 (Linux; Android 14; SM-S918N; wv) AppleWebKit/537.36 Version/4.0 Chrome/129.0 Mobile Safari/537.36"


def test_devices_os_browsers_and_in_app_from_the_user_agent(tmp_path: Path) -> None:
    uas = [
        IPAD,
        TABLET,
        IPHONE,
        MAC,
        CROS,
        CHROME + " Whale/3.27",
        CHROME + " Edg/129.0",
        KAKAO,
        WEBVIEW,
        IPHONE,
    ]
    write(
        tmp_path,
        "access.log",
        [line(T0 + i, "/", ua=ua, ip=f"10.0.{i}.0") for i, ua in enumerate(uas)],
    )
    people = summary(tmp_path, AFTER)["visitors"]
    assert people["devices"] == [["desktop", 0, 4], ["mobile", 0, 4], ["tablet", 0, 2]]
    assert people["os"] == [
        ["ios", 0, 4],
        ["android", 0, 2],
        ["windows", 0, 2],
        ["chromeos", 0, 1],
        ["macos", 0, 1],
    ]
    assert people["browsers"] == [
        ["safari", 0, 4],
        ["chrome", 0, 2],
        ["inapp", 0, 2],
        ["edge", 0, 1],
        ["whale", 0, 1],
    ]
    assert people["inApp"] == [["kakaotalk", 0, 1], ["other", 0, 1]]


def test_traits_use_the_first_1024_characters_and_pairs_the_whole_agent(
    tmp_path: Path,
) -> None:
    # 특성은 UA 앞 1,024자로 정하고(그 뒤 KAKAOTALK 은 인앱이 아니다), 짝은 UA 전체라 꼬리만 다른 둘은 두 짝 (038 §3.5)
    head = IPHONE + " " + "x" * (1_024 - len(IPHONE) - 1)
    lines = [line(T0 + i, "/", ua=f"{head} KAKAOTALK 10.4.{i}") for i in range(2)]
    write(tmp_path, "access.log", lines)
    people = summary(tmp_path, AFTER)["visitors"]
    assert people["shaped"] == 2 and people["inApp"] == []
    assert people["browsers"] == [["safari", 0, 2]]
