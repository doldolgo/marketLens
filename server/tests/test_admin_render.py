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


# 진짜 ECharts SVG 의 글자 상자(어림 — 한글 1em·숫자/영문 0.6em·띄어쓰기 0.3em, dominant-baseline central)와 겹침
TEXT_BOXES = r"""
const textBoxes = (svg) => [...svg.matchAll(/<text([^>]*)>([^<]*)<\/text>/g)].map(([, attrs, text]) => {
  const pick = (re) => Number(re.exec(attrs)?.[1] ?? 0)
  const t = /translate\(([-\d.]+)[ ,]+([-\d.]+)\)/.exec(attrs)
  const x = (t ? Number(t[1]) : 0) + pick(/\sx="([-\d.]+)"/)
  const y = (t ? Number(t[2]) : 0) + pick(/\sy="([-\d.]+)"/)
  const f = Number(/(\d+(?:\.\d+)?)px/.exec(attrs)?.[1] ?? 12)
  const w = [...text].reduce((s, ch) => s + (/[가-힣]/.test(ch) ? f : /\s/.test(ch) ? f * 0.3 : /[·:,/.]/.test(ch) ? f * 0.4 : f * 0.6), 0)
  const anchor = /text-anchor="(\w+)"/.exec(attrs)?.[1] ?? 'start'
  const x0 = anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x
  return { text, x0, x1: x0 + w, y0: y - f / 2, y1: y + f / 2 }
})
const clashes = (boxes) => {
  const out = []
  boxes.forEach((a, i) => boxes.slice(i + 1).forEach((b) => {
    if (a.x0 < b.x1 - 1 && b.x0 < a.x1 - 1 && a.y0 < b.y1 - 1 && b.y0 < a.y1 - 1) out.push(`${a.text} × ${b.text}`)
  }))
  return out
}
"""

PANES = r"""
const code = (await import('node:fs')).readFileSync(`${input.root}/web/admin/vendor/echarts-5.5.1.min.js`, 'utf8')
const lib = { exports: {} }
new Function('exports', 'module', code)(lib.exports, lib)
const real = lib.exports
setup({ visible: false, respond: () => [404, {}] })
let size = [720, 320]
globalThis.echarts = { init: () => real.init(null, null, { renderer: 'svg', ssr: true, width: size[0], height: size[1] }), registerTheme: real.registerTheme, connect: real.connect, format: real.format, graphic: real.graphic }
const charts = await page('charts.js')
const common = await page('common.js')
const ops = await page('opsdetail.js')
const S0 = 1_800_000_000
const pts = (f) => Array.from({ length: 24 }, (_, i) => [S0 + i * 3600, f(i)])
const box = { creditBalance: pts((i) => 120 - i), creditUsage: pts((i) => i / 10), surplusCharged: pts(() => 0), netIn: pts((i) => 2e5 + i * 1e4), netOut: pts((i) => 5e4 + i * 1e3), ebsRead: pts((i) => 1e4 * i), ebsWrite: pts(() => 3e4) }
const part = { boxes: ['collect', 'data', 'serve'].map((name) => ({ box: name, series: box })), canary: { durationMs: pts((i) => 800 + 40 * i), errors: pts((i) => (i === 3 ? 1 : 0)) } }
const w = { boxes: part.boxes, line: (get) => ({ data: (get(part) || []).map(([t, v]) => [t * 1000, v]), cmp: null }) }
const app = { filters: () => [] }
const traffic = { panes: [{ title: '사람 모양 페이지', fmt: common.int }, { title: '5xx', fmt: common.int }, { title: 'WS 접속', fmt: common.int }], series: [{ name: '사람 모양 페이지', mark: 'acc', type: 'bar', data: pts((i) => 40 + i).map(([t, v]) => [t * 1000, v]) }, { name: '5xx', mark: 'bad', type: 'bar', pane: 1, data: pts((i) => (i === 7 ? 1 : 0)).map(([t, v]) => [t * 1000, v]) }, { name: 'WS 접속', mark: 'c1', pane: 2, data: pts((i) => 3 + (i % 4)).map(([t, v]) => [t * 1000, v]) }] }
// 카드 높이는 그 페이지의 칸 그대로(canary h200 · 크레딧·네트워크·디스크 입출력 h320 · 개요 트래픽 h360), 판은 큰 그림(55vh, 최소 300)
const cases = {
  canary: [ops.canaryPanes(app, w), 200],
  credit: [ops.credits(app, w), 320],
  net: [ops.mirrorPanes('netIn', 'netOut', '들어옴', '나감')(app, w), 320],
  io: [ops.mirrorPanes('ebsRead', 'ebsWrite', '읽기', '쓰기')(app, w), 320],
  traffic: [traffic, 360],
}
out.clashes = {}
for (const [name, [build, h]] of Object.entries(cases)) {
  for (const [zoom, sizes] of [['card', [[720, h], [360, h]]], ['detail', [[1200, 480], [360, 300]]]]) {
    for (const s of sizes) {
      size = s
      const id = `c-${name}-${zoom}-${s.join('x')}`
      const chart = charts.draw(id, charts.timeOption({ startMs: S0 * 1000, endMs: (S0 + 86400) * 1000, zoom, legend: zoom === 'detail' ? false : undefined, labels: zoom === 'detail' ? false : undefined, ...build }))
      // 범례 쪽 넘김 글자('1/2')는 잘린 범례 칸 밖에 놓인다 — 잘린 칸 글자와의 겹침은 보이지 않는다
      const found = clashes(textBoxes(chart.renderToSVGString()).filter((b) => !/^\d+\/\d+$/.test(b.text)))
      if (found.length) out.clashes[id] = found
      out.top ??= {}
      out.top[id] = chart.getOption().grid.map((g) => g.top)
    }
  }
}
"""


