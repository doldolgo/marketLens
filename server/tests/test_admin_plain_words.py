"""관리자 화면 쉬운 이름표(스펙 082 §4) — 요청 종류 여덟의 이름·설명, 최근 요청 '페이지·파일' 가르기(직접 연 페이지·함께 받은 파일·
따로 부른 파일), IP 판 표의 배지·칸 머리 '?'·CSV, 화면에 옛 낱말('사람 모양'·'브라우저 모양'·'사람 브라우저'·'운영자 흔적')이 없음."""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_addr_sheet import ADDR, HARNESS, _run, detail
from tests.test_admin_addrs import NOW
from tests.test_admin_explain import _problems
from tests.test_admin_screen import PAGES, SCRIPTS
from tests.test_admin_scripts import _script
from tests.test_deploy import _text

OLD_WORDS = ("사람 모양", "브라우저 모양", "사람 브라우저", "운영자 흔적")

MODEL = r"""
setup({ visible: false, respond: () => [404, {}] })
const ip = await page('ipmodel.js')
const names = await page('names.js')
const SITE = 'https://kimptrack.com'
const cases = [
  ['/', 'browser', null], ['/app/', 'browser', SITE], ['/privacy', 'browser', null], ['/admin', 'scanner', null], ['/kimp-chart', 'search', null],
  ['/app/assets/x.js', 'browser', SITE], ['/api/history/candles', 'browser', SITE], ['/robots.txt', 'operator', 'https://admin.kimptrack.com'],
  ['/api/ws/spreads', 'browser', null], ['/favicon.svg', 'browser', null], ['/apple-touch-icon-precomposed.png', 'browser', null], ['/icon-192.png', 'browser', null],
  ['/api/ws/spreads', 'tool', null], ['/favicon.ico', 'scanner', null], ['/robots.txt', 'search', null], ['/.env', 'scanner', null], ['/api/landing', 'tool', null],
  ['/logo-512.png', 'browser', null], ['', 'browser', null], ['x.js', 'browser', SITE], [null, 'browser', null],
]
out.how = cases.map(([p, k, r]) => ip.pathHow(p, k, r))
out.rows = ip.recentRows([
  { ts: 3, path: '/robots.txt', kind: 'search', referer: null },
  { ts: 2, path: '', kind: 'browser' },
  { ts: 1, path: '/', kind: 'zzz' },
]).map((r) => [r.how, r.howText, r.kind])
out.names = names.CLASS_NAME
out.notes = names.CLASS_NOTE
out.howNames = names.HOW_NAME
"""


@pytest.fixture(scope="module")
def model() -> dict[str, Any]:
    return run_admin(MODEL)


def test_kind_keys_stay_and_names_are_plain(model: dict[str, Any]) -> None:
    """082 §3.1 — 종류 키 여덟은 038 그대로, 이름은 쉬운 말, 설명도 여덟. 표 밖 키는 원래 글자."""
    assert model["names"] == {
        "browser": "일반 브라우저",
        "search": "검색 로봇",
        "ai": "AI 로봇",
        "preview": "링크 미리보기",
        "tool": "자동화 도구",
        "scanner": "그 밖 봇·스캐너",
        "operator": "운영자 접속",
        "unknown": "브라우저 정보 없음",
    }
    assert set(model["notes"]) == set(model["names"])
    assert model["notes"]["browser"].startswith(
        "크롬·사파리 같은 보통 브라우저로 온 요청"
    )
    assert "방문자로 세지 않는다" in model["notes"]["operator"]
    assert model["rows"][2][2] == "zzz"


def test_path_how_splits_pages_files_with_a_page_and_files_alone(
    model: dict[str, Any],
) -> None:
    """082 §3.2 — 끝 조각에 점이 없고 /api/ 밖이면 직접 연 페이지(404 난 탐색도), 출처가 있으면 함께 받은 파일, 출처를 싣지 않는 브라우저
    요청(실시간 연결·탭 아이콘)도 함께 받은 파일, 나머지는 따로 부른 파일, '/' 로 시작하지 않으면 없음."""
    assert model["how"] == [
        *["page"] * 5,
        *["with"] * 3,
        *["with"] * 4,
        *["alone"] * 6,
        None,
        None,
        None,
    ]
    assert model["howNames"] == {
        "page": "직접 연 페이지",
        "with": "함께 받은 파일",
        "alone": "따로 부른 파일",
    }
    assert model["rows"] == [
        ["alone", "따로 부른 파일", "검색 로봇"],
        [None, "–", "일반 브라우저"],
        ["page", "직접 연 페이지", "zzz"],
    ]


def test_admin_screen_never_shows_the_old_words() -> None:
    """082 §3.1 — 관리자 스크립트(주석 빼고)·HTML 어디에도 옛 낱말이 없다."""
    for name in SCRIPTS:
        code = _script(name)
        assert not [w for w in OLD_WORDS if w in code], name
    for name in PAGES:
        html = _text(f"web/admin/{name}")
        assert not [w for w in OLD_WORDS if w in html], name


