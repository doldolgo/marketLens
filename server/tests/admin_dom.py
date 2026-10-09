"""관리자 화면 v3(스펙 064 §4) node 시험의 바탕 — 가짜 document·fetch·location·history·echarts.

042·041 의 방식을 ES 모듈로 옮겼다: `web/admin/*.js` 를 node 가 그대로 import 하고(web/package.json 이 "type": "module"),
화면 대신 가짜 노드(글자·자식·속성·듣기만)를 쓴다. 차트는 가짜 echarts 가 받은 option 을 기록하고, encodeHTML 은
vendor/echarts-5.5.1.min.js 와 같은 다섯 글자 바꿈이다(test_admin.py 가 사본에 같은 식이 있는지 본다).
시나리오마다 node 프로세스 하나 — 모듈 상태(고른 창·떠 있는 호출)가 다른 시나리오로 새지 않게.
"""

import json
import os
import shutil
import subprocess
from typing import Any

import pytest

from tests.test_deploy import ROOT

PRELUDE = r"""
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
const input = JSON.parse(readFileSync(0, 'utf8'))
const ADMIN = pathToFileURL(`${input.root}/web/admin/`)
const page = (name, tag = '') => import(new URL(`${name}${tag}`, ADMIN).href)

// 노드는 부모 하나 — 다른 곳에 붙이면 앞 부모에서 빠진다(브라우저 DOM 처럼)
class Node {
  constructor(tag) {
    Object.assign(this, { tag, kids: [], parent: null, dataset: {}, attrs: {}, _text: '', title: '', className: '', hidden: false, disabled: false, value: '', listeners: {} })
  }
  get textContent() { return this._text + this.kids.map((k) => (typeof k === 'string' ? k : k.textContent)).join('') }
  set textContent(v) { this._text = String(v); this.kids = [] }
  adopt(k) { for (const c of k) if (c instanceof Node) { if (c.parent) c.parent.kids = c.parent.kids.filter((x) => x !== c); c.parent = this } return k }
  append(...k) { this.kids.push(...this.adopt(k)) }
  replaceChildren(...k) { this.kids.forEach((c) => c instanceof Node && (c.parent = null)); this._text = ''; this.kids = this.adopt(k) }
  setAttribute(k, v) { this.attrs[k] = String(v) }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn) }
  fire(type, ev = {}) { (this.listeners[type] || []).forEach((fn) => fn(ev)) }
  // 진짜 ECharts(test_admin_render.py)가 글자 폭을 재려고 canvas 를 만들면 그림판 없음 — 글꼴 크기로 어림한다
  getContext() { return null }
}
const ENC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
const flush = async () => { for (let i = 0; i < 60; i++) await new Promise((r) => setImmediate(r)) }

// 화면 하나 — respond(url, init) 가 [상태, 본문] 을 돌려준다. hold(url) 가 참이면 그 응답을 붙잡아 둔다(s.held 의 함수로 푼다).
// buttons = { range: ['6h', …] } 면 querySelectorAll('[data-range]') 가 그 단추들
function setup({ visible = true, search = '', respond, hold = () => false, buttons = {}, charts = true } = {}) {
  const s = { calls: [], replaced: [], cleared: [], timers: 0, held: [], inflight: 0, maxInflight: 0, charts: new Map() }
  const byId = new Map()
  const groups = Object.fromEntries(Object.entries(buttons).map(([attr, values]) => [attr, values.map((v) => { const b = new Node('button'); b.dataset[attr] = v; return b })]))
  const docListeners = {}
  globalThis.document = {
    visibilityState: visible ? 'visible' : 'hidden',
    getElementById: (id) => byId.get(id) || byId.set(id, Object.assign(new Node('div'), { id })).get(id),
    createElement: (tag) => new Node(tag),
    querySelectorAll: (sel) => { const m = /^\[data-(\w+)\]$/.exec(sel); return (m && groups[m[1]]) || [] },
    addEventListener: (type, fn) => (docListeners[type] ||= []).push(fn),
  }
  globalThis.window = { addEventListener() {} }
  globalThis.location = { search, replace: (u) => s.replaced.push(u) }
  globalThis.history = { replaceState: (a, b, u) => { s.cleared.push(u); globalThis.location.search = '' } }
  globalThis.setTimeout = () => ++s.timers
  globalThis.clearTimeout = () => {}
  globalThis.fetch = (url, init = {}) => {
    s.calls.push(init.method ? `${init.method} ${url}` : url)
    s.maxInflight = Math.max(s.maxInflight, ++s.inflight)
    const answer = () => {
      s.inflight -= 1
      const [status, body] = respond(url, init)
      if (status === 'reject') throw new TypeError('Failed to fetch')
      return { status, text: async () => (typeof body === 'string' ? body : JSON.stringify(body)) }
    }
    if (hold(url)) return new Promise((done, fail) => s.held.push(() => { try { done(answer()) } catch (e) { fail(e) } }))
    return new Promise((done, fail) => { try { done(answer()) } catch (e) { fail(e) } })
  }
  globalThis.echarts = charts ? {
    init(el) { const c = { el, group: '', options: [], flags: [], setOption(o, f) { this.options.push(o); this.flags.push(f) }, resize() {}, dispose() { this.disposed = true }, dispatchAction() {} }; s.charts.set(el.id, c); return c },
    connect() {},
    format: { encodeHTML: (v) => (v == null ? '' : String(v)).replace(/([&<>"'])/g, (m, c) => ENC[c]) },
  } : undefined
  const text = (id) => byId.get(id)?.textContent
  const option = (id) => s.charts.get(id)?.options.at(-1)
  return { s, byId, groups, text, option, docListeners, click: (attr, v) => groups[attr].find((b) => b.dataset[attr] === v).fire('click') }
}
const out = {}
"""


def run_admin(scenario: str, payload: dict[str, Any] | None = None) -> Any:
    """PRELUDE + 시나리오를 node 하나로 돌려 표준 출력의 JSON 을 돌려준다(시나리오는 `out` 에 담는다)."""
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 관리자 스크립트를 돌리지 못한다")
    code = PRELUDE + scenario + "\nprocess.stdout.write(JSON.stringify(out))\n"
    done = subprocess.run(
        [node, "--input-type=module", "-e", code],
        input=json.dumps({"root": str(ROOT), **(payload or {})}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "TZ": "Asia/Seoul"},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)
