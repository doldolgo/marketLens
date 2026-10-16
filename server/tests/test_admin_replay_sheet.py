"""관리자 재생 판(스펙 074 §3.3·§3.5·§4 node) — `#detail=replay:<sid>`: session 을 next 로 끝까지 받기, 틀 주소(대시보드 탭·kt-replay)·크기,
준비 알림의 출처·창·화면 검사, 재생 중 메시지(누름 한 번)·빈 시간 건너뛰기·숨김·화면 바뀜(다시 띄우고 준비까지 멈춤)·5초 무응답 한 줄,
Space·←/→·시간 막대·사건 목록·속도·닫기, 없음·bad_sid·404·Redis 오류·'앞부분만', 따라 보기(after=next 2초·3초 뒤·30초 멈춤·뒤로 끌면 끔),
XSS(화면·영역 이름은 글로만, 틀은 띄우지 않음). visitors.js 를 가짜 DOM 에 싣고 시계·타이머를 손으로 돌린다.
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_addrs import BUTTONS

SITE = "https://kimptrack.com"
SID = "0123456789abcdef"
NOW = 1_792_000_100_000
S0 = NOW - 60_000
X = "<img src=x onerror=alert(1)>"


def _session(s0: int, dev: str = "pc", **meta: Any) -> dict[str, Any]:
    """조각 하나 — 묶음 다섯(빈 틈 25초·7초, 숨김 1초, 대시보드 탭 바뀜, 화면 크기 바뀜)."""
    batches = [
        {
            "at": s0 + 2_000,
            "seq": 0,
            "ev": [
                [0, "m", 100, 100],
                [500, "c", 110, 120, "filters"],
                [900, "s", 300],
                [1_000, "s", 800],
                [1_500, "a", "table"],
            ],
        },
        {
            "at": s0 + 4_000,
            "seq": 1,
            "ev": [
                [2_000, "m", 300, 300],
                [2_100, "e", "table", 0, 400],
                [3_900, "m", 320, 330],
            ],
        },
        {"at": s0 + 30_000, "seq": 2, "ev": [[28_000, "v", 0], [29_000, "v", 1]]},
        {
            "at": s0 + 32_000,
            "seq": 3,
            "ev": [
                [30_000, "p", "app-gap"],
                [30_500, "c", 50, 60, "tabs"],
                [31_000, "r", 1280, 800],
                [32_000, "m", 60, 70],
            ],
        },
        {
            "at": s0 + 40_000,
            "seq": 4,
            "ev": [[39_000, "m", 200, 210], [40_000, "a", "table"]],
        },
    ]
    row = {
        "sid": SID,
        "ip": "203.0.113.7",
        "dev": dev,
        "page": "app-spread",
        "pages": ["app-spread", "app-gap"],
        "now": "app-gap",
        "first": s0 + 2_000,
        "last": s0 + 40_000,
        "n": 5,
        "clicks": 2,
        "w": 1440,
        "h": 900,
        **meta,
    }
    return {"meta": row, "batches": batches}


HARNESS = (
    BUTTONS.replace("const respond", "const respond0")
    + r"""
