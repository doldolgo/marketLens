"""대시보드 글꼴 계약 — 같은 출처의 Inter 서브셋 하나만 싣고 Google Fonts 를 부르지 않는다 (스펙 002 §3.2·032 §3.4).

글꼴 때문에 방문자의 IP 가 국외(Google)로 가지 않게 한다. 셸·스타일·디자인 원본에 외부 글꼴 주소가 다시 생기거나,
@font-face·preload·서브셋 스크립트의 글자 범위가 서로 어긋나면 여기서 멈춘다. 실제 요청 0 은 002 §4 의 브라우저 확인이다.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SHARED = WEB / "src/shared"
FONT = "inter-latin.woff2"
SCRIPT = WEB / "scripts/subset-dashboard-font.py"
EXTERNAL = ("fonts.googleapis.com", "fonts.gstatic.com")


def _read(path: Path) -> str:
    return path.read_text("utf-8")


def test_dashboard_sources_call_no_external_font_host() -> None:
    """셸·번들 소스·디자인 원본 어디에도 Google Fonts 주소와 외부 @import 가 없다."""
    paths = [
        WEB / "index.html",
        *SHARED.parent.rglob("*.css"),
        *SHARED.parent.rglob("*.ts"),
        *SHARED.parent.rglob("*.tsx"),
        *(ROOT / "docs/design").glob("*.css"),
    ]
    for path in paths:
        text = _read(path)
        for host in EXTERNAL:
            assert host not in text, (path, host)
        assert not re.search(r"@import\s+url\(\s*['\"]?https?:", text), path


def test_theme_copies_match_the_design_source() -> None:
    """002 §3.2 — theme.css·index.css 는 docs/design 원본을 그대로 복사한다(글꼴 @import 를 뺀 것도 함께)."""
    for name in ("theme.css", "index.css"):
        assert (SHARED / name).read_bytes() == (
            ROOT / "docs/design" / name
        ).read_bytes(), name
    assert '--font-body: "Inter", system-ui, sans-serif;' in _read(SHARED / "theme.css")


def _script_range() -> set[str]:
    script = _read(SCRIPT)
    latin = re.search(r"LATIN = \(\n((?:\s*\"[^\"]*\"\n)+)\s*\)", script)
    extra = re.search(r"EXTRA = \[([^\]]*)\]", script)
    assert latin and extra
    parts = "".join(re.findall(r"\"([^\"]*)\"", latin.group(1))).split(",")
    return {p.strip() for p in parts} | {
        f"U+{int(x, 16):04X}" for x in extra.group(1).split(",")
    }


def test_one_same_origin_inter_face_with_the_script_range() -> None:
    """fonts.css 의 @font-face 하나 — 같은 폴더의 woff2, 굵기 400~700, swap, unicode-range = 서브셋 스크립트의 글자 목록."""
    css = _read(SHARED / "fonts.css")
    faces = re.findall(r"@font-face\s*\{([^}]*)\}", css)
    assert len(faces) == 1
    face = faces[0]
    for line in (
        "font-family: 'Inter';",
        "font-style: normal;",
        "font-weight: 400 700;",
        "font-display: swap;",
        f"src: url('./fonts/{FONT}') format('woff2');",
    ):
        assert line in face, line
    found = re.search(r"unicode-range:\s*([^;]+);", face)
    assert found
    assert {p.strip() for p in found.group(1).split(",")} == _script_range()
    assert "U+20A9" in _script_range()  # ₩ — 헤더 KPI·스프레드 표 가격
    # 셸이 index.css(테마)보다 먼저 싣는다
    main = _read(WEB / "src/main.tsx")
    assert main.index("import './shared/fonts.css'") < main.index(
        "import './shared/index.css'"
    )


def test_font_file_is_a_small_woff2_with_its_license() -> None:
    fonts = SHARED / "fonts"
    assert sorted(p.name for p in fonts.glob("*.woff2")) == [FONT]
    data = (fonts / FONT).read_bytes()
    assert data[:4] == b"wOF2"
    # 34,384B(2026-10-23) — Google Fonts 가 주던 latin 48,432B + ₩ 때문에 받던 latin-ext 85,272B 대신 한 파일
    assert len(data) < 40_000
    license_text = _read(fonts / "OFL.txt")
    assert "SIL OPEN FONT LICENSE" in license_text.upper()
    assert "The Inter Project Authors" in license_text


def test_index_preloads_the_same_file() -> None:
    """index.html 이 같은 파일을 preload 한다(빌드가 /app/assets/ 해시 주소로 바꾼다) — 앱 스크립트를 기다리지 않고 받는다."""
    html = _read(WEB / "index.html")
    links = re.findall(r"<link [^>]*rel=\"preload\"[^>]*>", html)
    assert len(links) == 1
    link = links[0]
    for attr in (
        f'href="/src/shared/fonts/{FONT}"',
        'as="font"',
        'type="font/woff2"',
        "crossorigin",
    ):
        assert attr in link, attr
