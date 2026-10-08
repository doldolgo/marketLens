"""관리자 화면 이용 절의 값 계산·부르기·틀 주고받기 (스펙 053 §4 node 확인).

042 `test_admin_traffic.py` 처럼 `web/admin/admin.js` 를 node 에 가짜 window·document·fetch·IntersectionObserver·틀 창과
싣는다. 그리기 모양·폭·실제 페이지 위 상자는 설계 세션이 브라우저로 본다(§4 마지막 줄).
"""

import json
import os
import shutil
import subprocess
from typing import Any

import pytest

from tests.test_deploy import _text

SITE = "https://kimptrack.com"

HARNESS = r"""
const { script, rows } = JSON.parse(require('fs').readFileSync(0, 'utf8'))
class Node {
  constructor(tag) {
    Object.assign(this, { tag, kids: [], parent: null, dataset: {}, attrs: {}, _text: '', title: '', className: '', hidden: false,
      open: false, disabled: false, listeners: {}, value: '', src: '', clientWidth: 640, props: {} })
    const n = this
    this.style = { setProperty(k, v) { n.props[k] = v } }
  }
  get textContent() { return this._text + this.kids.map((k) => (typeof k === 'string' ? k : k.textContent)).join('') }
  set textContent(v) { this._text = String(v); this.kids = [] }
  get classList() { const n = this; return { add(c) { n.className = `${n.className} ${c}`.trim() } } }
  get lastChild() { return this.kids[this.kids.length - 1] }
  adopt(k) {
    for (const c of k) if (c instanceof Node) { if (c.parent) c.parent.kids = c.parent.kids.filter((x) => x !== c); c.parent = this }
    return k
  }
  append(...k) { this.kids.push(...this.adopt(k)) }
  prepend(...k) { this.kids.unshift(...this.adopt(k)) }
  replaceChildren(...k) { this.kids.forEach((c) => c instanceof Node && (c.parent = null)); this._text = ''; this.kids = []; this.kids = this.adopt(k) }
  setAttribute(k, v) { this.attrs[k] = String(v) }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn) }
  fire(type) { (this.listeners[type] || []).forEach((fn) => fn({})) }
}
const flush = async () => { for (let i = 0; i < 30; i++) await new Promise((r) => setImmediate(r)) }
const PAGES = ['landing', 'app-spread', 'app-history', 'app-gap', 'app-pp', 'app-health', 'app-flow', 'privacy', 'kimp-chart', 'kimp-history']

// 새 화면 하나 — feed(days) 가 화면 이용 응답을 정하고, hold 면 그 응답을 붙잡아 둔다. 타이머는 기록만
function screen(feed) {
  const byId = new Map()
  const options = PAGES.map((p) => { const o = new Node('option'); o.value = p; return o })
  const devices = ['pc', 'mobile'].map((d) => { const b = new Node('button'); b.dataset.device = d; return b })
  const posted = []
  const frameWin = { postMessage: (data, origin) => posted.push({ data: JSON.parse(JSON.stringify(data)), origin }) }
  const frame = new Node('iframe')
  frame.contentWindow = frameWin
  byId.set('scr-frame', frame)
  const document = {
    visibilityState: 'visible',
    getElementById: (id) => byId.get(id) || byId.set(id, new Node('div')).get(id),
    createElement: (tag) => new Node(tag),
    createElementNS: (ns, tag) => new Node(tag),
    createDocumentFragment: () => new Node('#fragment'),
    querySelectorAll: (sel) => (sel === '#scr-page option' ? options : sel === '[data-device]' ? devices : []),
    addEventListener() {},
  }
  const s = { calls: [], inflight: 0, max: 0, hold: false, held: [], timers: [], posted, frames: [] }
  const fetch = (url) => {
    s.calls.push(url)
    const mine = url.startsWith('/svc/api/admin/attention')
    if (mine) s.max = Math.max(s.max, ++s.inflight)
    const answer = () => {
      if (mine) s.inflight--
      const days = Number(new URLSearchParams(url.split('?')[1] || '').get('days'))
      const body = mine ? feed(days) : { status: 'ok' }
      return { status: 200, text: async () => JSON.stringify(body) }
    }
    if (mine && s.hold) return new Promise((done) => s.held.push(() => done(answer())))
    return Promise.resolve(answer())
  }
  const win = { listeners: {}, addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn) } }
  let observer = null
  class IntersectionObserver { constructor(cb) { this.cb = cb; observer = this } observe(t) { this.target = t } }
  const location = { search: '', replace() {} }
  const history = { replaceState() {} }
  const setTimeout = (fn, ms) => { s.timers.push({ fn, ms }); return s.timers.length }
  const api = new Function('document', 'fetch', 'location', 'history', 'setTimeout', 'clearTimeout', 'Node', 'window', 'IntersectionObserver',
    script + '\n;return { screenValues, busiest, screenAnswer, plain, overlayAreas, scr, frameUrl }')(
    document, fetch, location, history, setTimeout, () => {}, Node, win, IntersectionObserver)
  // 틀 주소가 바뀔 때마다 기록 — src 를 가로챈다
  let src = ''
  Object.defineProperty(frame, 'src', { get: () => src, set: (v) => { src = v; s.frames.push(v) } })
  const view = (on) => observer.cb([{ isIntersecting: on }])
  const pick = (id, value) => { byId.get(id).value = value; byId.get(id).fire('change') }
  const message = (origin, source, data) => win.listeners.message.forEach((fn) => fn({ origin, source, data }))
  const text = (id) => document.getElementById(id).textContent
  const find = (n, tag) => (typeof n === 'string' ? [] : [...(n.tag === tag ? [n] : []), ...n.kids.flatMap((k) => find(k, tag))])
  const attCalls = () => s.calls.filter((u) => u.startsWith('/svc/api/admin/attention'))
  return { api, s, view, pick, message, text, find, attCalls, frameWin, frame, options, devices, byId, node: (id) => document.getElementById(id) }
}

const NOW = Date.now()
const ok = (days, rs) => ({ state: 'ok', code: null, gateAt: NOW - 30 * 86400000, days, from: '2026-10-18', to: '2026-10-25', fetchedAt: NOW, rows: rs })

async function run() {
  const out = {}
  // 1. 계산 — 순수 함수
  {
    const v = screen(() => ok(7, rows))
    const row = { page: 'landing', device: 'pc', pv: 4, areas: [
      { id: 'hero', ms: 12000, clicks: 3, seen: 5 },
      { id: 'top', ms: 3000, clicks: 0, seen: 1 },
      { id: 'faq', ms: 0, clicks: 2, seen: 0 },
    ] }
    out.values = v.api.screenValues(row, null)
    out.present = v.api.screenValues(row, ['hero', 'faq', 'foot', 'foot'])
    out.overlay = v.api.overlayAreas(out.present)
    // 단계 경계 — 가장 긴 것의 20% → 1, 21% → 2, 100% → 5, 0 → 0
    out.levels = v.api.screenValues({ pv: 10, areas: [
      { id: 'a', ms: 10000, clicks: 0, seen: 10 }, { id: 'b', ms: 2000, clicks: 0, seen: 10 },
      { id: 'c', ms: 2100, clicks: 0, seen: 10 }, { id: 'd', ms: 0, clicks: 1, seen: 0 }] }, null).ranked.map((a) => [a.id, a.level, a.rank])
    out.empty = v.api.screenValues(undefined, null)
    out.busiest = [
      v.api.busiest(rows, 'pc'), v.api.busiest(rows, 'mobile'), v.api.busiest([], 'pc'),
      v.api.busiest([{ page: 'privacy', device: 'pc', pv: 3 }, { page: 'kimp-chart', device: 'pc', pv: 3 }], 'pc'),
    ]
    const ans = (r, page, device, days) => v.api.plain(v.api.screenAnswer(v.api.screenValues(r, null), page, device, days))
    out.answers = [
      ans({ pv: 0, areas: [] }, 'landing', 'pc', 7),
      ans({ pv: 10, areas: [{ id: 'hero', ms: 30000, clicks: 4, seen: 9 }, { id: 'foot', ms: 1000, clicks: 0, seen: 1 }, { id: 'top', ms: 5000, clicks: 0, seen: 8 }] }, 'landing', 'pc', 7),
      ans(row, 'app-history', 'mobile', 1),
      ans({ pv: 6, areas: [] }, 'privacy', 'pc', 90),
    ]
  }
  return out
}
run().then((out) => process.stdout.write(JSON.stringify(out)), (err) => { console.error(err.stack); process.exit(1) })
"""

