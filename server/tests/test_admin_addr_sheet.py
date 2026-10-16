"""관리자 방문자 페이지의 IP 상세 판(스펙 070 §3.3·§4 node) — 행 → `#detail=addr:<ip>`, 머리 줄(IP·복사·판정 배지·점수), 판정 근거(가중치 큰 순·
막대 폭 |가중치|/70·봇 쪽/사람 쪽·점수 줄), 확인된/사칭 검색 로봇 줄, 어디 칸(null 섞임·대략·AS·DB-IP 표시), 최근 요청 신호 칩·CSV,
◀ ▶, 400 bad_ip 는 판 안 한 줄, partial 은 '일부만 읽음', XSS(근거 글·PTR·망 사업자).
"""

import re
from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_addrs import BUTTONS, LIST, NOW, X
from tests.test_admin_detail import FAST, answers
from tests.test_deploy import ROOT

ADDR = "/svc/api/admin/access/addr?ip={}&window=24h"


def detail(ip: str, **extra: Any) -> dict[str, Any]:
    """069 §3.6 꼴 — 목록 행 값 + geo·verifiedCrawler·067 상세 값·recent(flags)·partial."""
    listed = next((r for r in LIST["rows"] if r["ip"] == ip), LIST["rows"][0])
    recent = [
        {
            "ts": NOW - 30 * k,
            "path": "/.env" if k == 0 else "/",
            "status": 404 if k == 0 else 200,
            "kind": "scanner" if k == 0 else "browser",
            "browser": "chrome",
            "os": "windows",
            "device": "desktop",
            "referer": None,
            "flags": [["probe", "404"], ["asset", "js"], []][k],
        }
        for k in range(3)
    ]
    body = {
        "state": "ok",
        "code": None,
        "window": "24h",
        **listed,
        "ip": ip,
        "geo": {
            "country": listed["country"],
            "region": listed["region"],
            "city": listed["city"],
            "lat": 37.56651,
            "lon": 126.978,
            "asn": listed["asn"],
            "org": listed["org"],
            "net": listed["net"],
            "ptr": None,
            "approx": True,
        },
        "verifiedCrawler": None,
        "hourly": [{"ts": NOW - 3600, "requests": 4, "pages": 2, "js": 1, "errors": 0}],
        "agents": [["desktop·windows·chrome", 3]],
        "paths": [["/", 3]],
        "pairs": [],
        "recent": recent,
        "partial": False,
    }
    return {**body, **extra}


GOOGLE = detail(
    "66.249.66.1",
    verifiedCrawler=True,
    geo={**detail("66.249.66.1")["geo"], "ptr": "crawl-66-249-66-1.googlebot.com"},
)
HARNESS = (
    BUTTONS
    + r"""
const v = setup({ respond, search: input.search || '', hash: input.hash || '', buttons: BUTTONS })
document.getElementById('sheet').hidden = true
const wrote = []
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async (t) => { wrote.push(t) } } }, configurable: true })
await page('visitors.js')
await flush()
const kids = (id) => document.getElementById(id)?.kids || []
const sheet = () => {
  const verdict = kids('addr-verdict')
  const head = verdict[0]?.kids || []
  return {
    open: !document.getElementById('sheet').hidden, title: v.text('sheet-title'), unit: v.text('sheet-unit'), tag: v.text('t-sheet'), note: v.text('sheet-note'),
    flat: document.getElementById('sheet-custom').className,
    panels: kids('sheet-custom').map((k) => [k.id || k.kids[0]?.className, k.className]),
    head: head.map((k) => k.textContent), badge: head[2]?.kids[0]?.className,
    reasons: kids('addr-reasons').filter((r) => r.tag === 'div').map((r) => [r.kids[0].textContent, r.kids[1].textContent, r.kids[2].kids[0].className, r.kids[2].kids[0].style.width]),
    lines: verdict.slice(3).map((k) => [k.className, k.textContent]),
    where: kids('addr-where').find((k) => k.tag === 'dl')?.kids.map((k) => k.textContent) ?? [],
    credit: kids('addr-where').at(-1)?.id,
    table: (kids('sheet-tbl')[0]?.kids[0]?.kids || []).map((th) => th.textContent),
    flags: (kids('sheet-tbl')[1]?.kids || []).filter((tr) => tr.className !== 'rowmore').map((tr) => tr.kids.at(-1).kids[0]?.kids.map((c) => [c.textContent, c.className])),
    prev: document.getElementById('sheet-prev').disabled, next: document.getElementById('sheet-next').disabled,
    tags: [...v.byId.values()].flatMap(function tags(n) { return typeof n === 'string' ? [] : [n.tag, ...n.kids.flatMap(tags)] }).filter((t) => t === 'img' || t === 'script'),
    text: document.getElementById('sheet-custom').textContent + document.getElementById('sheet-tbl').textContent,
  }
}
"""
)
FLOW = (
    HARNESS
    + r"""
const rows = () => document.getElementById('tbl-addrs').kids[1].kids
rows()[3].fire('click') // '(없음)' — 모음 행은 판이 없다
await flush()
out.aggregate = [sheet().open, v.s.pushed.length]
rows()[1].fire('click')
await flush()
out.google = { ...sheet(), pushed: v.s.pushed, calls: v.s.calls.filter((c) => c.includes('/addr?')) }
kids('addr-verdict')[0].kids[1].fire('click')
await flush()
out.copied = [wrote, kids('addr-verdict')[0].kids[1].textContent]
document.getElementById('sheet-csv').fire('click')
await flush()
out.csv = await v.s.blobs.at(-1)?.text()
document.getElementById('sheet-next').fire('click')
await flush()
out.next = { title: v.text('sheet-title'), pushed: v.s.pushed.length, hash: location.hash, calls: v.s.calls.filter((c) => c.includes('/addr?')).at(-1) }
"""
)


