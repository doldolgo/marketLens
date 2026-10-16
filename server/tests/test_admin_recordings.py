"""관리자 Clarity 페이지 '녹화' 카드와 상세 판 (스펙 077 §3.3·§4 'node') — 가짜 피드 서버의 recordings 응답으로 그린다.

카드 표(시작·길이·페이지·클릭·지나간 페이지·재생 링크)·빈 문구·탭 바꿈 → `days`, 재생 링크 검사(Clarity 출처·허용 글자만 — 같은 탭·
noreferrer), 행 → 상세 판 `#detail=rec:<번호>` 의 시간선, XSS(제목·클릭 글자는 글로만, Clarity 밖 주소는 링크가 아님).
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_detail import answers
from tests.test_admin_fake import URLS

X = "<img src=x onerror=alert(1)>"
REC = [f"/svc/api/admin/clarity/recordings?days={d}" for d in (1, 3, 7, 30)]

CARD = r"""
const respond = (url) => input.answers[url] || [404, { detail: 'Not Found' }]
const v = setup({ respond, buttons: { detail: ['recs'] } })
await page('insight.js', `?rec=${input.tag}`)
await flush()
const rowsOf = (id) => (v.byId.get(id).kids[1]?.kids || []).map((tr) => tr.kids.map((td) => td.textContent))
const linkOf = (n) => { const a = walk(n).find((k) => k.tag === 'a'); return a ? [a.attrs.href, a.attrs.rel ?? null, a.attrs.target ?? null, a.textContent] : null }
const tbl = v.byId.get('tbl-recs')
const body = tbl.kids[1]?.kids || []
out.card = {
  caption: tbl.kids[0]?.tag === 'caption' ? tbl.textContent : null,
  rows: rowsOf('tbl-recs'),
  links: body.map(linkOf),
  tag: v.text('t-recs'),
  tagTitle: v.byId.get('t-recs').title,
  tabs: v.byId.get('tabs-recs').kids.map((b) => [b.textContent, b.attrs['aria-pressed']]),
  imgs: walk(tbl).filter((n) => n.tag === 'img').length,
}
// 재생 링크를 눌러도 행(상세 판 열기)은 움직이지 않는다
const a = body.length ? walk(body[0]).find((k) => k.tag === 'a') : null
if (a) {
  let stopped = 0
  for (const fn of a.listeners.click || []) fn({ stopPropagation: () => { stopped += 1 } })
  out.linkStops = stopped
}
if (body.length) {
  body[1 % body.length].fire('click')
  await flush()
  out.sheet = {
    hash: location.hash,
    title: v.text('sheet-title'),
    unit: v.text('sheet-unit'),
    custom: v.text('sheet-custom'),
    customLink: linkOf(v.byId.get('sheet-custom')),
    rows: rowsOf('sheet-tbl'),
    prev: v.byId.get('sheet-prev').disabled,
    next: v.byId.get('sheet-next').disabled,
    imgs: walk(v.byId.get('sheet-tbl')).filter((n) => n.tag === 'img').length,
  }
}
v.byId.get('tabs-recs').kids[3]?.fire('click')
await flush()
out.calls = v.s.calls.filter((u) => u.includes('/recordings'))
out.after = { rows: rowsOf('tbl-recs').length, tabs: v.byId.get('tabs-recs').kids.map((b) => b.attrs['aria-pressed']) }
"""


def _card(scenario: str) -> dict[str, Any]:
    return run_admin(
        CARD, {"tag": scenario, "answers": answers(scenario, [*URLS, *REC])}
    )


def test_card_lists_recordings_with_a_clarity_link_and_opens_the_timeline() -> None:
    got = _card("ok")
    card = got["card"]
    assert card["caption"] is None and card["tag"] == ""  # 정상 — 배지 없음
    assert len(card["rows"]) == 7  # 3일 — 가짜 서버 일곱
    start, length, pages, clicks, path, play = card["rows"][0]
    assert len(start) == 19 and start[4] == "-"  # KST 초까지
    assert " / " in length and length.endswith(("초", "분"))
    assert pages == "3" and clicks == "4"
    assert path == "랜딩 → 대시보드 스프레드 → 대시보드 기록"
    assert play == "재생 ↗"
    href, rel, target, text = card["links"][0]
    assert (
        href.startswith("https://clarity.microsoft.com/player/")
        and rel == "noreferrer"
        and target is None
    )
    assert got["linkStops"] == 1
    assert card["tabs"] == [
        ["1일", "false"],
        ["3일", "true"],
        ["7일", "false"],
        ["30일", "false"],
    ]
    sheet = got["sheet"]
    assert sheet["hash"] == "#detail=rec%3A1"
    assert sheet["title"] == "Clarity 녹화"
    assert "페이지 1" in sheet["unit"] and "클릭 1" in sheet["unit"]
    assert (
        sheet["customLink"][0].endswith("fakesession1")
        and sheet["customLink"][3] == "Clarity 에서 재생 ↗"
    )
    assert "로그인" in sheet["custom"]
    # 시간선 — 페이지 열림 줄 다음에 그 페이지의 클릭
    assert [r[:4] for r in sheet["rows"]][:2] == [
        ["00:00", "페이지 열림", "대시보드 스프레드", "KimpTrack"],
        [sheet["rows"][1][0], "클릭", "대시보드 스프레드", "기록/통계"],
    ]
    assert sheet["rows"][0][4].startswith("머묾 ") and "로딩 " in sheet["rows"][0][4]
    assert sheet["prev"] is False and sheet["next"] is False  # 1번 — 앞뒤 둘 다 있음
    # 탭 바꿈 → 그 기간을 곧바로 부른다(주소에는 싣지 않는다)
    assert got["calls"][0] == REC[1] and REC[3] in got["calls"]
    assert got["after"] == {"rows": 10, "tabs": ["false", "false", "false", "true"]}


@pytest.mark.parametrize(
    ("scenario", "caption"),
    [
        ("nobands", "이 기간 녹화 없음 — 동의한 방문만 녹화된다"),
        ("empty", "연결 안 됨"),
        ("old", "연결 안 됨"),  # 077 전 서버 — 404
    ],
)
def test_empty_unconfigured_and_missing_feed(scenario: str, caption: str) -> None:
    got = _card(scenario)
    assert got["card"]["rows"] == [] and caption in (got["card"]["caption"] or ""), got[
        "card"
    ]
    assert "sheet" not in got


def test_problem_shows_the_error_code_without_rows() -> None:
    got = _card("problem")
    card = got["card"]
    assert card["rows"] == [] and card["tag"] == "불러오지 못함", card
    assert card["tagTitle"] == "http_429" and card["caption"] == "불러오지 못함"


def test_xss_text_stays_text_and_foreign_links_are_not_links() -> None:
    got = _card("xss")
    card = got["card"]
    assert card["imgs"] == 0 and got["sheet"]["imgs"] == 0
    # 0번 `javascript:` · 1번 따옴표 섞인 Clarity 주소 — 링크가 아니라 '–'
    assert card["links"][0] is None and card["links"][1] is None
    assert card["rows"][0][5] == "–" and card["rows"][1][5] == "–"
    assert card["links"][2][0].startswith("https://clarity.microsoft.com/")
    assert (
        got["sheet"]["customLink"] is None
        and "재생 링크가 없다" in got["sheet"]["custom"]
    )
    assert any(X in cell for row in got["sheet"]["rows"] for cell in row)


PURE = r"""
const c = await page('common.js')
const r = await page('insightrec.js')
out.href = input.links.map((l) => c.clarityHref(l))
out.pages = input.urls.map((u) => r.recPage(u))
out.timeline = r.timelineRows({ timeline: [
  { url: 'https://kimptrack.com/app/?tab=pp', title: 'T', atSec: 65, stayMs: 61000, loadMs: 300, referrer: 'https://www.google.com', events: [{ type: 'Scroll', text: null, atSec: 70 }, { type: 'Hover', text: 'x', atSec: 71 }] },
  'bad',
] })
"""


def test_link_check_page_names_and_timeline_rows() -> None:
    ok = "https://clarity.microsoft.com/player/a/b/c?date=2026-10-15"
    links = [
        ok,
        "http://clarity.microsoft.com/player/a",
        "https://clarity.microsoft.com.evil.example/x",
        "https://clarity.microsoft.com@evil.example/",
        'https://clarity.microsoft.com/a"b',
        "https://clarity.microsoft.com/a b",
        "https://clarity.microsoft.com/a#b",
        "javascript:alert(1)",
        "https://clarity.microsoft.com/" + "a" * 471,
        "https://clarity.microsoft.com/" + "a" * 470,
        None,
        42,
    ]
    urls = [
        "https://kimptrack.com/",
        "https://kimptrack.com/app/",
        "https://kimptrack.com/app/?tab=history",
        "https://kimptrack.com/app/?tab=nope",
        "https://kimptrack.com/privacy",
        "https://www.kimptrack.com/kimp-chart",
        "https://kimptrack.com/kimp-history.html",
        "https://kimptrack.com/wp-login.php",
        "https://other.example/x",
        "not a url",
        None,
    ]
    got = run_admin(PURE, {"links": links, "urls": urls})
    assert got["href"] == [ok, *[None] * 8, links[9], None, None]
    assert got["pages"] == [
        "랜딩",
        "대시보드 스프레드",
        "대시보드 기록",
        "대시보드 nope",
        "처리방침",
        "김프 차트",
        "김프 기록",
        "/wp-login.php",
        "https://other.example/x",
        "not a url",
        "–",
    ]
    assert got["timeline"] == [
        {
            "atSec": 65,
            "kind": "페이지 열림",
            "page": "대시보드 선선갭",
            "text": "T",
            "note": "머묾 1분 · 로딩 300ms · 들어온 곳 https://www.google.com",
        },
        {
            "atSec": 70,
            "kind": "스크롤",
            "page": "대시보드 선선갭",
            "text": "",
            "note": "",
        },
        {
            "atSec": 71,
            "kind": "Hover",
            "page": "대시보드 선선갭",
            "text": "x",
            "note": "",
        },
    ]
