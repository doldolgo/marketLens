"""랜딩 검색·미리보기 계약 — landing.html·robots.txt·sitemap.xml·대시보드 셸·404 를 파일로 읽어 단언한다 (스펙 022 §3.6).

검색엔진은 제목·설명을 자주 바꾸면 불이익을 준다(네이버). 문구가 스펙과 어긋나거나, 구조화 데이터가 보이는 글과
달라지거나, 글꼴 서브셋에 없는 글자가 새로 생기면 여기서 멈춘다.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "web/public"
SITE = "https://kimptrack.com/"

# 스펙 022 §3.6 — 글자 그대로
TITLE = "KimpTrack - 실시간 김프(김치 프리미엄)·역프"
DESCRIPTION = (
    "업비트·빗썸과 바이낸스·바이비트·비트겟 사이 김치 프리미엄을 매초 계산하고, "
    "경로마다 입출금이 열렸는지 함께 보여 주는 무료 서비스입니다."
)
H1 = "실시간 김프·역프, 실제로 옮길 수 있는지까지"
MODIFIED = "2026-10-01"
OG_IMAGE = "https://kimptrack.com/landing/og-v2.png"


class _Page(HTMLParser):
    """필요한 것만 모은다 — head 의 meta·link, JSON-LD, 요소별 속성, h1·h2 글자, 링크."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.links: list[dict[str, str]] = []
        self.ld: list[str] = []
        self.titles: list[str] = []
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.headings: dict[str, list[str]] = {"h1": [], "h2": []}
        self.hrefs: list[str] = []
        self._stack: list[str] = []
        self._text: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        self.elements.append((tag, a))
        if tag == "meta":
            key = a.get("name") or a.get("property")
            if key:
                self.meta.setdefault(key, []).append(a.get("content", ""))
        elif tag == "link":
            self.links.append(a)
        elif tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
        if tag in ("title", "h1", "h2") or (
            tag == "script" and a.get("type") == "application/ld+json"
        ):
            key = "ld" if tag == "script" else tag
            self._stack.append(key)
            self._text[key] = []

    def handle_endtag(self, tag: str) -> None:
        key = "ld" if tag == "script" else tag
        if self._stack and self._stack[-1] == key:
            self._stack.pop()
            text = "".join(self._text.pop(key))
            if key == "ld":
                self.ld.append(text)
            elif key == "title":
                self.titles.append(text)
            else:
                self.headings[key].append(" ".join(text.split()))

    def handle_data(self, data: str) -> None:
        for key in self._stack:
            self._text[key].append(data)


def _page(rel: str) -> _Page:
    p = _Page()
    p.feed((PUBLIC / rel).read_text("utf-8"))
    return p


def _landing() -> _Page:
    return _page("landing.html")


def _graph() -> dict[str, dict]:
    page = _landing()
    assert len(page.ld) == 1, "JSON-LD 는 한 블록(@graph)"
    data = json.loads(page.ld[0])
    assert data["@context"] == "https://schema.org"
    return {node["@type"]: node for node in data["@graph"]}


def test_title_description_and_preview_match_the_spec() -> None:
    page = _landing()
    assert page.titles == [TITLE]
    assert page.meta["description"] == [DESCRIPTION]
    assert page.meta["og:title"] == [TITLE]
    assert page.meta["og:description"] == [DESCRIPTION]
    assert page.meta["og:site_name"] == ["KimpTrack"]
    assert page.meta["og:url"] == [SITE]
    assert page.meta["og:locale"] == ["ko_KR"]
    assert page.meta["og:image"] == [OG_IMAGE]
    assert page.meta["og:image:alt"][0]
    assert page.meta["twitter:card"] == ["summary_large_image"]
    assert page.meta["robots"] == ["index, follow, max-image-preview:large"]
    canonical = [link["href"] for link in page.links if link.get("rel") == "canonical"]
    assert canonical == [SITE]


def test_title_and_description_follow_the_naver_limits() -> None:
    """네이버 사이트 진단 — 제목 40자·설명 80자 이내, 같은 낱말 반복 금지, 경쟁 사이트 이름(김프가) 없음."""
    assert len(TITLE) <= 40
    assert len(DESCRIPTION) <= 80
    assert TITLE.count("김프") == 1
    assert (
        "김프" not in DESCRIPTION
    )  # 제목과 설명이 핵심어를 나눠 싣는다(설명은 김치 프리미엄)
    for text in (TITLE, DESCRIPTION, H1):
        assert "김프가" not in text
        # 수익을 약속하는 말은 쓰지 않는다 — 수수료·전송 시간을 뺀 참고값이다
        assert not re.search("이득|이익|수익|차익|무위험", text)