RECENT = [
    {"ts": NOW - 10, "path": "/", "status": 200, "kind": "browser", "referer": None},
    {
        "ts": NOW - 20,
        "path": "/app/assets/index-1.js",
        "status": 200,
        "kind": "browser",
        "referer": "https://kimptrack.com",
    },
    {"ts": NOW - 30, "path": "/api/ws/spreads", "status": 101, "kind": "browser"},
    {"ts": NOW - 40, "path": "/robots.txt", "status": 200, "kind": "search"},
    {"ts": NOW - 50, "path": "", "status": 400, "kind": "unknown"},
]
SHEET = (
    HARNESS
    + r"""
const ths = kids('sheet-tbl')[0].kids[0].kids
const helpOf = (th) => th.kids.find((k) => k.className === 'help qmark')
out.head = ths.map((th) => [th.textContent, helpOf(th)?.dataset.help ?? null, helpOf(th) ? th.kids.indexOf(helpOf(th)) : null, th.kids[0]?.tag ?? null])
out.helpAttrs = ['role', 'tabindex', 'aria-label', 'aria-expanded'].map((a) => helpOf(ths[2]).getAttribute(a))
out.badges = kids('sheet-tbl')[1].kids.filter((tr) => tr.className !== 'rowmore').map((tr) => [tr.kids[2].kids[0]?.className, tr.kids[2].textContent])
// '?' 누름 — 정렬 단추가 아니라 말풍선(071 — 문서의 잡기 단계에서 받는다)
const e = { target: helpOf(ths[2]), stopPropagation() { this.stopped = true }, preventDefault() {} }
;(v.docListeners.click || []).forEach((fn) => fn(e))
const pop = document.getElementById('help-pop')
out.pop = { stopped: e.stopped === true, hidden: pop.hidden, paras: pop.kids[0].kids.map((p) => p.kids[0].textContent) }
out.sortAfter = kids('sheet-tbl')[0].kids[0].kids.map((th) => th.getAttribute('aria-sort'))
document.getElementById('sheet-csv').fire('click')
await flush()
out.csv = await v.s.blobs.at(-1)?.text()
"""
)


@pytest.fixture(scope="module")
def sheet() -> dict[str, Any]:
    ip = "203.0.113.7"
    return _run(
        SHEET,
        {ADDR.format(ip): [200, detail(ip, recent=RECENT)]},
        hash=f"#detail=addr%3A{ip}",
    )


def test_ip_sheet_recent_table_has_the_page_file_badges(sheet: dict[str, Any]) -> None:
    """082 §3.2 — '경로' 다음 칸 '페이지·파일', 줄마다 배지(모양 클래스 how page·with·alone), 경로가 비면 '–'."""
    assert [h[0] for h in sheet["head"]] == [
        "시각(KST)",
        "경로",
        "페이지·파일",
        "상태",
        "종류",
        "브라우저",
        "신호",
    ]
    assert sheet["badges"] == [
        ["how page", "직접 연 페이지"],
        ["how with", "함께 받은 파일"],
        ["how with", "함께 받은 파일"],
        ["how alone", "따로 부른 파일"],
        ["dim", "–"],
    ]


def test_column_help_marks_follow_the_071_rules(sheet: dict[str, Any]) -> None:
    """082 §3.2·§3.3 — '페이지·파일'·'종류' 칸 머리만 '?'(정렬 단추 뒤 형제 — 단추 안이 아니다), 글은 071 §3.2 칸 넷, 단추 역할,
    누르면 말풍선(보여 주는 것·낱말 뜻·자료 출처·읽는 법)이 열리고 정렬은 그대로."""
    helps = {h[0]: h for h in sheet["head"] if h[1] is not None}
    assert set(helps) == {"페이지·파일", "종류"}
    for _, text, at, first in helps.values():
        assert _problems(text) == [], _problems(text)
        assert at == 1 and first == "button"
    for word in ("· 직접 연 페이지 — ", "· 함께 받은 파일 — ", "· 따로 부른 파일 — "):
        assert word in helps["페이지·파일"][1], word
    assert "· 운영자 접속 · 브라우저 정보 없음 — " in helps["종류"][1]
    assert sheet["helpAttrs"] == ["button", "0", "설명", "false"]
    assert sheet["pop"] == {
        "stopped": True,
        "hidden": False,
        "paras": ["보여 주는 것", "낱말 뜻", "자료 출처", "읽는 법"],
    }
    assert sheet["sortAfter"] == [
        "descending",
        None,
        None,
        None,
        None,
        None,
        None,
    ]


def test_csv_has_the_page_file_column_as_text(sheet: dict[str, Any]) -> None:
    """082 §3.2 — CSV 머리 줄은 칸 이름만, 줄에는 배지 글자."""
    head, *lines = sheet["csv"].lstrip("﻿").split("\r\n")
    assert head.split(",")[:3] == ["시각(KST)", "경로", "페이지·파일"]
    assert [line.split(",")[2] for line in lines[:4]] == [
        "직접 연 페이지",
        "함께 받은 파일",
        "함께 받은 파일",
        "따로 부른 파일",
    ]
