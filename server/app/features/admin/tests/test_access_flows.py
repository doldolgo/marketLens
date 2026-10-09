"""접속 흐름 — 페이지 이름·짝의 첫/마지막 페이지·페이지 수·`flows`·`entries`·`exits`·`depthPages` (스펙 062 §3·§4)."""

from pathlib import Path

import pytest

from app.features.admin.access_flows import PAGES, page_name
from app.features.admin.access_hours import FileTally
from app.features.admin.access_pairs import PairDay, PairKeys
from app.features.admin.tests.access_fakes import (
    AFTER,
    DAY,
    GATE,
    HOUR,
    IPHONE,
    NOW,
    Feeds,
    line,
    rotated,
    summary,
    write,
)
from app.features.attention import models as attention

T0 = AFTER - 2 * HOUR  # 오늘(KST) 08:30
OTHER = "(기타)"
NEW_KEYS = ["flows", "entries", "exits", "depthPages"]
PATHS = {
    "landing": "/",
    "app-spread": "/app/",
    "app-history": "/app/?tab=history",
    "app-gap": "/app/?tab=gap",
    "app-pp": "/app/?tab=pp",
    "app-health": "/app/?tab=health",
    "app-flow": "/app/?tab=flow",
    "privacy": "/privacy",
    "kimp-chart": "/kimp-chart",
    "kimp-history": "/kimp-history",
}


@pytest.mark.parametrize(
    ("uri", "name"),
    [
        ("/", "landing"),
        ("/app/", "app-spread"),
        ("/app/?tab=history", "app-history"),
        ("/app/?tab=bad", "app-spread"),
        ("/app/?tab=", "app-spread"),
        ("/app/?tab=History", "app-spread"),
        ("/app/?utm_source=x&tab=gap", "app-gap"),
        ("/app/x", "app-spread"),
        ("/app/x?tab=flow", "app-flow"),
        ("/privacy", "privacy"),
        ("/kimp-chart", "kimp-chart"),
        ("/kimp-history", "kimp-history"),
        ("/robots.txt", OTHER),
        ("/app", OTHER),
        ("/privacy/", OTHER),
        ("/landing", OTHER),
    ],
)
def test_page_name_by_path_and_query(uri: str, name: str) -> None:
    path, _, query = uri.partition("?")
    assert page_name(path, query) == name


def test_page_names_are_the_attention_screen_names() -> None:
    # 061·052 의 화면 이름과 같은 낱말 — 끝의 (기타) 만 더한다
    assert PAGES == (*attention.PAGES, OTHER)


def _pairs(lines: list[str]) -> list[PairDay]:
    tally = FileTally(0, GATE, PairKeys(lambda n: b"k" * n))
    tally.read(lines)
    return [p for record in tally.days.values() for p in record.pairs.values()]


@pytest.mark.parametrize(("n", "npages"), [(1, 1), (2, 2), (5, 5), (6, 6), (300, 255)])
def test_page_count_of_one_pair_stops_at_255(n: int, npages: int) -> None:
    lines = [line(T0 + i, "/") for i in range(n)]
    pairs = _pairs([*lines, line(T0 + n, "/clarity.js")])
    assert [(p.entry, p.exit, p.npages) for p in pairs] == [
        ("landing", "landing", npages)
    ]


def test_entry_and_exit_go_by_time_then_line_order() -> None:
    lines = [
        line(T0 + 10, "/"),
        line(T0, "/privacy"),  # 가장 이른 시각의 앞 줄
        line(T0, "/app/?tab=gap"),
        line(T0 + 10, "/kimp-chart"),  # 가장 늦은 시각의 뒤 줄
        line(T0 + 11, "/clarity.js"),  # 페이지 줄이 아니다
    ]
    assert [(p.entry, p.exit, p.npages) for p in _pairs(lines)] == [
        ("privacy", "kimp-chart", 4)
    ]


