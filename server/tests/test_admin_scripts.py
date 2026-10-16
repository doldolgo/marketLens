"""관리자 화면 v4 스크립트·스타일 정적 단언(스펙 066 §4 정적 · 064 §3.1 보안 규칙) — 피드 글자는 textContent·title 로만(HTML 해석
없음), 저장소·새 창·주소 쓰기는 정해진 곳에만(주소 상태는 app.js 하나, 링크는 jump()·download()·clarityLink() 셋(077), style 은 폭·자리 도움 둘),
요청은 call() 하나, 경로 표, 토큰(조사 §5 그대로)·상자 색 고정, 도넛·반원 게이지 없음(예산 게이지 하나만), 게이트 문구 없음.
"""

import re

from tests.test_admin_screen import PAGES, SCRIPTS
from tests.test_deploy import _text

# 어디에도 없어야 하는 것: 브라우저 저장소·HTML 해석·코드 실행·새 창·주소 직접 쓰기·속성 주입·다른 요청 수단
BANNED = (
    "localStorage|sessionStorage|indexedDB|document.cookie|innerHTML|outerHTML|insertAdjacentHTML|document.write|eval(|new Function|"
    "window.open|location.pathname|location.href|location.assign|location.search =|location =|setAttribute('style'|"
    "setAttribute('src'|.src|.href|setAttributeNS|xlink:href|new XMLHttpRequest|sendBeacon|new WebSocket|EventSource|postMessage"
).split("|")


def _script(name: str) -> str:
    """스크립트 코드 — 주석은 뺀다(주석은 금지 글자를 이름으로 말할 수 있다)."""
    code = re.sub(r"/\*.*?\*/", "", _text(f"web/admin/{name}"), flags=re.S)
    return re.sub(r"(?m)(^|[ \t;,{(])//.*$", r"\1", code)


def _every() -> str:
    return "".join(_script(name) for name in SCRIPTS)


# 065 화면·074 재생 판만의 예외 — 틀로 보내는 postMessage 한 곳(대상 출처 SITE_ORIGIN)과 틀 주소(.src) 한 곳, 공개 사이트 출처 글자 하나
FRAME = {"postMessage": 1, ".src": 1}
FRAMED = ("screens.js", "replayframe.js")


def test_scripts_never_parse_html_store_data_or_open_windows() -> None:
    """064 §3.1 — 금지 글자 없음, 스크립트 안 바깥 주소 없음, 요청은 call() 의 fetch 하나(헤더 X-Requested-With).
    065 화면(screens.js)·074 재생 틀(replayframe.js)만 틀 예외 둘과 출처 상수 하나(test_admin_screens_static·test_admin_replay_sheet 가 자리를 본다)."""
    for name in SCRIPTS:
        js = _script(name)
        for banned in BANNED:
            want = FRAME.get(banned, 0) if name in FRAMED else 0
            assert js.count(banned) == want, (name, banned)
        # SVG 이름공간(요소를 만드는 글자)만 — 바깥 주소가 아니다. 화면은 공개 사이트 출처 상수 하나,
        # common.js 는 Clarity 재생 링크의 출처 상수 하나(077 — clarityLink() 가 주소 앞부분을 견준다)
        site = {
            "screens.js": "const SITE_ORIGIN = 'https://kimptrack.com';",
            "replayframe.js": "const SITE_ORIGIN = 'https://kimptrack.com';",
            "common.js": "const CLARITY_ORIGIN = 'https://clarity.microsoft.com/';",
        }.get(name, "")
        assert js.count(site) == 1 or not site, name
        assert not re.search(
            r"https?://",
            js.replace("'http://www.w3.org/2000/svg'", "").replace(site or "\0", ""),
        ), name
    every = _every()
    common = _script("common.js")
    assert every.count("fetch(") == 1
    assert (
        "resp = await fetch(path, { ...init, headers: { ...XHR, ...init.headers }, cache: 'no-store' });"
        in common
    )
    assert every.count("'X-Requested-With': 'XMLHttpRequest'") == 1


