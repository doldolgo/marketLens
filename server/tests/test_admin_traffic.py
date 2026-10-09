"""관리자 접속 절 v3 의 글자 만들기·부르기 판단 (스펙 042 §4 node 논리 확인).

033 `test_clarity.py`·041 의 `test_admin.py` 처럼 `web/admin/admin.js` 를 node 에 가짜 window·document·fetch·시계와 싣고,
그리기가 아니라 답 문장 글자·창 부르기 순서·열지도 강도만 본다. 폭·사진·XSS 화면·대비는 설계 세션이 브라우저로 본다(§4).
시간대는 Asia/Seoul 로 고정한다(시간 막대·열지도는 브라우저 시간대).
"""

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.test_deploy import _text

DAY = 86_400
# 시행일 2026-10-11 00:00 KST (일요일)
G = int(datetime(2026, 10, 10, 15, tzinfo=UTC).timestamp())

HARNESS = r"""
const { script, cases, draws } = JSON.parse(require('fs').readFileSync(0, 'utf8'))
// 노드는 부모 하나 — 다른 곳에 붙이면 앞 부모에서 빠진다(브라우저 DOM 처럼. 한 노드를 두 칸에 붙이는 실수가 드러난다)
class Node {
  constructor(tag) {
    Object.assign(this, { tag, kids: [], parent: null, dataset: {}, attrs: {}, _text: '', title: '', className: '', hidden: false, open: false, disabled: false, listeners: {} })
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
}
const flush = async () => { for (let i = 0; i < 30; i++) await new Promise((r) => setImmediate(r)) }

// 새 화면 하나 — 보이는 탭(묶음이 돈다), 타이머는 기록만(다음 회차는 오지 않는다), 접속 응답은 hold 면 붙잡아 둔다
function screen(respond) {
  const byId = new Map()
  const buttons = ['24h', '7d', '30d'].map((w) => { const b = new Node('button'); b.dataset.window = w; return b })
  const document = {
    visibilityState: 'visible',
    getElementById: (id) => byId.get(id) || byId.set(id, new Node('div')).get(id),
    createElement: (tag) => new Node(tag),
    createElementNS: (ns, tag) => new Node(tag),
    createDocumentFragment: () => new Node('#fragment'),
    querySelectorAll: (sel) => (sel === '[data-window]' ? buttons : []),
    addEventListener() {},
  }
  const s = { calls: [], timers: 0, inflight: 0, maxInflight: 0, hold: false, held: [], replaced: [] }
  const fetch = (url) => {
    s.calls.push(url)
    const access = url.startsWith('/svc/api/admin/access')
    if (access) s.maxInflight = Math.max(s.maxInflight, ++s.inflight)
    const answer = () => {
      if (access) s.inflight--
      const [status, body] = respond(url)
      return { status, text: async () => (typeof body === 'string' ? body : JSON.stringify(body)) }
    }
    if (access && s.hold) return new Promise((done) => s.held.push(() => done(answer())))
    return Promise.resolve(answer())
  }
  const location = { search: '', replace: (to) => s.replaced.push(to) }
  const history = { replaceState() {} }
  // 053 — 화면 이용 절이 window(메시지·크기)와 IntersectionObserver(절이 보이는지)를 듣는다. 여기서는 듣기만 받는다
  const window = { addEventListener() {} }
  class IntersectionObserver { observe() {} }
  const api = new Function('document', 'fetch', 'location', 'history', 'setTimeout', 'clearTimeout', 'Node', 'window', 'IntersectionObserver',
    script + '\n;return { answers, plain, heatLevel, sentence, fillTraffic }')(
    document, fetch, location, history, () => ++s.timers, () => {}, Node, window, IntersectionObserver)
  const click = (w) => buttons.find((b) => b.dataset.window === w).listeners.click.forEach((fn) => fn())
  const text = (id) => byId.get(id).textContent
  const pressed = () => buttons.filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset.window)
  return { api, s, click, text, node: (id) => byId.get(id), pressed, accessCalls: () => s.calls.filter((u) => u.startsWith('/svc/api/admin/access')) }
}
const txt = (n) => (typeof n === 'string' ? n : n.textContent)
const find = (n, cls) => (typeof n === 'string' ? [] : [...(` ${n.className} `.includes(` ${cls} `) ? [n] : []), ...n.kids.flatMap((k) => find(k, cls))])
const bolds = (n) => (typeof n === 'string' ? [] : [...(n.tag === 'b' ? [n] : []), ...n.kids.flatMap(bolds)])

const NOW = Math.floor(Date.now() / 1000)
// 접속 응답 — 요청 창으로 답한다(answerAs 가 있으면 그 창으로), 세 창 모두 열림
const access = (url, answerAs) => {
  const asked = new URLSearchParams(url.split('?')[1] || '').get('window')
  return [200, { state: 'ok', code: null, fetchedAt: Date.now(), refreshSec: 60, window: answerAs || asked, windows: ['24h', '7d', '30d'],
    gateAt: (NOW - 10 * 86400) * 1000, startTs: NOW - 86400, endTs: NOW, totals: {}, hourly: [], status: {}, ws: {}, classes: {},
    visitors: { state: 'ok', code: null, sinceTs: NOW - 86400, confirmed: 1, shaped: 2, returning: 0, capped: false, days: [] },
    geo: { state: 'pending', code: null } }]
}
const plainOk = (url) => (url.startsWith('/svc/api/admin/access') ? access(url) : [200, { status: 'ok' }])

async function run() {
  const out = {}
  // 1. 7일을 누르면 접속 경로만 1회 곧바로 — 다른 일곱 경로 0회, 느린 묶음의 다음 시각(타이머)은 그대로
  {
    const v = screen(plainOk)
    await flush()
    const [before, timers] = [v.s.calls.length, v.s.timers]
    v.click('7d')
    await flush()
    out.click = { first: v.s.calls.slice(0, before), after: v.s.calls.slice(before), timers: v.s.timers - timers, pressed: v.pressed(), meta: v.text('m-q1'), window: v.text('m-window'), note: v.text('window-note') }
  }
  // 1-전. 게이트 전 응답(windows 24h 만) — 닫힌 버튼 옆 안내 글(060 §3.5)
  {
    const v = screen((url) => (url.startsWith('/svc/api/admin/access') ? [200, { ...access(url)[1], windows: ['24h'] }] : [200, { status: 'ok' }]))
    await flush()
    out.preNote = v.text('window-note')
  }
  // 2. 접속 응답을 붙잡은 채 24h→7d→30d — 겹친 호출 0, 끝에 30d 한 번, 7d 응답은 그리지 않음
  {
    const v = screen(plainOk)
    await flush()
    const before = v.accessCalls().length
    v.s.hold = true
    v.click('24h')
    v.click('7d')
    v.click('30d')
    await flush()
    const loading = { calls: v.accessCalls().slice(before), block: v.text('b-q1'), held: v.s.held.length }
    v.s.held.shift()()
    await flush()
    const after7d = { calls: v.accessCalls().slice(before), block: v.text('b-q1'), meta: v.text('m-q1') }
    v.s.held.shift()()
    await flush()
    out.overlap = { loading, after7d, calls: v.accessCalls().slice(before), max: v.s.maxInflight, meta: v.text('m-q1'), pressed: v.pressed(), left: v.s.held.length }
  }
  // 3. 7d 요청에 서버가 24h 로 답함 → 고른 창이 24h
  {
    const v = screen((url) => (url.startsWith('/svc/api/admin/access') ? access(url, '24h') : [200, { status: 'ok' }]))
    await flush()
    v.click('7d')
    await flush()
    out.fallback = { calls: v.accessCalls(), pressed: v.pressed(), meta: v.text('m-q1') }
  }
  // 4. 창 바꿈 호출만 401 비JSON → `/?relogin=1` 로 한 번(036 그대로)
  {
    let expire = false
    const v = screen((url) => (expire && url.startsWith('/svc/api/admin/access') ? [401, '<html>login</html>'] : plainOk(url)))
    await flush()
    expire = true
    v.click('30d')
    await flush()
    out.expired = { replaced: v.s.replaced, calls: v.accessCalls() }
  }
  // 답 문장·강도 — 가짜 값으로 글자만
  const v = screen(plainOk)
  out.answers = cases.map((a) => v.api.answers(a).map(v.api.plain))
  const node = new Node('p')
  v.api.sentence(node, v.api.answers(cases[0])[0])
  out.bold = bolds(node).map((k) => [k.tag, k.textContent])
  out.glue = node.kids.filter((k) => typeof k !== 'string').map((k) => [k.tag, k.className, k.textContent])
  // 마지막 답 틀 경우 = 긴 경로 — 자른 b 의 title 은 전체, 붙이지 않는다(20자 넘음)
  const long = new Node('p')
  v.api.sentence(long, v.api.answers(cases[cases.length - 1])[4])
  out.titled = long.kids.map((k) => (typeof k === 'string' ? 'text' : [k.tag, k.className, k.textContent, k.title]))
  // 그리기 — ② 단서·③ 칸·④ 눈금·덩어리 머리·본문 글자(창 목록 밖 이름이 Object 의 것을 집지 않는지)
  out.draws = draws.map((a) => {
    v.api.fillTraffic(a)
    return {
      cols: find(v.node('b-q3'), 'col').map((c) => [txt(c.kids[0].kids[0]), c.kids.slice(1).map(txt), find(c, 'brow').length]),
      clues: find(v.node('b-q2'), 'clue').map(txt),
      hours: find(v.node('b-q4'), 'hours').map((h) => h.kids.map(txt).filter(Boolean)),
      meta: v.text('m-q1'),
      all: [1, 2, 3, 4, 5, 6].map((q) => v.text(`m-q${q}`) + v.text(`a-q${q}`) + v.text(`b-q${q}`)).join('\n'),
    }
  })
  out.levels = [[0, 100], [100, 100], [20, 100], [21, 100], [1, 3], [3, 0]].map(([x, m]) => v.api.heatLevel(x, m))
  return out
}
run().then((out) => process.stdout.write(JSON.stringify(out)), (err) => { console.error(err.stack); process.exit(1) })
"""


