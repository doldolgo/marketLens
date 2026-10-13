"""관리자 화면 v3 의 가짜 피드 서버(`web/scripts/admin-fake-feeds.py`, 스펙 064 §4 설계 세션 도구)와 페이지가 어긋나지 않는가.

가짜 서버의 경우마다 피드 응답을 만들어 세 페이지를 node 로 그리고, 어느 카드도 '표시 오류'(그리다 예외)가 나지 않는지,
정상 경우는 머리의 점이 '정상'·문제 경우는 '문제' 인지 본다. 서버를 띄우지 않는다(함수만 부른다).
"""

import importlib.util
from types import ModuleType
from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_deploy import ROOT

PLAIN = [
    "/api/health",
    "/svc/api/health",
    "/svc/api/admin/status",
    "/api/health/collect",
    "/api/admin/aws",
    "/api/admin/alerts",
    "/svc/api/admin/clarity",
]
SCENARIO = r"""
const respond = (url) => {
  const [path, query] = url.split('?')
  const q = new URLSearchParams(query || '')
  if (path === '/api/admin/aws/series') return input.series[q.get('range')]
  if (path === '/svc/api/admin/access') return input.access[q.get('window')]
  return input.feeds[path] || [404, { detail: 'Not Found' }]
}
for (const name of ['overview.js', 'server.js', 'traffic.js']) {
  const v = setup({ respond, buttons: { range: ['6h', '24h', '7d', '30d'], window: ['24h', '7d', '30d'] } })
  await page(name, `?s=${name}`)
  await flush()
  if (name === 'server.js') { v.click('range', '30d'); await flush() }
  if (name === 'traffic.js') { v.click('window', '30d'); await flush() }
  const shown = [...v.byId.entries()].filter(([id, n]) => id.startsWith('t-') && n.textContent)
  out[name] = { errors: shown.filter(([, n]) => n.textContent === '표시 오류').map(([id]) => id), charts: v.s.charts.size, word: v.text('hdr-word') }
}
"""


def _fake() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "admin_fake", ROOT / "web/scripts/admin-fake-feeds.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "scenario", ["ok", "problem", "missing", "empty", "xss", "expired"]
)
def test_every_fake_scenario_draws_every_card_without_errors(scenario: str) -> None:
    fake = _fake()
    assert scenario in fake.SCENARIOS
    fake.state["scenario"] = scenario
    feeds = {path: list(fake.feed("GET", path, {}, {})) for path in PLAIN}
    series = {r: list(fake.series(r)) for r in ("6h", "24h", "7d", "30d")}
    access = {w: [200, fake.access(w)] for w in ("24h", "7d", "30d")}
    got: dict[str, Any] = run_admin(
        SCENARIO, {"feeds": feeds, "series": series, "access": access}
    )
    for page, result in got.items():
        assert result["errors"] == [], (page, result)
        if scenario in ("ok", "xss"):
            assert result["word"] == "정상", (page, result)
        if scenario == "problem":
            assert result["word"].startswith("문제"), (page, result)
    if scenario == "ok":
        assert [
            got[p]["charts"] for p in ("overview.js", "server.js", "traffic.js")
        ] == [12, 12, 19]