def _run(script: str, extra: dict[str, list] | None = None, **payload: Any) -> Any:
    urls = {
        ADDR.format("66.249.66.1"): [200, GOOGLE],
        ADDR.format("198.51.100.0%2F24"): [200, detail("198.51.100.0/24")],
        **(extra or {}),
    }
    return run_admin(
        script,
        {"addrs": LIST, "answers": {**answers("ok", FAST), **urls}, **payload},
    )


@pytest.fixture(scope="module")
def flow() -> dict[str, Any]:
    return _run(FLOW)


def test_row_opens_the_ip_sheet_with_a_detail_link(flow: dict[str, Any]) -> None:
    """070 §3.2 — 행을 누르면 066 상세 판(`#detail=addr:<ip>`), 모음 행 '(없음)' 은 열지 않는다. ◀ ▶ 는 표 차례(기록을 쌓지 않음 — 066)."""
    assert flow["aggregate"] == [False, 0]
    google = flow["google"]
    assert google["open"] and google["title"] == "66.249.66.1"
    assert google["pushed"] == ["/visitors.html#detail=addr%3A66.249.66.1"]
    assert google["calls"] == [ADDR.format("66.249.66.1")]
    assert google["unit"] == "\U0001f1fa\U0001f1f8 California · Google LLC"
    assert (google["prev"], google["next"]) == (False, False)
    assert flow["next"] == {
        "title": "198.51.100.0/24",
        "pushed": 1,
        "hash": "#detail=addr%3A198.51.100.0%2F24",
        "calls": ADDR.format("198.51.100.0%2F24"),
    }


def test_head_reasons_and_score_line(flow: dict[str, Any]) -> None:
    """070 §3.3 — 머리 줄 IP·복사·판정 배지·점수, 근거는 가중치 큰 순 '+70 …'(양수 봇 쪽 pos·음수 사람 쪽 neg, 폭 |가중치|/70), 바닥 점수 줄,
    판정 근거·어디 칸은 판 맨 위(사용자 칸을 풀어 격자 차례 -1), 쓰임 칸은 그 아래."""
    g = flow["google"]
    assert g["head"] == ["66.249.66.1", "복사", "봇", "점수 100"]
    assert g["badge"] == "shape bad"
    assert g["reasons"] == [
        ["+70", "검색 로봇 이름(Googlebot)", "fill pos", "100%"],
        ["+20", "데이터센터 망", "fill pos", "28.6%"],
        ["+10", "robots.txt 1번", "fill pos", "14.3%"],
    ]
    assert g["lines"] == [
        ["score-line", "점수 100 → 봇(60 이상)"],
        ["crawler ok", "✓ 주소 확인된 Google 로봇"],
    ]
    assert g["flat"] == "panel flat"
    # 074 §3.2 — '어디' 아래 화면 기록 절(맨 위 칸과 같은 차례 −1)
    assert g["panels"] == [
        ["addr-verdict", "panel addr-top"],
        ["addr-where", "panel addr-top"],
        ["addr-replay", "panel addr-top rp-ip"],
        ["grid2", "panel"],
    ]
    assert flow["copied"] == [["66.249.66.1"], "복사됨"]


