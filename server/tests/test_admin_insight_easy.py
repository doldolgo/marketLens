"""관리자 Clarity 페이지 쉽게 읽기(스펙 066 §3.3) — 불만 신호 문턱·100칸·전날, 페이지 × 기기 줄(방문 막대·페이지 모양 스크롤·
머문/움직인 시간 띠·눈에 띄는 신호), 카드마다 데이터로 만든 한 문장. 모델은 공개 함수만 부르고, 그림은 가짜 피드 응답으로 node 에서 그린다.
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_detail import answers
from tests.test_admin_fake import URLS
from tests.test_admin_screen import _rule
from tests.test_deploy import _text

MODEL = r"""
setup({ visible: false, respond: () => [404, {}] })
const ins = await page('insightdetail.js')
out.level = [ins.levelOf('rageClick', 1.9), ins.levelOf('rageClick', 2), ins.levelOf('rageClick', 5), ins.levelOf('quickback', null), ins.levelOf('nope', 3), ins.levelOf('constructor', 3)]
out.per100 = [0, 0.3, 0.5, 6.1, 140, null].map(ins.per100)
out.dur = [null, 0, 45.4, 60, 212, 3599.6, 3725].map(ins.dur)
out.depth = [10, 30, 50, 70, 95, null].map(ins.depthWord)
const sig = (sessionPct) => ({ sessions: 1, sessionPct, pageViews: 1, count: 1 })
const base = (sessions, signals, summary = {}) => ({ state: 'ok', numOfDays: 1, traffic: { sessions }, summary: { signals, ...summary } })
const part = base(51, { deadClick: sig(6.1), rageClick: sig(0.3), excessiveScroll: sig(12), quickback: sig(9.8), scriptError: sig(0), errorClick: sig(null) })
const day = (d, e, q) => ({ summary: { signals: { deadClick: { sessionPct: d }, excessiveScroll: { sessionPct: e }, quickback: { sessionPct: q } } } })
const m = ins.signalModel(part, [day(4, 12.2, 9), day(6.1, 12, 11)])
out.sig = m.items.map((it) => [it.key, it.name, it.level, it.cells, it.part, it.trend, ins.trendText(it), it.plain.length > 10])
out.sigSay = [
  ins.signalSay(m),
  ins.signalSay(ins.signalModel(base(8, { deadClick: sig(6.1), rageClick: sig(1), excessiveScroll: sig(0), quickback: sig(0), scriptError: sig(0), errorClick: sig(0.5) }), [])),
  ins.signalSay(ins.signalModel({ state: 'ok', numOfDays: 1, summary: {} }, [])),
  ins.signalSay(ins.signalModel(base(0, { deadClick: sig(0) }), [])),
]
out.zero = ins.signalModel(base(0, { deadClick: sig(0) }), []).items.map((it) => it.level)
const g = (page, device, sessions, extra = {}) => ({ page, device, sessions, scrollDepth: 50, totalSec: 100, activeSec: 40, deadClickPct: 0, rageClickPct: 0, excessiveScrollPct: 0, quickbackPct: 0, scriptErrorPct: 0, errorClickPct: 0, ...extra })
const groups = [
  g('landing', 'mobile', 30, { scrollDepth: 23, totalSec: 190, activeSec: 18, quickbackPct: 12 }),
  g('landing', 'desktop', 14, { scrollDepth: 86, totalSec: 244, activeSec: 54 }),
  g('app:history', 'desktop', 4, { scrollDepth: 69, totalSec: 269, activeSec: 119, errorClickPct: 1.5 }),
  g('privacy', 'other', 2, { scrollDepth: 10, totalSec: 30, activeSec: 5, rageClickPct: 40 }),
  g('app:gap', 'tablet', 14, { scrollDepth: null, totalSec: null, activeSec: null }),
]
const r3 = (v) => (v === null ? null : Math.round(v * 1000) / 1000)
const rows = ins.pagesModel({ groups })
out.rows = rows.map((r) => [r.where, r3(r.sessShare), r3(r.timeShare), r.lit, r3(r.share), r.worst?.key ?? null, r.worst?.level ?? null, r.thin])
out.pagesSay = [ins.pagesSay(rows), ins.pagesSay(ins.pagesModel({ groups: [g('landing', 'mobile', 2)] })), ins.pagesSay([])]
out.matchSay = [
  ins.matchSay([{ data: [[1, 10], [2, 20], [3, null]] }, { data: [[1, 40], [2, 40], [4, 9]] }]),
  ins.matchSay([{ data: [[1, 50]] }, { data: [[1, 10]] }]),
  ins.matchSay([{ data: [] }, { data: [] }]),
]
out.topSay = [
  ins.topSay(ins.dimItems([['South Korea', 44], ['Japan', 2], ['United States', 4]]), '가장 많은 나라는'),
  ins.topSay(ins.dimItems([['KR', 3]]), '가장 많은 나라는'),
  ins.topSay([], '가장 많은 나라는'),
]
out.dimsSay = [ins.dimsSay({ device: [['Mobile', 6], ['PC', 4]], browser: [['Chrome', 1]], os: [] }), ins.dimsSay({})]
const kpi = base(51, {}, { scrollDepth: 47.3, totalSec: 212, activeSec: 64 })
out.kpiSay = [ins.kpiSay('scroll', kpi), ins.kpiSay('active', kpi), ins.kpiSay('sessions', kpi), ins.kpiSay('active', { state: 'unconfigured' })]
"""


@pytest.fixture(scope="module")
def got() -> dict[str, Any]:
    return run_admin(MODEL)


def test_signal_levels_hundred_cells_and_yesterday(got: dict[str, Any]) -> None:
    """066 §3.3 불만 신호 — 문턱 [주의, 나쁨](분노 클릭 2·5), 방문 100번 중 몇 번(반올림·'1번 미만'·100 상한), 칸 = 반올림 수(0 초과 반 칸 미만은 첫 칸 옅게),
    전날 = 기록 마지막 두 날(0.5%p 안이면 비슷), 줄 아래 문턱 글, 쉬운 풀이 여섯."""
    assert got["level"] == ["ok", "warn", "bad", None, None, None]
    assert got["per100"] == ["0번", "1번 미만", "1번", "6번", "100번", "–"]
    assert got["sig"] == [
        [
            "deadClick",
            "죽은 클릭",
            "ok",
            6,
            False,
            "up",
            "전날 4번 · ▲ 늘었다 · 10번부터 주의 · 20번부터 나쁨",
            True,
        ],
        [
            "rageClick",
            "분노 클릭",
            "ok",
            0,
            True,
            None,
            "2번부터 주의 · 5번부터 나쁨",
            True,
        ],
        [
            "excessiveScroll",
            "과한 스크롤",
            "bad",
            12,
            False,
            "same",
            "전날 12번 · – 비슷 · 5번부터 주의 · 10번부터 나쁨",
            True,
        ],
        [
            "quickback",
            "빠른 뒤로 가기",
            "ok",
            10,
            False,
            "up",
            "전날 9번 · ▲ 늘었다 · 10번부터 주의 · 20번부터 나쁨",
            True,
        ],
        [
            "scriptError",
            "스크립트 오류",
            "ok",
            0,
            False,
            None,
            "2번부터 주의 · 5번부터 나쁨",
            True,
        ],
        [
            "errorClick",
            "오류 클릭",
            None,
            0,
            False,
            None,
            "1번부터 주의 · 3번부터 나쁨",
            True,
        ],
    ]
    assert got["sigSay"] == [
        "가장 눈에 띄는 신호는 과한 스크롤(나쁨) — 방문 100번 중 12번.",
        "모두 좋음 — 가장 잦은 죽은 클릭도 방문 100번 중 6번이다. 다만 최근 24시간 방문이 8번뿐이라 한두 번에도 크게 흔들린다.",
        "",
        "최근 24시간에 동의한 방문이 없어 셀 신호가 없다.",
    ]
    # 세션 0 이면 0% 는 '좋음' 이 아니다 — 등급을 비운다
    assert got["zero"] == [None] * 6


def test_words_for_time_and_scroll_depth(got: dict[str, Any]) -> None:
    """머문 시간은 초·분·시간 말로, 스크롤 깊이는 어디서 멈추는지 말로. 큰 수 타일 한 줄은 스크롤·활성 시간에만."""
    assert got["dur"] == [
        "–",
        "0초",
        "45초",
        "1분",
        "3분 32초",
        "1시간 0분",
        "1시간 2분",
    ]
    assert got["depth"] == [
        "첫 화면 근처에서 멈춘다",
        "위쪽 절반 안에서 멈춘다",
        "페이지 가운데쯤에서 멈춘다",
        "아래쪽까지 대부분 본다",
        "끝까지 본다",
        "",
    ]
    assert got["kpiSay"] == [
        "평균으로 보면 페이지 가운데쯤에서 멈춘다",
        "머문 3분 32초 중 1분 4초(30%) 동안 스크롤·클릭·입력을 했다",
        "",
        "",
    ]


def test_page_device_rows_and_their_sentences(got: dict[str, Any]) -> None:
    """066 §3.3 페이지 × 기기 — 방문 내림차순(같으면 받은 차례), 방문 막대 = 가장 많은 줄 대비, 시간 띠 = 가장 긴 머문 시간 대비·안쪽 = 움직인 몫(%),
    페이지 모양 밝은 칸 = 깊이 ÷ 10 반올림, 눈에 띄는 신호 = 0 보다 큰 것 중 등급·문턱 대비가 큰 것, 방문 3번 미만은 흐림.
    답은 흐린 줄을 빼고 셋까지 — 많이 오는 곳의 스크롤 → 시간 → 신호(덜 내려보는 곳은 45% 아래가 있을 때만)."""
    assert got["rows"] == [
        ["휴대폰 랜딩", 1, 0.706, 2, 9.474, "quickback", "warn", False],
        ["데스크톱 랜딩", 0.467, 0.907, 9, 22.131, None, None, False],
        ["태블릿 대시보드 갭", 0.467, 0, 0, None, None, None, False],
        ["데스크톱 대시보드 기록", 0.133, 1, 7, 44.238, "errorClick", "warn", False],
        ["처리방침(그 밖 기기)", 0.067, 0.112, 1, 16.667, "rageClick", "bad", True],
    ]
    assert got["pagesSay"] == [
        [
            "방문이 가장 많은 휴대폰 랜딩(30번)은 평균 23% 까지 내려본다 — 첫 화면 근처에서 멈춘다.",
            "스크롤·클릭·입력을 가장 오래 한 곳은 데스크톱 대시보드 기록 — 머문 4분 29초 중 1분 59초.",
            "불만 신호가 가장 눈에 띄는 곳은 데스크톱 대시보드 기록의 오류 클릭 — 방문 100번 중 2번.",
        ],
        ["모든 줄이 방문 3번 미만이라 아직 읽기 이르다 — 며칠 더 쌓인 뒤 본다."],
        [],
    ]


def test_match_and_list_sentences(got: dict[str, Any]) -> None:
    """맞대기는 둘 다 있는 날만 더해 견준다(세션이 더 많으면 그 까닭), 목록 카드는 가장 큰 항목과 몫, 기기 카드는 차원 셋의 첫째."""
    assert got["matchSay"] == [
        "둘 다 있는 2일 동안 Clarity 세션 30 · 확인된 방문자 80 — 방문자 100명 중 약 38명꼴로 화면 분석에 동의한 셈이다(세는 법이 달라 어림).",
        "둘 다 있는 1일 동안 Clarity 세션 50 · 확인된 방문자 10 — 세션이 더 많다. 한 사람이 여러 번 오면 세션은 여러 개로 센다.",
        "",
    ]
    assert got["topSay"] == [
        "가장 많은 나라는 South Korea — 세션의 88%.",
        "가장 많은 나라는 KR — 세션 전부.",
        "",
    ]
    assert got["dimsSay"] == [
        "가장 많은 기기는 Mobile(60%) · 브라우저는 Chrome(100%).",
        "",
    ]


DRAW = r"""
const respond = (url) => input.answers[url] || [404, { detail: 'Not Found' }]
const v = setup({ respond })
await page('insight.js', `?s=${input.tag}`)
await flush()
const kidsOf = (n) => n.kids.filter((k) => typeof k !== 'string')
const cls = (n) => n.className
const sigs = kidsOf(document.getElementById('l-signals'))
out.signals = sigs.map((row) => {
  const cells = kidsOf(row).find((k) => cls(k) === 'sig-foot')?.kids.find((k) => typeof k !== 'string' && cls(k).startsWith('cells'))
  return cells ? [cells.kids.length, cells.kids.filter((c) => cls(c) === 'on').length, cls(cells)] : cls(row)
})
const places = kidsOf(document.getElementById('l-pages'))
out.places = places.map((row) => [cls(row), kidsOf(row).length])
const first = places[1]
out.first = first ? { shape: kidsOf(kidsOf(first)[2]).find((k) => cls(k) === 'pshape')?.kids.length, visits: kidsOf(kidsOf(kidsOf(first)[1]).find((k) => cls(k).startsWith('pbar')))[0]?.style.width, title: first.title } : null
out.say = Object.fromEntries(['say-signals', 'say-pages', 'say-match', 'say-countries', 'say-dims', 'say-refs', 'say-scroll', 'say-active', 'say-sessions'].map((id) => {
  const n = document.getElementById(id)
  return [id, n ? [n.hidden, n.textContent.length > 0, n.kids.length] : null]
}))
if (first) first.fire('click')
out.sheet = document.getElementById('sheet').hidden
"""


def test_cards_draw_hundred_cells_page_rows_and_sentences() -> None:
    """가짜 피드(ok)로 그린 Clarity 페이지 — 신호 줄 여섯에 칸 100(색칠 = 반올림 %), 페이지 × 기기는 머리 + 12줄 + '전체 보기'(15칸),
    줄마다 칸 다섯·페이지 모양 열 칸·첫 줄 방문 막대 100%, 카드 한 문장이 보이고, 줄을 누르면 상세 판."""
    got = run_admin(DRAW, {"answers": answers("ok", URLS), "tag": "ok"})
    assert len(got["signals"]) == 6 and all(
        n == 100 and 0 <= on <= 100 and c.startswith("cells ")
        for n, on, c in got["signals"]
    )
    assert got["places"][0] == ["prow phead", 5]
    assert [c for c, _ in got["places"][1:13]] == [
        c for c, _ in got["places"][1:13] if c in ("prow", "prow thin")
    ]
    assert (
        all(n == 5 for _, n in got["places"][1:13])
        and got["places"][13][0] == "list-foot"
        and len(got["places"]) == 14
    )
    assert (
        got["first"]["shape"] == 10
        and got["first"]["visits"] == "100%"
        and "휴대폰 랜딩" in got["first"]["title"]
    )
    say = got["say"]
    for key in (
        "say-signals",
        "say-match",
        "say-countries",
        "say-dims",
        "say-refs",
        "say-scroll",
        "say-active",
    ):
        assert say[key][:2] == [False, True], key
    assert say["say-pages"][0] is False and 1 <= say["say-pages"][2] <= 3
    assert say["say-sessions"] == [True, False, 0]  # 세션 타일은 한 줄이 없다
    assert got["sheet"] is False


def test_cards_hide_sentences_when_not_connected() -> None:
    """Clarity 연결 안 됨(empty) — 그림 자리에 상태 글, 카드 한 문장은 숨긴다."""
    got = run_admin(DRAW, {"answers": answers("empty", URLS), "tag": "empty"})
    for key in (
        "say-signals",
        "say-pages",
        "say-countries",
        "say-dims",
        "say-refs",
        "say-scroll",
        "say-active",
    ):
        assert got["say"][key][0] is True, key
    assert got["places"] == [["empty", 0]] and got["signals"] == ["empty"]


def test_clarity_page_static_keys_and_shapes() -> None:
    """정적 — 카드 한 문장 자리(신호·맞대기·나라·기기·유입·스크롤·활성 시간은 <p>, 페이지 × 기기는 <ul>), 그림 열쇠 줄 둘,
    100칸은 두 줄 × 쉰 칸(위아래 먼저), 페이지 모양은 열 칸, 좁은 화면은 머리 줄을 숨기고 칸 이름을 보인다."""
    html = _text("web/admin/clarity.html")
    for key in ("signals", "match", "countries", "dims", "refs"):
        assert f'<p class="say" id="say-{key}" hidden></p>' in html, key
    for key in ("scroll", "active"):
        assert f'<p class="say small" id="say-{key}" hidden></p>' in html, key
    assert '<ul class="say" id="say-pages" hidden></ul>' in html
    assert (
        '<div class="sigs" id="l-signals"></div>' in html
        and '<div class="places" id="l-pages"></div>' in html
    )
    assert html.count('<p class="keys">') == 2 and "tbl-pages" not in html
    css = _text("web/admin/admin.css")
    cells = _rule(css, ".cells")
    assert (
        "grid-auto-flow: column;" in cells
        and "grid-template-columns: repeat(50, minmax(0, 1fr));" in cells
        and "grid-template-rows: repeat(2, 5px);" in cells
    )
    assert "grid-template-rows: repeat(10, 2px);" in _rule(css, ".pshape")
    narrow = css.split("@media (max-width: 767px)")[1]
    assert (
        ".prow.phead {\n    display: none;" in narrow
        and ".pc .plabel {\n    display: block;" in narrow
    )
