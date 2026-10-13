"""관리자 화면 v4 페이지의 부르기·주소 상태·세션·그리기 판단 (스펙 066 §4 node — 029·036·042·064 규칙을 이어받음).

페이지 스크립트(ES 모듈)를 node 에 가짜 document·fetch·location·history·echarts 와 싣는다(`tests/admin_dom.py`). 경우마다 node 하나.
"""

from typing import Any
from urllib.parse import urlsplit

from tests.admin_dom import run_admin
from tests.test_admin import _admin_route, _forward
from tests.test_deploy import API, COLLECTOR

NOW = 1_791_525_610_000  # 2026-10-09T06:00:10Z
SEC = NOW // 1000
OK = {"status": "ok", "version": "x", "lastTickAt": NOW}
FEEDS: dict[str, Any] = {
    "/api/health": [200, OK],
    "/svc/api/health": [200, OK],
    "/svc/api/admin/status": [
        200,
        {"wsConnections": 4, "redis": "ok", "influx": "ok", "version": "x"},
    ],
    "/api/health/collect": [
        200,
        {
            "successRate1h": 100,
            "exchanges": [{"exchange": "upbit", "state": "ok", "successRate1h": 100}],
            "outages": [],
        },
    ],
    "/api/admin/aws": [
        200,
        {
            "alarms": {"state": "ok", "counts": {"alarm": 0}, "items": []},
            "metrics": {"state": "ok"},
            "canary": {"state": "ok", "ok": True, "lastRunAt": NOW},
            "budget": {"state": "ok", "items": []},
        },
    ],
    "/api/admin/alerts": [
        200,
        {"slack": {"state": "ok"}, "alarms": {"state": "ok"}, "items": []},
    ],
    "/svc/api/admin/clarity": [
        200,
        {"state": "unconfigured", "code": None, "fetchedAt": None, "refreshSec": 21600},
    ],
}
SERIES = {
    "state": "ok",
    "fetchedAt": NOW,
    "refreshSec": 300,
    "periodSec": 300,
    "startTs": SEC - 86_400,
    "endTs": SEC - SEC % 300,
    "boxes": [
        {"box": "data", "series": {"cpu": [[SEC - 600, 20]]}},
        {"box": "collect", "series": {"cpu": [[SEC - 600, 40]]}},
    ],
    "wsClients": [[SEC - 600, 3]],
    "canary": {"errors": None, "durationMs": None},
}
ACCESS = {
    "state": "ok",
    "fetchedAt": NOW,
    "refreshSec": 60,
    "windows": ["24h", "7d", "30d"],
    "startTs": SEC - 86_400,
    "endTs": SEC,
    "totals": {"requests": 10, "humanPages": 4},
    "hourly": [{"ts": SEC - 3600, "humanPages": 4, "jsViews": 1}],
    "status": {"5xx": 0, "ws5xx": 0},
    "classes": {},
    "recent5xx": [],
    "visitors": {"state": "unconfigured", "code": "before_gate"},
    "geo": {"state": "unconfigured", "code": "before_gate"},
}

# respond(url) — 가짜 피드 표 + 고른 값 피드(series·access 는 요청한 값으로 답한다 — offset 도 그대로)
RESPOND = r"""
const respond = (url, init = {}) => {
  const [path, query] = url.split('?')
  const q = new URLSearchParams(query || '')
  if (input.overrides && input.overrides[url]) return input.overrides[url]
  if (path === '/api/admin/aws/series') {
    // offset=prev 면 같은 모양을 창 길이만큼 앞으로(068) — 점도 함께 민다
    const back = q.get('offset') === 'prev' ? { '6h': 21600, '24h': 86400, '7d': 604800, '30d': 2592000 }[q.get('range')] : 0
    const move = (pts) => (pts || []).map(([t, v]) => [t - back, v])
    const s = input.series
    return [200, { ...s, range: q.get('range'), startTs: s.startTs - back, endTs: s.endTs - back, boxes: s.boxes.map((b) => ({ ...b, series: { cpu: move(b.series.cpu) } })), wsClients: move(s.wsClients), ...(q.get('offset') ? { offset: q.get('offset') } : {}) }]
  }
  if (path === '/svc/api/admin/access') return [200, { ...input.access, window: input.answer || q.get('window') }]
  return input.feeds[path] || [404, { detail: 'Not Found' }]
}
const BUTTONS = { range: ['1h', '6h', '24h', '7d', '30d', '90d'], cmp: ['none', 'prev', 'week'], box: ['collect', 'data', 'serve'] }
"""


def _payload(**extra: Any) -> dict[str, Any]:
    return {"feeds": FEEDS, "series": SERIES, "access": ACCESS, **extra}


