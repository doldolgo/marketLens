"""관리자 '화면' 페이지의 계산·이름표 (스펙 065 §3.3·§3.4·§4 node) — screensmodel.js·screensnames.js 를 node 로 바로 부른다.

요소(c·100뷰당·마우스 올림·보인 시간·단계·순위·(기타)·지금 화면에 없음·표본 적음), 지표 바꿈, 영역(053 그대로), 떠난 곳·깊이,
덮어 보기 값 메시지, 이름표(EL_NAMES·틀 열쇠·자동 열쇠 여덟·(기타)), 그리고 052·061 의 목록을 이름표가 모두 덮는지.
"""

import json
import re
from typing import Any

from tests.admin_dom import run_admin
from tests.test_attention import DASHBOARD_COMMON, DASHBOARD_TABS, STATIC_AREAS
from tests.test_attention_elements import COMMON, DASHBOARD, SCRIPTED, STATIC
from tests.test_deploy import _text

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


def test_overlay_message_carries_mode_drawable_elements_tags_and_static_depth() -> None:
    """§3.5 — 값 메시지(053 + 065): 영역(053 꼴 — 페이지에 있는 것), 요소(지금 화면에 있고 단계가 있는 것만 — (기타)·없음·null 빼고,
    꼬리표 글 = 이름 · 고른 지표 값, small = 표본 적음), 깊이(정적 페이지의 도달 11칸, 대시보드는 null)."""
    row = _row(
        4,
        ELEMENTS,
        areas=[{"id": "hero", "ms": 8_000, "clicks": 1, "seen": 4}],
        depth=[1] * 11,
    )
    scenario = r"""
const m = await page('screensmodel.js')
const msg = (page, metric, present) => {
  const row = { ...input.row, page }
  return m.overlayMessage({ page, mode: 'element', metric, label: 'L', areas: m.areaValues(row, ['hero']), elements: m.elementValues(row, metric, present, 'pc'), depth: m.depthValues(row) })
}
out.landing = msg('landing', 'clicks', new Set(['hero|live:1']))
out.hover = msg('landing', 'hover', null).elements.map((e) => e.tag)
out.hoverKeys = msg('landing', 'hover', null).elements.map((e) => e.key)
out.dash = msg('app-spread', 'vis', null)
"""
    got = run_admin(scenario, {"row": row})
    land = got["landing"]
    assert {k: land[k] for k in ("type", "v", "label", "mode")} == {
        "type": "kt-attention",
        "v": 1,
        "label": "L",
        "mode": "element",
    }
    assert land["areas"] == [
        {
            "id": "hero",
            "level": 5,
            "rank": 1,
            "t": 2.0,
            "r": 100,
            "c": 25.0,
            "low": False,
            "small": True,
        }
    ]
    assert [
        (e["area"], e["key"], e["level"], e["rank"], e["small"])
        for e in land["elements"]
    ] == [
        ("hero", "live:1", 5, 1, True),
        ("events", "row:BTC", 4, 2, True),
    ]  # faq:rate 는 지금 화면에 없음 — 그리지 않는다, 행은 늘 있다
    assert (
        land["elements"][0]["tag"] == "지금 가장 큰 경로 카드 · 클릭 10 · 100뷰당 250.0"
    )
    assert land["depth"] == [
        100,
        90.9,
        81.8,
        72.7,
        63.6,
        54.5,
        45.5,
        36.4,
        27.3,
        18.2,
        9.1,
    ]
    assert got["hover"][:2] == [
        "지금 가장 큰 경로 카드 · 마우스 올림 평균 7.5초",
        "김프·역프란 링크 · 마우스 올림 평균 2.3초",
    ]
    # 단계가 null(m = 0 이고 보인 시간도 0)인 요소·행은 싣지 않는다 — 단계 0(보이기만)은 싣는다
    assert got["hoverKeys"] == ["live:1", "nav:kimp", "link:/app/", "faq:rate"]
    assert got["dash"]["depth"] is None
    assert (
        got["dash"]["elements"][0]["tag"]
        == "지금 가장 큰 경로 카드 · 보인 시간 평균 100.0초"
    )