const S = input.session
let shown = input.shown ?? S.batches.length
const respond = (url) => {
  const [path, query] = url.split('?')
  const q = new URLSearchParams(query || '')
  if (input.answers?.[url]) return input.answers[url]
  if (path === '/svc/api/admin/replay/session') {
    if (input.status) return input.status
    const after = Number(q.get('after') || 0)
    const all = S.batches.slice(0, shown)
    const part = all.slice(after, after + input.page)
    return [200, { state: 'ok', code: null, meta: S.meta, batches: part, next: after + part.length, more: after + part.length < all.length }]
  }
  if (path === '/svc/api/admin/replay/live') return [200, { state: 'ok', code: null, now: clock, rows: [S.meta] }]
  return [404, { detail: 'Not Found' }]
}
const v = setup({ respond, hash: input.hash || '', buttons: BUTTONS })
document.getElementById('sheet').hidden = true
const timers = []
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length }
globalThis.clearTimeout = (id) => { if (id && timers[id - 1]) timers[id - 1].fn = null }
let clock = input.now
Date.now = () => clock
const fire = async (ms) => { for (const t of timers.filter((x) => x.fn && x.ms === ms)) { const f = t.fn; t.fn = null; f() } await flush() }
const tick = async (n) => { for (let i = 0; i < n; i++) { clock += 50; await fire(50) } }
const pending = (ms) => timers.filter((x) => x.fn && x.ms === ms).length
await page('visitors.js')
await flush()
const posted = []
const win = { postMessage: (m, o) => posted.push([JSON.parse(JSON.stringify(m)), o]) }
const frame = () => v.byId.get('rp-frame')
const attach = () => { if (frame()) frame().contentWindow = win }
attach()
const msg = (data, o = {}) => (v.winListeners.message || []).forEach((fn) => fn({ origin: o.origin ?? 'https://kimptrack.com', source: o.source ?? win, data }))
const ready = (pg) => msg({ type: 'kt-replay-ready', v: 1, page: pg })
const sessionCalls = () => v.s.calls.filter((c) => c.includes('/replay/session?'))
const list = () => (v.byId.get('rp-list')?.kids || []).map((b) => b.kids.map((k) => k.textContent))
const snap = () => ({
  open: !document.getElementById('sheet').hidden, title: v.text('sheet-title'), unit: v.text('sheet-unit'), tag: v.text('t-sheet'),
  tagCls: document.getElementById('t-sheet').className, note: v.text('sheet-note'), custom: document.getElementById('sheet-custom').hidden,
  src: frame()?.src ?? null, size: frame() ? [frame().attrs.width, frame().attrs.height] : null,
  stage: { ...(v.byId.get('rp-stage')?.style || {}) }, frameNote: v.byId.get('rp-note')?.hidden === false ? v.text('rp-note') : null,
  shade: v.byId.get('rp-shade')?.hidden === false, time: v.text('rp-time') ?? null, play: v.text('rp-play') ?? null,
  speed: (v.byId.get('rp-speed')?.kids || []).filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.textContent),
  skip: v.byId.get('rp-skip')?.attrs['aria-pressed'] ?? null, fill: v.byId.get('rp-fill')?.style.width ?? null,
  marks: (v.byId.get('rp-marks')?.kids || []).map((k) => [k.className, k.style.left, k.style.width ?? null]),
  current: (v.byId.get('rp-list')?.kids || []).findIndex((b) => b.attrs['aria-current'] === 'true'),
  posted: posted.length, last: posted.at(-1)?.[0] ?? null, to: posted.at(-1)?.[1] ?? null,
})
"""
)


def _run(steps: str, **payload: Any) -> Any:
    base = {
        "session": _session(S0),
        "page": 2,
        "now": NOW,
        "hash": f"#detail=replay%3A{SID}",
    }
    return run_admin(HARNESS + steps, {**base, **payload})


FLOW = r"""
out.load = { ...snap(), calls: sessionCalls() }
// 준비 전에는 보내지 않는다 — 출처 다름·창 다름·화면 다름·꼴 다름도 준비가 아니다
msg({ type: 'kt-replay-ready', v: 1, page: 'app-spread' }, { origin: 'https://evil.example' })
msg({ type: 'kt-replay-ready', v: 1, page: 'app-spread' }, { source: {} })
msg({ type: 'kt-replay-ready', v: 1, page: 'app-gap' })
msg({ type: 'kt-replay-ready', v: 2, page: 'app-spread' })
msg('kt-replay-ready')
await tick(4)
out.ignored = { posted: posted.length, time: v.text('rp-time') }
ready('app-spread')
out.ready = snap()
// 재생 0.6초 — 누름은 한 번만 click 참
await tick(12)
out.played = { ...snap(), clicks: posted.filter(([m]) => m.click).length, kinds: [...new Set(posted.map(([m]) => m.type))] }
// Space 멈춤 — 시계가 가도 그대로
v.key(' ')
await tick(10)
out.paused = snap()
// → 5초 — 빈 틈 안(4초→29초)
v.key('ArrowRight')
out.right = snap()
// 다시 재생 — 빈 틈 25초는 1초에(건너뛰기 켬), 숨김 구간에서 커서 숨김
v.key(' ')
await tick(19)
out.hidden = snap()
// 화면 바뀜(app-gap) — 틀을 다시 띄우고 준비까지 멈춘다
await tick(20)
out.reload = snap()
await tick(10)
out.held = snap()
attach()
ready('app-gap')
await tick(24)
out.gap = snap()
// ← 5초 — 앞 화면(스프레드)으로 돌아가 다시 띄움, 5초 안에 준비가 없으면 한 줄을 띄우고 이어 간다
v.key('ArrowLeft')
out.back = snap()
await fire(5000)
out.silent = snap()
// 사건 목록 줄 → 그 시각, 시간 막대 → 그 자리, 속도·건너뛰기
v.byId.get('rp-list').kids[0].fire('click')
out.row = snap()
const bar = v.byId.get('rp-bar')
bar._width = 1000
bar.fire('pointerdown', { clientX: 500, pointerId: 1 })
bar.fire('pointermove', { clientX: 750 })
bar.fire('pointerup', {})
bar.fire('pointermove', { clientX: 100 })
out.bar = snap()
v.byId.get('rp-speed').kids[2].fire('click')
v.byId.get('rp-skip').fire('click')
out.controls = snap()
out.list = list()
// 닫기 — 틀을 비우고 시계를 멈춘다
document.getElementById('sheet-close').fire('click')
await tick(4)
out.closed = { ...snap(), src: frame()?.src ?? null, ticks: pending(50), polls: pending(2000) }
"""


@pytest.fixture(scope="module")
def flow() -> dict[str, Any]:
    return _run(FLOW)


def test_sheet_loads_every_page_and_frames_the_dashboard_tab(
    flow: dict[str, Any],
) -> None:
    """§3.3 — 판 머리 '화면 재생' · 'IP · 기기 · 시작 KST', 073 session 을 after=next 로 more 가 끝날 때까지, 틀 = 공개 사이트 그 화면 +
    kt-replay=1(대시보드 /app/?tab=), 크기 = 조각의 화면 크기(w×h), 칸 맞춤 변수(늘리지 않음 — 창 높이 900 에서 몫 640/900)."""
    got = flow["load"]
    assert got["open"] and got["title"] == "화면 재생" and got["custom"] is False
    assert got["unit"] == "203.0.113.7 · PC · 2026-10-15 02:47:22 KST"
    assert got["calls"] == [
        f"/svc/api/admin/replay/session?sid={SID}&after=0",
        f"/svc/api/admin/replay/session?sid={SID}&after=2",
        f"/svc/api/admin/replay/session?sid={SID}&after=4",
    ]
    assert got["src"] == f"{SITE}/app/?tab=spread&kt-replay=1"
    assert got["size"] == ["1440", "900"]
    assert got["stage"]["--fw"] == "1440px" and got["stage"]["--fit"] == "0.7111"
    assert got["stage"]["--stage-h"] == "640px"
    assert got["frameNote"] == "페이지를 띄우는 중…"
    assert (
        got["note"] == ""
    )  # 가짜 DOM 에는 카드 '?' 가 없다 — 뜻 줄은 정적 시험이 본다
    assert got["tag"] == "" and got["posted"] == 0
    assert got["time"] == "02:47:20 KST · 경과 0:00 / 전체 0:39"
    # 시간 막대 — 숨김 칸 하나·화면 바뀜 눈금 하나·누름 점 둘
    assert [m[0] for m in got["marks"]] == ["rp-hide", "rp-tick", "rp-dot", "rp-dot"]


def test_messages_go_only_to_a_ready_frame_from_the_site_origin(
    flow: dict[str, Any],
) -> None:
    """§3.4 — 준비 알림은 출처 https://kimptrack.com·창 = 틀·화면 = 띄운 화면·꼴(type·v)이 맞을 때만. 그 전에는 보내지 않고 시계도 멈춘다.
    준비 뒤 그 시각의 상태 하나를 공개 사이트 출처로."""
    assert flow["ignored"] == {
        "posted": 0,
        "time": "02:47:20 KST · 경과 0:00 / 전체 0:39",
    }
    got = flow["ready"]
    assert got["frameNote"] is None and got["posted"] == 1 and got["to"] == SITE
    assert got["last"] == {
        "type": "kt-replay",
        "v": 1,
        "x": 100,
        "y": 100,
        "touch": False,
        "click": False,
        "sy": 0,
        "els": [["table", 0, 0]],
        "area": "",
    }


def test_playback_sends_state_changes_with_one_click_and_skips_idle(
    flow: dict[str, Any],
) -> None:
    """§3.3 재생 — 시각이 바뀌면 그 시각의 상태(같으면 다시 안 보냄), 누름은 한 번만 click 참. Space 멈춤, → 5초, 빈 틈(25초)은 1초에,
    숨김 구간은 회색 덮개·커서 null."""
    played = flow["played"]
    assert played["clicks"] == 1 and played["kinds"] == ["kt-replay"]
    assert played["last"]["x"] == 110 and played["last"]["sy"] == 0
    assert played["time"].endswith("경과 0:00 / 전체 0:39") and played["play"] == "❚❚"
    paused = flow["paused"]
    assert paused["play"] == "▶" and paused["time"] == played["time"]
    assert paused["posted"] == played["posted"]
    assert flow["right"]["time"].endswith("경과 0:05 / 전체 0:39")
    hidden = flow["hidden"]
    assert hidden["shade"] is True and hidden["last"]["x"] is None
    assert hidden["time"].endswith("경과 0:28 / 전체 0:39")


def test_page_change_reloads_the_frame_and_waits_for_ready(
    flow: dict[str, Any],
) -> None:
    """§3.5 — 조각 중간 탭 바뀜(p) → 틀 주소를 그 탭으로 바꾸고 준비를 기다리는 동안 시계를 멈춘 뒤 그 시각부터. r 사건이면 틀 크기도."""
    reload, held, gap = flow["reload"], flow["held"], flow["gap"]
    assert reload["src"] == f"{SITE}/app/?tab=gap&kt-replay=1"
    assert reload["frameNote"] == "페이지를 띄우는 중…" and reload["shade"] is False
    assert held["time"] == reload["time"] and held["posted"] == reload["posted"]
    assert gap["frameNote"] is None and gap["size"] == ["1280", "800"]
    assert gap["stage"]["--fw"] == "1280px"
    assert gap["last"]["els"] == [] and gap["last"]["sy"] == 800
    assert (gap["last"]["x"], gap["last"]["y"]) == (50, 60)
    assert gap["time"].endswith("경과 0:30 / 전체 0:39")


def test_unready_frame_shows_one_line_after_five_seconds(flow: dict[str, Any]) -> None:
    """§3.3 — 틀이 5초 안에 준비를 알리지 않으면 '페이지를 띄우지 못했다(공개 사이트가 관리자 출처를 허용하는지)' 한 줄."""
    assert flow["back"]["src"] == f"{SITE}/app/?tab=spread&kt-replay=1"
    assert flow["back"]["frameNote"] == "페이지를 띄우는 중…"
    assert (
        flow["silent"]["frameNote"]
        == "페이지를 띄우지 못했다(공개 사이트가 관리자 출처를 허용하는지)"
    )


def test_event_list_bar_and_controls_seek(flow: dict[str, Any]) -> None:
    """§3.3 — 사건 목록 줄을 누르면 그 시각(그 줄이 지금 줄), 시간 막대를 누르거나 끌면 그 자리(손을 뗀 뒤 움직임은 무시),
    속도 1×·2×·4×·8× 하나만 눌림, 빈 시간 건너뛰기 기본 켬 → 끔. 목록은 움직임 없이 시각 · 종류 · 영역 이름."""
    assert (
        flow["row"]["time"].endswith("경과 0:00 / 전체 0:39")
        and flow["row"]["current"] == 0
    )
    assert flow["bar"]["time"].endswith("경과 0:29 / 전체 0:39")
    assert flow["bar"]["fill"] == "75%"
    assert flow["load"]["speed"] == ["1×"] and flow["load"]["skip"] == "true"
    assert flow["controls"]["speed"] == ["4×"] and flow["controls"]["skip"] == "false"
    assert flow["list"] == [
        ["02:47:21", "누름", "필터 바"],
        ["02:47:21", "스크롤", "아래로 800px"],
        ["02:47:22", "보던 영역", "김프 표"],
        ["02:47:22", "영역 안 스크롤", "김프 표 · 아래로 400px"],
        ["02:47:49", "숨김", "탭을 숨김"],
        ["02:47:50", "보임", "탭이 다시 보임"],
        ["02:47:50", "화면 바뀜", "대시보드 갭"],
        ["02:47:50", "누름", "탭 단추"],
        ["02:48:00", "보던 영역", "갭 표"],
    ]


def test_closing_the_sheet_blanks_the_frame_and_stops_the_clock(
    flow: dict[str, Any],
) -> None:
    """§3.3 — 판을 닫으면 틀을 about:blank 로(대시보드 실시간 연결을 닫는다), 재생·받기 주기를 멈춘다."""
    got = flow["closed"]
    assert got["open"] is False and got["src"] == "about:blank"
    assert got["ticks"] == 0 and got["polls"] == 0
