"""attention.js 덮어 보기 모드를 node 로 돌린다 — 가짜 window·document·MutationObserver·시계 (스펙 053 §3.4·§4).

test_attention_js.py 와 같은 방식이다. 영역은 (id, 화면 위 left·top·width·height) 이고, 관리자 메시지는 출처·창을 골라
보내며, 그린 층(#kt-overlay)의 상자·꼬리표 글자·글씨 크기·빗금과 부모 창으로 보낸 ready 메시지를 본다.
"""

import json
import os
import shutil
import subprocess
from typing import Any

import pytest

from tests.test_deploy import ROOT

JS = (ROOT / "web/public/attention.js").read_text("utf-8")
ADMIN = "https://admin.kimptrack.com"
ON = {"kt.analytics": "granted", "kt.analytics.v": "2026-10-18"}

HARNESS = r"""
const [script, cases] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
process.stdout.write(JSON.stringify(cases.map((c) => {
  let clock = 1800000000000
  class FakeDate { static now() { return clock } }
  const listeners = { window: [], document: [] }
  const adder = (who) => (type, fn, opt) => listeners[who].push({ type, fn, capture: opt === true || !!(opt && opt.capture) })
  const fire = (who, type, ev) => listeners[who].filter((l) => l.type === type).forEach((l) => l.fn(ev))
  class Node {
    constructor(tag) { Object.assign(this, { tag, kids: [], attrs: {}, style: {}, className: '', id: '', textContent: '' }) }
    setAttribute(k, v) { this.attrs[k] = String(v) }
    appendChild(k) { this.kids.push(k); return k }
    replaceChildren(...k) { this.kids = k }
  }
  const scrolled = []
  const areas = []
  const make = ([id, left, top, width, height]) => {
    const el = {
      id, rect: { left, top, width, height }, isConnected: true,
      getAttribute: (n) => (n === 'data-area' ? id : null),
      getBoundingClientRect() { const r = this.rect; return { left: r.left, top: r.top, width: r.width, height: r.height, bottom: r.top + r.height } },
      scrollIntoView(o) { scrolled.push([id, o]) },
    }
    areas.push(el)
  }
  for (const a of c.areas || []) make(a)
  const mos = []
  class MO { constructor(cb) { this.cb = cb; mos.push(this) } observe(t, o) { this.opts = o } disconnect() {} }
  let rafs = []
  let timers = []
  const posted = []
  const sent = []
  const body = new Node('body')
  const parent = { postMessage: (data, origin) => posted.push({ data: JSON.parse(JSON.stringify(data)), origin }) }
  const window = {
    innerWidth: 1280, innerHeight: 800, parent,
    localStorage: { getItem: (k) => (c.ls || {})[k] ?? null },
    addEventListener: adder('window'),
    IntersectionObserver: class { constructor() { sent.push('io') } observe() {} },
    MutationObserver: MO,
    requestAnimationFrame: (fn) => { rafs.push(fn); return rafs.length },
    setTimeout: (fn, ms) => { timers.push({ at: clock + ms, fn }); return timers.length },
    fetch: () => { sent.push('fetch'); return Promise.resolve() },
  }
  window.top = c.framed === false ? window : {}
  const document = {
    readyState: 'interactive', visibilityState: 'visible', body, documentElement: new Node('html'),
    createElement: (tag) => new Node(tag),
    querySelectorAll: (sel) => (sel === '[data-area]' ? areas.filter((e) => e.isConnected) : []),
    addEventListener: adder('document'),
  }
  const navigator = { sendBeacon: () => { sent.push('beacon'); return true } }
  const location = { hostname: c.host || 'kimptrack.com', pathname: c.path || '/', search: c.search === undefined ? '?kt-overlay=1' : c.search }
  new Function('window', 'document', 'navigator', 'location', 'Date', script)(window, document, navigator, location, FakeDate)
  const flush = () => { const fs = rafs; rafs = []; fs.forEach((f) => f(clock)) }
  const blocked = {}
  for (const step of c.steps || []) {
    const [kind, a = '', b = ''] = step.split('|')
    if (kind === 'msg') {
      // 'msg|<출처>|<json>' — 창은 부모, 'msgfrom|<json>' — 출처는 관리자인데 다른 창
      fire('window', 'message', { origin: a, source: parent, data: JSON.parse(b) })
    } else if (kind === 'msgfrom') fire('window', 'message', { origin: c.admin, source: {}, data: JSON.parse(a) })
    else if (kind === 'flush') flush()
    else if (kind === 't') clock += Number(a)
    else if (kind === 'timers') { const due = timers.filter((x) => x.at <= clock); timers = timers.filter((x) => x.at > clock); due.forEach((x) => x.fn()) }
    else if (kind === 'add') { make(JSON.parse(a)); mos.forEach((m) => m.cb([])) }
    else if (kind === 'mutate') mos.forEach((m) => m.cb([]))
    else if (kind === 'scroll') { const dy = Number(a); areas.forEach((e) => { e.rect.top -= dy }); fire('document', 'scroll', {}) }
    else if (kind === 'press') {
      const ev = { prevented: false, stopped: false, preventDefault() { this.prevented = true }, stopPropagation() { this.stopped = true } }
      listeners.window.filter((l) => l.type === a && l.capture).forEach((l) => l.fn(ev))
      blocked[a] = [ev.prevented, ev.stopped]
    } else if (kind === 'hide') { document.visibilityState = 'hidden'; fire('document', 'visibilitychange', {}); fire('window', 'pagehide', {}) }
    else throw new Error('step ' + step)
  }
  const layer = body.kids.find((k) => k.id === 'kt-overlay') || null
  return {
    listens: { window: listeners.window.map((l) => [l.type, l.capture]), document: listeners.document.map((l) => [l.type, l.capture]) },
    mo: mos.map((m) => m.opts),
    posted, sent, blocked, scrolled,
    layer: layer && { label: layer.attrs['aria-label'] || null, fixed: layer.style.position, pass: layer.style.pointerEvents, z: layer.style.zIndex },
    boxes: layer ? layer.kids.map((k) => ({
      id: k.attrs['data-kt-area'], cls: k.className, left: k.style.left, top: k.style.top, width: k.style.width, height: k.style.height,
      border: k.style.border, borderWidth: k.style.borderWidth || null, fill: k.style.backgroundColor, hatch: k.style.backgroundImage,
      tag: k.kids[0].textContent, font: k.kids[0].style.font, tagTop: k.kids[0].style.top,
    })) : null,
  }
})))
"""