def test_where_panel_and_recent_flags(flow: dict[str, Any]) -> None:
    """070 §3.3 — 어디 칸(나라 국기·이름·지역·도시 '대략'·망 사업자·AS·망 종류·역방향 DNS·위도·경도, 바닥 DB-IP 표시 노드),
    최근 요청 줄마다 신호 칩(탐색·404 는 상태색), CSV 의 신호 칸은 글."""
    g = flow["google"]
    assert g["where"] == [
        "나라",
        "\U0001f1fa\U0001f1f8 미국",
        "지역",
        "California",
        "도시",
        "–",
        "망 사업자",
        "Google LLC",
        "망 종류",
        "데이터센터",
        "역방향 DNS",
        "crawl-66-249-66-1.googlebot.com",
        "위도 · 경도",
        "37.5665, 126.978",
    ]
    assert g["credit"] == "dbip-sheet"
    assert g["table"] == ["시각(KST)", "경로", "상태", "종류", "브라우저", "신호"]
    assert g["flags"] == [
        [["탐색", "flag bad"], ["404", "flag warn"]],
        [["자원", "flag"], ["JS", "flag"]],
        [],
    ]
    head, first = flow["csv"].lstrip("﻿").split("\r\n")[:2]
    assert head.endswith(",신호") and first.endswith(",탐색 404")


SHEET = (
    HARNESS
    + r"""
out.sheet = sheet()
"""
)
IMPOSTOR = detail(
    "203.0.113.9",
    partial=True,
    verifiedCrawler=False,
    score=130,
    verdict="bot",
    reasons=[
        {"code": "ua_bot", "text": "검색 로봇 이름(Yeti)", "weight": 70},
        {
            "code": "fake_crawler",
            "text": "검색 로봇 이름을 쓰지만 주소 확인 실패",
            "weight": 30,
        },
        {"code": "js", "text": "자바스크립트가 돈 페이지 1번", "weight": -40},
    ],
    geo={
        "country": None,
        "region": None,
        "city": "Busan",
        "lat": None,
        "lon": 129.0,
        "asn": 3786,
        "org": None,
        "net": None,
        "ptr": None,
        "approx": True,
    },
)


def test_impostor_crawler_partial_and_null_geo() -> None:
    """070 §3.3 — 이름만 검색 로봇('✕ 이름만 네이버 — 주소 확인 실패'), 사칭 근거 +30, 음수 근거는 사람 쪽 막대, partial = '일부만 읽음' 배지,
    어디 칸의 null 은 '–'(도시는 '대략', 망 사업자 이름 없이 AS 만, 위도·경도 하나라도 없으면 '–')."""
    got = _run(
        SHEET,
        {ADDR.format("203.0.113.9"): [200, IMPOSTOR]},
        hash="#detail=addr%3A203.0.113.9",
    )["sheet"]
    assert got["open"] and got["tag"] == "일부만 읽음"
    assert [r[0] for r in got["reasons"]] == ["+70", "+30", "−40"]
    assert got["reasons"][2][2:] == ["fill neg", "57.1%"]
    assert got["lines"] == [
        ["score-line", "점수 130 → 봇(60 이상)"],
        ["crawler bad", "✕ 이름만 네이버 — 주소 확인 실패"],
    ]
    assert got["where"] == [
        "나라",
        "–",
        "지역",
        "–",
        "도시",
        "Busan대략",
        "망 사업자",
        "AS3786",
        "망 종류",
        "–",
        "역방향 DNS",
        "–",
        "위도 · 경도",
        "–",
    ]


def test_bad_ip_is_one_line_inside_the_sheet() -> None:
    """070 §3.3 — 400 bad_ip 는 페이지 실패가 아니라 판 안 한 줄(근거·어디 칸 없음)."""
    got = _run(
        SHEET,
        {
            ADDR.format("1.2.3.4"): [
                400,
                {"error": {"code": "bad_ip", "message": "x", "detail": None}},
            ]
        },
        hash="#detail=addr%3A1.2.3.4",
    )["sheet"]
    assert got["open"] and got["tag"] == "불러오지 못함"
    assert got["note"] == "이 IP 는 상세를 볼 수 없다 — IP 꼴이 아니다(bad_ip)"
    assert got["reasons"] == [] and got["where"] == []


def test_reason_ptr_and_org_text_never_become_html() -> None:
    """070 §3.4 — 근거 글·PTR 이름·망 사업자·도시·경로·브라우저 정보는 textContent 로만(066 XSS 규칙)."""
    evil = detail(
        "203.0.113.66",
        reasons=[{"code": "probe", "text": f"탐색 {X}", "weight": 60}],
        geo={**IMPOSTOR["geo"], "org": X, "ptr": X, "city": X, "country": "KR"},
        agents=[[X, 2]],
        paths=[[X, 2]],
        recent=[
            {
                "ts": NOW,
                "path": X,
                "status": 200,
                "kind": X,
                "browser": X,
                "flags": [X, "js"],
            }
        ],
    )
    got = _run(
        SHEET,
        {ADDR.format("203.0.113.66"): [200, evil]},
        hash="#detail=addr%3A203.0.113.66",
    )["sheet"]
    assert got["text"].count(X) >= 5 and got["tags"] == []
    assert got["reasons"][0][1] == f"탐색 {X}"
    assert got["flags"] == [[["JS", "flag"]]]  # 목록 밖 신호는 버린다


