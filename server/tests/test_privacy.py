"""개인정보 처리방침 계약 — nginx 위치 셋·privacy.html·링크·sitemap 을 파일로 읽어 단언한다 (스펙 032 §4).

머지가 곧 게시다. 사람이 채울 자리표시자(`〔`)가 남았거나, 페이지가 외부 자원·서버 호출을 부르게 되거나, 033 과 함께 쓰는
동의 계약(`kt.analytics` — 동의한 방문자만 분석, 기본 꺼짐)과 방침의 필수 안내가 빠지면 여기서 멈춘다. 실제로 브라우저에서
CSP·버튼을 보는 검증은 스펙 §5 의 로컬 Docker·브라우저 명령이다.
"""

import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

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
# §3.3·§3.4 — 033 과 함께 쓰는 저장값, 동의·철회 안내, Clarity 약관이 요구하는 링크, 구제 기관 넷
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
    "운영 알림",  # §3.4-6 — 알림에 방문자 정보가 가지 않는다(025 제안·034 가 이 문장에 기댄다)
    'href="https://privacy.microsoft.com/ko-kr/privacystatement"',
    'href="https://optout.aboutads.info/"',
    "개인정보분쟁조정위원회 1833-6972",
    "개인정보침해신고센터 118",
    "대검찰청 1301",
    "경찰청 182",
    "아무것도 고르지 않으면 분석하지 않습니다",  # §3.3 — 값이 없으면 꺼짐(동의 방식)
    '<section id="consent"',  # 033 띠의 '처리방침에서 자세히 보기'·거부 칸 링크가 /privacy#consent 로 온다
    # §3.3 — 033 과 같은 다른 탭 반영 문장
    "철회하면 그 탭이 한 번 새로고침되어 분석을 멈추고, 동의하면 그 탭에서도 분석을 시작합니다",
)
# 동의 방식(사람 결정 2026-10-01)과 맞지 않는 문장 — 페이지에 있으면 거짓이다
# 파기는 날수 기한을 약속하지 않는다(§3.5 — '90일 뒤 지웁니다' 같은 단정 없음), Microsoft 의 자기 목적 이용은
# 어디서나 '씁니다' 로 단정한다(§3.4-2 — '쓸 수 있어' 로 흐리지 않는다)
MUST_NOT_SAY = (
    "허용으로 봅니다",
    "다시 허용",
    "유럽 시간대",
    "5일 안에",
    "90일 뒤 지웁니다",
    "쓸 수 있어",
)
# §3.3 — 033 의 띠와 같은 문장(머리·셋 모두 규칙·나이). 동의 상자 안 세 칸보다 앞에 있다
SHARED_SENTENCES = (
    "랜딩과 대시보드의 화면 이용 기록(클릭·스크롤 등)을 Microsoft Clarity(미국)로 보내 서비스 개선에 써도 될까요? "
    "Microsoft는 이 기록을 광고 등 자기 목적에도 씁니다. 동의하지 않아도 모든 기능을 그대로 씁니다.",
    "세 가지에 모두 동의하고 [선택한 대로 저장]을 누른 경우에만 분석합니다 — Clarity는 셋이 다 있어야 돌아가므로 "
    "하나라도 빠지면 거부로 저장합니다.",
    "만 14세 미만은 동의하지 마세요.",
)
# §3.3 — 상태 줄의 다섯 글자(스크립트가 그린다)
STATES = ("정하지 않음", "동의함", "거부함", "GPC로 거부", "저장할 수 없음")
# §3.3 — 동의 세 칸(법 제22조 제1항 — 나눠 각각 받는다): 체크 상자 id, 체크 글자, 그 동의의 알릴 사항
# (수집·이용 제15조 제2항 · 제3자 제공 제17조 제2항 · 국외 이전 제28조의8 제2항 — 033 의 띠와 같은 글자)
CONSENT_CELLS = (
    (
        "an-collect",
        "(선택) 화면 이용 기록의 수집·이용에 동의합니다",
        {"항목", "목적", "보유 기간", "거부 권리·불이익"},
    ),
    (
        "an-provide",
        "(선택) 화면 이용 기록을 Microsoft에 제공하는 데 동의합니다",
        {
            "받는 자",
            "받는 자의 목적",
            "항목",
            "받는 자의 보유 기간",
            "거부 권리·불이익",
        },
    ),
    (
        "an-transfer",
        "(선택) 화면 이용 기록을 미국으로 이전하는 데 동의합니다",
        {
            "받는 자·연락처",
            "국가·시기·방법",
            "항목",
            "목적·보유 기간",
            "거부 방법·효과",
        },
    ),
)
BOXES = [cell[0] for cell in CONSENT_CELLS]
# §3.3 — 세 칸의 항목 칸이 2절 없이도 다 읽히는지 보는 Clarity 항목들
CLARITY_ITEMS = (
    "페이지 주소",
    "이전 페이지 주소",
    "누른 링크의 주소",
    "클릭·스크롤·마우스 움직임·화면 크기",
    "화면 내용",
    "고른 코인 이름",
    "기기·브라우저·운영체제",
    "IP 주소",
    "_clck",
    "_clsk",
    "_cltk",
)
# §3.4-4 — 제17조 제2항 다섯 가지 + 근거
PROVISION_TERMS = {
    "받는 자",
    "받는 자의 목적",
    "항목",
    "보유 기간",
    "거부 권리·불이익",
    "근거",
}
# §3.4-7 — 작성지침의 행태정보 항목
BEHAVIOR_TERMS = {
    "수집 항목",
    "수집 방법",
    "목적",
    "보유 기간",
    "수집하는 사업자",
    "거부 방법",
}
# §3.3 — 동의 관리 스크립트를 node 로 돌리는 가짜 브라우저. 표준 입력의 [스크립트, 경우들] 을 받아 경우마다 새로 돌리고,
# 버튼 클릭·다른 탭의 storage 이벤트 뒤의 저장값·쿠키·상태 글자·보이는 요소를 JSON 으로 낸다. 라이브러리 없음.
HARNESS = r"""
const [script, cases] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
const KEY = 'kt.analytics'
const out = cases.map((c) => {
  const data = new Map(c.stored === undefined ? [] : [[KEY, c.stored]])
  const fail = new Set(c.fail || [])
  const localStorage = {
    getItem: (k) => { if (fail.has('get')) throw new Error('get'); return data.has(k) ? data.get(k) : null },
    setItem: (k, v) => { if (fail.has('set')) throw new Error('QuotaExceededError'); data.set(k, String(v)) },
    removeItem: (k) => { if (fail.has('remove')) throw new Error('remove'); data.delete(k) },
  }
  const els = {}
  const el = (id) => (els[id] = els[id] || {
    hidden: c.hidden.includes(id), textContent: '', on: {},
    addEventListener(type, fn) { this.on[type] = fn },
  })
  const jar = new Set(c.cookies || [])
  const writes = []
  const document = {
    getElementById: el,
    get cookie() { return [...jar].map((n) => n + '=1').join('; ') },
    set cookie(line) {
      writes.push(line)
      const name = line.split('=')[0].trim()
      if (/;\s*max-age=0\s*(;|$)/i.test(line)) jar.delete(name); else jar.add(name)
    },
  }
  const listeners = {}
  const window = { localStorage, addEventListener: (type, fn) => { listeners[type] = fn } }
  const navigator = c.gpc ? { globalPrivacyControl: true } : {}
  new Function('window', 'document', 'navigator', 'location', script)(
    window, document, navigator, { hostname: 'kimptrack.com' })
  for (const step of c.steps || []) {
    if (step.startsWith('click:')) el(step.slice(6)).on.click()
    else { data.set(KEY, step.slice(6)); listeners.storage({ key: KEY }) } // 'other:<값>'
  }
  return {
    stored: data.has(KEY) ? data.get(KEY) : null, cookies: [...jar].sort(), writes,
    state: el('an-value').textContent, shown: Object.keys(els).filter((id) => !els[id].hidden).sort(),
  }
})
process.stdout.write(JSON.stringify(out))
"""
# 스크립트가 숨기고 보이는 상태 요소 — 버튼 둘은 an-actions 가 함께 숨긴다
STATUS_IDS = {"an-state", "an-actions", "an-gpc", "an-nostore", "an-stuck"}
WITH_BUTTONS = {"an-state", "an-actions"}
# (이름, 경우, 뒤의 저장값, 상태 글자, 보이는 상태 요소) — 경우: stored 처음 값(없으면 키 없음), gpc, fail(get·set·remove 예외),
# steps(click:<버튼 id> · other:<다른 탭이 쓴 값>)
CONSENT_CASES = [
    ("값 없음", {}, None, "정하지 않음", WITH_BUTTONS),
    ("동의", {"stored": "granted"}, "granted", "동의함", WITH_BUTTONS),
    ("거부", {"stored": "denied"}, "denied", "거부함", WITH_BUTTONS),
    ("그 밖의 값", {"stored": "off"}, "off", "정하지 않음", WITH_BUTTONS),
    (
        "GPC 가 동의보다 앞선다",
        {"stored": "granted", "gpc": True},
        "granted",
        "GPC로 거부",
        {"an-state", "an-gpc"},
    ),
    (
        "읽기 예외",
        {"fail": ["get"]},
        None,
        "저장할 수 없음",
        {"an-state", "an-nostore"},
    ),
    ("[동의]", {"steps": ["click:an-grant"]}, "granted", "동의함", WITH_BUTTONS),
    (
        "거부 뒤 [동의]",
        {"stored": "denied", "steps": ["click:an-grant"]},
        "granted",
        "동의함",
        WITH_BUTTONS,
    ),
    (
        "[동의 철회]",
        {"stored": "granted", "steps": ["click:an-withdraw"]},
        "denied",
        "거부함",
        WITH_BUTTONS,
    ),
    (
        "쓰기 예외의 [동의] — 실제 값 그대로",
        {"fail": ["set"], "steps": ["click:an-grant"]},
        None,
        "정하지 않음",
        WITH_BUTTONS,
    ),
    (
        "쓰기 예외의 [동의 철회] — 값을 지운다",
        {"stored": "granted", "fail": ["set"], "steps": ["click:an-withdraw"]},
        None,
        "정하지 않음",
        WITH_BUTTONS,
    ),
    (
        "지우기도 예외 — 동의가 남았다고 알린다",
        {
            "stored": "granted",
            "fail": ["set", "remove"],
            "steps": ["click:an-withdraw"],
        },
        "granted",
        "동의함",
        WITH_BUTTONS | {"an-stuck"},
    ),
    ("다른 탭의 동의", {"steps": ["other:granted"]}, "granted", "동의함", WITH_BUTTONS),
    (
        "다른 탭의 철회",
        {"stored": "granted", "steps": ["other:denied"]},
        "denied",
        "거부함",
        WITH_BUTTONS,
    ),
]
# §3.4-6 절 — 받는 곳마다 법 제28조의8 제2항 다섯 가지(+ 근거)
TRANSFER_TERMS = {
    "항목",
    "국가·시기·방법",
    "받는 자·연락처",
    "목적·보유 기간",
    "거부 방법·효과",
    "근거",
}


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


