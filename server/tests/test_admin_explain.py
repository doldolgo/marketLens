"""관리자 화면 설명(스펙 071 §4 node) — '?' 말풍선(누름·다시 누름·밖 누름·Esc·말풍선 안 스크롤·마우스로 옮겨 가기·글은 글로만)·칸 넷
나누기(보여 주는 것·낱말 뜻·자료 출처·읽는 법, 칸 이름 없는 두 줄)·자리와 높이, '누가' 판 묶음 넷(분모·막대 몫·0 빠짐·요청 종류 설명·
방문자 값 없을 때), Clarity 진단 줄 세 경우와 숨김, 트래픽 칸 이름."""

import html
import re
from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_screen import PAGES
from tests.test_deploy import _text

SCENARIO = r"""
const v = setup({ visible: false, respond: () => [404, {}] })
const help = await page('help.js')
const common = await page('common.js')

const FULL = '보여 주는 것: 지금 열린 탭 수.\n낱말 뜻:\n· WS 연결 — 실시간 표를 받는 연결\n·  설명만 있는 줄\n자료 출처: api 가 센다.\n이어지는 줄.\n읽는 법: 0 이면 아무도 없다.'
const parts = (t) => help.helpParts(t).map((p) => [p.key, p.head, p.text, p.items])
out.parts = [parts(FULL), parts('뜻 줄\n읽는 법 줄'), parts('한 줄뿐'), parts('\n  \n가\n\n나\n다'), parts(null), parts('머리 없는 줄\n읽는 법: 끝')]
out.what = [help.helpWhat(FULL), help.helpWhat('옛 뜻\n옛 읽는 법'), help.helpWhat('')]
// 자리 — 아래가 기본, 아래가 모자라고 위에 들어가면 위, 둘 다 모자라면 넓은 쪽에 그 높이만큼(600 한도), 좌우는 화면 안으로
const r = (top, left, w = 16, h = 16) => ({ top, left, width: w, height: h, bottom: top + h, right: left + w })
out.place = [
  help.placeOf(r(100, 500), [300, 80], [1200, 900]),
  help.placeOf(r(860, 500), [300, 80], [1200, 900]),
  help.placeOf(r(100, 2), [300, 80], [1200, 900]),
  help.placeOf(r(100, 1190), [300, 80], [1200, 900]),
  help.placeOf(r(100, 100), [380, 80], [390, 800]),
  help.placeOf(r(400, 100), [400, 900], [1200, 900]),
  help.placeOf(r(600, 100), [400, 900], [1200, 900]),
  help.placeOf(r(40, 100), [374, 2000], [390, 844]),
]

// 말풍선 — 큰 수 타일(단추) 안의 '?'. 문서 듣기는 가짜 문서에 쌓인다(잡기 단계 표시는 무시된다)
help.mountHelp()
const tile = common.el('button', 'card kpi')
const q = common.el('span', 'help')
q.setAttribute('title', '<img src=x onerror=alert(1)>\n두째 줄')
tile.append(q)
const ev = (target) => { const e = { target, stopped: false, prevented: false, stopPropagation() { this.stopped = true }, preventDefault() { this.prevented = true } }; return e }
const fire = (type, e) => (v.docListeners[type] || []).forEach((fn) => fn(e))
const scroll = (target) => (v.winListeners.scroll || []).forEach((fn) => fn({ target }))
const pop = () => v.byId.get('help-pop')
const inner = () => pop().kids[0]
const e1 = ev(q)
fire('click', e1)
out.opened = { stopped: e1.stopped, prevented: e1.prevented, expanded: q.getAttribute('aria-expanded'), described: q.getAttribute('aria-describedby'), role: q.getAttribute('role'), tab: q.getAttribute('tabindex'), title: q.getAttribute('title'), data: q.dataset.help, hidden: pop().hidden, role2: pop().getAttribute('role'), body: inner().className, parts: inner().kids.length, text: pop().textContent, tags: walk(pop()).map((n) => n.tag), side: pop().dataset.side }
fire('click', ev(q))
out.again = { hidden: pop().hidden, expanded: q.getAttribute('aria-expanded'), closed: help.closeHelp() }
// 밖 누름 · 말풍선 안 누름
fire('click', ev(q))
fire('click', ev(inner().kids[0]))
const stayed = !pop().hidden
fire('click', ev(common.el('div')))
out.outside = { stayed, hidden: pop().hidden }
// 스크롤 — 말풍선 안 스크롤은 남고, 페이지 스크롤은 닫는다
fire('click', ev(q))
scroll(inner())
const scrolledInside = !pop().hidden
scroll(document)
out.scroll = { inside: scrolledInside, page: pop().hidden }
// Esc 는 app.js 가 closeHelp() 로 묻는다 — 연 것이 있으면 true(상세 판은 닫지 않는다)
fire('click', ev(q))
out.esc = [help.closeHelp(), help.closeHelp(), pop().hidden]
// 마우스 올림 → 떠나면 닫힘, 말풍선으로 옮겨 가면 남고 말풍선 밖으로 나가면 닫힘, 올린 뒤 누름 → 고정(터치), 다시 누름 → 닫힘
fire('mouseover', ev(q))
const hovered = !pop().hidden
fire('mouseout', { target: q, relatedTarget: common.el('div') })
const left = pop().hidden
fire('mouseover', ev(q))
fire('mouseout', { target: q, relatedTarget: inner() })
const intoPop = !pop().hidden
fire('mouseout', { target: inner().kids[0], relatedTarget: inner() })
const withinPop = !pop().hidden
fire('mouseout', { target: inner(), relatedTarget: q })
const backToQ = !pop().hidden
fire('mouseout', { target: q, relatedTarget: inner() })
fire('mouseout', { target: inner(), relatedTarget: common.el('div') })
const outOfPop = pop().hidden
fire('mouseover', ev(q))
fire('click', ev(q))
fire('mouseout', { target: q, relatedTarget: common.el('div') })
const pinned = !pop().hidden
fire('click', ev(q))
out.hover = { hovered, left, intoPop, withinPop, backToQ, outOfPop, pinned, after: pop().hidden }
// 키보드 — Enter·Space 로 열고 다른 키는 무시
const k1 = ev(q); k1.key = 'Enter'
fire('keydown', k1)
const k2 = ev(q); k2.key = 'x'
fire('keydown', k2)
out.keys = { open: !pop().hidden, prevented: k1.prevented, other: k2.prevented }
help.closeHelp()
// 한 줄 글은 보여 주는 것 칸만
const q2 = common.el('span', 'help')
q2.setAttribute('title', '하나')
fire('click', ev(q2))
out.single = { parts: inner().kids.length, text: pop().textContent }
help.closeHelp()
// 칸 넷 — 칸 이름(h3)·글(p)·낱말 목록(ul > li > b + span), 낱말 없는 줄은 span 하나
const q3 = common.el('span', 'help')
q3.setAttribute('title', FULL)
fire('click', ev(q3))
out.full = inner().kids.map((sec) => ({ cls: sec.className, tags: sec.kids.map((k) => k.tag), text: sec.textContent }))
out.items = inner().kids[1].kids[1].kids.map((li) => li.kids.map((k) => [k.tag, k.textContent]))
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


def test_help_text_splits_into_four_parts(got: dict[str, Any]) -> None:
    full, legacy, single, blanks, empty, headless = got["parts"]
    assert full == [
        ["what", "보여 주는 것", "지금 열린 탭 수.", []],
        [
            "words",
            "낱말 뜻",
            "",
            [["WS 연결", "실시간 표를 받는 연결"], ["", "설명만 있는 줄"]],
        ],
        ["source", "자료 출처", "api 가 센다. 이어지는 줄.", []],
        ["how", "읽는 법", "0 이면 아무도 없다.", []],
    ]
    # 칸 이름 없는 글(071 첫 판의 두 줄) — 첫 줄 = 보여 주는 것, 둘째 줄 = 읽는 법, 셋째부터 버림
    assert legacy == [
        ["what", "보여 주는 것", "뜻 줄", []],
        ["how", "읽는 법", "읽는 법 줄", []],
    ]
    assert single == [["what", "보여 주는 것", "한 줄뿐", []]]
    assert blanks == [["what", "보여 주는 것", "가", []], ["how", "읽는 법", "나", []]]
    assert empty == []
    # 칸 이름 앞의 줄은 보여 주는 것 칸
    assert headless == [
        ["what", "보여 주는 것", "머리 없는 줄", []],
        ["how", "읽는 법", "끝", []],
    ]
    assert got["what"] == ["지금 열린 탭 수.", "옛 뜻", ""]


def test_bubble_sits_below_or_above_and_stays_on_screen(got: dict[str, Any]) -> None:
    below, above, left_edge, right_edge, narrow, tall_low, tall_high, phone = got[
        "place"
    ]
    assert below == {"top": 122, "left": 358, "height": 80, "side": "below"}
    assert above == {"top": 774, "left": 358, "height": 80, "side": "above"}
    assert left_edge["left"] == 8 and right_edge["left"] == 1200 - 8 - 300
    assert narrow["left"] == 8  # 말풍선이 화면보다 넓어도 왼쪽 여백에 붙는다
    # 위아래 다 모자라면 넓은 쪽에 그 자리만큼(안에서 스크롤), 높이는 600 을 넘지 않는다
    assert tall_low == {"top": 422, "left": 8, "height": 470, "side": "below"}
    assert tall_high == {"top": 8, "left": 8, "height": 586, "side": "above"}
    assert phone["side"] == "below" and phone["height"] == 600 and phone["top"] == 62


def test_click_opens_one_bubble_with_parts_as_text_only(
    got: dict[str, Any],
) -> None:
    o = got["opened"]
    # 타일 단추까지 가지 않는다
    assert o["stopped"] and o["prevented"]
    assert o["expanded"] == "true" and o["described"] == "help-pop"
    assert o["role"] == "button" and o["tab"] == "0" and o["role2"] == "tooltip"
    # title 은 data-help 로 옮겨 기본 툴팁이 겹치지 않는다
    assert o["title"] is None and o["data"].startswith("<img")
    assert o["hidden"] is False and o["body"] == "help-body" and o["parts"] == 2
    assert o["text"] == "보여 주는 것<img src=x onerror=alert(1)>읽는 법두째 줄"
    assert "img" not in o["tags"]
    assert o["side"] in ("below", "above")


def test_bubble_closes_on_second_click_outside_click_page_scroll_and_escape(
    got: dict[str, Any],
) -> None:
    assert got["again"] == {"hidden": True, "expanded": "false", "closed": False}
    # 말풍선 안 누름은 닫지 않는다
    assert got["outside"] == {"stayed": True, "hidden": True}
    assert got["scroll"] == {"inside": True, "page": True}
    assert got["esc"] == [True, False, True]


def test_hover_stays_while_moving_into_the_bubble_and_a_click_pins_it(
    got: dict[str, Any],
) -> None:
    assert got["hover"] == {
        "hovered": True,
        "left": True,
        "intoPop": True,
        "withinPop": True,
        "backToQ": True,
        "outOfPop": True,
        "pinned": True,
        "after": True,
    }


def test_enter_opens_and_other_keys_pass_through(got: dict[str, Any]) -> None:
    assert got["keys"] == {"open": True, "prevented": True, "other": False}


def test_single_line_help_shows_only_what_it_shows(got: dict[str, Any]) -> None:
    assert got["single"] == {"parts": 1, "text": "보여 주는 것하나"}


def test_four_parts_render_heads_text_and_word_list(got: dict[str, Any]) -> None:
    assert [p["cls"] for p in got["full"]] == ["help-part"] * 4
    assert [p["tags"] for p in got["full"]] == [
        ["h3", "p"],
        ["h3", "ul"],
        ["h3", "p"],
        ["h3", "p"],
    ]
    assert got["full"][2]["text"] == "자료 출처api 가 센다. 이어지는 줄."
    assert got["items"] == [
        [["b", "WS 연결"], ["span", " — 실시간 표를 받는 연결"]],
        [["span", "설명만 있는 줄"]],
    ]


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


# --- '?' 글의 칸 규칙(071 §3.2 — 정적) -------------------------------------------------------------------

HEADS = ("보여 주는 것", "낱말 뜻", "자료 출처", "읽는 법")
NEEDED = {"보여 주는 것", "자료 출처", "읽는 법"}
LINE_MAX = 180
TOTAL_MAX = 900
# 페이지마다 '?' 수의 바닥 — 카드가 빠지지 않게(066 의 카드 + 화면 페이지 판 머리)
AT_LEAST = {
    "index.html": 8,
    "server.html": 13,
    "traffic.html": 15,
    "visitors.html": 12,
    "clarity.html": 13,
    "screens.html": 5,
}


def _helps(page: str) -> list[tuple[str, str]]:
    """(그 '?' 앞의 가장 가까운 id, 글) — 글은 HTML 글자를 푼 title."""
    src = _text(f"web/admin/{page}")
    out = []
    for m in re.finditer(r'<span class="help" title="([^"]*)">', src):
        ids = re.findall(r'id="([^"]+)"', src[max(0, m.start() - 800) : m.start()])
        out.append((ids[-1] if ids else "", html.unescape(m.group(1))))
    return out


KEYS = dict(zip(HEADS, ("what", "words", "source", "how"), strict=True))


def _parts(text: str) -> list[dict[str, Any]]:
    """칸 규칙을 지킨 글 → [{key, text, items: [(낱말, 뜻)…]}] — help.js helpParts 와 같은 나누기(정적 시험용)."""
    out: list[dict[str, Any]] = []
    for line in (x.strip() for x in text.split("\n")):
        head = next((h for h in HEADS if line.startswith(f"{h}:")), None)
        if head:
            out.append(
                {"key": KEYS[head], "text": line[len(head) + 1 :].strip(), "items": []}
            )
        elif line.startswith("· ") and out:
            word, _, meaning = line[2:].partition(" — ")
            out[-1]["items"].append((word, meaning))
    return out


def _problems(text: str) -> list[str]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    bad = []
    if sum(len(line) for line in lines) > TOTAL_MAX:
        bad.append("너무 길다")
    heads: list[str] = []
    for line in lines:
        if len(line) > LINE_MAX:
            bad.append(f"줄이 길다: {line[:30]}")
        head = next((h for h in HEADS if line.startswith(f"{h}:")), None)
        if head:
            heads.append(head)
            body = line[len(head) + 1 :].strip()
            if (head == "낱말 뜻") != (body == ""):
                bad.append(f"{head} 칸 글 꼴")
        elif line.startswith("· "):
            word, sep, meaning = line[2:].partition(" — ")
            if (
                not heads
                or heads[-1] != "낱말 뜻"
                or not sep
                or not word
                or not meaning
            ):
                bad.append(f"낱말 줄 꼴: {line[:30]}")
        else:
            bad.append(f"칸 이름 없는 줄: {line[:30]}")
    if not lines or not lines[0].startswith("보여 주는 것:"):
        bad.append("보여 주는 것 칸으로 시작하지 않는다")
    if heads != [h for h in HEADS if h in heads] or len(heads) != len(set(heads)):
        bad.append(f"칸 순서: {heads}")
    if not NEEDED <= set(heads):
        bad.append(f"빠진 칸: {NEEDED - set(heads)}")
    if "낱말 뜻" in heads:
        at = lines.index("낱말 뜻:")
        if at + 1 >= len(lines) or not lines[at + 1].startswith("· "):
            bad.append("낱말 뜻 칸이 비었다")
    return bad


def test_every_help_text_follows_the_four_part_rule() -> None:
    """071 §3.2 — 칸 이름 붙은 줄(보여 주는 것 · 낱말 뜻 · 자료 출처 · 읽는 법, 이 순서), 셋은 꼭, 낱말 뜻은 '· 낱말 — 뜻' 줄들,
    줄마다 180자·글 하나 900자 이하. 페이지마다 '?' 수는 바닥 이상."""
    for page in PAGES:
        helps = _helps(page)
        assert len(helps) >= AT_LEAST[page], page
        for at, text in helps:
            assert _problems(text) == [], (page, at, _problems(text))


def test_the_rule_catches_the_old_two_line_form() -> None:
    assert "보여 주는 것 칸으로 시작하지 않는다" in _problems("뜻 줄\n읽는 법 줄")
    assert (
        _problems(
            "보여 주는 것: 가.\n낱말 뜻:\n· 나 — 다\n자료 출처: 라.\n읽는 법: 마."
        )
        == []
    )
    assert _problems("보여 주는 것: 가.\n읽는 법: 마.\n자료 출처: 라.") == [
        "칸 순서: ['보여 주는 것', '읽는 법', '자료 출처']"
    ]
    assert "낱말 뜻 칸이 비었다" in _problems(
        "보여 주는 것: 가.\n낱말 뜻:\n자료 출처: 라.\n읽는 법: 마."
    )
