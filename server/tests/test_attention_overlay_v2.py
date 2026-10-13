"""attention.js 덮어 보기의 065 키 — mode·elements·depth·focus key, ready 의 elements (스펙 065 §3.5·§4).

053 의 덮어 보기 시험(test_attention_overlay.py — 새 키 없는 메시지)은 그대로 두고, 여기서는 부모·자식 나무와 선택자를 흉내 낸
가짜 문서(test_attention_js.py 와 같은 꼴)에 요소를 놓는다. 요소·영역은 문서 위 (left, top, width, height) 이고 scrollY 로 옮긴다.
그린 층(#kt-overlay)의 상자(영역 테두리·요소 상자·깊이 띠)와 꼬리표 글자·글씨, 부모 창으로 보낸 ready, scrollIntoView 를 본다.
"""

import json
import os
import shutil
import subprocess
from typing import Any

import pytest

from tests.test_deploy import ROOT

JS = (ROOT / "web/public/attention.js").read_text("utf-8")
ADMIN = "https://admin.kimptrack.com"

HARNESS = r"""
const [script, cases] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
process.stdout.write(JSON.stringify(cases.map((c) => {
  let clock = 1800000000000
  class FakeDate { static now() { return clock } }
  const listeners = { window: [], document: [] }
  const adder = (who) => (type, fn) => listeners[who].push({ type, fn })
  const fire = (who, type, ev) => listeners[who].filter((l) => l.type === type).forEach((l) => l.fn(ev))
  let scrollY = 0
  // 선택자 — attention.js 가 쓰는 꼴만: 태그, [속성], [속성=값], [속성^="값"], :not(…), 쉼표 목록
  const compound = (el, s) => {
    let i = 0
    const m = /^[a-z]+/.exec(s)
    if (m) { if (el.tag !== m[0]) return false; i = m[0].length }
    while (i < s.length) {
      if (s.startsWith(':not(', i)) {
        const end = s.lastIndexOf(')')
        if (compound(el, s.slice(i + 5, end))) return false
        i = end + 1
      } else if (s[i] === '[') {
        const end = s.indexOf(']', i)
        const mm = /^([a-z-]+)(?:(\^?=)"?([^"]*)"?)?$/.exec(s.slice(i + 1, end))
        if (!mm) throw new Error('selector ' + s)
        const v = el.getAttribute(mm[1])
        if (v === null || (mm[2] === '=' && v !== mm[3]) || (mm[2] === '^=' && !v.startsWith(mm[3]))) return false
        i = end + 1
      } else throw new Error('selector ' + s)
    }
    return true
  }
  const selects = (el, sel) => sel.split(',').some((s) => compound(el, s.trim()))
  const scrolled = []
  // 페이지 요소 — 문서 위 (left, top, width, height), 부모·자식, 속성·글자, 라벨의 control
  class El {
    constructor(ref, tag, attrs, rect, text) {
      Object.assign(this, { ref, tag, attrs: attrs || {}, rect, text: text || '', kids: [], parent: null, attached: true })
    }
    get tagName() { return this.tag.toUpperCase() }
    get parentElement() { return this.parent }
    get isConnected() { return this.attached && (this.parent ? this.parent.isConnected : this === root) }
    get textContent() { return this.text + this.kids.map((k) => k.textContent).join('') }
    getAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n) ? String(this.attrs[n]) : null }
    hasAttribute(n) { return this.getAttribute(n) !== null }
    getBoundingClientRect() {
      const [l, t, w, h] = this.rect
      return { left: l, top: t - scrollY, width: w, height: h, right: l + w, bottom: t - scrollY + h }
    }
    append(k) { k.parent = this; this.kids.push(k); return k }
    closest(sel) { for (let n = this; n; n = n.parent) if (selects(n, sel)) return n; return null }
    querySelectorAll(sel) {
      const out = []
      const walk = (n) => { for (const k of n.kids) { if (selects(k, sel)) out.push(k); walk(k) } }
      walk(this)
      return out
    }
    scrollIntoView(o) { scrolled.push([this.ref, o]) }
  }
  // 덮어 보기가 만드는 노드(층·상자·꼬리표)
  class Node {
    constructor(tag) { Object.assign(this, { tag, kids: [], attrs: {}, style: {}, className: '', id: '', textContent: '' }) }
    setAttribute(k, v) { this.attrs[k] = String(v) }
    appendChild(k) { this.kids.push(k); return k }
    replaceChildren(...k) { this.kids = k }
  }
  const root = new El('html', 'html', {}, [0, 0, 0, 0])
  root.scrollHeight = c.docH || 4000
  const body = root.append(new El('body', 'body', {}, [0, 0, 1280, root.scrollHeight]))
  const refs = new Map()
  for (const [id, l, t, w, h] of c.areas || []) refs.set(id, body.append(new El(id, 'section', { 'data-area': id }, [l, t, w, h])))
  // 요소 — { ref, in: 영역 id 나 다른 요소의 ref, tag, attrs, rect, text, control: ref, later: true(add 단계 전엔 떼어 둠) }
  const make = (e) => {
    const parent = refs.get(e.in) || body
    const el = new El(e.ref, e.tag || 'button', e.attrs, e.rect || [0, 0, 100, 30], e.text)
    refs.set(e.ref, el)
    if (e.later) { el.attached = false; el.parent = parent } else parent.append(el)
    return el
  }
  for (const e of c.els || []) make(e)
  for (const e of c.els || []) if (e.control) refs.get(e.ref).control = refs.get(e.control)
  const mos = []
  class MO { constructor(cb) { this.cb = cb; mos.push(this) } observe() {} disconnect() {} }
  let rafs = []
  let timers = []
  const posted = []
  const layerHost = new Node('body')
  const parent = { postMessage: (data, origin) => posted.push({ data: JSON.parse(JSON.stringify(data)), origin }) }
  const window = {
    innerWidth: c.w || 1280, innerHeight: c.h || 800, parent,
    localStorage: { getItem: () => null },
    addEventListener: adder('window'),
    IntersectionObserver: class { observe() {} },
    MutationObserver: MO,
    requestAnimationFrame: (fn) => { rafs.push(fn); return rafs.length },
    setTimeout: (fn, ms) => { timers.push({ at: clock + ms, fn }); return timers.length },
    fetch: () => Promise.resolve(),
  }
  Object.defineProperty(window, 'scrollY', { get: () => scrollY })
  window.top = {}
  const document = {
    readyState: 'interactive', visibilityState: 'visible', body: layerHost, documentElement: root,
    createElement: (tag) => new Node(tag),
    querySelectorAll: (sel) => root.querySelectorAll(sel),
    addEventListener: adder('document'),
  }
  const navigator = { sendBeacon: () => true }
  const path = c.path || '/'
  const search = c.search || '?kt-overlay=1'
  const location = { hostname: 'kimptrack.com', pathname: path, search, origin: 'https://kimptrack.com', href: 'https://kimptrack.com' + path + search }
  new Function('window', 'document', 'navigator', 'location', 'Date', script)(window, document, navigator, location, FakeDate)
  const flush = () => { const fs = rafs; rafs = []; fs.forEach((f) => f(clock)) }
  for (const step of c.steps || []) {
    const at = step.indexOf('|')
    const kind = at < 0 ? step : step.slice(0, at)
    const arg = at < 0 ? '' : step.slice(at + 1)
    if (kind === 'msg') fire('window', 'message', { origin: c.origin || 'https://admin.kimptrack.com', source: parent, data: JSON.parse(arg) })
    else if (kind === 'flush') flush()
    else if (kind === 't') clock += Number(arg)
    else if (kind === 'timers') { const due = timers.filter((x) => x.at <= clock); timers = timers.filter((x) => x.at > clock); due.forEach((x) => x.fn()) }
    else if (kind === 'scroll') { scrollY = Number(arg); fire('document', 'scroll', {}) }
    else if (kind === 'add') { const el = refs.get(arg); el.attached = true; el.parent.append(el); mos.forEach((m) => m.cb([])) }
    else if (kind === 'detach') { const el = refs.get(arg); el.parent.kids = el.parent.kids.filter((k) => k !== el); el.attached = false; mos.forEach((m) => m.cb([])) }
    else throw new Error('step ' + step)
  }
  const layer = layerHost.kids.find((k) => k.id === 'kt-overlay') || null
  const tagOf = (k) => k.kids[0] && { text: k.kids[0].textContent, font: k.kids[0].style.font, bottom: k.kids[0].style.bottom || null, top: k.kids[0].style.top || null }
  return {
    posted, scrolled,
    boxes: layer ? layer.kids.map((k) => ({
      cls: k.className, area: k.attrs['data-kt-area'] || null, el: k.attrs['data-kt-el'] || null, depth: k.attrs['data-kt-depth'] ?? null,
      left: k.style.left, top: k.style.top, width: k.style.width, height: k.style.height, border: k.style.border || null,
      borderWidth: k.style.borderWidth || null, fill: k.style.backgroundColor, borderTop: k.style.borderTop || null,
      tag: tagOf(k) || null, tags: k.kids.map((t) => t.textContent),
    })) : null,
  }
})))
"""


