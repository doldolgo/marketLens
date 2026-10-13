"""관리자 화면 v4 — 세션 만료·덜 그려짐·즉시 갱신·거르기 칩·트래픽 지표 바꾸기·방문자 거르기/정렬/찾기/쪽 (스펙 066 §4 node).

029·036·064 의 세션 규칙(401 비JSON·fetch 실패 = 만료 신호 → 고정 주소로 한 번 새로고침)은 그대로이고, 066 에서 새로고침 주소가 보기
(기간·비교·거르기·판)를 지킨다. 페이지 스크립트를 가짜 DOM 에 싣는다(`tests/admin_dom.py`).
"""

from tests.admin_dom import run_admin
from tests.test_admin_pages import RESPOND, _payload

EXPIRY = (
    RESPOND
    + r"""
const html = [401, '<html>login</html>']
// 1. 빠른 경로 둘이 401 비JSON — 새로고침은 한 번(그 페이지의 고정 주소 + 보기), 그 뒤로는 부르지 않는다
{
  const v = setup({ search: '?range=7d', respond: (url) => (url === '/api/health' || url === '/svc/api/health' ? html : respond(url)) })
  await page('server.js', '?e=1')
  await flush()
  out.first = { replaced: v.s.replaced, calls: v.s.calls.length }
}
// 2. 표시(?relogin=1)가 있으면 새로고침하지 않고 알림만
{
  const v = setup({ search: '?relogin=1', respond: (url) => (url === '/api/health/collect' ? ['reject', null] : respond(url)) })
  await page('traffic.js', '?e=2')
  await flush()
  out.marked = { replaced: v.s.replaced, hidden: v.byId.get('notice').hidden, text: v.text('notice') }
}
// 3. 표시가 있어도 지금 쓰는 열쇠가 모두 만료 신호 없이 끝나면 표시를 지운다(보기는 남긴다)
{
  const v = setup({ search: '?relogin=1&cmp=prev', respond })
  await page('overview.js', '?e=3')
  await flush()
  out.alive = { cleared: v.s.cleared.at(-1), hidden: v.byId.get('notice').hidden, replaced: v.s.replaced }
}
// 4. 앱 JSON 의 401(토큰 오류)은 만료가 아니다 — 그 칸만 '불러오지 못함'
{
  const v = setup({ respond: (url) => (url === '/api/admin/alerts' ? [401, { detail: 'nope' }] : respond(url)) })
  await page('server.js', '?e=4')
  await flush()
  out.appJson = { replaced: v.s.replaced, chart: v.text('c-alarms') }
}
"""
)


def test_expiry_signal_reloads_once_keeping_the_view_and_the_mark_clears() -> None:
    """029·036 그대로 — 만료 신호면 표시가 없을 때 `<이 페이지>?<보기>&relogin=1` 로 한 번, 있으면 알림만. 지금 쓰는 열쇠가 모두 신호 없이
    끝나면 표시를 지운다(기간·비교는 주소에 남는다). 401 + 앱 JSON 은 만료가 아니다."""
    got = run_admin(EXPIRY, _payload())
    assert got["first"]["replaced"] == ["/server.html?range=7d&relogin=1"]
    assert (
        got["first"]["calls"] == 7
    )  # 첫 두 묶음뿐 — 새로고침을 기다리는 동안 더 부르지 않는다
    assert got["marked"]["replaced"] == []
    assert got["marked"]["hidden"] is False and "로그인" in got["marked"]["text"]
    assert got["alive"] == {"cleared": "/?cmp=prev", "hidden": True, "replaced": []}
    assert got["appJson"] == {"replaced": [], "chart": "불러오지 못함"}


DEGRADED = (
    RESPOND
    + r"""
// 1. 차트 라이브러리를 못 불러옴 — 그림 칸만 한 줄, 큰 수·상태 띠·표는 그대로(064 §3.5)
{
  const v = setup({ respond, charts: false })
  await page('overview.js', '?d=1')
  await flush()
  out.nolib = { band: v.text('band-word'), ws: v.text('v-ws'), spark: v.text('c-ws'), traffic: v.text('c-traffic'), boxes: v.byId.get('tbl-boxes').kids.length }
}
// 2. 피드가 없다(404 — 그 피드의 배포 전): 그 카드만 '연결 안 됨'(066 §3.4), 비교를 골라도 오류 없음
{
  const v = setup({ search: '?cmp=prev', respond: (url) => (url.startsWith('/api/admin/aws/series') ? [404, '<html>404</html>'] : respond(url)) })
  await page('server.js', '?d=2')
  await flush()
  out.noSeries = { tag: v.text('t-cpu'), chart: v.text('c-cpu'), collect: v.text('t-collect'), mark: v.byId.get('m-server').className }
}
// 3. 문제가 있으면 띠는 칩(심각한 순) — 칩은 그 페이지의 그 카드 상세 판으로, 내비 모양은 그 페이지의 가장 나쁜 색
{
  const v = setup({ respond: (url) => (url === '/api/health' ? [503, { status: 'stale', lastTickAt: null }] : url === '/svc/api/admin/status' ? [200, { wsConnections: 0, redis: 'ok', influx: 'down' }] : respond(url)) })
  await page('overview.js', '?d=3')
  await flush()
  out.problem = {
    band: v.text('band-word'), cls: v.byId.get('band').className,
    chips: v.byId.get('chips').kids.map((c) => [c.className, c.textContent, c.attrs.href]),
    marks: ['overview', 'server', 'traffic'].map((id) => [v.byId.get(`m-${id}`).className, v.byId.get(`m-${id}`).title]),
  }
}
"""
)


