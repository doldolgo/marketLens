"""관리자 화면 v3 의 차트 option 이 진짜 ECharts 5.5.1 에서 끝까지 그려지는가 (스펙 064 §4 보강 — 브라우저 없이).

vendor 사본을 node 에 그대로 싣고(서버 그리기 — `renderer: 'svg', ssr: true`) 가짜 피드 서버의 경우마다 세 페이지가 만든
option 을 setOption·renderToSVGString 으로 그린다. 예외 0·카드 '표시 오류' 0, 그림마다 SVG 가 나오고 축·이름표 글자가
실제로 찍힌다. 툴팁·확대·크기 맞춤 같은 상호작용과 보기 좋음은 설계 세션이 브라우저로 본다.
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_fake import PLAIN, _fake

SCENARIO = r"""
const code = (await import('node:fs')).readFileSync(`${input.root}/web/admin/vendor/echarts-5.5.1.min.js`, 'utf8')
const lib = { exports: {} }
new Function('exports', 'module', code)(lib.exports, lib)
const real = lib.exports
const respond = (url) => {
  const [path, query] = url.split('?')
  const q = new URLSearchParams(query || '')
  if (path === '/api/admin/aws/series') return input.series[q.get('range')]
  if (path === '/svc/api/admin/access') return input.access[q.get('window')]
  return input.feeds[path] || [404, { detail: 'Not Found' }]
}
out.version = real.version
for (const name of ['overview.js', 'server.js', 'traffic.js']) {
  const v = setup({ respond, buttons: { range: ['6h', '24h', '7d', '30d'], window: ['24h', '7d', '30d'] } })
  const made = new Map()
  globalThis.echarts = {
    init: (el) => { const c = real.init(null, null, { renderer: 'svg', ssr: true, width: 720, height: 320 }); made.set(el.id, c); return c },
    connect: real.connect,
    format: real.format,
    graphic: real.graphic,
  }
  await page(name, `?r=${name}`)
  await flush()
  if (name === 'server.js') { v.click('range', '7d'); await flush() }
  const charts = {}
  for (const [id, chart] of made) {
    try {
      const svg = chart.renderToSVGString()
      charts[id] = { ok: true, texts: (svg.match(/<text/g) || []).length, paths: (svg.match(/<path/g) || []).length, has: input.needles.filter((w) => svg.includes(w)) }
    } catch (err) {
      charts[id] = { ok: false, err: String((err && err.stack) || err).slice(0, 400) }
    }
  }
  const errors = [...v.byId.entries()].filter(([id, n]) => id.startsWith('t-') && n.textContent === '표시 오류').map(([id]) => id)
  out[name] = { charts, errors }
}
"""
NEEDLES = ["랜딩", "collect", "data", "serve", "업비트", "%", "Slack", "들어옴", "잔량"]


@pytest.mark.parametrize("scenario", ["ok", "problem", "missing", "xss"])
def test_real_echarts_draws_every_chart_of_every_page(scenario: str) -> None:
    fake = _fake()
    fake.state["scenario"] = scenario
    feeds = {path: list(fake.feed("GET", path, {}, {})) for path in PLAIN}
    series = {r: list(fake.series(r)) for r in ("6h", "24h", "7d", "30d")}
    access = {w: [200, fake.access(w)] for w in ("24h", "7d", "30d")}
    got: dict[str, Any] = run_admin(
        SCENARIO,
        {"feeds": feeds, "series": series, "access": access, "needles": NEEDLES},
    )
    assert got.pop("version") == "5.5.1"
    for page, result in got.items():
        assert result["errors"] == [], (page, result["errors"])
        assert result["charts"], page
        for chart, drawn in result["charts"].items():
            assert drawn["ok"], (page, chart, drawn.get("err"))
            assert drawn["paths"] > 0, (page, chart)
    if scenario == "ok":
        overview, server, traffic = (
            got[p]["charts"] for p in ("overview.js", "server.js", "traffic.js")
        )
        assert "%" in overview["g-data"]["has"]  # 반원 게이지 값 글자
        assert {"collect", "data", "serve"} <= set(
            server["c-cpu"]["has"]
        )  # 선 끝 이름표
        assert (
            "들어옴" in server["c-net"]["has"] and "잔량" in server["c-credit"]["has"]
        )
        assert (
            "업비트" in server["c-collect"]["has"]
            and "Slack" in server["c-alerts"]["has"]
        )
        assert "랜딩" in traffic["c-flows"]["has"]  # 흐름 마디 이름표
        for chart in ("c-cpu", "c-traffic", "c-trend"):
            drawn = server.get(chart) or overview.get(chart) or traffic.get(chart)
            assert drawn["texts"] >= 4, chart  # 축 눈금 글자
