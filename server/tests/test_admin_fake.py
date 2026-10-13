"""관리자 화면 v4 의 가짜 피드 서버(`web/scripts/admin-fake-feeds.py`, 스펙 064·066 §4 설계 세션 도구)와 페이지가 어긋나지 않는가.

가짜 서버의 경우마다(067·068 배포 전 old·대역 많음·대역 없음 포함) 피드 응답을 만들어 페이지 여섯을 node 로 그리고(비교를 켠 보기도),
어느 카드도 '표시 오류'(그리다 예외)가 나지 않는지, 내비 모양이 경우와 맞는지 본다. 서버를 띄우지 않는다(함수만 부른다).
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_detail import FAST, SERIES, _fake, answers

URLS = [
    *FAST,
    "/api/admin/aws",
    "/api/admin/alerts",
    *SERIES,
    *[f"/svc/api/admin/access?window={w}" for w in ("24h", "7d", "30d")],
    "/svc/api/admin/access/ips?window=24h&sort=requests&limit=200&offset=0",
    "/svc/api/admin/clarity",
    *[f"/svc/api/admin/clarity/history?days={d}" for d in (7, 30, 90)],
]
PAGES = [
    "overview.js",
    "server.js",
    "traffic.js",
    "visitors.js",
    "insight.js",
    "screens.js",
]
SCENARIO = r"""
const respond = (url) => input.answers[url] || [404, { detail: 'Not Found' }]
for (const name of input.pages) {
  for (const search of ['', '?cmp=prev']) {
    const v = setup({ respond, search })
    await page(name, `?s=${name}${search}`)
    await flush()
    const shown = [...v.byId.entries()].filter(([id, n]) => id.startsWith('t-') && n.textContent)
    out[`${name}${search}`] = {
      errors: shown.filter(([, n]) => n.textContent === '표시 오류').map(([id]) => id),
      off: shown.filter(([, n]) => n.textContent === '연결 안 됨').map(([id]) => id),
      charts: v.s.charts.size,
      mark: document.getElementById('m-overview').className,
      calls: v.s.calls.length,
    }
  }
}
"""


def test_scenarios_cover_the_new_feeds() -> None:
    """066 §4 — 가짜 서버에 067·068 응답과 경우(대역 많음·대역 없음·XSS)를 더했다. old = 067·068 배포 전."""
    fake = _fake("ok")
    assert {
        "ok",
        "problem",
        "old",
        "missing",
        "empty",
        "bands",
        "nobands",
        "xss",
        "expired",
    } == set(fake.SCENARIOS)
    assert set(fake.PRE_068) == {"old", "missing"}


@pytest.mark.parametrize(
    "scenario",
    ["ok", "problem", "old", "missing", "empty", "bands", "nobands", "xss", "expired"],
)
def test_every_fake_scenario_draws_every_card_without_errors(scenario: str) -> None:
    got: dict[str, Any] = run_admin(
        SCENARIO, {"pages": PAGES, "answers": answers(scenario, URLS)}
    )
    for view, result in got.items():
        assert result["errors"] == [], (view, result)
        if scenario in ("ok", "xss", "bands", "nobands"):
            assert result["mark"] == "mark", (
                view,
                result,
            )  # 문제 없음 — 내비 모양 없음
        if scenario == "problem":
            assert result["mark"] == "mark shape bad", (view, result)
        if scenario == "old":  # 067·068 이 없는 서버 — 그 카드만 '연결 안 됨'
            if view.startswith("visitors.js"):
                assert "t-bands" in result["off"], result
            if view.startswith("insight.js"):
                assert {"t-dims", "t-refs", "t-match"} <= set(result["off"]), result
    if scenario == "ok":
        assert {v: got[f"{v}"]["charts"] for v in PAGES} == {
            "overview.js": 9,
            "server.js": 12,
            "traffic.js": 4,
            "visitors.js": 0,
            "insight.js": 7,
            "screens.js": 0,
        }
