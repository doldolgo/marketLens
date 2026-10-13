"""관리자 화면 v4 의 계산 (스펙 066 §4 node) — 상세 판 통계(경계값·null 섞임)·CSV 글자(따옴표·쉼표·한국어·UTC/KST)·비교선 시각 이동
(068 offset·지난주·1시간·offset 을 모르는 서버·접속 요약의 더 긴 창)·상위 목록 막대 폭·100% 띠 다섯 + 기타. 공개 함수만 부른다.
"""

from typing import Any

import pytest

from tests.admin_dom import run_admin
from tests.test_admin_rules import NOW

SCENARIO = r"""
setup({ visible: false, respond: () => [404, {}] })
const calc = await page('calc.js')
const common = await page('common.js')
const models = await page('models.js')
const H = 3600000
// 상세 판 통계 — 구간 경계 포함·null 섞임·정렬 안 된 입력
const pts = [[40, 3], [0, 5], [10, null], [20, 1], [30, 9]]
out.stats = [calc.stats(pts), calc.stats(pts, 10, 30), calc.stats(pts, 50, 60), calc.stats(Array.from({ length: 20 }, (_, i) => [i, i + 1])), calc.stats([[1, 7]])]
out.change = [calc.change(110, 100), calc.change(5, 0), calc.change(-5, -10), calc.change(null, 1)]
out.delta = [calc.deltaText(110, 100, '어제 같은 때'), calc.deltaText(88, 100, '앞 7일'), calc.deltaText(100.2, 100, '전날'), calc.deltaText(1, 0, 'x')]
// CSV — 따옴표·쉼표·줄바꿈·한국어·수식 막기, BOM·CRLF, UTC/KST 두 칸, 파일 이름
out.cells = ['=1+1', '+82', '-x', '@a', 'a,b', 'say "hi"', '줄\n바꿈', '한국어', 3.5, -2, null, Infinity].map(calc.csvCell)
out.csv = calc.toCsv(['이름', '값'], [['a,b', 1], ['한국어', null]])
const t0 = Date.UTC(2026, 9, 14, 0, 0)
out.series = calc.seriesCsv([{ name: 'CPU', data: [[t0, 12.5], [t0 + 300000, null]], cmp: [[t0, 10]] }, { name: 'data', data: [[t0 + 300000, 3]] }], t0, t0 + 300000)
out.name = calc.csvName('ip:203.0.113.0/24', t0, t0 + 6 * H)
// 비교선 — 이전 기간 점을 지금 축으로(시각 + 기간 길이)
out.align = calc.align([[0, 1], [10, 2], [20, 3], [35, 4]], 15, 30, 10)
out.valueAt = [calc.valueAt([[0, 1], [10, null], [20, 3]], 15), calc.valueAt([[0, 1]], -1)]
// 상위 목록 — 큰 순, 막대 폭 = 값 ÷ 최댓값, 비율 = 값 ÷ 분모, 0·틀린 행은 뺀다
out.top = calc.topRows([{ key: 'a', value: 5 }, { key: 'b', value: 20 }, { key: 'c', value: 0 }, { key: 'd', value: 10 }, null], 2, 40)
const list = document.getElementById('l')
;(await page('ui.js')).topList('l', [{ key: 'a', name: 'A', value: 5 }, { key: 'b', name: 'B', value: 20 }], { n: 6, fmt: common.int, more: () => {} })
out.topDom = list.kids[0].kids.map((row) => [row.kids[0].style.width, row.textContent])
out.topFoot = list.kids[1].textContent
// 100% 띠 — 다섯 + 기타(나머지·이름이 기타인 칸)
out.split = calc.split100(['a', 'b', 'c', 'd', 'e', 'f', 'other', '(기타)'].map((key, i) => ({ key, value: 8 - i })))
out.split0 = calc.split100([{ key: 'a', value: 0 }])
;(await page('ui.js')).bar100s('b', [{ name: '기기', items: [{ key: 'x', name: 'X', value: 3, mark: 'k1' }, { key: 'y', name: 'Y', value: 1, mark: 'k2' }], fmt: common.int }])
out.barDom = document.getElementById('b').kids[0].kids[1].kids.map((seg) => [seg.className, seg.style.width])
// 시간 창 모델 — 가짜 app(열쇠 → 결과)
const fakeApp = (bodies) => ({ entry: (r) => (bodies[r.key] === undefined ? undefined : bodies[r.key] === 404 ? { off: true } : { body: bodies[r.key], text: 'x' }), now: () => input.now, view: { f: [] }, filters: () => [] })
const END = 1_800_000_000
const series = (start, end, extra = {}) => ({ state: 'ok', periodSec: 300, startTs: start, endTs: end, boxes: [], wsClients: [[start, 1], [end - 300, 2]], ...extra })
const W = (bodies, range, cmp) => { const w = models.seriesWindow(fakeApp(bodies), range, cmp); return { ok: w.ok, state: w.cmpState, start: w.startMs, end: w.endMs, line: w.ok ? w.line((p) => p.wsClients) : null } }
const r = (range, extra) => models.seriesReqs(range, extra)
out.reqs = [r('24h', 'prev'), r('1h', 'prev'), r('6h', 'week'), r('7d', 'week'), r('30d', 'none')].map((x) => x.map((q) => q.url))
out.prev24 = W({ [r('24h', 'prev')[0].key]: series(END - 86400, END), [r('24h', 'prev')[1].key]: series(END - 2 * 86400 + 600, END - 86400 + 600, { offset: 'prev' }) }, '24h', 'prev')
out.week6 = W({ [r('6h', 'week')[0].key]: series(END - 21600, END), [r('6h', 'week')[1].key]: series(END - 7 * 86400 - 3600, END - 6 * 86400) }, '6h', 'week')
out.hour = W({ [r('1h', 'prev')[0].key]: series(END - 21600, END) }, '1h', 'prev')
out.compareBefore = models.seriesCompare('24h')
out.old = W({ [r('24h', 'prev')[0].key]: series(END - 86400, END), [r('24h', 'prev')[1].key]: series(END - 86400, END) }, '24h', 'prev')
out.compareAfter = [models.seriesCompare('24h'), models.seriesCompare('1h'), models.seriesCompare('6h'), models.seriesCompare('7d'), models.seriesCompare('30d')]
const hours = (start, n, v) => Array.from({ length: n }, (_, i) => ({ ts: start + i * 3600, humanPages: v(i) }))
const acc = (start, n, v) => ({ state: 'ok', startTs: start, endTs: start + n * 3600, hourly: hours(start, n, v) })
const a24 = models.accessReqs('24h', 'prev')
const aw = models.accessWindow(fakeApp({ [a24[0].key]: acc(END - 86400, 24, () => 5), [a24[1].key]: acc(END - 7 * 86400, 168, (i) => i) }), '24h', 'prev')
out.access = { state: aw.cmpState, line: aw.hours('humanPages'), compare: [models.accessCompare('24h'), models.accessCompare('30d')] }
"""


