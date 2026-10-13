"""관리자 화면 v4 의 차트 option 이 진짜 ECharts 5.5.1 에서 끝까지 그려지는가 (스펙 066 §4 '진짜 ECharts SVG 그리기' — 브라우저 없이).

vendor 사본을 node 에 그대로 싣고(서버 그리기 — `renderer: 'svg', ssr: true`, 테마 'kt' 등록) 가짜 피드 서버의 경우마다 페이지 여섯과
열 수 있는 상세 판을 모두 그린다. 예외 0·카드 '표시 오류' 0, 그림마다 SVG 가 나오고 이름표 글자가 실제로 찍힌다. 상태 띠의 '자료 없음'
빗금(사용자 정의 계열)도 한 번 그린다. 툴팁·확대·크기 맞춤 같은 상호작용과 보기 좋음은 설계 세션이 브라우저로 본다.
"""

import copy
from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_detail import answers
from tests.test_admin_fake import URLS

DETAILS = {
    "overview.js": ["ws", "visit", "collect", "alarms", "cost", "cpu", "traffic"],
    "server.js": [
        "cpu",
        "net",
        "mem",
        "disk",
        "credit",
        "io",
        "ws",
        "canary",
        "checks",
        "collect",
        "alarms",
        "cost",
    ],
    "traffic.js": [
        "trend",
        "flows",
        "src",
        "geo",
        "ends",
        "what",
        "who",
        "heat",
        "status",
        "durations",
    ],
    "visitors.js": [],
    "insight.js": [
        "sessions",
        "botSessions",
        "users",
        "pps",
        "scroll",
        "active",
        "signals",
        "match",
        "pages",
        "countries",
        "dims",
        "refs",
    ],
}
SCENARIO = r"""
const code = (await import('node:fs')).readFileSync(`${input.root}/web/admin/vendor/echarts-5.5.1.min.js`, 'utf8')
const lib = { exports: {} }
new Function('exports', 'module', code)(lib.exports, lib)
const real = lib.exports
out.version = real.version
const respond = (url) => input.answers[url] || [404, { detail: 'Not Found' }]
const themes = []
for (const [name, details] of Object.entries(input.pages)) {
  const v = setup({ respond, search: input.search, buttons: { detail: details } })
  document.getElementById('sheet').hidden = true
  const made = new Map()
  const failed = []
  globalThis.echarts = {
    init: (el, theme) => { const c = real.init(null, theme, { renderer: 'svg', ssr: true, width: 720, height: 320 }); made.set(el.id, c); return c },
    registerTheme: (n, t) => { themes.push(n); real.registerTheme(n, t) },
    connect: real.connect,
    format: real.format,
    graphic: real.graphic,
  }
  const drawn = {}
  const render = (tag) => {
    for (const [id, chart] of made) {
      if (chart.isDisposed()) continue
      try {
        const svg = chart.renderToSVGString()
        drawn[`${tag}:${id}`] = { paths: (svg.match(/<path/g) || []).length, has: input.needles.filter((w) => svg.includes(w)) }
      } catch (err) {
        failed.push([`${tag}:${id}`, String((err && err.stack) || err).slice(0, 400)])
      }
    }
  }
  await page(name, `?r=${name}${input.search}`)
  await flush()
  render('page')
  for (const d of details) {
    v.click('detail', d)
    await flush()
    render(d)
    document.getElementById('sheet-close').fire('click')
    await flush()
  }
  const errors = [...v.byId.entries()].filter(([id, n]) => id.startsWith('t-') && n.textContent === '표시 오류').map(([id]) => id)
  out[name] = { drawn, failed, errors }
}
out.themes = themes  // 공통 모듈은 node 에서 한 번만 실린다 — 테마 등록도 첫 페이지에서 한 번
"""
NEEDLES = [
    "랜딩",
    "collect",
    "data",
    "serve",
    "업비트",
    "%",
    "Slack",
    "잔량",
    "주의 75%",
    "들어온 길",
    "사람 모양 페이지",
]


def _run(scenario: str, search: str = "", tweak: Any = None) -> dict[str, Any]:
    feeds = answers(scenario, URLS)
    if tweak:
        tweak(feeds)
    pages = {**DETAILS, "screens.js": []}
    got: dict[str, Any] = run_admin(
        SCENARIO,
        {"pages": pages, "answers": feeds, "needles": NEEDLES, "search": search},
    )
    assert got.pop("version") == "5.5.1"
    assert got.pop("themes") == ["kt"]  # 테마는 CSS 토큰으로 한 번 등록
    return got


