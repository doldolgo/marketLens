"""관리자 화면 v4 상세 판(스펙 066 §3.2·§4) — 열고 닫기·뒤로 가기·주소 #detail, 통계·원자료 표, CSV 내려받기, 판의 기간·비교, 계열 칩,
목록 표의 찾기·정렬·200행씩, 방문자 대역 판(일부만 읽음 배지·◀ ▶ 는 기록을 쌓지 않음·400 bad_band 는 판 안에·행 펼치기).
가짜 피드 서버(`web/scripts/admin-fake-feeds.py`)의 응답을 미리 만들어 페이지를 가짜 DOM 에 싣는다.
"""

import importlib.util
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

from tests.admin_dom import run_admin
from tests.test_deploy import ROOT


def _fake(scenario: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "admin_fake_detail", ROOT / "web/scripts/admin-fake-feeds.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.state["scenario"] = scenario
    return module


def answers(scenario: str, urls: list[str]) -> dict[str, list]:
    """주소마다 가짜 피드 서버의 [상태, 본문] — 페이지가 부를 주소를 미리 다 만든다."""
    fake = _fake(scenario)
    out = {}
    for url in urls:
        parts = urlsplit(url)
        got = fake.feed("GET", parts.path, parse_qs(parts.query), {})
        out[url] = list(got) if got else [404, {"detail": "Not Found"}]
    return out


FAST = [
    "/api/health",
    "/svc/api/health",
    "/svc/api/admin/status",
    "/api/health/collect",
]
SERIES = [
    f"/api/admin/aws/series?range={r}{o}"
    for r in ("6h", "24h", "7d", "30d")
    for o in ("", "&offset=prev")
]
SERVER = [*FAST, "/api/admin/aws", "/api/admin/alerts", *SERIES]
HARNESS = r"""
const respond = (url) => input.answers[url] || [404, { detail: 'Not Found' }]
const v = setup({ respond, search: input.search || '', hash: input.hash || '', buttons: { detail: input.details || [], scmp: ['none', 'prev', 'week'], cmp: ['none', 'prev', 'week'], range: ['6h', '24h', '7d', '30d'] } })
document.getElementById('sheet').hidden = true // 페이지의 틀처럼 처음엔 닫혀 있다
await page(input.page)
await flush()
const sheet = () => ({
  open: !document.getElementById('sheet').hidden, title: v.text('sheet-title'), unit: v.text('sheet-unit'), tag: v.text('t-sheet'), note: v.text('sheet-note'),
  inert: 'inert' in document.getElementById('app').attrs, scroll: document.body.attrs['data-sheet'] ?? null,
  chips: document.getElementById('sheet-chips').kids.map((c) => [c.textContent, c.attrs['aria-pressed']]),
  ranges: document.getElementById('sheet-ranges').kids.map((b) => [b.textContent, b.attrs['aria-pressed']]),
  stats: (document.getElementById('sheet-stat-tbl').kids[1]?.kids || []).map((tr) => tr.kids.map((td) => td.textContent)),
  head: (document.getElementById('sheet-tbl').kids[0]?.kids[0]?.kids || []).map((th) => th.textContent),
  rows: (document.getElementById('sheet-tbl').kids[1]?.kids || []).filter((tr) => tr.className !== 'rowmore').length,
  count: v.text('sheet-count'), more: !document.getElementById('sheet-more').hidden,
  related: document.getElementById('sheet-related').kids.map((b) => b.textContent),
  group: v.s.charts.get('c-sheet')?.group ?? null, disposed: v.s.charts.get('c-sheet')?.disposed ?? null,
})
"""

SERVER_FLOW = (
    HARNESS
    + r"""
v.click('detail', 'cpu')
await flush()
out.open = { ...sheet(), pushed: v.s.pushed, state: history.state, pageGroup: v.s.charts.get('c-cpu').group }
v.byId.get('sheet-chips').kids[1].fire('click')
await flush()
out.hidden = sheet()
v.byId.get('sheet-csv').fire('click')
await flush()
const bomText = async (blob) => new TextDecoder('utf-8', { ignoreBOM: true }).decode(await blob.arrayBuffer())
out.csv = { downloads: v.s.downloads, text: await bomText(v.s.blobs.at(-1)) }
const before = v.s.calls.length
v.byId.get('sheet-ranges').kids.find((b) => b.textContent === '7일').fire('click')
await flush()
v.click('scmp', 'prev')
await flush()
out.panel = { calls: v.s.calls.slice(before), url: location.search + location.hash, ...sheet() }
v.key('x')
await flush()
out.x = { scmp: v.groups.scmp.filter((b) => b.attrs['aria-pressed'] === 'true').map((b) => b.dataset.scmp), url: location.search + location.hash }
v.key('Escape')
await flush()
out.closed = { ...sheet(), backs: v.s.backs, url: location.search + location.hash, cleared: v.s.cleared.at(-1) }
"""
)


def test_detail_sheet_opens_over_the_page_and_closes_with_back() -> None:
    """066 §3.2 — ⤢ → 같은 페이지 위 판(#detail 쌓기 — 뒤로 가기로 닫힘), 뒤 가림(inert·스크롤 멈춤), 계열 칩·통계(보이는 구간)·원자료·관련 카드,
    판 그림은 페이지 connect 무리 밖. CSV = 보이는 계열 그대로(UTC·KST). 판의 기간·비교는 판만의 값(페이지 주소는 그대로), X 는 판의 비교."""
    got = run_admin(
        SERVER_FLOW,
        {"page": "server.js", "details": ["cpu"], "answers": answers("ok", SERVER)},
    )
    opened = got["open"]
    assert opened["open"] and opened["inert"] and opened["scroll"] == "open"
    assert opened["pushed"] == ["/server.html#detail=cpu"] and opened["state"] == {
        "ktDetail": "cpu"
    }
    assert (opened["title"], opened["unit"], opened["tag"]) == ("CPU", "% · 상자별", "")
    assert opened["chips"] == [["collect", "true"], ["data", "true"], ["serve", "true"]]
    assert opened["ranges"] == [
        ["1시간", "false"],
        ["6시간", "false"],
        ["24시간", "true"],
        ["7일", "false"],
        ["30일", "false"],
    ]
    assert [row[0] for row in opened["stats"]] == ["collect", "data", "serve"] and all(
        c.endswith("%") for c in opened["stats"][0][1:6]
    )
    assert (
        opened["head"] == ["시각(KST)", "collect", "data", "serve"]
        and opened["rows"] == 200
        and opened["more"]
    )
    assert opened["related"] == ["관련", "CPU 크레딧", "WS 접속"]
    assert opened["group"] == "" and opened["pageGroup"] == "server"
    assert got["hidden"]["chips"][1] == ["data", "false"] and [
        r[0] for r in got["hidden"]["stats"]
    ] == ["collect", "serve"]
    (name, href), *_ = got["csv"]["downloads"]
    assert (
        name.startswith("kimptrack-cpu-")
        and name.endswith(".csv")
        and href == "blob:admin/1"
    )
    assert got["csv"]["text"].startswith("﻿시각(UTC),시각(KST),collect,serve\r\n")
    panel = got["panel"]
    assert panel["calls"] == [
        "/api/admin/aws/series?range=7d",
        "/api/admin/aws/series?range=7d&offset=prev",
    ]
    assert panel["url"] == "#detail=cpu" and panel["ranges"][3] == ["7일", "true"]
    assert ["data", "false"] in panel[
        "chips"
    ]  # 숨긴 계열은 판의 기간·비교를 바꿔도 숨긴 채
    assert got["x"] == {"scmp": ["none"], "url": "#detail=cpu"}
    closed = got["closed"]
    assert (
        closed["backs"] == 1
        and not closed["open"]
        and not closed["inert"]
        and closed["scroll"] is None
    )
    assert closed["url"] == "" and closed["disposed"] is True


HASH_FLOW = (
    HARNESS
    + r"""
out.loaded = sheet()
v.byId.get('sheet-close').fire('click')
await flush()
out.closed = { ...sheet(), backs: v.s.backs, cleared: v.s.cleared.at(-1) }
"""
)


def test_a_detail_link_opens_on_load_and_unknown_ids_are_ignored() -> None:
    """066 §3.2·§3.4 — 주소 #detail=<id> 로 열린다(링크). 이 문서가 쌓은 기록이 아니면 × 는 주소에서 판만 지운다. 목록 밖 id 는 열지 않는다."""
    got = run_admin(
        HASH_FLOW,
        {
            "page": "server.js",
            "search": "?range=7d",
            "hash": "#detail=disk",
            "answers": answers("ok", SERVER),
        },
    )
    assert (
        got["loaded"]["open"]
        and got["loaded"]["title"] == "디스크"
        and got["loaded"]["ranges"][3] == ["7일", "true"]
    )
    assert (
        got["closed"]["backs"] == 0
        and not got["closed"]["open"]
        and got["closed"]["cleared"] == "/server.html?range=7d"
    )
    bad = run_admin(
        HASH_FLOW,
        {"page": "server.js", "hash": "#detail=nope", "answers": answers("ok", SERVER)},
    )
    assert not bad["loaded"]["open"]


TABLE_FLOW = (
    HARNESS
    + r"""
v.click('detail', 'collect')
await flush()
out.first = sheet()
v.byId.get('sheet-more').fire('click')
await flush()
out.more = sheet()
const find = v.byId.get('sheet-find')
find.value = 'okx'
find.fire('input', { target: find })
await flush()
out.found = sheet()
v.byId.get('sheet-tbl').kids[0].kids[0].kids[3].kids[0].fire('click')
await flush()
out.sorted = [v.byId.get('sheet-tbl').kids[0].kids[0].kids[3].attrs['aria-sort'], sheet().count]
v.byId.get('sheet-csv').fire('click')
await flush()
out.csv = new TextDecoder('utf-8', { ignoreBOM: true }).decode(await v.s.blobs.at(-1).arrayBuffer()).split('\r\n').slice(0, 2)
"""
)


def test_table_details_page_200_rows_and_search_sort_and_export_all_found_rows() -> (
    None
):
    """066 §3.2 — 표 판(수집 끊김 목록): 200행씩 더 보기, 찾기(글자 칸), 머리 눌러 정렬, CSV 는 찾고 정렬한 행 전부(수 칸은 수 그대로)."""
    now = 1_800_000_000_000
    outages = [
        {
            "exchange": "okx" if i % 3 else "upbit",
            "kind": "timeout",
            "startedAt": now - i * 60_000,
            "endedAt": now - i * 60_000 + 30_000,
            "count": i,
            "statusCode": None,
            "message": f"m{i}",
        }
        for i in range(450)
    ]
    collect = {
        "successRate1h": 99,
        "exchanges": [
            {
                "exchange": "upbit",
                "state": "ok",
                "successRate1h": 99,
                "lastSuccessAt": now,
            }
        ],
        "outages": outages,
    }
    feeds = answers("ok", SERVER)
    feeds["/api/health/collect"] = [200, collect]
    got = run_admin(
        TABLE_FLOW, {"page": "server.js", "details": ["collect"], "answers": feeds}
    )
    assert (
        got["first"]["title"] == "수집 상태"
        and got["first"]["rows"] == 200
        and got["first"]["count"] == "200 / 450행"
        and got["first"]["more"]
    )
    assert got["more"]["rows"] == 400 and got["more"]["count"] == "400 / 450행"
    assert got["found"]["rows"] == 200 and got["found"]["count"] == "200 / 300행"
    assert got["sorted"] == ["descending", "200 / 300행"]
    assert (
        got["csv"][0] == "﻿시작(KST),원천,종류,길이,횟수,HTTP,내용"
        and got["csv"][1].split(",")[1] == "OKX"
    )


IPS = "/svc/api/admin/access/ips?window=24h&sort=requests&limit=200&offset=0"
BAND_FLOW = (
    HARNESS
    + r"""
const rows = () => v.byId.get('tbl-bands').kids[1].kids
const opened = rows().map((tr) => tr.kids[0].textContent)
out.bands = opened
// 모음 행((기타)·(없음))은 상세를 열지 않는다
rows().find((tr) => tr.kids[0].textContent === '(기타)').fire('click')
await flush()
out.aggregate = { open: sheet().open, pushed: v.s.pushed.length }
rows()[1].fire('click')
await flush()
out.second = { ...sheet(), pushed: v.s.pushed, prev: v.byId.get('sheet-prev').disabled, next: v.byId.get('sheet-next').disabled, calls: v.s.calls.filter((u) => u.includes('/access/ip?')) }
v.byId.get('sheet-next').fire('click')
await flush()
out.next = { title: sheet().title, pushed: v.s.pushed.length, url: location.hash }
const recent = v.byId.get('sheet-tbl').kids[1].kids
recent[0].fire('click')
out.expand = [recent[0].attrs['aria-expanded'], recent[1].className, recent[1].hidden]
"""
)


def test_band_detail_steps_without_stacking_history_and_shows_partial_and_bad_band_inline() -> (
    None
):
    """066 §3.3 방문자(067 #132) — 대역 행 → 상세 판(그 대역 글자 그대로 부름), 모음 행은 열지 않음, ◀ 이전/다음 ▶ 은 기록을 바꿔 쌓지 않음,
    읽기 상한이면 '일부만 읽음' 배지, 최근 요청 행 펼치기. 400 bad_band 는 페이지 실패가 아니라 판 안 한 줄."""
    # 목록은 ok 경우(대역 48 + 모음 둘이 한 쪽에), 대역 판은 bands 경우(읽기 상한 — partial)
    listed = _fake("ok").ips(
        {"window": ["24h"], "sort": ["requests"], "limit": ["200"], "offset": ["0"]}
    )[1]["bands"]
    valid = [b["band"] for b in listed if "/" in b["band"]][:3]
    detail = [
        f"/svc/api/admin/access/ip?band={b.replace('/', '%2F').replace(':', '%3A')}&window=24h"
        for b in valid
    ]
    got = run_admin(
        BAND_FLOW,
        {
            "page": "visitors.js",
            "answers": {**answers("ok", [*FAST, IPS]), **answers("bands", detail)},
        },
    )
    assert "(기타)" in got["bands"] and "(없음)" in got["bands"]
    assert got["aggregate"] == {"open": False, "pushed": 0}
    second = got["second"]
    assert (
        second["open"]
        and second["title"] == got["bands"][1]
        and second["tag"] == "일부만 읽음"
    )
    assert second["pushed"] == [
        f"/visitors.html#detail=ip%3A{got['bands'][1].replace('/', '%2F').replace(':', '%3A')}"
    ]
    assert second["calls"][-1].startswith("/svc/api/admin/access/ip?band=") and second[
        "calls"
    ][-1].endswith("&window=24h")
    assert (second["prev"], second["next"]) == (False, False) and second[
        "count"
    ] == "100 / 100행"
    assert got["next"]["pushed"] == 1 and got["next"]["title"] == got["bands"][2]
    assert got["expand"] == ["true", "rowmore", False]
    bad = run_admin(
        HASH_FLOW,
        {
            "page": "visitors.js",
            "hash": "#detail=ip%3A1.2.3.0%2F24",
            "answers": {
                **answers("bands", [*FAST, IPS]),
                "/svc/api/admin/access/ip?band=1.2.3.0%2F24&window=24h": [
                    400,
                    {"error": {"code": "bad_band", "message": "x", "detail": None}},
                ],
            },
        },
    )
    loaded: dict[str, Any] = bad["loaded"]
    assert (
        loaded["open"]
        and loaded["tag"] == "불러오지 못함"
        and "bad_band" in loaded["note"]
    )
