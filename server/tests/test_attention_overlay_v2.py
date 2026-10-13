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
