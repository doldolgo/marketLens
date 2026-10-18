"""관리자 화면 설명(스펙 071 §4 node) — '?' 말풍선(누름·다시 누름·밖 누름·Esc·글은 글로만)·두 줄 나누기·자리,
'누가' 판 묶음 넷(분모·막대 몫·0 빠짐·요청 종류 설명·방문자 값 없을 때), Clarity 진단 줄 세 경우와 숨김, 트래픽 칸 이름."""

from typing import Any

import pytest

from tests.admin_dom import run_admin

SCENARIO = r"""
const v = setup({ visible: false, respond: () => [404, {}] })
const help = await page('help.js')
const common = await page('common.js')

out.lines = [help.helpLines('뜻 줄\n읽는 법 줄'), help.helpLines('한 줄뿐'), help.helpLines('\n  \n가\n\n나\n다'), help.helpLines(null)]
// 자리 — 아래가 기본, 화면 아래가 모자라면 위, 좌우는 화면 안으로
const r = (top, left, w = 16, h = 16) => ({ top, left, width: w, height: h, bottom: top + h, right: left + w })
out.place = [
  help.placeOf(r(100, 500), [300, 80], [1200, 900]),
  help.placeOf(r(860, 500), [300, 80], [1200, 900]),
  help.placeOf(r(100, 2), [300, 80], [1200, 900]),
  help.placeOf(r(100, 1190), [300, 80], [1200, 900]),
  help.placeOf(r(100, 100), [380, 80], [390, 800]),
]

// 말풍선 — 큰 수 타일(단추) 안의 '?'. 문서 듣기는 가짜 문서에 쌓인다(잡기 단계 표시는 무시된다)
help.mountHelp()
const tile = common.el('button', 'card kpi')
const q = common.el('span', 'help')
q.setAttribute('title', '<img src=x onerror=alert(1)>\n두째 줄')
tile.append(q)
const ev = (target) => { const e = { target, stopped: false, prevented: false, stopPropagation() { this.stopped = true }, preventDefault() { this.prevented = true } }; return e }
const fire = (type, e) => (v.docListeners[type] || []).forEach((fn) => fn(e))
const pop = () => v.byId.get('help-pop')
const e1 = ev(q)
fire('click', e1)
out.opened = { stopped: e1.stopped, prevented: e1.prevented, expanded: q.getAttribute('aria-expanded'), described: q.getAttribute('aria-describedby'), role: q.getAttribute('role'), tab: q.getAttribute('tabindex'), title: q.getAttribute('title'), data: q.dataset.help, hidden: pop().hidden, role2: pop().getAttribute('role'), paras: pop().kids.length, text: pop().textContent, tags: walk(pop()).map((n) => n.tag) }
fire('click', ev(q))
out.again = { hidden: pop().hidden, expanded: q.getAttribute('aria-expanded'), closed: help.closeHelp() }
// 밖 누름
fire('click', ev(q))
fire('click', ev(common.el('div')))
out.outside = { hidden: pop().hidden }
// Esc 는 app.js 가 closeHelp() 로 묻는다 — 연 것이 있으면 true(상세 판은 닫지 않는다)
fire('click', ev(q))
out.esc = [help.closeHelp(), help.closeHelp(), pop().hidden]
// 마우스 올림 → 떠나면 닫힘, 올린 뒤 누름 → 고정(터치), 다시 누름 → 닫힘
fire('mouseover', ev(q))
const hovered = !pop().hidden
fire('mouseout', { target: q, relatedTarget: common.el('div') })
const left = pop().hidden
fire('mouseover', ev(q))
fire('click', ev(q))
fire('mouseout', { target: q, relatedTarget: common.el('div') })
const pinned = !pop().hidden
fire('click', ev(q))
out.hover = { hovered, left, pinned, after: pop().hidden }
// 키보드 — Enter·Space 로 열고 다른 키는 무시
const k1 = ev(q); k1.key = 'Enter'
fire('keydown', k1)
const k2 = ev(q); k2.key = 'x'
fire('keydown', k2)
out.keys = { open: !pop().hidden, prevented: k1.prevented, other: k2.prevented }
help.closeHelp()
// 한 줄 글은 뜻 단락만
const q2 = common.el('span', 'help')
q2.setAttribute('title', '하나')
fire('click', ev(q2))
out.single = { paras: pop().kids.length, text: pop().textContent }
help.closeHelp()

// '누가' 판 묶음 — 방문자 있음 / 없음
const td = await page('trafficdetail.js')
const visitors = {
  state: 'ok', shaped: 9, confirmed: 3,
  devices: [['desktop', 2, 6], ['mobile', 1, 3], ['tablet', 0, 0]],
  os: [['windows', 1, 5], ['ios', 1, 4]],
  browsers: [['chrome', 2, 7], ['kakaotalk', 0, 2]],
}
const part = { state: 'ok', visitors, classes: { browser: { requests: 60 }, scanner: { requests: 30 }, search: { requests: 10 }, ai: { requests: 0 } }, devices: [], browsers: [] }
out.who = td.whoSections(part)
out.whoNoVisitors = td.whoSections({ state: 'ok', classes: {}, devices: [['desktop', 4]], browsers: [['chrome', 4]] }).map((g) => [g.id, g.unit, g.items.length])
out.classNotes = Object.keys((await page('names.js')).CLASS_NOTE)
out.metricNow = td.METRIC_NAME.now

// Clarity 진단 줄
const ins = await page('insightdetail.js')
const clarity = (sessions, state = 'ok') => ({ state, numOfDays: 1, fetchedAt: 1792112161257, traffic: { sessions } })
const attn = (pvs, state = 'ok') => ({ state, rows: pvs.map((pv) => ({ pv })) })
out.diag = [
  ins.diagOf(clarity(0), attn([3, 4])),
  ins.diagOf(clarity(0), attn([])),
  ins.diagOf(clarity(5), attn([3])),
  ins.diagOf(clarity(0, 'error'), attn([3])),
  ins.diagOf(clarity(0), attn([3], 'error')),
  ins.diagOf(clarity(0), undefined),
  ins.diagOf({ ...clarity(0), why: 'http' }, attn([3])),
  ins.diagOf(clarity(null), attn([3])),
]
out.attnUrl = ins.ATTN_TODAY.url
"""


