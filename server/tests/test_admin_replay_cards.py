"""관리자 방문자 페이지의 화면 기록 카드 둘과 IP 판 절(스펙 074 §3.1·§3.2·§4 node) — 지금 보는 중(행 모양·빈 문구·5초 묶음은 보일 때만·
방문자 페이지에만), 화면 기록(행 모양·머리 정렬·IP·화면 찾기·빈 문구·행 → 재생 판), IP 상세 판의 '화면 기록' 절('어디' 아래·[재생]·빈 문구·
가린 대역은 절도 부르기도 없음), XSS(IP·화면 이름은 글로만). 069·073 피드 응답은 손으로 만든다.
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_addr_sheet import detail
from tests.test_admin_addrs import BUTTONS, LIST

NOW = 1_792_000_100_000
X = "<img src=x onerror=alert(1)>"
LIVE = "/svc/api/admin/replay/live"
SESSIONS = "/svc/api/admin/replay/sessions"
ADDR = "/svc/api/admin/access/addr?ip={}&window=24h"


def row(sid: str, ip: str, first: int, last: int, **extra: Any) -> dict[str, Any]:
    base = {
        "sid": sid,
        "ip": ip,
        "dev": "pc",
        "page": "landing",
        "pages": ["landing"],
        "now": "landing",
        "first": first,
        "last": last,
        "n": 3,
        "clicks": 1,
    }
    return {**base, **extra}


LIVE_ROWS = [
    row(
        "a1b2c3d4e5f60718",
        "203.0.113.7",
        NOW - 125_000,
        NOW - 4_000,
        dev="mobile",
        page="app-spread",
        pages=["app-spread", "app-gap"],
        now="app-gap",
        consent="unset",
        consentFirst="unset",
    ),
    row("0000000000000bad"[:15] + "z", X, NOW - 9_000, NOW - 8_000, now=X, consent=X),
]
SESSION_ROWS = [
    row(
        "c0ffee0000000001",
        "66.249.66.1",
        NOW - 3_600_000,
        NOW - 3_505_000,
        clicks=4,
        consent="denied",
        consentFirst="denied",
    ),
    row(
        "c0ffee0000000002",
        "203.0.113.7",
        NOW - 7_200_000,
        NOW - 7_120_000,
        dev="mobile",
        page="app-spread",
        pages=["app-spread", "app-history", "app-gap"],
        clicks=2,
        consent="granted",
        consentFirst="unset",
    ),
    # 동의 키가 없는 행 — 동의한 방문만 기록하던 때의 조각
    row("c0ffee0000000003", X, NOW - 9_000_000, NOW - 8_700_000, pages=[X], clicks=0),
]
HARNESS = (
    BUTTONS.replace("const respond", "const respond0")
    + r"""