def _section(html: str, label: str) -> str:
    """`<section aria-labelledby="…">` 하나의 본문."""
    start = re.search(rf'<section[^>]* aria-labelledby="{label}"', html)
    assert start, label
    body = html[start.start() :]
    return body[: body.index("</section>")]


def _terms(dl: str) -> set[str]:
    return set(re.findall(r"<dt>(.*?)</dt>", dl))


def _run_consent_script(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 동의 관리 스크립트를 돌리지 못한다")
    html, page = _read(PUBLIC / "privacy.html")
    hidden = re.findall(r'<[^>]* id="(an-[\w-]+)"[^>]*\shidden[\s>]', html)
    payload = json.dumps(
        ["".join(page.scripts), [{**c, "hidden": hidden} for c in cases]]
    )
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


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
    assert "화면 분석 동의 관리" in page.h2


def test_page_loads_nothing_external_and_scripts_make_no_requests() -> None:
    html, page = _read(PUBLIC / "privacy.html")
    _assert_no_external_resources("privacy.html", page)
    styles = "".join(re.findall(r"<style\b.*?</style>", html, flags=re.S))
    assert "@import" not in styles and "url(" not in styles
    assert page.scripts, "동의 관리 버튼 스크립트가 없다"
    # 인라인만 — CSP 의 script-src 가 'unsafe-inline' 뿐이라 같은 출처 파일도 막힌다
    assert not [src for tag, src in page.srcs if tag == "script"]
    for body in page.scripts:
        for call in NO_CALLS:
            assert call not in body, call


def test_page_states_the_consent_contract_and_required_notices() -> None:
    html, page = _read(PUBLIC / "privacy.html")
    for text in MUST_SAY:
        assert text in html, text
    for text in MUST_NOT_SAY:
        assert text not in html, text
    # 같은 모양의 버튼 둘 — 철회는 동의만큼 쉽다 — 과 자바스크립트가 꺼졌을 때의 안내 (§3.3·§3.7)
    assert '<button class="btn" type="button" id="an-grant">동의</button>' in html
    assert (
        '<button class="btn" type="button" id="an-withdraw">동의 철회</button>' in html
    )
    assert "<noscript>" in html
    # 두 버튼의 너비는 글자 길이가 아니라 같은 칸 너비를 따른다
    assert re.search(r"\.actions \{[^}]*grid-template-columns: repeat\(auto-fit", html)
    script = "".join(page.scripts)
    for state in STATES:
        assert f"'{state}'" in script, state
    assert "'granted'" in script and "'denied'" in script


def test_consent_script_shows_the_stored_value_and_the_buttons_write_it() -> None:
    """동의 관리 스크립트를 node 로 돌린다 — 상태 글자·보이는 요소·저장값·쿠키가 실제 저장값과 같다 (§3.3)."""
    cookies = ["_clck", "_clsk", "other"]
    results = _run_consent_script(
        [{**case, "cookies": cookies} for _, case, *_ in CONSENT_CASES]
    )
    for (name, case, stored, state, shown), got in zip(
        CONSENT_CASES, results, strict=True
    ):
        assert got["stored"] == stored, name
        assert got["state"] == state, name
        assert set(got["shown"]) & STATUS_IDS == shown, name
        withdrew = "click:an-withdraw" in case.get("steps", [])
        assert got["cookies"] == (["other"] if withdrew else cookies), name


def test_withdrawal_expires_clarity_cookies_in_both_domain_shapes() -> None:
    (got,) = _run_consent_script(
        [{"stored": "granted", "cookies": ["_clck"], "steps": ["click:an-withdraw"]}]
    )
    for name in ("_clck", "_clsk"):
        lines = [line for line in got["writes"] if line.startswith(f"{name}=;")]
        assert all("Max-Age=0" in line and "path=/" in line for line in lines), name
        assert any("domain=" not in line for line in lines), name
        assert any(line.endswith("; domain=kimptrack.com") for line in lines), name


def test_consent_box_splits_three_consents_before_the_buttons() -> None:
    """동의를 셋으로 나눠 칸마다 그 동의의 알릴 사항을 버튼 앞에 둔다 (§3.3 — 법 제22조 제1항)."""
    html, _ = _read(PUBLIC / "privacy.html")
    box = _section(html, "consent-title")
    box = box[box.index('<div class="consent">') : box.index('id="an-actions"')]
    # 033 의 띠와 같은 문장 — 세 칸보다 앞
    first_cell = box.index('<div class="cell">')
    for sentence in SHARED_SENTENCES:
        assert 0 <= box.find(sentence) < first_cell, sentence
    cells = re.findall(r'<div class="cell">(.*?)</div>', box, flags=re.S)
    assert len(cells) == len(CONSENT_CELLS)
    for cell, (box_id, label, terms) in zip(cells, CONSENT_CELLS, strict=True):
        assert re.search(
            rf'<label class="pick"><input type="checkbox" id="{box_id}" disabled /><span>'
            rf"{re.escape(label)}</span></label>",
            cell,
        ), box_id
        dls = re.findall(r"<dl[^>]*>(.*?)</dl>", cell, flags=re.S)
        assert len(dls) == 1 and _terms(dls[0]) == terms, box_id
        # 033 의 띠가 이 칸들을 옮긴다 — 랜딩·대시보드에는 방침의 절이 없으니 칸은 다른 절을 가리키지 않는다
        assert "절" not in dls[0], box_id
        notes = dict(re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", dls[0], flags=re.S))
        assert "등" not in notes["항목"], box_id
        for item in CLARITY_ITEMS:
            assert item in notes["항목"], (box_id, item)
        # 거부 칸은 거부해도 불이익이 없다는 것과 철회하는 곳을 적는다 (법 제28조의8 제2항 제5호·제38조 제4항)
        refusal = notes.get("거부 권리·불이익") or notes["거부 방법·효과"]
        assert "불이익이 없습니다" in refusal, box_id
        assert 'href="/privacy#consent"' in refusal and "[동의 철회]" in refusal
    collect, provide, transfer = (
        dict(re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", cell, flags=re.S))
        for cell in cells
    )
    assert "KimpTrack의 화면 이용 분석" in collect["목적"]
    assert provide["받는 자"] == "Microsoft Corporation(미국)"
    assert "Microsoft Advertising" in provide["받는 자의 목적"]
    assert "Microsoft 개인정보처리방침" in provide["받는 자의 보유 기간"]
    assert transfer["국가·시기·방법"].startswith("미국 — ")
    assert "Microsoft Corporation" in transfer["받는 자·연락처"]
    assert "Microsoft Advertising" in transfer["목적·보유 기간"]


def test_clarity_rests_on_consent_for_collection_provision_and_transfer() -> None:
    """사람 결정 2026-10-01 — 수집·제공은 동의, 국외 이전은 별도 동의 (§3.6)."""
    html, _ = _read(PUBLIC / "privacy.html")
    assert "제15조 제1항 제1호" in _section(html, "s2")
    provision = _section(html, "s4")
    dls = re.findall(r"<dl[^>]*>(.*?)</dl>", provision, flags=re.S)
    assert len(dls) == 1 and _terms(dls[0]) == PROVISION_TERMS
    assert "제17조 제1항 제1호" in provision and "제17조 제1항 제2호" not in provision
    assert "불이익이 없습니다" in provision
    groups = re.findall(
        r"<h3>(.*?)</h3>\s*<dl[^>]*>(.*?)</dl>", _section(html, "s6"), flags=re.S
    )
    microsoft = [body for name, body in groups if name.startswith("Microsoft")]
    assert len(microsoft) == 1
    basis = re.search(r"<dt>근거</dt><dd>(.*?)</dd>", microsoft[0]).group(1)
    assert "제28조의8 제1항 제1호" in basis and "제3호" not in basis
    assert "불이익이 없습니다" in microsoft[0]


def test_behavioral_information_has_the_guideline_items() -> None:
    """행태정보를 제3자가 광고 목적에도 쓸 수 있다 — 7절 소항목에 작성지침 항목 (§3.4-7)."""
    html, _ = _read(PUBLIC / "privacy.html")
    section = _section(html, "s7")
    block = re.search(
        r"<h3>행태정보의 수집·이용·제공과 거부</h3>.*?<dl[^>]*>(.*?)</dl>",
        section,
        flags=re.S,
    )
    assert block and _terms(block.group(1)) == BEHAVIOR_TERMS
    assert "Microsoft Corporation" in block.group(1) and "광고" in block.group(1)


def test_every_overseas_recipient_lists_the_five_items() -> None:
    html, _ = _read(PUBLIC / "privacy.html")
    section = _section(html, "s6")
    groups = re.findall(r"<h3>(.*?)</h3>\s*<dl[^>]*>(.*?)</dl>", section, flags=re.S)
    assert len(groups) >= 5
    for name, body in groups:
        assert _terms(body) == TRANSFER_TERMS, name


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