def test_description_is_not_a_copy_of_the_lead() -> None:
    """네이버 — 본문을 그대로 옮긴 설명은 불이익. 설명은 첫 문단과 다른 문장이다."""
    html = (PUBLIC / "landing.html").read_text("utf-8")
    lead = re.search(r'<p class="prose lead">(.*?)</p>', html, re.S)
    assert lead
    text = re.sub(r"<[^>]+>", "", lead.group(1))
    assert DESCRIPTION not in text
    assert (
        "KimpTrack(김프트랙)" in text
    )  # 한글 이름을 본문에 한 번 — WebSite.alternateName 과 같다


def test_one_h1_and_section_headings() -> None:
    page = _landing()
    assert page.headings["h1"] == [H1]
    assert page.headings["h2"] == [
        "표시된 김프와 실제로 먹을 수 있는 김프는 다릅니다",
        "지난 7일 김프·역프 사건",
        "김프(김치 프리미엄)와 역프란",
        "KimpTrack이 김프를 계산하는 방법",
        "자주 묻는 질문",
    ]


def test_in_page_links_point_to_existing_sections() -> None:
    page = _landing()
    ids = {a["id"] for _, a in page.elements if "id" in a}
    anchors = {h[1:] for h in page.hrefs if h.startswith("#")}
    assert anchors == {"kimp", "method", "faq"}
    assert anchors <= ids
    # 대시보드로 가는 링크는 목적지를 말한다 — 같은 글자 하나로 몰지 않는다
    assert "/app/" in page.hrefs
    assert any(h.startswith("/app/?tab=history") for h in page.hrefs)


def test_structured_data_matches_what_the_page_shows() -> None:
    g = _graph()
    assert set(g) == {"WebSite", "Organization", "WebPage", "WebApplication"}
    site = g["WebSite"]
    assert site["name"] == "KimpTrack" and site["url"] == SITE
    assert site["alternateName"] == ["김프트랙"]
    page = g["WebPage"]
    assert page["name"] == TITLE
    assert page["description"] == DESCRIPTION
    assert page["dateModified"] == MODIFIED
    assert page["primaryImageOfPage"]["url"] == OG_IMAGE
    app = g["WebApplication"]
    assert app["url"] == SITE + "app/"
    assert app["offers"]["price"] == "0"
    # 평점은 없다 — 지어낸 평점은 금지
    assert "aggregateRating" not in app and "review" not in app
    # 운영 주체·연락처 — 바닥에 보이는 값과 같다(2026-10-01 사람이 준 값)
    org = g["Organization"]
    html = (PUBLIC / "landing.html").read_text("utf-8")
    for email in org["email"]:
        assert f'href="mailto:{email}"' in html
    for person in org["member"]:
        assert person["name"] in html
    logo = org["logo"]["url"]
    for url in (logo, OG_IMAGE, *app["screenshot"]):
        assert url.startswith(SITE)
        assert (PUBLIC / url.removeprefix(SITE)).is_file(), url


def test_modified_date_is_the_same_everywhere() -> None:
    """설명을 고친 날 — 바닥의 <time>, WebPage.dateModified, sitemap lastmod 가 같다. 실시간 값이 바뀌어도 올리지 않는다."""
    html = (PUBLIC / "landing.html").read_text("utf-8")
    assert re.findall(r'<time datetime="([^"]+)">', html) == [MODIFIED]
    sitemap = (PUBLIC / "sitemap.xml").read_text("utf-8")
    # 두 줄 — 랜딩과 처리방침(032, lastmod 는 방침 시행일 — test_privacy.py). /app/ 은 넣지 않는다
    urls = dict(re.findall(r"<loc>([^<]+)</loc><lastmod>([^<]+)</lastmod>", sitemap))
    assert list(urls) == [SITE, SITE + "privacy"]
    assert sitemap.count("<url>") == len(urls)
    assert urls[SITE] == MODIFIED


def test_icons_are_absolute_and_exist() -> None:
    """구글은 SVG 파비콘을 쓰지 않고 네이버는 상대 경로를 읽지 않는다 — rel 마다 하나, 절대 주소."""
    icons = [
        link
        for link in _landing().links
        if link.get("rel") in ("icon", "apple-touch-icon", "shortcut icon")
    ]
    assert sorted(link["rel"] for link in icons) == ["apple-touch-icon", "icon"]
    for link in icons:
        assert link["href"].startswith(SITE)
        assert (PUBLIC / link["href"].removeprefix(SITE)).is_file()
    assert (PUBLIC / "favicon.ico").is_file()


def test_png_images_have_the_declared_size() -> None:
    def size(rel: str) -> tuple[int, int]:
        head = (PUBLIC / rel).read_bytes()[:24]
        assert head[:8] == b"\x89PNG\r\n\x1a\n", rel
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")

    assert size("landing/og-v2.png") == (1200, 630)
    assert size("logo-512.png") == (512, 512)
    assert size("icon-192.png") == (192, 192)
    assert size("apple-touch-icon.png") == (180, 180)


