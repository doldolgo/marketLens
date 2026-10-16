"""관리자 화면 v4 정적 단언(스펙 066 §4 정적) — 페이지 여섯의 내비·현재 표시·같은 틀, 기간·비교 단추, 아이콘 묶음(로컬),
인라인 스크립트 없음, 링크, 스크립트 금지 글자(HTML 해석·저장소·주소 쓰기는 정해진 곳에만), 경로 표, 토큰(조사 §5 그대로)·
상자 색 고정, 도넛·반원 게이지 없음(예산 하나만). CSP·nginx 분기는 test_admin.py 가, 계산·부르기·그리기는 node 시험이 본다.
"""

import hashlib
import re

from tests.test_deploy import ROOT, _text

SCREEN = ROOT / "web/admin"
# 페이지 → (고정 주소, 모듈, 상세 판 틀이 있는가)
PAGES = {
    "index.html": ("/", "overview.js", True),
    "server.html": ("/server.html", "server.js", True),
    "traffic.html": ("/traffic.html", "traffic.js", True),
    "visitors.html": ("/visitors.html", "visitors.js", True),
    "clarity.html": ("/clarity.html", "insight.js", True),
    "screens.html": ("/screens.html", "screens.js", True),
}
# 차트가 없어 ECharts 를 싣지 않는 페이지(065 화면 — 상세 판은 표만)
NO_LIBRARY = {"screens.html"}
NAV = [
    ("/", "개요", "overview"),
    ("/server.html", "서버", "server"),
    ("/traffic.html", "트래픽", "traffic"),
    ("/visitors.html", "방문자", "visitors"),
    ("/clarity.html", "Clarity", "clarity"),
    ("/screens.html", "화면", "screens"),
]
# 페이지 → (기간 프리셋, 기본) — 피드가 주는 것만(조사 §3.2)
RANGES = {
    "index.html": (["1h", "6h", "24h"], "24h"),
    "server.html": (["6h", "24h", "7d", "30d"], "24h"),
    "traffic.html": (["24h", "7d", "30d"], "24h"),
    "visitors.html": (["24h", "7d", "30d"], "24h"),
    "clarity.html": (["7d", "30d", "90d"], "30d"),
    "screens.html": (["1d", "7d", "30d", "90d"], "7d"),
}
MODULES = (
    "common.js",
    "names.js",
    "rules.js",
    "calc.js",
    "charts.js",
    "ui.js",
    "view.js",
    "store.js",
    "app.js",
    "tables.js",
    "detail.js",
    "models.js",
    "ops.js",
    "opsdetail.js",
    "trafficmodel.js",
    "trafficdetail.js",
    "ipmodel.js",
    "addrmodel.js",
    "addrdetail.js",
    "addrs.js",
    "insightdetail.js",
    "screensnames.js",
    "screensmodel.js",
    "screensdetail.js",
    "screenspanel.js",
    "help.js",
    "replaymodel.js",
    "replayframe.js",
    "replayview.js",
    "replaysheet.js",
    "replaycards.js",
)
SCRIPTS = (*MODULES, *(module for _, module, _ in PAGES.values()))
VENDOR = "vendor/echarts-5.5.1.min.js"
# npm 꾸러미 echarts@5.5.1 의 dist/echarts.min.js 그대로(고치지 않는다 — 064 §2)
VENDOR_SHA256 = "e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf"
EXTERNAL_HOSTS = {
    "clarity.microsoft.com",
    "dash.cloudflare.com",
    "one.dash.cloudflare.com",
    "github.com",
    "db-ip.com",
}


def _script(name: str) -> str:
    return _text(f"web/admin/{name}")


def _every() -> str:
    return "".join(_script(name) for name in SCRIPTS)