const respond = (url) => {
  const [path] = url.split('?')
  if (input.answers[url]) return input.answers[url]
  if (path === '/svc/api/admin/access/addrs') return [200, input.addrs]
  return [404, { detail: 'Not Found' }]
}
const v = setup({ respond, search: input.search || '', hash: input.hash || '', visible: input.visible ?? true, buttons: BUTTONS })
document.getElementById('sheet').hidden = true
const timers = []
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length }
globalThis.clearTimeout = (id) => { if (id && timers[id - 1]) timers[id - 1].fn = null }
Date.now = () => input.now // 묶음 다음 시각이 정확히 5초·60초 뒤가 되게
const fire = async (ms) => { for (const t of timers.filter((x) => x.fn && x.ms === ms)) { const f = t.fn; t.fn = null; f() } await flush() }
await page(input.page || 'visitors.js')
await flush()
const caption = (id) => (document.getElementById(id).kids[0]?.tag === 'caption' ? v.text(id) : null)
const replayCalls = () => v.s.calls.filter((c) => c.includes('/replay/'))
"""
)


def _run(steps: str, answers: dict[str, list] | None = None, **payload: Any) -> Any:
    base = {
        LIVE: [200, {"state": "ok", "code": None, "now": NOW, "rows": LIVE_ROWS}],
        SESSIONS: [
            200,
            {"state": "ok", "code": None, "ip": None, "rows": SESSION_ROWS},
        ],
    }
    return run_admin(
        HARNESS + steps,
        {"addrs": LIST, "answers": {**base, **(answers or {})}, "now": NOW, **payload},
    )


CARDS = r"""
out.first = { calls: replayCalls(), live: cells('tbl-rplive'), sessions: cells('tbl-replay'), count: v.text('replay-count'), tags: [v.text('t-rplive'), v.text('t-replay')], liveTimers: timers.filter((t) => t.fn && t.ms === 5000).length }
// 5초 묶음 — 지금 보는 중만 다시
await fire(5000)
out.again = replayCalls().slice(2)
// 머리 '길이' 눌러 정렬(큰 순 → 작은 순), 찾기
const head = () => document.getElementById('tbl-replay').kids[0].kids[0].kids
head()[5].kids[0].fire('click')
out.sorted = cells('tbl-replay').map((r) => r[5])
head()[5].kids[0].fire('click')
out.sortedUp = cells('tbl-replay').map((r) => r[5])
const q = document.getElementById('rp-q')
q.fire('input', { target: { value: '기록' } })
out.found = { rows: cells('tbl-replay').map((r) => r[1]), count: v.text('replay-count') }
// 동의 칸으로 찾기 — '거부' 는 미동의(거부함) 조각 하나
q.fire('input', { target: { value: '거부' } })
out.foundConsent = { rows: cells('tbl-replay').map((r) => r[1]), count: v.text('replay-count') }
q.fire('input', { target: { value: '' } })
// 행 → 재생 판(따라 보기 아님)
document.getElementById('tbl-replay').kids[1].kids[0].fire('click')
await flush()
out.opened = { pushed: v.s.pushed, title: v.text('sheet-title'), tag: v.text('t-sheet'), calls: replayCalls().filter((c) => c.includes('/session?')) }
// sid 꼴이 아닌 지금 보는 중 행은 열지 않는다
document.getElementById('sheet-close').fire('click')
await flush()
document.getElementById('tbl-rplive').kids[1].kids[1].fire('click')
await flush()
out.bad = { pushed: v.s.pushed.length, open: !document.getElementById('sheet').hidden }
const tagsOf = (n) => (typeof n === 'string' ? [] : [n.tag, ...n.kids.flatMap(tagsOf)])
out.tags = [...new Set(['tbl-rplive', 'tbl-replay'].flatMap((id) => tagsOf(document.getElementById(id))))]
"""


@pytest.fixture(scope="module")
def cards() -> dict[str, Any]:
    return _run(CARDS)


def test_live_card_rows_and_five_second_cadence(cards: dict[str, Any]) -> None:
    """§3.1 지금 보는 중 — 행 = IP · 동의 · 지금 화면 · 기기 · 머문 시간(last − first) · 마지막 움직임 'n초 전'(서버 now 기준). 처음 한 번 부르고
    5초 묶음(지금 묶음 — 이 카드 하나)으로 다시 부른다. 화면 기록은 느린 묶음."""
    first = cards["first"]
    assert first["calls"] == [SESSIONS, LIVE]
    assert first["liveTimers"] == 1
    assert first["live"] == [
        ["203.0.113.7", "미동의(답 안 함)", "대시보드 갭", "휴대폰", "2분", "4초 전"],
        [X, "미동의(답 안 함)", X, "PC", "1초", "8초 전"],
    ]
    assert first["tags"] == ["", ""]
    assert cards["again"] == [LIVE]


def test_sessions_card_rows_sort_find_and_open(cards: dict[str, Any]) -> None:
    """§3.1 화면 기록 — 최근 조각 표(시작 KST · IP · 동의 · 지나간 화면들 · 기기 · 길이 · 누름 수), 머리 눌러 정렬, IP·화면·동의 찾기, 행 → 재생 판
    (`#detail=replay:<sid>` — 따라 보기 아님)."""
    first = cards["first"]
    assert first["sessions"] == [
        ["10-15 01:48:20", "66.249.66.1", "미동의(거부함)", "랜딩", "PC", "1분", "4"],
        [
            "10-15 00:48:20",
            "203.0.113.7",
            "미동의(답 안 함) → 동의",
            "대시보드 스프레드 → 대시보드 기록 → 대시보드 갭",
            "휴대폰",
            "1분",
            "2",
        ],
        ["10-15 00:18:20", X, "동의", X, "PC", "5분", "0"],
    ]
    assert first["count"] == "3 / 3개 조각"
    assert cards["sorted"] == ["5분", "1분", "1분"] and cards["sortedUp"] == [
        "1분",
        "1분",
        "5분",
    ]
    assert cards["found"] == {"rows": ["203.0.113.7"], "count": "1 / 3개 조각"}
    assert cards["foundConsent"] == {"rows": ["66.249.66.1"], "count": "1 / 3개 조각"}
    opened = cards["opened"]
    # 길이 작은 순으로 정렬된 표의 첫 행(80초 — 대시보드 휴대폰 조각)
    assert opened["pushed"] == ["/visitors.html#detail=replay%3Ac0ffee0000000002"]
    assert opened["title"] == "화면 재생" and opened["tag"] != "● 따라 보는 중"
    assert opened["calls"] == [
        "/svc/api/admin/replay/session?sid=c0ffee0000000002&after=0"
    ]
    assert cards["bad"] == {"pushed": 1, "open": False}


def test_card_text_is_text_only(cards: dict[str, Any]) -> None:
    """§4 XSS — IP·화면 이름의 HTML 조각은 표에 글자 그대로(그림·스크립트 요소 없음)."""
    assert not {"img", "script", "iframe"} & set(cards["tags"])


EMPTY = r"""
out.empty = { live: caption('tbl-rplive'), sessions: caption('tbl-replay'), tags: [v.text('t-rplive'), v.text('t-replay')] }
"""


@pytest.mark.parametrize(
    ("live", "sessions", "want"),
    [
        (
            [200, {"state": "ok", "code": None, "now": NOW, "rows": []}],
            [200, {"state": "ok", "code": None, "ip": None, "rows": []}],
            {
                "live": "지금 화면을 움직이는 방문 없음",
                "sessions": "화면 기록 없음 — 모든 방문을 기록하고 서비스 종료까지 보관한다",
                "tags": ["", ""],
            },
        ),
        (
            [200, {"state": "error", "code": "redis", "now": NOW, "rows": []}],
            [404, {"detail": "Not Found"}],
            {
                "live": "불러오지 못함",
                "sessions": "연결 안 됨",
                "tags": ["불러오지 못함", "연결 안 됨"],
            },
        ),
    ],
)
def test_empty_and_unavailable_cards(live: list, sessions: list, want: dict) -> None:
    """§3.1 — 조각이 없으면 빈 문구(모든 방문을 기록), Redis 오류·피드 배포 전(404)은 카드 상태 배지와 같은 글."""
    got = _run(EMPTY, {LIVE: live, SESSIONS: sessions})
    assert got["empty"] == want


HIDDEN = r"""
await fire(5000)
out.hidden = { calls: replayCalls(), live: timers.filter((t) => t.fn && t.ms === 5000).length }
"""


def test_live_cadence_only_while_visible_and_only_on_the_visitors_page() -> None:
    """§3.1 — 5초 묶음은 페이지가 보일 때만(숨은 채 뜨면 부르지 않는다), 다른 페이지에는 없다."""
    got = _run(HIDDEN, visible=False)
    assert got["hidden"] == {"calls": [], "live": 0}
    server = _run(HIDDEN, page="server.js")
    assert server["hidden"] == {"calls": [], "live": 0}


SHEET = r"""
const rows = () => document.getElementById('tbl-addrs').kids[1].kids
rows()[input.row].fire('click')
await flush()
const sec = v.byId.get('addr-replay')
const kids = document.getElementById('sheet-custom').kids
out.sheet = {
  panels: kids.map((k) => k.id || k.kids[0]?.className),
  head: sec && sec.parent ? sec.kids[0].textContent : null,
  rows: sec && sec.parent ? (sec.kids[1]?.kids?.[0]?.kids?.[1]?.kids || []).map((tr) => tr.kids.map((td) => td.textContent)) : null,
  empty: sec && sec.parent ? sec.kids.find((k) => k.className === 'empty')?.textContent ?? null : null,
  calls: replayCalls(),
}
if (sec && sec.parent && out.sheet.rows?.length) {
  sec.kids[1].kids[0].kids[1].kids[0].fire('click')
  await flush()
  out.replay = { pushed: v.s.pushed.at(-1), title: v.text('sheet-title') }
}
"""


def _sheet(row_index: int, ip: str, sessions: list | None) -> Any:
    extra = {ADDR.format(ip.replace("/", "%2F")): [200, detail(ip)]}
    if sessions is not None:
        extra[f"{SESSIONS}?ip={ip}"] = sessions
    return _run(SHEET, extra, row=row_index)


def test_ip_sheet_has_a_replay_section_below_where() -> None:
    """§3.2 — IP 판의 '어디' 아래 그 IP 의 조각(sessions?ip=) 표(시작 · 동의 · 화면 · 기기 · 길이 · 누름 · [재생]), 머리에 동의·미동의 수, 행 → 재생 판."""
    rows = [r for r in SESSION_ROWS if r["ip"] == "66.249.66.1"]
    got = _sheet(
        1,
        "66.249.66.1",
        [200, {"state": "ok", "code": None, "ip": "66.249.66.1", "rows": rows}],
    )
    sheet = got["sheet"]
    assert sheet["panels"] == ["addr-verdict", "addr-where", "addr-replay", "grid2"]
    assert (
        sheet["head"] == "화면 기록1개 조각 · 동의 0 · 미동의 1 · 서비스 종료까지 보관"
    )
    assert sheet["rows"] == [
        ["10-15 01:48:20", "미동의(거부함)", "랜딩", "PC", "1분", "4", "재생 ▶"]
    ]
    assert sheet["calls"][-1] == f"{SESSIONS}?ip=66.249.66.1"
    assert got["replay"] == {
        "pushed": "/visitors.html#detail=replay%3Ac0ffee0000000001",
        "title": "화면 재생",
    }


def test_ip_sheet_section_says_when_there_is_nothing() -> None:
    """§3.2 — 조각이 없으면 '이 IP 의 화면 기록 없음'."""
    got = _sheet(
        1,
        "66.249.66.1",
        [200, {"state": "ok", "code": None, "ip": "66.249.66.1", "rows": []}],
    )
    assert got["sheet"]["empty"] == "이 IP 의 화면 기록 없음"


def test_masked_band_rows_have_no_replay_section() -> None:
    """§3.2 — 가린 대역 행(전체 저장 전)에는 절을 두지 않고 sessions?ip= 도 부르지 않는다."""
    got = _sheet(2, "198.51.100.0/24", None)
    assert got["sheet"]["panels"] == ["addr-verdict", "addr-where", "grid2"]
    assert not [c for c in got["sheet"]["calls"] if "?ip=" in c]