FAST = [
    "/api/health",
    "/svc/api/health",
    "/svc/api/admin/status",
    "/api/health/collect",
]
EXPECTED_CALLS = {
    "overview.js": [
        *FAST,
        "/api/admin/aws",
        "/api/admin/alerts",
        "/api/admin/aws/series?range=24h",
        "/svc/api/admin/access?window=24h",
    ],
    "server.js": [
        *FAST,
        "/api/admin/aws",
        "/api/admin/alerts",
        "/api/admin/aws/series?range=24h",
    ],
    "traffic.js": [*FAST, "/svc/api/admin/access?window=24h"],
    "visitors.js": [
        *FAST,
        "/svc/api/admin/access/ips?window=24h&sort=requests&limit=200&offset=0",
    ],
    "insight.js": [
        *FAST,
        "/svc/api/admin/clarity",
        "/svc/api/admin/clarity/history?days=30",
        "/svc/api/admin/access?window=30d",
    ],
    "screens.js": FAST,
}
# 관리자 nginx 가 넘기는 곳(029·034·035·063 + 067·068) — 067·068 의 정확 일치 location 은 그 스펙의 PR 이 더한다
BACKEND = {
    "/api/health": (COLLECTOR, "/health"),
    "/svc/api/health": (API, "/health"),
    "/svc/api/admin/status": (API, "/admin/status"),
    "/api/health/collect": (COLLECTOR, "/health/collect"),
    "/api/admin/aws": (COLLECTOR, "/admin/aws"),
    "/api/admin/aws/series": (COLLECTOR, "/admin/aws/series"),
    "/api/admin/alerts": (COLLECTOR, "/admin/alerts"),
    "/svc/api/admin/access": (API, "/admin/access"),
    "/svc/api/admin/access/ips": (API, "/admin/access/ips"),
    "/svc/api/admin/access/ip": (API, "/admin/access/ip"),
    "/svc/api/admin/clarity": (API, "/admin/clarity"),
    "/svc/api/admin/clarity/history": (API, "/admin/clarity/history"),
    "/api/refresh": (COLLECTOR, "/refresh"),
}
PENDING_ROUTES = {
    "/svc/api/admin/access/ips",
    "/svc/api/admin/access/ip",
    "/svc/api/admin/clarity/history",
}

FIRST_LOAD = (
    RESPOND
    + r"""
for (const name of input.pages) {
  const v = setup({ respond, buttons: BUTTONS })
  await page(name, `?first=${name}`)
  await flush()
  out[name] = { calls: v.s.calls, mark: v.byId.get('m-overview').className, updated: v.text('updated'), clock: v.text('clock') }
}
"""
)


def test_each_page_calls_its_paths_once_and_every_path_has_an_admin_route() -> None:
    """066 §3.1 — 빠른 넷(029)과 그 페이지가 쓰는 느린 피드만(주소 = 경로 + 쿼리), 경로마다 관리자 nginx 분기. 067·068 의 경로는 그 스펙이
    정확 일치 location 을 더할 때까지 정적 '/' 로 가서 404 — 화면은 '연결 안 됨'(066 §3.4)."""
    got = run_admin(FIRST_LOAD, _payload(pages=list(EXPECTED_CALLS)))
    for name, expected in EXPECTED_CALLS.items():
        assert got[name]["calls"] == expected, name
        assert got[name]["mark"] == "mark", name  # 문제 없음 — 모양 없음
        assert (
            len(got[name]["clock"]) == 8 and got[name]["clock"] == got[name]["updated"]
        ), name
    paths = {urlsplit(u).path for calls in EXPECTED_CALLS.values() for u in calls} | {
        "/svc/api/admin/access/ip",
        "/api/refresh",
    }
    assert paths == set(BACKEND)
    for path, target in BACKEND.items():
        if path in PENDING_ROUTES and _admin_route(path) == ("/",):
            continue
        assert _forward(path) == target, path


ROUND_TRIP = r"""
setup({ visible: false, respond: () => [404, {}] })
const view = await page('view.js')
const page0 = { home: '/server.html', ranges: ['6h', '24h', '7d', '30d'], range: '24h', compare: (r) => ({ prev: true, week: r !== '30d' }), filters: { box: (v) => ['collect', 'data', 'serve'].includes(v) }, details: { cpu: {}, ip: { accepts: (a) => /^\d+\.\d+\.\d+\.0\/24$/.test(a) } } }
const read = (search, hash) => view.readView(page0, search, hash)
out.cases = [
  read('', ''),
  read('?range=7d&cmp=prev&f=box:data&f=box:serve', '#detail=cpu'),
  read('?range=1y&cmp=yesterday&f=box:nope&f=nokey&f=box:data&f=box:data', '#detail=nope'),
  read('?range=30d&cmp=week', '#detail=ip%3A203.0.113.0%2F24'),
  read('?range=30d', '#detail=ip%3A203.0.113.5%2F24'),
  read('?f=:x&f=box:', '#detail=cpu&x=1'),
]
out.urls = out.cases.map((v) => view.urlOf(page0, v))
out.again = out.urls.map((u) => { const x = new URL(u, 'https://a.test'); return view.readView(page0, x.search, x.hash) })
out.extra = view.urlOf(page0, { range: '24h', cmp: 'none', f: [], detail: null }, [['relogin', '1']])
"""


