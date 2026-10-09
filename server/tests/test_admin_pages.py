"""관리자 화면 v3 페이지의 부르기·세션·그리기 판단 (스펙 064 §4 node — 029·036·042 규칙을 옮긴 것).

페이지 스크립트(ES 모듈)를 node 에 가짜 document·fetch·location·history·echarts 와 싣는다(`tests/admin_dom.py`).
경우마다 node 하나 — 고른 값·떠 있는 호출이 다른 경우로 새지 않게. 화면 모양은 설계 세션이 브라우저로 본다.
"""

from typing import Any
from urllib.parse import urlsplit

from tests.admin_dom import run_admin
from tests.test_admin import _forward
from tests.test_deploy import API, COLLECTOR

NOW = 1_791_525_610_000  # 2026-10-09T06:00:10Z
SEC = NOW // 1000
OK = {"status": "ok", "version": "x", "lastTickAt": NOW}

# 피드 가짜 — 계약 모양(029·011·034·035·038·040·062·063)을 줄여 담았다
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
        {"state": "unconfigured", "code": None, "fetchedAt": None, "refreshSec": 14400},
    ],
}


def _series(range_: str) -> dict:
    return {
        "state": "ok",
        "fetchedAt": NOW,
        "refreshSec": 300,
        "range": range_,
        "periodSec": 300,
        "startTs": SEC - 86_400,
        "endTs": SEC - SEC % 300,
        "boxes": [{"box": "data", "series": {"cpu": [[SEC - 600, 20]], "mem": None}}],
        "wsClients": [[SEC - 600, 3]],
        "canary": {"errors": None, "durationMs": None},
    }


