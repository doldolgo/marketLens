"""화면 분석(Clarity) 계약 — clarity.js·두 HTML·셸·nginx 를 파일로 읽어 단언하고, clarity.js 를 node 로 돌린다 (스펙 033 §4).

동의 방식이다: 세 칸에 모두 동의해 지금 판으로 저장한 방문자만 태그를 받는다(032 와 같은 값 계약). 띠 글자가 032 방침 동의
상자와 어긋나거나, 처리방침 게시 전에 ID 가 들어가거나, 띠가 타이머·외부 자원을 만들게 되면 여기서 멈춘다. 실제 브라우저의
띠·요청은 스펙 §5 의 명령이다.
"""

import json
import re

from tests.test_deploy import ROOT, _args, _locations, _public_server
from tests.test_privacy import (
    CONSENT_CELLS,
    NOTICE,
    SHARED_SENTENCES,
    _consent_box,
    _notes,
    _text,
)

WEB = ROOT / "web"
JS = (WEB / "public/clarity.js").read_text("utf-8")
PRIVACY = (WEB / "public/privacy.html").read_text("utf-8")
TAG = "https://www.clarity.ms/tag/"
# 띠 안에서 바깥을 가리키는 주소는 둘뿐 — 띠는 외부 자원을 부르지 않는다 (§3.3)
MS_PRIVACY = "https://privacy.microsoft.com/ko-kr/privacystatement"
MS_INQUIRY = "https://go.microsoft.com/fwlink/?linkid=2126612"
MUST = (
    "consentv2", "ad_Storage", "analytics_Storage", '"granted"', '"denied"', "globalPrivacyControl",
    "kt.analytics", "kt.analytics.v", "_clck", "_clsk", "_cltk", "DOMContentLoaded", TAG, "storage",
    "location.reload", "region", "aria-label", "checkbox", "label", "/privacy#consent", "_blank",
    "noopener", "kt:clarity", "선택한 대로 저장", "모두 거부", "<details>", "<summary", "내용 보기",
    "저장 규칙", "data-nosnippet",
)  # fmt: skip
MUST_NOT = (
    "setInterval", "setTimeout", "requestAnimationFrame", "Observer", "resolvedOptions", "Europe/",
    "'off'", '"off"', "identify", "www.kimptrack.com", "[허용]",
)  # fmt: skip


def _const(name: str) -> str:
    found = re.search(rf"const {name} = (.+)\n", JS)
    assert found, name
    return found.group(1)


def _clarity_id() -> str:
    return json.loads(_const("CLARITY_ID"))


def _strip_cells() -> list[str]:
    """띠 HTML 의 세 칸(체크 상자가 있는 칸) — 칸마다 label 과 <dl>."""
    html = JS.split("const HTML = `", 1)[1].split("`", 1)[0]
    return [
        c
        for c in re.findall(r'<div class="kt-c-cell">(.*?)</div>', html, flags=re.S)
        if "checkbox" in c
    ]


# --- 설정 한 곳 (§3.1) --------------------------------------------------------------


def test_settings_are_one_id_a_page_list_and_the_notice_version() -> None:
    assert re.fullmatch(r"[A-Za-z0-9]{0,32}", _clarity_id())
    pages = json.loads(_const("PAGES"))
    assert pages and set(pages) <= {"landing", "app"}
    # 032 동의 관리 스크립트의 판과 같다 — 다르면 한쪽의 동의가 다른 쪽에서 '정하지 않음' 이다
    assert json.loads(_const("NOTICE_VERSION")) == NOTICE


def test_an_id_needs_the_published_consent_policy() -> None:
    """ID 가 들어 있으면 032 방침이 게시된 모양이다 — 자리표시자 없음, 동의 방식의 국외 이전 근거 (§3.1)."""
    if not _clarity_id():
        return
    assert "〔" not in PRIVACY
    for text in ("Clarity", "kt.analytics", "제28조의8 제1항 제1호", 'id="consent"'):
        assert text in PRIVACY, text


# --- clarity.js 글자 (§3.2~§3.5) ----------------------------------------------------


def test_file_has_the_consent_contract_and_no_timers_or_regional_guessing() -> None:
    for text in MUST:
        assert text in JS, text
    for text in MUST_NOT:
        assert text not in JS, text
    # 폐기 예정인 옛 동의 API 를 부르지 않는다 — consentv2 만
    assert not re.search(r"""\(\s*["']consent["']""", JS)
    assert set(re.findall(r"https?://[^\s\"'`<>]+", JS)) == {
        TAG,
        MS_PRIVACY,
        MS_INQUIRY,
    }
    # 중요한 내용 표시 — 032 와 같이 20% 크게·굵게·밑줄 (§3.3)
    assert re.search(
        r"\.key\{font-size:1\.2em;font-weight:700;[^}]*text-decoration:underline", JS
    )