@pytest.fixture(scope="module")
def got() -> dict[str, Any]:
    return run_admin(SCENARIO, {"now": NOW})


def test_detail_stats_over_the_visible_window(got: dict[str, Any]) -> None:
    """066 §3.2 — 보이는 구간 [from, to] 안 null 아닌 값의 최소·평균·최대·p95(가장 가까운 순위 — 올림)·마지막(가장 늦은 점)."""
    full, window, none, twenty, one = got["stats"]
    assert full == {"n": 4, "min": 1, "avg": 4.5, "max": 9, "p95": 9, "last": 3}
    assert window == {"n": 2, "min": 1, "avg": 5, "max": 9, "p95": 9, "last": 9}
    assert none == {
        "n": 0,
        "min": None,
        "avg": None,
        "max": None,
        "p95": None,
        "last": None,
    }
    assert twenty["p95"] == 19 and twenty["avg"] == 10.5
    assert one == {"n": 1, "min": 7, "avg": 7, "max": 7, "p95": 7, "last": 7}
    assert got["change"] == [
        {"diff": 10, "pct": 10},
        {"diff": 5, "pct": None},
        {"diff": 5, "pct": 50},
        {"diff": None, "pct": None},
    ]
    assert got["delta"] == [
        "어제 같은 때보다 ▲ 10%",
        "앞 7일보다 ▼ 12%",
        "전날보다 – 0%",
        "",
    ]


def test_csv_text_quotes_formulas_korean_and_two_clocks(got: dict[str, Any]) -> None:
    """066 §3.2 — CSV: 보이는 구간·계열 그대로, 시각은 UTC·KST 두 칸, 이전 기간은 그 계열 옆 칸, BOM·CRLF, 파일 이름 kimptrack-<id>-<시작>-<끝>."""
    assert got["cells"] == [
        "'=1+1",
        "'+82",
        "'-x",
        "'@a",
        '"a,b"',
        '"say ""hi"""',
        '"줄\n바꿈"',
        "한국어",
        "3.5",
        "-2",
        "",
        "",
    ]
    assert got["csv"] == '﻿이름,값\r\n"a,b",1\r\n한국어,\r\n'
    assert got["series"].split("\r\n") == [
        "﻿시각(UTC),시각(KST),CPU,CPU (이전),data",
        "2026-10-14T00:00:00Z,2026-10-14 09:00:00,12.5,10,",
        "2026-10-14T00:05:00Z,2026-10-14 09:05:00,,,3",
        "",
    ]
    assert got["name"] == "kimptrack-ip-203.0.113.0-24-20261014T0900-20261014T1500.csv"