def _access(window: str, windows: list[str]) -> dict:
    return {
        "state": "ok",
        "fetchedAt": NOW,
        "refreshSec": 60,
        "window": window,
        "windows": windows,
        "gateAt": NOW - 86_400_000,
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


# respond(url) — 가짜 피드 표 + 고른 값 피드(series·access 는 요청한 값으로 답하거나 input.answer 로)
RESPOND = r"""
const respond = (url, init = {}) => {
  const [path, query] = url.split('?')
  const q = new URLSearchParams(query || '')
  if (input.overrides && input.overrides[url]) return input.overrides[url]
  if (path === '/api/admin/aws/series') return [200, { ...input.series, range: input.answer || q.get('range') }]
  if (path === '/svc/api/admin/access') return [200, { ...input.access, window: input.answer || q.get('window') }]
  return input.feeds[path] || [404, { detail: 'Not Found' }]
}
"""


def _payload(**extra: Any) -> dict[str, Any]:
    return {
        "feeds": FEEDS,
        "series": _series("24h"),
        "access": _access("24h", ["24h", "7d", "30d"]),
        **extra,
    }


EXPECTED_CALLS = {
    "overview.js": [
        "/api/health",
        "/svc/api/health",
        "/svc/api/admin/status",
        "/api/health/collect",
        "/api/admin/aws",
        "/api/admin/aws/series?range=24h",
        "/api/admin/alerts",
        "/svc/api/admin/access?window=24h",
    ],
    "server.js": [
        "/api/health",
        "/svc/api/health",
        "/svc/api/admin/status",
        "/api/health/collect",
        "/api/admin/aws",
        "/api/admin/aws/series?range=24h",
        "/api/admin/alerts",
    ],
    "traffic.js": [
        "/api/health",
        "/svc/api/health",
        "/svc/api/admin/status",
        "/api/health/collect",
        "/svc/api/admin/access?window=24h",
        "/svc/api/admin/clarity",
    ],
}
# 관리자 nginx 가 넘기는 곳(029·034·035·063) — 시계열은 /api/ 접두 분기로 수집기에 간다(063 §3.1)
BACKEND = {
    "/api/health": (COLLECTOR, "/health"),
    "/svc/api/health": (API, "/health"),
    "/svc/api/admin/status": (API, "/admin/status"),
    "/api/health/collect": (COLLECTOR, "/health/collect"),
    "/api/admin/aws": (COLLECTOR, "/admin/aws"),
    "/api/admin/aws/series": (COLLECTOR, "/admin/aws/series"),
    "/api/admin/alerts": (COLLECTOR, "/admin/alerts"),
    "/svc/api/admin/access": (API, "/admin/access"),
    "/svc/api/admin/clarity": (API, "/admin/clarity"),
    "/api/refresh": (COLLECTOR, "/refresh"),
}

FIRST_LOAD = (
    RESPOND
    + r"""
for (const name of ['overview.js', 'server.js', 'traffic.js']) {
  const v = setup({ respond })
  await page(name, `?first=${name}`)
  await flush()
  out[name] = { calls: v.s.calls, headers: [v.text('hdr-word'), v.byId.get('hdr-state').className, v.text('updated')] }
}
"""
)


def test_each_page_calls_its_paths_once_and_every_path_has_an_admin_route() -> None:
    """§3.1 — 빠른 넷(029)과 그 페이지가 쓰는 느린 피드만, 고른 값은 쿼리 하나. 경로마다 관리자 nginx 분기가 있다."""
    got = run_admin(FIRST_LOAD, _payload())
    for name, expected in EXPECTED_CALLS.items():
        assert got[name]["calls"] == expected, name
        word, state, updated = got[name]["headers"]
        assert (word, state) == ("정상", "state ok"), name
        assert len(updated) == 8 and updated.count(":") == 2, name
    paths = {urlsplit(u).path for calls in EXPECTED_CALLS.values() for u in calls}
    assert paths | {"/api/refresh"} == set(BACKEND)
    for path, target in BACKEND.items():
        assert _forward(path) == target, path


SERVER_RANGE = (
    RESPOND
    + r"""
const v = setup({ respond, buttons: { range: ['6h', '24h', '7d', '30d'] } })
await page('server.js')
await flush()
const before = v.s.calls.length
const timers = v.s.timers
v.click('range', '7d')
await flush()
v.click('range', '7d')
await flush()
out.after = v.s.calls.slice(before)
out.timers = v.s.timers - timers
out.pressed = v.groups.range.filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset.range)
out.cpu = v.option('c-cpu')?.series?.length
"""
)


def test_server_range_button_calls_only_the_series_path() -> None:
    """§3.3 — 기간 단추는 `/api/admin/aws/series?range=` 하나만 곧바로, 같은 값을 또 누르면 부르지 않고, 주기(타이머)는 그대로."""
    got = run_admin(SERVER_RANGE, _payload())
    assert got["after"] == ["/api/admin/aws/series?range=7d"]
    assert got["timers"] == 0
    assert got["pressed"] == ["7d"]
    assert got["cpu"] == 1


TRAFFIC_WINDOW = (
    RESPOND
    + r"""
// 1. 7일 — 접속 경로만 1회, 시행 전 응답이면 7·30일 단추는 꺼진다
{
  const v = setup({ respond, buttons: { window: ['24h', '7d', '30d'] } })
  await page('traffic.js', '?case=1')
  await flush()
  const before = v.s.calls.length
  v.click('window', '7d')
  await flush()
  out.click = { after: v.s.calls.slice(before), pressed: v.groups.window.filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset.window) }
}
// 2. 붙잡은 채 24h→7d→30d — 겹친 호출 없이 끝난 뒤 30d 한 번(마지막 값)
{
  let holding = false
  const v = setup({ respond, hold: (url) => holding && url.startsWith('/svc/api/admin/access'), buttons: { window: ['24h', '7d', '30d'] } })
  await page('traffic.js', '?case=2')
  await flush()
  const before = v.s.calls.length
  holding = true
  v.click('window', '7d')
  v.click('window', '30d')
  await flush()
  const during = v.s.calls.slice(before)
  v.s.held.shift()()
  await flush()
  v.s.held.shift()()
  await flush()
  out.overlap = { during, all: v.s.calls.slice(before), max: v.s.maxInflight, left: v.s.held.length, pressed: v.groups.window.filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset.window) }
}
"""
)


def test_traffic_window_button_calls_once_and_never_overlaps() -> None:
    """§3.4·042 — 창 단추는 접속 경로 하나만 곧바로, 떠 있으면 겹쳐 부르지 않고 끝난 뒤 한 번(마지막 창)."""
    got = run_admin(TRAFFIC_WINDOW, _payload())
    assert got["click"]["after"] == ["/svc/api/admin/access?window=7d"]
    assert got["click"]["pressed"] == ["7d"]
    overlap = got["overlap"]
    assert overlap["during"] == ["/svc/api/admin/access?window=7d"]
    assert overlap["all"] == [
        "/svc/api/admin/access?window=7d",
        "/svc/api/admin/access?window=30d",
    ]
    assert overlap["max"] == 1 and overlap["left"] == 0
    assert overlap["pressed"] == ["30d"]


TRAFFIC_FALLBACK = (
    RESPOND
    + r"""
const v = setup({ respond, buttons: { window: ['24h', '7d', '30d'] } })
await page('traffic.js')
await flush()
input.answer = '24h'
v.click('window', '7d')
await flush()
out.pressed = v.groups.window.filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset.window)
out.disabled = v.groups.window.map((b) => b.disabled)
out.note = v.text('window-note')
"""
)


def test_traffic_server_answering_another_window_moves_the_pick_back() -> None:
    """042 §3.2 — 응답 window 가 요청과 다르면 고른 창을 응답 창으로, 시행 전(windows 24h 하나)이면 7·30일 단추를 끈다."""
    got = run_admin(TRAFFIC_FALLBACK, _payload(access=_access("24h", ["24h"])))
    assert got["pressed"] == ["24h"]
    assert got["disabled"] == [False, True, True]
    assert got["note"].startswith("7·30일은 ") and got["note"].endswith(" 부터")
