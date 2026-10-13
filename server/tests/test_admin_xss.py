"""관리자 화면 v4 — 피드 글자가 HTML 이 되지 않는가 (스펙 066 §4 '툴팁·표 글자 XSS', 064 §3.1 보안).

가짜 피드 서버의 xss 경우(글자 칸마다 `<img onerror>`)로 페이지 여섯을 그리고, 카드와 열 수 있는 상세 판마다 차트 option 의 툴팁 서식을
그 차트의 실제 데이터로 불러 본다(ECharts 는 서식 결과를 HTML 로 쓴다 — `<img` 가 없어야 한다). 표·목록·띠·칩 글자는 DOM 에 글로만
들어가야 한다(그림·스크립트·틀 요소가 생기지 않는다).
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_detail import FAST, SERIES, answers

X = "<img src=x onerror=alert(1)>"
PAGES: dict[str, tuple[list[str], list[str]]] = {
    "overview.js": (
        [
            "/api/admin/aws",
            "/api/admin/alerts",
            *SERIES,
            "/svc/api/admin/access?window=24h",
            "/svc/api/admin/access?window=7d",
        ],
        ["ws", "visit", "collect", "alarms", "cost", "traffic"],
    ),
    "server.js": (
        ["/api/admin/aws", "/api/admin/alerts", *SERIES],
        ["cpu", "net", "credit", "canary", "checks", "collect", "alarms", "cost"],
    ),
    "traffic.js": (
        [f"/svc/api/admin/access?window={w}" for w in ("24h", "7d", "30d")],
        [
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
    ),
    "visitors.js": (
        ["/svc/api/admin/access/ips?window=24h&sort=requests&limit=200&offset=0"],
        [],
    ),
    "insight.js": (
        [
            "/svc/api/admin/clarity",
            *[f"/svc/api/admin/clarity/history?days={d}" for d in (7, 30, 90)],
            "/svc/api/admin/access?window=30d",
        ],
        ["sessions", "signals", "match", "pages", "countries", "dims", "refs"],
    ),
}

TIP_TEXT = {"overview.js", "server.js", "traffic.js"}
DOM_TEXT = {"server.js", "traffic.js", "visitors.js", "insight.js"}

SCENARIO = r"""
const respond = (url) => input.answers[url] || [404, { detail: 'Not Found' }]
const v = setup({ respond, buttons: { detail: input.details } })
document.getElementById('sheet').hidden = true
await page(input.page)
await flush()
const first = (ser) => {
  const d = Array.isArray(ser.data) ? ser.data.find((x) => x != null) : undefined
  return d && typeof d === 'object' && !Array.isArray(d) ? d.value : d
}
const html = []
const tooltips = (tag) => {
  for (const [id, chart] of v.s.charts) {
    const o = chart.options.at(-1)
    const f = o?.tooltip?.formatter
    if (typeof f !== 'function') continue
    const series = o.series || []
    if (o.tooltip.trigger === 'axis') {
      html.push([`${tag}:${id}`, f(series.map((ser, i) => ({ seriesIndex: i, seriesName: ser.name ?? input.x, dataIndex: 0, value: first(ser), data: first(ser), axisValue: 0 })))])
    } else {
      series.forEach((ser, i) => {
        const data = Array.isArray(ser.data) ? ser.data : []
        data.slice(0, 6).forEach((d, j) => html.push([`${tag}:${id}`, f({ seriesIndex: i, dataIndex: j, name: d?.name ?? input.x, value: d && typeof d === 'object' && !Array.isArray(d) ? d.value : d, data: d })]))
        ;(ser.links || []).slice(0, 6).forEach((d) => html.push([`${tag}:${id}`, f({ seriesIndex: i, dataType: 'edge', data: d, value: d.value })]))
      })
    }
  }
}
tooltips('page')
for (const d of input.details) {
  v.click('detail', d)
  await flush()
  tooltips(d)
  document.getElementById('sheet-close').fire('click')
  await flush()
}
if (input.page === 'visitors.js') {
  const rows = document.getElementById('tbl-bands').kids[1]?.kids || []
  // 글자를 넣은 대역은 대역 꼴이 아니라 열리지 않는다 — 꼴이 맞는 대역의 판(경로·브라우저 정보에 글자가 든다)
  rows.find((tr) => !tr.kids[0].textContent.includes('<img') && tr.kids[0].textContent.includes('/'))?.fire('click')
  await flush()
  tooltips('ip')
}
const tagsOf = (n) => (typeof n === 'string' ? [] : [n.tag, ...n.kids.flatMap(tagsOf)])
out.tags = [...new Set([...v.byId.values()].flatMap(tagsOf))]
out.html = html
out.text = [...v.byId.values()].map((n) => n.textContent).join('\n')
"""


@pytest.mark.parametrize("page", list(PAGES))
def test_feed_text_never_becomes_html_in_tooltips_tables_or_the_page(page: str) -> None:
    feeds, details = PAGES[page]
    urls = [*FAST, *feeds]
    payload: dict[str, Any] = {
        "page": page,
        "details": details,
        "x": X,
        "answers": answers("xss", urls),
    }
    if page == "visitors.js":
        bands = payload["answers"][feeds[0]][1]["bands"]
        for band in bands[:3]:
            url = f"/svc/api/admin/access/ip?band={band['band'].replace('/', '%2F').replace(':', '%3A')}&window=24h"
            payload["answers"].update(answers("xss", [url]))
    got = run_admin(SCENARIO, payload)
    for chart, html in got["html"]:
        assert "<img" not in html, (chart, html)
    escaped = [
        chart
        for chart, html in got["html"]
        if "&lt;img src=x onerror=alert(1)&gt;" in html
    ]
    # 피드 글자가 실제로 툴팁(차트에만 그리는 이름 — 원천·경보·흐름 마디)과 DOM(표·목록·알약)에 들어갔다 — 빈 시험이 아니다
    if page in TIP_TEXT:
        assert escaped, page
    if page in DOM_TEXT:
        assert X in got["text"], page
    assert not {"img", "script", "iframe", "object", "embed"} & set(got["tags"]), got[
        "tags"
    ]