def _vis(confirmed: int, shaped: int, returning: int = 0, **extra: Any) -> dict:
    return {
        "state": "ok",
        "code": None,
        "confirmed": confirmed,
        "shaped": shaped,
        "returning": returning,
        "capped": False,
        "days": [],
        **extra,
    }


ALL = ["24h", "7d", "30d"]
LONG_PATH = "/" + "a" * 79
BEFORE = {
    "state": "unconfigured",
    "code": "before_gate",
}


def _case(window: str, start: int, end: int, **extra: Any) -> dict:
    return {
        "state": "ok",
        "window": window,
        "windows": ALL,
        "gateAt": G * 1000,
        "startTs": start,
        "endTs": end,
        "totals": {},
        "status": {},
        "classes": {},
        "hourly": [],
        **extra,
    }


CLASSES = {
    "browser": {"requests": 410, "pages": 200},
    "search": {"requests": 120, "pages": 0},
    "ai": {"requests": 60, "pages": 0},
    "preview": {"requests": 0, "pages": 0},
    "tool": {"requests": 130, "pages": 0},
    "scanner": {"requests": 220, "pages": 0},
    "operator": {"requests": 50, "pages": 0},
    "unknown": {"requests": 10, "pages": 0},
}
NETS = [["telecom_kr", 30, 60], ["cloud", 2, 40], ["telecom", 8, 20]]
CHANNELS = [["direct", 20, 50], ["search", 12, 30], ["social", 5, 10], ["ai", 3, 4]]
PRE = {"windows": ["24h"], "visitors": BEFORE, "geo": BEFORE}
TOTALS = {"requests": 1000, "humanPages": 200, "jsViews": 50, "probes": 1234}