# 7일 기본 응답 — PC 는 기록 탭(pv 12)이 가장 크고, 휴대폰은 랜딩(pv 3)
ROWS = [
    {
        "page": "landing",
        "device": "pc",
        "pv": 5,
        "areas": [{"id": "hero", "ms": 5000, "clicks": 0, "seen": 5}],
    },
    {
        "page": "app-history",
        "device": "pc",
        "pv": 12,
        "areas": [
            {"id": "table", "ms": 60000, "clicks": 12, "seen": 12},
            {"id": "filters", "ms": 6000, "clicks": 1, "seen": 2},
            {"id": "list", "ms": 3000, "clicks": 0, "seen": 1},
        ],
    },
    {
        "page": "landing",
        "device": "mobile",
        "pv": 3,
        "areas": [{"id": "hero", "ms": 9000, "clicks": 0, "seen": 3}],
    },
]


@pytest.fixture(scope="module")
def screens() -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 admin.js 를 돌리지 못한다")
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps({"script": _text("web/admin/admin.js"), "rows": ROWS}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "TZ": "Asia/Seoul"},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _pick(areas: list[dict], *keys: str) -> list[list]:
    return [[a[k] for k in keys] for a in areas]


def test_values_per_area_follow_the_formulas(screens: dict[str, Any]) -> None:
    """§3.3 — t = ms/pv 초(소수 1자리), r = seen/pv %(정수, 100 에서 멈춤), c = clicks/pv × 100(소수 1자리),
    L = ⌈ms/최대 × 5⌉(0 이면 0), 순위 = t 큰 순, low = r < 20, small = pv < 5."""
    v = screens["values"]
    assert (v["pv"], v["small"]) == (4, True)
    keys = ("id", "t", "r", "c", "level", "rank", "low", "small", "state")
    assert _pick(v["ranked"], *keys) == [
        ["hero", 3.0, 100, 75.0, 5, 1, False, True, "ok"],
        ["top", 0.8, 25, 0.0, 2, 2, False, True, "ok"],
        ["faq", 0.0, 0, 50.0, 0, 3, True, True, "ok"],
    ]
    assert v["blank"] == [] and v["away"] == []
    assert screens["levels"] == [["a", 5, 1], ["c", 2, 2], ["b", 1, 3], ["d", 0, 4]]
    empty = screens["empty"]
    assert (empty["pv"], empty["ranked"], empty["small"]) == (0, [], True)