@pytest.fixture(scope="module")
def got() -> dict[str, Any]:
    return run_admin(SCENARIO)


def test_help_text_splits_into_meaning_and_how_to_read(got: dict[str, Any]) -> None:
    assert got["lines"] == [["뜻 줄", "읽는 법 줄"], ["한 줄뿐"], ["가", "나"], []]


def test_bubble_sits_below_or_above_and_stays_on_screen(got: dict[str, Any]) -> None:
    below, above, left_edge, right_edge, narrow = got["place"]
    assert below == {"top": 122, "left": 358}
    assert above == {"top": 774, "left": 358}
    assert left_edge["left"] == 8 and right_edge["left"] == 1200 - 8 - 300
    assert narrow["left"] == 8  # 말풍선이 화면보다 넓어도 왼쪽 여백에 붙는다


def test_click_opens_one_bubble_with_two_paragraphs_as_text_only(
    got: dict[str, Any],
) -> None:
    o = got["opened"]
    # 타일 단추까지 가지 않는다
    assert o["stopped"] and o["prevented"]
    assert o["expanded"] == "true" and o["described"] == "help-pop"
    assert o["role"] == "button" and o["tab"] == "0" and o["role2"] == "tooltip"
    # title 은 data-help 로 옮겨 기본 툴팁이 겹치지 않는다
    assert o["title"] is None and o["data"].startswith("<img")
    assert o["hidden"] is False and o["paras"] == 2
    assert o["text"] == "뜻<img src=x onerror=alert(1)>읽는 법두째 줄"
    assert "img" not in o["tags"]


def test_bubble_closes_on_second_click_outside_click_and_escape(
    got: dict[str, Any],
) -> None:
    assert got["again"] == {"hidden": True, "expanded": "false", "closed": False}
    assert got["outside"] == {"hidden": True}
    assert got["esc"] == [True, False, True]


def test_hover_opens_until_leaving_and_a_click_pins_it(got: dict[str, Any]) -> None:
    assert got["hover"] == {
        "hovered": True,
        "left": True,
        "pinned": True,
        "after": True,
    }


def test_enter_opens_and_other_keys_pass_through(got: dict[str, Any]) -> None:
    assert got["keys"] == {"open": True, "prevented": True, "other": False}


def test_single_line_help_shows_only_the_meaning(got: dict[str, Any]) -> None:
    assert got["single"] == {"paras": 1, "text": "뜻하나"}


def test_who_sections_have_their_own_denominator_and_explanations(
    got: dict[str, Any],
) -> None:
    devices, os_, browsers, classes = got["who"]
    assert [g["id"] for g in got["who"]] == ["devices", "os", "browsers", "classes"]
    assert devices["unit"] == "브라우저로 온 방문자 9명 기준"
    assert [(i["key"], i["value"], round(i["share"], 1)) for i in devices["items"]] == [
        ("desktop", 6, 66.7),
        ("mobile", 3, 33.3),
    ]  # 0 인 태블릿은 빠진다
    assert os_["unit"] == "브라우저로 온 방문자 9명 기준"
    assert [i["key"] for i in browsers["items"]] == ["chrome", "kakaotalk"]
    assert classes["unit"] == "모든 요청 100건 기준"
    assert [(i["key"], i["share"]) for i in classes["items"]] == [
        ("browser", 60.0),
        ("scanner", 30.0),
        ("search", 10.0),
    ]
    assert all(i["desc"] for i in classes["items"]) and not any(
        i["desc"] for i in devices["items"]
    )
    assert all(g["note"] for g in got["who"])
    assert set(got["classNotes"]) == {
        "browser",
        "search",
        "ai",
        "preview",
        "tool",
        "scanner",
        "operator",
        "unknown",
    }


def test_who_falls_back_to_page_counts_without_visitor_values(
    got: dict[str, Any],
) -> None:
    assert got["whoNoVisitors"] == [
        ["devices", "브라우저로 연 페이지 4건 기준", 1],
        ["os", "브라우저로 온 방문자 0명 기준", 0],
        ["browsers", "브라우저로 연 페이지 4건 기준", 1],
        ["classes", "모든 요청 0건 기준", 0],
    ]


def test_traffic_pane_is_named_dashboard_concurrent(got: dict[str, Any]) -> None:
    assert got["metricNow"] == "대시보드 동시 접속"


def test_clarity_diagnosis_line(got: dict[str, Any]) -> None:
    """071 §3.6 — 경고가 아니라 안내: 받은 시각과 두 수를 그대로, 어긋났다고 단정하지 않는다(토큰 탓을 하지 않는다)."""
    missed, empty, *hidden = got["diag"]
    assert missed["tone"] is None and "받은 최근 24시간 세션은 0" in missed["text"]
    assert (
        "오늘 페이지 보기 7회" in missed["text"]
        and "바로 견주지 못한다" in missed["text"]
    )
    assert "토큰" not in missed["text"] and "안 잡혔다" not in missed["text"]
    assert empty["tone"] is None and "비는 게 정상" in empty["text"]
    assert hidden == [None] * 6
    assert got["attnUrl"] == "/svc/api/admin/attention?days=1"