def _hours(start: int, *hours: int) -> list[dict]:
    """시행일 0시부터 n시간 뒤 칸마다 스크립트가 돈 페이지 10."""
    return [{"ts": start + h * 3600, "jsViews": 10} for h in hours]


# 7일 창 — 시행일로 잘린 시작, 끝 = 시행 + 2일 5시간(그날 = 오늘 KST)
END7 = G + 2 * DAY + 5 * 3600
HOURLY7 = [
    {"ts": G + 19 * 3600, "jsViews": 10},  # 일요일 19시
    {"ts": G + 20 * 3600, "jsViews": 10},
    {"ts": G + 21 * 3600, "jsViews": 10},
    {"ts": G + DAY + 9 * 3600, "jsViews": 12},  # 월요일 9시
]
DAYS7 = [
    {"ts": G, "confirmed": 5, "shaped": 9, "returning": 1},
    {"ts": G + DAY, "confirmed": 9, "shaped": 20, "returning": 2},
    {"ts": G + 2 * DAY, "confirmed": 1, "shaped": 3, "returning": 0},
]

CASES: list[tuple[dict, dict[int, str]]] = [
    # 0. §4 예 — 30일·시작이 시행일로 잘림·6일, confirmed 60·shaped 600·returning 15 → 10.0·100.0·25%, '대부분 봇' 경계 = 붙음
    (
        _case("30d", G, G + 6 * DAY, visitors=_vis(60, 600, 15, sinceTs=G)),
        {
            0: "10-11 부터 6일 동안 스크립트가 돈 방문자는 하루 평균 10.0명, 브라우저 모양까지 치면 100.0명"
            " — 실제 사람은 이 사이다. 확인의 25%는 다시 온 사람이다. 브라우저 모양의 대부분은 봇이다.",
        },
    ),
    # 1. 경계 +1 — 붙지 않음
    (
        _case("30d", G, G + 6 * DAY, visitors=_vis(61, 600, 15, sinceTs=G)),
        {
            0: "10-11 부터 6일 동안 스크립트가 돈 방문자는 하루 평균 10.2명, 브라우저 모양까지 치면 100.0명"
            " — 실제 사람은 이 사이다. 확인의 25%는 다시 온 사람이다.",
        },
    ),
    # 2. 24시간·시행 뒤 — 창이 시행 전 시간을 품어 꼬리, ② 다 채움, ③ ok·국내 통신사·클라우드 대부분 봇, ⑥ 5xx 0·ws5xx 3
    (
        _case(
            "24h",
            G - 10 * 3600,
            G + 13 * 3600 + 120,
            visitors=_vis(3, 40, sinceTs=G, channels=CHANNELS),
            geo={"state": "ok", "networks": NETS},
            totals=TOTALS,
            classes=CLASSES,
            status={"5xx": 0, "ws5xx": 3, "4xx": 12},
        ),
        {
            0: "확인 3, 브라우저 모양 40(날마다 센 방문자) — 오늘·어제(KST)를 따로 세어 더한 수라 사람 수가 아니다."
            " 10-11 부터 센 값이다. 브라우저 모양의 대부분은 봇이다.",
            1: "요청 1,000줄 가운데 사람 브라우저 모양은 41%다. 자동 요청은 스캐너(22%)·자동화 도구(13%) 순으로"
            " 많다. 사람 모양 페이지 가운데 스크립트가 돈 것은 25%다.",
            2: "확인의 75%는 국내 통신사 망에서 왔다. 데이터센터·클라우드 망은 브라우저 모양의 33%이고 그중 확인은"
            " 2뿐 — 대부분 봇이다. 들어온 길은 직접 50%·검색 30%·소셜·커뮤니티 13% 순이다.",
            5: "서버 오류(5xx)는 없었고, 대시보드 재접속 실패는 3건(배포 때 몇 건은 정상)이다. 4xx 는 12건,"
            " 탐색 경로 요청은 1,234줄이다.",
        },
    ),
    # 3. 시행 전 — ①·③·⑤ 전 틀, ④ 24시간(가장 많은 시 = 같으면 이른 시), ⑥ 5xx 있음·ws5xx 0
    (
        _case(
            "24h",
            G - 4 * DAY,
            G - 3 * DAY,
            **PRE,
            totals={"humanPages": 1234, "jsViews": 56},
            hourly=[
                {"ts": G - 4 * DAY + 3600, "jsViews": 2, "humanPages": 10},
                {"ts": G - 4 * DAY + 7200, "jsViews": 5, "humanPages": 20},
                {"ts": G - 4 * DAY + 10800, "jsViews": 5, "humanPages": 1},
            ],
            paths=[["/", 617]],
            tabs=[["history", 3], ["spread", 1]],
            devices=[["mobile", 600]],
            status={"5xx": 2, "ws5xx": 0, "4xx": 0},
        ),
        {
            0: "방문자는 10-11 부터 센다. 지금은 줄 수만 — 사람 모양 페이지 1,234,"
            " 그중 스크립트가 돈 페이지 56.",
            2: "나라·망 종류와 들어온 길은 10-11 부터 센다. 지금은 외부 출처·utm 만"
            " 페이지 줄로 보인다.",
            3: "최근 24시간 중 화면이 가장 많이 뜬 때는 2시(5회)다. 스크립트가 돈 페이지 보기 12회, 사람 모양"
            " 페이지 31회.",
            4: "사람 모양 페이지는 / 경로가 50%로 가장 많고, 대시보드는 기록/통계 탭으로 들어온 경우가 75%다."
            " 기기는 휴대폰 49%(페이지 줄 기준).",
            5: "서버 오류(5xx)는 2건, 대시보드 재접속 실패는 0건이다. 4xx 는 0건, 탐색 경로 요청은 0줄이다.",
        },
    ),
    # 4. 분모 0 — 요청·페이지가 없으면 ② 는 '기록 없음', ④ 24시간 스크립트 0 문장
    (
        _case(
            "24h", G - 4 * DAY, G - 3 * DAY, **PRE, hourly=[{"ts": G, "humanPages": 7}]
        ),
        {
            1: "기록 없음",
            3: "최근 24시간 동안 스크립트가 돈 페이지 보기가 없다. 사람 모양 페이지 7회.",
        },
    ),
    # 5. 분모 0 조각만 빠짐 — 요청 0 이라 ② 앞 두 문장이 빠진다
    (
        _case(
            "24h",
            G - 4 * DAY,
            G - 3 * DAY,
            **PRE,
            totals={"humanPages": 10, "jsViews": 5},
        ),
        {1: "사람 모양 페이지 가운데 스크립트가 돈 것은 50%다."},
    ),
    # 6. 7일 — ④ 가장 몰린 세 시간·가장 많은 칸(월요일)·오늘을 뺀 날마다 범위, ③ 국내 통신사 없음·클라우드 경계 5 > 4
    (
        _case(
            "7d",
            G,
            END7,
            hourly=HOURLY7,
            visitors=_vis(15, 32, sinceTs=G, days=DAYS7, channels=CHANNELS),
            geo={"state": "ok", "networks": [["telecom", 35, 60], ["cloud", 5, 40]]},
        ),
        {
            2: "데이터센터·클라우드 망은 브라우저 모양의 40%이고 그중 확인은 5다. 들어온 길은 직접 50%·검색 30%·"
            "소셜·커뮤니티 13% 순이다.",
            3: "화면이 실제로 뜬 페이지 보기는 19~22시에 가장 몰린다. 가장 많은 칸은 월요일 9시(12회)."
            " 날마다 확인 방문자는 5~9명이다.",
        },
    ),
    # 7. ③ 클라우드 경계 4 ≤ 4 → '뿐', geo 받는 중·받지 못함, ⑤ 시행 뒤(경로 없음 — 탭·기기만)
    (
        _case(
            "7d",
            G,
            END7,
            visitors=_vis(
                12, 50, sinceTs=G, devices=[["desktop", 5, 30], ["mobile", 7, 20]]
            ),
            geo={"state": "ok", "networks": [["telecom_kr", 36, 60], ["cloud", 4, 40]]},
            tabs=[["spread", 2]],
        ),
        {
            2: "확인의 90%는 국내 통신사 망에서 왔다. 데이터센터·클라우드 망은 브라우저 모양의 40%이고 그중 확인은"
            " 4뿐 — 대부분 봇이다.",
            4: "대시보드는 실시간 스프레드 탭으로 들어온 경우가 100%다. 기기는 휴대폰 58%(확인 방문자 기준).",
        },
    ),
    (
        _case("7d", G, END7, visitors=_vis(1, 2, sinceTs=G), geo={"state": "pending"}),
        {2: "나라·망 종류는 자료를 받는 중이다."},
    ),
    (
        _case(
            "7d",
            G,
            END7,
            visitors=_vis(1, 2, sinceTs=G),
            geo={"state": "error", "code": "http_404"},
            paths=[["/app/", 4]],
            totals={"humanPages": 10},
        ),
        {
            2: "나라·망 종류 자료를 받지 못했다(http_404).",
            4: "사람 모양 페이지는 /app/ 경로가 40%로 가장 많다.",
        },
    ),
    # 10·11. ④-기간 {h2} = {h1}+3 — 24 를 넘을 때만 24 를 뺀다(21 → 21~24시, 22 → 22~1시)
    (
        _case("7d", G, END7, hourly=_hours(G, 21, 22, 23)),
        {
            3: "화면이 실제로 뜬 페이지 보기는 21~24시에 가장 몰린다."
            " 가장 많은 칸은 일요일 21시(10회)."
        },
    ),
    (
        _case("7d", G, END7, hourly=_hours(G, 22, 23, 24)),
        {
            3: "화면이 실제로 뜬 페이지 보기는 22~1시에 가장 몰린다."
            " 가장 많은 칸은 월요일 0시(10회)."
        },
    ),
    # 12. (마지막) ⑤ 경로가 60자를 넘으면 자르고 '…' — 전체는 그 b 의 title(§3.10)
    (
        _case(
            "7d",
            G,
            END7,
            paths=[[LONG_PATH, 5]],
            totals={"humanPages": 10},
            visitors=_vis(1, 2, sinceTs=G),
        ),
        {4: f"사람 모양 페이지는 {LONG_PATH[:60]}… 경로가 50%로 가장 많다."},
    ),
]

