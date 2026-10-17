"""replay.js 를 node 로 돌린다 — 가짜 window·document·시계·타이머 (스펙 073 §3.1~§3.3·§4, 074 §3.4).

단계 문자열로 시계·포인터·누름·스크롤·숨김·탭 바뀜·동의를 움직이고 보낸 묶음(sendBeacon·fetch)을 본다. 재생 쪽은 틀 안(top ≠ window)에
kt-replay=1 로 띄워 관리자 출처의 메시지를 넣고 그린 것(커서·누름 원·스크롤)을 본다.
"""

import json
import re
import shutil
import subprocess
from typing import Any

import pytest

from tests.test_deploy import ROOT
from tests.test_privacy import NOTICE, OLD

JS = (ROOT / "web/public/replay.js").read_text("utf-8")
ON = {"kt.analytics": "granted", "kt.analytics.v": NOTICE}
ADMIN = "https://admin.kimptrack.com"

HARNESS = r"""
const [script, cases] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
process.stdout.write(JSON.stringify(cases.map((c) => {
  let clock = 1800000000000
  const timers = []
  let tid = 0
  const addTimer = (fn, ms, every) => { const id = ++tid; timers.push({ id, at: clock + ms, fn, every }); return id }
  const clearTimer = (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1) }
  const advance = (ms) => {
    const end = clock + ms
    for (;;) {
      timers.sort((a, b) => a.at - b.at || a.id - b.id)
      const t = timers[0]
      if (!t || t.at > end) break
      clock = t.at
      if (t.every) t.at = clock + t.every
      else timers.shift()
      t.fn()
    }
    clock = end
  }
  class FakeDate { static now() { return clock } }
  const data = new Map(Object.entries(c.ls || {}))
  const writes = []
  const localStorage = {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => writes.push([k, v]),
  }
  const bags = { window: {}, document: {} }
  const adder = (bag) => (type, fn) => { (bag[type] = bag[type] || []).push(fn) }
  const fire = (bag, type, ev) => (bag[type] || []).slice().forEach((fn) => fn(ev || {}))
  let scrollY = c.scrollY || 0
  // 가짜 요소 — 문서 위 top·height, 부모·자식, 속성, 스크롤 높이
  const style = {}
  class El {
    constructor(tag, attrs = {}, box = {}) {
      Object.assign(this, { tag, attrs: { ...attrs }, kids: [], parent: null, top: box.top || 0, height: box.height || 0, scrollHeight: box.sh || 0, clientHeight: box.ch || 0, overflowY: box.oy || 'visible', scrollTop: 0, isConnected: true, style: {} })
    }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null }
    setAttribute(k, v) { this.attrs[k] = String(v) }
    closest(sel) { const m = /^\[([a-z-]+)\]$/.exec(sel); let n = this; while (n) { if (m && n.getAttribute(m[1]) !== null) return n; n = n.parent } return null }
    querySelectorAll(sel) { const out = []; const walk = (n) => n.kids.forEach((k) => { if (sel === '*' || match(k, sel)) out.push(k); walk(k) }); walk(this); return out }
    getBoundingClientRect() { const t = this.top - scrollY; return { top: t, bottom: t + this.height, left: 0, width: 300, height: this.height, right: 300 } }
    append(...k) { for (const x of k) { x.parent = this; this.kids.push(x) } }
    replaceChildren(...k) { this.kids = []; this.append(...k) }
    remove() { if (this.parent) this.parent.kids = this.parent.kids.filter((x) => x !== this) }
  }
  const match = (el, sel) => {
    const m = /^\[([a-z-]+)(?:="([^"]*)")?\]$/.exec(sel)
    if (!m) return false
    const v = el.getAttribute(m[1])
    return v !== null && (m[2] === undefined || v === m[2])
  }
  const root = new El('html')
  const body = new El('body')
  root.append(body)
  const byName = {}
  for (const a of c.areas || []) {
    const area = new El('section', { 'data-area': a.id }, { top: a.top, height: a.height })
    body.append(area)
    byName[a.id] = area
    for (const s of a.scrollers || []) {
      const el = new El('div', {}, { sh: s.sh, ch: s.ch, oy: s.oy })
      area.append(el)
      byName[s.name] = el
    }
  }
  const all = () => root.querySelectorAll('*')
  const beacons = []
  const posted = []
  const scrolls = []
  const document = {
    visibilityState: 'visible',
    readyState: c.readyState || 'interactive',
    documentElement: root,
    body,
    addEventListener: adder(bags.document),
    querySelector: (sel) => all().find((e) => match(e, sel)) || null,
    querySelectorAll: (sel) => all().filter((e) => match(e, sel)),
    createElement: (tag) => new El(tag),
    createElementNS: (ns, tag) => new El(tag),
  }
  const self = {}
  const parent = { postMessage: (m, o) => posted.push([m, o]) }
  const window = Object.assign(self, {
    innerWidth: c.w || 1280,
    innerHeight: c.h || 800,
    localStorage,
    addEventListener: adder(bags.window),
    setTimeout: (fn, ms) => addTimer(fn, ms || 0, 0),
    clearTimeout: clearTimer,
    setInterval: (fn, ms) => addTimer(fn, ms, ms),
    clearInterval: clearTimer,
    requestAnimationFrame: (fn) => addTimer(fn, 16, 0),
    scrollTo: (x, y) => { scrolls.push(y); scrollY = y },
    getComputedStyle: (el) => ({ overflowY: el.overflowY }),
    crypto: { getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = (i * 37 + 11) & 255; return a } },
    fetch: (url, init) => { beacons.push(['fetch', url, init.body]); return Promise.resolve() },
    MutationObserver: class { constructor(fn) { this.fn = fn } observe() {} disconnect() {} },
  })
  Object.defineProperty(window, 'scrollY', { get: () => scrollY })
  window.top = c.framed ? {} : self
  window.parent = c.framed ? parent : self
  const navigator = { globalPrivacyControl: c.gpc === true, sendBeacon: c.noBeacon ? undefined : (url, b) => { beacons.push(['beacon', url, b]); return true } }
  const location = { hostname: c.host || 'kimptrack.com', pathname: c.path || '/', search: c.search || '' }
  new Function('window', 'document', 'navigator', 'location', 'Date', 'URLSearchParams', script)(window, document, navigator, location, FakeDate, URLSearchParams)
  const pointer = (x, y) => fire(bags.document, 'pointermove', { clientX: x, clientY: y })
  const events = []
  let sent = 0
  const take = () => { const out = beacons.slice(sent).map(([how, url, b]) => ({ how, url, body: JSON.parse(b) })); sent = beacons.length; return out }
  for (const step of c.steps || []) {
    const [op, ...a] = step
    if (op === 'wait') advance(a[0])
    else if (op === 'move') pointer(a[0], a[1])
    else if (op === 'click') fire(bags.document, 'click', { clientX: a[0], clientY: a[1], detail: 1, target: byName[a[2]] || body })
    else if (op === 'scroll') { scrollY = a[0]; fire(bags.document, 'scroll', { target: document }) }
    else if (op === 'inner') { byName[a[0]].scrollTop = a[1]; fire(bags.document, 'scroll', { target: byName[a[0]] }) }
    else if (op === 'hide') { document.visibilityState = 'hidden'; fire(bags.document, 'visibilitychange') }
    else if (op === 'show') { document.visibilityState = 'visible'; fire(bags.document, 'visibilitychange') }
    else if (op === 'tab') fire(bags.window, 'kt:tab', { detail: a[0] })
    else if (op === 'resize') { window.innerWidth = a[0]; window.innerHeight = a[1]; fire(bags.window, 'resize') }
    else if (op === 'pagehide') fire(bags.window, 'pagehide')
    else if (op === 'consent') { for (const [k, v] of Object.entries(a[0])) v === null ? data.delete(k) : data.set(k, v); fire(bags.window, a[1] || 'kt:clarity') }
    else if (op === 'take') events.push(take())
    else if (op === 'msg') fire(bags.window, 'message', { origin: a[0], source: a[1] === 'parent' ? parent : {}, data: a[2] })
    else if (op === 'press') { let prevented = false; fire(bags.document, a[0], { preventDefault() { prevented = true }, stopPropagation() {} }); events.push({ press: a[0], prevented }) }
    else throw new Error('op ' + op)
  }
  const layer = body.kids.find((k) => k.style.zIndex === '2147483000')
  const shown = layer ? layer.kids.map((k) => ({ display: k.style.display, left: k.style.left, top: k.style.top, kids: k.kids.length })) : null
  return { events, rest: take(), writes, posted, scrolls, layer: shown, inner: Object.fromEntries(Object.entries(byName).filter(([k]) => k.startsWith('s')).map(([k, v]) => [k, v.scrollTop])) }
})))
"""