def test_address_state_round_trips_and_out_of_list_values_fall_back() -> None:
    """066 §3.1·§3.4 — ?range·cmp·f · #detail 왕복(기본값은 싣지 않는다), 목록 밖·틀린 값은 오류 없이 기본값(비교는 그 기간에 열려 있을 때만),
    같은 거르기는 한 번, 값을 받는 판(ip:<대역>)은 그 판의 검사를 지나야 한다."""
    got = run_admin(ROUND_TRIP)
    assert got["cases"] == [
        {"range": "24h", "cmp": "none", "f": [], "detail": None},
        {
            "range": "7d",
            "cmp": "prev",
            "f": [["box", "data"], ["box", "serve"]],
            "detail": "cpu",
        },
        {"range": "24h", "cmp": "none", "f": [["box", "data"]], "detail": None},
        {"range": "30d", "cmp": "none", "f": [], "detail": "ip:203.0.113.0/24"},
        {"range": "30d", "cmp": "none", "f": [], "detail": None},
        {"range": "24h", "cmp": "none", "f": [], "detail": "cpu"},
    ]
    assert got["urls"] == [
        "/server.html",
        "/server.html?range=7d&cmp=prev&f=box%3Adata&f=box%3Aserve#detail=cpu",
        "/server.html?f=box%3Adata",
        "/server.html?range=30d#detail=ip%3A203.0.113.0%2F24",
        "/server.html?range=30d",
        "/server.html#detail=cpu",
    ]
    assert got["again"] == got["cases"]
    assert got["extra"] == "/server.html?relogin=1"


SERVER_RANGE = (
    RESPOND
    + r"""
const v = setup({ respond, buttons: BUTTONS })
await page('server.js')
await flush()
let before = v.s.calls.length
const timers = v.s.timers
v.click('range', '7d')
await flush()
v.click('range', '7d')
await flush()
out.range = { calls: v.s.calls.slice(before), url: v.s.cleared.at(-1), pressed: v.groups.range.filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset.range), timers: v.s.timers - timers }
before = v.s.calls.length
v.click('cmp', 'prev')
await flush()
out.prev = { calls: v.s.calls.slice(before), url: v.s.cleared.at(-1), foot: v.text('f-cpu'), cmpSeries: v.option('c-cpu')?.series?.length }
before = v.s.calls.length
v.key('x')
await flush()
out.x = { url: v.s.cleared.at(-1), calls: v.s.calls.slice(before), pressed: v.groups.cmp.filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset.cmp) }
v.click('cmp', 'week')
await flush()
v.click('range', '30d')
await flush()
out.week30 = { url: v.s.cleared.at(-1), disabled: v.groups.cmp.filter((b) => b.disabled).map((b) => [b.dataset.cmp, b.title]) }
v.key('x', { ctrlKey: true })
await flush()
out.ctrl = v.s.cleared.at(-1)
"""
)


def test_range_and_compare_rewrite_the_address_and_call_only_the_new_feeds() -> None:
    """066 §3.1 — 기간·비교를 고르면 주소(replaceState)를 고치고 새 열쇠만 곧바로(같은 값은 다시 부르지 않음, 주기 타이머 그대로).
    이전 기간 = offset=prev 한 번 더(점선 계열·바닥 한 줄), X 는 비교 켜고 끄기, 지난주가 없는 30일로 가면 비교를 거둔다."""
    got = run_admin(SERVER_RANGE, _payload())
    assert got["range"] == {
        "calls": ["/api/admin/aws/series?range=7d"],
        "url": "/server.html?range=7d",
        "pressed": ["7d"],
        "timers": 0,
    }
    assert got["prev"]["calls"] == ["/api/admin/aws/series?range=7d&offset=prev"]
    assert got["prev"]["url"] == "/server.html?range=7d&cmp=prev"
    assert got["prev"]["cmpSeries"] == 4 and got["prev"]["foot"].startswith(
        "앞 7일보다 평균 — "
    )
    assert got["x"] == {
        "url": "/server.html?range=7d",
        "calls": [],
        "pressed": ["none"],
    }
    assert got["week30"] == {
        "url": "/server.html?range=30d",
        "disabled": [["week", "이 자료는 비교 없음"]],
    }
    assert got["ctrl"] == "/server.html?range=30d"  # Ctrl+X 는 비교 단축키가 아니다