# 그리기 경우 — fillTraffic 을 통째로 돌려 칸 글자만 본다(§3.8 geo 글·§3.9 화면 상한·§3.4 ④ 눈금·§3.10 창 이름)
NETS_OK = [["telecom_kr", 30, 60], ["cloud", 2, 40]]
COUNTRIES = [[f"{chr(65 + i // 26)}{chr(65 + i % 26)}", 30 - i, 40] for i in range(30)]
END24 = G + 20 * 3600 + 600  # 20:10 KST — 마지막 칸이 20시
DRAWS = [
    # 0. geo 받는 중 — 나라·망 두 칸 모두 같은 글(노드 하나를 두 칸에 붙이면 나라 칸이 빈다)
    _case("7d", G, END7, visitors=_vis(1, 2, sinceTs=G), geo={"state": "pending"}),
    # 1. 지난달 판 — 두 칸에 ▲ 글, cloud 단서는 '그중 확인' 과 ▲ 글을 함께
    _case(
        "7d",
        G,
        END7,
        visitors=_vis(1, 2, sinceTs=G),
        geo={"state": "ok", "month": "2026-09", "networks": NETS_OK, "countries": []},
    ),
    # 2. 나라 30행 — 화면 상한 21행(위 5 + '16개 더')
    _case(
        "7d",
        G,
        END7,
        visitors=_vis(1, 2, sinceTs=G),
        geo={
            "state": "ok",
            "month": "2026-10",
            "networks": NETS_OK,
            "countries": COUNTRIES,
        },
    ),
    # 3. 24시간 — 마지막 칸 20시: 끝 글자 '지금' 앞 두 칸의 18시는 비운다
    _case(
        "24h",
        END24 - DAY,
        END24,
        hourly=[
            {"ts": END24 - END24 % 3600 - (23 - i) * 3600, "jsViews": 1}
            for i in range(24)
        ],
    ),
    # 4. 목록 밖 창 이름 — Object 의 것을 집지 않고 고른 창(24시간)으로 그린다
    _case(
        "constructor", G, G + 6 * DAY, visitors=_vis(60, 600, 15, sinceTs=G, days=DAYS7)
    ),
    # 5. 30일 창 시작이 게이트로 잘림 — 게이트 전 날들의 이름표는 날짜로(060 §3.5)
    _case("30d", G, G + 6 * DAY, visitors=_vis(60, 600, 15, sinceTs=G, days=DAYS7)),
]


