"""attention.js 를 node 로 돌린다 — 가짜 window·document·IntersectionObserver·MutationObserver·시계 (스펙 052 §3.2·§3.3·§4).

test_clarity.py 와 같은 방식이다. 영역은 (id, 문서 위 top, 높이) 이고 화면은 innerHeight 로 정한다 — 스크롤은 scrollY 를 바꿔
getBoundingClientRect 를 옮긴다. 단계 문자열로 시계·입력·숨김·탭 바뀜·동의를 움직이고, 보낸 비콘(sendBeacon·fetch)을 본다.
"""

import json
import os
import shutil
import subprocess

import pytest

from tests.test_deploy import ROOT
from tests.test_privacy import NOTICE, OLD

JS = (ROOT / "web/public/attention.js").read_text("utf-8")
# 지금 안내 판(NOTICE)과 바로 앞 판(OLD)은 방침 동의 스크립트·clarity.js 와 같은 값이다(060 §3.2 — 판은 NOTICE_DIGESTS 의 마지막 키)
ON = {"kt.analytics": "granted", "kt.analytics.v": NOTICE}

HARNESS = r"""
const [script, cases] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
process.stdout.write(JSON.stringify(cases.map((c) => {
  let clock = 1800000000000
  class FakeDate { static now() { return clock } }
  const data = new Map(Object.entries(c.ls || {}))
  const localStorage = {
    getItem: (k) => { if (c.lsFail) throw new Error('get'); return data.has(k) ? data.get(k) : null },
  }
  const bags = { window: {}, document: {} }
  let listens = 0
  const adder = (bag) => (type, fn) => { listens++; (bag[type] = bag[type] || []).push(fn) }
  const fire = (bag, type, ev) => (bag[type] || []).slice().forEach((fn) => fn(ev || {}))
  const view = { w: c.w || 1280, h: c.h || 800 }
  let scrollY = 0
  const elements = []
  const make = (id, top, height) => {
    const el = {
      top, height, isConnected: true,
      getAttribute: (n) => (n === 'data-area' ? id : null),
      getBoundingClientRect() { const t = this.top - scrollY; return { top: t, bottom: t + this.height, height: this.height } },
      closest() { return this },
    }
    elements.push(el)
    return el
  }
  for (const [id, top, height] of c.areas || []) make(id, top, height)
  const ios = []
  class IO {
    constructor(cb, opts) { this.cb = cb; this.opts = opts; this.els = new Set(); this.dead = false; ios.push(this) }
    observe(el) { this.els.add(el) }
    unobserve(el) { this.els.delete(el) }
    disconnect() { this.els.clear(); this.dead = true }
  }
  const mos = []
  class MO {
    constructor(cb) { this.cb = cb; this.on = false; mos.push(this) }
    observe() { this.on = true }
    disconnect() { this.on = false }
  }
  let rafs = []
  let timers = []
  let setTimeouts = 0
  const sent = []
  const window = {
    innerWidth: view.w, innerHeight: view.h, localStorage,
    addEventListener: adder(bags.window),
    IntersectionObserver: IO, MutationObserver: MO,
    requestAnimationFrame: (fn) => { rafs.push(fn); return rafs.length },
    setTimeout: (fn, ms) => { setTimeouts++; timers.push({ at: clock + ms, fn }); return setTimeouts },
    fetch: (url, opts) => { sent.push({ via: 'fetch', url, opts: { ...opts, body: undefined }, body: opts.body }); return Promise.resolve() },
  }
  window.top = c.framed ? {} : window
  const document = {
    visibilityState: 'visible', documentElement: {},
    querySelectorAll: () => elements.filter((e) => e.isConnected),
    addEventListener: adder(bags.document),
  }
  let beacon = c.beacon === undefined ? true : c.beacon
  const navigator = {
    globalPrivacyControl: c.gpc ? true : undefined,
    sendBeacon: c.noBeacon ? undefined : (url, body) => { sent.push({ via: 'beacon', url, body }); return beacon },
  }
  const location = { hostname: c.host || 'kimptrack.com', pathname: c.path || '/', search: c.search || '' }
  new Function('window', 'document', 'navigator', 'location', 'Date', script)(window, document, navigator, location, FakeDate)
  const settle = () => {
    for (const io of ios) {
      if (io.dead || !io.els.size) continue
      io.cb([...io.els].map((el) => {
        const r = el.getBoundingClientRect()
        return { target: el, isIntersecting: el.isConnected && r.height > 0 && r.bottom > 0 && r.top < view.h }
      }), io)
    }
    const fs = rafs
    rafs = []
    fs.forEach((f) => f(clock))
  }
  const byId = (id) => elements.find((e) => e.getAttribute('data-area') === id)
  const started = ios.length > 0
  const firstListens = listens
  settle()
  for (const step of c.steps || []) {
    const [kind, a = '', b = '', d = ''] = step.split(':')
    if (kind === 't') clock += Number(a)
    else if (kind === 'move') fire(bags.document, 'pointermove')
    else if (kind === 'scroll') { scrollY = Number(a); fire(bags.document, 'scroll'); settle() }
    else if (kind === 'settle') settle()
    else if (kind === 'hide') { document.visibilityState = 'hidden'; fire(bags.document, 'visibilitychange') }
    else if (kind === 'show') { document.visibilityState = 'visible'; fire(bags.document, 'visibilitychange') }
    else if (kind === 'pagehide') fire(bags.window, 'pagehide')
    else if (kind === 'click') {
      const target = a === 'none' ? { closest: () => null } : byId(a)
      for (let i = 0; i < Number(b || 1); i++) fire(bags.document, 'click', { target })
    }
    else if (kind === 'tab') fire(bags.window, 'kt:tab', { detail: a })
    else if (kind === 'grant') { data.set('kt.analytics.v', NOTICE); data.set('kt.analytics', 'granted') }
    else if (kind === 'revoke') { data.set('kt.analytics', 'denied'); data.delete('kt.analytics.v') }
    else if (kind === 'clarity') fire(bags.window, 'kt:clarity')
    else if (kind === 'storage') fire(bags.window, 'storage', { key: 'kt.analytics' })
    else if (kind === 'beacon') beacon = a === 'true'
    else if (kind === 'add') { make(a, Number(b), Number(d)); for (const mo of mos) if (mo.on) mo.cb([]) }
    else if (kind === 'timers') { const due = timers.filter((x) => x.at <= clock); timers = timers.filter((x) => x.at > clock); due.forEach((x) => x.fn()) }
    else throw new Error('step ' + step)
  }
  return {
    started, firstListens, listens, timers: timers.length, setTimeouts,
    sent: sent.map((s) => ({ via: s.via, url: s.url, opts: s.opts, bytes: Buffer.byteLength(s.body), body: JSON.parse(s.body) })),
  }
}).map((r) => r)))
""".replace("NOTICE", json.dumps(NOTICE))


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