def test_cards_degrade_alone_and_problems_show_as_shaped_chips() -> None:
    """064 §3.5·066 §3.4 — 라이브러리가 없으면 그림 칸만 '차트를 불러오지 못함', 404 피드는 그 카드만 '연결 안 됨'(본문이 HTML 이어도).
    문제는 ■·▲ 칩(누르면 그 카드의 상세 판), 정상은 조용한 한 줄, 내비 옆 모양."""
    got = run_admin(DEGRADED, _payload())
    nolib = got["nolib"]
    assert (
        nolib["band"].startswith("정상 · ")
        and nolib["ws"] == "4"
        and nolib["boxes"] == 2
    )
    assert nolib["spark"] == nolib["traffic"] == "차트를 불러오지 못함"
    assert got["noSeries"] == {
        "tag": "연결 안 됨",
        "chart": "연결 안 됨",
        "collect": "",
        "mark": "mark",
    }
    problem = got["problem"]
    assert problem["band"] == "문제" and problem["cls"] == "band bad s12"
    assert problem["chips"] == [
        ["chip bad", "수집 멈춤stale", "/server.html#detail=collect"],
        ["chip bad", "Influx 끊김down", "/server.html#detail=collect"],
    ]
    assert problem["marks"] == [
        ["mark shape bad", "문제"],
        ["mark shape bad", "문제"],
        ["mark", ""],
    ]


REFRESH = (
    RESPOND
    + r"""
const v = setup({ respond: (url, init) => (url === '/api/refresh' ? [input.refresh.status, input.refresh.body] : respond(url)) })
await page('server.js')
await flush()
document.getElementById('token').value = 'secret-token'
const sent = []
const real = globalThis.fetch
globalThis.fetch = (url, init = {}) => { if (url === '/api/refresh') sent.push(init.headers); return real(url, init) }
v.byId.get('refresh').fire('click')
await flush()
out.result = { status: v.text('refresh-status'), saved: v.text('refresh-saved'), failures: v.text('refresh-failures'), warnings: v.text('refresh-warnings'), shown: !v.byId.get('refresh-result').hidden }
out.sent = sent
out.replaced = v.s.replaced
out.last = v.s.calls.at(-1)
"""
)


def test_refresh_tool_posts_the_token_header_and_shows_the_result() -> None:
    """029 그대로 — POST /api/refresh 에 X-Refresh-Token·X-Requested-With, 200 이면 저장 수·실패·경고, 401 JSON 은 토큰 오류(새로고침 없음)."""
    ok = {
        "status": 200,
        "body": {
            "totalSaved": 1234,
            "failures": [{"exchange": "okx", "errorCode": "timeout"}],
            "warnings": ["<img src=x onerror=alert(1)>"],
        },
    }
    got = run_admin(REFRESH, _payload(refresh=ok))
    assert got["last"] == "POST /api/refresh"
    assert got["sent"] == [
        {"X-Requested-With": "XMLHttpRequest", "X-Refresh-Token": "secret-token"}
    ]
    assert got["result"] == {
        "status": "200",
        "saved": "1,234",
        "failures": "okx · timeout",
        "warnings": "<img src=x onerror=alert(1)>",
        "shown": True,
    }
    bad = run_admin(REFRESH, _payload(refresh={"status": 401, "body": {"detail": "x"}}))
    assert bad["result"]["status"] == "401 토큰 오류" and bad["replaced"] == []


FILTERS = (
    RESPOND
    + r"""
const v = setup({ respond, buttons: BUTTONS })
await page('server.js')
await flush()
const before = v.s.calls.length
v.click('box', 'data')
await flush()
const chips = () => v.byId.get('filters').kids.map((c) => c.textContent)
out.one = { url: v.s.cleared.at(-1), chips: chips(), hidden: v.byId.get('filters').hidden, pressed: v.groups.box.map((b) => b.attrs['aria-pressed']), cpu: v.option('c-cpu').series.map((s) => s.name), calls: v.s.calls.slice(before) }
v.click('box', 'collect')
await flush()
out.two = { url: v.s.cleared.at(-1), chips: chips() }
v.byId.get('filters').kids[0].kids[2].fire('click')
await flush()
out.removed = { url: v.s.cleared.at(-1), chips: chips(), cpu: v.option('c-cpu').series.map((s) => s.name) }
v.click('box', 'collect')
await flush()
out.none = { url: v.s.cleared.at(-1), hidden: v.byId.get('filters').hidden, cpu: v.option('c-cpu').series.map((s) => s.name) }
"""
)


def test_box_legend_chips_filter_the_page_and_the_chip_row_removes_them() -> None:
    """066 §3.3 서버 — 상자 칩 = 범례 겸 거르기(누르면 그 상자만, 더 누르면 더하기·빼기), 주소 f=box:…, 머리 아래 칩 줄(× 로 지움).
    거르기는 화면 안에서만이라 피드를 다시 부르지 않는다."""
    got = run_admin(FILTERS, _payload())
    assert got["one"] == {
        "url": "/server.html?f=box%3Adata",
        "chips": ["상자data×"],
        "hidden": False,
        "pressed": ["false", "true", "false"],
        "cpu": ["data"],
        "calls": [],
    }
    assert got["two"] == {
        "url": "/server.html?f=box%3Adata&f=box%3Acollect",
        "chips": ["상자data×", "상자collect×"],
    }
    assert got["removed"] == {
        "url": "/server.html?f=box%3Acollect",
        "chips": ["상자collect×"],
        "cpu": ["collect"],
    }
    assert got["none"] == {
        "url": "/server.html",
        "hidden": True,
        "cpu": ["data", "collect"],
    }