@pytest.fixture(scope="module")
def traffic() -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 admin.js 를 돌리지 못한다")
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps(
            {
                "script": _text("web/admin/admin.js"),
                "cases": [c for c, _ in CASES],
                "draws": DRAWS,
            }
        ),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "TZ": "Asia/Seoul"},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_answer_sentences_follow_the_templates(traffic: dict[str, Any]) -> None:
    """§3.5 — 틀마다 가짜 값 하나로 글자 그대로, '대부분 봇' 경계(같으면 붙고 +1 이면 안 붙음), 분모 0 조각은 빠지고
    다 빠지면 '기록 없음', 5xx 0·ws5xx 3 → '없었고'·'(배포 때 몇 건은 정상)'."""
    for (_, expected), got in zip(CASES, traffic["answers"], strict=True):
        for block, text in expected.items():
            assert got[block] == text, (block, got[block])


def test_answer_values_are_bold_elements(traffic: dict[str, Any]) -> None:
    """§3.5 — `{…}` 자리는 b 요소다(글자는 textContent). §3.9 — 값은 바로 뒤 글자(공백 전까지)와 줄 안 바꿈 span 하나."""
    assert traffic["bold"] == [
        ["b", "10-11 부터 6일"],
        ["b", "10.0"],
        ["b", "100.0"],
        ["b", "25"],
    ]
    assert traffic["glue"] == [
        ["span", "nb", "10-11 부터 6일"],
        ["span", "nb", "10.0명,"],
        ["span", "nb", "100.0명"],
        ["span", "nb", "25%는"],
    ]