def test_multi_pane_charts_keep_legend_pane_titles_and_tick_labels_apart() -> None:
    """066 §3.1 차트 규칙(064 빚) — 위아래 칸 그림(크레딧·canary·네트워크·디스크 입출력·개요 트래픽)은 칸을 px 로 놓아 범례 아래에
    첫 칸 이름 자리를, 칸 사이에 '앞 칸 맨 아래 눈금 글자 + 다음 칸 이름' 자리를 남긴다 — 카드(200~360px)·판(미니맵) 높이, 넓은·좁은
    폭에서 진짜 ECharts 가 그린 글자 상자가 하나도 겹치지 않는다."""
    got = run_admin(TEXT_BOXES + PANES, {})
    assert got["clashes"] == {}
    for chart, tops in got["top"].items():
        assert all(isinstance(t, int) for t in tops), chart  # 그릴 때 px 배치
        # 첫 칸 이름은 범례 줄(28px) 아래 자기 줄(26px)에 — 범례와 같은 줄에 앉지 않는다(판은 범례가 없다)
        assert tops[0] == (32 if "-detail-" in chart else 54), (chart, tops)


END_LABELS = r"""
const code = (await import('node:fs')).readFileSync(`${input.root}/web/admin/vendor/echarts-5.5.1.min.js`, 'utf8')
const lib = { exports: {} }
new Function('exports', 'module', code)(lib.exports, lib)
const real = lib.exports
setup({ visible: false, respond: () => [404, {}] })
let size = [720, 320]
globalThis.echarts = { init: () => real.init(null, null, { renderer: 'svg', ssr: true, width: size[0], height: size[1] }), registerTheme: real.registerTheme, connect: real.connect, format: real.format, graphic: real.graphic }
const charts = await page('charts.js')
const common = await page('common.js')
const T0 = 1_800_000_000_000
const H = 3_600_000
const pts = (f) => Array.from({ length: 24 }, (_, i) => [T0 + i * H, f(i)])
// 트래픽 큰 추이(사람 모양 페이지 · 스크립트가 돈 페이지)·Clarity 맞대기(세 선)와 같은 꼴 — 이름이 길다. 잘림은 범례 줄(28px — 넘치면
// 넘김 단추로 자르는 칸) 아래의 글자만 본다
const cases = {
  trend: [{ name: '사람 모양 페이지', mark: 'acc', type: 'bar', data: pts((i) => 40 + i) }, { name: '스크립트가 돈 페이지', mark: 'c1', data: pts((i) => 20 + i) }],
  match: [{ name: 'Clarity 세션', mark: 'acc', data: pts((i) => 30 + i) }, { name: '확인된 방문자', mark: 'c1', data: pts((i) => 28 + i) }, { name: '브라우저 모양 방문자', mark: 'c3', data: pts((i) => 50 + i) }],
}
out.charts = {}
for (const [name, series] of Object.entries(cases)) {
  for (const s of [[1150, 320], [560, 280], [326, 320]]) {
    size = s
    const id = `c-${name}-${s[0]}`
    const chart = charts.draw(id, charts.timeOption({ startMs: T0, endMs: T0 + 24 * H, zoom: 'card', panes: [{ fmt: common.int }], series }))
    const boxes = textBoxes(chart.renderToSVGString()).filter((b) => !/^\d+\/\d+$/.test(b.text))
    out.charts[id] = { width: s[0], right: chart.getOption().grid[0].right, lines: series.map((x) => x.type !== 'bar'), count: series.map((x) => boxes.filter((b) => b.text === x.name).length), cut: boxes.filter((b) => b.y0 > 28 && (b.x1 > s[0] + 1 || b.x0 < -1)).map((b) => b.text), clashes: clashes(boxes) }
  }
}
"""


def test_end_labels_fit_inside_the_chart_or_give_way_to_the_legend() -> None:
    """066 §3.1 차트 규칙 — 선 끝 이름표(계열 둘~넷)는 가장 긴 이름만큼 오른쪽 자리를 남겨 잘리지 않는다. 그 자리가 그림 폭의 30% 를
    넘는 좁은 카드에서는 이름표를 끄고 범례만 둔다(트래픽 큰 추이 '스크립트가 돈 페이지'·Clarity 맞대기 '브라우저 모양 방문자')."""
    got = run_admin(TEXT_BOXES + END_LABELS, {})
    for chart, c in got["charts"].items():
        assert c["cut"] == [] and c["clashes"] == [], (chart, c)
        wide = c["width"] >= 560
        # 넓으면 선은 범례 + 선 끝 이름표 = 두 번(막대는 이름표 없음), 좁으면 범례에 한 번(이름표 끔)
        assert c["count"] == [2 if wide and line else 1 for line in c["lines"]], (
            chart,
            c,
        )
        assert c["right"] >= (100 if wide else 14), (chart, c)