def _run(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 attention.js 를 돌리지 못한다")
    for case in cases:
        case.setdefault("admin", ADMIN)
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps([JS, cases]),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _one(**case: Any) -> dict:
    return _run([case])[0]


def _msg(data: dict, origin: str = ADMIN) -> str:
    return f"msg|{origin}|{json.dumps(data)}"


# 화면 안 영역 둘 + 크기 0(숨은 탭) 하나
AREAS = [
    ["top", 0, 0, 1280, 60],
    ["hero", 0, 60, 1280, 500],
    ["table", 0, 0, 0, 0],
]


def _value(i: str, level: int, rank: int | None, **extra: Any) -> dict:
    return {
        "id": i,
        "level": level,
        "rank": rank,
        "t": 3.2,
        "r": 64,
        "c": 12.5,
        "low": False,
        "small": False,
        **extra,
    }


def _values(*areas: dict, label: str = "최근 7일 · 랜딩 · PC") -> str:
    return _msg({"type": "kt-attention", "v": 1, "label": label, "areas": list(areas)})


VALUES = _values(_value("hero", 5, 1), _value("top", 2, 2))


def test_overlay_needs_both_the_flag_and_a_frame_and_never_counts() -> None:
    """§3.4 — `kt-overlay=1` 이고 틀 안일 때만 켜진다. 이 모드는 세기 리스너(입력·숨김·탭·동의)도, 보이는 영역 관찰도,
    보내기도 없다 — 동의한 브라우저가 숨겨도 비콘 0."""
    got = _one(ls=ON, areas=AREAS, steps=["hide"])
    assert sorted(got["listens"]["window"]) == sorted(
        [
            ["click", True],
            ["auxclick", True],
            ["submit", True],
            ["dblclick", True],
            ["message", False],
            ["resize", False],
        ]
    )
    assert got["listens"]["document"] == [["scroll", True]]
    assert got["mo"] == [{"childList": True, "subtree": True}]
    assert got["sent"] == []
    for case in (
        {"framed": False},  # 덮어 보기 표시만
        {"search": ""},  # 틀 안이지만 표시 없음
        {"host": "www.kimptrack.com"},
        {"path": "/404.html"},
    ):
        other = _one(ls=ON, areas=AREAS, **case)
        assert other["listens"] == {"window": [], "document": []}, case
        assert other["posted"] == [] and other["sent"] == [], case


def test_presses_are_stopped_before_the_page_sees_them() -> None:
    """§3.4 — window 캡처 단계의 click·auxclick·submit·dblclick 이 기본 동작과 전파를 모두 막는다."""
    got = _one(areas=AREAS, steps=["press|click", "press|auxclick", "press|submit", "press|dblclick"])
    assert got["blocked"] == {
        name: [True, True] for name in ("click", "auxclick", "submit", "dblclick")
    }


def test_ready_names_the_sized_areas_and_is_sent_again_when_they_change() -> None:
    """§3.4 — 문서를 읽은 뒤 한 번(크기 있는 영역 id — 크기 0 은 뺀다), 영역 모임이 바뀌면 다시(1초에 한 번까지),
    같으면 다시 보내지 않는다. 대상 출처는 관리자 출처 하나다."""
    got = _one(
        path="/app/",
        search="?tab=history&kt-overlay=1",
        areas=AREAS,
        steps=[
            "mutate",
            "t|1000",
            "timers",  # 모임 그대로 — 다시 안 보냄
            'add|["chart", 0, 600, 1280, 300]',
            "t|1000",
            "timers",
        ],
    )
    assert [p["origin"] for p in got["posted"]] == [ADMIN, ADMIN]
    assert [p["data"] for p in got["posted"]] == [
        {"type": "kt-attention-ready", "v": 1, "page": "app-history", "areas": ["top", "hero"]},
        {
            "type": "kt-attention-ready",
            "v": 1,
            "page": "app-history",
            "areas": ["top", "hero", "chart"],
        },
    ]


def test_messages_from_another_origin_or_window_are_ignored() -> None:
    """§3.4 — 출처가 관리자가 아니거나 창이 부모가 아니면 그리지도 옮기지도 않는다."""
    focus = {"type": "kt-attention-focus", "v": 1, "id": "hero"}
    got = _one(
        areas=AREAS,
        steps=[
            VALUES.replace(ADMIN, "https://kimptrack.com"),
            _msg(focus, "https://evil.example"),
            f"msgfrom|{json.dumps({'type': 'kt-attention', 'v': 1, 'areas': []})}",
            "flush",
        ],
    )
    assert got["layer"] is None and got["scrolled"] == []


def test_boxes_cover_each_sized_area_with_level_font_sizes() -> None:
    """§3.4 — 값이 오면 맨 위 고정·누름 통과 층에 크기 있는 영역마다 상자 하나(그 자리), 꼬리표 글씨는 단계 0~5 에
    12·12·14·16·19·22px, 꼬리표 글은 '#순위 · 평균 n초 · 도달 n% · 클릭 n'. 값이 없는 영역은 '기록 없음'·점선."""
    levels = [0, 1, 2, 3, 4, 5]
    areas = [[f"a{n}", 0, 100 * n, 400, 80] for n in levels]
    msgs = _values(*(_value(f"a{n}", n, 6 - n) for n in levels[1:]))
    got = _one(areas=[*areas, ["table", 0, 0, 0, 0]], steps=[msgs, "flush"])
    assert got["layer"] == {
        "label": "최근 7일 · 랜딩 · PC",
        "fixed": "fixed",
        "pass": "none",
        "z": "2147483647",
    }
    boxes = got["boxes"]
    assert [b["id"] for b in boxes] == [f"a{n}" for n in levels]
    sizes = [12, 12, 14, 16, 19, 22]
    for n, b in zip(levels, boxes, strict=True):
        assert f" {sizes[n]}px/" in b["font"], (n, b["font"])
        assert f"kt-ov-l{n}" in b["cls"]
        assert (b["left"], b["top"], b["width"], b["height"]) == (
            "0px",
            f"{100 * n}px",
            "400px",
            "80px",
        )
    assert boxes[0]["tag"] == "기록 없음"
    assert boxes[0]["border"].startswith("2px dashed") and boxes[0]["fill"] == "transparent"
    assert boxes[5]["tag"] == "#1 · 평균 3.2초 · 도달 64% · 클릭 12.5"
    assert boxes[5]["border"].startswith("4px solid") and boxes[5]["fill"].endswith(",.5)")
    assert boxes[1]["fill"].endswith(",.18)")
    # 값이 오기 전에는 그리지 않는다
    assert _one(areas=areas, steps=["flush"])["layer"] is None


def test_low_reach_hatches_small_samples_say_so_and_tags_follow_the_scroll() -> None:
    """§3.4 — 거의 안 닿음은 빗금, 표본 적음은 꼬리표 끝 '· 표본 적음'(기록 없음에도), 위로 스크롤된 상자의 꼬리표는
    화면 맨 위에 붙는다. 스크롤마다 다시 놓는다."""
    got = _one(
        areas=AREAS,
        steps=[
            _values(
                _value("hero", 3, 1, low=True, small=True),
                _value("top", 0, None, small=True),
            ),
            "flush",
            "scroll|100",
            "flush",
        ],
    )
    top, hero = got["boxes"]
    assert hero["hatch"].startswith("repeating-linear-gradient(") and "kt-ov-low" in hero["cls"]
    assert top["hatch"] == "none"
    assert hero["tag"].endswith(" · 표본 적음")
    assert top["tag"] == "기록 없음 · 표본 적음"
    assert (hero["top"], hero["tagTop"]) == ("-40px", "40px")
    assert (top["top"], top["tagTop"]) == ("-100px", "100px")


def test_focus_scrolls_to_the_area_and_thickens_its_border_for_two_seconds() -> None:
    """§3.4 — focus 를 받으면 그 영역을 가운데로 scrollIntoView, 상자 테두리 2초 굵게. 없는 영역이면 아무것도."""
    focus = {"type": "kt-attention-focus", "v": 1, "id": "hero"}
    got = _one(
        areas=AREAS,
        steps=[VALUES, _msg(focus), _msg({**focus, "id": "nope"}), "flush"],
    )
    assert got["scrolled"] == [["hero", {"block": "center"}]]
    hero = next(b for b in got["boxes"] if b["id"] == "hero")
    assert hero["borderWidth"] == "6px"
    later = _one(areas=AREAS, steps=[VALUES, _msg(focus), "t|2000", "timers", "flush"])
    assert all(b["borderWidth"] is None for b in later["boxes"])


def test_bad_entries_are_dropped_and_their_areas_show_no_record() -> None:
    """받은 값은 꼴을 검사한다 — 단계 밖·순위 꼴·수 아님은 버리고 그 영역은 '기록 없음'."""
    got = _one(
        areas=AREAS,
        steps=[
            _values(_value("hero", 7, 1), _value("top", 2, "1")),
            "flush",
        ],
    )
    assert [b["tag"] for b in got["boxes"]] == ["기록 없음", "기록 없음"]