def test_long_path_in_the_answer_is_cut_with_the_full_text_in_title(
    traffic: dict[str, Any],
) -> None:
    """§3.10 — 답 ⑤ 의 경로(방문자 글자)는 60자에서 자르고 전체는 그 b 의 title, 20자를 넘어 붙이지 않는다(줄을 바꾼다)."""
    assert traffic["titled"][1] == ["b", "", f"{LONG_PATH[:60]}…", LONG_PATH]
    assert traffic["titled"][0] == "text"


def test_drawn_blocks_keep_geo_words_in_both_columns_and_cap_long_lists(
    traffic: dict[str, Any],
) -> None:
    """§3.8 — geo 글은 나라·망 두 칸 모두에(받는 중·지난달 판), 지난달 판의 cloud 단서는 '그중 확인' 과 ▲ 글을 함께.
    036 §3.9 — 피드 상한을 믿지 않고 목록은 21행까지. §3.4 ④ — '지금' 앞 두 칸의 정시는 비운다. §3.10 — 목록 밖 창 이름."""
    pending, old, many, day, odd, cut = traffic["draws"]
    for name in ("나라", "망 종류"):
        col = next(c for c in pending["cols"] if c[0] == name)
        assert col[1] == ["자료 받는 중 — 다음 갱신(60초)에 찬다"], col
        col = next(c for c in old["cols"] if c[0] == name)
        assert col[1][0] == "▲지난달 판 2026-09 으로 셈", col
    assert "그중 확인 2 · ▲지난달 판 2026-09 으로 셈" in old["clues"][1]
    country = next(c for c in many["cols"] if c[0] == "나라")
    assert country[2] == 21
    assert country[1][5].startswith("16개 더 — ")
    assert day["hours"] == [["0시", "6시", "12시", "지금"]]
    assert odd["meta"] == "24시간 · 날마다 센 방문자"
    assert "function" not in odd["all"] and "native code" not in odd["all"]
    # 060 §3.5 — 게이트로 잘린 30일 창의 날 막대: 게이트 전 날들은 날짜로 적고(정책 낱말 없음), 30일이 다 차는 날
    assert "10-11 전 24일 — 세지 않음 · 11-09 부터 30일이 다 찬다" in cut["all"]
    for drawn in traffic["draws"]:
        assert "시행" not in drawn["all"] and "개정" not in drawn["all"]


