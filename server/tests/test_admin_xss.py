"""관리자 화면 v3 — 피드 글자가 HTML 이 되지 않는가 (스펙 064 §3.1 보안·§4 툴팁 서식 확인).

글자가 들어갈 수 있는 모든 칸(경로·출처·나라·경보 이름·알림 글·상자 이름·거래소 이름…)에 `<img onerror>` 를 넣은 가짜
피드로 세 페이지를 그린 뒤, 기록된 차트 option 마다 툴팁 서식(formatter)을 그 차트의 실제 데이터로 불러 본다.
ECharts 는 툴팁 서식의 결과를 HTML 로 쓰므로, 결과에 `<img` 가 없어야 한다(글자는 encodeHTML 을 지난다).
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin

X = "<img src=x onerror=alert(1)>"
NOW = 1_791_525_610_000
SEC = NOW // 1000
END = SEC - SEC % 300
PTS = [[END - 900, 5], [END - 600, 7], [END - 300, 9]]
ONE = {"cpu": PTS, "mem": PTS, "disk": PTS, "netIn": PTS, "netOut": PTS, "ebsRead": PTS}
SERIES = {
    **ONE,
    "ebsWrite": PTS,
    "creditBalance": PTS,
    "creditUsage": PTS,
    "surplusCharged": PTS,
    "statusFailed": [[END - 600, 1]],
}

FEEDS = {
    "/api/health": [200, {"status": X}],
    "/svc/api/health": [200, {"status": "ok"}],
    "/svc/api/admin/status": [200, {"wsConnections": 1, "redis": X, "influx": "ok"}],
    "/api/health/collect": [
        200,
        {
            "successRate1h": 98,
            "exchanges": [
                {
                    "exchange": X,
                    "state": X,
                    "lastSuccessAt": NOW,
                    "successRate1h": 98,
                    "openOutage": {"startedAt": NOW - 60_000},
                }
            ],
            "outages": [
                {
                    "exchange": X,
                    "kind": X,
                    "startedAt": NOW - 60_000,
                    "endedAt": None,
                    "count": 2,
                    "statusCode": 500,
                    "message": X,
                }
            ],
        },
    ],
    "/api/admin/aws": [
        200,
        {
            "alarms": {
                "state": "ok",
                "counts": {"alarm": 1},
                "items": [{"name": X, "state": "ALARM", "reason": X}],
            },
            "canary": {"state": "ok", "ok": False, "lastRunAt": NOW},
            "budget": {
                "state": "ok",
                "items": [
                    {
                        "name": X,
                        "timeUnit": "MONTHLY",
                        "limit": 10,
                        "actual": 11,
                        "forecast": 12,
                        "unit": X,
                    }
                ],
            },
        },
    ],
    "/api/admin/aws/series": [
        200,
        {
            "state": "ok",
            "fetchedAt": NOW,
            "refreshSec": 300,
            "range": "24h",
            "periodSec": 300,
            "startTs": END - 86_400,
            "endTs": END,
            "boxes": [{"box": X, "series": SERIES}, {"box": "data", "series": SERIES}],
            "wsClients": PTS,
            "canary": {"errors": [[END - 600, 2]], "durationMs": PTS},
        },
    ],
    "/api/admin/alerts": [
        200,
        {
            "slack": {"state": "ok"},
            "alarms": {"state": "ok"},
            "items": [
                {
                    "source": "slack",
                    "at": NOW - 5000,
                    "text": X,
                    "role": X,
                    "key": X,
                    "delivered": False,
                },
                {
                    "source": "alarm",
                    "at": NOW - 6000,
                    "alarm": X,
                    "fromState": X,
                    "toState": X,
                    "text": X,
                },
            ],
        },
    ],
    "/svc/api/admin/clarity": [
        200,
        {
            "state": "ok",
            "fetchedAt": NOW,
            "refreshSec": 14400,
            "numOfDays": 1,
            "traffic": {"sessions": 3},
            "summary": {
                "scrollDepth": 40,
                "activeSec": 9,
                "signals": {"deadClick": {"sessionPct": 5, "sessions": 1, "count": 1}},
            },
        },
    ],
}
ROWS3 = [[X, 1, 3], ["(기타)", 1, 2]]
ACCESS = {
    "state": "ok",
    "fetchedAt": NOW,
    "refreshSec": 60,
    "window": "7d",
    "windows": ["24h", "7d", "30d"],
    "gateAt": NOW - 10 * 86_400_000,
    "startTs": SEC - 7 * 86_400,
    "endTs": SEC,
    "totals": {"requests": 10, "humanPages": 5, "jsViews": 2},
    "hourly": [
        {"ts": SEC - 3600, "humanPages": 3, "jsViews": 1, "errors": 1, "wsErrors": 1}
    ],
    "status": {"2xx": 5, "5xx": 1, "ws5xx": 1},
    "recent5xx": [{"ts": SEC - 10, "path": X, "status": 502}],
    "ws": {"durations": {"lt10s": 1}},
    "classes": {
        "browser": {"requests": 5, "pages": 3},
        "scanner": {"requests": 5, "pages": 2},
    },
    "paths": [[X, 4]],
    "referrers": [[X, 3]],
    "utmSources": [[X, 2]],
    "tabs": [[X, 1]],
    "devices": [[X, 1]],
    "browsers": [[X, 1]],
    "visitors": {
        "state": "ok",
        "sinceTs": SEC - 7 * 86_400,
        "confirmed": 1,
        "shaped": 3,
        "returning": 0,
        "capped": False,
        "days": [{"ts": SEC - 86_400, "confirmed": 1, "shaped": 3, "returning": 1}],
        "channels": ROWS3,
        "devices": ROWS3,
        "os": ROWS3,
        "browsers": ROWS3,
        "inApp": ROWS3,
        "flows": [[X, X, X, 1, 3], ["(기타)", "(기타)", "(기타)", 1, 2]],
        "entries": ROWS3,
        "exits": ROWS3,
        "depthPages": {"1": [1, 3]},
    },
    "geo": {
        "state": "ok",
        "month": "2026-10",
        "countries": ROWS3,
        "networks": ROWS3,
        "ipv6": 0,
    },
}

SCENARIO = r"""
const respond = (url) => {
  const [path] = url.split('?')
  if (path === '/svc/api/admin/access') return [200, { ...input.access, window: new URLSearchParams(url.split('?')[1]).get('window') }]
  return input.feeds[path] || [404, { detail: 'Not Found' }]
}
const v = setup({ respond, buttons: { range: ['24h'], window: ['24h', '7d', '30d'] } })
await page(input.page)
await flush()
if (input.page === 'traffic.js') { v.click('window', '7d'); await flush() }
const first = (ser) => {
  const d = Array.isArray(ser.data) ? ser.data[0] : undefined
  return d && typeof d === 'object' && !Array.isArray(d) ? d.value : d
}
const html = []
for (const [id, chart] of v.s.charts) {
  const o = chart.options.at(-1)
  const f = o?.tooltip?.formatter
  if (typeof f !== 'function') continue
  const series = o.series || []
  if (o.tooltip.trigger === 'axis') {
    html.push([id, f(series.map((ser, i) => ({ seriesIndex: i, seriesName: ser.name ?? input.x, dataIndex: 0, value: first(ser), data: first(ser) })))])
  } else {
    series.forEach((ser, i) => {
      const data = Array.isArray(ser.data) ? ser.data : []
      data.slice(0, 6).forEach((d, j) => html.push([id, f({ seriesIndex: i, dataIndex: j, name: d?.name ?? input.x, value: d && typeof d === 'object' && !Array.isArray(d) ? d.value : d, data: d })]))
      ;(ser.links || []).slice(0, 6).forEach((d) => html.push([id, f({ seriesIndex: i, dataType: 'edge', data: d, value: d.value })]))
    })
  }
}
// DOM 에 그림·스크립트 요소가 생기지 않았다(글자는 textContent 로만)
const walk = (n) => (typeof n === 'string' ? [] : [n.tag, ...n.kids.flatMap(walk)])
out.tags = [...new Set([...v.byId.values()].flatMap(walk))]
out.html = html
out.text = [...v.byId.values()].map((n) => n.textContent).join('\n')
"""


@pytest.mark.parametrize("page", ["overview.js", "server.js", "traffic.js"])
def test_feed_text_never_becomes_html_in_tooltips_or_the_page(page: str) -> None:
    got: dict[str, Any] = run_admin(
        SCENARIO, {"page": page, "feeds": FEEDS, "access": ACCESS, "x": X}
    )
    assert got["html"], page
    for chart, html in got["html"]:
        assert "<img" not in html, (chart, html)
    escaped = [
        chart
        for chart, html in got["html"]
        if "&lt;img src=x onerror=alert(1)&gt;" in html
    ]
    assert escaped, page  # 피드 글자가 실제로 툴팁에 들어갔다(빈 시험이 아니다)
    assert not {"img", "script", "iframe"} & set(got["tags"]), got["tags"]
    assert X in got["text"]  # 글자 그대로 보인다
