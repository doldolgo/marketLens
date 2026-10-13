"""관리자 화면 v4(스펙 066 §4) node 시험의 바탕 — 가짜 document·fetch·location·history·echarts·Blob.

064 의 방식을 이어받는다: `web/admin/*.js` 를 node 가 그대로 import 하고(web/package.json 이 "type": "module"), 화면 대신
가짜 노드(글자·자식·속성·듣기·style·classList)를 쓴다. 차트는 가짜 echarts 가 받은 option 을 기록하고, encodeHTML 은
vendor/echarts-5.5.1.min.js 와 같은 다섯 글자 바꿈이다(test_admin.py 가 사본에 같은 식이 있는지 본다).
066 에서 더한 것: id 를 붙인 노드는 getElementById 로 찾힌다, history 의 쌓기·바꾸기·뒤로 가기(popstate), location.hash,
createElementNS, 내려받기(Blob 글자·파일 이름 기록), 키보드(document keydown), 찾기 칸 input.
시나리오마다 node 프로세스 하나 — 모듈 상태(고른 값·떠 있는 호출)가 다른 시나리오로 새지 않게.
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
let byId = new Map()

// 노드는 부모 하나 — 다른 곳에 붙이면 앞 부모에서 빠진다(브라우저 DOM 처럼). id 를 붙이면 getElementById 로 찾힌다
class Node {
  constructor(tag) {
    Object.assign(this, { tag, kids: [], parent: null, dataset: {}, attrs: {}, _text: '', title: '', className: '', hidden: false, disabled: false, value: '', listeners: {}, style: {}, _id: '' })
  }
  get id() { return this._id }
  set id(v) { this._id = String(v); byId.set(this._id, this) }
  get textContent() { return this._text + this.kids.map((k) => (typeof k === 'string' ? k : k.textContent)).join('') }
  set textContent(v) { this._text = String(v); this.kids.forEach((c) => c instanceof Node && (c.parent = null)); this.kids = [] }
  adopt(k) { for (const c of k) if (c instanceof Node) { if (c.parent) c.parent.kids = c.parent.kids.filter((x) => x !== c); c.parent = this } return k }
  append(...k) { this.kids.push(...this.adopt(k)) }
  replaceChildren(...k) { this.kids.forEach((c) => c instanceof Node && (c.parent = null)); this._text = ''; this.kids = this.adopt(k) }
  remove() { if (this.parent) { this.parent.kids = this.parent.kids.filter((x) => x !== this); this.parent = null } }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = v }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null }
  removeAttribute(k) { delete this.attrs[k] }
  hasAttribute(k) { return k in this.attrs }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn) }
  fire(type, ev = {}) { (this.listeners[type] || []).forEach((fn) => fn({ target: this, preventDefault() {}, ...ev })) }
  // 내려받기 — download 를 단 a 의 click 은 파일 이름과 주소를 지금 화면 기록에 남긴다
  click() { if (this.tag === 'a' && 'download' in this.attrs) globalThis.__s?.downloads.push([this.attrs.download, this.attrs.href]); this.fire('click') }
  focus() { globalThis.document.activeElement = this }
  get classList() {
    const node = this
    const has = (c) => node.className.split(/\s+/).includes(c)
    return {
      contains: has,
      add: (c) => { if (!has(c)) node.className = `${node.className} ${c}`.trim() },
      remove: (c) => { node.className = node.className.split(/\s+/).filter((x) => x && x !== c).join(' ') },
      toggle: (c, on) => { const want = on === undefined ? !has(c) : on; if (want) node.classList.add(c); else node.classList.remove(c); return want },
    }
  }
  // '.이름' 하나만 — 부모를 거슬러 그 클래스를 가진 첫 노드
  closest(sel) { const cls = sel.replace(/^\./, ''); let n = this; while (n) { if (n.className.split(/\s+/).includes(cls)) return n; n = n.parent } return null }
  // 진짜 ECharts(test_admin_render.py)가 글자 폭을 재려고 canvas 를 만들면 그림판 없음 — 글꼴 크기로 어림한다
  getContext() { return null }
}
const ENC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
const flush = async () => { for (let i = 0; i < 80; i++) await new Promise((r) => setImmediate(r)) }
const walk = (n) => (typeof n === 'string' ? [] : [n, ...n.kids.flatMap(walk)])

// 화면 하나 — respond(url, init) 가 [상태, 본문] 을 돌려준다. hold(url) 가 참이면 그 응답을 붙잡아 둔다(s.held 의 함수로 푼다).
// buttons = { range: ['6h', …] } 면 querySelectorAll('[data-range]') 가 그 단추들(data- 뒤 이름은 글자·숫자만)
function setup({ visible = true, search = '', hash = '', respond, hold = () => false, buttons = {}, charts = true } = {}) {
  byId = new Map()
  const s = { calls: [], replaced: [], cleared: [], pushed: [], backs: 0, timers: 0, held: [], inflight: 0, maxInflight: 0, charts: new Map(), themes: [], actions: [], blobs: [], downloads: [] }
  const groups = Object.fromEntries(Object.entries(buttons).map(([attr, values]) => [attr, values.map((v) => { const b = new Node('button'); b.dataset[attr] = v; return b })]))
  const docListeners = {}
  const winListeners = {}
  globalThis.document = {
    visibilityState: visible ? 'visible' : 'hidden',
    getElementById: (id) => byId.get(id) || (() => { const n = new Node('div'); n.id = id; return n })(),
    createElement: (tag) => new Node(tag),
    createElementNS: (ns, tag) => new Node(tag),
    querySelectorAll: (sel) => { const m = /^\[data-(\w+)\]$/.exec(sel); return (m && groups[m[1]]) || [] },
    addEventListener: (type, fn) => (docListeners[type] ||= []).push(fn),
    body: new Node('body'),
    documentElement: new Node('html'),
    activeElement: null,
  }
  globalThis.window = { addEventListener: (type, fn) => (winListeners[type] ||= []).push(fn) }
  // 기록(history) — 쌓기·바꾸기·뒤로 가기. 주소의 ?…·#… 를 location 에 옮긴다
  const entries = [{ state: null, url: `${search}${hash}` }]
  let at = 0
  const apply = (url) => { const u = new URL(url, 'https://admin.test'); globalThis.location.search = u.search; globalThis.location.hash = u.hash }
  globalThis.location = { search, hash, replace: (u) => s.replaced.push(u) }
  globalThis.history = {
    get state() { return entries[at].state },
    pushState: (state, _, u) => { entries.splice(at + 1); entries.push({ state, url: u }); at += 1; s.pushed.push(u); apply(u) },
    replaceState: (state, _, u) => { entries[at] = { state, url: u }; s.cleared.push(u); apply(u) },
    back: () => { s.backs += 1; if (at === 0) return; at -= 1; apply(entries[at].url); (winListeners.popstate || []).forEach((fn) => fn({})) },
  }
  globalThis.setTimeout = () => ++s.timers
  globalThis.clearTimeout = () => {}
  globalThis.URL.createObjectURL = (blob) => { s.blobs.push(blob); return `blob:admin/${s.blobs.length}` }
  globalThis.URL.revokeObjectURL = () => {}
  globalThis.__s = s
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
    init(el, theme) {
      const c = { el, theme, group: '', options: [], flags: [], handlers: {}, setOption(o, f) { this.options.push(o); this.flags.push(f) }, getOption() { return this.options.at(-1) }, resize() {}, dispose() { this.disposed = true }, dispatchAction(a) { s.actions.push([el.id, a]) }, on(type, fn) { (this.handlers[type] ||= []).push(fn) } }
      s.charts.set(el.id, c)
      return c
    },
    registerTheme: (name, theme) => s.themes.push([name, theme]),
    connect() {},
    format: { encodeHTML: (v) => (v == null ? '' : String(v)).replace(/([&<>"'])/g, (m, c) => ENC[c]) },
  } : undefined
  const text = (id) => byId.get(id)?.textContent
  const option = (id) => s.charts.get(id)?.options.at(-1)
  const key = (k, extra = {}) => (docListeners.keydown || []).forEach((fn) => fn({ key: k, target: globalThis.document.body, ...extra }))
  return { s, byId, groups, text, option, key, docListeners, winListeners, click: (attr, v) => groups[attr].find((b) => b.dataset[attr] === v).fire('click') }
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
        timeout=120,
        check=False,
        env={**os.environ, "TZ": "Asia/Seoul"},
    )
    assert done.returncode == 0, done.stderr
    if os.environ.get("ADMIN_DEBUG"):
        print(done.stderr)
    return json.loads(done.stdout)
