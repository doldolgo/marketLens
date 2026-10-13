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
    bounds,
    series_data_response,
    series_ids,
    stub_discovery,
)
from app.features.admin.tests.test_series_feed import World
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
  if (path === '/api/admin/aws/series' && url.includes('offset=prev')) return input.prevSeries && url.includes('range=24h') ? [200, input.prevSeries] : [404, { detail: 'Not Found' }]
  if (path === '/api/admin/aws/series') return input.series && url.includes('range=24h') ? [200, input.series] : [404, { detail: 'Not Found' }]
  if (path === '/svc/api/admin/access') return input.access && url.includes(`window=${input.access.window}`) ? [200, input.access] : [404, { detail: 'Not Found' }]
  if (path === '/api/admin/aws') return [200, { alarms: { state: 'unconfigured' }, metrics: { state: 'unconfigured' }, canary: { state: 'unconfigured' }, budget: { state: 'unconfigured' } }]
  const q = new URLSearchParams(url.split('?')[1] || '')
  if (path === '/svc/api/admin/access/ips') return input.ips ? [200, input.ips] : [404, { detail: 'Not Found' }]
  if (path === '/svc/api/admin/access/ip') return input.ip?.[q.get('band')] ? [200, input.ip[q.get('band')]] : [400, { error: { code: 'bad_band', message: 'x', detail: null } }]
  if (path === '/svc/api/admin/clarity' && input.clarity) return [200, input.clarity]
  if (path === '/svc/api/admin/clarity/history') return input.history?.[q.get('days')] ? [200, input.history[q.get('days')]] : [404, { detail: 'Not Found' }]
  return input.feeds[path] || [404, { detail: 'Not Found' }]
}
for (const [name, search, details] of input.pages) {
  const v = setup({ respond, search, buttons: { detail: details } })
  document.getElementById('sheet').hidden = true
  const made = new Map()
  globalThis.echarts = {
    init: (el, theme) => { const c = real.init(null, theme, { renderer: 'svg', ssr: true, width: 720, height: 320 }); made.set(el.id, c); return c },
    registerTheme: real.registerTheme,
    connect: real.connect,
    format: real.format,
    graphic: real.graphic,
  }
  await page(name, `?c=${name}`)
  await flush()
  const failed = []
  const render = (tag) => {
    for (const [id, chart] of made) {
      if (chart.isDisposed()) continue
      try { chart.renderToSVGString() } catch (err) { failed.push([`${tag}:${id}`, String(err).slice(0, 300)]) }
    }
  }
  render('page')
  const option = (id) => made.get(id)?.getOption()
  const got = {
    tags: Object.fromEntries([...v.byId.entries()].filter(([id, n]) => id.startsWith('t-') && n.textContent).map(([id, n]) => [id, n.textContent])),
    charts: [...made.keys()],
    chips: (v.byId.get('chips')?.kids || []).map((c) => c.textContent),
    sankey: option('c-flows')?.series?.[0]?.data?.map((n) => n.name) ?? null,
    cpu: option('c-cpu')?.series?.map((s) => s.name) ?? null,
    texts: Object.fromEntries(['v-visitors', 'v-pages', 'v-errors', 'v-bots', 'v-count', 'v-sessions', 'v-scroll'].map((id) => [id, v.text(id)])),
    sheets: {},
  }
  // 상세 판 — 진짜 응답으로 그래프(진짜 dataZoom 으로 보이는 구간)·통계·원자료가 찬다. 대역 판은 표의 행을 눌러 연다
  const sheet = (d) => ({ title: v.text('sheet-title'), tag: v.text('t-sheet'), chart: made.has('c-sheet') && !made.get('c-sheet').isDisposed(), stats: v.text('sheet-stat-tbl'), count: v.text('sheet-count'), raw: v.text('sheet-tbl'), note: v.text('sheet-note') })
  const opens = details.map((d) => [d, () => v.click('detail', d)])
  if (name === 'visitors.js') {
    got.bands = v.text('tbl-bands')
    for (const b of input.openBands || []) opens.push([b, () => (document.getElementById('tbl-bands').kids[1]?.kids || []).find((tr) => tr.kids[0].textContent.startsWith(b))?.fire('click')])
  }
  for (const [d, open] of opens) {
    open()
    await flush()
    render(d)
    got.sheets[d] = sheet(d)
    document.getElementById('sheet-close').fire('click')
    await flush()
  }
  got.failed = failed
  got.errors = [...v.byId.entries()].filter(([id, n]) => id.startsWith('t-') && n.textContent === '표시 오류').map(([id]) => id)
  out[name] = got
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


async def _real_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    """068 — 진짜 피드 코드(`AdminFeeds.aws_series`)의 24시간 창과 그 이전 기간(`offset=prev` — 끝 = 지금 창의 시작)."""
    out = []
    for offset in (None, "prev"):
        clients = Clients("cloudwatch")
        start, end = bounds(86_400, 300)
        if offset:
            start, end = start - 86_400, start
        stub_discovery(clients)
        clients["cloudwatch"].add_response(
            "get_metric_data", series_data_response(series_ids(), start, end, 300)
        )
        body = await World(clients).feeds.aws_series("24h", offset)
        body["fetchedAt"] = int(time.time() * 1000)  # 가짜 시계면 '오래됨'
        out.append(body)
    return out[0], out[1]


def _lines(t0: float) -> list[str]:
    """방문자 셋(검색·직접·소셜 — 대역 셋)·봇 하나·5xx 하나의 caddy 줄, t0 부터 4분."""
    return [
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


async def _real_access() -> dict[str, Any]:
    """038·039·062 — 위 줄로 센 7일 창."""
    with tempfile.TemporaryDirectory() as directory:
        write(Path(directory), "access.log", _lines(AFTER - 2 * HOUR))
        return await Feeds(Path(directory), AFTER).get("7d")


def test_real_series_feed_draws_the_server_and_overview_pages() -> None:
    """063 응답: 서버 시간 카드 아홉과 개요 상자 셋이 그려지고, 마지막 칸의 상태 검사 실패(빨강)·메모리 사용 87.65%(주황)를
    상자마다 칩으로 낸다(최근 15분 평균 — 064 §3.2). 상세 판(066 §3.2)은 같은 응답으로 그래프·통계·원자료를 채운다."""
    pages = [
        ["server.js", "", ["cpu", "mem", "credit", "checks", "net"]],
        ["overview.js", "", ["cpu"]],
    ]
    feeds = {"pages": pages, "feeds": PLAIN, "series": _real_series(), "access": None}
    got = run_admin(SCENARIO, feeds)
    server, overview = got["server.js"], got["overview.js"]
    for page in (server, overview):
        assert page["failed"] == [] and page["errors"] == [], page
    assert server["cpu"] == ["collect", "data", "serve"]
    times = {"c-cpu", "c-net", "c-mem", "c-disk", "c-credit", "c-io", "c-ws"}
    assert times | {"c-canary", "c-checks"} <= set(server["charts"])
    assert not {k for k in server["tags"] if k in {"t-cpu", "t-net", "t-checks"}}
    for d, sheet in server["sheets"].items():
        assert sheet["chart"] and sheet["tag"] == "" and sheet["count"], (d, sheet)
    assert server["sheets"]["cpu"]["title"] == "CPU"
    for box in ("collect", "data", "serve"):
        assert box in server["sheets"]["cpu"]["stats"], server["sheets"]["cpu"]
    assert {"c-box-collect", "c-box-data", "c-box-serve"} <= set(overview["charts"])
    assert overview["sheets"]["cpu"]["chart"]
    chips = overview["chips"]
    for box in ("collect", "data", "serve"):
        assert f"{box} 상태 검사실패" in chips, chips
        assert f"{box} 메모리87.7%" in chips, chips
    # 빨강 먼저
    assert chips.index("serve 상태 검사실패") < chips.index("collect 메모리87.7%")


async def test_real_previous_window_draws_dashed_compare_lines() -> None:
    """068 `offset=prev` 응답: `?cmp=prev` 면 서버 시간 카드에 '· 이전' 점선 계열이, 상세 판 원자료에 이전 값 칸이 든다(066 §3.2 비교선)."""
    now, prev = await _real_pair()
    assert (now["offset"], prev["offset"]) == (None, "prev")
    assert prev["endTs"] == now["startTs"]
    pages = [["server.js", "?cmp=prev", ["cpu"]]]
    feeds = {"pages": pages, "feeds": PLAIN, "series": now, "prevSeries": prev}
    server = run_admin(SCENARIO, {**feeds, "access": None})["server.js"]
    assert server["failed"] == [] and server["errors"] == [], server
    boxes = ["collect", "data", "serve"]
    assert server["cpu"] == [f"{b} · 이전" for b in boxes] + boxes
    raw = server["sheets"]["cpu"]["raw"]
    assert all(f"{b} (이전)" in raw for b in boxes), raw[:200]


async def test_real_access_feed_draws_the_traffic_page() -> None:
    """038·039·062 응답(7일 창 — `?range=7d`): 흐름 마디가 들어온 길·첫·마지막 페이지 층으로 나뉘고, 큰 수·카드·상세 판이
    그 값으로 찬다."""
    access = await _real_access()
    assert (access["state"], access["window"]) == ("ok", "7d")
    pages = [
        ["traffic.js", "?range=7d", ["trend", "flows", "src", "geo", "ends", "status"]]
    ]
    got = run_admin(
        SCENARIO, {"pages": pages, "feeds": PLAIN, "series": None, "access": access}
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
    texts = traffic["texts"]
    assert texts["v-visitors"] == f"{visitors['confirmed']}~ {visitors['shaped']}"
    assert texts["v-pages"] == str(access["totals"]["humanPages"])
    assert texts["v-errors"].endswith("1")  # ■ 모양 + 수
    assert {"c-flows", "c-trend", "c-heat"} <= set(traffic["charts"])
    # DB-IP 를 받는 중(039 — 이 시험은 받지 않는다)
    assert traffic["tags"].get("t-geo") == "첫 조회 중"
    sheets = traffic["sheets"]
    assert sheets["trend"]["chart"] and sheets["flows"]["chart"], sheets
    # 날별 방문자 — 오늘(창 끝 = 지금, 정오 전) 막대도 창 안에 든다
    assert sheets["trend"]["count"] == "3 / 3행", sheets["trend"]
    assert sheets["geo"]["tag"] == "첫 조회 중", sheets["geo"]
    assert all(sheets[d]["count"] for d in ("src", "ends", "status")), sheets
