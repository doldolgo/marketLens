"""관리자 '화면' 페이지의 부르기·주소·틀·판 (스펙 065 §3.2·§3.6·§3.7·§3.8·§4 node).

066 의 가짜 DOM(tests/admin_dom.py)에 screens.js 를 싣고, 가짜 피드 서버의 attention 응답으로 그린다. 틀은 가짜 창(contentWindow)
— 보낸 메시지를 모으고, 시나리오가 ready·다른 출처 메시지를 window 'message' 로 보낸다. setTimeout 은 기록만 해 두고 시나리오가 부른다
(5초 무응답). 경우마다 node 하나(모듈 상태가 새지 않게).
"""

from typing import Any

from tests.admin_dom import run_admin
from tests.test_admin_detail import FAST, answers

SITE = "https://kimptrack.com"
ATT = "/svc/api/admin/attention"


def _urls() -> list[str]:
    pages = (
        "landing",
        "privacy",
        "kimp-chart",
        "kimp-history",
        "app-spread",
        "app-history",
        "app-gap",
        "app-pp",
        "app-health",
        "app-flow",
    )
    days = (1, 7, 30, 90)
    return [
        *FAST,
        *(f"{ATT}?days={d}" for d in days),
        *(f"{ATT}?days={d}&page={p}" for d in days for p in pages),
    ]


HARNESS = r"""
const respond = (url) => input.answers[url] || [404, { detail: 'Not Found' }]
const BUTTONS = { device: ['pc', 'mobile'], mode: ['element', 'area', 'depth'], metric: ['clicks', 'hover', 'vis'], panel: ['elements', 'areas', 'exits'],
  range: ['1d', '7d', '30d', '90d'], cmp: ['none', 'prev', 'week'], detail: ['elements', 'areas', 'exits'], scmp: ['none', 'prev', 'week'] }
const v = setup({ respond, search: input.search || '', buttons: BUTTONS })
document.getElementById('sheet').hidden = true
const posted = []
const win = { postMessage: (m, o) => posted.push([JSON.parse(JSON.stringify(m)), o]) }
document.getElementById('frame').contentWindow = win
const timers = []
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length }
globalThis.clearTimeout = (id) => { if (timers[id - 1]) timers[id - 1].fn = null }
await page('screens.js')
await flush()
const fire = (data, o = {}) => (v.winListeners.message || []).forEach((fn) => fn({ origin: o.origin ?? 'https://kimptrack.com', source: o.source ?? win, data }))
const ready = (pg, areas, elements) => fire({ type: 'kt-attention-ready', v: 1, page: pg, areas, ...(elements ? { elements } : {}) })
const pressedOf = (attr) => v.groups[attr].filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset[attr])
const rows = (id) => (document.getElementById(id).kids[1]?.kids || []).map((tr) => ({ cls: tr.className, cells: tr.kids.map((td) => td.textContent), tr }))
const snap = () => ({
  calls: [...v.s.calls], src: document.getElementById('frame').src ?? null, url: v.s.cleared.at(-1) ?? null, here: document.getElementById('here').attrs.href,
  device: pressedOf('device'), mode: pressedOf('mode'), metric: pressedOf('metric'), metricHidden: document.getElementById('metric-seg').hidden,
  off: [...v.groups.mode, ...v.groups.metric].filter((b) => b.disabled).map((b) => [b.dataset.mode ?? b.dataset.metric, b.title]),
  chips: document.getElementById('pick-page').kids.map((b) => [b.textContent, b.attrs['aria-pressed'], b.className]),
  line: v.text('frame-line'), small: !document.getElementById('frame-small').hidden, stage: document.getElementById('stage').className,
  note: document.getElementById('frame-note').hidden ? null : v.text('frame-note'), panelNote: v.text('panel-note'),
  tags: [v.text('t-frame'), v.text('t-panel')], foot: v.text('el-foot'),
  elements: rows('tbl-elements').map((r) => [r.cls, ...r.cells.slice(0, 3)]),
  posted: posted.map(([m, o]) => [m, o]),
})
"""


def _run(scenario: str, steps: str, *, search: str = "", tweak: Any = None) -> dict:
    feeds = answers(scenario, _urls())
    if tweak:
        tweak(feeds)
    return run_admin(HARNESS + steps, {"answers": feeds, "search": search})