def test_style_links_and_addresses_are_written_in_fixed_places_only() -> None:
    """style 은 막대 폭·자리 도움 둘(비율 수만)과 틀 칸의 CSS 변수 셋(065 — 몫·높이, 074 — 틀 폭, 수만), 링크 주소는 jump()(고정 표)·download()
    (Blob 주소)·'새 창으로'(065 — app.js 가 urlOf() 로 만든 이 페이지 주소)·clarityLink()(077 — Clarity 출처·허용 글자만) 넷, 주소 상태(쌓기·바꾸기·뒤로·새로고침)는 app.js 하나에서
    urlOf() 로 만든 그 페이지의 고정 주소로만(066 §3.1·§3.2 — 지금 경로를 쓰지 않는다, 029)."""
    every = _every()
    common = _script("common.js")
    rules = _script("rules.js")
    app = _script("app.js")
    view = _script("view.js")
    # + '?' 말풍선 자리 둘(071 §3.1 — 화면 안으로 민 top·left 픽셀)
    assert every.count(".style") == 5
    assert _script("help.js").count(
        ".style"
    ) == 2 and "pop.style.top = `${top}px`;" in _script("help.js")
    assert (
        "node.style.width = ratioPct(ratio);" in common
        and "node.style.left = ratioPct(ratio);" in common
        and "const s = node.style;" in common
    )
    # 074 재생 판 — 틀 폭(조각의 화면 폭)
    assert re.findall(r"\bs\.setProperty\('([\w-]+)'", common) == [
        "--fit",
        "--stage-h",
        "--fw",
    ]
    assert every.count("setProperty(") == 3
    assert every.count("setAttribute('href'") == 4
    assert (
        "const url = urlOf(page, view);" in app
        and "if (here && here.getAttribute('href') !== url) here.setAttribute('href', url);"
        in app
    )
    assert (
        "const href = own(JUMP, to);" in rules
        and "if (href !== undefined) node.setAttribute('href', href);" in rules
    )
    assert (
        "link.setAttribute('href', url);" in common
        and every.count("URL.createObjectURL(") == 1
    )
    # 077 — Clarity 재생 링크: 출처 상수로 시작하고 허용 글자만·500자 이하일 때만, 같은 탭·noreferrer
    assert "link.setAttribute('href', href);" in common
    assert (
        "typeof v === 'string' && v.length <= 500 && v.startsWith(CLARITY_ORIGIN) && CLARITY_REST.test(v.slice(CLARITY_ORIGIN.length)) ? v : null;"
        in common
    )
    assert (
        "const CLARITY_REST = /^[A-Za-z0-9/_.~%-]*(?:\\?[A-Za-z0-9=&_.~%:-]*)?$/;"
        in common
    )
    assert (
        every.count("clarityHref(") == 1
    )  # clarityLink() 안 하나 — 다른 곳은 이 검사로 링크를 만들지 않는다
    assert (
        "const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));"
        in common
    )
    for name in SCRIPTS:
        if name != "app.js":
            assert "history." not in _script(name) and "location." not in _script(
                name
            ), name
    assert (
        app.count("location.replace(") == 1
        and "location.replace(urlOf(page, view, [[RELOAD_MARK, '1']]));" in app
    )
    assert (
        app.count("history.pushState(") == 1
        and "history.pushState({ ktDetail: id }, '', urlOf(page, view, marked()));"
        in app
    )
    replaces = re.findall(r"history\.replaceState\(([^;]*)\);", app)
    assert replaces and all("urlOf(page, view" in r for r in replaces), replaces
    assert app.count("history.back()") == 1
    assert (
        app.count("location.hash") == 2
        and app.count("readView(page, location.search, location.hash)") == 2
    )
    assert (
        "return `${page.home}${s ? `?${s}` : ''}${view.detail ? `#detail=${encodeURIComponent(view.detail)}` : ''}`;"
        in view
    )
    for page, (home, module, _) in PAGES.items():
        assert _script(module).count(f"home: '{home}',") == 1, page


