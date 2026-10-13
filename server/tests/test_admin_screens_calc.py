"""관리자 '화면' 페이지의 계산·이름표 (스펙 065 §3.3·§3.4·§4 node) — screensmodel.js·screensnames.js 를 node 로 바로 부른다.

요소(c·100뷰당·마우스 올림·보인 시간·단계·순위·(기타)·지금 화면에 없음·표본 적음), 지표 바꿈, 영역(053 그대로), 떠난 곳·깊이,
덮어 보기 값 메시지, 이름표(EL_NAMES·틀 열쇠·자동 열쇠 여덟·(기타)), 그리고 052·061 의 목록을 이름표가 모두 덮는지.
"""

from typing import Any

from tests.admin_dom import run_admin

# calls = [[이름, 'm.<함수>' | 'n.<함수>', [인자…]]] — 인자 {"$set": […]} 는 Set 으로
CALL = r"""
const m = await page('screensmodel.js')
const n = await page('screensnames.js')
const arg = (a) => (a && a.$set ? new Set(a.$set) : a)
for (const [name, fn, args] of input.calls) out[name] = (fn.startsWith('n.') ? n : m)[fn.slice(2)](...args.map(arg))
"""


def _call(*calls: tuple[str, str, list]) -> dict[str, Any]:
    return run_admin(CALL, {"calls": [list(c) for c in calls]})


def _row(pv: int, elements: list[list], **more: Any) -> dict:
    els = [
        {"area": a, "key": k, "clicks": c, "hoverMs": h, "visMs": v}
        for a, k, c, h, v in elements
    ]
    return {"page": "landing", "device": "pc", "pv": pv, "elements": els, **more}


ELEMENTS = [
    ["top", "nav:kimp", 40, 9_000, 120_000],
    ["hero", "live:1", 10, 30_000, 400_000],
    ["hero", "link:/app/", 10, 0, 50_000],  # 클릭이 live:1 과 같다 — 피드 차례
    ["faq", "faq:rate", 0, 0, 80_000],  # 클릭 0 · 보인 시간만 — 단계 0
    ["faq", "faq:cost", 0, 0, 0],  # 그리지 않음(null)
    ["events", "row:BTC", 7, 0, 0],  # 행 — 클릭만
    ["faq", "(기타)", 99, 0, 0],
]


def _ranks(v: dict) -> list[tuple]:
    return [(e["key"], e["level"], e["rank"]) for e in v["rows"]]


def test_element_values_counts_rates_levels_and_ranks_per_metric() -> None:
    """§3.3 — c = clicks, 100뷰당 = clicks/pv×100(소수 1자리), h = hoverMs/pv/1000초(휴대폰 '–'), v = visMs/pv/1000초(행 '–').
    단계 = ⌈m/최대×5⌉, m = 0 이고 보인 시간이 있으면 0, 그 밖의 m = 0 은 null(그리지 않음). 순위 = m 이 0 보다 큰 것의 m 큰 순(같으면
    피드 차례), (기타)는 순위·단계 없이 끝. 지표를 바꾸면 단계·순위가 그 값으로."""
    row = _row(40, ELEMENTS)
    got = _call(
        ("clicks", "m.elementValues", [row, "clicks", None, "pc"]),
        ("hover", "m.elementValues", [row, "hover", None, "pc"]),
        ("vis", "m.elementValues", [row, "vis", None, "pc"]),
        (
            "mobile",
            "m.elementValues",
            [{**row, "device": "mobile"}, "clicks", None, "mobile"],
        ),
    )
    tail = [("(기타)", None, None)]
    assert _ranks(got["clicks"]) == [
        ("nav:kimp", 5, 1),
        ("live:1", 2, 2),
        ("link:/app/", 2, 3),
        ("row:BTC", 1, 4),
        ("faq:rate", 0, None),
        ("faq:cost", None, None),
        *tail,
    ]
    assert _ranks(got["hover"]) == [
        ("live:1", 5, 1),
        ("nav:kimp", 2, 2),
        ("link:/app/", 0, None),
        ("faq:rate", 0, None),
        ("faq:cost", None, None),
        ("row:BTC", None, None),
        *tail,
    ]
    assert _ranks(got["vis"]) == [
        ("live:1", 5, 1),
        ("nav:kimp", 2, 2),
        ("faq:rate", 1, 3),
        ("link:/app/", 1, 4),
        ("faq:cost", None, None),
        ("row:BTC", None, None),
        *tail,
    ]
    nav, *_ = got["clicks"]["rows"]
    assert (nav["c"], nav["cRate"], nav["h"], nav["v"]) == (40, 100.0, 0.2, 3.0)
    btc = next(e for e in got["clicks"]["rows"] if e["key"] == "row:BTC")
    assert (btc["cRate"], btc["v"], btc["h"]) == (17.5, None, 0)
    assert got["clicks"]["rows"][-1]["state"] == "other"
    assert all(e["h"] is None for e in got["mobile"]["rows"])
    assert (got["clicks"]["small"], got["clicks"]["capped"], got["clicks"]["max"]) == (
        False,
        False,
        40,
    )