def test_window_click_calls_only_the_access_path_at_once(
    traffic: dict[str, Any],
) -> None:
    """§3.2 — 처음 두 묶음은 여덟 경로(접속은 ?window=24h), 7일을 누르면 접속 경로만 1회(`?window=7d`), 타이머(느린 묶음의
    다음 시각)는 새로 걸지 않는다."""
    click = traffic["click"]
    assert len(click["first"]) == 8
    assert "/svc/api/admin/access?window=24h" in click["first"]
    assert click["after"] == ["/svc/api/admin/access?window=7d"]
    assert click["timers"] == 0
    assert click["pressed"] == ["7d"]
    assert click["meta"] == "7일 · 날마다 센 방문자"
    # 060 §3.5 — 창 줄 meta: 게이트로 잘린 7일 창은 'MM-DD 부터 n일'(자료가 있는 날만), 게이트가 지나 닫힌 버튼 안내는 없다
    assert re.match(r"\d{2}-\d{2} 부터 1일\u00a0· 60초마다\u00a0· ", click["window"]), (
        click["window"]
    )
    assert click["note"] == ""


def test_closed_window_buttons_say_from_which_day_they_count(
    traffic: dict[str, Any],
) -> None:
    """060 §3.5 — 게이트 전(windows 24h 만)이면 닫힌 7일·30일 버튼 옆에 'MM-DD 부터 센다' — 정책 낱말 없이 날짜만."""
    assert re.fullmatch(r"7일·30일은 \d{2}-\d{2} 부터 센다", traffic["preNote"])