def test_address_params_set_the_first_view_and_out_of_list_values_fall_back() -> None:
    """§3.2 — 주소 page·device·mode·metric 이 처음 값(목록 밖은 기본값: 화면 = pv 가장 큰 화면, PC, 요소, 클릭). 대시보드의 mode=depth 는
    요소로, 휴대폰의 metric=hover 는 클릭으로 — 주소도 고쳐 쓰고 그 단추는 꺼진다(title)."""
    got = _run(
        "ok",
        "out.a = snap()",
        search="?page=kimp-chart&device=mobile&mode=area&metric=vis&range=30d",
    )["a"]
    assert (got["device"], got["mode"], got["metric"], got["metricHidden"]) == (
        ["mobile"],
        ["area"],
        ["vis"],
        True,
    )
    assert [c[0] for c in got["chips"] if c[1] == "true"] == ["김프 차트74"]
    assert (
        dict((c[0], c[2]) for c in got["chips"])["대시보드 기록0"] == "scr-chip zero"
    )  # 휴대폰 기록 없음 — 흐리게
    assert got["calls"][4:] == [f"{ATT}?days=30", f"{ATT}?days=30&page=kimp-chart"]
    assert (got["src"], got["stage"]) == (
        f"{SITE}/kimp-chart?kt-overlay=1",
        "scr-stage mobile",
    )
    assert got["line"] == "김프 차트 · 휴대폰 · 최근 30일 — pv 74"
    assert (
        got["here"]
        == "/screens.html?range=30d&page=kimp-chart&device=mobile&mode=area&metric=vis"
    )
    got = _run(
        "ok",
        "out.a = snap()",
        search="?page=nope&device=tablet&mode=heat&metric=x&range=2d&cmp=prev",
    )["a"]
    assert (got["device"], got["mode"], got["metric"], got["metricHidden"]) == (
        ["pc"],
        ["element"],
        ["clicks"],
        False,
    )
    assert got["calls"][4:] == [f"{ATT}?days=7", f"{ATT}?days=7&page=landing"]
    assert got["here"] == "/screens.html" and got["src"] == f"{SITE}/?kt-overlay=1"
    got = _run("ok", "out.a = snap()", search="?page=app-spread&mode=depth")["a"]
    assert got["mode"] == ["element"] and got["url"] == "/screens.html?page=app-spread"
    assert ["depth", "탭 화면은 깊이 없음"] in got["off"] and got[
        "src"
    ] == f"{SITE}/app/?tab=spread&kt-overlay=1"
    got = _run("ok", "out.a = snap()", search="?device=mobile&metric=hover")["a"]
    assert got["metric"] == ["clicks"] and got["url"] == "/screens.html?device=mobile"
    assert ["hover", "휴대폰은 마우스 올림 없음"] in got["off"]


CHANGES = r"""
out.first = snap()
document.getElementById('pick-page').kids[4].fire('click')
await flush()
out.screen = snap()
v.click('range', '30d')
await flush()
out.range = snap()
const before = posted.length
ready('app-spread', ['filters', 'table'], [['filters', 'search']])
await flush()
out.ready = snap().posted.slice(before)
v.click('mode', 'area')
await flush()
v.click('metric', 'vis')
await flush()
v.click('device', 'mobile')
await flush()
out.after = snap()
"""


def test_screen_and_period_changes_call_one_feed_each_and_views_call_nothing() -> None:
    """§3.7 — 처음: page 없는 응답(화면 칩 pv) 하나 → 기본 화면(pv 가장 큰 화면)의 응답 하나. 화면을 바꾸면 그 화면 하나, 기간을 바꾸면
    page 없는 것과 고른 화면 둘(주소에 page 가 남는다 — 한 번 고르면 그대로). 기기·보기·지표는 부르지 않는다(기기는 틀을 다시 띄운다)."""
    got = _run("ok", CHANGES)
    first, screen, rng, after = got["first"], got["screen"], got["range"], got["after"]
    assert first["calls"] == [*FAST, f"{ATT}?days=7", f"{ATT}?days=7&page=landing"]
    assert [c[0] for c in first["chips"]][:2] == ["랜딩240", "처리방침16"]
    assert first["chips"][0][1] == "true" and first["chips"][7][2] == "scr-chip"
    assert screen["calls"][len(first["calls"]) :] == [f"{ATT}?days=7&page=app-spread"]
    assert (
        screen["url"] == "/screens.html?page=app-spread"
        and screen["src"] == f"{SITE}/app/?tab=spread&kt-overlay=1"
    )
    assert rng["calls"][len(screen["calls"]) :] == [
        f"{ATT}?days=30",
        f"{ATT}?days=30&page=app-spread",
    ]
    assert rng["url"] == "/screens.html?range=30d&page=app-spread"
    assert after["calls"] == rng["calls"]
    assert (
        after["url"]
        == "/screens.html?range=30d&page=app-spread&device=mobile&mode=area&metric=vis"
    )
    assert (
        after["src"] == f"{SITE}/app/?tab=spread&kt-overlay=1"
        and after["stage"] == "scr-stage mobile"
    )
    (message, origin), *_ = got["ready"]
    assert origin == SITE and (message["type"], message["v"], message["mode"]) == (
        "kt-attention",
        1,
        "element",
    )
    assert (
        message["label"] == "대시보드 스프레드 · PC · 최근 30일"
        and message["depth"] is None
    )
    # 보기·지표를 바꾸면 값을 다시 보낸다. 기기를 바꾸면 틀을 다시 띄워 ready 를 기다린다(그 전에는 보내지 않는다)
    modes = [m["mode"] for m, _ in after["posted"]]
    assert (
        modes[-2:] == ["area", "area"] and len(after["posted"]) == len(got["ready"]) + 2
    )