def test_comparison_lines_move_onto_the_current_axis(got: dict[str, Any]) -> None:
    """066 §3.2 — 이전 기간 = 068 offset=prev(1시간은 6시간 응답 안에서), 지난주 같은 때 = 7일 창에서 잘라(24시간 이하). offset 을 모르는 서버
    (068 전 — offset 키 없음)는 비교 없음이고 그 뒤 이 문서에서 offset 비교 단추가 꺼진다. 시각으로 맞춘다(배열 순서가 아니라)."""
    assert got["align"] == [[20, 2], [30, 3]]
    assert got["valueAt"] == [1, None]
    assert got["reqs"] == [
        [
            "/api/admin/aws/series?range=24h",
            "/api/admin/aws/series?range=24h&offset=prev",
        ],
        ["/api/admin/aws/series?range=6h"],
        ["/api/admin/aws/series?range=6h", "/api/admin/aws/series?range=7d"],
        [
            "/api/admin/aws/series?range=7d",
            "/api/admin/aws/series?range=7d&offset=prev",
        ],
        ["/api/admin/aws/series?range=30d"],
    ]
    end = 1_800_000_000_000
    prev = got["prev24"]
    assert (prev["ok"], prev["state"], prev["start"], prev["end"]) == (
        True,
        "ok",
        end - 86_400_000,
        end,
    )
    assert (
        prev["line"]["cmp"] == [[end - 86_400_000 + 600_000, 1], [end + 300_000, 2]][:1]
    )  # 창 밖(끝 뒤)은 잘린다
    # 7일 응답의 (끝 − 7일 − 1시간) 점이 지금 축의 (끝 − 1시간)으로
    assert got["week6"]["state"] == "ok" and got["week6"]["line"]["cmp"] == [
        [end - 3_600_000, 1]
    ]
    hour = got["hour"]
    assert (hour["state"], hour["start"]) == ("ok", end - 3_600_000) and hour["line"][
        "data"
    ] == [[end - 300_000, 2]]
    assert hour["line"]["cmp"] == []
    assert got["compareBefore"] == {"prev": True, "week": True}
    assert got["old"]["state"] == "missing" and got["old"]["line"]["cmp"] is None
    assert got["compareAfter"] == [
        {"prev": False, "week": True},
        {"prev": True, "week": True},
        {"prev": False, "week": True},
        {"prev": False, "week": False},
        {"prev": False, "week": False},
    ]
    access = got["access"]
    assert (
        access["state"] == "ok"
        and len(access["line"]["data"]) == 24
        and len(access["line"]["cmp"]) == 24
    )
    # 7일 응답에서 바로 앞 하루(24시간 뒤로 민 것)
    assert [v for _, v in access["line"]["cmp"]][:2] == [
        120,
        121,
    ]  # 7일 응답의 마지막 하루 앞 칸(24시간 뒤로 민 것)
    assert access["compare"] == [
        {"prev": True, "week": True},
        {"prev": False, "week": False},
    ]


def test_top_list_bars_and_hundred_percent_bands(got: dict[str, Any]) -> None:
    """066 §3.1 — 상위 목록: 큰 순, 글 뒤 막대 폭 = 값 ÷ 최댓값, 비율 = 값 ÷ 분모. 100% 띠: 다섯 조각 + 기타(이름이 기타·그 밖인 칸 포함)."""
    assert [(r["key"], r["ratio"], r["share"]) for r in got["top"]] == [
        ("b", 1, 0.5),
        ("d", 0.5, 0.25),
    ]
    assert got["topDom"] == [["100%", "B2080%"], ["25%", "A520%"]]
    assert got["topFoot"] == "2 / 2전체 보기"
    split = got["split"]
    assert [p["key"] for p in split] == ["a", "b", "c", "d", "e", "(기타)"]
    assert (
        split[-1]["value"] == 2 + 1 + 3
        and split[-1]["mark"] == "kx"
        and split[-1]["other"] is True
    )
    assert abs(sum(p["share"] for p in split) - 1) < 1e-9
    assert got["split0"] == []
    assert got["barDom"] == [["k1", "75%"], ["k2", "25%"]]
