"""관리자 방문자 페이지의 IP 상세 판(스펙 070 §3.3·§4 node) — 행 → `#detail=addr:<ip>`, 머리 줄(IP·복사·판정 배지·점수), 판정 근거(가중치 큰 순·
막대 폭 |가중치|/70·봇 쪽/사람 쪽·점수 줄), 확인된/사칭 검색 로봇 줄, 어디 칸(null 섞임·대략·AS·DB-IP 표시), 최근 요청 신호 칩·CSV,
◀ ▶, 400 bad_ip 는 판 안 한 줄, partial 은 '일부만 읽음', XSS(근거 글·PTR·망 사업자).
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_addrs import BUTTONS, LIST, NOW, X
from tests.test_admin_detail import FAST, answers

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
    assert g["panels"] == [
        ["addr-verdict", "panel top"],
        ["addr-where", "panel top"],
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