def test_strip_text_is_the_policy_consent_box_text() -> None:
    """같은 문장 셋·세 칸의 체크 글자·알릴 사항이 032 방침의 동의 상자와 같은 글자다(태그를 떼고 비교) (§3.3)."""
    strip = _text(JS)
    for sentence in SHARED_SENTENCES:
        assert sentence in strip, sentence
    cells = _strip_cells()
    policy = _consent_box(PRIVACY)[1]
    assert len(cells) == len(policy) == len(CONSENT_CELLS)
    for mine, theirs, (_, label, terms, _) in zip(
        cells, policy, CONSENT_CELLS, strict=True
    ):
        assert (
            re.search(
                r'<input type="checkbox" id="kt-c-\w+" /><span>(.*?)</span>', mine
            ).group(1)
            == label
        )
        assert [(_text(k), _text(v)) for k, v in _notes(mine).items()] == [
            (_text(k), _text(v)) for k, v in _notes(theirs).items()
        ], label
        assert set(_notes(mine)) == terms
        refusal = (
            _notes(mine).get("거부 권리·불이익") or _notes(mine)["거부 방법·절차·효과"]
        )
        assert 'href="/privacy#consent"' in refusal and "동의 철회" in refusal
        assert "<details>" in mine and "내용 보기" in mine
    # 세 칸은 처음에 빈 칸이다
    assert not re.search(r"<input [^>]*checked", JS)


# --- 싣는 곳 (§3.4·§3.6·§2) -----------------------------------------------------------


def test_landing_and_dashboard_load_the_file_once_and_unmask() -> None:
    shell = (WEB / "index.html").read_text("utf-8")
    landing = (WEB / "public/landing.html").read_text("utf-8")
    for name, html, src in (
        ("index.html", shell, "/clarity.js"),
        ("landing.html", landing, "clarity.js"),
    ):
        scripts = re.findall(r"<script\b[^>]*clarity[^>]*>", html)
        assert scripts == [f'<script defer src="{src}">'], name
        assert "clarity.ms" not in html, name
    # 앱 모듈보다 앞 — defer 는 문서 순서대로 실행된다(대기열이 셸의 첫 태그보다 먼저)
    assert shell.index('src="/clarity.js"') < shell.index('type="module"')
    assert '<div id="root" data-clarity-unmask="true"></div>' in shell
    assert '<body data-clarity-unmask="true">' in landing


def test_settings_link_sits_on_the_landing_footer_and_the_dashboard_header() -> None:
    landing = (WEB / "public/landing.html").read_text("utf-8")
    nav = landing[
        landing.index('<nav class="foot-nav"') : landing.index(
            "</nav>", landing.index('<nav class="foot-nav"')
        )
    ]
    assert re.search(
        r'<a [^>]*href="/privacy"[^>]*>개인정보 처리방침</a>\s*<a href="/privacy#consent">화면 분석 설정</a>',
        nav,
    )
    app = (WEB / "src/App.tsx").read_text("utf-8")
    link = re.search(r'<a href="/privacy#consent"([^>]*)>\s*화면 분석 설정\s*</a>', app)
    assert (
        link
        and 'target="_blank"' in link.group(1)
        and 'rel="noopener"' in link.group(1)
    )


def test_policy_admin_and_not_found_pages_never_load_clarity() -> None:
    """방침·404·관리자는 파일을 싣지 않고 Clarity 주소도 없다(방침 스크립트 주석이 판 계약으로 파일 이름을 말하는 것은 괜찮다)."""
    for rel in (
        "public/privacy.html",
        "public/404.html",
        "admin/index.html",
        "admin/admin.js",
    ):
        text = (WEB / rel).read_text("utf-8")
        assert not re.search(r"""src=["'][^"']*clarity""", text), rel
        assert "clarity.ms" not in text, rel


def test_nginx_revalidates_the_file_every_time() -> None:
    block = _locations(_public_server())[("=", "/clarity.js")]
    assert _args(block, "add_header") == [["Cache-Control", "no-cache", "always"]]


def test_search_terms_leave_the_url_and_filters_skip_the_clarity_hook() -> None:
    """검색어 셋은 URL 상태가 아니고, tab 밖의 URL 쓰기는 원래 replaceState 다 (§3.7)."""
    src = "".join(p.read_text("utf-8") for p in (WEB / "src").rglob("*.ts*"))
    for key in ("s.q", "g.q", "p.q"):
        assert f"useUrlState('{key}'" not in src, key
    assert "kt:clarity" in src
    url_state = (WEB / "src/shared/urlState.ts").read_text("utf-8")
    assert "History.prototype.replaceState" in url_state
    assert "/^[A-Z0-9]{1,20}$/" in url_state
