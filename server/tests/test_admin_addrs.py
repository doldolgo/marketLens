"""관리자 방문자 페이지 'IP 별'(스펙 070 §4 node) — IP 표 행(판정 배지·위치 글·가린 행·근거 title), 거르기 → 부르는 주소, 보기 단추·주소
왕복, 'IP 전체는 … 부터' 줄, 큰 수 넷. 069 피드(`/admin/access/addrs`)의 응답을 손으로 만든다(069 §3.5 꼴 — 가짜 피드 서버와 따로).
판(상세)은 test_admin_addr_sheet.py 가 본다.
"""

import time
from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_detail import FAST, answers

NOW = int(time.time())
KST0 = (NOW + 32_400) // 86_400 * 86_400 - 32_400  # 오늘(KST) 00:00
X = "<img src=x onerror=alert(1)>"


def row(ip: str, **extra: Any) -> dict[str, Any]:
    base = {
        "ip": ip,
        "masked": False,
        "country": None,
        "region": None,
        "city": None,
        "asn": None,
        "org": None,
        "net": None,
        "requests": 1,
        "pages": 0,
        "js": 0,
        "ws": 0,
        "kinds": {},
        "status": {},
        "first": NOW - 3 * 86_400,
        "last": NOW - 60,
        "spark": [1, 2],
        "topPaths": [],
        "topAgents": [],
        "score": 0,
        "verdict": "unknown",
        "reasons": [],
    }
    return {**base, **extra}


HUMAN = [
    {"code": "js", "text": "자바스크립트가 돈 페이지 9번", "weight": -40},
    {"code": "search_ref", "text": "검색 엔진에서 들어온 페이지 1번", "weight": -10},
    {"code": "ws", "text": "실시간 연결(WS) 2번", "weight": -30},
    {"code": "assets", "text": "정적 자원 요청 30번", "weight": -15},
]
GOOGLE = [
    {"code": "robots", "text": "robots.txt 1번", "weight": 10},
    {"code": "ua_bot", "text": "검색 로봇 이름(Googlebot)", "weight": 70},
    {"code": "cloud", "text": "데이터센터 망", "weight": 20},
]
ROWS = [
    row(
        "203.0.113.7",
        country="KR",
        region="Seoul",
        city="Seoul",
        asn=4766,
        org="Korea Telecom",
        net="telecom_kr",
        requests=40,
        pages=12,
        first=KST0 + 1,
        score=-95,
        verdict="human",
        reasons=HUMAN,
    ),
    row(
        "66.249.66.1",
        country="US",
        region="California",
        org="Google LLC",
        net="cloud",
        requests=30,
        first=NOW - 2 * 86_400,
        score=100,
        verdict="bot",
        reasons=GOOGLE,
    ),
    row(
        "198.51.100.0/24",
        masked=True,
        country="JP",
        net="telecom",
        requests=5,
        pages=3,
        first=NOW - 4 * 86_400,
        score=50,
        verdict="suspect",
        reasons=[
            {"code": "no_js", "text": "페이지 3번인데 JS 0 (대역)", "weight": 30},
            {"code": "cloud", "text": "데이터센터 망 (대역)", "weight": 20},
        ],
    ),
    row("(없음)", requests=2, first=NOW, score=None, verdict="unknown"),
    row(
        "2001:db8::5",
        net="ipv6",
        requests=3,
        score=None,
        verdict="operator",
        org=X,
        city=X,
    ),
]
LIST = {
    "state": "ok",
    "code": None,
    "window": "24h",
    "total": 7,
    "since": NOW - 5 * 3600,
    "rows": ROWS,
}
ADDRS = "/svc/api/admin/access/addrs?window=24h&sort=requests&limit=200&offset=0"
IPS = "/svc/api/admin/access/ips?window=24h&sort=requests&limit=200&offset=0"
BUTTONS = r"""
const BUTTONS = { view: ['ip', 'band'], verdict: ['bot', 'suspect', 'human', 'unknown', 'operator'], net: ['', 'telecom_kr', 'telecom', 'cloud', 'other', 'ipv6'], detail: [] }
const respond = (url) => {
  const [path] = url.split('?')
  if (path === '/svc/api/admin/access/addrs') return input.addrs?.state ? [200, input.addrs] : [404, { detail: 'Not Found' }]
  return input.answers[url] || (path === '/svc/api/admin/access/ips' ? [200, { state: 'ok', code: null, window: '24h', total: 0, bands: [] }] : [404, { detail: 'Not Found' }])
}
const cells = (id) => (document.getElementById(id).kids[1]?.kids || []).map((tr) => tr.kids.map((td) => td.textContent))
"""
PAGE = (
    BUTTONS
    + r"""
const v = setup({ respond, search: input.search || '', buttons: BUTTONS })
document.getElementById('sheet').hidden = true
await page('visitors.js')
await flush()
const body = document.getElementById('tbl-addrs').kids[1]?.kids || []
out.first = {
  calls: v.s.calls.filter((c) => c.includes('/access/')),
  cells: cells('tbl-addrs'),
  head: (document.getElementById('tbl-addrs').kids[0]?.kids[0]?.kids || []).map((th) => th.textContent),
  shapes: body.map((tr) => tr.kids[1].kids[0]?.kids[0]?.className),
  masked: body.map((tr) => tr.kids[0].kids[0]?.kids.map((k) => k.className)),
  titles: body.map((tr) => tr.title),
  scoreWidth: body.map((tr) => tr.kids[2].kids[0]?.kids[0]?.kids[0]?.style.width),
  placeTitle: body[0]?.kids[3].kids[0]?.title,
  kpis: ['acount', 'abot', 'adc', 'anew'].map((k) => v.text(`v-${k}`)),
  deltas: ['abot', 'adc'].map((k) => v.text(`d-${k}`)),
  since: [v.text('since'), document.getElementById('since').hidden],
  hidden: ['kpis-ip', 'card-addrs', 'kpis-band', 'card-bands'].map((id) => document.getElementById(id).hidden),
  view: v.groups.view.map((b) => b.attrs['aria-pressed']),
  count: v.text('addrs-count'),
  tag: v.text('t-addrs'),
  caption: document.getElementById('tbl-addrs').kids[0]?.tag === 'caption' ? v.text('tbl-addrs') : null,
  tags: [...v.byId.values()].flatMap(function tags(n) { return typeof n === 'string' ? [] : [n.tag, ...n.kids.flatMap(tags)] }).filter((t) => t === 'img' || t === 'script'),
}
"""
)