def test_live_regions_are_kept_out_of_snippets() -> None:
    """매초 바뀌는 값이 검색 결과·AI 답변에 '지금 값'처럼 남지 않게 — data-nosnippet 은 HTML 에 처음부터 있다."""
    html = (PUBLIC / "landing.html").read_text("utf-8")
    for el_id in ("why-depth", "why-rate", "why-open", "events-sum", "events-table"):
        tag = re.search(rf'<(\w+) id="{el_id}"[^>]*>', html)
        assert tag and "data-nosnippet" in tag.group(0), el_id
        assert tag.group(1) in ("span", "div", "section"), (
            el_id
        )  # 구글은 이 세 요소에서만 읽는다
    wrapper = html.index("<div data-nosnippet>")
    assert wrapper < html.index('id="card"') < html.index('id="next"')
    # 카드 자리는 값이 오기 전에도 무엇을 보여 주는지 말한다 — 크롤러가 오류 문구만 보지 않게
    card = html[html.index('id="card"') : html.index('id="next"')]
    assert "지금 가장 큰 경로" in card


def test_font_subset_covers_every_character_on_the_page() -> None:
    """랜딩 글꼴은 이 페이지 글자만 담은 서브셋이다 — 글을 고치고 서브셋을 다시 만들지 않으면 여기서 멈춘다.
    다시 만들기: uv run web/scripts/subset-landing-font.py (스펙 022 §3.4)"""
    html = (PUBLIC / "landing.html").read_text("utf-8")
    html = re.sub(r"<style\b.*?</style>", "", html, flags=re.S)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"(^|\s)//[^\n]*", r"\1", html)
    need = {c for c in html if ord(c) > 0x7E}
    have = set(
        (ROOT / "web/scripts/landing-font-glyphs.txt").read_text("utf-8").strip()
    )
    assert not need - have, "".join(sorted(need - have))
    # 파일 이름은 내용 해시 — preload 와 @font-face 가 같은 주소를 가리키고, 그 파일 하나만 있다
    raw = (PUBLIC / "landing.html").read_text("utf-8")
    refs = re.findall(r"landing/fonts/(kimptrack-sans-[0-9a-f]{8}\.woff2)", raw)
    assert len(refs) == 2 and len(set(refs)) == 1, refs
    fonts = sorted(p.name for p in (PUBLIC / "landing/fonts").glob("*.woff2"))
    assert fonts == [refs[0]]
    assert "SIL OPEN FONT LICENSE" in (PUBLIC / "landing/fonts/OFL.txt").read_text(
        "utf-8"
    )
    assert "cdn.jsdelivr.net" not in (PUBLIC / "landing.html").read_text("utf-8")


def test_robots_opens_only_the_landing_summary_under_api() -> None:
    """검색 로봇이 랜딩을 그릴 때 실데이터를 받게 /api/landing 만 연다 — 더 긴 규칙이 이긴다(RFC 9309)."""
    text = (PUBLIC / "robots.txt").read_text("utf-8")
    # 주석 줄은 빼고 본다 — 다음 웹마스터도구 PIN 은 주석 줄로 들어간다(§3.6)
    rules = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert rules == [
        "User-agent: *",
        "Allow: /api/landing",
        "Disallow: /api/",
        "Sitemap: https://kimptrack.com/sitemap.xml",
    ]


def test_dashboard_shell_is_noindex_with_share_preview() -> None:
    """/app/ 은 자바스크립트로 그리는 화면이라 검색에는 랜딩만 — noindex 는 정적으로, 제목 스모크 문자열은 그대로 (007)."""
    shell = _page("../index.html")
    assert shell.titles == ["KimpTrack"]
    assert shell.meta["robots"] == ["noindex, follow"]
    assert shell.meta["og:image"] == [OG_IMAGE]
    assert shell.meta["og:url"] == [SITE + "app/"]


def test_not_found_page_is_noindex_and_links_home() -> None:
    page = _page("404.html")
    assert page.meta["robots"] == ["noindex"]
    assert {"/", "/app/"} <= set(page.hrefs)
    conf = (ROOT / "web/nginx.conf").read_text("utf-8")
    assert "error_page 404 /404.html;" in conf


def test_nginx_folds_duplicate_landing_urls_and_marks_the_summary_noindex() -> None:
    conf = (ROOT / "web/nginx.conf").read_text("utf-8")
    assert "location = /landing.html { return 301 /; }" in conf
    assert "location = /app/landing.html { return 301 /; }" in conf
    landing_api = conf.split("location = /api/landing {", 1)[1].split("}", 1)[0]
    assert 'add_header X-Robots-Tag "noindex" always;' in landing_api
    root = conf.split("location = / {", 1)[1].split("try_files /landing.html", 1)[
        0
    ]  # if 블록의 } 를 넘어서
    # 매번 다시 확인하되 bfcache 에는 들어가게 — no-store 가 아니다
    assert 'add_header Cache-Control "no-cache" always;' in root
