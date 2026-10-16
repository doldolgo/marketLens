"""관리자 화면 v4 의 가짜 피드 서버(`web/scripts/admin-fake-feeds.py`, 스펙 064·066·070·074 §4 설계 세션 도구)와 페이지가 어긋나지 않는가.

가짜 서버의 경우마다(067·068 배포 전 old·대역 많음·대역 없음·069 배포 전 noaddrs·IP 많음 포함) 피드 응답을 만들어 페이지 여섯을 node 로
그리고(비교를 켠 보기·방문자 대역 별 보기도),
어느 카드도 '표시 오류'(그리다 예외)가 나지 않는지, 내비 모양이 경우와 맞는지 본다. 서버를 띄우지 않는다(함수만 부른다).
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_detail import FAST, SERIES, _fake, answers

X = "<img src=x onerror=alert(1)>"

URLS = [
    *FAST,
    "/api/admin/aws",
    "/api/admin/alerts",
    *SERIES,
    *[f"/svc/api/admin/access?window={w}" for w in ("24h", "7d", "30d")],
    "/svc/api/admin/access/ips?window=24h&sort=requests&limit=200&offset=0",
    "/svc/api/admin/access/addrs?window=24h&sort=requests&limit=200&offset=0",
    "/svc/api/admin/clarity",
    *[f"/svc/api/admin/clarity/history?days={d}" for d in (7, 30, 90)],
    "/svc/api/admin/clarity/recordings?days=3",  # 077 — 녹화 카드
    "/svc/api/admin/attention?days=1",  # 071 — Clarity 진단 줄
    "/svc/api/admin/attention?days=7",
    "/svc/api/admin/attention?days=7&page=landing",
    "/svc/api/admin/replay/live",  # 074 — 방문자 페이지 화면 기록 카드 둘
    "/svc/api/admin/replay/sessions",
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
  for (const search of ['', '?cmp=prev', ...(name === 'visitors.js' ? ['?view=band'] : [])]) {
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
      diag: v.byId.has('clarity-diag') && !v.byId.get('clarity-diag').hidden ? v.text('clarity-diag') : null,
    }
  }
}
"""


def test_scenarios_cover_the_new_feeds() -> None:
    """066·070 §4 — 가짜 서버에 067·068·069 응답과 경우(대역 많음·대역 없음·XSS·IP 많음)를 더했다. old = 067·068 배포 전,
    noaddrs = 069 만 배포 전(대역은 그대로)."""
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
        "noaddrs",
        "many",
        "noclarity",
    } == set(fake.SCENARIOS)
    assert set(fake.PRE_068) == {"old", "missing"}
    assert set(fake.PRE_069) == {"old", "missing", "noaddrs"}


@pytest.mark.parametrize(
    "scenario",
    [
        "ok",
        "problem",
        "old",
        "missing",
        "empty",
        "bands",
        "nobands",
        "xss",
        "expired",
        "noaddrs",
        "many",
        "noclarity",
    ],
)
def test_every_fake_scenario_draws_every_card_without_errors(scenario: str) -> None:
    got: dict[str, Any] = run_admin(
        SCENARIO, {"pages": PAGES, "answers": answers(scenario, URLS)}
    )
    for view, result in got.items():
        assert result["errors"] == [], (view, result)
        if scenario in (
            "ok",
            "xss",
            "bands",
            "nobands",
            "noaddrs",
            "many",
            "noclarity",
        ):
            assert result["mark"] == "mark", (
                view,
                result,
            )  # 문제 없음 — 내비 모양 없음
        if scenario == "problem":
            assert result["mark"] == "mark shape bad", (view, result)
        if scenario in ("old", "noaddrs") and view.startswith("visitors.js"):
            # 069 가 없는 서버 — IP 별은 큰 수·표가 '연결 안 됨', 대역 별은 067 이 있으면(noaddrs) 그대로 쓸 수 있다
            if view == "visitors.js?view=band":
                assert ("t-bands" in result["off"]) == (scenario == "old"), result
            else:
                assert {"t-addrs", "t-acount", "t-abot"} <= set(result["off"]), result
        if scenario == "old":  # 067·068 이 없는 서버 — 그 카드만 '연결 안 됨'
            if view.startswith("insight.js"):
                assert {"t-dims", "t-refs", "t-match"} <= set(result["off"]), result
        if scenario == "empty" and view.startswith(
            "screens.js"
        ):  # 화면 이용 통계 시행 전(before_gate)
            assert {"t-frame", "t-panel"} <= set(result["off"]), result
    # 071 §3.6 — 진단 줄은 Clarity 세션 0 이고 화면 이용 통계가 있는 운영 모양에서만
    diag = got["insight.js"]["diag"]
    if scenario == "noclarity":
        assert diag and "세션은 0" in diag and "토큰" not in diag, diag
    elif scenario not in ("problem", "empty"):
        assert diag is None, (scenario, diag)
    if scenario == "ok":
        assert {v: got[f"{v}"]["charts"] for v in PAGES} == {
            "overview.js": 9,
            "server.js": 12,
            "traffic.js": 4,
            "visitors.js": 0,
            "insight.js": 7,
            "screens.js": 0,
        }


