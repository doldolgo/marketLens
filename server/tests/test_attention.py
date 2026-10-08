"""화면 영역 이용 통계 계약 — 화면 이름·nginx·RedisBus·싣는 곳·data-area 목록을 파일로 읽어 단언한다 (스펙 052 §4).

화면 이름 목록(서버 상수)·App.tsx 의 TabId·attention.js 의 목록이 어긋나거나, 정적 페이지가 attention.js 말고 다른 파일
스크립트를 싣거나, 영역 id 가 꼴·겹침·품음·개수 규칙을 어기면 여기서 멈춘다. 영역 목록은 053 의 이름표가 쓰는 계약이라
화면마다 그대로 적어 둔다(스펙 §7 '붙인 영역 목록' 과 같다). attention.js 의 동작은 test_attention_js.py 가 node 로 돌린다.
"""

import inspect
import json
import re
from html.parser import HTMLParser
from typing import get_type_hints

from app.core.redis_bus import RedisBus
from app.features.attention.models import PAGES, TABS
from tests.test_deploy import API, ROOT, _args, _locations, _public_server

WEB = ROOT / "web"
PUBLIC = WEB / "public"
JS = (PUBLIC / "attention.js").read_text("utf-8")
AREA_ID = re.compile(r"[a-z][a-z0-9-]{0,31}")
STATIC_PAGES = {
    "landing": "landing.html",
    "privacy": "privacy.html",
    "kimp-chart": "kimp-chart.html",
    "kimp-history": "kimp-history.html",
}
# 053 이 이름표로 쓰는 영역 목록 — 문서 순서. 바꾸면 스펙 §7 과 053 의 이름표를 함께 고친다
STATIC_AREAS = {
    "landing": ["top", "hero", "analyze", "events", "kimp", "method", "faq", "foot"],
    "privacy": [
        "top",
        "changes",
        "glance",
        "consent",
        *(f"s{n}" for n in range(1, 13)),
        "foot",
    ],
    "kimp-chart": ["top", "what", "read", "coins", "faq", "foot"],
    "kimp-history": ["top", "live", "what", "how", "faq", "foot"],
}
DASHBOARD_COMMON = ["header", "tabs", "kpi"]
# 탭 → (영역을 다는 파일, 영역) — 파일 안 문서 순서
DASHBOARD_TABS = {
    "spread": (["features/spreads/Tab.tsx"], ["filters", "table"]),
    "history": (
        ["features/history/Tab.tsx", "features/history/Chart.tsx"],
        ["filters", "table", "summary", "list", "chart", "filters-2"],
    ),
    "gap": (["features/gap/Tab.tsx"], ["filters", "table"]),
    "pp": (["features/pp/Tab.tsx"], ["filters", "table"]),
    "health": (["features/health/Tab.tsx"], ["chart", "list", "summary", "cards"]),
    "flow": (["features/flow/Tab.tsx"], ["filters", "table", "table-2"]),
}
TAB_WORDS = {
    "filters",
    "table",
    "chart",
    "list",
    "cards",
    "summary",
    "detail",
    "legend",
}


# --- 화면 이름 (§3.1) ---------------------------------------------------------------


def _tab_ids() -> list[str]:
    app = (WEB / "src/App.tsx").read_text("utf-8")
    found = re.search(r"type TabId = ([^\n]+)\n", app)
    assert found, "App.tsx 에 TabId 가 없다"
    return re.findall(r"'([a-z]+)'", found.group(1))


def test_page_names_are_the_six_tabs_and_the_four_static_screens() -> None:
    assert list(TABS) == _tab_ids()
    assert PAGES == ("landing", *(f"app-{t}" for t in TABS), *list(STATIC_PAGES)[1:])
    # attention.js 도 같은 목록 — 탭 id 와 정적 경로 넷
    tabs = re.search(r"const TABS = (\[[^\]]*\])", JS)
    assert tabs and json.loads(tabs.group(1)) == list(TABS)
    static = dict(re.findall(r'\["(/[a-z-]*)", "([a-z-]+)"\]', JS))
    assert static == {
        "/": "landing",
        "/privacy": "privacy",
        "/kimp-chart": "kimp-chart",
        "/kimp-history": "kimp-history",
    }


