"""관리자 화면 v3 가 진짜 피드 코드의 응답을 그리는가 (스펙 064 §4 보강 — 062·063 이 main 에 든 뒤의 계약 대조).

063 의 `AwsReader.series`(botocore Stubber — `aws_fakes`)와 038·039·062 의 `VisitFeeds`(caddy 접속 줄 — `access_fakes`)가
만든 응답을 그대로 페이지에 주고 진짜 ECharts 5.5.1(SSR)로 그린다 — 예외 0·카드 '표시 오류' 0, 상태 띠는 그 값으로 센 칩.
"""

import tempfile
import time
from pathlib import Path
from typing import Any

from app.features.admin.aws import AwsReader
from app.features.admin.feeds import SERIES_KEYS
from app.features.admin.parts import Result, render
from app.features.admin.tests.access_fakes import (
    AFTER,
    HOUR,
    IPHONE,
    Feeds,
    line,
    write,
)
from app.features.admin.tests.aws_fakes import (
    END,
    NOW,
    START,
    Clients,
    series_data_response,
    series_ids,
    stub_discovery,
)
from tests.admin_dom import run_admin

OK = [200, {"status": "ok", "version": "x", "lastTickAt": 1}]
PLAIN = {
    "/api/health": OK,
    "/svc/api/health": OK,
    "/svc/api/admin/status": [
        200,
        {"wsConnections": 2, "redis": "ok", "influx": "ok", "version": "x"},
    ],
    "/api/health/collect": [
        200,
        {"successRate1h": 100, "exchanges": [], "outages": []},
    ],
    "/api/admin/alerts": [
        200,
        {"items": [], "slack": {"state": "ok"}, "alarms": {"state": "ok"}},
    ],
    "/svc/api/admin/clarity": [
        200,
        {"state": "unconfigured", "code": None, "fetchedAt": None, "refreshSec": 14400},
    ],
}

SCENARIO = r"""
const code = (await import('node:fs')).readFileSync(`${input.root}/web/admin/vendor/echarts-5.5.1.min.js`, 'utf8')
const lib = { exports: {} }
new Function('exports', 'module', code)(lib.exports, lib)
const real = lib.exports
const respond = (url) => {
  const [path] = url.split('?')
  if (path === '/api/admin/aws/series') return input.series ? [200, input.series] : [404, { detail: 'Not Found' }]
  if (path === '/svc/api/admin/access') return input.access ? [200, input.access] : [404, { detail: 'Not Found' }]
  if (path === '/api/admin/aws') return [200, { alarms: { state: 'unconfigured' }, metrics: { state: 'unconfigured' }, canary: { state: 'unconfigured' }, budget: { state: 'unconfigured' } }]
  return input.feeds[path] || [404, { detail: 'Not Found' }]
}
for (const name of input.pages) {
  const v = setup({ respond, buttons: { range: ['24h'], window: ['24h', '7d', '30d'] } })
  const made = new Map()
  globalThis.echarts = {
    init: (el) => { const c = real.init(null, null, { renderer: 'svg', ssr: true, width: 720, height: 320 }); made.set(el.id, c); return c },
    connect: real.connect,
    format: real.format,
    graphic: real.graphic,
  }
  await page(name, `?c=${name}`)
  await flush()
  if (name === 'traffic.js') { v.click('window', '7d'); await flush() }
  const failed = []
  for (const [id, chart] of made) {
    try { chart.renderToSVGString() } catch (err) { failed.push([id, String(err).slice(0, 200)]) }
  }
  const option = (id) => made.get(id)?.getOption()
  out[name] = {
    failed,
    errors: [...v.byId.entries()].filter(([id, n]) => id.startsWith('t-') && n.textContent === '표시 오류').map(([id]) => id),
    tags: Object.fromEntries([...v.byId.entries()].filter(([id, n]) => id.startsWith('t-') && n.textContent).map(([id, n]) => [id, n.textContent])),
    charts: [...made.keys()],
    chips: (v.byId.get('chips')?.kids || []).map((c) => c.textContent),
    sankey: option('c-flows')?.series?.[0]?.data?.map((n) => n.name) ?? null,
    cpu: option('c-cpu')?.series?.map((s) => s.name) ?? null,
    texts: Object.fromEntries(['v-visitors', 'v-pages', 'v-5xx', 'v-bots'].map((id) => [id, v.text(id)])),
  }
}
"""


