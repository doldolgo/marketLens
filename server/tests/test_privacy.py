"""개인정보 처리방침 계약 — nginx 위치 셋·privacy.html·링크·sitemap 을 파일로 읽어 단언한다 (스펙 032 §4).

머지가 곧 게시다. 사람이 채울 자리표시자(`〔`)가 남았거나, 페이지가 외부 자원·서버 호출을 부르게 되거나, 033 이 읽는
저장값 계약(`kt.analytics`)과 방침의 필수 안내가 빠지면 여기서 멈춘다. 실제로 브라우저에서 CSP·버튼을 보는 검증은
스펙 §5 의 로컬 Docker·브라우저 명령이다.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

from tests.test_deploy import (
    PUBLIC_API,
    ROOT,
    _args,
    _locations,
    _public_server,
    _route,
)

PUBLIC = ROOT / "web/public"
CANONICAL = "https://kimptrack.com/privacy"
EFFECTIVE = "2026-10-01"  # 시행일 — 페이지의 <time> 과 sitemap lastmod 가 같다
CSP = (
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# §3.4 — 법 제30조 제1항·시행령 제31조 제1항 순서. 해당 없는 절(민감정보·가명정보 등)은 두지 않는다
SECTIONS = [
    "1. 개인정보의 처리 목적",
    "2. 처리하는 개인정보의 항목과 보유 기간",
    "3. 개인정보의 파기",
    "4. 개인정보의 제3자 제공",
    "5. 개인정보 처리의 위탁",
    "6. 개인정보의 국외 이전",
    "7. 자동으로 수집하는 장치의 설치·운영과 거부",
    "8. 정보주체의 권리와 행사 방법",
    "9. 개인정보의 안전성 확보 조치",
    "10. 개인정보 보호책임자",
    "11. 권익침해 구제 방법",
    "12. 개인정보 처리방침의 변경",
]
# 자원을 부르는 <link rel> — canonical 과 <a href> 는 주소일 뿐 요청을 만들지 않는다
RESOURCE_RELS = {
    "stylesheet",
    "preload",
    "modulepreload",
    "preconnect",
    "dns-prefetch",
    "icon",
}
# 인라인 스크립트 본문에 없어야 하는 요청 수단 — 본문 글자는 보지 않는다(요청 자체는 CSP 가 막는다)
NO_CALLS = ("fetch(", "new WebSocket(", "XMLHttpRequest", "sendBeacon", "clarity.ms")
# §3.3·§3.4 — 033 이 읽는 저장값, 거부 안내, Clarity 약관이 요구하는 링크, 구제 기관 넷
MUST_SAY = (
    "kt.analytics",
    "denied",
    "granted",
    "globalPrivacyControl",
    "_clck",
    "_clsk",
    "_cltk",
    "Safari",
    "7일",
    'href="https://privacy.microsoft.com/ko-kr/privacystatement"',
    'href="https://optout.aboutads.info/"',
    "개인정보분쟁조정위원회 1833-6972",
    "개인정보침해신고센터 118",
    "대검찰청 1301",
    "경찰청 182",
)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.titles: list[str] = []
        self.h2: list[str] = []
        self.links: list[dict[str, str]] = []
        self.srcs: list[tuple[str, str]] = []
        self.scripts: list[str] = []
        self.times: list[str] = []
        self._key: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "link":
            self.links.append(a)
        elif tag == "time":
            self.times.append(a.get("datetime", ""))
        if "src" in a:
            self.srcs.append((tag, a["src"]))
        if tag in ("title", "h2", "script"):
            self._key, self._buf = tag, []

    def handle_endtag(self, tag: str) -> None:
        if tag != self._key:
            return
        text = "".join(self._buf)
        if tag == "title":
            self.titles.append(text)
        elif tag == "h2":
            self.h2.append(" ".join(text.split()))
        else:
            self.scripts.append(text)
        self._key = None

    def handle_data(self, data: str) -> None:
        if self._key:
            self._buf.append(data)


def _read(path: Path) -> tuple[str, _Page]:
    html = path.read_text("utf-8")
    page = _Page()
    page.feed(html)
    return html, page


def _external(url: str) -> bool:
    return url.startswith(("http://", "https://", "//"))


def _assert_no_external_resources(name: str, page: _Page) -> None:
    for tag, src in page.srcs:
        assert not _external(src), (name, tag, src)
    for link in page.links:
        rels = set(link.get("rel", "").lower().split())
        if rels & RESOURCE_RELS:
            assert not _external(link.get("href", "")), (name, link)


# --- nginx: 공개 server 의 위치 셋 (§3.1) -------------------------------------------


def test_privacy_location_serves_the_file_with_no_cache_and_csp_always() -> None:
    block = _locations(_public_server())[("=", "/privacy")]
    assert _args(block, "try_files") == [["/privacy.html", "=404"]]
    headers = _args(block, "add_header")
    assert ["Cache-Control", "no-cache", "always"] in headers
    assert ["Content-Security-Policy", CSP, "always"] in headers
    assert len(headers) == 2
    assert not _args(block, "proxy_pass") and not _args(block, "alias")


def test_file_name_urls_fold_into_privacy() -> None:
    """/app/privacy.html 은 dist 의 사본이 /app/ alias 로 CSP 없이 나가는 주소라 막는다."""
    locations = _locations(_public_server())
    for path in ("/privacy.html", "/app/privacy.html"):
        assert locations[("=", path)] == [(["return", "301", "/privacy"], None)], path


def test_privacy_routing_keeps_the_allowlist_and_has_no_regex() -> None:
    locations = _locations(_public_server())
    assert _route("/privacy") == ("=", "/privacy")
    assert _route("/app/privacy.html") == ("=", "/app/privacy.html")
    # /privacy/ 는 루트 location / 의 try_files $uri =404 로 404 다
    assert _route("/privacy/") == ("/",)
    assert not [key for key in locations if key[0] in ("~", "~*")]
    open_api = {
        key[1] for key in locations if key[0] == "=" and key[1].startswith("/api/")
    }
    assert open_api == set(PUBLIC_API)


# --- privacy.html (§3.1·§3.3·§3.4·§3.6) ---------------------------------------------


def test_page_has_no_placeholder_and_the_twelve_sections() -> None:
    html, page = _read(PUBLIC / "privacy.html")
    assert "〔" not in html, "사람이 채울 자리표시자가 남았다 (§3.6)"
    assert page.lang == "ko"
    assert page.titles == ["개인정보 처리방침 — KimpTrack"]
    canonical = [link["href"] for link in page.links if link.get("rel") == "canonical"]
    assert canonical == [CANONICAL]
    assert [h for h in page.h2 if re.match(r"\d+\. ", h)] == SECTIONS
    assert "화면 분석 거부" in page.h2


def test_page_loads_nothing_external_and_scripts_make_no_requests() -> None:
    html, page = _read(PUBLIC / "privacy.html")
    _assert_no_external_resources("privacy.html", page)
    styles = "".join(re.findall(r"<style\b.*?</style>", html, flags=re.S))
    assert "@import" not in styles and "url(" not in styles
    assert page.scripts, "분석 거부 버튼 스크립트가 없다"
    # 인라인만 — CSP 의 script-src 가 'unsafe-inline' 뿐이라 같은 출처 파일도 막힌다
    assert not [src for tag, src in page.srcs if tag == "script"]
    for body in page.scripts:
        for call in NO_CALLS:
            assert call not in body, call


def test_page_states_the_opt_out_contract_and_required_notices() -> None:
    html, _ = _read(PUBLIC / "privacy.html")
    for text in MUST_SAY:
        assert text in html, text
    # 버튼 둘과 자바스크립트가 꺼졌을 때의 안내 (§3.3·§3.7)
    assert ">분석 거부</button>" in html and ">다시 허용</button>" in html
    assert "<noscript>" in html


def test_archived_versions_are_static_and_external_free() -> None:
    """이전 판 privacy-<시행일>.html 은 location / 로 CSP 없이 나간다 — 스크립트 없는 정적 사본 (§3.6)."""
    for path in sorted(PUBLIC.glob("privacy-*.html")):
        html, page = _read(path)
        assert re.fullmatch(r"privacy-\d{8}\.html", path.name), path.name
        assert "〔" not in html and "<script" not in html, path.name
        _assert_no_external_resources(path.name, page)


# --- 링크·sitemap (§3.2) ------------------------------------------------------------


def test_landing_footer_links_to_privacy_in_the_same_tab() -> None:
    html = (PUBLIC / "landing.html").read_text("utf-8")
    footer = html[html.index("<footer") : html.index("</footer>")]
    link = re.search(r'<a [^>]*href="/privacy"[^>]*>개인정보 처리방침</a>', footer)
    assert link and "target=" not in link.group(0)


def test_dashboard_header_opens_privacy_in_a_new_tab() -> None:
    app = (ROOT / "web/src/App.tsx").read_text("utf-8")
    assert 'href="/privacy"' in app
    assert 'target="_blank"' in app and 'rel="noopener"' in app


def test_sitemap_lists_privacy_with_the_effective_date() -> None:
    sitemap = (PUBLIC / "sitemap.xml").read_text("utf-8")
    assert f"<loc>{CANONICAL}</loc><lastmod>{EFFECTIVE}</lastmod>" in sitemap
    _, page = _read(PUBLIC / "privacy.html")
    assert page.times and set(page.times) == {EFFECTIVE}
