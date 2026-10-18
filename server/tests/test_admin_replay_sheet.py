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
    // 078 — 14일이 지나 S3 로 옮긴 조각: 머리만 있고 묶음은 없다
    if (input.archived) return [200, { state: 'ok', code: null, meta: S.meta, batches: [], next: after, more: false, archived: true }]
    const all = S.batches.slice(0, shown)
    const part = all.slice(after, after + input.page)
    return [200, { state: 'ok', code: null, meta: S.meta, batches: part, next: after + part.length, more: after + part.length < all.length, archived: false }]
  }
  if (path === '/api/admin/replay/archive') {
    if (input.archiveStatus) return input.archiveStatus
    const after = Number(q.get('after') || 0)
    const part = S.batches.slice(after, after + input.page)
    return [200, { state: 'ok', code: null, meta: S.meta, batches: part, next: after + part.length, more: after + part.length < S.batches.length, archived: true }]
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
    """§3.3 — 판 머리 '화면 재생' · 'IP · 기기 · 시작 KST · 동의', 073 session 을 after=next 로 more 가 끝날 때까지, 틀 = 공개 사이트 그 화면 +
    kt-replay=1(대시보드 /app/?tab=), 크기 = 조각의 화면 크기(w×h), 칸 맞춤 변수(늘리지 않음 — 창 높이 900 에서 몫 640/900)."""
    got = flow["load"]
    assert got["open"] and got["title"] == "화면 재생" and got["custom"] is False
    # 동의 키가 없는 머리 = 동의한 방문만 기록하던 때의 조각
    assert got["unit"] == "203.0.113.7 · PC · 2026-10-15 02:47:22 KST · 동의"
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


EDGE = r"""
out.edge = snap()
out.calls = sessionCalls()
"""


@pytest.mark.parametrize(
    ("status", "note", "tag"),
    [
        (
            [
                200,
                {
                    "state": "ok",
                    "code": None,
                    "meta": None,
                    "batches": [],
                    "next": 0,
                    "more": False,
                },
            ],
            "기록이 없거나 지났다",
            "",
        ),
        (
            [400, {"error": {"code": "bad_sid", "message": "x", "detail": None}}],
            "이 조각은 볼 수 없다 — 조각 id 꼴이 아니다(bad_sid)",
            "불러오지 못함",
        ),
        (
            [404, {"detail": "Not Found"}],
            "이 서버에는 화면 기록 피드가 아직 없다",
            "연결 안 됨",
        ),
        ([200, {"state": "error", "code": "redis"}], "", "불러오지 못함"),
    ],
)
def test_missing_bad_absent_and_broken_sessions_are_one_line(
    status: list, note: str, tag: str
) -> None:
    """§3.5 — 없음·만료(meta null) → '기록이 없거나 지났다', 400 bad_sid · 404(피드 배포 전) · Redis 오류는 판 상태 배지와 한 줄.
    틀은 띄우지 않는다."""
    got = _run(EDGE, status=status)
    edge = got["edge"]
    assert edge["note"].endswith(note) and edge["tag"] == tag
    assert edge["custom"] is True and edge["src"] is None
    assert len(got["calls"]) == 1


ARCHIVED = r"""
out.edge = snap()
out.calls = v.s.calls.filter((c) => c.includes('/replay/session?') || c.includes('/replay/archive?'))
"""


def test_an_archived_session_reloads_from_the_archive_feed() -> None:
    """078 §3.5 — session 이 archived(머리만·묶음 없음)면 같은 조각을 수집기 archive 로 처음부터 다시 받아 그대로 재생한다."""
    got = _run(ARCHIVED, archived=True)
    assert got["calls"] == [
        f"/svc/api/admin/replay/session?sid={SID}&after=0",
        f"/api/admin/replay/archive?sid={SID}&after=0",
        f"/api/admin/replay/archive?sid={SID}&after=2",
        f"/api/admin/replay/archive?sid={SID}&after=4",
    ]
    edge = got["edge"]
    assert edge["tag"] == "" and edge["src"] == f"{SITE}/app/?tab=spread&kt-replay=1"
    assert edge["time"] == "02:47:20 KST · 경과 0:00 / 전체 0:39"


def test_archive_feed_missing_on_the_server_is_one_line() -> None:
    """078 §3.5 — 수집기에 archive 가 아직 없으면(404) '연결 안 됨' 한 줄, 틀은 띄우지 않는다."""
    got = _run(ARCHIVED, archived=True, archiveStatus=[404, {"detail": "Not Found"}])
    edge = got["edge"]
    assert edge["note"].endswith("이 서버에는 화면 기록 피드가 아직 없다")
    assert edge["tag"] == "연결 안 됨" and edge["src"] is None


def test_a_cut_session_gets_the_front_only_badge() -> None:
    """§3.5 — 묶음 1MB·3,600개 상한에 닿은 조각은 머리에 '앞부분만' 배지."""
    got = _run(EDGE, session=_session(S0, n=3600))["edge"]
    assert got["tag"] == "앞부분만" and got["tagCls"] == "tag warn"


FOLLOW = r"""
// 지금 보는 중의 행 → 따라 보기(마지막 사건 − 3초 = 대시보드 스프레드 화면)
document.getElementById('tbl-rplive').kids[1].kids[0].fire('click')
await flush()
attach()
ready('app-spread')
out.open = { ...snap(), pushed: v.s.pushed, calls: sessionCalls(), polls: pending(2000) }
// 2초 — after=next, 새 묶음 없음
await tick(10)
await fire(2000)
out.none = { ...snap(), calls: sessionCalls().slice(-1), polls: pending(2000) }
// 새 묶음 둘 — 다시 펴고 8초 넘게 뒤처졌으니 마지막 사건 − 3초로(갭 화면 — 틀을 다시 띄움), 1× 로 따라간다
shown = 5
await fire(2000)
ready('app-gap')
await tick(20)
out.more = { ...snap(), calls: sessionCalls().slice(-1), rows: list().length }
// 1× — 마지막 사건을 넘지 않는다
await tick(200)
out.caught = snap()
// 30초 동안 새 묶음 없음 → 멈춤
clock += 31_000
await fire(2000)
out.gone = { ...snap(), polls: pending(2000) }
"""


def test_follow_mode_polls_every_two_seconds_and_stops_after_thirty() -> None:
    """§3.3 따라 보기 — 지금 보는 중에서 연 판은 2초마다 session?after=next 로 새 묶음을 받아 1× 로, 마지막 사건에서 3초 뒤를 따라간다.
    머리 '● 따라 보는 중 · n초 늦음'(마지막 묶음을 서버가 받은 시각 + 그 뒤 흐른 시간 − 재생 시각). 30초 동안 새 묶음이 없으면
    '방문자가 떠났거나 멈춤' 으로 바꾸고 받기를 멈춘다."""
    got = _run(FOLLOW, session=_session(NOW - 45_000), shown=3, hash="")
    first = got["open"]
    assert first["pushed"] == [f"/visitors.html#detail=replay%3A{SID}"]
    assert first["calls"] == [
        f"/svc/api/admin/replay/session?sid={SID}&after=0",
        f"/svc/api/admin/replay/session?sid={SID}&after=2",
    ]
    assert first["tag"] == "● 따라 보는 중 · 3초 늦음" and first["tagCls"] == "tag live"
    assert first["time"].endswith("경과 0:26 / 전체 0:29") and first["polls"] == 1
    assert got["none"]["calls"] == [f"/svc/api/admin/replay/session?sid={SID}&after=3"]
    assert (
        got["none"]["tag"] == "● 따라 보는 중 · 3초 늦음" and got["none"]["polls"] == 1
    )
    more = got["more"]
    assert more["calls"] == [f"/svc/api/admin/replay/session?sid={SID}&after=3"]
    assert more["src"] == f"{SITE}/app/?tab=gap&kt-replay=1"
    assert more["rows"] == 9 and more["time"].endswith("경과 0:37 / 전체 0:39")
    assert more["tag"] == "● 따라 보는 중 · 3초 늦음"
    caught = got["caught"]
    assert caught["time"].endswith("경과 0:39 / 전체 0:39") and caught["play"] == "❚❚"
    gone = got["gone"]
    assert gone["tag"] == "방문자가 떠났거나 멈춤" and gone["polls"] == 0


DRAG = r"""
document.getElementById('tbl-rplive').kids[1].kids[0].fire('click')
await flush()
attach()
ready('app-gap')
out.before = { tag: v.text('t-sheet'), polls: pending(2000) }
const bar = v.byId.get('rp-bar')
bar._width = 1000
bar.fire('pointerdown', { clientX: 100, pointerId: 1 })
out.after = { ...snap(), polls: pending(2000) }
"""


def test_dragging_back_leaves_follow_mode() -> None:
    """§3.3 — 시간 막대를 뒤로 끌면 따라 보기를 끄고(받기 멈춤) 받은 만큼 재생으로."""
    got = _run(DRAG, session=_session(NOW - 41_000), hash="")
    assert (
        got["before"]["tag"].startswith("● 따라 보는 중")
        and got["before"]["polls"] == 1
    )
    after = got["after"]
    assert after["tag"] == "" and after["polls"] == 0
    assert after["time"].endswith("경과 0:03 / 전체 0:39")


XSS = r"""
out.x = snap()
out.text = document.getElementById('sheet-custom').textContent + v.text('sheet-unit')
const tagsOf = (n) => (typeof n === 'string' ? [] : [n.tag, ...n.kids.flatMap(tagsOf)])
out.tags = [...new Set(tagsOf(document.getElementById('sheet-custom')))]
"""


def test_page_and_area_names_are_text_and_unknown_screens_are_not_framed() -> None:
    """§4 XSS — 화면·영역 이름이 HTML 조각이면 사건 목록·머리에 글자 그대로(그림·스크립트 요소 없음), 화면 이름이 목록 밖이면 틀은
    about:blank 에 '이 화면은 틀로 띄울 수 없다'."""
    session = _session(S0, ip=X, page=X)
    for b in session["batches"]:
        b["ev"] = [
            [*e[:4], X]
            if e[1] == "c"
            else [e[0], e[1], X, *e[3:]]
            if e[1] in ("a", "e", "p")
            else e
            for e in b["ev"]
        ]
    got = _run(XSS, session=session)
    assert got["x"]["src"] == "about:blank"
    assert got["x"]["frameNote"] == "이 화면은 틀로 띄울 수 없다"
    assert got["text"].count(X) >= 5
    assert not {"img", "script", "object", "embed"} & set(got["tags"])