def run(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 가 없다")
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps([JS, cases]),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(done.stdout)


def one(**case: Any) -> dict[str, Any]:
    return run([case])[0]


def bodies(got: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        b["body"] for group in got["events"] if isinstance(group, list) for b in group
    ] + [b["body"] for b in got["rest"]]


MOVES = [["move", 10, 10], ["wait", 2000]]


@pytest.mark.parametrize(
    "case",
    [
        {},  # 동의 없음
        {
            "ls": {"kt.analytics": "granted", "kt.analytics.v": "2026-10-09-2"}
        },  # 예전 판
        {
            "ls": {"kt.analytics": "granted", "kt.analytics.v": OLD}
        },  # 바로 앞 판(078 — v7 의 2026-10-16)
        {"ls": ON, "gpc": True},
        {"ls": ON, "host": "www.kimptrack.com"},
        {"ls": ON, "framed": True},
        {"ls": ON, "path": "/nope"},
    ],
)
def test_nothing_is_sent_without_consent_or_outside_the_site(
    case: dict[str, Any],
) -> None:
    got = one(**case, steps=MOVES + [["click", 5, 5], ["pagehide"], ["take"]])
    assert bodies(got) == []


def test_a_batch_every_two_seconds_with_the_header() -> None:
    got = one(
        ls=ON, steps=[["move", 10, 10], ["wait", 1999], ["take"], ["wait", 1], ["take"]]
    )
    first, second = got["events"]
    assert first == []
    (b,) = second
    assert b["how"] == "beacon" and b["url"] == "/api/replay"
    body = b["body"]
    assert set(body) == {"v", "sid", "seq", "page", "dev", "w", "h", "ev"}
    assert body["v"] == 1 and body["seq"] == 0 and body["page"] == "landing"
    assert body["dev"] == "pc" and (body["w"], body["h"]) == (1280, 800)
    assert re.fullmatch(r"[0-9a-f]{16}", body["sid"])
    assert body["ev"] == [[0, "m", 10, 10]]


def test_pointer_is_sampled_every_100ms_with_a_trailing_last_position() -> None:
    got = one(
        ls=ON,
        steps=[
            ["move", 10, 10],
            ["wait", 50],
            ["move", 30, 30],  # 100ms 안 — 꼬리로 100ms 에
            ["move", 40, 40],
            ["wait", 60],
            ["move", 42, 41],  # 4px 안 — 버림
            ["wait", 200],
            ["move", 100, 100],
            ["wait", 2000],
        ],
    )
    ev = bodies(got)[0]["ev"]
    assert ev == [[0, "m", 10, 10], [100, "m", 40, 40], [310, "m", 100, 100]]


def test_click_carries_position_and_area() -> None:
    areas = [{"id": "hero", "top": 0, "height": 500}]
    got = one(ls=ON, areas=areas, steps=[["click", 120, 80, "hero"], ["wait", 2000]])
    # 1초 뒤 보던 영역도 한 번(처음 정해짐)
    assert bodies(got)[0]["ev"] == [[0, "c", 120, 80, "hero"], [1000, "a", "hero"]]


def test_window_scroll_is_throttled_and_the_last_value_always_goes() -> None:
    got = one(
        ls=ON,
        steps=[
            ["scroll", 100],
            ["wait", 50],
            ["scroll", 150],
            ["wait", 50],
            ["scroll", 400],
            ["wait", 2000],
        ],
    )
    ev = bodies(got)[0]["ev"]
    assert ev == [[0, "s", 100], [200, "s", 400]]


def test_inner_scroll_names_the_area_and_the_scroller_index() -> None:
    areas = [
        {
            "id": "table",
            "top": 0,
            "height": 600,
            "scrollers": [
                {
                    "name": "s0",
                    "sh": 100,
                    "ch": 100,
                    "oy": "auto",
                },  # 넘치지 않음 — 세지 않는다
                {
                    "name": "s1",
                    "sh": 900,
                    "ch": 300,
                    "oy": "hidden",
                },  # 숨김 — 세지 않는다
                {"name": "s2", "sh": 900, "ch": 300, "oy": "auto"},
            ],
        }
    ]
    got = one(
        ls=ON, path="/app/", areas=areas, steps=[["inner", "s2", 240], ["wait", 2000]]
    )
    body = bodies(got)[0]
    assert body["page"] == "app-spread"
    assert [0, "e", "table", 0, 240] in body["ev"]


def test_tab_change_area_change_visibility_and_resize() -> None:
    areas = [
        {"id": "kpi", "top": 0, "height": 100},
        {"id": "table", "top": 100, "height": 900},
    ]
    got = one(
        ls=ON,
        path="/app/",
        search="?tab=gap",
        areas=areas,
        steps=[
            ["wait", 1000],  # 보던 영역 table
            ["tab", "gap"],  # 같은 탭 — 버림
            ["tab", "history"],
            ["tab", "nope"],
            ["wait", 1000],  # 그대로 — a 없음
            ["resize", 1000, 700],
            ["wait", 600],
            ["hide"],  # 곧바로 보냄
            ["take"],
        ],
    )
    sent = got["events"][0]  # 2초 묶음 하나 + 숨길 때 곧바로
    ev = [e for b in sent for e in b["body"]["ev"]]
    assert {b["body"]["page"] for b in sent} == {"app-gap"}
    kinds = [e[1:] for e in ev]
    assert kinds == [["a", "table"], ["p", "app-history"], ["r", 1000, 700], ["v", 0]]


def test_no_empty_batches_and_seq_counts_up() -> None:
    got = one(
        ls=ON,
        steps=[
            ["move", 1, 1],
            ["wait", 2000],
            ["wait", 6000],
            ["move", 50, 50],
            ["wait", 2000],
        ],
    )
    assert [b["seq"] for b in bodies(got)] == [0, 1]


def test_600_events_flush_at_once_and_pagehide_sends_everything() -> None:
    steps: list[list[Any]] = []
    for i in range(650):
        steps += [["move", (i % 2) * 100, 0], ["wait", 100]]
    got = one(ls=ON, steps=steps[:-1] + [["pagehide"], ["take"]])
    sent = bodies(got)
    assert sent and all(len(b["ev"]) <= 600 for b in sent)
    assert sum(len(b["ev"]) for b in sent) == 650
    assert all(
        len(json.dumps(b, separators=(",", ":")).encode()) <= 16384 for b in sent
    )


def test_withdrawn_consent_stops_and_drops() -> None:
    got = one(
        ls=ON,
        steps=[
            ["move", 10, 10],
            ["consent", {"kt.analytics": "denied"}, "storage"],
            ["wait", 2000],
            ["move", 99, 99],
            ["wait", 2000],
            ["take"],
        ],
    )
    assert bodies(got) == []


def test_consent_turned_on_mid_page_starts_recording() -> None:
    got = one(steps=[["move", 1, 1], ["consent", ON], ["move", 20, 20], ["wait", 2000]])
    (body,) = bodies(got)
    assert body["ev"] == [[0, "m", 20, 20]]


def test_a_segment_ends_after_two_hours() -> None:
    steps: list[list[Any]] = [["move", 1, 1]]
    for i in range(40):
        steps += [["wait", 200_000], ["move", 10 + i * 10, 0]]
    got = one(ls=ON, steps=steps + [["wait", 2000], ["take"]])
    sent = bodies(got)
    assert sent and max(e[0] for b in sent for e in b["ev"]) <= 7_200_000


def test_fetch_is_the_fallback_without_sendbeacon() -> None:
    got = one(ls=ON, noBeacon=True, steps=MOVES + [["take"]])
    (b,) = got["events"][0]
    assert b["how"] == "fetch" and b["url"] == "/api/replay"


def test_script_writes_no_storage_or_cookie_and_posts_to_one_path() -> None:
    assert (
        "setItem" not in JS
        and "document.cookie" not in JS
        and "sessionStorage" not in JS
    )
    assert set(re.findall(r"[\"'](/api/[\w/-]*)", JS)) == {"/api/replay"}
    assert re.findall(r"https?://[^\s\"']*", JS) == [
        ADMIN,
        "http://www.w3.org/2000/svg",
    ]
    assert f'const NOTICE_VERSION = "{NOTICE}"' in JS and NOTICE == "2026-10-17"
    got = one(ls=ON, steps=MOVES + [["click", 1, 1], ["pagehide"]])
    assert got["writes"] == []


# --- 재생 쪽 (074 §3.4) ----------------------------------------------------------------

FRAME = {"framed": True, "search": "?kt-replay=1", "ls": ON}


def test_frame_announces_ready_and_records_nothing() -> None:
    got = one(**FRAME, steps=MOVES + [["click", 3, 3], ["pagehide"], ["take"]])
    assert bodies(got) == []
    assert got["posted"] == [
        [{"type": "kt-replay-ready", "v": 1, "page": "landing"}, ADMIN]
    ]


def test_frame_draws_only_messages_from_the_admin_parent() -> None:
    areas = [
        {
            "id": "table",
            "top": 0,
            "height": 600,
            "scrollers": [{"name": "s0", "sh": 900, "ch": 300, "oy": "auto"}],
        }
    ]
    state = {
        "type": "kt-replay",
        "v": 1,
        "x": 40,
        "y": 60,
        "touch": False,
        "click": True,
        "sy": 300,
        "els": [["table", 0, 120], ["BAD", 0, 1]],
        "area": "table",
    }
    got = one(
        **FRAME,
        path="/app/",
        areas=areas,
        steps=[
            ["msg", "https://evil.example", "parent", state],
            ["msg", ADMIN, "other", state],
            ["msg", ADMIN, "parent", {**state, "v": 2}],
            ["msg", ADMIN, "parent", state],
        ],
    )
    assert got["scrolls"] == [300]
    assert got["inner"] == {"s0": 120}
    outline, cursor, ripple = got["layer"]
    assert cursor == {"display": "block", "left": "40px", "top": "60px", "kids": 1}
    assert outline["display"] == "block" and ripple["left"] == "22px"


def test_frame_hides_cursor_on_null_and_blocks_clicks_and_wheel() -> None:
    hide = {
        "type": "kt-replay",
        "v": 1,
        "x": None,
        "y": None,
        "sy": None,
        "els": [],
        "area": "",
    }
    got = one(
        **FRAME,
        steps=[
            ["msg", ADMIN, "parent", hide],
            ["press", "click"],
            ["press", "wheel"],
            ["press", "keydown"],
        ],
    )
    assert got["scrolls"] == []
    outline, cursor = got["layer"]
    assert cursor["display"] == "none" and outline["display"] == "none"
    assert [e for e in got["events"] if isinstance(e, dict)] == [
        {"press": "click", "prevented": True},
        {"press": "wheel", "prevented": True},
        {"press": "keydown", "prevented": True},
    ]