@pytest.mark.parametrize(
    ("scenario", "search"),
    [
        ("ok", ""),
        ("ok", "?cmp=prev"),
        ("problem", "?cmp=week"),
        ("old", "?cmp=prev"),
        ("missing", ""),
        ("xss", ""),
    ],
)
def test_real_echarts_draws_every_chart_and_detail_sheet(
    scenario: str, search: str
) -> None:
    got = _run(scenario, search)
    for page, result in got.items():
        assert result["failed"] == [] and result["errors"] == [], (
            page,
            result["failed"],
            result["errors"],
        )
        for chart, drawn in result["drawn"].items():
            assert drawn["paths"] > 0, (page, chart)
    if (scenario, search) == ("ok", ""):
        overview, server, traffic = (
            got["overview.js"]["drawn"],
            got["server.js"]["drawn"],
            got["traffic.js"]["drawn"],
        )
        assert "업비트" in overview["page:c-ex"]["has"]  # 거래소 줄 이름표
        assert {"collect", "data", "serve"} <= set(
            server["page:c-cpu"]["has"]
        ) and "주의 75%" in server["page:c-cpu"]["has"]
        assert (
            "잔량" in server["page:c-credit"]["has"]
            and "Slack" in server["page:c-alarms"]["has"]
        )
        assert {"랜딩", "들어온 길"} <= set(traffic["page:c-flows"]["has"])
        assert "사람 모양 페이지" in overview["traffic:c-sheet"]["has"]  # 판의 칸 이름
        sheets = {k.split(":")[0] for k in server if k.endswith(":c-sheet")}
        assert sheets == set(DETAILS["server.js"]) - {"cost"}  # 비용 판은 예산 표만


def test_status_lanes_draw_the_no_data_hatch() -> None:
    """066 §3.3 — 상태 띠: 정상 얇고 옅게 · 실패 칸 가득 · 자료 없음 빗금(사용자 정의 계열의 선 묶음)이 진짜 ECharts 에서 그려진다."""

    def holes(feeds: dict[str, list]) -> None:
        body = copy.deepcopy(feeds["/api/admin/aws/series?range=24h"][1])
        for box in body["boxes"]:
            points = box["series"]["statusFailed"]
            for i in range(40, 60):
                points[i][1] = None
            for i in range(80, 90):
                points[i][1] = 1
        feeds["/api/admin/aws/series?range=24h"] = [200, body]

    got = _run("ok", tweak=holes)
    server = got["server.js"]
    assert server["failed"] == [] and server["errors"] == []
    assert server["drawn"]["page:c-checks"]["paths"] > 30  # 빗금 선들


COUNT_TICKS = r"""
const code = (await import('node:fs')).readFileSync(`${input.root}/web/admin/vendor/echarts-5.5.1.min.js`, 'utf8')
const lib = { exports: {} }
new Function('exports', 'module', code)(lib.exports, lib)
const real = lib.exports
setup({ visible: false, respond: () => [404, {}] })
const charts = await page('charts.js')
const common = await page('common.js')
const T0 = 1_800_000_000_000
const H = 3_600_000
const texts = (option) => {
  const c = real.init(null, null, { renderer: 'svg', ssr: true, width: 720, height: 200 })
  c.setOption(option)
  const svg = c.renderToSVGString()
  c.dispose()
  return [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => m[1])
}
const pts = Array.from({ length: 24 }, (_, i) => [T0 + i * H, i === 5 ? 1 : 0])
const one = (pane) => charts.timeOption({ startMs: T0, endMs: T0 + 24 * H, panes: [pane], series: [{ name: 'x', mark: 'bad', type: 'bar', data: pts }] })
out.counts = texts(one({ title: '5xx', fmt: common.int })).filter((t) => /^\d+$/.test(t))
const word = (v) => (v >= 1 ? '실패' : '정상')
out.checks = texts(one({ fmt: word, axisFmt: word, max: 1, int: true })).filter((t) => t === '실패' || t === '정상')
"""


def test_count_axes_have_integer_ticks_without_repeated_labels() -> None:
    """066 §3.1 차트 규칙 — 센 수의 세로축(5xx·canary 오류·접속·알림…)은 눈금이 정수라 값이 0·1 뿐이어도 '1, 1, 0' 처럼 같은 글자가
    되풀이되지 않는다(진짜 ECharts 로 그려 축 글자를 센다). 상태 검사(정상 0·실패 1)도 같다."""
    got = run_admin(COUNT_TICKS, {})
    assert sorted(got["counts"]) == [
        "0",
        "1",
    ]  # 고치기 전에는 ['0', '0', '1', '1', '1']
    assert sorted(got["checks"]) == ["실패", "정상"]