SHADES = r"""
const respond = (url) => input.answers[url] || [404, { detail: 'Not Found' }]
const v = setup({ respond })
await page('insight.js', '?shade=1')
await flush()
const body = document.getElementById('tbl-pages').kids[1]?.kids || []
const cells = body.flatMap((tr) => tr.kids)
out.cells = cells.map((td) => [td.className, td.kids.map((k) => (typeof k === 'string' ? 'text' : k.className))])
"""


def test_clarity_page_table_shades_whole_cells() -> None:
    """066 §3.3 Clarity 페이지 × 기기 — 칸 진하기(다섯 단계)는 칸 전체 바탕이다. 글 뒤 띠(안쪽 span)로 칠하면 고른 글처럼 보였다."""
    got = run_admin(SHADES, {"answers": answers("ok", URLS)})
    shaded = [
        cls
        for cls, _ in got["cells"]
        if any(f"q{i}" in cls.split() for i in range(1, 6))
    ]
    assert len(shaded) >= 5, got["cells"][:12]
    assert all(kids in ([], ["text"]) for _, kids in got["cells"]), got["cells"][:12]


def test_attention_answers_cover_page_or_not_300_keys_mobile_gate_and_xss() -> None:
    """065 §4 — 가짜 서버의 attention 응답: page 없음(행만 — 요소 없음)·page 있음(그 화면 행 + 요소), bands 는 랜딩 요소 300(그중 (기타)),
    휴대폰 행은 마우스 올림 0, empty 는 before_gate(행 빔), problem 은 error·redis, xss 는 `<img onerror>` 열쇠."""

    def att(scenario: str, **query: str) -> dict:
        got = _fake(scenario).feed(
            "GET", "/svc/api/admin/attention", {k: [v] for k, v in query.items()}, {}
        )
        assert got and got[0] == 200
        return got[1]

    whole = att("ok", days="7")
    assert (
        whole["state"] == "ok"
        and len(whole["rows"]) > 10
        and all("elements" not in r for r in whole["rows"])
    )
    landing = att("ok", days="7", page="landing")
    assert {r["page"] for r in landing["rows"]} == {"landing"} and all(
        r["elements"] for r in landing["rows"]
    )
    mobile = next(r for r in landing["rows"] if r["device"] == "mobile")
    assert all(e["hoverMs"] == 0 for e in mobile["elements"])
    many = att("bands", days="7", page="landing")
    assert all(
        len(r["elements"]) == 300
        and sum(e["key"] == "(기타)" for e in r["elements"]) == 1
        for r in many["rows"]
    )
    gate = att("empty", days="7")
    assert (gate["state"], gate["rows"]) == ("before_gate", [])
    broken = att("problem", days="7")
    assert (broken["state"], broken["code"], broken["rows"]) == ("error", "redis", [])
    keys = {
        e["key"]
        for r in att("xss", days="7", page="landing")["rows"]
        for e in r["elements"]
    }
    assert "<img src=x onerror=alert(1)>" in keys