def test_notice_version_is_the_clarity_one() -> None:
    clarity = (PUBLIC / "clarity.js").read_text("utf-8")
    mine = re.search(r'const NOTICE_VERSION = "([^"]+)"\n', JS)
    theirs = re.search(r'const NOTICE_VERSION = "([^"]+)"\n', clarity)
    assert mine and theirs and mine.group(1) == theirs.group(1)


def test_script_sends_nothing_but_the_beacon_and_keeps_no_visitor_state() -> None:
    """좌표·글자·입력값·저장소 쓰기·쿠키·다른 주소 없음 (§2 하지 않는 것). 053 의 덮어 보기 부분(파일 끝)은 세지 않고
    관리자 출처 하나와만 메시지를 주고받는다 — 꼬리표 글자를 textContent 로 쓰는 것 말고는 같은 금지를 따른다."""
    assert set(re.findall(r"[\"'](/api/[\w/-]*)", JS)) == {"/api/attention"}
    # 주소 글자는 덮어 보기의 관리자 출처 상수 하나뿐
    assert re.findall(r"https?://[^\s\"']*", JS) == ["https://admin.kimptrack.com"]
    assert JS.count('const ADMIN_ORIGIN = "https://admin.kimptrack.com"') == 1
    marker = "// ── 덮어 보기 (053)"
    assert JS.count(marker) == 1
    counting, overlay = JS.split(marker)
    for banned in ("setItem", "removeItem", "cookie", "sessionStorage", "innerHTML"):
        assert banned not in overlay, banned
    for banned in ("sendBeacon", "fetch(", ".value", "XMLHttpRequest", "clientX"):
        assert banned not in overlay, banned
    # 보내는 메시지는 부모 창의 관리자 출처로만 — '*' 없음
    assert overlay.count("postMessage(") == 1
    assert "window.parent.postMessage(" in overlay and "ADMIN_ORIGIN)" in overlay
    assert "'*'" not in overlay and '"*"' not in overlay
    for banned in (
        "setItem",
        "removeItem",
        "cookie",
        "sessionStorage",
        "indexedDB",
        "clientX",
        "clientY",
        "pageX",
        "pageY",
        "screenX",
        "offsetX",
        "innerText",
        "textContent",
        "innerHTML",
        ".value",
        "setInterval",
        "XMLHttpRequest",
    ):
        assert banned not in counting, banned


# --- nginx·caddy (§3.2·§3.4·§3.6) ----------------------------------------------------


def test_public_beacon_location_is_post_only_4k_and_passes_the_visitor_ip() -> None:
    block = _locations(_public_server())[("=", "/api/attention")]
    guards = [c for args, c in block if args[0] == "if"]
    assert [args for args, _ in block if args[0] == "if"] == [
        ["if", "($request_method", "!=", "POST)"]
    ]
    assert guards == [[(["return", "405"], None)]]
    # if 는 location 의 첫 줄 — 다른 지시어보다 먼저
    assert block[0][0][0] == "if"
    assert _args(block, "client_max_body_size") == [["4k"]]
    assert _args(block, "proxy_pass") == [[API]]
    assert ["X-Client-IP", "$http_x_forwarded_for"] in _args(block, "proxy_set_header")


def test_attention_script_location_revalidates_like_clarity() -> None:
    locations = _locations(_public_server())
    block = locations[("=", "/attention.js")]
    assert block == locations[("=", "/clarity.js")]
    assert _args(block, "add_header") == [["Cache-Control", "no-cache", "always"]]


# --- RedisBus 계약 (§3.5) -------------------------------------------------------------


def test_redis_bus_has_the_two_async_methods() -> None:
    add = RedisBus.attention_add
    days = RedisBus.attention_days
    assert inspect.iscoroutinefunction(add) and inspect.iscoroutinefunction(days)
    assert list(inspect.signature(add).parameters) == [
        "self",
        "day",
        "counts",
        "expire_at",
    ]
    assert list(inspect.signature(days).parameters) == ["self", "days"]
    hints = get_type_hints(add)
    assert (
        hints["day"] is str
        and hints["expire_at"] is int
        and hints["return"] is type(None)
    )
    assert str(hints["counts"]) == "collections.abc.Mapping[str, int]"
    assert str(get_type_hints(days)["return"]) == "dict[str, dict[str, int]]"


# --- 싣는 곳 (§3.2) -------------------------------------------------------------------


