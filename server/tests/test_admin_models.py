"""관리자 화면 v4 의 모델 계산 (스펙 066 §4 node) — IP 대역(067 #132 꼴: 쪽 요청·큰 수·표 행·대역 검사)·Clarity 날별 선·칸 진하기,
그리고 064 에서 이어받은 계산(바이트·오늘 방문자·시간별 성공률·알림·봇 비율·예산·흐름·툴팁 글자·이름표). 공개 함수만 부른다.
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_rules import NOW

SCENARIO = r"""
setup({ visible: false, respond: () => [404, {}] })
const calc = await page('calc.js')
const common = await page('common.js')
const charts = await page('charts.js')
const models = await page('models.js')
const names = await page('names.js')
const ip = await page('ipmodel.js')
const tm = await page('trafficmodel.js')
const ins = await page('insightdetail.js')
const rules = await page('rules.js')
const H = 3600000
const fakeApp = (bodies) => ({ entry: (r) => (bodies[r.key] === undefined ? undefined : bodies[r.key] === 404 ? { off: true } : { body: bodies[r.key], text: 'x' }), now: () => input.now, view: { f: [] }, filters: () => [] })
const END = 1_800_000_000
const days = (n) => Array.from({ length: n }, (_, i) => ({ date: new Date((END - (n - 1 - i) * 86400 + 32400) * 1000).toISOString().slice(0, 10), traffic: { sessions: i } }))
const hq = models.historyReqs('7d', 'prev')
const hw = models.historyWindow(fakeApp({ [hq[0].key]: { state: 'ok', rows: days(20) } }), '7d', 'prev', END * 1000)
out.history = { reqs: [hq, models.historyReqs('90d', 'prev'), models.historyReqs('30d', 'none')].map((x) => x.map((q) => q.url)), line: hw.line((row) => row.traffic.sessions), first: hw.firstMs, rows: hw.rows.length }
out.daily = [calc.dayMs('2026-10-14'), calc.dayMs('20261014'), calc.dayMs('bad'), calc.dailyLine([{ date: '2026-10-15', v: 2 }, { date: '2026-10-14', v: 'x' }, { date: 'nope', v: 3 }], (row) => row.v)]
out.lastTwo = [ins.lastTwo([{ v: 1 }, { v: 4 }], (x) => x.v), ins.lastTwo([{ v: 1 }], (x) => x.v)]
out.shade = [ins.shade(0, 10), ins.shade(1, 10), ins.shade(10, 10), ins.shade(5, 0), ins.shade(null, 3)]
// IP 대역(067 #132 꼴) — 쪽 요청, 큰 수, 표 행(모음 행은 상세 없음), 대역 검사
out.ips = ip.ipsReqs({ range: '7d', f: [['net', 'cloud'], ['bots', '0']] }, { q: ' 1.2.', pages: 2, sort: 'botPct' }).map((q) => q.url)
const band = (b, n, extra = {}) => ({ band: b, requests: n, net: 'telecom_kr', country: 'KR', first: END - 10, last: END, status: { '5xx': 1 }, spark: [1, 2], ...extra })
const bands = [band('1.2.3.0/24', 60, { net: 'cloud' }), band('2001:db8:1::/48', 30, { net: 'ipv6', country: null }), band('(기타)', 10, { net: null, country: null, first: END - 9 * 86400 })]
out.kpis = ip.bandKpis(bands, 5, END * 1000)
out.rows = ip.bandRows(bands).map((x) => [x.label, x.where, x.ratio, x.open, x.seen.length > 0])
out.valid = ['1.2.3.0/24', '1.2.3.4/24', '1.2.3.0', '2001:db8:1::/48', '2001:DB8:1::/48', '(없음)', '(기타)'].map(ip.validBand)
// 064 에서 이어받은 계산
out.bytes = [0, 512, 1023, 1024, 1536, 10240, 1048576, -2048, 5e12, null].map(common.bytes)
const kst0 = Math.floor((input.now / 1000 + 32400) / 86400) * 86400 - 32400
out.today = [models.todayVisitors({ endTs: input.now / 1000, visitors: { state: 'ok', days: [{ ts: kst0 - 86400, confirmed: 3, shaped: 9 }, { ts: kst0, confirmed: 2, shaped: 7 }] } }), models.todayVisitors({ endTs: input.now / 1000, visitors: { state: 'unconfigured' } })]
// 접속 날 점(038 visitors.days) — 그날이 창과 겹친 구간의 가운데(오늘은 지금까지), 이전 기간 날도 민 뒤 같은 규칙. 맞대기는 그날 00:00
const nowS = input.now / 1000
const visits = (start, days) => ({ state: 'ok', startTs: start, endTs: nowS, hourly: [], visitors: { state: 'ok', days } })
const aq = models.accessReqs('7d', 'prev')
const week = visits(nowS - 7 * 86400, [{ ts: kst0 - 7 * 86400, shaped: 1 }, { ts: kst0 - 86400, shaped: 4, confirmed: 3 }, { ts: kst0, shaped: 2, confirmed: 1 }])
out.accessDays = models.accessWindow(fakeApp({ [aq[0].key]: week, [aq[1].key]: visits(nowS - 30 * 86400, [{ ts: kst0 - 8 * 86400, shaped: 5 }, { ts: kst0 - 7 * 86400, shaped: 3 }]) }), '7d', 'prev').days('shaped')
const kdate = (sec) => new Date((sec + 32400) * 1000).toISOString().slice(0, 10)
const mq = [models.historyReqs('7d', 'none')[0], models.accessReqs(ins.accessRange('7d'), 'none')[0]]
const rows = [{ date: kdate(kst0 - 86400), traffic: { sessions: 2 } }, { date: kdate(kst0), traffic: { sessions: 1 } }]
out.match = ins.matchModel(fakeApp({ [mq[0].key]: { state: 'ok', rows }, [mq[1].key]: week }), '7d').series.map((x) => x.data)
const top = Math.floor(input.now / H) * H
const rate = models.hourlySuccess({ exchanges: [{}, {}], outages: [{ startedAt: top - 3 * H, endedAt: top - 3 * H + 1800000 }, { startedAt: top - 600000, endedAt: null }] }, input.now)
out.success = { n: rate.length, early: rate.find((p) => p[0] === top - 3 * H + H / 2)?.[1], other: rate[0][1], current: rate.at(-1)[1] }
const alerts = [{ source: 'slack', at: input.now - 1000 }, { source: 'alarm', toState: 'ALARM', at: input.now - 2000 }, { source: 'alarm', toState: 'OK', at: input.now - 3000 }]
out.alerts = [models.alertHours(alerts, input.now).at(-1)[1], models.alertHours(alerts, input.now, 168, 'slack').length, models.alertHours(alerts, input.now, 24, 'alarm').at(-1)[1]]
out.bots = [tm.botShare({ totals: { requests: 1000 }, classes: { scanner: { requests: 220 }, tool: { requests: 130 }, ai: { requests: 60 } } }), tm.botShare({ totals: { requests: 0 } })]
out.worst = rules.worstMonthly({ items: [{ timeUnit: 'MONTHLY', limit: 100, actual: 20, forecast: 120 }, { timeUnit: 'MONTHLY', limit: 50, actual: 30 }] }).b.limit
out.flows = tm.flowGraph([['direct', 'landing', 'landing', 3, 10], ['direct', 'landing', 'app-spread', 2, 8], ['direct', 7, 'x', 1, 1], ['(기타)', '(기타)', '(기타)', 1, 4]])
out.tip = charts.tip('<img src=x onerror=alert(1)>', [['c1', '<img src=x onerror=alert(1)>', '<b>9</b>'], ['x" onmouseover="alert(1)', 'a', 'b']])
out.names = [names.countryLabel('KR'), names.countryLabel('ZZ'), names.channelName('constructor'), names.pageName('app-history'), names.netName('ipv6'), names.boxMark('serve'), names.boxMark('<x>')]
"""


@pytest.fixture(scope="module")
def got() -> dict[str, Any]:
    return run_admin(SCENARIO, {"now": NOW})


def test_ip_band_requests_kpis_and_rows(got: dict[str, Any]) -> None:
    """066 §3.3 방문자(067 #132) — 쪽 요청(거르기·찾기·서버 정렬 열만·200행씩), 큰 수 넷(비중은 불러온 대역 기준 — total 이 더 크면 어림),
    표 행(모음 '(기타)'·'(없음)' 은 상세 없음), 대역 검사(/24·/48 꼴·소문자)."""
    assert got["ips"] == [
        "/svc/api/admin/access/ips?window=7d&net=cloud&q=1.2.&bots=0&sort=requests&limit=200&offset=0",
        "/svc/api/admin/access/ips?window=7d&net=cloud&q=1.2.&bots=0&sort=requests&limit=200&offset=200",
    ]
    assert got["kpis"] == {
        "count": 5,
        "dc": 60,
        "top": 100,
        "fresh": 2,
        "partial": True,
    }
    assert got["rows"] == [
        ["1.2.3.0/24", "\U0001f1f0\U0001f1f7 대한민국 · 데이터센터", 1, True, True],
        ["2001:db8:1::/48", "– · IPv6", 0.5, True, True],
        ["(기타)", "– · –", 1 / 6, False, True],
    ]
    assert got["valid"] == [True, False, False, True, False, False, False]


def test_clarity_daily_lines_and_shading(got: dict[str, Any]) -> None:
    """066 §3.3 Clarity(068 history) — 날 'YYYY-MM-DD'(KST) → 그날 00:00, 창 안만·같은 길이의 앞 구간은 지금 축으로, 전날 대비, 칸 진하기 다섯 단계."""
    history = got["history"]
    assert history["reqs"] == [
        ["/svc/api/admin/clarity/history?days=30"],
        ["/svc/api/admin/clarity/history?days=90"],
        ["/svc/api/admin/clarity/history?days=30"],
    ]
    data, cmp = history["line"]["data"], history["line"]["cmp"]
    assert [v for _, v in data] == list(range(13, 20)) and [v for _, v in cmp] == list(
        range(6, 13)
    )
    assert (
        data[0][0] == cmp[0][0]
        and history["rows"] == 7
        and history["first"] is not None
    )
    tokyo = 1_791_903_600_000  # 2026-10-14 00:00 KST
    assert got["daily"] == [
        tokyo,
        tokyo,
        None,
        [[tokyo, None], [tokyo + 86_400_000, 2]],
    ]
    assert got["lastTwo"] == [[4, 1], [1, None]]
    assert got["shade"] == ["", "q1", "q5", "", ""]


def test_access_day_points_stay_inside_the_window(got: dict[str, Any]) -> None:
    """066 §3.3 트래픽 큰 추이(7·30일 날별) — 날 점은 그날이 창과 겹친 구간의 가운데라 오늘 막대가 축(창 끝 = 지금) 밖으로 나가지 않는다.
    이전 기간 날은 창 길이만큼 민 뒤 같은 규칙. 맞대기는 Clarity 날 점처럼 그날 00:00 KST 에 둔다."""
    day, now = 86_400_000, NOW
    k = ((now // 1000 + 32_400) // 86_400 * 86_400 - 32_400) * 1000
    first = (now - 7 * day + k - 6 * day) / 2
    assert got["accessDays"] == {
        "data": [[first, 1], [k - day / 2, 4], [(k + now) / 2, 2]],
        "cmp": [[k - day / 2, 5], [(k + now) / 2, 3]],
    }
    assert max(t for t, _ in got["accessDays"]["data"]) < now
    assert got["match"] == [
        [[k - day, 2], [k, 1]],
        [[k - day, 3], [k, 1]],
        [[k - day, 4], [k, 2]],
    ]


def test_inherited_calculations_and_tooltip_text(got: dict[str, Any]) -> None:
    """064 에서 이어받은 계산 — 바이트 단위, 오늘(KST) 방문자, 시간별 성공률(칸 가운데 ms), 알림 수, 봇 비율, 가장 나쁜 월 예산, 흐름 마디,
    툴팁 글자는 encodeHTML 을 지나고 표시 클래스는 허용 목록만, 이름표(표 밖 값은 원래 글자)."""
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
    assert got["today"] == [[2, 7], None]
    assert (
        got["success"]["n"] == 24
        and got["success"]["early"] == 75
        and got["success"]["other"] == 100
        and got["success"]["current"] < 100
    )
    assert got["alerts"] == [2, 168, 1]
    assert got["bots"] == [35, None] and got["worst"] == 50
    assert [n["name"] for n in got["flows"]["nodes"]] == [
        "0:direct",
        "1:landing",
        "2:landing",
        "2:app-spread",
        "0:(기타)",
        "1:(기타)",
        "2:(기타)",
    ]
    assert {n["name"]: n["mark"] for n in got["flows"]["nodes"]}["0:direct"] == "k1"
    html = got["tip"]
    assert (
        "<img" not in html
        and "onmouseover" not in html
        and html.count("&lt;img src=x onerror=alert(1)&gt;") == 2
    )
    assert '<i class="tt-k c1"></i>' in html and "&lt;b&gt;9&lt;/b&gt;" in html
    assert got["names"] == [
        "\U0001f1f0\U0001f1f7 대한민국",
        "ZZ",
        "constructor",
        "대시보드 기록",
        "IPv6",
        "k3",
        "kx",
    ]