def test_screen_is_six_pages_modules_one_icon_sprite_and_the_vendored_library() -> None:
    """066 §2 — 페이지 여섯 + 모듈 + 스타일 + 아이콘 묶음 하나 + vendor(ECharts 5.5.1 사본·라이선스). 공개 root 밖."""
    files = sorted(str(p.relative_to(SCREEN)) for p in SCREEN.rglob("*") if p.is_file())
    expected = [*PAGES, *SCRIPTS, "admin.css", "icons.svg", VENDOR]
    expected += [
        "vendor/ECHARTS-LICENSE.txt",
        "vendor/ECHARTS-NOTICE.txt",
        "vendor/ECHARTS-LICENSE-d3.txt",
    ]
    assert files == sorted(expected)
    assert "COPY admin /usr/share/nginx/admin" in _text("web/Dockerfile").splitlines()
    public = {p.name for p in (ROOT / "web/public").rglob("*")}
    assert not {"admin", *SCRIPTS, "admin.css", "icons.svg"} & public


def test_vendored_echarts_is_the_untouched_5_5_1_build_with_its_license() -> None:
    """dist/echarts.min.js 를 고치지 않고(체크섬), Apache-2.0 원문과 NOTICE·d3 BSD 를 함께. 가짜 encodeHTML 은 사본과 같은 바꿈."""
    data = (SCREEN / VENDOR).read_bytes()
    assert hashlib.sha256(data).hexdigest() == VENDOR_SHA256
    text = data.decode("utf-8")
    assert 'version="5.5.1"' in text and "registerTheme" in text
    assert re.search(
        r"""/\(\[&<>"'\]\)/g,\w+=\{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"\}""",
        text,
    )
    license_text = _text("web/admin/vendor/ECHARTS-LICENSE.txt")
    assert (
        license_text.lstrip().startswith("Apache License")
        and "Version 2.0, January 2004" in license_text
    )
    assert "Apache ECharts" in _text("web/admin/vendor/ECHARTS-NOTICE.txt")
    assert "Mike Bostock" in _text("web/admin/vendor/ECHARTS-LICENSE-d3.txt")
    harness = _text("server/tests/admin_dom.py")
    assert "replace(/([&<>\"'])/g" in harness and "'&#39;'" in harness


NAV_LINK = re.compile(
    r'<a href="([^"]+)"( aria-current="page")? title="([^"]+)"><svg class="i" aria-hidden="true"><use href="icons\.svg#([\w-]+)"/></svg>'
    r'<span>([^<]+)</span>(?:<i class="mark" id="m-(\w+)"></i>)?</a>'
)


def test_every_page_has_the_same_left_nav_shell_with_its_own_link_marked() -> None:
    """066 §3.1 — 왼쪽 세로 내비(아이콘 + 이름, 지금 페이지 강조, 개요·서버·트래픽 옆 상태 모양 자리), 아래 마지막 갱신·로그아웃,
    좁은 화면 ☰, 머리('10초마다 · 시각'), 거르기 칩 줄. 틀(내비·머리 위)과 상세 판은 페이지마다 같은 글자다."""
    shells, sheets = set(), set()
    for page, (home, _, sheet) in PAGES.items():
        html = _text(f"web/admin/{page}")
        nav = re.search(
            r'<nav class="nav" aria-label="페이지">(.*?)</nav>', html, flags=re.S
        )
        assert nav, page
        links = NAV_LINK.findall(nav.group(1))
        assert [(h, label, icon) for h, _, _, icon, label, _ in links] == NAV, page
        assert all(title == label for _, _, title, _, label, _ in links), page
        assert [h for h, current, *_ in links if current] == [home], page
        assert [m for *_, m in links if m] == ["overview", "server", "traffic"], page
        for frag in (
            '<div class="app" id="app" data-menu="closed">',
            '<span id="updated">--:--:--</span>',
            '<a href="/cdn-cgi/access/logout" title="로그아웃">',
            '<button type="button" class="ibtn" id="menu-btn" aria-label="메뉴" aria-expanded="false" aria-controls="side">',
            '<span class="tick">10초마다 · <span id="clock">--:--:--</span></span>',
            '<div class="filters" id="filters" hidden></div>',
            '<p id="notice" class="notice" role="alert" hidden></p>',
            '<main class="content" id="content">',
        ):
            assert html.count(frag) == 1, (page, frag)
        shells.add(
            re.sub(
                r' aria-current="page"',
                "",
                html[
                    html.index('<div class="app"') : html.index('<header class="head">')
                ],
            )
        )
        assert ('<div class="sheet" id="sheet" hidden>' in html) == sheet, page
        if sheet:
            sheets.add(
                html[
                    html.index('<div class="sheet" id="sheet" hidden>') : html.index(
                        "</body>"
                    )
                ]
            )
    assert len(shells) == 1 and len(sheets) == 1
    (sheet,) = sheets
    for frag in (
        'role="dialog" aria-modal="true"',
        'id="c-sheet"',
        'id="sheet-csv"',
        'id="sheet-close"',
        'id="sheet-stat-tbl"',
        'id="sheet-tbl"',
        'data-scmp="prev"',
    ):
        assert frag in sheet, frag