def _script_tags(html: str) -> list[str]:
    return re.findall(r"<script\b[^>]*>", html)


def test_landing_and_dashboard_load_attention_right_after_clarity() -> None:
    for name, html, src in (
        ("index.html", (WEB / "index.html").read_text("utf-8"), "/attention.js"),
        ("landing.html", (PUBLIC / "landing.html").read_text("utf-8"), "attention.js"),
    ):
        tags = _script_tags(html)
        clarity = next(i for i, t in enumerate(tags) if "clarity.js" in t)
        assert tags[clarity + 1] == f'<script defer src="{src}">', name
        assert sum("attention.js" in t for t in tags) == 1, name


def test_static_pages_load_only_attention_js_as_a_file_in_the_head() -> None:
    for page in ("privacy", "kimp-chart", "kimp-history"):
        html = (PUBLIC / STATIC_PAGES[page]).read_text("utf-8")
        head = html[: html.index("</head>")]
        files = re.findall(r"<script\b[^>]*\bsrc=\"([^\"]*)\"", html)
        assert files == ["/attention.js"], page
        assert '<script defer src="/attention.js"></script>' in head, page
        assert "clarity.js" not in "".join(_script_tags(html)), page


def test_old_policy_copies_and_404_load_no_script() -> None:
    for path in (*PUBLIC.glob("privacy-*.html"), PUBLIC / "404.html"):
        assert "attention.js" not in path.read_text("utf-8"), path.name


# --- 영역 (§3.1) ----------------------------------------------------------------------


class _Areas(HTMLParser):
    """data-area 요소를 문서 순서로 — 열린 영역 안에서 또 영역이 열리면 품음이다."""

    VOID = {"meta", "link", "img", "br", "hr", "input", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.areas: list[str] = []
        self.nested: list[tuple[str, str]] = []
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        area = dict(attrs).get("data-area")
        if area is not None:
            inside = [a for _, a in self._stack if a is not None]
            if inside:
                self.nested.append((inside[-1], area))
            self.areas.append(area)
        if tag not in self.VOID:
            self._stack.append((tag, area))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return


def test_static_pages_have_the_listed_areas_without_nesting() -> None:
    for page, file in STATIC_PAGES.items():
        parser = _Areas()
        parser.feed((PUBLIC / file).read_text("utf-8"))
        assert parser.areas == STATIC_AREAS[page], page
        assert not parser.nested, (page, parser.nested)
        assert len(parser.areas) == len(set(parser.areas)) <= 40, page
        assert all(AREA_ID.fullmatch(a) for a in parser.areas), page


def _jsx_areas(rel: str) -> list[str]:
    text = (WEB / "src" / rel).read_text("utf-8")
    return re.findall(r'(?:data-area|\barea)="([^"]*)"', text)


def test_dashboard_areas_follow_the_naming_rule_per_tab() -> None:
    assert _jsx_areas("App.tsx") == DASHBOARD_COMMON
    for tab, (files, areas) in DASHBOARD_TABS.items():
        found = [a for rel in files for a in _jsx_areas(rel)]
        assert found == areas, tab
        assert 2 <= len(found) <= 6, tab
        screen = DASHBOARD_COMMON + found
        assert len(screen) == len(set(screen)) <= 40, tab
        for area in found:
            word = area.removesuffix("-2")
            assert word in TAB_WORDS and AREA_ID.fullmatch(area), (tab, area)
    # 탭 자리(Pane — display: contents)와 공유 조각에는 영역 id 를 박지 않는다 — 탭이 정한다
    shared = (WEB / "src/shared/ui.tsx").read_text("utf-8")
    assert re.findall(r"data-area=\{(\w+)\}", shared) == ["area"]
    assert 'data-area="' not in shared


def test_app_announces_tab_changes_but_not_the_first_render() -> None:
    app = (WEB / "src/App.tsx").read_text("utf-8")
    assert "window.dispatchEvent(new CustomEvent('kt:tab', { detail: tab }))" in app
    effect = app[app.index("const attentionTabRef = useRef(tab)") :]
    effect = effect[: effect.index("}, [tab])")]
    # 처음 값이 지금 탭이라 첫 그리기에는 같아서 내지 않는다
    assert "if (attentionTabRef.current === tab) return" in effect