READY = r"""
out.before = snap()
fire({ type: 'kt-attention-ready', v: 1, page: 'landing', areas: ['top'], elements: [['top', 'nav:kimp']] }, { origin: 'https://evil.example' })
fire({ type: 'kt-attention-ready', v: 1, page: 'landing', areas: ['top'], elements: [['top', 'nav:kimp']] }, { source: {} })
ready('privacy', ['top'], [['top', 'nav:kimp']])
await flush()
out.ignored = snap()
timers.filter((t) => t.ms === 5000 && t.fn).forEach((t) => t.fn())
await flush()
out.silent = snap()
ready('landing', ['top', 'hero', 'faq', 'events'], [['top', 'nav:kimp'], ['hero', 'live:1'], ['hero', 'app:history'], ['faq', 'faq:rate']])
await flush()
out.ready = snap()
rows('tbl-elements').find((r) => r.cells[1] === '김프·역프란 링크').tr.fire('click')
await flush()
out.focus = snap()
v.click('panel', 'areas')
await flush()
out.areaRows = rows('tbl-areas').map((r) => [r.cls, ...r.cells])
rows('tbl-areas')[0].tr.fire('click')
await flush()
out.areaFocus = posted.at(-1)
ready('landing', ['top', 'hero'])
await flush()
v.click('panel', 'elements')
await flush()
out.noElements = snap()
"""


def test_ready_judges_absent_elements_and_rows_stay_present() -> None:
    """§3.6·§3.7 — ready 전에는 '지금 화면에 없음' 을 판정하지 않고 값도 보내지 않는다(5초 안에 없으면 '페이지가 응답하지 않음').
    출처·창·화면이 다른 메시지는 무시. ready 의 elements 에 없는 열쇠는 끝에 흐리게(행 row:* 는 늘 있음), 받으면 값을 보낸다(대상 출처는
    공개 사이트). 행 → focus(id·key)·그 행 강조, 영역 행 → focus(id). elements 가 없는 ready 는 판정하지 않는다."""
    got = _run("ok", READY)
    before, ignored, silent, ready = (
        got["before"],
        got["ignored"],
        got["silent"],
        got["ready"],
    )
    assert before["note"] == "페이지를 띄우는 중…" and before["posted"] == []
    assert before["foot"] == "열쇠 26개 · 지금 화면에 없음 0개"
    assert {r[0] for r in before["elements"]} == {"go"}
    assert [r[1] for r in before["elements"]][19:21] == [
        "#20",
        "–",
    ]  # 클릭 0 은 순위 없음
    assert ignored["posted"] == [] and ignored["foot"] == before["foot"]
    assert silent["note"].startswith("페이지가 응답하지 않음 — 5초 안에")
    assert ready["note"] is None
    ((message, origin),) = ready["posted"]
    assert origin == SITE and message["mode"] == "element"
    assert [
        (e["area"], e["key"], e["level"], e["rank"]) for e in message["elements"]
    ] == [
        ("top", "nav:kimp", 5, 1),
        ("faq", "faq:rate", 5, 2),
        ("hero", "app:history", 4, 3),
        ("hero", "live:1", 3, 4),
        ("events", "row:BTC", 1, 5),
    ]
    assert [e["tag"] for e in message["elements"]] == [
        "66",
        "53",
        "42",
        "28",
        "1",
    ]  # 클릭 수
    assert ready["foot"] == "열쇠 26개 · 지금 화면에 없음 20개"
    assert ready["elements"][:6] == [
        ["go", "#1", "김프·역프란 링크", "머리 막대"],
        ["go", "#2", "환율은 무엇을 쓰나", "자주 묻는 질문"],
        ["go", "#3", "김프 기록과 차트 링크", "첫 화면"],
        ["go", "#4", "지금 가장 큰 경로 카드", "첫 화면"],
        ["go", "#5", "사건 행 BTC", "지난 7일 사건"],
        ["go", "–", "사건 행 ETH", "지난 7일 사건"],  # 행은 늘 있다
    ]
    assert {r[0] for r in ready["elements"][6:]} == {"gone"}
    assert ready["elements"][6] == [
        "gone",
        "–",
        "링크 /kimp-history · 지금 화면에 없음",
        "히스토리 분석",
    ]
    focus = got["focus"]
    assert focus["posted"][-1] == [
        {"type": "kt-attention-focus", "id": "top", "key": "nav:kimp"},
        SITE,
    ]
    assert [r[0] for r in focus["elements"][:2]] == ["on go", "go"]
    assert got["areaRows"][0] == ["go", "#1", "머리 막대", "평균 5.4초", "87%", "25.8"]
    assert got["areaRows"][4] == [
        "gone",
        "–",
        "히스토리 분석 · 지금 화면에 없음",
        "평균 2.6초",
        "23%",
        "5.0",
    ]
    assert got["areaFocus"] == [{"type": "kt-attention-focus", "id": "top"}, SITE]
    assert got["noElements"]["foot"] == "열쇠 26개 · 지금 화면에 없음 0개"