def _one(**case: object) -> dict:
    return _run([case])[0]


def _bodies(got: dict) -> list[dict]:
    return [s["body"] for s in got["sent"]]


# 화면 위쪽에 꽉 보이는 영역 하나
HERO = [["hero", 0, 300]]


def test_other_hosts_frames_overlay_and_unknown_pages_do_nothing() -> None:
    for case in (
        {"host": "www.kimptrack.com"},
        {"host": "localhost"},
        {"framed": True},
        {"search": "?kt-overlay=1"},
        {"path": "/app/", "search": "?tab=gap&kt-overlay=1"},
        {"path": "/404.html"},
        {"path": "/privacy-20261011.html"},
        {"path": "/app"},
    ):
        got = _one(ls=ON, areas=HERO, **case, steps=["t:5000", "hide", "pagehide"])
        assert (got["listens"], got["started"], got["sent"]) == (0, False, []), case


def test_without_consent_only_two_listeners_and_kt_clarity_turns_it_on() -> None:
    for case in (
        {},
        {"ls": {"kt.analytics": "denied"}},
        {"ls": {**ON, "kt.analytics.v": "2026-10-01"}},  # 예전 판
        {"ls": {**ON, "kt.analytics.v": OLD}},  # 바로 앞 판(060 — 2026-10-09)
        {"ls": ON, "gpc": True},
        {"ls": ON, "lsFail": True},
    ):
        got = _one(areas=HERO, **case, steps=["t:5000", "hide"])
        assert (got["firstListens"], got["started"], got["sent"]) == (2, False, []), (
            case
        )
    # 같은 탭의 띠 저장 → kt:clarity → 그 순간부터 센다
    got = _one(
        areas=HERO, steps=["t:5000", "grant", "clarity", "settle", "t:2000", "hide"]
    )
    assert _bodies(got) == [
        {
            "v": 1,
            "page": "landing",
            "device": "pc",
            "pv": 1,
            "a": {"hero": [2000, 0, 1]},
        }
    ]
    # 다른 탭의 저장 → storage
    got = _one(areas=HERO, steps=["grant", "storage", "settle", "t:700", "hide"])
    assert _bodies(got)[0]["a"] == {"hero": [700, 0, 0]}
    # 동의가 아닌 채로 온 이벤트는 켜지 않는다
    got = _one(areas=HERO, steps=["clarity", "storage", "t:700", "hide"])
    assert got["sent"] == []