def test_a_pair_with_only_js_or_ws_lines_has_no_flow(tmp_path: Path) -> None:
    write(
        tmp_path,
        "access.log",
        [
            line(T0, "/clarity.js", ip="198.51.100.0"),
            line(T0 + 1, "/api/ws/spreads", status=101, ip="198.51.100.0"),
            line(T0, "/", ua=IPHONE),
        ],
    )
    people = summary(tmp_path, AFTER)["visitors"]
    assert (people["shaped"], people["confirmed"]) == (2, 1)
    assert people["flows"] == [["direct", "landing", "landing", 0, 1]]
    assert (
        sum(r[2] for r in people["entries"]) == 1 == sum(r[2] for r in people["exits"])
    )
    # 페이지 줄이 없는 짝 = 채널 unknown 인 짝
    unknown = {r[0]: r[2] for r in people["channels"]}["unknown"]
    assert sum(r[2] for r in people["entries"]) == people["shaped"] - unknown


def test_operator_and_probing_pairs_are_left_out_that_day(tmp_path: Path) -> None:
    write(
        tmp_path,
        "access.log",
        [
            line(T0, "/app/", referer="http://localhost:5173", ip="192.0.2.0"),
            line(T0 + 1, "/privacy", ip="192.0.2.0"),
            line(T0, "/", ip="198.51.100.0"),
            line(T0 + 1, "/wp-login.php", status=404, ip="198.51.100.0"),
            line(T0, "/kimp-chart", ua=IPHONE),
        ],
    )
    people = summary(tmp_path, AFTER)["visitors"]
    assert people["flows"] == [["direct", "kimp-chart", "kimp-chart", 0, 1]]
    assert people["depthPages"] == {
        "1": [0, 1],
        "2": [0, 0],
        "3-5": [0, 0],
        "6+": [0, 0],
    }


def test_depth_buckets_and_all_page_names_in_entries_and_exits(tmp_path: Path) -> None:
    lines = []
    for k, n in enumerate((1, 2, 3, 5, 6, 300)):
        ip = f"10.0.{k}.0"
        lines += [line(T0 + i, "/app/?tab=bad" if i else "/", ip=ip) for i in range(n)]
    lines.append(line(T0 + 400, "/clarity.js", ip="10.0.5.0"))  # 300쪽 짝만 확인
    write(tmp_path, "access.log", lines)
    people = summary(tmp_path, AFTER)["visitors"]
    assert people["depthPages"] == {
        "1": [0, 1],
        "2": [0, 1],
        "3-5": [0, 2],
        "6+": [1, 2],
    }
    # 0 인 이름도 모두 — shaped 큰 순, 같으면 이름순
    zeros = sorted(set(PAGES) - {"landing"})
    assert people["entries"] == [["landing", 1, 6], *([n, 0, 0] for n in zeros)]
    zeros = sorted(set(PAGES) - {"landing", "app-spread"})
    assert people["exits"] == [
        ["app-spread", 1, 5],
        ["landing", 0, 1],
        *([n, 0, 0] for n in zeros),
    ]
    assert people["flows"] == [
        ["direct", "landing", "app-spread", 1, 5],
        ["direct", "landing", "landing", 0, 1],
    ]


def test_a_day_split_over_files_takes_entry_from_the_earlier_and_exit_from_the_later(
    tmp_path: Path,
) -> None:
    tie = T0 - HOUR
    rotated(tmp_path, [line(tie - 60, "/x.css"), line(tie, "/privacy")])  # 앞 파일
    rotated(tmp_path, [line(tie, "/kimp-chart"), line(tie + 30, "/a.js")])  # 뒤 파일
    write(tmp_path, "access.log", [line(T0, "/clarity.js")])
    people = summary(tmp_path, AFTER)["visitors"]
    # 같은 시각의 두 페이지 줄 — 첫 페이지는 앞 파일, 마지막 페이지는 뒤 파일(줄 순서)
    assert people["flows"] == [["direct", "privacy", "kimp-chart", 1, 1]]
    assert people["depthPages"]["2"] == [1, 1]