def test_head_has_range_presets_and_compare_matching_each_page_module() -> None:
    """066 §3.1·조사 §3.2 — 머리 오른쪽에 그 피드가 주는 기간 프리셋, '비교'(없음·이전 기간·지난주, 단축키 X). 모듈의 값과 같다."""
    for page, (ranges, default) in RANGES.items():
        html = _text(f"web/admin/{page}")
        buttons = re.findall(
            r'<button type="button" data-range="(\w+)" aria-pressed="(true|false)">',
            html,
        )
        assert [r for r, _ in buttons] == ranges, page
        assert [r for r, pressed in buttons if pressed == "true"] == [default], page
        assert re.findall(r'data-cmp="(\w+)"', html) == ["none", "prev", "week"], page
        assert 'data-cmp="prev" aria-pressed="false" title="단축키 X"' in html, page
        module = _script(PAGES[page][1])
        quoted = ", ".join(f"'{r}'" for r in ranges)
        assert (
            f"ranges: [{quoted}]" in module
            or "ranges: ACCESS_RANGES" in module
            or "ranges: IP_RANGES" in module
            or "ranges: HISTORY_RANGES" in module
        ), page
        assert f"range: '{default}'," in module, page


def test_icons_are_one_local_sprite_and_every_use_points_into_it() -> None:
    """066 §2 — 아이콘은 직접 그린 SVG 묶음 하나(`<use href="icons.svg#이름">`) — 글꼴 아이콘·CDN·다른 파일 참조 없음, 스크립트는 아이콘을 만들지 않는다."""
    sprite = _text("web/admin/icons.svg")
    assert sprite.startswith('<svg xmlns="http://www.w3.org/2000/svg">')
    assert "http" not in sprite.replace("http://www.w3.org/2000/svg", "")
    body = re.sub(r"<!--.*?-->", "", sprite, flags=re.S)
    assert (
        "href" not in body
        and "<image" not in body
        and "<script" not in body
        and "<style" not in body
    )
    symbols = set(re.findall(r'<symbol id="([\w-]+)" viewBox="0 0 24 24"', body))
    used = set()
    for page in PAGES:
        html = re.sub(r"<!--.*?-->", "", _text(f"web/admin/{page}"), flags=re.S)
        for use in re.findall(r"<use\b[^>]*>", html):
            m = re.fullmatch(r'<use href="icons\.svg#([\w-]+)"/>', use)
            assert m and m.group(1) in symbols, (page, use)
            used.add(m.group(1))
        assert "xlink" not in html, page
    assert (
        used <= symbols
        and {
            "overview",
            "server",
            "traffic",
            "visitors",
            "clarity",
            "screens",
            "expand",
            "close",
            "download",
        }
        <= used
    )
    css = _text("web/admin/admin.css")
    assert "@font-face" not in css and "url(" not in css and "@import" not in css
    assert "icons.svg" not in _every() and "'use'" not in _every()