STATES = r"""
ready(document.getElementById('frame').src.includes('/app/') ? 'app-pp' : 'landing', ['top'], [['top', 'nav:kimp']])
await flush()
out.a = snap()
"""


def test_gate_error_empty_and_small_samples() -> None:
    """§3.8 — before_gate·error: 066 부분 상태 배지('연결 안 됨'·'불러오지 못함'), 틀은 띄우되 값은 보내지 않는다. 기록 없는
    화면·기기는 틀을 띄우고 판에 '이 기간 기록 없음'. 표본 적음(pv < 5)은 머리 줄·판에."""
    gate = _run("empty", STATES)["a"]
    assert (
        gate["tags"] == ["연결 안 됨", "연결 안 됨"]
        and gate["src"] == f"{SITE}/?kt-overlay=1"
    )
    assert (
        gate["posted"] == []
        and gate["panelNote"] == "연결 안 됨"
        and gate["line"] == "랜딩 · PC · 최근 7일"
    )
    error = _run("problem", STATES)["a"]
    assert error["tags"] == ["불러오지 못함", "불러오지 못함"] and error["posted"] == []
    empty = _run("nobands", STATES)["a"]
    assert empty["tags"] == ["", ""] and empty["panelNote"] == "이 기간 기록 없음"
    assert (
        empty["line"] == "랜딩 · PC · 최근 7일 — pv 0"
        and not empty["small"]
        and empty["elements"] == []
    )
    # 값은 보낸다(영역은 053 꼴 — 기록 없음 단계 0)
    assert [m["areas"] for m, _ in empty["posted"]] == [
        [
            {
                "id": "top",
                "level": 0,
                "rank": None,
                "t": 0,
                "r": 0,
                "c": 0,
                "low": False,
                "small": True,
            }
        ]
    ]
    small = _run("ok", STATES, search="?page=app-pp")["a"]
    assert small["small"] and small["line"] == "대시보드 선선갭 · PC · 최근 7일 — pv 3"
    assert (
        small["panelNote"] == "표본 적음 — 페이지뷰 3(5 미만)이라 단계·순위가 흔들린다"
    )
    assert all(e["small"] for m, _ in small["posted"] for e in m["elements"])


PANELS = r"""
v.click('panel', 'exits')
await flush()
const lines = (id) => document.getElementById(id).kids.map((k) => k.kids.map((c) => c.textContent))
out.exits = lines('l-exits')
out.exitNote = v.text('exit-note')
out.depth = lines('l-depth')
out.depthNote = v.text('depth-note')
v.byId.get('panel-exits').hidden = false
"""