def test_page_areas_split_into_ranked_no_record_and_gone(
    screens: dict[str, Any],
) -> None:
    """§3.3 — 페이지가 알린 영역 기준: 기록 없는 페이지 영역 = '기록 없음'(L 0·순위 없음), 페이지에 없는 기록 영역 =
    '지금 화면에 없음'(목록 끝). 순위·단계는 페이지에 있는 것끼리. 페이지로 넘기는 값은 페이지에 있는 것만."""
    p = screens["present"]
    assert _pick(p["ranked"], "id", "level", "rank") == [
        ["hero", 5, 1],
        ["faq", 0, 2],
    ]
    assert _pick(p["blank"], "id", "level", "rank", "state", "low") == [
        ["foot", 0, None, "none", False]
    ]
    assert _pick(p["away"], "id", "rank", "state") == [["top", None, "gone"]]
    assert [a["id"] for a in screens["overlay"]] == ["hero", "faq", "foot"]
    assert set(screens["overlay"][0]) == {
        "id",
        "level",
        "rank",
        "t",
        "r",
        "c",
        "low",
        "small",
    }


def test_default_screen_is_the_biggest_pv_for_the_device(
    screens: dict[str, Any],
) -> None:
    """§3.2 — 기본 화면 = 고른 기간·기기에서 pv 가 가장 큰 화면(같으면 고르기 순서의 앞, 없으면 랜딩)."""
    assert screens["busiest"] == ["app-history", "landing", "landing", "privacy"]


def test_answer_has_three_branches(screens: dict[str, Any]) -> None:
    """§3.5 — pv 0 · 보통 · 표본 적음(끝에 한 문장). 가장 덜 닿은 곳은 도달률이 가장 낮은 곳, 이름표에 없는 id 는 그대로."""
    assert screens["answers"] == [
        "이 기간 이 화면·기기의 동의한 방문 기록이 없다.",
        "최근 7일 랜딩 PC — 페이지뷰 10. 가장 오래 본 곳은 첫 화면(평균 3.0초), 가장 덜 닿은 곳은 바닥(도달 10%).",
        "오늘 대시보드 기록 휴대폰 — 페이지뷰 4. 가장 오래 본 곳은 hero(평균 3.0초), 가장 덜 닿은 곳은"
        " faq(도달 0%). 표본 적음 — 페이지뷰가 5보다 적어 단계·순위가 흔들린다.",
        "최근 90일 처리방침 PC — 페이지뷰 6. 영역 기록은 없다.",
    ]