def test_pages_load_the_library_then_their_module_without_inline_code() -> None:
    """064 §3.1 그대로 — 인라인 스크립트·스타일·on* 없음(CSP 'self'), 라이브러리(defer) 뒤에 그 페이지의 ES 모듈 하나. 설명은 '?' title 두 줄(071)."""
    for page, (_, module, sheet) in PAGES.items():
        html = _text(f"web/admin/{page}")
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S)
        expected = [(f' type="module" src="{module}"', "")]
        if sheet and page not in NO_LIBRARY:
            expected.insert(0, (f' src="{VENDOR}" defer', ""))
        assert scripts == expected, page
        assert "<style" not in html and "style=" not in html, page
        assert re.search(r"\son\w+\s*=", html) is None and "<form" not in html, page
        assert html.count('<link rel="stylesheet" href="admin.css" />') == 1, page
        assert "이 절 읽는 법" not in html and "이 칸 뜻" not in html, page
        # 071 §3.2 — '?' 글은 두 줄(뜻 · 읽는 법, `&#10;` 로 나눔), 줄마다 120자 이하
        for title in re.findall(r'<span class="help" title="([^"]*)">', html):
            lines = title.split("&#10;")
            assert len(lines) == 2 and all(0 < len(line) <= 120 for line in lines), (
                page,
                title,
            )


def _anchors(html: str) -> list[dict[str, str]]:
    out = []
    for attrs in re.findall(r"<a\b([^>]*)>", html):
        pairs = re.findall(
            r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", attrs
        )
        found = {k.lower(): a or b or c for k, a, b, c in pairs}
        assert "href" in found, f"href 를 못 읽는 링크: <a{attrs}>"
        out.append(found)
    return out


def test_links_are_fixed_addresses_and_none_opens_a_new_window() -> None:
    """036 §3.8 그대로 — 밖으로 나가는 링크는 고정 https·noreferrer·같은 탭, 계정·팀·프로젝트 ID 없음(레포 공개). 새 창 없음 —
    065 화면의 '새 창으로 ↗' 하나만 예외(그 페이지 주소, noopener — 주소는 app.js 가 지금 보기로 맞춘다)."""
    for page in PAGES:
        html = _text(f"web/admin/{page}")
        for a in _anchors(html):
            href = a["href"]
            if a.get("id") == "here":
                assert page == "screens.html" and a == {
                    "class": "out",
                    "id": "here",
                    "href": "/screens.html",
                    "target": "_blank",
                    "rel": "noopener",
                }, a
                continue
            assert "target" not in a, (page, href)
            if href.startswith("/") and not href.startswith("//"):
                continue
            assert href.startswith("https://") and a.get("rel") == "noreferrer", href
            host = href.split("/")[2]
            assert host in EXTERNAL_HOSTS or host.endswith(".console.aws.amazon.com"), (
                href
            )
            if "cloudflare.com" in host:
                assert href == f"https://{host}/", (
                    href
                )  # 대시보드 주소에는 계정 ID 가 든다 — 루트만
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", html), page
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", html), page
        assert not re.search(r"[0-9a-f]{32,}", html), page
    assert (
        _text("web/admin/traffic.html").count(
            '<a href="https://db-ip.com" rel="noreferrer">IP Geolocation by DB-IP</a>'
        )
        == 1
    )
    assert (
        _text("web/admin/clarity.html").count(
            '<a class="out" href="https://clarity.microsoft.com/projects" rel="noreferrer">'
        )
        == 1
    )
    assert '<a href="/visitors.html">방문자</a>' in _text("web/admin/clarity.html")
    server = _text("web/admin/server.html")
    assert '<a href="/api/docs">' in server and '<a href="/api/redoc">' in server
    assert re.search(r'<input id="token" type="password" autocomplete="off"', server)