MODEL = r"""
setup({ visible: false, respond: () => [404, {}] })
const m = await page('addrmodel.js')
out.valid = ['1.2.3.4', '1.2.3.0', '203.0.113.0/24', '2001:db8::5', '::ffff:1.2.3.4', '2001:DB8::5', '1.2.3.256', '(없음)', '(기타)', 'a b'].map(m.validAddr)
out.lines = ['bot', 'suspect', 'human', 'unknown', 'operator', 'nope'].map((v) => m.scoreLine(v === 'operator' ? null : 42.4, v))
out.asn = [4766, '4766', 'AS15169', null, ''].map(m.asnText)
"""


def test_ip_check_score_lines_and_as_numbers() -> None:
    """070 §3.3 — 상세를 부를 IP(IPv4·IPv6 소문자·067 대역, 모음 행 아님), 점수 줄의 문턱 글(069 §3.4), AS 번호 글."""
    got = run_admin(MODEL)
    assert got["valid"] == [
        True,
        True,
        True,
        True,
        True,
        False,
        False,
        False,
        False,
        False,
    ]
    assert got["lines"] == [
        "점수 42 → 봇(60 이상)",
        "점수 42 → 의심(30~59)",
        "점수 42 → 사람(30 미만)",
        "점수 42 → 모름(요청 3 미만)",
        "점수 – → 운영자(운영자 흔적 — 점수 안 셈)",
        "점수 42 → 모름(요청 3 미만)",
    ]
    assert got["asn"] == ["AS4766", "AS4766", "AS15169", "", ""]


def _rules(css: str) -> list[tuple[str, str]]:
    """admin.css 의 (선택자, 몸) — 미디어 쿼리 안 규칙도 한 줄씩(중첩 한 단계)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [
        (sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    ]


def test_ip_sheet_panels_are_not_hidden_by_any_css_rule() -> None:
    """070 §3.3 — 판정 근거·어디 칸이 실제 브라우저에서 보인다(설계 세션 확인 2026-10-14: 칸 클래스 'top' 이 좁은 화면 머리 줄
    `.top { display: none }` 과 겹쳐 두 칸이 사라졌다 — node 가짜 DOM 은 CSS 를 모른다). IP 판·표가 만드는 요소의 클래스마다 그 클래스를
    고르는 규칙에 display: none 이 없고, 칸은 hidden 으로 만들지 않으며, 풀어 둔 사용자 칸(display: contents) 안 두 칸은 차례 −1."""
    css = (ROOT / "web/admin/admin.css").read_text()
    used: set[str] = set()
    for name in ("addrdetail.js", "addrs.js"):
        js = (ROOT / f"web/admin/{name}").read_text()
        if name == "addrdetail.js":  # 판의 칸은 hidden 으로 만들지 않는다
            assert ".hidden" not in js and "'hidden'" not in js
        for classes in re.findall(r"\bel\('\w+', `?'?([\w -]+)", js):
            used |= set(classes.split())
    assert {
        "panel",
        "addr-top",
        "reasons",
        "reason",
        "rbar",
        "verdict",
        "flags",
        "flag",
    } <= used
    for sel, body in _rules(css):
        if re.search(r"display:\s*none", body):
            for part in sel.split(","):
                last = part.strip().split()[-1] if part.strip() else ""
                hit = set(re.findall(r"\.([\w-]+)", last))
                assert not (hit and hit <= used), (sel, used & hit)
    flat = dict(_rules(css))
    assert "display: contents;" in flat["#sheet-custom.flat"]
    assert "order: -1;" in flat["#sheet-custom.flat > .addr-top"]


CLOSED = (
    HARNESS
    + r"""
out.sheet = { open: !document.getElementById('sheet').hidden, calls: v.s.calls.filter((c) => c.includes('/addr?')) }
"""
)


def test_an_invalid_ip_in_the_address_does_not_open_the_sheet() -> None:
    """070 §3.3·066 §3.4 — 꼴이 아닌 IP(#detail=addr:nope)는 판을 열지 않고 부르지도 않는다(목록 밖 id 와 같음). 꼴은 맞는데 서버가
    400 bad_ip 면 판 안 한 줄(test_bad_ip_is_one_line_inside_the_sheet)."""
    got = _run(CLOSED, hash="#detail=addr%3Anope")["sheet"]
    assert got == {"open": False, "calls": []}
