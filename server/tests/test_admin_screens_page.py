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