def _run(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 attention.js 를 돌리지 못한다")
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps([JS, cases]),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _one(**case: Any) -> dict:
    return _run([case])[0]


def _msg(**data: Any) -> str:
    return "msg|" + json.dumps(data)


def _values(mode: str | None = None, **more: Any) -> str:
    """065 값 메시지 — 053 의 키(label·areas) + mode·elements·depth."""
    body: dict[str, Any] = {
        "type": "kt-attention",
        "v": 1,
        "label": "랜딩 · PC · 최근 7일",
    }
    body["areas"] = [
        {"id": "top", "level": 2, "rank": 2, "t": 1.5, "r": 90, "c": 3.0},
        {"id": "hero", "level": 5, "rank": 1, "t": 9.0, "r": 100, "c": 12.5},
    ]
    if mode is not None:
        body["mode"] = mode
    return _msg(**body, **more)


def _el(area: str, key: str, level: int, rank: int | None, **more: Any) -> dict:
    return {
        "area": area,
        "key": key,
        "level": level,
        "rank": rank,
        "tag": f"이름 {key}",
        "small": False,
        **more,
    }


AREAS = [
    ["top", 0, 0, 1280, 100],
    ["hero", 0, 100, 1280, 600],
    ["foot", 0, 3600, 1280, 400],
]
ELS = [
    {
        "ref": "nav",
        "in": "top",
        "tag": "a",
        "attrs": {"href": "#faq", "data-el": "nav:faq"},
        "rect": [10, 10, 80, 30],
    },
    # 같은 자동 열쇠 link:/app/ 둘(쿼리는 열쇠에 들지 않는다 — 061 과 같은 함수)
    {
        "ref": "app1",
        "in": "hero",
        "tag": "a",
        "attrs": {"href": "/app/?tab=history"},
        "rect": [10, 120, 200, 40],
    },
    {
        "ref": "app2",
        "in": "hero",
        "tag": "a",
        "attrs": {"href": "/app/"},
        "rect": [10, 200, 200, 40],
    },
    # data-el 카드 안의 링크는 카드 열쇠로 센다 — 따로 그리지 않는다
    {
        "ref": "card",
        "in": "hero",
        "tag": "div",
        "attrs": {"data-el": "live:1"},
        "rect": [300, 120, 300, 120],
    },
    {
        "ref": "cardlink",
        "in": "card",
        "tag": "a",
        "attrs": {"href": "/x"},
        "rect": [310, 130, 100, 20],
    },
    {"ref": "price", "in": "hero", "text": "BTC 1.25%", "rect": [700, 120, 100, 30]},
    {
        "ref": "row",
        "in": "hero",
        "tag": "tr",
        "attrs": {"data-el": "row:BTC"},
        "rect": [0, 400, 1280, 40],
    },
    {
        "ref": "far",
        "in": "foot",
        "tag": "a",
        "attrs": {"href": "/app/"},
        "rect": [10, 3700, 100, 30],
    },
]


def test_element_values_are_checked_and_bad_entries_dropped() -> None:
    """§3.5 — 요소 값은 영역 id 꼴·열쇠 꼴(1~64자·| ·제어 문자 없음)·단계 0~5 정수·순위 1 이상 정수 또는 null·꼬리표 글·참거짓.
    어긋난 항목은 버리고(그 요소는 그리지 않는다), 꼬리표 글은 80자에서 자른다."""
    long = "가" * 100
    bad = [
        _el("Hero", "nav:faq", 3, 1),  # 영역 id 꼴
        _el("top", "a" * 65, 3, 1),
        _el("top", "nav|faq", 3, 1),
        _el("top", "nav\nfaq", 3, 1),
        {**_el("top", "nav:faq", 3, 1), "key": 7},
        _el("top", "nav:faq", 6, 1),
        _el("top", "nav:faq", 1.5, 1),
        _el("top", "nav:faq", 3, 0),
        _el("top", "nav:faq", 3, "1"),
        {**_el("top", "nav:faq", 3, 1), "tag": None},
        {**_el("top", "nav:faq", 3, 1), "small": 1},
    ]
    got = _one(areas=AREAS, els=ELS, steps=[_values("element", elements=bad), "flush"])
    assert [b["el"] for b in got["boxes"] if b["el"]] == []
    good = {**_el("hero", "live:1", 4, 1), "tag": long, "small": True}
    got = _one(
        areas=AREAS, els=ELS, steps=[_values("element", elements=[*bad, good]), "flush"]
    )
    (card,) = [b for b in got["boxes"] if b["el"]]
    assert card["el"] == "hero|live:1"
    assert card["tag"]["text"] == "#1 · " + "가" * 80 + " · 표본 적음"


def test_modes_area_element_and_depth_and_no_mode_is_053() -> None:
    """§3.5 — area 는 053 그대로(영역 상자·꼬리표), element 는 영역을 옅은 회색 테두리만(꼬리표 없음)·요소 상자를 위에,
    depth 는 띠만. mode 가 없거나 목록 밖이면 area(053)."""
    elements = [_el("hero", "live:1", 5, 1)]
    for mode in (None, "area", "heat"):
        got = _one(
            areas=AREAS, els=ELS, steps=[_values(mode, elements=elements), "flush"]
        )
        assert [b["area"] for b in got["boxes"]] == ["top", "hero", "foot"], mode
        assert got["boxes"][1]["tag"]["text"].startswith("#1 · 평균 9.0초"), mode
    got = _one(
        areas=AREAS, els=ELS, steps=[_values("element", elements=elements), "flush"]
    )
    frames = [b for b in got["boxes"] if b["area"]]
    assert [b["area"] for b in frames] == ["top", "hero", "foot"]
    assert all(b["tags"] == [] and b["fill"] == "transparent" for b in frames)
    assert all(b["border"].startswith("1px solid") for b in frames)
    assert [b["el"] for b in got["boxes"] if b["el"]] == ["hero|live:1"]
    reach = [100, 90, 80, 70, 60, 45, 30, 20, 10, 5, 2]
    got = _one(
        areas=AREAS,
        els=ELS,
        steps=[_values("depth", elements=elements, depth=reach), "flush"],
    )
    assert [b["depth"] for b in got["boxes"]] == [str(j) for j in range(10)]


def test_element_boxes_cover_every_matching_element_with_the_collection_key() -> None:
    """§3.5 — (영역, 열쇠)를 가진 요소마다(같은 열쇠 여럿은 모두) — 열쇠는 061 수집과 같은 함수(data-el·자동 열쇠·행).
    data-el 조상 안의 요소는 따로 그리지 않고, 행은 지금 DOM 에 있는 것만, 화면 밖 요소는 건너뛴다, 값 없는 요소는 그리지 않는다."""
    elements = [
        _el("hero", "link:/app/", 3, 2),
        _el("hero", "live:1", 5, 1),
        _el("hero", "btn:BTC #%", 2, 3),  # 화면 글자의 숫자는 # — 수집과 같은 이름
        _el("hero", "row:BTC", 1, 4),
        _el("hero", "row:ETH", 1, 5),  # DOM 에 없는 행
        _el("foot", "link:/app/", 4, 6),  # 화면 밖(문서 3,700px)
        _el("top", "link:/app/", 4, 7),  # 그 영역에는 없다
    ]
    got = _one(
        areas=AREAS, els=ELS, steps=[_values("element", elements=elements), "flush"]
    )
    drawn = [(b["el"], b["left"], b["top"]) for b in got["boxes"] if b["el"]]
    assert drawn == [
        ("hero|link:/app/", "10px", "120px"),
        ("hero|link:/app/", "10px", "200px"),
        ("hero|live:1", "300px", "120px"),
        ("hero|btn:BTC #%", "700px", "120px"),
        ("hero|row:BTC", "0px", "400px"),
    ]
    # 스크롤하면 화면 밖이던 바닥 링크가 들어오고 위의 것은 나간다
    got = _one(
        areas=AREAS,
        els=ELS,
        steps=[_values("element", elements=elements), "scroll|3300", "flush"],
    )
    assert [(b["el"], b["top"]) for b in got["boxes"] if b["el"]] == [
        ("foot|link:/app/", "400px")
    ]


def test_only_the_top_20_get_tags_and_fonts_follow_the_level() -> None:
    """§3.5 — 꼬리표 '#순위 · 글' 은 순위 20 안만(나머지는 테두리만), 글씨는 단계 0~5 에 11·12·13·15·17·17px, 단계 0 은 회색 점선만.
    꼬리표는 상자 위(화면 위가 모자라면 상자 안)."""
    els = [
        {
            "ref": f"b{i}",
            "in": "hero",
            "attrs": {"data-el": f"b{i}"},
            "rect": [10 + 40 * i, 300, 30, 30],
        }
        for i in range(22)
    ]
    elements = [_el("hero", f"b{i}", (i % 6), i + 1) for i in range(22)]
    got = _one(
        areas=AREAS, els=els, steps=[_values("element", elements=elements), "flush"]
    )
    boxes = [b for b in got["boxes"] if b["el"]]
    assert len(boxes) == 22
    assert [b["tag"] is not None for b in boxes] == [True] * 20 + [False] * 2
    sizes = [11, 12, 13, 15, 17, 17]
    for i, b in enumerate(boxes[:20]):
        assert f" {sizes[i % 6]}px/" in b["tag"]["font"], (i, b["tag"])
        assert (
            b["tag"]["text"] == f"#{i + 1} · 이름 b{i}" and b["tag"]["bottom"] == "100%"
        )
    assert (
        boxes[0]["border"].startswith("2px dashed")
        and boxes[0]["fill"] == "transparent"
    )
    assert boxes[5]["border"].startswith("4px solid") and boxes[5]["fill"].endswith(
        ",.5)"
    )
    # 화면 맨 위에 붙은 요소의 꼬리표는 상자 안
    got = _one(
        areas=AREAS,
        els=ELS,
        steps=[_values("element", elements=[_el("top", "nav:faq", 3, 1)]), "flush"],
    )
    (nav,) = [b for b in got["boxes"] if b["el"]]
    assert nav["tag"]["top"] == "0" and nav["tag"]["bottom"] is None


def test_depth_draws_ten_bands_with_tags_and_dashboards_ignore_it() -> None:
    """§3.5 — 정적 페이지: 문서 높이를 10칸으로 나눈 띠, 칸 j 의 어둡기 = (100 − 도달[j+1]) / 100 × 0.55, 칸 위 경계 왼쪽에
    'j×10% · 도달 n%', 문서 끝에 '끝까지 n%'. 스크롤하면 다시 놓는다. 대시보드 화면은 깊이를 그리지 않는다."""
    reach = [100, 90, 80, 70, 60, 45, 30, 20, 10, 5, 2]
    got = _one(areas=AREAS, docH=4000, steps=[_values("depth", depth=reach), "flush"])
    bands = got["boxes"]
    assert [b["top"] for b in bands] == [f"{400 * j}px" for j in range(10)]
    assert all(
        b["height"] == "400px" and b["borderTop"].startswith("1px dashed")
        for b in bands
    )
    assert [b["fill"] for b in bands] == [
        f"rgba(17,24,39,{(100 - reach[j + 1]) / 100 * 0.55:.3f})" for j in range(10)
    ]
    assert [b["tags"] for b in bands[:2]] == [["0% · 도달 100%"], ["10% · 도달 90%"]]
    assert bands[9]["tags"] == ["90% · 도달 5%", "끝까지 2%"]
    got = _one(
        areas=AREAS,
        docH=4000,
        steps=[_values("depth", depth=reach), "scroll|1000", "flush"],
    )
    assert [b["top"] for b in got["boxes"]][:3] == ["-1000px", "-600px", "-200px"]
    # 대시보드·깊이 꼴이 틀림 — 띠 없음
    for case in (
        {"path": "/app/", "search": "?tab=spread&kt-overlay=1", "depth": reach},
        {"depth": reach[:10]},
        {"depth": [*reach[:10], 101]},
    ):
        got = _one(
            areas=AREAS,
            path=case.get("path", "/"),
            search=case.get("search", "?kt-overlay=1"),
            steps=[_values("depth", depth=case["depth"]), "flush"],
        )
        assert got["boxes"] == [], case


def test_ready_lists_sized_elements_without_rows_up_to_300_and_again_when_they_change() -> (
    None
):
    """§3.5 — ready 에 elements: 지금 크기 있는 요소의 [영역, 열쇠](행 빼고, 같은 짝은 한 번, 300개까지). 모임이 바뀔 때만 다시
    (053 의 1초 규칙). 크기 0 요소는 빠진다."""
    els = [
        *ELS,
        {
            "ref": "hidden",
            "in": "hero",
            "attrs": {"data-el": "hidden"},
            "rect": [0, 0, 0, 0],
        },
    ]
    els.append(
        {
            "ref": "late",
            "in": "top",
            "attrs": {"data-el": "late"},
            "rect": [200, 10, 50, 30],
            "later": True,
        }
    )
    got = _one(
        areas=AREAS, els=els, steps=["t|1000", "timers", "add|late", "t|1000", "timers"]
    )
    first, second = (p["data"] for p in got["posted"])
    assert [p["origin"] for p in got["posted"]] == [ADMIN, ADMIN]
    assert first == {
        "type": "kt-attention-ready",
        "v": 1,
        "page": "landing",
        "areas": ["top", "hero", "foot"],
        "elements": [
            ["top", "nav:faq"],
            ["hero", "link:/app/"],
            ["hero", "live:1"],
            ["hero", "btn:BTC #%"],
            ["foot", "link:/app/"],
        ],
    }
    assert second["elements"][1] == ["top", "late"]
    many = [
        {
            "ref": f"m{i}",
            "in": "hero",
            "attrs": {"data-el": f"m{i:03d}"},
            "rect": [0, 100 + i, 10, 1],
        }
        for i in range(310)
    ]
    got = _one(areas=AREAS, els=many, steps=[])
    (ready,) = (p["data"] for p in got["posted"])
    assert len(ready["elements"]) == 300 and ready["elements"][-1] == ["hero", "m299"]


def test_focus_with_a_key_scrolls_to_the_first_element_and_thickens_it() -> None:
    """§3.5 — focus 에 key 가 있으면 그 영역 안 그 열쇠의 첫 요소를 scrollIntoView({block:'center'}), 그 상자를 2초 굵게(값이 없어도).
    없는 열쇠·꼴이 틀린 열쇠는 아무것도. key 가 없으면 053 그대로(영역)."""
    focus = {"type": "kt-attention-focus", "id": "hero", "key": "link:/app/"}
    got = _one(
        areas=AREAS,
        els=ELS,
        steps=[_values("element", elements=[]), _msg(**focus), "flush"],
    )
    assert got["scrolled"] == [["app1", {"block": "center"}]]
    (box,) = [b for b in got["boxes"] if b["el"]]
    assert (box["el"], box["top"], box["borderWidth"]) == (
        "hero|link:/app/",
        "120px",
        "6px",
    )
    later = _one(
        areas=AREAS,
        els=ELS,
        steps=[
            _values("element", elements=[]),
            _msg(**focus),
            "t|2000",
            "timers",
            "flush",
        ],
    )
    assert [b for b in later["boxes"] if b["el"]] == []
    for key in ("link:/nope", "a|b", 3):
        got = _one(
            areas=AREAS,
            els=ELS,
            steps=[_values("element"), _msg(**{**focus, "key": key}), "flush"],
        )
        assert got["scrolled"] == [], key
    got = _one(
        areas=AREAS,
        els=ELS,
        steps=[_values("element"), _msg(type="kt-attention-focus", id="hero"), "flush"],
    )
    assert got["scrolled"] == [["hero", {"block": "center"}]]
    hero = next(b for b in got["boxes"] if b["area"] == "hero")
    assert hero["borderWidth"] == "6px"


def test_another_origin_cannot_send_the_new_keys() -> None:
    """053 그대로 — 출처가 관리자가 아니면 새 키도 무시한다."""
    got = _one(
        areas=AREAS,
        els=ELS,
        origin="https://kimptrack.com",
        steps=[_values("element", elements=[_el("hero", "live:1", 5, 1)]), "flush"],
    )
    assert got["boxes"] is None and got["scrolled"] == []
