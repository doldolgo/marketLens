"""화면 분석(Clarity) 계약 — clarity.js·두 HTML·셸·nginx 를 파일로 읽어 단언하고, clarity.js 를 node 로 돌린다 (스펙 033 §4).

동의 방식이다: 세 칸에 모두 동의해 지금 판으로 저장한 방문자만 태그를 받는다(032 와 같은 값 계약). 띠 글자가 032 방침 동의
상자와 어긋나거나, 처리방침 게시 전에 ID 가 들어가거나, 띠가 타이머·외부 자원을 만들게 되면 여기서 멈춘다. 실제 브라우저의
띠·요청은 스펙 §5 의 명령이다.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

from tests.test_deploy import ROOT, _args, _locations, _public_server
from tests.test_privacy import (
    CONSENT_CELLS,
    NOTICE,
    OLD,
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
    "location.reload", "dialog", "aria-modal", "inert", "aria-label", "checkbox", "label", "/privacy#consent", "_blank",
    "noopener", "kt:clarity", "선택한 대로 저장", "모두 거부", "<details>", "<summary", "내용 보기",
    "저장 규칙", "data-nosnippet", "pageshow", "persisted", "stopImmediatePropagation",
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
    """방침·404·관리자 파일 전부는 clarity.js 를 싣지 않고(src) Clarity 주소도 없다 (§4).

    파일 이름 글자 자체는 막지 않는다 — 방침 동의 스크립트 주석이 판 계약으로 `clarity.js` 를 말한다.
    """
    admin = sorted(p for p in (WEB / "admin").rglob("*") if p.is_file())
    assert {p.name for p in admin} >= {"index.html", "admin.js", "admin.css"}
    for path in (WEB / "public/privacy.html", WEB / "public/404.html", *admin):
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
  const location = { hostname: c.host || 'kimptrack.com', pathname: c.path || '/', reload: () => reloads++ }
  class Event { constructor(type) { this.type = type } }
  new Function('window', 'document', 'navigator', 'location', 'Event', script)(
    window, document, c.gpc ? { globalPrivacyControl: true } : {}, location, Event)
  // 동의 창 뒤의 페이지 — 창보다 먼저 body 에 있던 형제 둘(랜딩의 header·main, 대시보드의 #root 자리)
  const page = [el('header'), el('main')]
  body.push(...page)
  const strip = () => body.find((e) => e.id === 'kt-consent')
  const snap = () => ({
    stored: data.get('kt.analytics') ?? null, v: data.get('kt.analytics.v') ?? null,
    strip: strip() ? { first: body[0] === strip(), ...strip().attrs, links: strip().anchors.map((a) => a.target + '|' + a.rel) } : null,
    queue: window.clarity ? window.clarity.q.map((a) => Array.from(a)) : null,
    tags: head.filter((e) => e.tag === 'script').map((e) => [e.src, e.async]),
    style: head.filter((e) => e.tag === 'style').length, fired, reloads, stopped, capture,
    inert: page.map((e) => !!e.inert), html: { ...document.documentElement.style },
    focused: strip() ? strip().querySelector('.kt-c-body').focused : 0,
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
TAG_ID = [[TAG + _clarity_id(), True]]


def _run(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 clarity.js 를 돌리지 못한다")
    done = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps([JS, cases]),
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
    """www·localhost·처리방침 경로는 띠·대기열·쿠키 정리·듣기 모두 없다 (§3.2)."""
    for case in (
        {"host": "www.kimptrack.com"},
        {"host": "localhost"},
        {"path": "/privacy"},
        {"path": "/404.html"},
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
def test_undecided_visitor_gets_the_modal_after_the_document_is_parsed() -> None:
    """없음·그 밖의 값·예전 판·판 없는 granted = 정하지 않음 — 남은 저장값을 지우고 DOMContentLoaded 뒤 동의 창 (§3.2·§3.3)."""
    for ls in (
        {},
        {"kt.analytics": "off"},
        {**ON, "kt.analytics.v": OLD},
        {"kt.analytics": "granted"},
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
        # 모달 — 뒤 페이지의 형제 요소는 모두 inert, 문서 스크롤은 멈추고, 초점은 창 안 글 영역으로 한 번
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
    assert len(links) == 6 and set(links) == {"_blank|noopener"}
    # 스크롤 막대가 있던 문서는 그 자리를 남긴다(막대가 사라져 페이지가 옆으로 밀리지 않게), 없던 문서는 건드리지 않는다
    assert (
        _one(bar=True, steps=["ready"])["last"]["html"]["scrollbarGutter"] == "stable"
    )
    assert _one(steps=["ready"])["last"]["html"]["scrollbarGutter"] == ""


@with_id
def test_refused_blocked_and_unreadable_visitors_get_nothing() -> None:
    """denied·GPC(granted 여도)·저장소 예외 — 띠도 대기열도 없고 남은 쿠키·_cltk 를 지운다 (§3.2)."""
    for case in (
        {"ls": {"kt.analytics": "denied"}},
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
    """[선택한 대로 저장] 셋 모두 → 판·granted·곧바로 태그와 kt:clarity, 하나라도 빠지면·[모두 거부] → denied (§3.3)."""
    agree, partial, deny, broken = _run(
        [
            {"steps": ["ready", *ALL, "click:kt-c-save"]},
            {"steps": ["ready", *ALL[:2], "click:kt-c-save"]},
            {"ls": {"kt.analytics.v": OLD}, "steps": ["ready", "click:kt-c-deny"]},
            {"fail": ["set"], "steps": ["ready", *ALL, "click:kt-c-save"]},
        ]
    )
    last = agree["last"]
    assert (last["stored"], last["v"], last["strip"], last["style"]) == (
        "granted",
        NOTICE,
        None,
        0,
    )
    # 닫으면 뒤 페이지가 돌아온다 — 어느 버튼이든, 저장에 실패해도
    for got in (agree, partial, deny, broken):
        assert got["last"]["inert"] == [False, False]
        assert got["last"]["html"] == {"overflow": "", "scrollbarGutter": ""}
    assert (last["queue"], last["tags"], last["fired"]) == (
        [CONSENT],
        TAG_ID,
        ["kt:clarity"],
    )
    for got in (partial, deny):
        last = got["last"]
        assert (
            last["stored"],
            last["v"],
            last["strip"],
            last["queue"],
            last["tags"],
        ) == ("denied", None, None, None, [])
    last = broken["last"]
    assert (last["stored"], last["strip"], last["queue"], last["tags"]) == (
        None,
        None,
        None,
        [],
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
            {"steps": ["ready", "other:kt.analytics=denied"]},
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
            {"steps": ["ready", "quiet:kt.analytics=denied", "back"]},
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