def _replay(scenario: str, path: str, **query: str) -> tuple[int, dict]:
    got = _fake(scenario).feed(
        "GET", f"/svc/api/admin/replay/{path}", {k: [v] for k, v in query.items()}, {}
    )
    assert got
    return got


def test_replay_answers_follow_the_073_contract_in_every_scenario() -> None:
    """074 §4 — 가짜 서버의 화면 기록 피드 셋: ok 는 지금 보는 중 하나(60초 안)·지난 조각(랜딩 PC·대시보드 휴대폰 대본 + 9)·조각은
    600 묶음씩 next·more, 없는 조각 meta null, 꼴이 틀리면 400 bad_sid·bad_ip. 069 전(old·missing·noaddrs)은 404, empty 는 연결 안 됨,
    problem 은 Redis 실패, nobands 는 조각 없음, many 는 지금 12·지난 200(상한), xss 는 IP·화면·영역 이름에 `<img onerror>`."""
    status, live = _replay("ok", "live")
    assert status == 200 and live["state"] == "ok" and len(live["rows"]) == 1
    (now,) = live["rows"]
    assert now["sid"] == "a1b2c3d4e5f60718" and live["now"] - now["last"] <= 60_000
    assert set(now) >= {
        "sid",
        "ip",
        "dev",
        "page",
        "pages",
        "now",
        "first",
        "last",
        "n",
        "clicks",
    }
    status, sessions = _replay("ok", "sessions")
    rows = sessions["rows"]
    assert status == 200 and len(rows) == 12
    assert [r["last"] for r in rows] == sorted((r["last"] for r in rows), reverse=True)
    dash = next(r for r in rows if r["sid"] == "c0ffee0000000002")
    assert (
        dash["pages"] == ["app-spread", "app-history", "app-gap"]
        and dash["dev"] == "mobile"
    )
    status, mine = _replay("ok", "sessions", ip=dash["ip"])
    assert status == 200 and {r["ip"] for r in mine["rows"]} == {dash["ip"]}
    status, one = _replay("ok", "session", sid="c0ffee0000000001")
    assert status == 200 and one["meta"]["n"] == len(one["batches"]) == one["next"]
    assert one["more"] is False and one["meta"]["clicks"] == 4
    kinds = {e[1] for b in one["batches"] for e in b["ev"]}
    assert kinds == {"m", "c", "s", "r", "v", "a"}
    assert [b["seq"] for b in one["batches"]] == list(range(len(one["batches"])))
    ats = [b["at"] for b in one["batches"]]
    assert ats == sorted(set(ats))
    # 지금 보는 중은 600 묶음씩 — 두 번째 쪽은 after=next
    _, first = _replay("ok", "session", sid="a1b2c3d4e5f60718")
    if first["more"]:
        _, second = _replay(
            "ok", "session", sid="a1b2c3d4e5f60718", after=str(first["next"])
        )
        assert second["batches"][0]["seq"] == first["next"]
    assert _replay("ok", "session", sid="0000000000000000")[1]["meta"] is None
    assert _replay("ok", "session", sid="nope")[1]["error"]["code"] == "bad_sid"
    assert _replay("ok", "sessions", ip="nope")[1]["error"]["code"] == "bad_ip"
    for scenario in ("old", "missing", "noaddrs"):
        assert _replay(scenario, "live")[0] == 404
    assert _replay("empty", "live")[1]["state"] == "unconfigured"
    assert _replay("problem", "session", sid="c0ffee0000000001")[1]["code"] == "redis"
    assert _replay("nobands", "sessions")[1]["rows"] == []
    assert len(_replay("many", "live")[1]["rows"]) == 12
    assert len(_replay("many", "sessions")[1]["rows"]) == 200
    xss = _replay("xss", "sessions")[1]["rows"]
    assert all(X in r["ip"] and X in r["page"] for r in xss)
    areas = {
        e[-1]
        for b in _replay("xss", "session", sid="c0ffee0000000001")[1]["batches"]
        for e in b["ev"]
        if e[1] == "c"
    }
    assert areas == {X}