def test_tooltip_html_is_built_in_one_function_from_escaped_text() -> None:
    """툴팁 HTML 을 만드는 곳은 charts.js 의 tip() 하나 — 끼우는 값은 encodeHTML 을 지난 글자와 허용 목록의 클래스뿐(064 §3.1)."""
    charts = _script("charts.js")
    tip = re.search(
        r"\nexport function tip\(head, rows = \[\]\) \{\n(.*?)\n\}\n",
        charts,
        flags=re.S,
    )
    assert tip, "tip()"
    for name in SCRIPTS:
        literals = len(re.findall(r"""['"`]<""", _script(name)))
        assert literals == (
            len(re.findall(r"""['"`]<""", tip.group(1))) if name == "charts.js" else 0
        ), name
    assert set(re.findall(r"\$\{([^}]*)\}", tip.group(1))) == {
        "esc(head)",
        "cls",
        "key",
        "esc(name)",
        "esc(value)",
    }
    assert "const esc = (v) => lib().format.encodeHTML(clean(v));" in charts
    assert "const cls = tipMark(mark);" in tip.group(1)
    # 표·목록 글자는 textContent 로만 — el()·put() 이 clean() 을 지나 넣는다
    common = _script("common.js")
    assert (
        "if (text !== undefined && text !== null) node.textContent = clean(text);"
        in common
    )
    # SVG 는 표 칸의 작은 선(속성 이름 고정·값은 수)만 — 아이콘·링크를 스크립트가 만들지 않는다
    assert _every().count("svg('") == 2 and "svg('polyline', { points," in _script(
        "ui.js"
    )


def test_paths_table_polling_periods_and_no_copied_feed_periods() -> None:
    """064 §3.1 + 066 — 부르는 경로는 common.js 표 하나(067 ips·ip, 068 history, 065 attention, 069 addrs·addr, 073 replay 셋 더함), 빠른 10초·느린 60초
    (065 화면만 5분 — 페이지가 slowMs 로 준다, 074 지금 보는 중만 5초 — page.live), 피드 주기는 refreshSec 로만."""
    common = _script("common.js")
    table = re.search(
        r"export const PATHS = Object\.freeze\(\{(.*?)\}\);", common, flags=re.S
    )
    assert table, "PATHS"
    assert dict(re.findall(r"(\w+): '([^']+)'", table.group(1))) == {
        "collector": "/api/health",
        "api": "/svc/api/health",
        "status": "/svc/api/admin/status",
        "collect": "/api/health/collect",
        "aws": "/api/admin/aws",
        "series": "/api/admin/aws/series",
        "alerts": "/api/admin/alerts",
        "access": "/svc/api/admin/access",
        "ips": "/svc/api/admin/access/ips",
        "ip": "/svc/api/admin/access/ip",
        "addrs": "/svc/api/admin/access/addrs",
        "addr": "/svc/api/admin/access/addr",
        "clarity": "/svc/api/admin/clarity",
        "history": "/svc/api/admin/clarity/history",
        "recordings": "/svc/api/admin/clarity/recordings",
        "attention": "/svc/api/admin/attention",
        "replayLive": "/svc/api/admin/replay/live",
        "replaySessions": "/svc/api/admin/replay/sessions",
        "replaySession": "/svc/api/admin/replay/session",
    }
    assert re.search(r"\bFAST_MS = 10_000;", common) and re.search(
        r"\bSLOW_MS = 60_000;", common
    )
    # 074 — 지금 보는 중 하나만 5초(page.live 인 페이지만)
    assert re.search(r"\bLIVE_MS = 5_000;", common)
    assert "export const LIVE = Object.freeze(['replayLive']);" in common
    assert (
        "export const FAST = Object.freeze(['collector', 'api', 'status', 'collect']);"
        in common
    )
    for name in SCRIPTS:
        if name != "common.js":
            expected = ["/api/refresh"] if name == "server.js" else []
            assert re.findall(r"'(/(?:api|svc)/[^']*)'", _script(name)) == expected, (
                name
            )
    every = _every()
    for number in (
        "10800",
        "10_800",
        "14400",
        "14_400",
        "21600",
        "21_600",
        "43200",
        "43_200",
    ):
        assert not re.search(rf"(?<![\w.]){number}(?![\w.])", every), number


def _root_tokens(css: str) -> dict[str, str]:
    block = re.search(r":root\s*\{(.*?)\}", css, flags=re.S)
    assert block, ":root 블록"
    return {
        k: " ".join(v.split())
        for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block.group(1))
    }


