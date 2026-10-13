"""관리자 '화면' 페이지 정적 단언 (스펙 065 §4 정적) — screens.html 이 066 틀(내비·머리·상세 판·공통 모듈)을 쓰고 '준비 중' 이 없으며,
고르기 줄·오른쪽 탭 셋·판마다 ⤢, 틀(iframe) 속성은 053 그대로, 틀 주소·postMessage 대상·받는 출처는 글자 그대로 https://kimptrack.com
한 상수, 인라인 스크립트 없음(CSP 는 test_admin.py — 그대로). 틀·내비 같은 글자는 test_admin_screen.py, 이름표는 test_admin_screens_calc.py.
"""

import re

from tests.test_admin_scripts import _script
from tests.test_deploy import _text

HTML = _text("web/admin/screens.html")


def test_page_replaces_the_placeholder_with_picker_frame_and_three_panels() -> None:
    """§3.2 — 머리(기간 오늘·7일·30일·90일, 비교, '새 창으로 ↗'), 고르기 줄(화면 칩 자리·기기·보기·지표), 몸(틀 칸 | 오른쪽 판 — 탭 셋,
    판마다 ⤢ → 상세 판). '준비 중' 없음."""
    assert "준비 중" not in HTML and "준비 중" not in _text("web/admin/screens.js")
    assert re.findall(r'data-range="(\w+)"', HTML) == ["1d", "7d", "30d", "90d"]
    assert re.findall(r'data-device="(\w+)"', HTML) == ["pc", "mobile"]
    assert re.findall(r'data-mode="(\w+)"', HTML) == ["element", "area", "depth"]
    assert re.findall(r'data-metric="(\w+)"', HTML) == ["clicks", "hover", "vis"]
    assert re.findall(r'data-panel="(\w+)" aria-pressed="(\w+)">([^<]+)<', HTML) == [
        ("elements", "true", "요소"),
        ("areas", "false", "영역"),
        ("exits", "false", "떠난 곳·깊이"),
    ]
    # 판마다 ⤢ 하나 — 그 판의 상세(표 전체)
    assert re.findall(r'class="ibtn" data-detail="(\w+)"', HTML) == [
        "elements",
        "areas",
        "exits",
    ]
    for frag in (
        'id="pick-page"',
        'id="tbl-elements"',
        'id="el-areas"',
        'id="el-foot"',
        'id="tbl-areas"',
        'id="l-exits"',
        'id="l-depth"',
        'id="frame-line"',
        'id="frame-note"',
    ):
        assert HTML.count(frag) == 1, frag
    assert (
        '<a class="out" id="here" href="/screens.html" target="_blank" rel="noopener">새 창으로'
        in HTML
    )


def test_frame_attributes_are_053s_and_the_address_is_set_in_one_place() -> None:
    """§3.2 — 틀 속성 053 그대로(sandbox="allow-scripts allow-same-origin"·referrerpolicy="strict-origin"), 주소는 HTML 에 없고
    screens.js 의 aim() 한 곳에서 frameUrl(SITE_ORIGIN + 정적 경로 또는 /app/?tab=<id>)로만."""
    frames = re.findall(r"<iframe\b[^>]*>", HTML)
    assert frames == [
        '<iframe id="frame" title="실제 화면 덮어 보기" sandbox="allow-scripts allow-same-origin" referrerpolicy="strict-origin" loading="lazy">'
    ]
    js = _script("screens.js")
    assert js.count(".src") == 1 and "$('frame').src = want;" in js
    assert (
        "const want = page && document.visibilityState === 'visible' ? frameUrl(page) : 'about:blank';"
        in js
    )
    assert (
        "const frameUrl = (page) => `${SITE_ORIGIN}${isDashboard(page) ? `/app/?tab=${page.slice(4)}&` : `${STATIC_PATH[page]}?`}kt-overlay=1`;"
        in js
    )
    assert re.search(
        r"const STATIC_PATH = Object\.freeze\(\{ landing: '/', privacy: '/privacy', 'kimp-chart': '/kimp-chart', 'kimp-history': '/kimp-history' \}\);",
        js,
    )


def test_frame_messages_go_only_to_the_public_site_origin() -> None:
    """§3.5·053 §3.4 — 틀로 보내는 postMessage 는 한 곳이고 대상 출처는 SITE_ORIGIN(글자 그대로 https://kimptrack.com), '*' 없음.
    받는 메시지는 그 출처와 틀 창을 둘 다 본다. 출처 글자는 이 파일에 한 번."""
    js = _script("screens.js")
    assert (
        js.count("const SITE_ORIGIN = 'https://kimptrack.com';") == 1
        and js.count("https://") == 1
    )
    assert (
        js.count("postMessage(") == 1
        and "if (win) win.postMessage(message, SITE_ORIGIN);" in js
    )
    assert "'*'" not in js and '"*"' not in js
    assert (
        "if (event.origin !== SITE_ORIGIN || !win || event.source !== win) return;"
        in js
    )
    assert (
        "window.addEventListener('message', (event) => onFrameMessage(app, event));"
        in js
    )
    for name in (
        "screensmodel.js",
        "screensnames.js",
        "screensdetail.js",
        "screenspanel.js",
    ):
        assert "postMessage" not in _script(name) and "https://" not in _script(name), (
            name
        )


def test_name_tables_live_in_the_screens_names_module_as_json() -> None:
    """§3.4 — 이름표 둘(AREA_NAMES — 053 표, EL_NAMES — 061 §7 열쇠)은 화면 페이지의 이름표 모듈 screensnames.js 에 JSON 꼴로 한 번씩."""
    names = _text("web/admin/screensnames.js")
    for table in ("AREA_NAMES", "EL_NAMES"):
        assert len(re.findall(rf"\nconst {table} = \{{\n", names)) == 1, table
    assert "AREA_NAMES" not in _text(
        "web/admin/screens.js"
    ) and "EL_NAMES" not in _text("web/admin/screens.js")