def _page(search: str = "", addrs: dict | None = None, extra: str = "") -> Any:
    return run_admin(
        PAGE + extra,
        {
            "search": search,
            "addrs": LIST if addrs is None else addrs,
            "answers": answers("ok", FAST),
        },
    )


@pytest.fixture(scope="module")
def first() -> dict[str, Any]:
    return _page()["first"]


def test_ip_view_is_the_default_and_calls_addrs_only(first: dict[str, Any]) -> None:
    """070 §3.2 — 보기 기본 IP 별: addrs 한 쪽만 부르고(대역 ips 는 안 부름), IP 상자를 보이고 대역 상자를 숨긴다."""
    assert first["calls"] == [ADDRS]
    assert first["hidden"] == [False, False, True, True]
    assert first["view"] == ["true", "false"]


def test_table_rows_show_verdict_badges_places_and_masked_bands(
    first: dict[str, Any],
) -> None:
    """070 §3.2 — 열 차례, 판정 배지(모양 + 글), 점수, 위치('국기 도시(없으면 지역) · 망 사업자'), 가린 행은 '대역' 표시, 근거 상위 셋은 행 title."""
    assert first["head"] == [
        "IP",
        "판정",
        "점수",
        "위치",
        "요청",
        "사람 모양 페이지",
        "마지막",
        "추이",
    ]
    ips = [c[0] for c in first["cells"]]
    assert ips == [
        "203.0.113.7",
        "66.249.66.1",
        "198.51.100.0/24대역",
        "(없음)",
        "2001:db8::5",
    ]
    assert [c[1] for c in first["cells"]] == ["사람", "봇", "의심", "모름", "운영자"]
    assert first["shapes"] == [
        "shape ok",
        "shape bad",
        "shape warn",
        "shape none",
        "shape op",
    ]
    assert [c[2] for c in first["cells"]] == ["-95", "100", "50", "–", "–"]
    assert first["scoreWidth"][:3] == ["0%", "100%", "50%"]
    assert [c[3] for c in first["cells"]][:4] == [
        "\U0001f1f0\U0001f1f7 Seoul · Korea Telecom",
        "\U0001f1fa\U0001f1f8 California · Google LLC",
        "\U0001f1ef\U0001f1f5 일본",
        "–",
    ]
    assert first["placeTitle"] == "\U0001f1f0\U0001f1f7 Seoul · Korea Telecom"
    assert first["masked"][2] == ["mono", "vtag"] and first["masked"][0] == ["mono"]
    assert first["titles"][0] == (
        "−10 검색 엔진에서 들어온 페이지 1번\n−15 정적 자원 요청 30번\n−30 실시간 연결(WS) 2번"
    )
    assert first["titles"][1] == (
        "+70 검색 로봇 이름(Googlebot)\n+20 데이터센터 망\n+10 robots.txt 1번"
    )
    assert first["count"] == "5 / 7개"


def test_kpis_count_bot_and_datacenter_shares_and_today(first: dict[str, Any]) -> None:
    """070 §3.2 — IP 수 = total, 봇 판정 = (봇+의심 요청) ÷ 불러온 요청, 데이터센터 요청 비율, 오늘(KST) 처음 본 IP. total 이 더 크면 어림(≈)."""
    assert first["kpis"] == ["7", "≈ 44%", "≈ 38%", "2"]
    assert first["deltas"] == ["불러온 5개 기준", "불러온 5개 기준"]