def test_flows_keep_forty_rows_and_sum_the_rest(tmp_path: Path) -> None:
    names = list(PATHS)
    combos = [(e, x) for e in names for x in names][:45]
    lines = []
    for k, (entry, exit_) in enumerate(combos):
        for j in range(3 if k < 5 else 2 if k < 15 else 1):
            ip = f"10.{k}.{j}.0"
            lines += [line(T0, PATHS[entry], ip=ip), line(T0 + 5, PATHS[exit_], ip=ip)]
            if j == 0 and k % 2:
                lines.append(line(T0 + 6, "/app/clarity.js", ip=ip))
    write(tmp_path, "access.log", lines)
    people = summary(tmp_path, AFTER)["visitors"]
    flows = people["flows"]
    assert (
        len(flows) == 41 and [r[4] for r in flows[:40]] == [3] * 5 + [2] * 10 + [1] * 25
    )
    for a, b in zip(flows[:40], flows[1:40], strict=False):
        assert a[4] > b[4] or (a[4] == b[4] and a[:3] < b[:3])
    assert {tuple(r[1:3]) for r in flows[:40]} < set(combos)
    confirmed = sum(1 for k in range(45) if k % 2)
    assert flows[40] == [
        OTHER,
        OTHER,
        OTHER,
        confirmed - sum(r[3] for r in flows[:40]),
        5,
    ]
    assert sum(r[4] for r in flows) == people["shaped"] == 65
    for key in ("entries", "exits"):
        assert sum(r[2] for r in people[key]) == 65
        assert sum(r[1] for r in people[key]) == people["confirmed"] == confirmed
    assert sum(s for _, s in people["depthPages"].values()) == 65


def test_exactly_forty_flows_have_no_rest_row(tmp_path: Path) -> None:
    names = list(PATHS)
    combos = [(e, x) for e in names for x in names][:40]
    lines = []
    for k, (entry, exit_) in enumerate(combos):
        ip = f"10.{k}.0.0"
        lines += [line(T0, PATHS[entry], ip=ip), line(T0 + 5, PATHS[exit_], ip=ip)]
    write(tmp_path, "access.log", lines)
    flows = summary(tmp_path, AFTER)["visitors"]["flows"]
    assert len(flows) == 40 and all(r[0] == "direct" for r in flows)


def test_entry_and_exit_belong_to_the_day_of_a_pair_counted_in_the_window(
    tmp_path: Path,
) -> None:
    # 어제(KST) 09:00 의 페이지 줄은 24시간 창 앞이지만, 창 안의 WS 101 로 038 이 세는 짝 — 그날의 첫·마지막 페이지
    yesterday = GATE + DAY + 9 * HOUR
    write(
        tmp_path,
        "access.log",
        [
            line(yesterday, "/app/?tab=gap"),
            line(yesterday + 3 * HOUR, "/api/ws/spreads", status=101, duration=10_800),
        ],
    )
    for window in ("24h", "7d"):
        people = summary(tmp_path, AFTER, window)["visitors"]
        assert (people["shaped"], people["confirmed"]) == (1, 1)
        assert people["flows"] == [["direct", "app-gap", "app-gap", 1, 1]]


def test_a_visit_over_midnight_counts_as_two_pairs(tmp_path: Path) -> None:
    midnight = GATE + 2 * DAY  # 오늘 00:00 KST
    write(
        tmp_path,
        "access.log",
        [line(midnight - 30, "/"), line(midnight + 30, "/privacy")],
    )
    people = summary(tmp_path, AFTER, "7d")["visitors"]
    assert people["flows"] == [
        ["direct", "landing", "landing", 0, 1],
        ["direct", "privacy", "privacy", 0, 1],
    ]


def test_keys_are_added_after_the_old_ones_and_absent_before_the_gate(
    tmp_path: Path,
) -> None:
    (tmp_path / "before").mkdir()
    write(tmp_path / "before", "access.log", [line(NOW - 60, "/")])
    before = summary(tmp_path / "before", NOW)["visitors"]
    assert before["state"] == "unconfigured" and not set(NEW_KEYS) & set(before)
    write(tmp_path, "access.log", [line(AFTER - 60, "/")])
    after = summary(tmp_path, AFTER)["visitors"]
    old = "state code sinceTs confirmed shaped returning capped days channels devices os browsers inApp"
    assert list(after) == [*old.split(), *NEW_KEYS]


async def test_the_feed_answers_with_the_flow_keys(tmp_path: Path) -> None:
    write(tmp_path, "access.log", [line(T0, "/privacy"), line(T0 + 1, "/clarity.js")])
    body = await Feeds(tmp_path, AFTER).get("7d")
    assert (body["state"], body["window"]) == ("ok", "7d")
    assert body["visitors"]["flows"] == [["direct", "privacy", "privacy", 1, 1]]
    assert body["visitors"]["depthPages"]["1"] == [1, 1]