def _rule(css: str, selector: str) -> str:
    """admin.css 에서 선택자 하나의 맨 바깥(미디어 쿼리 밖) 규칙 몸."""
    match = re.search(rf"^{re.escape(selector)} \{{\n(.*?)^\}}", css, re.S | re.M)
    assert match, selector
    return match.group(1)


def test_controls_wrap_inside_their_own_box_on_narrow_screens() -> None:
    """066 §3.4 — 360px 에도 가로 넘침 없음: 고르기(망 종류 여섯 등)·카드 안 탭·카드 머리는 자리가 모자라면 자기 상자 안에서
    줄을 바꾸고, 입력 칸은 상자보다 넓어지지 않는다. 표는 카드 안 가로 스크롤(.scroll) 안에만 있다."""
    css = _text("web/admin/admin.css")
    for selector in (".seg", ".tabs", ".card-head:has(.tabs)"):
        assert "flex-wrap: wrap;" in _rule(css, selector), selector
    # 탭 없는 카드 머리는 넓은 화면에서 한 줄(단위 글이 줄임표로 준다) — 좁은 화면(767px 아래)에서만 줄을 바꾼다
    assert "flex-wrap" not in _rule(css, ".card-head")
    narrow = css[css.index("@media (max-width: 767px) {") :]
    assert re.search(r"^  \.card-head \{\n    flex-wrap: wrap;\n  \}", narrow, re.M)
    for selector in (".seg", ".tabs", ".input", ".search", ".end", ".tag"):
        assert "max-width: 100%;" in _rule(css, selector), selector
    assert "nowrap" not in _rule(css, ".tag")  # 긴 상태 글은 배지 안에서 줄 바꿈
    for page in PAGES:
        html = _text(f"web/admin/{page}")
        tables = re.findall(r"(.{0,22})<table\b", html)
        assert all(before.endswith('<div class="scroll">') for before in tables), page


def test_overview_box_table_spans_the_row_so_all_six_columns_show() -> None:
    """066 §3.3 개요 상자 표(상자 | CPU·메모리·디스크 | 크레딧 | CPU 선) — 1280px 이상에서 안쪽 가로 스크롤 없이 여섯 칸이 다
    보이게 한 줄 전체(s12). 옆에 있던 수집 원천 띠도 한 줄 전체(반쪽 카드 혼자 남지 않게). 안쪽 스크롤은 좁은 화면에서만."""
    html = _text("web/admin/index.html")
    for card in ("card-ex", "card-boxes"):
        assert re.search(rf'<section class="card s12" id="{card}"', html), card


def test_clarity_six_kpis_fill_whole_rows() -> None:
    """066 §3.3 Clarity 큰 수 여섯 — 1280px 이상 한 줄 여섯, 768~1279px 셋씩 두 줄, 768px 아래 둘씩(5 + 1 로 하나가 남지 않게)."""
    assert '<div class="kpis six s12"' in _text("web/admin/clarity.html")
    css = _text("web/admin/admin.css")
    assert "repeat(6, minmax(0, 1fr))" in _rule(css, ".kpis.six")
    wide, narrow = css.split("@media (max-width: 1279px)")[1].split(
        "@media (max-width: 767px)"
    )
    assert re.search(
        r"\.kpis\.six \{\n\s+grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);",
        wide,
    )
    assert re.search(
        r"\.kpis\.six \{\n\s+grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);",
        narrow,
    )


def test_signal_deltas_have_a_fixed_column_wide_enough_for_the_whole_value() -> None:
    """066 §3.3 Clarity 불만 신호 — 셋째 칸(전날 대비 '▲ 12.3%p')이 '▲ 0.5…' 로 잘리지 않게 그 목록만 고정 폭 7em."""
    assert '<div class="list deltas" id="l-signals">' in _text("web/admin/clarity.html")
    css = _text("web/admin/admin.css")
    assert "grid-template-columns: minmax(0, 1fr) auto 7em;" in _rule(
        css, ".deltas .lrow"
    )
    assert "overflow: visible;" in _rule(css, ".deltas .lrow .p")
