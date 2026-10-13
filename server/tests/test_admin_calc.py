"""관리자 화면 v3 의 계산 — 큰 수·흐름 그림·바이트 단위·툴팁 글자·이름표 (스펙 064 §4 node).

`web/admin` 의 모듈을 node 에 가짜 document 와 싣고(보이지 않는 탭 — 요청 0) 공개한 계산 함수만 부른다.
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_rules import NOW

SCENARIO = r"""
setup({ visible: false, respond: () => [404, {}] })
const common = await page('common.js')
const charts = await page('charts.js')
const overview = await page('overview.js')
const traffic = await page('traffic.js')
const now = input.now
out.bytes = [0, 512, 1023, 1024, 1536, 10240, 1048576, -2048, 5e12, null].map(common.bytes)
const day = (d) => ({ ts: d, confirmed: 3, shaped: 9 })
const kst0 = Math.floor((input.now / 1000 + 32400) / 86400) * 86400 - 32400
out.today = [
  overview.todayVisitors({ endTs: input.now / 1000, visitors: { state: 'ok', days: [day(kst0 - 86400), { ts: kst0, confirmed: 2, shaped: 7 }] } }),
  overview.todayVisitors({ endTs: input.now / 1000, visitors: { state: 'ok', days: [day(kst0 - 86400)] } }),
  overview.todayVisitors({ endTs: input.now / 1000, visitors: { state: 'unconfigured', code: 'before_gate' } }),
]
const top = Math.floor(now / 3600000) * 3600000
const rate = overview.hourlySuccess({ exchanges: [{}, {}], outages: [{ startedAt: top - 3 * 3600000, endedAt: top - 3 * 3600000 + 1800000 }, { startedAt: top - 600000, endedAt: null }] }, now)
out.success = { n: rate.length, early: rate.find((p) => p[0] * 1000 === top - 3 * 3600000)?.[1], current: rate.at(-1)?.[1], other: rate[0][1] }
const hours = overview.alertHours([{ source: 'slack', at: now - 1000 }, { source: 'alarm', toState: 'ALARM', at: now - 2000 }, { source: 'alarm', toState: 'OK', at: now - 3000 }, { source: 'slack', at: now - 2 * 86400000 }], now)
out.alerts = { n: hours.length, last: hours.at(-1)[1], sum: hours.reduce((s, h) => s + h[1], 0) }
out.bots = [traffic.botShare({ totals: { requests: 1000 }, classes: { scanner: { requests: 220 }, tool: { requests: 130 }, ai: { requests: 60 } } }), traffic.botShare({ totals: { requests: 0 } })]
const worst = common.worstMonthly({ items: [{ timeUnit: 'MONTHLY', limit: 100, actual: 20, forecast: 120 }, { timeUnit: 'MONTHLY', limit: 50, actual: 30 }, { timeUnit: 'ANNUALLY', limit: 10, actual: 9 }] })
out.worst = [worst.b.limit, worst.tone, common.worstMonthly({ items: [{ timeUnit: 'MONTHLY', limit: 100, actual: 20, forecast: 120 }] }).tone, common.worstMonthly({ items: [] })]
out.flows = traffic.flowGraph(input.flows)
out.tip = charts.tip('<img src=x onerror=alert(1)>', [['c0', '<img src=x onerror=alert(1)>', '<b>9</b>'], ['x" onmouseover="alert(1)', 'a', 'b']])
out.countries = ['KR', 'US', 'ZZ', '(기타)', 'kr'].map(traffic.countryLabel)
out.names = [traffic.channelName('direct'), traffic.channelName('constructor'), traffic.pageName('app-history'), traffic.pageName('__proto__')]
"""

FLOWS = [
    ["direct", "landing", "landing", 3, 10],
    ["direct", "landing", "app-spread", 2, 8],
    ["search", "kimp-chart", "landing", 1, 5],
    ["direct", "app-spread", "app-spread", 0, 0],  # 모양 0 — 빠진다
    ["direct", 7, "landing", 1, 1],  # 글자가 아닌 이름 — 빠진다
    "깨진 줄",
    ["(기타)", "(기타)", "(기타)", 1, 4],
]


@pytest.fixture(scope="module")
def got() -> dict[str, Any]:
    return run_admin(SCENARIO, {"now": NOW, "flows": FLOWS})


def test_bytes_pick_units_automatically(got: dict[str, Any]) -> None:
    """§3.5 — 1024 단위 B·KB·MB·GB·TB, 10 미만은 소수 1자리, 음수(아래로 뒤집은 면적)는 크기만, null 은 '–'."""
    assert got["bytes"] == [
        "0 B",
        "512 B",
        "1,023 B",
        "1.0 KB",
        "1.5 KB",
        "10 KB",
        "1.0 MB",
        "2.0 KB",
        "4.5 TB",
        "–",
    ]


def test_big_numbers_today_visitors_success_alerts_bots_budget(
    got: dict[str, Any],
) -> None:
    """§3.2 2 — 오늘(KST) 방문자 [확인, 모양](오늘 칸 없으면 0, 시행 전 null), 시간별 성공률(원천 둘·30분 끊김 = 75%,
    진행 중 구간), 시간별 알림(Slack + ALARM 으로 바뀐 경보, 24칸), 봇 비율(스캐너 + 도구 ÷ 전체), 가장 나쁜 월 예산."""
    assert got["today"] == [[2, 7], [0, 0], None]
    success = got["success"]
    assert success["n"] == 24
    assert success["early"] == 75
    assert success["other"] == 100
    assert success["current"] < 100
    assert got["alerts"] == {"n": 24, "last": 2, "sum": 2}
    assert got["bots"] == [35, None]
    assert got["worst"] == [50, "ok", "warn", None]


def test_flows_become_sankey_nodes_and_links(got: dict[str, Any]) -> None:
    """§4 — 층 머리로 들어온/나간 같은 페이지를 다른 마디로, 고리 굵기 = 모양 합(확인은 c), (기타)는 세 층 마디, 모양 0·틀린 줄은 뺀다."""
    flows = got["flows"]
    assert [n["name"] for n in flows["nodes"]] == [
        "0:direct",
        "1:landing",
        "2:landing",
        "2:app-spread",
        "0:search",
        "1:kimp-chart",
        "0:(기타)",
        "1:(기타)",
        "2:(기타)",
    ]
    assert [n["depth"] for n in flows["nodes"]] == [0, 1, 2, 2, 0, 1, 0, 1, 2]
    labels = {n["name"]: n["label"] for n in flows["nodes"]}
    assert labels["1:landing"] == labels["2:landing"] == "랜딩"
    assert labels["0:direct"] == "직접" and labels["2:(기타)"] == "(기타)"
    links = {(x["source"], x["target"]): (x["value"], x["c"]) for x in flows["links"]}
    assert links == {
        ("0:direct", "1:landing"): (18, 5),
        ("1:landing", "2:landing"): (10, 3),
        ("1:landing", "2:app-spread"): (8, 2),
        ("0:search", "1:kimp-chart"): (5, 1),
        ("1:kimp-chart", "2:landing"): (5, 1),
        ("0:(기타)", "1:(기타)"): (4, 1),
        ("1:(기타)", "2:(기타)"): (4, 1),
    }


def test_tooltip_html_turns_feed_text_into_plain_text(got: dict[str, Any]) -> None:
    """§3.1 보안 — 툴팁 서식의 글자는 encodeHTML 을 지나 `<img onerror>` 가 글로 나오고, 표시 클래스는 허용 목록만."""
    html = got["tip"]
    assert "<img" not in html and "<b>9" not in html
    assert html.count("&lt;img src=x onerror=alert(1)&gt;") == 2
    assert "&lt;b&gt;9&lt;/b&gt;" in html
    assert "onmouseover" not in html
    assert '<i class="tt-k c0"></i>' in html


def test_country_and_name_labels(got: dict[str, Any]) -> None:
    """나라는 국기 그림 문자 + 한국어 이름, 못 푸는 값은 글자 그대로. 이름표 밖 값(constructor·__proto__)은 원래 글자."""
    kr, us, zz, other, low = got["countries"]
    assert kr == "\U0001f1f0\U0001f1f7 대한민국"
    assert us.startswith("\U0001f1fa\U0001f1f8 ")
    assert [zz, other, low] == ["ZZ", "(기타)", "kr"]
    assert got["names"] == ["직접", "constructor", "대시보드 기록", "__proto__"]
