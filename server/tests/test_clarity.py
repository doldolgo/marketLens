"""화면 분석(Clarity)·방문 통계(GA4) 계약 — clarity.js·두 HTML·셸·nginx 를 파일로 읽어 단언하고, clarity.js 를 node 로 돌린다
(스펙 033 §4·075 §4).

동의 방식이다: 세 칸에 모두 동의해 지금 판으로 저장한 방문자만 태그를 받는다(032 와 같은 값 계약). 띠 글자가 032 방침 동의
상자와 어긋나거나, 처리방침 게시 전에 ID 가 들어가거나, 띠가 타이머·외부 자원을 만들게 되면 여기서 멈춘다. 실제 브라우저의
띠·요청은 스펙 §5 의 명령이다.
"""

import json
import os
import re
import shutil
import subprocess
import time

import pytest

from tests.test_deploy import ROOT, _args, _locations, _public_server
from tests.test_privacy import (
    CONSENT_CELLS,
    NOTICE,
    OLD,
    SHARED_SENTENCES,
    _cell_notes,
    _consent_box,
    _notes,
    _text,
)

WEB = ROOT / "web"
REPO_JS = (WEB / "public/clarity.js").read_text("utf-8")
# 033 §3.2 — 레포 파일은 동의 창 없이 모두에게 켠다(TRACK_ALL, 사람 결정 2026-10-18). 아래 동의 방식 시험은 스위치를 끈 사본으로 돈다
JS = REPO_JS.replace("const TRACK_ALL = true", "const TRACK_ALL = false", 1)
assert JS != REPO_JS
PRIVACY = (WEB / "public/privacy.html").read_text("utf-8")
TAG = "https://www.clarity.ms/tag/"
# 075 — GA4 태그(측정 ID 를 붙인다)
GA_TAG = "https://www.googletagmanager.com/gtag/js?id="
# 띠 안에서 바깥을 가리키는 주소는 셋뿐 — 띠는 외부 자원을 부르지 않는다 (§3.3, 075 — Google 개인정보처리방침)
MS_PRIVACY = "https://privacy.microsoft.com/ko-kr/privacystatement"
MS_INQUIRY = "https://go.microsoft.com/fwlink/?linkid=2126612"
G_PRIVACY = "https://policies.google.com/privacy?hl=ko"
MUST = (
    "consentv2", "ad_Storage", "analytics_Storage", '"granted"', '"denied"', "globalPrivacyControl",
    "kt.analytics", "kt.analytics.v", "_clck", "_clsk", "_cltk", "DOMContentLoaded", TAG, "storage",
    "location.reload", "dialog", "aria-modal", "inert", "aria-label", "checkbox", "label", "/privacy#consent", "_blank",
    "noopener", "kt:clarity", "선택한 대로 저장", "모두 동의", "하루 동안 안 보기", "kt.analytics.until", "<details>", "<summary", "내용 보기",
    "저장 규칙", "data-nosnippet", "pageshow", "persisted", "stopImmediatePropagation",
    # 075 — GA: 대기열·동의 신호 넷·끈 신호 둘·검색어 뺀 주소·쿠키 기간·쿠키 이름
    "GA_ID", "dataLayer", "gtag", "ad_storage", "ad_user_data", "ad_personalization", "analytics_storage",
    "allow_google_signals: false", "allow_ad_personalization_signals: false", "page_location", "cookie_expires",
    "_ga", "_ga_", GA_TAG, "GA_SEARCH_KEYS",
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


def _ga_id() -> str:
    return json.loads(_const("GA_ID"))


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
    # 075 — GA 측정 ID 는 비었거나 G-<영문 대문자·숫자>, 쿠키 기간은 2개월(60일)
    assert re.fullmatch(r"(G-[A-Z0-9]{4,16})?", _ga_id())
    assert _const("GA_COOKIE_SECONDS") == "60 * 24 * 60 * 60"
    pages = json.loads(_const("PAGES"))
    assert pages and set(pages) <= {"landing", "app"}
    # 032 동의 관리 스크립트의 판과 같다 — 다르면 한쪽의 동의가 다른 쪽에서 '정하지 않음' 이다
    assert json.loads(_const("NOTICE_VERSION")) == NOTICE
    # 방침의 보이는 판 글자도, 052 의 attention.js 가 생기면 그 판도 같다 — 동의 하나로 Clarity·영역 통계가 함께 돈다 (051 §3.2)
    assert f'<code id="an-version">{NOTICE}</code>' in PRIVACY
    attention = WEB / "public/attention.js"
    if attention.exists():
        found = re.search(
            r"const NOTICE_VERSION = (.+)\n", attention.read_text("utf-8")
        )
        assert found and json.loads(found.group(1)) == NOTICE


def test_an_id_needs_the_published_consent_policy() -> None:
    """ID 가 들어 있으면 032 방침이 게시된 모양이다 — 자리표시자 없음, 동의 방식의 국외 이전 근거 (§3.1)."""
    if not _clarity_id():
        return
    assert "〔" not in PRIVACY
    for text in ("Clarity", "kt.analytics", "제28조의8 제1항 제1호", 'id="consent"'):
        assert text in PRIVACY, text


def test_a_ga_id_needs_the_policy_that_names_google_analytics() -> None:
    """075 §4 — GA 측정 ID 가 들어 있으면 방침이 Google Analytics 를 알리는 판이다 — 2절 소절·받는 자 Google LLC·국외 이전 묶음."""
    if not _ga_id():
        return
    assert "〔" not in PRIVACY
    for text in (
        "<h3>방문 통계 — Google Analytics</h3>",
        "Microsoft Corporation(미국), Google LLC(미국)",
        "<h4>Google LLC — 방문 통계(Google Analytics)</h4>",
        "_ga_*",
    ):
        assert text in PRIVACY, text


# --- clarity.js 글자 (§3.2~§3.5) ----------------------------------------------------


def test_file_has_the_consent_contract_and_no_timers_or_regional_guessing() -> None:
    for text in MUST:
        assert text in JS, text
    for text in MUST_NOT:
        assert text not in JS, text
    # 폐기 예정인 Clarity 의 옛 동의 API 를 부르지 않는다 — consentv2 만(075 의 gtag("consent", "default") 는 GA 의 동의 신호다)
    assert not re.search(r"""clarity\(\s*["']consent["']""", JS)
    assert re.search(r'gtag\(\s*"consent",\s*"default"', JS)
    assert set(re.findall(r"https?://[^\s\"'`<>]+", JS)) == {
        TAG,
        GA_TAG,
        MS_PRIVACY,
        MS_INQUIRY,
        G_PRIVACY,
    }
    # 중요한 내용 표시 — 032 와 같이 20% 크게·굵게·밑줄 (§3.3)
    assert re.search(
        r"\.key\{font-size:1\.2em;font-weight:700;[^}]*text-decoration:underline", JS
    )
    # 글자 15px 이상 — 띠 기본 15px 이고 어느 요소도 그보다 줄이지 않는다 (§3.3 접근성)
    css = JS.split("const CSS = `", 1)[1].split("`", 1)[0]
    assert re.search(r"#kt-consent\{[^}]*font:400 15px/", css)
    for size in re.findall(r"font-size:([\d.]+)em", css):
        assert float(size) >= 1, size
    assert not re.search(r"font-size:\d+px", css)


def test_strip_text_is_the_policy_consent_box_text() -> None:
    """같은 문장 셋·세 칸의 체크 글자·알릴 사항이 032 방침의 동의 상자와 같은 글자다 (§3.3).

    알릴 사항은 원문 HTML(`<dt>`·`<dd>`)을 그대로 비교한다 — 중요한 내용 표시(`<strong class="key">`)가 빠지거나 다른 칸으로
    옮겨도 멈춘다. 문장 셋은 자리까지 본다: 머리와 나이는 맨 위 한 문단, 셋 모두 규칙은 '저장 규칙' 안.
    """
    head, rule, age = SHARED_SENTENCES
    html = JS.split("const HTML = `", 1)[1].split("`", 1)[0]
    first = re.search(r'<div class="kt-c-body"[^>]*>\s*<p>(.*?)</p>', html, flags=re.S)
    assert first and _text(first.group(1)) == f"{head} {age}"
    # 051 §3.3-4 — KimpTrack 서버의 통계는 띠가 없는 페이지에서도 저장된 동의로 돈다(모든 페이지). 060 §3.3-4 — 영역과 요소
    assert "화면의 영역과 요소" in first.group(1) and "모든 페이지" in first.group(1)
    rules = re.search(
        r"<details><summary>저장 규칙</summary><p>(.*?)</p></details>", html, flags=re.S
    )
    assert rules and _text(rules.group(1)) == rule
    cells = _strip_cells()
    policy = _consent_box(PRIVACY)[1]
    assert len(cells) == len(policy) == len(CONSENT_CELLS)
    for mine, theirs, (_, label, terms, key_terms) in zip(
        cells, policy, CONSENT_CELLS, strict=True
    ):
        assert (
            re.search(
                r'<input type="checkbox" id="kt-c-\w+" /><span>(.*?)</span>', mine
            ).group(1)
            == label
        )
        notes = _notes(mine)
        assert list(notes.items()) == list(_notes(theirs).items()), label
        # 칸 끝 한 줄(051 — 수집·이용 칸의 국내 보관)도 같은 글자
        assert _cell_notes(mine) == _cell_notes(theirs), label
        assert set(notes) == terms
        for term, value in notes.items():
            key = re.fullmatch(r'<strong class="key">.*</strong>', value, flags=re.S)
            assert bool(key) == (term in key_terms), (label, term)
            assert value.count('class="key"') == (term in key_terms), (label, term)
        refusal = notes.get("거부 권리·불이익") or notes["거부 방법·절차·효과"]
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


def test_no_settings_link_and_the_policy_hides_consent_controls() -> None:
    """033 §3.2 — 동의 창 없이 모두에게 켜므로(2026-10-18) 랜딩 바닥·대시보드 헤더·메뉴의 '화면 분석 설정' 링크가 없고, 처리방침의
    동의 관리 절(#consent)은 hidden 이다(본문 글은 그대로)."""
    landing = (WEB / "public/landing.html").read_text("utf-8")
    app = (WEB / "src/App.tsx").read_text("utf-8")
    for text in (landing, app):
        assert "/privacy#consent" not in text and "consent-settings" not in text
    assert '<a class="foot-privacy" href="/privacy">개인정보 처리방침</a>' in landing
    assert (
        '<section id="consent" aria-labelledby="consent-title" data-area="consent" hidden>'
        in PRIVACY
    )


def test_policy_and_search_pages_load_the_file_once_but_404_and_admin_never() -> None:
    """방침·검색어 페이지 둘은 `/clarity.js` 를 한 번 싣는다(2026-10-17 — 받기만 하고 곧바로 끝난다, 서버 기록의 '확인' 신호).
    404·관리자 파일 전부는 싣지 않고(src) Clarity 주소도 없다 (§4)."""
    for name in ("privacy.html", "kimp-chart.html", "kimp-history.html"):
        text = (WEB / "public" / name).read_text("utf-8")
        assert re.findall(r"""src=["']([^"']*clarity[^"']*)""", text) == [
            "/clarity.js"
        ], name
        assert "clarity.ms" not in text, name
    admin = sorted(p for p in (WEB / "admin").rglob("*") if p.is_file())
    # 관리자 화면 v3(064) — 페이지 셋·모듈·스타일·차트 사본까지 모두 본다
    assert {p.name for p in admin} >= {
        "index.html",
        "server.html",
        "traffic.html",
        "common.js",
        "admin.css",
    }
    for path in (WEB / "public/404.html", *admin):
        text = path.read_text("utf-8")
        assert not re.search(r"""src=["'][^"']*clarity""", text), path.name
        assert "clarity.ms" not in text, path.name


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


# --- clarity.js 를 node 로 (§3.2~§3.5) ---------------------------------------------
# 가짜 window·document·location 에서 돌리고, 단계(ready·check·click·다른 탭의 저장값) 뒤의 저장값·띠·대기열·태그·쿠키를 낸다
HARNESS = r"""
const [script, cases] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
process.stdout.write(JSON.stringify(cases.map((c) => {
  const data = new Map(Object.entries(c.ls || {}))
  const fail = new Set(c.fail || [])
  const localStorage = {
    getItem: (k) => { if (fail.has('get')) throw new Error('get'); return data.has(k) ? data.get(k) : null },
    setItem: (k, v) => { if (fail.has('set')) throw new Error('set'); data.set(k, String(v)) },
    removeItem: (k) => data.delete(k),
  }
  const session = new Set(['_cltk'])
  const cookies = [], head = [], body = [], doc = {}, win = {}, capture = {}, fired = []
  let reloads = 0, stopped = 0
  const el = (tag) => ({
    tag, attrs: {}, kids: {}, on: {}, anchors: [], checked: false, focused: 0, focus() { this.focused++ },
    setAttribute(k, v) { this.attrs[k] = v },
    set innerHTML(h) { this.html = h; this.anchors = (h.match(/<a /g) || []).map(() => ({ target: '', rel: '' })) },
    querySelector(sel) { return this.kids[sel] || (this.kids[sel] = el('stub')) },
    querySelectorAll(sel) { return sel === 'a' ? this.anchors : [] },
    addEventListener(t, fn) { this.on[t] = fn },
    remove() { for (const list of [head, body]) if (list.includes(this)) list.splice(list.indexOf(this), 1) },
  })
  const document = {
    // defer 로 실리면 readyState 는 이미 interactive 이고 DOMContentLoaded 전이다
    readyState: c.defer ? 'interactive' : 'loading', currentScript: c.defer ? { defer: true } : null, createElement: el,
    set cookie(line) { cookies.push(line) },
    // 'jar' 는 이 문서가 처음 받은 쿠키 이름(075 — _ga_<기호> 는 이름을 읽어 지운다)
    get cookie() { return (c.jar || []).map((n) => n + '=1').join('; ') },
    head: { appendChild: (e) => head.push(e) },
    body: { get firstChild() { return body[0] || null }, get children() { return body }, insertBefore: (e) => body.unshift(e) },
    // 'bar' 면 스크롤 막대가 있는 문서(창 폭 > 문서 폭)
    documentElement: { style: { overflow: '', scrollbarGutter: '' }, clientWidth: c.bar ? 1425 : 1440 },
    getElementById: (id) => head.concat(body).find((e) => e.id === id) || null,
    addEventListener: (t, fn) => { doc[t] = fn },
  }
  const window = {
    innerWidth: 1440, localStorage, sessionStorage: { removeItem: (k) => session.delete(k) },
    addEventListener: (t, fn, opt) => { win[t] = fn; capture[t] = opt === true || !!(opt && opt.capture) },
    dispatchEvent: (e) => fired.push(e.type),
  }
  // 'framed' 면 틀 안(window.top 이 다른 창), search 는 053 덮어 보기 표시
  window.top = c.framed ? {} : window
  const location = {
    hostname: c.host || 'kimptrack.com', origin: 'https://' + (c.host || 'kimptrack.com'), pathname: c.path || '/',
    search: c.search || '', hash: c.hash || '', reload: () => reloads++,
  }
  class Event { constructor(type) { this.type = type } }
  new Function('window', 'document', 'navigator', 'location', 'Event', script)(
    window, document, c.gpc ? { globalPrivacyControl: true } : {}, location, Event)
  // 동의 창 뒤의 페이지 — 창보다 먼저 body 에 있던 형제 둘(랜딩의 header·main, 대시보드의 #root 자리)
  const page = [el('header'), el('main')]
  body.push(...page)
  const strip = () => body.find((e) => e.id === 'kt-consent')
  const snap = () => ({
    stored: data.get('kt.analytics') ?? null, v: data.get('kt.analytics.v') ?? null, until: data.get('kt.analytics.until') ?? null,
    strip: strip() ? { first: body[0] === strip(), ...strip().attrs, links: strip().anchors.map((a) => a.target + '|' + a.rel) } : null,
    queue: window.clarity ? window.clarity.q.map((a) => Array.from(a)) : null,
    // 075 — GA 대기열(gtag 호출마다 arguments 하나). js 의 Date 는 'date' 로
    layer: window.dataLayer ? window.dataLayer.map((a) => Array.from(a).map((x) => x instanceof Date ? 'date' : x)) : null,
    tags: head.filter((e) => e.tag === 'script').map((e) => [e.src, e.async]),
    style: head.filter((e) => e.tag === 'style').length, fired, reloads, stopped, capture,
    inert: page.map((e) => !!e.inert), html: { ...document.documentElement.style },
    focused: strip() ? strip().querySelector('.kt-c-box').focused : 0,
    forgot: cookies.length > 0 && !session.has('_cltk'), cookies, listens: 'storage' in win || 'pageshow' in win,
  })
  const first = JSON.parse(JSON.stringify(snap()))
  for (const step of c.steps || []) {
    const [kind, arg = ''] = step.split(':')
    if (kind === 'ready') { document.readyState = 'interactive'; if (doc.DOMContentLoaded) doc.DOMContentLoaded() }
    else if (kind === 'check') strip().querySelector('#' + arg).checked = true
    else if (kind === 'click') strip().querySelector('#' + arg).on.click()
    else if (kind === 'clear') { data.clear(); win.storage({ key: null }) }
    // 'quiet:<키>=<값>' — 이 문서가 뒤로 가기 캐시에 든 동안 다른 페이지가 쓴 값(storage 이벤트 없음, 빈 값은 지움)
    else if (kind === 'quiet') { const [k, v] = arg.split('='); if (v) data.set(k, v); else data.delete(k) }
    // 'back' — 캐시에서 복원(pageshow persisted), 'show' — 보통 로드의 pageshow
    else if (kind === 'back' || kind === 'show') win.pageshow({ persisted: kind === 'back', stopImmediatePropagation: () => stopped++ })
    else { const [k, v] = arg.split('='); data.set(k, v); win.storage({ key: k }) } // 'other:<키>=<값>'
  }
  return { first, last: snap() }
})))
"""
ALL = ["check:kt-c-collect", "check:kt-c-provide", "check:kt-c-transfer"]
ON = {"kt.analytics": "granted", "kt.analytics.v": NOTICE}
CONSENT = ["consentv2", {"ad_Storage": "denied", "analytics_Storage": "granted"}]
DAY_MS = 86_400_000


def _ms(offset: int = 0) -> str:
    """지금(epoch ms) + offset 의 글자 — `kt.analytics.until` 값 (079 §3.1)."""
    return str(int(time.time() * 1000) + offset)


TAG_ID = [[TAG + _clarity_id(), True]]


# 위 Clarity 동작 테스트는 GA 를 끈 사본으로 돈다 — GA 는 아래 075 절이 시험 ID 를 넣은 사본으로 따로 본다
CLARITY_ONLY_JS = JS.replace(f"const GA_ID = {_const('GA_ID')}", 'const GA_ID = ""', 1)


def _run(cases: list[dict], script: str = CLARITY_ONLY_JS) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 clarity.js 를 돌리지 못한다")
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps([script, cases]),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _one(**case: object) -> dict:
    return _run([case])[0]


# ID 가 비면 clarity.js 는 아무것도 하지 않는다 — 아래 동작 테스트는 ID 가 있을 때만
with_id = pytest.mark.skipif(not _clarity_id(), reason="ID 가 비어 있다")


@with_id
def test_other_hosts_pages_do_nothing_at_all() -> None:
    """www·localhost·처리방침·검색어 페이지 경로는 띠·대기열·쿠키 정리·듣기 모두 없다 (§3.2) — 그 페이지들은 파일을 받기만 한다
    (2026-10-17, 서버 기록의 '확인' 신호 — 동의·GPC·저장값과 상관없이)."""
    for case in (
        {"host": "www.kimptrack.com"},
        {"host": "localhost"},
        {"path": "/privacy"},
        {"path": "/kimp-chart"},
        {"path": "/kimp-history"},
        {"path": "/404.html"},
        {"path": "/privacy", "ls": ON},
        {
            "path": "/kimp-chart",
            "ls": {"kt.analytics": "granted", "kt.analytics.v": OLD},
        },
    ):
        got = _one(**case, steps=["ready"])["last"]
        assert (
            got["strip"],
            got["queue"],
            got["tags"],
            got["cookies"],
            got["listens"],
        ) == (None, None, [], [], False), case


@with_id
def test_admin_overlay_frame_gets_neither_clarity_nor_the_modal() -> None:
    """053 §3.4 — `kt-overlay=1` 이고 틀 안이면(attention.js 덮어 보기와 같은 조건) 동의 창·대기열·태그·정리·듣기 모두
    없다(동의한 방문자도). 둘 중 하나만이면 평소대로 동의 창이 뜬다."""
    for ls in ({}, ON):
        for path in ("/", "/app/"):
            got = _one(
                ls=ls, path=path, search="?kt-overlay=1", framed=True, steps=["ready"]
            )["last"]
            assert (
                got["strip"],
                got["queue"],
                got["tags"],
                got["cookies"],
                got["listens"],
                got["inert"],
            ) == (None, None, [], [], False, [False, False]), (ls, path)
    for case in ({"search": "?kt-overlay=1"}, {"framed": True}):
        assert _one(**case, steps=["ready"])["last"]["strip"], case


@with_id
def test_undecided_visitor_gets_the_modal_after_the_document_is_parsed() -> None:
    """없음·그 밖의 값·예전 판·판 없는 granted = 정하지 않음 — 남은 저장값을 지우고 DOMContentLoaded 뒤 동의 창 (§3.2·§3.3).
    079 §3.1 — 판 없는 예전 denied(예전 동의 창의 [모두 거부])·지난 later·시각이 없거나 숫자가 아니거나 24시간보다 먼 later 도."""
    for ls in (
        {},
        {"kt.analytics": "off"},
        {**ON, "kt.analytics.v": OLD},
        {"kt.analytics": "granted"},
        {"kt.analytics": "denied"},
        {"kt.analytics": "later", "kt.analytics.until": "1"},
        {"kt.analytics": "later", "kt.analytics.until": _ms(-1000)},
        {"kt.analytics": "later"},
        {"kt.analytics": "later", "kt.analytics.until": "soon"},
        {"kt.analytics": "later", "kt.analytics.until": _ms(2 * DAY_MS)},
    ):
        got = _one(ls=ls, steps=["ready"])
        assert got["first"]["strip"] is None and got["first"]["forgot"], ls
        strip = got["last"]["strip"]
        assert (
            strip
            and strip["first"]
            and strip["role"] == "dialog"
            and strip["aria-modal"] == "true"
            and strip["aria-label"] == "화면 분석 동의"
        ), ls
        # 모달 — 뒤 페이지의 형제 요소는 모두 inert, 문서 스크롤은 멈추고, 초점은 카드로 한 번
        assert got["first"]["inert"] == [False, False], ls
        assert (
            got["last"]["inert"],
            got["last"]["html"]["overflow"],
            got["last"]["focused"],
        ) == (
            [True, True],
            "hidden",
            1,
        ), ls
        assert (
            "data-nosnippet" in strip
            and got["last"]["queue"] is None
            and got["last"]["tags"] == []
        ), ls
        # 랜딩은 같은 탭, 대시보드는 띠 안 링크 모두 새 탭
        assert set(strip["links"]) == {"|"}, ls
    links = _one(path="/app/", steps=["ready"])["last"]["strip"]["links"]
    # 075 — 국외 이전 칸의 Google 개인정보처리방침 링크가 하나 늘었다
    assert len(links) == 7 and set(links) == {"_blank|noopener"}
    # 스크롤 막대가 있던 문서는 그 자리를 남긴다(막대가 사라져 페이지가 옆으로 밀리지 않게), 없던 문서는 건드리지 않는다
    assert (
        _one(bar=True, steps=["ready"])["last"]["html"]["scrollbarGutter"] == "stable"
    )
    assert _one(steps=["ready"])["last"]["html"]["scrollbarGutter"] == ""


@with_id
def test_refused_blocked_and_unreadable_visitors_get_nothing() -> None:
    """판 있는 denied(처리방침의 [모두 거부]·[동의 철회])·아직 안 지난 later(079)·GPC(granted 여도)·저장소 예외 — 띠도 대기열도
    없고 남은 쿠키·_cltk 를 지운다 (§3.2)."""
    for case in (
        {"ls": {"kt.analytics": "denied", "kt.analytics.v": OLD}},
        {"ls": {"kt.analytics": "denied", "kt.analytics.v": NOTICE}},
        {"ls": {"kt.analytics": "later", "kt.analytics.until": _ms(3_600_000)}},
        {"gpc": True},
        {"gpc": True, "ls": ON},
        {"fail": ["get"]},
    ):
        got = _one(**case, steps=["ready"])["last"]
        assert (got["strip"], got["queue"], got["tags"]) == (None, None, []), case
        assert got["forgot"], case
        lines = [ln for ln in got["cookies"] if ln.startswith(("_clck=;", "_clsk=;"))]
        assert len(lines) == 4 and all(
            "Max-Age=0" in ln and "path=/" in ln for ln in lines
        )
        assert sum(ln.endswith("; domain=kimptrack.com") for ln in lines) == 2


@with_id
def test_agreed_visitor_queues_consent_first_and_gets_the_tag_after_parsing() -> None:
    """대기열은 곧바로(첫 항목 consentv2), 태그는 DOMContentLoaded 뒤 — defer 로 실려 readyState 가 interactive 여도 (§3.4)."""
    for defer in (False, True):
        got = _one(ls=ON, path="/app/", defer=defer, steps=["ready"])
        assert got["first"]["queue"] == [CONSENT] and got["first"]["tags"] == [], defer
        last = got["last"]
        assert (last["tags"], last["strip"], last["fired"]) == (TAG_ID, None, []), defer
        assert not last["forgot"], defer
    # 정하지 않은 방문자의 띠도 DOMContentLoaded 뒤
    got = _one(defer=True, steps=["ready"])
    assert got["first"]["strip"] is None and got["last"]["strip"] is not None


@with_id
def test_strip_buttons_write_the_choice() -> None:
    """079 §3.2 — [모두 동의] → 세 칸 체크·판·granted·곧바로 태그와 kt:clarity, [선택한 대로 저장] 셋 모두 → 같음, 하나라도
    빠지면·[하루 동안 안 보기] → later·다시 물을 시각 = 지금 + 24시간·판 없음, 저장 실패면 창만 닫음. 버튼은 셋, [모두 동의]가 먼저."""
    before = int(time.time() * 1000)
    agree_all, agree, partial, later, broken = _run(
        [
            {"steps": ["ready", "click:kt-c-all"]},
            {"steps": ["ready", *ALL, "click:kt-c-save"]},
            {
                "ls": {"kt.analytics.v": OLD},
                "steps": ["ready", *ALL[:2], "click:kt-c-save"],
            },
            {
                "ls": {"kt.analytics.v": OLD},
                "steps": ["ready", *ALL, "click:kt-c-later"],
            },
            {"fail": ["set"], "steps": ["ready", *ALL, "click:kt-c-save"]},
        ]
    )
    after = int(time.time() * 1000)
    for got in (agree_all, agree):
        last = got["last"]
        assert (
            last["stored"],
            last["v"],
            last["until"],
            last["strip"],
            last["style"],
        ) == (
            "granted",
            NOTICE,
            None,
            None,
            0,
        )
        assert (last["queue"], last["tags"], last["fired"]) == (
            [CONSENT],
            TAG_ID,
            ["kt:clarity"],
        )
    # 닫으면 뒤 페이지가 돌아온다 — 어느 버튼이든, 저장에 실패해도
    for got in (agree_all, agree, partial, later, broken):
        assert got["last"]["inert"] == [False, False]
        assert got["last"]["html"] == {"overflow": "", "scrollbarGutter": ""}
    for got in (partial, later):
        last = got["last"]
        assert (
            last["stored"],
            last["v"],
            last["strip"],
            last["queue"],
            last["tags"],
        ) == (
            "later",
            None,
            None,
            None,
            [],
        )
        assert before + DAY_MS <= int(last["until"]) <= after + DAY_MS
    last = broken["last"]
    assert (last["stored"], last["strip"], last["queue"], last["tags"]) == (
        None,
        None,
        None,
        [],
    )
    # 버튼 줄 — [모두 동의](채운 강조)·[선택한 대로 저장]·[하루 동안 안 보기], [모두 거부]는 없다
    html = JS.split("const HTML = `", 1)[1].split("`", 1)[0]
    actions = re.search(r'<div class="kt-c-actions">(.*?)</div>', html, flags=re.S)
    assert actions and re.findall(
        r'id="(kt-c-\w+)"[^>]*>([^<]+)<', actions.group(1)
    ) == [
        ("kt-c-all", "모두 동의"),
        ("kt-c-save", "선택한 대로 저장"),
        ("kt-c-later", "하루 동안 안 보기"),
    ]
    assert (
        'class="kt-c-primary"' in actions.group(1)
        and "모두 거부" not in JS.split("const CSS")[0].split("const HTML")[1]
    )


@with_id
def test_other_tabs_reload_on_withdrawal_and_load_on_consent() -> None:
    """부른 문서는 켜는 값이 아니게 되면 곧바로 한 번 새로고침, 띠 문서는 동의면 그 자리에서 부르고 거부면 띠만 지운다 (§3.5)."""
    withdrawn, cleared, consent, refused, gpc, other_key = _run(
        [
            {
                "ls": ON,
                "steps": [
                    "ready",
                    "other:kt.analytics=denied",
                    "other:kt.analytics.v=",
                ],
            },
            {"ls": ON, "steps": ["ready", "clear"]},
            {
                "steps": [
                    "ready",
                    f"other:kt.analytics.v={NOTICE}",
                    "other:kt.analytics=granted",
                ]
            },
            {
                "steps": [
                    "ready",
                    f"other:kt.analytics.v={NOTICE}",
                    "other:kt.analytics=denied",
                ]
            },
            {
                "gpc": True,
                "steps": [
                    "ready",
                    f"other:kt.analytics.v={NOTICE}",
                    "other:kt.analytics=granted",
                ],
            },
            {"steps": ["ready", "other:_cltk=1"]},
        ]
    )
    assert withdrawn["last"]["reloads"] == 1 and cleared["last"]["reloads"] == 1
    last = consent["last"]
    assert (last["strip"], last["queue"], last["tags"], last["fired"]) == (
        None,
        [CONSENT],
        TAG_ID,
        ["kt:clarity"],
    )
    assert (refused["last"]["strip"], refused["last"]["queue"]) == (None, None)
    assert (gpc["last"]["queue"], gpc["last"]["reloads"]) == (None, 0)
    assert other_key["last"]["strip"] is not None and other_key["last"]["queue"] is None
    # 079 — 다른 탭에서 [하루 동안 안 보기] → 창만 지운다. 판 없는 denied(예전 값)는 창을 그대로 둔다
    later, legacy = _run(
        [
            {
                "steps": [
                    "ready",
                    f"other:kt.analytics.until={_ms(DAY_MS)}",
                    "other:kt.analytics=later",
                ]
            },
            {"steps": ["ready", "other:kt.analytics=denied"]},
        ]
    )
    assert (later["last"]["strip"], later["last"]["queue"]) == (None, None)
    assert legacy["last"]["strip"] is not None


@with_id
def test_back_forward_restore_rechecks_the_choice() -> None:
    """뒤로 가기 캐시에서 돌아온 문서는 storage 이벤트 없이도 다시 본다 (§3.5).

    부른 문서는 켜는 값이 아니면 Clarity 의 재시작(같은 pageshow 의 non-capture 처리기)을 막고 한 번 새로고침한다 — 같은 탭에서
    랜딩 → 방침 [동의 철회] → 뒤로 가기. 띠 문서는 동의면 띠를 지우고 부르고, 고른 값이면 띠만 지운다.
    """
    agree = [f"quiet:kt.analytics.v={NOTICE}", "quiet:kt.analytics=granted", "back"]
    withdrawn, late, kept, first_show, consent, refused, unchanged, gpc = _run(
        [
            {
                "ls": ON,
                "steps": [
                    "ready",
                    "quiet:kt.analytics=denied",
                    "quiet:kt.analytics.v=",
                    "back",
                ],
            },
            # 크롬처럼 storage 이벤트가 복원 뒤에 늦게 와도 새로고침은 한 번
            {
                "ls": ON,
                "steps": [
                    "ready",
                    "quiet:kt.analytics=denied",
                    "back",
                    "other:kt.analytics=denied",
                ],
            },
            {"ls": ON, "steps": ["ready", "back"]},
            {"ls": ON, "steps": ["ready", "quiet:kt.analytics=denied", "show"]},
            {"steps": ["ready", *agree]},
            {
                "steps": [
                    "ready",
                    f"quiet:kt.analytics.v={NOTICE}",
                    "quiet:kt.analytics=denied",
                    "back",
                ]
            },
            {"steps": ["ready", "back"]},
            {"gpc": True, "steps": ["ready", *agree]},
        ]
    )
    # capture 단계 — 나중에 붙는 Clarity 의 pageshow 처리기(non-capture)보다 먼저 돈다
    assert withdrawn["last"]["capture"]["pageshow"] is True
    for got in (withdrawn, late):
        assert (got["last"]["reloads"], got["last"]["stopped"]) == (1, 1)
    for got in (kept, first_show):
        assert (got["last"]["reloads"], got["last"]["stopped"]) == (0, 0)
    last = consent["last"]
    assert (last["strip"], last["style"], last["queue"], last["tags"]) == (
        None,
        0,
        [CONSENT],
        TAG_ID,
    )
    assert last["fired"] == ["kt:clarity"]
    assert (refused["last"]["strip"], refused["last"]["queue"]) == (None, None)
    assert unchanged["last"]["strip"] is not None
    assert (gpc["last"]["queue"], gpc["last"]["tags"]) == (None, [])


# --- 075 — GA4 를 같은 동의로 (§3.2·§3.3) ----------------------------------------------
# 동작은 시험 ID 를 넣은 사본으로 본다(레포의 ID 와 상관없이). Clarity ID 만 비운 사본은 GA 만 켠 경우
TEST_GA = "G-TEST123"
GA_JS = CLARITY_ONLY_JS.replace('const GA_ID = ""', f'const GA_ID = "{TEST_GA}"', 1)
GA_ONLY_JS = GA_JS.replace(
    f"const CLARITY_ID = {_const('CLARITY_ID')}", 'const CLARITY_ID = ""', 1
)
NONE_JS = CLARITY_ONLY_JS.replace(
    f"const CLARITY_ID = {_const('CLARITY_ID')}", 'const CLARITY_ID = ""', 1
)
GA_CONSENT = [
    "consent",
    "default",
    {
        "ad_storage": "denied",
        "ad_user_data": "denied",
        "ad_personalization": "denied",
        "analytics_storage": "granted",
    },
]
GA_CONFIG = {
    "allow_google_signals": False,
    "allow_ad_personalization_signals": False,
    "cookie_expires": 60 * 24 * 60 * 60,
}
GA_TAG_ID = [GA_TAG + TEST_GA, True]


def test_the_repo_file_loads_its_ga_id_for_agreed_visitors() -> None:
    """075 §3.1 — 레포의 측정 ID 가 들어 있다(켜짐). 실제 파일 그대로 — 동의한 방문자는 Clarity 태그 뒤에 그 ID 의 GA 태그를 받고,
    정하지 않은 방문자는 Google 요청이 없다."""
    assert _ga_id(), (
        "GA 측정 ID 가 비었다 — 끄는 PR 이면 이 테스트를 '빈 ID' 단언으로 바꾼다"
    )
    assert len({JS, CLARITY_ONLY_JS, GA_JS, GA_ONLY_JS, NONE_JS}) == 5
    agreed, undecided = _run([{"ls": ON, "steps": ["ready"]}, {"steps": ["ready"]}], JS)
    assert agreed["last"]["tags"] == [*TAG_ID, [GA_TAG + _ga_id(), True]]
    assert agreed["last"]["layer"][2][:2] == ["config", _ga_id()]
    assert undecided["last"]["layer"] is None and undecided["last"]["tags"] == []


def test_ga_waits_for_consent_and_sends_no_request_before() -> None:
    """정하지 않음·거부·GPC·저장소 예외·www·덮어 보기 틀 — GA 대기열도 태그도 없다(동의 전 Google 요청 0)."""
    cases = [
        {"steps": ["ready"]},
        {"ls": {"kt.analytics": "denied"}, "steps": ["ready"]},
        {"gpc": True, "ls": ON, "steps": ["ready"]},
        {"fail": ["get"], "steps": ["ready"]},
        {"ls": {**ON, "kt.analytics.v": OLD}, "steps": ["ready"]},
        {"host": "www.kimptrack.com", "ls": ON, "steps": ["ready"]},
        {"ls": ON, "search": "?kt-overlay=1", "framed": True, "steps": ["ready"]},
        {"ls": ON, "search": "?kt-replay=1", "framed": True, "steps": ["ready"]},
    ]
    for case, got in zip(cases, _run(cases, GA_JS), strict=True):
        last = got["last"]
        assert last["layer"] is None, case
        assert not [t for t in last["tags"] if t[0].startswith(GA_TAG)], case


def test_agreed_visitor_gets_ga_consent_first_then_config_and_tag_after_parsing() -> (
    None
):
    """동의 신호(광고 셋 거부·분석 허용)가 곧바로 첫 항목, config·태그는 DOMContentLoaded 뒤 — 주소는 검색어·# 를 뺀 주소, Google
    신호·광고 개인화 신호 끔, 쿠키 2개월. Clarity 대기열·태그도 그대로 (§3.2)."""
    for defer in (False, True):
        got = _run(
            [
                {
                    "ls": ON,
                    "path": "/app/",
                    "search": "?tab=history&sym=BTC",
                    "hash": "#x",
                    "defer": defer,
                    "steps": ["ready"],
                }
            ],
            GA_JS,
        )[0]
        assert got["first"]["layer"] == [GA_CONSENT] and got["first"]["tags"] == [], (
            defer
        )
        last = got["last"]
        assert last["layer"][:2] == [GA_CONSENT, ["js", "date"]], defer
        name, ga_id, config = last["layer"][2]
        assert (name, ga_id) == ("config", TEST_GA), defer
        assert config == {**GA_CONFIG, "page_location": "https://kimptrack.com/app/"}, (
            defer
        )
        assert len(last["layer"]) == 3, defer
        assert last["tags"] == [*TAG_ID, GA_TAG_ID], defer
        assert last["queue"] == [CONSENT] and last["fired"] == [], defer


def test_ga_only_shows_the_modal_and_loads_ga_alone() -> None:
    """Clarity ID 만 비면 동의 창은 뜨고 동의하면 GA 만 돈다 — Clarity 대기열·태그·kt:clarity 없음. 둘 다 비면 아무것도 없다 (§3.8)."""
    undecided, agree, saved = _run(
        [
            {"steps": ["ready"]},
            {"ls": ON, "steps": ["ready"]},
            {"steps": ["ready", *ALL, "click:kt-c-save"]},
        ],
        GA_ONLY_JS,
    )
    assert undecided["last"]["strip"] is not None and undecided["last"]["layer"] is None
    for got in (agree, saved):
        last = got["last"]
        assert (last["queue"], last["tags"], last["fired"]) == (None, [GA_TAG_ID], [])
        assert last["layer"][0] == GA_CONSENT
    (nothing,) = _run([{"ls": ON, "steps": ["ready"]}], NONE_JS)
    last = nothing["last"]
    assert (
        last["strip"],
        last["queue"],
        last["layer"],
        last["tags"],
        last["listens"],
    ) == (
        None,
        None,
        None,
        [],
        False,
    )


def test_saving_consent_loads_ga_at_once_and_other_tabs_follow() -> None:
    """동의 창의 [선택한 대로 저장]·다른 탭의 동의 — 그 자리에서 GA 대기열·태그 (§3.2·033 §3.5)."""
    saved, other = _run(
        [
            {"steps": ["ready", *ALL, "click:kt-c-save"]},
            {
                "steps": [
                    "ready",
                    f"other:kt.analytics.v={NOTICE}",
                    "other:kt.analytics=granted",
                ]
            },
        ],
        GA_JS,
    )
    for got in (saved, other):
        last = got["last"]
        assert last["layer"][0] == GA_CONSENT and last["layer"][2][0] == "config"
        assert last["tags"] == [*TAG_ID, GA_TAG_ID] and last["fired"] == ["kt:clarity"]


def test_documents_that_do_not_load_forget_ga_cookies() -> None:
    """부르지 않는 문서는 남은 GA 쿠키 _ga 와 _ga_<기호>(이름을 읽어 찾는다)를 두 꼴(도메인 없이·kimptrack.com)로 지운다 (§3.3)."""
    for js in (JS, GA_JS):
        got = _run(
            [
                {
                    "ls": {"kt.analytics": "denied"},
                    "jar": ["_ga", "_ga_ABC123", "_gid", "x"],
                    "steps": ["ready"],
                }
            ],
            js,
        )[0]["last"]
        for name in ("_ga", "_ga_ABC123"):
            lines = [ln for ln in got["cookies"] if ln.startswith(f"{name}=;")]
            assert len(lines) == 2 and all(
                "Max-Age=0" in ln and "path=/" in ln for ln in lines
            ), name
            assert sum(ln.endswith("; domain=kimptrack.com") for ln in lines) == 1, name
        assert not [ln for ln in got["cookies"] if ln.startswith(("_gid=", "x="))]


def test_ga_stays_off_on_addresses_with_search_like_query_keys() -> None:
    """075 §3.2 — GA 사이트 검색이 검색어로 읽는 쿼리 키(q·s·search·query·keyword)가 주소에 있으면 동의했어도 그 문서에서는 GA 를
    부르지 않는다(동의 창 저장으로 켤 때도). Clarity 는 그대로 돈다. 이름이 비슷한 이 사이트의 키(s.io·tab·sym·utm_*)는 막지 않는다."""
    blocked = [
        "?q=btc",
        "?s=1",
        "?search=x",
        "?query=x",
        "?keyword=x",
        "?tab=history&q=eth",
    ]
    allowed = ["?s.io=1&tab=history", "?tab=history&sym=BTC", "?utm_source=x", ""]
    cases = [{"ls": ON, "search": q, "steps": ["ready"]} for q in blocked + allowed]
    cases.append({"search": "?q=btc", "steps": ["ready", *ALL, "click:kt-c-save"]})
    got = _run(cases, GA_JS)
    for q, result in zip(
        blocked + ["?q=btc (저장)"], got[: len(blocked)] + got[-1:], strict=True
    ):
        last = result["last"]
        assert last["layer"] is None, q
        assert last["tags"] == TAG_ID and last["queue"] == [CONSENT], q
    for q, result in zip(allowed, got[len(blocked) : -1], strict=True):
        last = result["last"]
        assert last["tags"] == [*TAG_ID, GA_TAG_ID], q
        assert last["layer"][0] == GA_CONSENT, q


# --- 033 §3.2 TRACK_ALL — 레포 파일 그대로(사람 결정 2026-10-18: 동의 창 없이 모든 방문자에게 Clarity·GA) ---------------------------

EVERYONE = (
    {},  # 정하지 않음
    {"ls": {"kt.analytics": "denied", "kt.analytics.v": NOTICE}},  # 판 있는 거부
    {"ls": {"kt.analytics": "later", "kt.analytics.until": _ms(3_600_000)}},
    {"ls": {"kt.analytics": "granted", "kt.analytics.v": OLD}},  # 예전 판 동의
    {"gpc": True},
    {"fail": ["get"]},
    {"ls": ON},
)


@with_id
def test_track_all_loads_clarity_and_ga_for_every_visitor_without_the_modal() -> None:
    """동의 상태와 관계없이 랜딩·대시보드에서 동의 창 없이 대기열 첫 항목에 동의 신호(광고 거부·분석 허용), DOMContentLoaded 뒤 Clarity·GA
    태그를 넣는다. 남은 쿠키를 지우지 않고 다른 탭·뒤로 가기를 듣지 않는다(철회 버튼이 없다)."""
    cases = [{**c, "path": p} for c in EVERYONE for p in ("/", "/app/")]
    got = _run([{**c, "steps": ["ready"]} for c in cases], REPO_JS)
    for case, result in zip(cases, got, strict=True):
        first, last = result["first"], result["last"]
        assert first["queue"] == [CONSENT] and first["tags"] == [], case
        assert last["strip"] is None and last["inert"] == [False, False], case
        assert last["tags"] == [*TAG_ID, [GA_TAG + _ga_id(), True]], case
        assert last["layer"][0] == GA_CONSENT, case
        assert last["layer"][2][:2] == ["config", _ga_id()], case
        assert not last["forgot"] and last["listens"] is False, case


@with_id
def test_track_all_still_skips_other_pages_and_admin_frames() -> None:
    """모두에게 켜도 www·처리방침·검색어 페이지와 관리자 덮어 보기·재생 틀에서는 아무것도 하지 않는다."""
    cases = [
        {"host": "www.kimptrack.com"},
        {"path": "/privacy"},
        {"path": "/kimp-chart"},
        {"search": "?kt-overlay=1", "framed": True},
        {"path": "/app/", "search": "?kt-replay=1", "framed": True},
    ]
    got = _run([{**c, "steps": ["ready"]} for c in cases], REPO_JS)
    for case, result in zip(cases, got, strict=True):
        last = result["last"]
        assert (last["strip"], last["queue"], last["tags"], last["listens"]) == (
            None,
            None,
            [],
            False,
        ), case


@with_id
def test_track_all_keeps_ga_off_on_search_like_addresses() -> None:
    """075 §3.2 그대로 — 검색어 쿼리 키가 있는 주소에서는 GA 를 부르지 않고 Clarity 만."""
    (got,) = _run([{"search": "?q=btc", "steps": ["ready"]}], REPO_JS)
    last = got["last"]
    assert (
        last["layer"] is None and last["tags"] == TAG_ID and last["queue"] == [CONSENT]
    )
