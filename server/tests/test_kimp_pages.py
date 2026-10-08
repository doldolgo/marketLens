"""검색어 페이지 둘(김프 차트·김프 히스토리) 계약 — nginx 위치·정적 파일·링크·sitemap 을 파일로 읽어 단언한다 (스펙 044 §4).

두 페이지는 privacy.html 과 같은 방식(번들 밖 정적 한 파일, 외부 자원 0, CSP)이고, kimp-history 만 /api/landing 을 부른다.
"""

import json
import re

from tests.test_deploy import _args, _locations, _public_server, _route
from tests.test_landing_seo import PUBLIC, SITE, _page
from tests.test_privacy import _assert_no_external_resources, _read

MODIFIED = "2026-10-08"
CSP_STATIC = (
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
CSP_LIVE = (
    "default-src 'none'; connect-src 'self'; img-src 'self'; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
PAGES = {
    "kimp-chart": {
        "title": "김프 차트 - 실시간·과거 김치프리미엄 봉 차트 | KimpTrack",
        "h1": "김프 차트 — 실시간·과거 김치프리미엄 봉 차트",
        "keyword": "김프 차트",
        "csp": CSP_STATIC,
    },
    "kimp-history": {
        "title": "김프 히스토리 - 지난 김프·역프 사건 기록 | KimpTrack",
        "h1": "김프 히스토리 — 지난 김프·역프 사건 기록",
        "keyword": "김프 히스토리",
        "csp": CSP_LIVE,
    },
}


# --- nginx (§3.1) -----------------------------------------------------------------


def test_each_page_location_serves_the_file_with_no_cache_and_csp() -> None:
    locations = _locations(_public_server())
    for slug, spec in PAGES.items():
        block = locations[("=", f"/{slug}")]
        assert _args(block, "try_files") == [[f"/{slug}.html", "=404"]], slug
        headers = _args(block, "add_header")
        assert ["Cache-Control", "no-cache", "always"] in headers, slug
        assert ["Content-Security-Policy", spec["csp"], "always"] in headers, slug
        assert len(headers) == 2, slug
        assert not _args(block, "proxy_pass") and not _args(block, "alias"), slug


def test_file_name_urls_fold_into_each_page() -> None:
    """/app/<slug>.html 은 dist 사본이 /app/ alias 로 CSP 없이 나가는 주소라 막는다 (privacy 와 같은 이유)."""
    locations = _locations(_public_server())
    for slug in PAGES:
        for path in (f"/{slug}.html", f"/app/{slug}.html"):
            assert locations[("=", path)] == [(["return", "301", f"/{slug}"], None)], (
                path
            )
        assert _route(f"/{slug}") == ("=", f"/{slug}")
        assert _route(f"/{slug}/") == (
            "/",
        )  # 끝 슬래시는 루트 location 의 try_files 로 404


# --- 페이지 (§3.2·§3.3) --------------------------------------------------------------


def test_each_page_has_search_metadata_and_one_h1_with_the_keyword() -> None:
    for slug, spec in PAGES.items():
        page = _page(f"{slug}.html")
        url = f"{SITE}{slug}"
        assert page.titles == [spec["title"]], slug
        assert len(spec["title"]) <= 60, slug
        assert spec["title"].startswith(spec["keyword"]), (
            slug
        )  # 검색어가 앞, 브랜드가 뒤
        description = page.meta["description"][0]
        assert 0 < len(description) <= 110, slug
        assert spec["keyword"] in description, slug
        assert "김프가" not in spec["title"] and "김프가" not in description, slug
        assert not re.search(
            "이득|이익|수익|차익|무위험", spec["title"] + description
        ), slug
        assert page.meta["robots"] == ["index, follow, max-image-preview:large"], slug
        assert page.meta["og:title"] == [spec["title"]], slug
        assert page.meta["og:description"] == [description], slug
        assert page.meta["og:url"] == [url], slug
        assert page.meta["og:image"] == [f"{SITE}landing/og-v2.png"], slug
        canonical = [
            link["href"] for link in page.links if link.get("rel") == "canonical"
        ]
        assert canonical == [url], slug
        assert page.headings["h1"] == [spec["h1"]], slug
        assert len(page.headings["h2"]) >= 3, slug


def test_each_page_structured_data_matches_the_visible_values() -> None:
    for slug, spec in PAGES.items():
        page = _page(f"{slug}.html")
        assert len(page.ld) == 1, slug
        data = json.loads(page.ld[0])
        assert data["@type"] == "WebPage", slug
        assert data["url"] == f"{SITE}{slug}", slug
        assert data["name"] == spec["title"], slug
        assert data["description"] == page.meta["description"][0], slug
        assert data["isPartOf"] == {"@id": f"{SITE}#website"}, slug
        assert data["dateModified"] == MODIFIED, slug
        image = data["primaryImageOfPage"]["url"]
        assert (PUBLIC / image.removeprefix(SITE)).is_file(), slug


def test_each_page_loads_nothing_external_and_only_history_calls_the_api() -> None:
    for slug in PAGES:
        html, page = _read(PUBLIC / f"{slug}.html")
        _assert_no_external_resources(slug, page)
        scripts = "".join(page.scripts)
        assert "WebSocket" not in scripts and "clarity" not in html, slug
        if slug == "kimp-history":
            assert "fetch('/api/landing'" in scripts
            assert "data-nosnippet" in html  # 실시간 사건 표는 스니펫에서 뺀다
        else:
            assert "fetch(" not in scripts, slug


def test_pages_link_each_other_the_dashboard_and_the_landing() -> None:
    for slug in PAGES:
        page = _page(f"{slug}.html")
        other = next(s for s in PAGES if s != slug)
        assert f"/{other}" in page.hrefs, slug
        assert "/app/?tab=history" in page.hrefs, slug
        assert "/" in page.hrefs and "/privacy" in page.hrefs, slug


def test_modified_date_is_the_same_in_the_footer_json_ld_and_sitemap() -> None:
    sitemap = (PUBLIC / "sitemap.xml").read_text("utf-8")
    for slug in PAGES:
        html = (PUBLIC / f"{slug}.html").read_text("utf-8")
        assert re.findall(r'<time datetime="([^"]+)">', html) == [MODIFIED], slug
        assert f"<loc>{SITE}{slug}</loc><lastmod>{MODIFIED}</lastmod>" in sitemap, slug


def test_landing_links_both_pages_in_the_footer_and_the_faq() -> None:
    html = (PUBLIC / "landing.html").read_text("utf-8")
    footer = html[html.index("<footer") : html.index("</footer>")]
    faq = html[html.index('id="faq"') : html.index("</main>")]
    for slug in PAGES:
        assert f'href="/{slug}"' in footer, slug
        assert f'href="/{slug}"' in faq, slug