def test_rapid_window_clicks_never_overlap_and_draw_only_the_last(
    traffic: dict[str, Any],
) -> None:
    """§3.2 — 접속 호출이 떠 있으면 겹쳐 부르지 않고 끝난 뒤 한 번(마지막 창만), 7d 응답은 그리지 않는다."""
    o = traffic["overlap"]
    assert o["loading"]["calls"] == ["/svc/api/admin/access?window=7d"]
    assert o["loading"]["held"] == 1
    assert o["loading"]["block"] == "… 30일 불러오는 중"
    assert o["after7d"]["calls"] == [
        "/svc/api/admin/access?window=7d",
        "/svc/api/admin/access?window=30d",
    ]
    assert o["after7d"]["block"] == "… 30일 불러오는 중"
    assert o["after7d"]["meta"] == "30일"
    assert o["calls"] == o["after7d"]["calls"] and o["max"] == 1 and o["left"] == 0
    assert o["meta"] == "30일 · 날마다 센 방문자"
    assert o["pressed"] == ["30d"]


def test_server_answering_24h_moves_the_picked_window_back(
    traffic: dict[str, Any],
) -> None:
    """§3.2 — 응답 window 가 요청한 창과 다르면 변수와 버튼을 응답 창으로 되돌린다."""
    f = traffic["fallback"]
    assert f["calls"][-1] == "/svc/api/admin/access?window=7d"
    assert f["pressed"] == ["24h"]
    assert f["meta"] == "24시간 · 날마다 센 방문자"


def test_expired_window_call_reloads_once(traffic: dict[str, Any]) -> None:
    """036 그대로 — 창 바꿈 호출만 401 비JSON 이어도 `/?relogin=1` 로 한 번 새로고침한다."""
    e = traffic["expired"]
    assert e["calls"][-1] == "/svc/api/admin/access?window=30d"
    assert e["replaced"] == ["/?relogin=1"]


def test_heatmap_level_is_ceil_of_five_steps(traffic: dict[str, Any]) -> None:
    """§3.4 ④ — 0 → h0, 최댓값 → h5, 최댓값 100 에서 20 → h1·21 → h2."""
    assert traffic["levels"] == ["h0", "h5", "h1", "h2", "h2", "h0"]