def test_exits_and_depth_panel() -> None:
    """§3.3·§3.6 — 떠난 곳 상위 목록(이름·%·n, 바탕 줄 'pagehide 로 끝난 pv Σn — 전체 pv 의 x%'), 깊이 계단 막대(0%~100% 지점 11줄,
    도달이 처음 50% 밑으로 가는 줄에 '절반이 여기 전에 떠남', 바탕 줄 'Σ depth pv 기준'). 대시보드는 '탭 화면은 깊이 없음'."""
    got = _run("ok", PANELS)
    assert got["exits"][0] == ["", "머리 막대", "39%", "60"]  # 60 / (60 + 40 + 30 + 24)
    assert got["exitNote"] == "pagehide 로 끝난 pv 154 — 전체 pv 의 64%"  # 154 / 240
    assert [row[0] for row in got["depth"]] == [f"{j * 10}%" for j in range(11)]
    assert [row[2] for row in got["depth"]][:4] == ["100%", "96%", "87%", "75%"]
    half = [row for row in got["depth"] if len(row) == 4]
    assert (
        len(half) == 1 and half[0][0] == "50%" and half[0][3] == "절반이 여기 전에 떠남"
    )
    assert got["depthNote"] == "212 pv 기준"
    dash = _run("ok", PANELS, search="?page=app-spread")
    assert dash["depth"] == [] and dash["depthNote"] == "탭 화면은 깊이 없음"


CSV = r"""
v.click('detail', 'elements')
await flush()
out.title = [v.text('sheet-title'), v.text('sheet-unit'), v.text('t-sheet')]
v.byId.get('sheet-csv').fire('click')
await flush()
const text = async (blob) => new TextDecoder('utf-8', { ignoreBOM: true }).decode(await blob.arrayBuffer())
out.elements = { name: v.s.downloads.at(-1)[0], lines: (await text(v.s.blobs.at(-1))).split('\r\n') }
document.getElementById('sheet-close').fire('click')
await flush()
v.click('detail', 'exits')
await flush()
v.byId.get('sheet-csv').fire('click')
await flush()
out.exits = { name: v.s.downloads.at(-1)[0], lines: (await text(v.s.blobs.at(-1))).split('\r\n'), note: v.text('sheet-note') }
"""


def test_detail_sheets_export_csv_with_the_raw_key_column() -> None:
    """§3.6 — 상세 판(⤢): 판의 표 전체 — CSV kimptrack-screens-<화면>-<기기>-<range>.csv(066 CSV 규칙 — BOM·CRLF·수 칸은 수), 요소 표는
    열쇠 원문 칸. 떠난 곳·깊이 판은 두 목록을 한 표로."""
    got = _run("ok", CSV, search="?device=mobile&range=30d")
    assert got["title"] == ["요소", "랜딩 · 휴대폰 · 최근 30일 · 클릭 큰 순", ""]
    assert got["elements"]["name"] == "kimptrack-screens-landing-mobile-30d.csv"
    head, first, *_ = got["elements"]["lines"]
    assert (
        head
        == "﻿순위,이름,열쇠,영역,단계,클릭,100뷰당 클릭,평균 마우스 올림(초),평균 보인 시간(초),상태"
    )
    # 수 칸은 수 그대로(순위 1·클릭·100뷰당), 휴대폰은 마우스 올림 '–', 지금 화면에 있으면 상태 빈칸
    assert first == "1,메일 링크,link:mailto,바닥,5,126,28.5,–,5.3,"
    assert got["exits"]["name"] == "kimptrack-screens-landing-mobile-30d.csv"
    assert got["exits"]["lines"][0] == "﻿구분,항목,영역 id,pv,몫(%),도달(%)"
    kinds = [line.split(",")[0] for line in got["exits"]["lines"][1:] if line]
    assert kinds == ["떠난 곳"] * 4 + ["깊이"] * 11
    assert got["exits"]["note"].startswith("떠난 곳: pagehide 로 끝난 pv ")


HIDE = r"""
out.shown = document.getElementById('frame').src
document.visibilityState = 'hidden'
;(v.docListeners.visibilitychange || []).forEach((fn) => fn({}))
await flush()
out.hidden = document.getElementById('frame').src
document.visibilityState = 'visible'
;(v.docListeners.visibilitychange || []).forEach((fn) => fn({}))
await flush()
out.again = document.getElementById('frame').src
"""


def test_hidden_document_blanks_the_frame_and_shows_it_again() -> None:
    """§3.7(추측 — 053 그대로) — 이 문서가 숨은 동안 틀은 about:blank(대시보드 틀은 실시간 연결을 연다), 다시 보이면 그 화면."""
    got = _run("ok", HIDE, search="?page=app-gap")
    assert (got["shown"], got["hidden"], got["again"]) == (
        f"{SITE}/app/?tab=gap&kt-overlay=1",
        "about:blank",
        f"{SITE}/app/?tab=gap&kt-overlay=1",
    )