def test_visibility_rule_half_of_the_area_or_half_of_the_screen_and_40px() -> None:
    # 화면 높이 800 — 작은 영역은 자기 높이의 절반, 큰 영역은 화면 절반(400), 그리고 min(40px, 영역 높이) 이상
    areas = [
        ["half", 750, 100],  # 50px 보임 = 100 의 절반 → 보임
        ["under", 751, 100],  # 49px → 안 보임
        ["tall", 400, 3000],  # 400px 보임 = 화면 절반 → 보임
        ["tall-short", 401, 3000],  # 399px → 안 보임
        ["tiny", 0, 30],  # 30px 영역이 다 보임 → 보임(40px 보다 낮은 영역은 통째로)
        ["tiny-cut", 785, 30],  # 30px 영역 가운데 15px → 안 보임
        ["forty", 100, 40],  # 40px → 보임
        ["low", 761, 60],  # 39px(절반 30 은 넘지만 40 미만) → 안 보임
    ]
    got = _one(ls=ON, areas=areas, steps=["t:1500", "hide"])
    assert set(_bodies(got)[0]["a"]) == {"half", "tall", "tiny", "forty"}
    # 스크롤로 3,000px 영역이 화면을 다 채워도 보임, 화면 밖으로 나가면 안 보임
    got = _one(
        ls=ON,
        areas=[["table", 0, 3000], ["foot", 3000, 200]],
        steps=["t:1000", "scroll:1500", "t:1000", "scroll:2950", "t:1000", "hide"],
    )
    assert _bodies(got)[0]["a"] == {"table": [2000, 0, 1], "foot": [1000, 0, 1]}


def test_hidden_document_and_five_idle_minutes_do_not_count() -> None:
    got = _one(
        ls=ON,
        areas=HERO,
        steps=[
            "t:1000",
            "hide",  # 보냄 1 — 1,000ms
            "t:5000",  # 숨은 동안
            "show",
            "t:1000",
            "hide",  # 보냄 2 — 1,000ms 만(숨은 5초 빼고), pv 0
            "show",
            "move",
            "t:300000",
            "t:100000",  # 마지막 입력 뒤 5분이 지난 100초는 안 쌓인다
            "hide",
            "show",
            "move",
            "t:1000",
            "pagehide",  # 입력이 다시 오면 그때부터 — pagehide 도 보낸다
        ],
    )
    assert [(b["pv"], b["a"]) for b in _bodies(got)] == [
        (1, {"hero": [1000, 0, 1]}),
        (0, {"hero": [1000, 0, 0]}),
        (0, {"hero": [300000, 0, 0]}),
        (0, {"hero": [1000, 0, 0]}),
    ]


def test_caps_hold_over_the_whole_segment_even_across_sends() -> None:
    got = _one(
        ls=ON,
        areas=HERO,
        steps=[
            "t:290000",
            "move",
            "t:290000",
            "click:hero:30",
            "hide",  # 580,000ms · 30클릭
            "show",
            "move",
            "t:100000",
            "click:hero:30",
            "hide",  # 남은 20,000ms · 20클릭만
            "show",
            "move",
            "t:5000",
            "click:hero:5",
            "hide",  # 상한에 닿아 더할 것이 없다 — 보내지 않는다
        ],
    )
    assert [b["a"] for b in _bodies(got)] == [
        {"hero": [580000, 30, 1]},
        {"hero": [20000, 20, 0]},
    ]


def test_seen_is_sent_once_when_the_segment_total_reaches_one_second() -> None:
    got = _one(
        ls=ON,
        areas=HERO,
        steps=["t:999", "hide", "show", "t:1", "hide", "show", "t:5000", "hide"],
    )
    assert [b["a"]["hero"] for b in _bodies(got)] == [
        [999, 0, 0],
        [1, 0, 1],
        [5000, 0, 0],
    ]