def _real_series() -> dict[str, Any]:
    """063 — 상자 셋·시계열 열둘(첫·마지막 칸만 값, c7g collect 의 크레딧 셋은 없음)을 응답 꼴로."""
    clients = Clients("cloudwatch")
    stub_discovery(clients)
    clients["cloudwatch"].add_response(
        "get_metric_data", series_data_response(series_ids(), START, END, 300)
    )
    values = AwsReader(clients, clock=lambda: NOW, mono=lambda: 0.0).series(86_400, 300)
    # 받은 때는 지금 — 가짜 시계(NOW)로 두면 화면이 '오래됨'(refreshSec × 3 넘음)으로 칠한다
    body = render(Result("ok", None, int(time.time() * 1000), values), 300, SERIES_KEYS)
    body["range"] = "24h"
    return body


async def _real_access() -> dict[str, Any]:
    """038·039·062 — 방문자 셋(검색·직접·소셜)·봇 하나·5xx 하나의 caddy 줄로 센 7일 창."""
    t0 = AFTER - 2 * HOUR
    lines = [
        line(t0, "/", referer="https://www.google.com", ip="203.0.113.0"),
        line(t0 + 1, "/clarity.js", ip="203.0.113.0"),
        line(t0 + 60, "/app/?tab=history", ip="203.0.113.0"),
        line(t0 + 61, "/app/clarity.js", ip="203.0.113.0"),
        line(t0 + 120, "/privacy", ua=IPHONE, ip="198.51.100.0"),
        line(t0 + 121, "/clarity.js", ua=IPHONE, ip="198.51.100.0"),
        line(t0 + 180, "/kimp-chart", referer="https://t.co", ip="192.0.2.0"),
        line(t0 + 200, "/wp-login.php", ua="curl/8.0", ip="192.0.2.0"),
        line(t0 + 240, "/api/history/events", status=502, ip="203.0.113.0"),
    ]
    with tempfile.TemporaryDirectory() as directory:
        write(Path(directory), "access.log", lines)
        return await Feeds(Path(directory), AFTER).get("7d")


def test_real_series_feed_draws_the_server_and_overview_pages() -> None:
    """063 응답: 서버 시간 카드 아홉과 개요 상자 셋이 그려지고, 마지막 칸의 상태 검사 실패(빨강)·메모리 사용 87.65%(주황)를
    상자마다 칩으로 낸다(최근 15분 평균 — §3.2)."""
    got = run_admin(
        SCENARIO,
        {
            "pages": ["server.js", "overview.js"],
            "feeds": PLAIN,
            "series": _real_series(),
            "access": None,
        },
    )
    server, overview = got["server.js"], got["overview.js"]
    for page in (server, overview):
        assert page["failed"] == [] and page["errors"] == [], page
    assert server["cpu"] == ["collect", "data", "serve"]
    assert {
        "c-cpu",
        "c-net",
        "c-mem",
        "c-disk",
        "c-credit",
        "c-io",
        "c-ws",
        "c-canary",
        "c-checks",
    } <= set(server["charts"])
    assert not {
        k: v for k, v in server["tags"].items() if k in {"t-cpu", "t-net", "t-checks"}
    }
    chips = overview["chips"]
    for box in ("collect", "data", "serve"):
        assert f"{box} 상태 검사실패" in chips, chips
        assert f"{box} 메모리87.7%" in chips, chips
    assert chips.index("serve 상태 검사실패") < chips.index(
        "collect 메모리87.7%"
    )  # 빨강 먼저


async def test_real_access_feed_draws_the_traffic_page() -> None:
    """038·039·062 응답(7일 창): 흐름 마디가 들어온 길·첫·마지막 페이지 층으로 나뉘고, 큰 수·카드가 그 값으로 찬다."""
    access = await _real_access()
    assert (access["state"], access["window"]) == ("ok", "7d")
    got = run_admin(
        SCENARIO,
        {"pages": ["traffic.js"], "feeds": PLAIN, "series": None, "access": access},
    )
    traffic = got["traffic.js"]
    assert traffic["failed"] == [] and traffic["errors"] == [], traffic
    assert set(traffic["sankey"]) == {
        "0:direct",
        "0:search",
        "0:social",
        "1:privacy",
        "1:landing",
        "1:kimp-chart",
        "2:privacy",
        "2:app-history",
        "2:kimp-chart",
    }
    visitors = access["visitors"]
    assert (
        traffic["texts"]["v-visitors"]
        == f"{visitors['confirmed']} ~ {visitors['shaped']}"
    )
    assert traffic["texts"]["v-5xx"] == "1" and traffic["texts"]["v-pages"] == str(
        access["totals"]["humanPages"]
    )
    assert {
        "c-flows",
        "c-ends",
        "c-depth",
        "c-trend",
        "c-heat",
        "c-classes",
        "c-status",
    } <= set(traffic["charts"])
    assert (
        traffic["tags"].get("t-countries") == "첫 조회 중"
    )  # DB-IP 를 받는 중(039 — 이 시험은 받지 않는다)
