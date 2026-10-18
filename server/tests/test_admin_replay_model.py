"""관리자 화면 재생의 값 계산(스펙 074 §3.3·§3.5·§4 node) — 서버 시계 맞추기(at − 마지막 t + t)·사건 꼴 검사, 빈 시간 건너뛰기·속도,
한 시각의 틀 상태(커서·누름 한 번·숨김·손가락·스크롤·영역 안 스크롤·보던 영역·화면·화면 크기), 시간 막대 표시, 사건 목록(움직임 빼고·
스크롤 크게·이름표), 틀 주소, 따라 보기(3초 뒤·늦음), '앞부분만', 카드 행·XSS(글자는 그대로 글로). `web/admin/replaymodel.js` 를 node 로 부른다.
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin

X = "<img src=x onerror=alert(1)>"
T0 = 1_792_000_000_000  # 2026-10-14T17:46:40Z = 10-15 02:46:40 KST
BATCHES = [
    # 묶음 at(서버가 받은 시각) − 그 묶음 마지막 t + 사건 t — 방문자 시계가 서버보다 앞서도 서버 시각에 맞는다
    {
        "at": T0 + 2_000,
        "seq": 0,
        "ev": [
            [0, "m", 100, 100],
            [500, "c", 110, 120, "filters"],
            [900, "s", 300],
            [1_000, "s", 800],
            [1_500, "a", "table"],
            [1_600, "x", 1, 2],  # 모르는 종류 — 버림
            [1_700, "m", 5],  # 자리 수 틀림 — 버림
        ],
    },
    {
        "at": T0 + 4_100,
        "seq": 1,
        "ev": [
            [2_000, "m", 300, 300],
            [2_100, "e", "table", 0, 400],
            [4_000, "m", 99_999, 320],
        ],
    },
    # 26초 빈 틈 뒤 — 숨김 1초, 화면 바뀜(대시보드 갭), 누름, 화면 크기 바뀜
    {"at": T0 + 30_000, "seq": 2, "ev": [[28_000, "v", 0], [29_000, "v", 1]]},
    {
        "at": T0 + 32_000,
        "seq": 3,
        "ev": [
            [30_000, "p", "app-gap"],
            [30_500, "c", 50, 60, "tabs"],
            [31_000, "r", 1280, 800],
            [32_000, "e", "table", 1, 700],
        ],
    },
    {"at": None, "seq": 4, "ev": [[0, "m", 1, 1]]},  # at 없음 — 버림
]
META = {
    "sid": "0123456789abcdef",
    "dev": "pc",
    "page": "app-spread",
    "w": 1440,
    "h": 900,
}

SCENARIO = r"""
setup({ visible: false, respond: () => [404, {}] })
const m = await page('replaymodel.js')
const tl = m.timeline(input.meta, input.batches)
const at = (T) => m.frameState(tl, T)
out.events = tl.events.map((e) => [e.at - input.t0, e.k])
out.span = [tl.start - input.t0, tl.end - input.t0, tl.base, tl.chunks.map((c) => [c.from === -Infinity ? null : c.from - input.t0, c.page])]
out.gaps = tl.gaps.map((g) => [g.a - input.t0, g.b - input.t0])
out.hides = tl.hides.map(([a, b]) => [a - input.t0, b - input.t0])
// 재생 시계 — 빈 시간(26초 틈)은 1초에, 끄면 그대로, 끝에서 멈춤
out.advance = [
  m.advance(tl, tl.start, 1_000, true) - input.t0,
  m.advance(tl, input.t0 + 3_000, 1_500, true) - input.t0,
  m.advance(tl, input.t0 + 3_000, 1_500, false) - input.t0,
  m.advance(tl, input.t0 + 10_000, 500, true) - input.t0,
  m.advance(tl, tl.start, 1e9, true) - input.t0,
]
// 틀 상태
out.state = {
  first: at(input.t0 + 500),
  click: m.frameState(tl, input.t0 + 1_100, input.t0 + 900).message,
  seek: m.frameState(tl, input.t0 + 1_100, null).message.click,
  again: m.frameState(tl, input.t0 + 1_200, input.t0 + 1_100).message.click,
  hidden: at(input.t0 + 29_500),
  gap: at(input.t0 + 31_900),
  end: at(input.t0 + 32_000),
}
const touch = m.timeline({ ...input.meta, dev: 'mobile', w: undefined }, input.batches)
out.touch = [m.frameState(touch, input.t0 + 2_150).message, m.frameState(touch, input.t0 + 3_500).message.x, touch.base]
out.marks = m.barMarks(tl)
out.rows = m.eventRows(tl).map((r) => [r.at - input.t0, r.kind, r.name, r.detail, r.time])
out.rowAt = [m.rowAt(m.eventRows(tl), input.t0), m.rowAt(m.eventRows(tl), input.t0 + 1_400)]
out.paths = ['app-gap', 'app-flow', 'landing', 'privacy', 'kimp-chart', 'kimp-history', 'app-nope', input.x, null].map(m.framePath)
out.text = [m.timeText(tl, input.t0 + 65_000), m.mss(3_725_000), m.clockText(null)]
out.follow = [m.followTarget(tl, null) - input.t0, m.followTarget(tl, input.t0 + 25_000) - input.t0, m.followTarget(tl, input.t0 + 10_000) - input.t0, m.lagSec(input.t0 + 32_000, 1_400, input.t0 + 29_000)]
out.cut = [m.truncated({ n: 3600 }), m.truncated({ bytes: 1_040_000 }), m.truncated({ cut: true }), m.truncated({ n: 12, bytes: 4000 }), m.truncated(null)]
const rows = [
  { sid: '0123456789abcdef', ip: '203.0.113.7', dev: 'pc', page: 'app-spread', pages: ['app-spread', 'app-gap'], now: 'app-gap', first: input.t0, last: input.t0 + 95_000, n: 9, clicks: 3, consent: 'granted', consentFirst: 'unset' },
  { sid: 'nope', ip: input.x, dev: input.x, page: input.x, pages: `landing,${input.x}`, now: null, first: null, last: null, clicks: null, consent: input.x },
]
out.consent = [{}, { consent: 'granted' }, { consent: 'denied' }, { consent: 'later' }, { consent: 'unset' }, { consent: 'gpc' }, { consent: 'gpc', consentFirst: 'granted' }, { consent: 'denied', consentFirst: null }].map(m.consentText)
out.live = m.liveRows(rows, input.t0 + 101_000)
out.sessions = m.sessionRows(rows)
out.reqs = [m.liveReq().url, m.sessionsReq().url, m.sessionsReq('2001:db8::5').url, m.sessionReq('0123456789abcdef', 600).url, m.liveReq().live, m.sessionsReq().live]
const xss = m.timeline({ dev: 'pc', page: input.x }, [{ at: input.t0, seq: 0, ev: [[0, 'c', 1, 2, input.x], [10, 'a', input.x], [20, 'p', input.x], [30, 'e', input.x, 0, 900]] }])
out.xss = { rows: m.eventRows(xss).map((r) => r.detail), state: m.frameState(xss, input.t0 + 30).message, page: m.frameState(xss, input.t0).page }
"""


@pytest.fixture(scope="module")
def got() -> dict[str, Any]:
    return run_admin(SCENARIO, {"meta": META, "batches": BATCHES, "t0": T0, "x": X})


def test_events_are_aligned_to_server_time_and_bad_ones_dropped(
    got: dict[str, Any],
) -> None:
    """§3.3 — 사건 시각 = 묶음 at − 그 묶음 마지막 t + 사건 t(묶음 둘째는 at 이 100ms 늦어 그만큼 민다). 모르는 종류·자리 수 틀림·at 없는
    묶음은 버리고, 좌표는 0~20,000 으로 자른다. 화면 조각은 첫 화면(meta.page) + p 마다."""
    assert got["events"] == [
        [500, "m"],
        [1_000, "c"],
        [1_400, "s"],
        [1_500, "s"],
        [2_000, "a"],
        [2_100, "m"],
        [2_200, "e"],
        [4_100, "m"],
        [29_000, "v"],
        [30_000, "v"],
        [30_000, "p"],
        [30_500, "c"],
        [31_000, "r"],
        [32_000, "e"],
    ]
    assert got["span"] == [
        500,
        32_000,
        [1440, 900],
        [[None, "app-spread"], [30_000, "app-gap"]],
    ]
    assert got["gaps"] == [[4_100, 29_000]]
    assert got["hides"] == [[29_000, 30_000]]
    assert got["state"]["end"]["message"]["x"] == 50


def test_idle_skip_turns_long_gaps_into_one_second(got: dict[str, Any]) -> None:
    """§3.3 '빈 시간 건너뛰기' — 사건 사이 5초 넘는 틈(4.1초→29초)은 재생 1초에 지나간다. 끄면 흐른 만큼, 끝에서 멈춘다."""
    start, mid, off, inside, end = got["advance"]
    assert start == 1_500
    # 3.0초에서 1.5초 — 4.1초(틈 시작)까지 1.1초 + 틈 안 0.4초 × 24.9 = 9.96초 더
    assert mid == pytest.approx(4_100 + 0.4 * 24_900)
    assert off == 4_500
    # 틈 안(10초)에서 0.5초 — 틈 1초에 24.9초가 지나므로 12.45초
    assert inside == pytest.approx(10_000 + 0.5 * 24_900)
    assert end == 32_000


def test_frame_state_is_the_message_for_one_moment(got: dict[str, Any]) -> None:
    """§3.4 메시지 — 커서는 마지막 포인터(누름 포함), 누름은 재생으로 지난 구간에 있을 때 한 번만(되감기는 아님), 숨김 구간은 커서 null,
    스크롤은 마지막 s(없으면 0), 영역 안 스크롤은 지금 화면 조각에서 본 짝(그 전이면 0), 보던 영역은 지금 화면 조각의 마지막 a(id 꼴만)."""
    first = got["state"]["first"]
    assert first["page"] == "app-spread" and first["view"] == [1440, 900]
    assert first["message"] == {
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
    assert got["state"]["click"] == {
        "type": "kt-replay",
        "v": 1,
        "x": 110,
        "y": 120,
        "touch": False,
        "click": True,
        "sy": 0,
        "els": [["table", 0, 0]],
        "area": "",
    }
    assert got["state"]["seek"] is False and got["state"]["again"] is False
    hidden = got["state"]["hidden"]
    assert hidden["hidden"] is True and hidden["message"]["x"] is None
    assert hidden["message"]["sy"] == 800 and hidden["message"]["area"] == "table"
    assert hidden["message"]["els"] == [["table", 0, 400]]
    # 화면 바뀜 뒤 — 새 조각은 영역 안 스크롤·보던 영역을 비우고(그 화면에서 본 짝만), 화면 크기는 r 사건 값, 스크롤은 문서 그대로
    gap = got["state"]["gap"]
    assert gap["page"] == "app-gap" and gap["view"] == [1280, 800]
    assert gap["message"]["els"] == [["table", 1, 0]] and gap["message"]["area"] == ""
    assert gap["message"]["sy"] == 800 and (
        gap["message"]["x"],
        gap["message"]["y"],
    ) == (50, 60)
    assert got["state"]["end"]["message"]["els"] == [["table", 1, 700]]


def test_touch_devices_show_a_finger_only_briefly(got: dict[str, Any]) -> None:
    """§3.4 — 손가락 기기(dev mobile)는 touch 참, 마지막 움직임 뒤 1초가 지나면 커서 null(손을 뗀 뒤 남은 원은 거짓). 화면 크기가 없으면 기기 기본값."""
    msg, later, base = got["touch"]
    assert msg["touch"] is True and (msg["x"], msg["y"]) == (300, 300)
    assert later is None
    assert base == [390, 844]


def test_bar_marks_show_clicks_page_changes_and_hidden_spans(
    got: dict[str, Any],
) -> None:
    """§3.3 시간 막대 — 누름 = 점(0~1 자리), 화면 바뀜 = 눈금, 숨김 구간 = [자리, 폭]."""
    span = 31_500
    marks = got["marks"]
    assert marks["clicks"] == pytest.approx([500 / span, 30_000 / span])
    assert marks["pages"] == pytest.approx([29_500 / span])
    assert marks["hides"] == [pytest.approx([28_500 / span, 1_000 / span])]


def test_event_list_leaves_out_moves_and_names_areas(got: dict[str, Any]) -> None:
    """§3.3 사건 목록 — 움직임은 빼고 누름·스크롤 크게(한 번 굴림 300px 이상 — 영역 안도)·화면 바뀜·숨김/보임·보던 영역,
    영역은 071 이름표(그 시각의 화면 — 대시보드 공통 이름 포함), 시각은 KST."""
    assert got["rows"] == [
        [1_000, "c", "누름", "필터 바", "02:46:41"],
        [1_400, "s", "스크롤", "아래로 800px", "02:46:41"],
        [2_000, "a", "보던 영역", "김프 표", "02:46:42"],
        [2_200, "e", "영역 안 스크롤", "김프 표 · 아래로 400px", "02:46:42"],
        [29_000, "v", "숨김", "탭을 숨김", "02:47:09"],
        [30_000, "v", "보임", "탭이 다시 보임", "02:47:10"],
        [30_000, "p", "화면 바뀜", "대시보드 갭", "02:47:10"],
        [30_500, "c", "누름", "탭 단추", "02:47:10"],
        [32_000, "e", "영역 안 스크롤", "갭 표 · 아래로 700px", "02:47:12"],
    ]
    assert got["rowAt"] == [-1, 1]


def test_frame_address_time_text_follow_and_truncation(got: dict[str, Any]) -> None:
    """§3.3 틀 주소(대시보드는 /app/?tab=<탭>&kt-replay=1, 모르는 화면은 띄우지 않음), 시각 글, 따라 보기(마지막 사건 − 3초, 8초 넘게
    뒤처지면 건너뜀, 늦음 = 마지막 묶음 at + 받은 뒤 흐른 시간 − 재생 시각), §3.5 '앞부분만'(3,600 묶음·1MB 근처·cut)."""
    assert got["paths"] == [
        "/app/?tab=gap&kt-replay=1",
        "/app/?tab=flow&kt-replay=1",
        "/?kt-replay=1",
        "/privacy?kt-replay=1",
        "/kimp-chart?kt-replay=1",
        "/kimp-history?kt-replay=1",
        None,
        None,
        None,
    ]
    assert got["text"] == [
        "02:47:45 KST · 경과 1:04 / 전체 0:31",
        "62:05",
        "--:--:--",
    ]
    assert got["follow"] == [29_000, 25_000, 29_000, 4]
    assert got["cut"] == [True, True, True, False, False]


def test_card_rows_and_requests(got: dict[str, Any]) -> None:
    """§3.1·§3.2 카드 행 — 지금 화면(이름표)·기기·머문 시간(last − first)·'n초 전'(서버 now), 화면 기록의 지나간 화면들(배열·쉼표 글자),
    sid 꼴이 아니면 열지 않음, 글자는 글 그대로. 부르는 주소는 PATHS 표의 셋(지금 보는 중만 지금 묶음)."""
    live = got["live"]
    assert live[0] == {
        "sid": "0123456789abcdef",
        "open": True,
        "ip": "203.0.113.7",
        "now": "대시보드 갭",
        "dev": "PC",
        "stay": 95,
        "last": T0 + 95_000,
        "ago": "6초 전",
        "consent": "미동의(답 안 함) → 동의",
        "agreed": True,
    }
    assert live[1]["open"] is False and live[1]["ip"] == X and live[1]["now"] == X
    # 모르는 동의 값은 답 안 함
    assert (live[1]["consent"], live[1]["agreed"]) == ("미동의(답 안 함)", False)
    assert (sessions_consent := [r["consent"] for r in got["sessions"]]) == [
        "미동의(답 안 함) → 동의",
        "미동의(답 안 함)",
    ], sessions_consent
    assert live[1]["dev"] == X and live[1]["stay"] is None and live[1]["ago"] == "–"
    sessions = got["sessions"]
    assert sessions[0]["start"] == "10-15 02:46:40"
    assert sessions[0]["pages"] == "대시보드 스프레드 → 대시보드 갭"
    assert (sessions[0]["len"], sessions[0]["clicks"]) == (95, 3)
    assert sessions[1]["pages"] == f"랜딩 → {X}" and sessions[1]["start"] == "–"
    assert got["reqs"] == [
        "/svc/api/admin/replay/live",
        "/svc/api/admin/replay/sessions",
        "/svc/api/admin/replay/sessions?ip=2001%3Adb8%3A%3A5",
        "/svc/api/admin/replay/session?sid=0123456789abcdef&after=600",
        True,
        False,
    ]


def test_area_and_page_strings_stay_text_and_never_reach_the_frame(
    got: dict[str, Any],
) -> None:
    """§4 XSS — 영역·화면 이름이 HTML 조각이면 목록에는 글자 그대로(그리는 쪽이 textContent), 틀 메시지의 영역은 id 꼴만(아니면 ''·영역 안
    스크롤 짝 없음), 화면 이름은 그대로 두되 틀 주소가 없다(위 paths)."""
    xss = got["xss"]
    assert xss["rows"] == [X, X, X, f"{X} · 아래로 900px"]
    assert xss["state"]["area"] == "" and xss["state"]["els"] == []
    assert xss["page"] == X


def test_consent_text_names_every_state(got: dict[str, Any]) -> None:
    """073 §3.4 의 c — 동의·미동의(사유), 조각 안에서 바뀌면 처음 → 지금. 키가 없으면 동의한 방문만 기록하던 때의 조각(동의)."""
    assert got["consent"] == [
        "동의",
        "동의",
        "미동의(거부함)",
        "미동의(하루 동안 안 보기)",
        "미동의(답 안 함)",
        "미동의(GPC 거부)",
        "동의 → 미동의(GPC 거부)",
        "미동의(거부함)",
    ]