def test_absent_elements_go_last_without_rank_and_rows_are_always_present() -> None:
    """§3.6 — ready 의 elements 에 없는 열쇠는 '지금 화면에 없음'(순위 없이 끝에, 단계는 같은 최대 대비 5 에서 멈춤), 행은 늘 있는
    것으로. ready 를 받기 전(null)에는 판정하지 않는다. 단계·순위는 있는 요소끼리 — 없는 요소가 최대여도 있는 첫째가 5."""
    row = _row(4, ELEMENTS)
    present = {"$set": ["hero|live:1", "faq|faq:rate"]}
    got = _call(("v", "m.elementValues", [row, "clicks", present, "pc"]))["v"]
    assert _ranks(got) == [
        ("live:1", 5, 1),
        ("row:BTC", 4, 2),  # 행은 늘 있다
        ("faq:rate", 0, None),
        ("nav:kimp", 5, None),  # 없음 — 40/10 이지만 5 에서 멈춤
        ("link:/app/", 5, None),
        ("faq:cost", None, None),
        ("(기타)", None, None),
    ]
    assert [e["state"] for e in got["rows"]] == [
        "ok",
        "ok",
        "ok",
        "gone",
        "gone",
        "gone",
        "other",
    ]
    assert got["small"] is True  # pv 4 < 5
    many = _row(9, [["faq", f"k{i}", 1, 0, 1] for i in range(300)])
    assert (
        _call(("v", "m.elementValues", [many, "clicks", None, "pc"]))["v"]["capped"]
        is True
    )


def test_area_values_follow_053() -> None:
    """§3.3 영역(053 §3.3 그대로) — t = ms/pv 초, r = seen/pv %(100 에서 멈춤), c = clicks/pv×100, 단계 = ⌈ms×5/최대⌉, 순위 = ms 큰 순,
    거의 안 닿음 r < 20%. 페이지에 있는데 기록 없음(blank)·기록은 있는데 페이지에 없음(away)."""
    row = {
        "pv": 10,
        "areas": [
            {"id": "hero", "ms": 90_000, "clicks": 3, "seen": 12},
            {"id": "faq", "ms": 30_000, "clicks": 0, "seen": 1},
            {"id": "gone", "ms": 120_000, "clicks": 0, "seen": 10},
            {"id": "Bad", "ms": 1, "clicks": 0, "seen": 0},
        ],
    }
    got = _call(
        ("all", "m.areaValues", [row, None]),
        ("here", "m.areaValues", [row, ["hero", "faq", "top"]]),
    )
    assert [
        (a["id"], a["t"], a["r"], a["c"], a["level"], a["rank"], a["low"])
        for a in got["all"]["ranked"]
    ] == [
        ("gone", 12.0, 100, 0.0, 5, 1, False),
        ("hero", 9.0, 100, 30.0, 4, 2, False),
        ("faq", 3.0, 10, 0.0, 2, 3, True),
    ]
    here = got["here"]
    assert [(a["id"], a["level"], a["rank"]) for a in here["ranked"]] == [
        ("hero", 5, 1),
        ("faq", 2, 2),
    ]
    assert [(a["id"], a["state"], a["level"]) for a in here["blank"]] == [
        ("top", "none", 0)
    ]
    assert [(a["id"], a["state"], a["rank"], a["level"]) for a in here["away"]] == [
        ("gone", "gone", None, 5)
    ]


def test_exits_share_and_depth_reach_with_the_half_line() -> None:
    """§3.3 — 떠난 곳 = n/Σn %, 바탕 줄의 몫 = Σn/pv. 깊이 도달[j] = Σ depth[j..10] / Σ depth × 100(j = 0 은 100), '절반' = 도달이 처음
    50% 밑으로 가는 줄. Σ depth = 0 이면 도달 없음, 대시보드(depth null)는 null."""
    row = {
        "pv": 50,
        "exits": [["faq", 30], ["hero", 10], ["x", 0]],
        "depth": [10, 10, 10, 5, 5, 5, 5, 0, 0, 0, 10],
    }
    got = _call(
        ("exits", "m.exitValues", [row]),
        ("depth", "m.depthValues", [row]),
        ("zero", "m.depthValues", [{"depth": [0] * 11}]),
        ("dash", "m.depthValues", [{"depth": None}]),
    )
    assert got["exits"] == {
        "total": 40,
        "share": 80.0,
        "rows": [
            {"id": "faq", "n": 30, "pct": 75.0},
            {"id": "hero", "n": 10, "pct": 25.0},
        ],
    }
    reach = got["depth"]["reach"]
    assert [round(r, 2) for r in reach] == [
        100,
        83.33,
        66.67,
        50.0,
        41.67,
        33.33,
        25.0,
        16.67,
        16.67,
        16.67,
        16.67,
    ]
    assert (got["depth"]["half"], got["depth"]["total"]) == (4, 60)
    assert got["zero"]["reach"] is None and got["dash"] is None