def test_names_page_specific_template_and_auto_keys() -> None:
    """§3.4 — EL_NAMES(화면마다 뜻이 다르면 '<화면>|<열쇠>' 먼저), 틀 열쇠는 061 §7 이름 꼴, 목록에 없는 자동 열쇠 여덟, (기타), 그 밖은 열쇠
    그대로. 영역 이름은 053 표(대시보드는 공통 app 다음), 없으면 id."""
    cases = {
        "a": ["n.elName", ["app-spread", "row:BTC"]],
        "b": ["n.elName", ["app-history", "period:7d"]],
        "c": ["n.elName", ["app-history", "row:ETH"]],
        "d": ["n.elName", ["app-spread", "fx:gateio"]],
        "e": ["n.elName", ["app-history", "fx:okx"]],
        "f": ["n.elName", ["app-spread", "dom:all"]],
        "g": ["n.elName", ["app-history", "dom:all"]],
        "h": ["n.elName", ["app-history", "sort:sym"]],
        "i": ["n.elName", ["app-spread", "sort:sym"]],
        "j": ["n.elName", ["app-flow", "search"]],
        "k": ["n.elName", ["app-gap", "search"]],
        "l": ["n.elName", ["landing", "link:/kimp-chart"]],
        "m": ["n.elName", ["landing", "link:mailto"]],
        "n": ["n.elName", ["privacy", "btn:동의 철회"]],
        "o": ["n.elName", ["landing", "faq:왜 다른가"]],
        "p": ["n.elName", ["app-spread", "input:q"]],
        "q": ["n.elName", ["app-spread", "select:dom"]],
        "r": ["n.elName", ["privacy", "check:checkbox"]],
        "s": ["n.elName", ["app-gap", "tab:history"]],
        "t": ["n.elName", ["landing", "(기타)"]],
        "u": ["n.elName", ["landing", "weird-key"]],
        "v": ["n.elName", ["app-history", "interval:4h"]],
        "w": ["n.elName", ["kimp-chart", "coin:all"]],
        "x": ["n.elName", ["landing", "live:3"]],
        "y": ["n.areaName", ["app-gap", "kpi"]],
        "z": ["n.areaName", ["landing", "nope"]],
    }
    got = _call(*((k, fn, args) for k, (fn, args) in cases.items()))
    assert got == {
        "a": "코인 행 BTC",
        "b": "기간 1주",
        "c": "티커 행 ETH",
        "d": "비교 해외 거래소 Gate.io",
        "e": "해외 OKX",
        "f": "기준 국내 거래소 모두",
        "g": "국내 전체",
        "h": "티커 정렬",
        "i": "심볼 정렬",
        "j": "코인 심볼 입력칸",
        "k": "심볼 검색칸",
        "l": "링크 /kimp-chart",
        "m": "메일 링크",
        "n": "단추 동의 철회",
        "o": "질문 왜 다른가",
        "p": "입력칸 q",
        "q": "고르기 dom",
        "r": "체크 상자 checkbox",
        "s": "탭 기록/통계",
        "t": "(기타 — 열쇠 300 넘음)",
        "u": "weird-key",
        "v": "봉 종류 4시간",
        "w": "전체 코인 김프 차트",
        "x": "경로 카드 3위",
        "y": "KPI 줄",
        "z": "nope",
    }


SAMPLE = {
    "row": "BTC",
    "fx": "binance",
    "period": "7d",
    "interval": "1h",
    "chart-dom": "upbit",
    "fold": "okx",
    "card": "bybit",
    "dir": "kimp",
    "tab": "spread",
    "live": "3",
}


def _expand(page: str, key: str) -> str:
    """템플릿 열쇠 '<접두>:*' → 보기 값 하나."""
    return f"{key[:-2]}:{SAMPLE[key[:-2]]}" if key.endswith(":*") else key


def test_name_tables_cover_every_listed_element_key_and_area() -> None:
    """§4 정적 — EL_NAMES 가 061 목록(tests/test_attention_elements.py)의 화면별 열쇠를 모두 덮는다(틀 열쇠는 규칙 — 보기 값 하나로),
    AREA_NAMES 가 052 영역 id(tests/test_attention.py)를 모두 덮는다. 이름은 열쇠 그대로가 아닌 한국어."""
    screens: dict[str, list[str]] = {
        f"app-{tab}": [*COMMON, *keys] for tab, (_, keys) in DASHBOARD.items()
    }
    for page, pairs in STATIC.items():
        # 같은 열쇠가 영역 둘에 있으면(랜딩 머리·바닥의 nav:*) 이름은 하나
        screens[page] = list(dict.fromkeys(k for _, k in pairs)) + SCRIPTED.get(
            page, []
        )
    calls = [
        (f"{p}|{k}", "n.elName", [p, _expand(p, k)])
        for p, keys in screens.items()
        for k in keys
    ]
    areas = {
        p: [*DASHBOARD_COMMON, *a]
        for p, (_, a) in ((f"app-{t}", v) for t, v in DASHBOARD_TABS.items())
    } | STATIC_AREAS
    calls += [
        (f"area:{p}|{a}", "n.areaName", [p, a]) for p, ids in areas.items() for a in ids
    ]
    got = _call(*calls)
    assert len(got) == len(calls) > 150
    for name, text in got.items():
        key = name.split("|", 1)[1]
        assert re.search(r"[가-힣]", text) and text != key, (name, text)
    # AREA_NAMES 는 053 의 표 그대로(JSON 꼴) — 화면별 표의 id 가 052 목록과 같다
    block = re.search(
        r"\nconst AREA_NAMES = (\{\n.*?\n\});\n",
        _text("web/admin/screensnames.js"),
        flags=re.S,
    )
    assert block, "AREA_NAMES"
    names = json.loads(block.group(1))
    assert {p: list(t) for p, t in names.items() if p in STATIC_AREAS} == STATIC_AREAS
    assert list(names["app"]) == DASHBOARD_COMMON
    assert {t: sorted(names[f"app-{t}"]) for t in DASHBOARD_TABS} == {
        t: sorted(a) for t, (_, a) in DASHBOARD_TABS.items()
    }