def test_tokens_are_the_research_values_and_box_colours_are_fixed() -> None:
    """066 §3.1 — 조사 §5 의 CSS 변수 그대로(바탕 3단·선·글 3단·강조·상태 4색·차트 8색·진하기 5단·글자·간격·모서리·그림자), 차트 테마의
    기본값도 같은 값, 상자 색 고정(collect --c1 · data --c2 · serve --c3)."""
    research = _text("docs/admin-design-research.md")
    block = re.search(r"```css\n:root \{(.*?)\n\}\n```", research, flags=re.S)
    assert block, "조사 §5 css"
    want = {
        k: " ".join(v.split())
        for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block.group(1))
    }
    assert len(want) == 48
    css = re.sub(r"/\*.*?\*/", "", _text("web/admin/admin.css"), flags=re.S)
    tokens = _root_tokens(css)
    assert {k: tokens.get(k) for k in want} == want
    assert {k: tokens[k] for k in ("--box-collect", "--box-data", "--box-serve")} == {
        "--box-collect": "var(--c1)",
        "--box-data": "var(--c2)",
        "--box-serve": "var(--c3)",
    }
    charts = _script("charts.js")
    fallback = dict(
        re.findall(
            r"\n  '?([\w-]+)'?: '(#[0-9a-f]{6})',",
            charts.split("export const TOKENS", 1)[1].split("});", 1)[0],
        )
    )
    assert (
        fallback == {name: tokens[f"--{name}"] for name in fallback}
        and len(fallback) == 27
    )
    assert (
        "export const boxMark = markIn({ collect: 1, data: 2, serve: 3 });"
        in _script("names.js")
    )
    server = _text("web/admin/server.html")
    assert re.findall(
        r'data-box="(\w+)" aria-pressed="true"><i class="key (k\d)"></i>', server
    ) == [("collect", "k1"), ("data", "k2"), ("serve", "k3")]
    assert "color-scheme: dark;" in css and "prefers-color-scheme" not in css
    body = re.search(r"(?m)^body \{([^}]*)\}", css).group(1)
    assert (
        "font: 400 var(--fs-14)/1.5 var(--font);" in body
        and "background: var(--bg);" in body
    )
    big = re.search(r"(?m)^\.big \{([^}]*)\}", css).group(1)
    assert "font-size: var(--fs-32);" in big and "font-weight: 600;" in big
    assert (
        "max-width: 1600px;" in css
        and "grid-template-columns: repeat(12, minmax(0, 1fr));" in css
    )
    assert (
        "@media (max-width: 1279px)" in css
        and "@media (max-width: 767px)" in css
        and "--side: 56px;" in css
    )


def test_no_donut_and_a_single_gauge_for_the_budget() -> None:
    """066 §3.1 — 도넛·반원 게이지를 없애고(구성비 = 100% 띠, 순위 = 상위 목록, 상자 = 문턱 눈금 막대) 게이지는 예산 하나만."""
    every = _every()
    assert "'pie'" not in every and '"pie"' not in every
    assert (
        every.count("type: 'gauge'") == 1
        and "type: 'gauge'"
        in _script("charts.js").split("export function budgetOption", 1)[1]
    )
    assert _script("server.js").count(
        "budgetOption("
    ) == 1 and "budgetOption" not in _script("overview.js")


def test_no_server_log_gate_wording_on_any_page() -> None:
    """사람 결정 2026-10-09(060) 그대로 — 처리방침·시행일 문구가 없다. '처리방침' 은 페이지 이름표 둘(names.js)뿐, 기간 단추는 HTML 에서 꺼 두지 않는다."""
    for name in [*PAGES, *SCRIPTS]:
        text = _text(f"web/admin/{name}")
        for word in ("시행", "개정", "DB-IP 자료", "gateAt", "window-note"):
            assert word not in text, (name, word)
        # 이름표 둘 — 페이지 이름(names.js 의 두 표)과 대시보드 머리 링크 열쇠(065 screensnames.js 의 EL_NAMES)
        assert text.count("처리방침") == {"names.js": 2, "screensnames.js": 1}.get(
            name, 0
        ), name
    for page in PAGES:
        buttons = re.findall(
            r"<button\b[^>]*\bdata-range=[^>]*>", _text(f"web/admin/{page}")
        )
        assert not any("disabled" in b for b in buttons), page
