// KimpTrack 화면 분석(Microsoft Clarity) — 설정 한 곳·동의 창(모달)·불러올지 판단 (스펙 033).
// 동의 방식(032 §3.3 과 같은 값 계약): 수집·이용·Microsoft 제공·미국 이전 세 칸에 모두 동의해 저장한 방문자만 태그를 받는다.
// 번들 밖 정적 파일이라 oxlint 대상이 아니다(node --check). 예외를 밖으로 던지지 않는다 — 실패하면 동의 창·Clarity 만 없고 페이지는 그대로다.
;(() => {
  // ── 설정 한 곳 (§3.1) ──────────────────────────────────────────────────────────────
  // 프로젝트 ID(공개 값 — 페이지 소스에 그대로 보인다). 빈 문자열이면 아무것도 하지 않는다 — 끄기는 이 값을 비우는 PR
  const CLARITY_ID = "yqqzmx15ps"
  // 켤 페이지 — 랜딩(/)과 대시보드(/app/). 대시보드만 끄려면 "app" 을 뺀다
  const PAGES = ["landing", "app"]
  // 지금 안내 판 — privacy.html 동의 스크립트의 VERSION 과 같다(테스트가 본다). 받는 자·항목·목적·보유 기간이 바뀌면 그 PR 이
  // 세 곳(이 값·privacy.html 스크립트·방침의 보이는 판 글자)을 함께 올린다 — 예전 판의 granted 는 '정하지 않음' 이라 동의 창이 다시 묻는다
  const NOTICE_VERSION = "2026-10-01"

  const HOST = "kimptrack.com" // www 는 apex 로 301 이고 저장값이 출처마다 따로라 이 호스트 하나만
  const KEY = "kt.analytics"
  const VKEY = "kt.analytics.v"
  const BOXES = ["kt-c-collect", "kt-c-provide", "kt-c-transfer"]

  // 동의 창 글자 — 032 방침 동의 상자와 같은 글자다(server/tests/test_clarity.py 가 privacy.html 에서 읽어 맞춘다).
  // 처음엔 접힌 짧은 창(§3.3): 머리 문장(나이 문장을 붙인다)·세 칸(체크 상자·칸 제목·'내용 보기')·방침 링크와 '저장 규칙'·버튼 줄.
  // 알릴 사항 전문은 칸마다, 셋 모두 규칙은 '저장 규칙' 의 <details> 로 펼친다 — 모바일에서도 접힌 창이 한 화면에 들게
  const HTML = `<div class="kt-c-body" tabindex="0">
<p>랜딩과 대시보드의 화면 이용 기록(클릭·스크롤 등)을 Microsoft Clarity(미국)로 보내 서비스 개선에 써도 될까요? Microsoft는 이 기록을 광고 등 자기 목적에도 씁니다. 동의하지 않아도 모든 기능을 그대로 씁니다. 만 14세 미만은 동의하지 마세요.</p>
<div class="kt-c-cell">
<label><input type="checkbox" id="kt-c-collect" /><span>(선택) 화면 이용 기록의 수집·이용에 동의합니다</span></label>
<details><summary aria-label="수집·이용 동의 내용 보기">내용 보기</summary><dl>
<dt>항목</dt><dd>페이지 주소(검색어는 뺌)·이전 페이지 주소·누른 링크의 주소, 클릭·스크롤·마우스 움직임·화면 크기, 화면 내용(입력칸은 늘 가림), 보고 있는 탭과 기록 탭에서 고른 코인 이름·탭을 바꾼 일, 기기·브라우저·운영체제, IP 주소(대략의 위치 추정에 쓰임), Clarity가 브라우저와 방문을 알아보는 쿠키 <code>_clck</code>·<code>_clsk</code>와 탭 ID(sessionStorage <code>_cltk</code>)</dd>
<dt>목적</dt><dd>KimpTrack의 화면 이용 분석 — 어떤 화면·기능이 쓰이고 어디서 막히는지 알아 서비스를 고치기</dd>
<dt>보유 기간</dt><dd><strong class="key">녹화 30일, 히트맵·즐겨찾기·표본 녹화·라벨 최대 9개월</strong></dd>
<dt>거부 권리·불이익</dt><dd>동의하지 않을 수 있고, 동의한 뒤에도 처리방침의 “화면 분석 동의 관리”(<a class="link" href="/privacy#consent">/privacy#consent</a>)에서 [동의 철회]를 눌러 언제든 철회할 수 있습니다. 거부·철회해도 서비스 이용에 불이익이 없습니다.</dd>
</dl></details>
</div>
<div class="kt-c-cell">
<label><input type="checkbox" id="kt-c-provide" /><span>(선택) 화면 이용 기록을 Microsoft에 제공하는 데 동의합니다</span></label>
<details><summary aria-label="Microsoft 제공 동의 내용 보기">내용 보기</summary><dl>
<dt>받는 자</dt><dd><strong class="key">Microsoft Corporation(미국)</strong></dd>
<dt>받는 자의 목적</dt><dd><strong class="key">Clarity로 KimpTrack에 화면 이용 분석을 제공하고, Microsoft Advertising 제공 등 Microsoft 자신의 목적에 씁니다</strong></dd>
<dt>항목</dt><dd>페이지 주소(검색어는 뺌)·이전 페이지 주소·누른 링크의 주소, 클릭·스크롤·마우스 움직임·화면 크기, 화면 내용(입력칸은 늘 가림), 보고 있는 탭과 기록 탭에서 고른 코인 이름·탭을 바꾼 일, 기기·브라우저·운영체제, IP 주소(대략의 위치 추정에 쓰임), Clarity가 브라우저와 방문을 알아보는 쿠키 <code>_clck</code>·<code>_clsk</code>와 탭 ID(sessionStorage <code>_cltk</code>)</dd>
<dt>받는 자의 보유 기간</dt><dd><strong class="key">Clarity의 기록은 녹화 30일, 히트맵·즐겨찾기·표본 녹화·라벨 최대 9개월. Microsoft 자신의 목적에 쓰는 정보는 <a class="link" href="https://privacy.microsoft.com/ko-kr/privacystatement">Microsoft 개인정보처리방침</a>이 정한 기간</strong></dd>
<dt>거부 권리·불이익</dt><dd>동의하지 않을 수 있고, 동의한 뒤에도 처리방침의 “화면 분석 동의 관리”(<a class="link" href="/privacy#consent">/privacy#consent</a>)에서 [동의 철회]를 눌러 언제든 철회할 수 있습니다. 거부·철회해도 서비스 이용에 불이익이 없습니다.</dd>
</dl></details>
</div>
<div class="kt-c-cell">
<label><input type="checkbox" id="kt-c-transfer" /><span>(선택) 화면 이용 기록을 미국으로 이전하는 데 동의합니다</span></label>
<details><summary aria-label="미국 이전 동의 내용 보기">내용 보기</summary><dl>
<dt>받는 자·연락처</dt><dd><strong class="key">Microsoft Corporation, One Microsoft Way, Redmond, WA 98052, USA — <a class="link" href="https://go.microsoft.com/fwlink/?linkid=2126612">개인정보 문의</a></strong></dd>
<dt>국가·시기·방법</dt><dd>미국 — 동의한 뒤 랜딩·대시보드를 보는 동안 수시로, 방문자의 브라우저가 Microsoft 서버로 네트워크를 통해 전송</dd>
<dt>항목</dt><dd>페이지 주소(검색어는 뺌)·이전 페이지 주소·누른 링크의 주소, 클릭·스크롤·마우스 움직임·화면 크기, 화면 내용(입력칸은 늘 가림), 보고 있는 탭과 기록 탭에서 고른 코인 이름·탭을 바꾼 일, 기기·브라우저·운영체제, IP 주소(대략의 위치 추정에 쓰임), Clarity가 브라우저와 방문을 알아보는 쿠키 <code>_clck</code>·<code>_clsk</code>와 탭 ID(sessionStorage <code>_cltk</code>)</dd>
<dt>목적·보유 기간</dt><dd><strong class="key">KimpTrack의 화면 이용 분석과 Microsoft Advertising 제공 등 Microsoft 자신의 목적 — Clarity의 기록은 녹화 30일, 히트맵·즐겨찾기·표본 녹화·라벨 최대 9개월, Microsoft 자신의 목적에 쓰는 정보는 Microsoft 개인정보처리방침이 정한 기간</strong></dd>
<dt>거부 방법·절차·효과</dt><dd>이 칸에 체크하지 않거나 [모두 거부]를 누르면 이전되지 않습니다. 동의한 뒤에는 처리방침의 “화면 분석 동의 관리”(<a class="link" href="/privacy#consent">/privacy#consent</a>)에서 [동의 철회]를 누르면 그 뒤로 이전되지 않습니다. 거부·철회해도 서비스 이용에 불이익이 없습니다.</dd>
</dl></details>
</div>
<div class="kt-c-cell">
<a class="link" href="/privacy#consent">처리방침에서 자세히 보기</a>
<details><summary>저장 규칙</summary><p>세 가지에 모두 동의하고 [선택한 대로 저장]을 누른 경우에만 분석합니다 — Clarity는 셋이 다 있어야 돌아가므로 하나라도 빠지면 거부로 저장합니다.</p></details>
</div>
</div>
<div class="kt-c-actions"><button type="button" id="kt-c-save">선택한 대로 저장</button><button type="button" id="kt-c-deny">모두 거부</button></div>`

  // 색은 랜딩 토큰 복사(032 동의 상자와 같은 surface·accent), 글꼴은 시스템 글꼴(032 방침과 같은 목록) — 랜딩 서브셋 글꼴에 없는
  // 글자가 섞이지 않고 대시보드의 웹 글꼴을 기다리지 않게. 화면 가운데 모달 — 어두운 배경이 페이지를 덮고 창은 최대 720px.
  // 높이는 화면에서 위아래 16px 뺀 만큼까지 — 넘치면(내용 보기를 펼쳤을 때) 창 안에서 스크롤하고 버튼 줄은 늘 보인다.
  // 배경을 흐리게(blur) 하지 않는다 — 뒤에서 대시보드 표가 매초 다시 그려진다. 애니메이션 없음.
  // 글자는 모두 15px 이상(§3.3) — 방침은 쿠키 이름(code)을 0.88em 으로 줄이지만 동의 창은 줄이지 않는다
  const CSS = `
#kt-consent{position:fixed;z-index:2147483000;inset:0;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(9,10,16,.72);overscroll-behavior:contain;color:#e9e9ed;font:400 15px/1.4 -apple-system,BlinkMacSystemFont,system-ui,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",sans-serif;letter-spacing:normal;text-align:left;word-break:keep-all;overflow-wrap:anywhere;font-variant-numeric:normal;-webkit-font-smoothing:antialiased}
#kt-consent .kt-c-box{display:flex;flex-direction:column;width:100%;max-width:720px;max-height:100%;border-radius:14px;background:#232532;box-shadow:0 0 0 1px #595d6c,0 18px 48px rgba(0,0,0,.55)}
#kt-consent *,#kt-consent *::before,#kt-consent *::after{box-sizing:border-box}
#kt-consent .kt-c-body{min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:14px 20px 2px}
#kt-consent p,#kt-consent dl,#kt-consent dd{margin:0}
#kt-consent p{color:#cfd3e5}
#kt-consent a{color:#f3f5fe;text-decoration:underline;text-decoration-color:#75798c;text-underline-offset:3px}
#kt-consent .kt-c-cell{position:relative;min-height:28px;margin-top:8px;padding-right:80px}
#kt-consent .kt-c-body>p+.kt-c-cell{margin-top:12px}
#kt-consent label{display:flex;align-items:flex-start;gap:10px;min-height:28px;font-weight:700;color:#e9e9ed;cursor:pointer}
#kt-consent input{flex:none;width:20px;height:20px;margin:1px 0 0;accent-color:#9184d9;cursor:pointer}
#kt-consent summary{position:absolute;top:0;right:0;display:block;min-height:28px;padding:0 2px;list-style:none;color:#9184d9;font-weight:600;white-space:nowrap;cursor:pointer}
#kt-consent summary::-webkit-details-marker{display:none}
#kt-consent summary::after{content:" \\25B8"}
#kt-consent details[open] summary::after{content:" \\25BE"}
#kt-consent details>dl,#kt-consent details>p{margin:6px -80px 8px 30px}
#kt-consent dl{display:grid;grid-template-columns:minmax(0,8.5em) minmax(0,1fr);gap:4px 16px;line-height:1.6}
#kt-consent dt{color:#9397ab}
#kt-consent dd{color:#cfd3e5}
#kt-consent code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:1em;color:#f3f5fe}
#kt-consent .key{font-size:1.2em;font-weight:700;color:#e9e9ed;text-decoration:underline;text-decoration-color:#9184d9;text-underline-offset:4px}
#kt-consent .kt-c-actions{flex:none;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;max-width:404px;padding:12px 20px 16px}
#kt-consent button{height:44px;padding:0 8px;border:1px solid #9184d9;border-radius:8px;background:none;color:#9184d9;font:inherit;font-weight:600;line-height:1;white-space:nowrap;cursor:pointer}
#kt-consent button:hover{background:rgba(145,132,217,.12)}
#kt-consent :focus-visible{outline:2px solid #9184d9;outline-offset:2px;border-radius:4px}
#kt-consent .kt-c-body:focus-visible{outline-offset:-2px}
#kt-consent .kt-c-box:focus{outline:none}
@media (max-width:599px){#kt-consent .kt-c-body{padding:12px 14px 2px}#kt-consent .kt-c-actions{max-width:none;padding:8px 14px}#kt-consent details>dl,#kt-consent details>p{margin-left:0}#kt-consent dl{grid-template-columns:minmax(0,1fr);gap:0}#kt-consent dd+dt{margin-top:8px}}
`

  // 하나라도 걸리면 부르지 않는 조건의 앞 셋(§3.2) — ID·호스트·페이지. 여기서 빠진 문서는 듣지도 않는다(storage·pageshow)
  const page = location.pathname === "/" ? "landing" : location.pathname.indexOf("/app/") === 0 ? "app" : ""
  if (!CLARITY_ID || location.hostname !== HOST || PAGES.indexOf(page) < 0) return

  let loaded = false // 이 문서에서 Clarity 를 불렀다 — 두 번 부르지 않는다
  let modal = null
  let blocked = [] // 동의 창이 inert 로 막은 형제 요소 — 닫을 때 되돌린다
  let scrollWas = ["", ""] // 열기 전 <html> 의 overflow·scrollbar-gutter
  let reloading = false

  // 이벤트 처리기도 밖으로 던지지 않는다
  const guard = (fn) => (event) => {
    try {
      fn(event)
    } catch (e) {
      // 동의 창·Clarity 만 없고 페이지는 그대로
    }
  }

  // 지금 값 — "on"(지금 판의 granted) · "denied" · "undecided"(없음·그 밖의 값·판이 다르거나 없는 granted — 032 의 '정하지 않음')
  // · "blocked"(GPC 이거나 저장소를 읽다 예외 — 고른 값을 저장할 수 없다). GPC 는 granted 보다 앞선다
  const judge = () => {
    if (navigator.globalPrivacyControl === true) return "blocked"
    let value
    let version
    try {
      value = window.localStorage.getItem(KEY)
      version = window.localStorage.getItem(VKEY)
    } catch (e) {
      return "blocked"
    }
    if (value === "granted" && version === NOTICE_VERSION) return "on"
    return value === "denied" ? "denied" : "undecided"
  }

  // 부르지 않는 문서는 예전 방문에서 남은 Clarity 저장값을 지운다 — 쿠키는 도메인 속성 없이 한 번, 이 호스트로 한 번(.kimptrack.com)
  const forget = () => {
    for (const name of ["_clck", "_clsk"]) {
      const gone = name + "=; Max-Age=0; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/"
      document.cookie = gone
      document.cookie = gone + "; domain=" + HOST
    }
    try {
      window.sessionStorage.removeItem("_cltk")
    } catch (e) {
      // sessionStorage 를 못 쓰면 남은 것도 없다
    }
  }

  // defer 로 실린 이 파일은 문서 해석이 끝난 뒤(readyState 가 이미 "interactive")·DOMContentLoaded 전에 돈다 — 그래서 readyState 만
  // 보지 않고 defer 로 실렸는지도 본다. 대시보드는 앱 모듈(defer)까지 돈 뒤에 DOMContentLoaded 가 온다
  const beforeReady = document.readyState === "loading" || !!(document.currentScript && document.currentScript.defer)
  const whenReady = (fn) => {
    if (beforeReady) document.addEventListener("DOMContentLoaded", guard(fn), { once: true })
    else fn()
  }

  // 부른다(§3.4) — 표준 대기열 함수를 곧바로 만들고 첫 호출로 동의 신호(광고 저장 거부·분석 허용)를 넣는다.
  // 파일이 실행될 때 이미 켜는 값이면 태그는 DOMContentLoaded 뒤(셸의 첫 URL 정리가 끝난 뒤 첫 주소를 읽게),
  // 동의 창의 저장·다른 탭의 동의로 부를 때(late)는 곧바로 넣고 셸이 지금 화면의 태그를 두게 kt:clarity 를 한 번 보낸다
  const load = (late) => {
    if (loaded) return
    loaded = true
    window.clarity =
      window.clarity ||
      function () {
        ;(window.clarity.q = window.clarity.q || []).push(arguments)
      }
    window.clarity("consentv2", { ad_Storage: "denied", analytics_Storage: "granted" })
    const inject = () => {
      const tag = document.createElement("script")
      tag.async = true
      tag.src = "https://www.clarity.ms/tag/" + CLARITY_ID
      document.head.appendChild(tag)
    }
    if (!late) return whenReady(inject)
    inject()
    window.dispatchEvent(new Event("kt:clarity"))
  }

  // 닫을 때 뒤 페이지를 되돌린다 — 이 창이 막은 형제 요소의 inert 와 문서 스크롤
  const closeModal = () => {
    if (!modal) return
    modal.remove()
    modal = null
    for (const sib of blocked) sib.inert = false
    blocked = []
    const root = document.documentElement.style
    root.overflow = scrollWas[0]
    root.scrollbarGutter = scrollWas[1]
    const style = document.getElementById("kt-consent-style")
    if (style) style.remove()
  }

  // [모두 거부] — denied 를 쓰고 판을 지운 뒤 동의 창을 닫는다. 아무것도 부르지 않는다(쓰기 예외여도 창만 닫는다)
  const refuse = () => {
    try {
      window.localStorage.setItem(KEY, "denied")
    } catch (e) {
      // 저장하지 못해도 창은 닫는다 — 값이 그대로면 다음 페이지에서 다시 묻는다
    }
    try {
      window.localStorage.removeItem(VKEY)
    } catch (e) {
      // 위와 같다
    }
    closeModal()
  }

  // [선택한 대로 저장] — 셋 다 체크해야 동의. 판을 먼저 써서 다른 탭이 granted 를 볼 때 판이 이미 맞게, 쓰기가 예외면 부르지 않는다
  const save = () => {
    if (!BOXES.every((id) => modal.querySelector("#" + id).checked)) return refuse()
    let stored = false
    try {
      window.localStorage.setItem(VKEY, NOTICE_VERSION)
      window.localStorage.setItem(KEY, "granted")
      stored = true
    } catch (e) {
      // 동의를 저장할 수 없으면 이어 갈 수 없다
    }
    closeModal()
    if (stored) load(true)
  }

  // 동의 창(§3.3) — <body> 의 첫 자식, #root 밖이라 React 트리를 다시 그리지 않는다. 모달이다 — 뒤 페이지의 형제 요소를 모두
  // inert 로 막고(누르기·초점·스크린 리더) 문서 스크롤을 멈추고, 초점을 카드(.kt-c-box, tabindex -1)로 옮긴다 — 스크립트가 옮긴
  // 초점에도 크롬은 :focus-visible 테두리를 그리므로 카드는 테두리 없이 받고, 다음 Tab 부터 글 영역·칸·버튼에 테두리가 보인다. 닫기(×)·Esc 는 없다 — 두 버튼
  // 가운데 하나를 골라야 닫힌다. 대시보드에서는 창 안의 링크를 모두 새 탭으로 — 열어 둔 WebSocket 을 끊지 않게
  const openModal = () => {
    if (loaded || modal || judge() !== "undecided") return
    const style = document.createElement("style")
    style.id = "kt-consent-style"
    style.textContent = CSS
    document.head.appendChild(style)
    const el = document.createElement("div")
    el.id = "kt-consent"
    el.setAttribute("role", "dialog")
    el.setAttribute("aria-modal", "true")
    el.setAttribute("aria-label", "화면 분석 동의")
    el.setAttribute("data-nosnippet", "")
    el.innerHTML = '<div class="kt-c-box" tabindex="-1">' + HTML + "</div>"
    if (page === "app") {
      for (const a of el.querySelectorAll("a")) {
        a.target = "_blank"
        a.rel = "noopener"
      }
    }
    el.querySelector("#kt-c-save").addEventListener("click", guard(save))
    el.querySelector("#kt-c-deny").addEventListener("click", guard(refuse))
    document.body.insertBefore(el, document.body.firstChild)
    modal = el
    for (const sib of Array.from(document.body.children)) {
      if (sib === el || sib.inert) continue
      sib.inert = true
      blocked.push(sib)
    }
    // 스크롤 막대가 있던 문서는 그 자리를 남겨 둔다 — 막대가 사라지며 페이지가 옆으로 밀리지 않게
    const root = document.documentElement.style
    scrollWas = [root.overflow, root.scrollbarGutter]
    if (window.innerWidth > document.documentElement.clientWidth) root.scrollbarGutter = "stable"
    root.overflow = "hidden"
    el.querySelector(".kt-c-box").focus({ preventScroll: true })
  }

  // 다른 탭 반영(§3.5) — 부른 문서는 켜는 값이 아니게 되면 곧바로 한 번 새로고침(숨은 탭도), 부르지 않은 문서는 켜는 값이 되면
  // 동의 창을 닫고 그 자리에서 부른다(GPC 면 그대로), 창이 떠 있는데 denied 가 되면 창만 닫는다
  const onStorage = (event) => {
    if (event.key !== KEY && event.key !== VKEY && event.key !== null) return
    const now = judge()
    if (loaded) {
      if (now !== "on" && !reloading) {
        reloading = true
        location.reload()
      }
      return
    }
    if (now === "on") {
      closeModal()
      load(true)
    } else if (now === "denied") closeModal()
  }

  // 뒤로 가기 캐시(bfcache)에서 돌아온 문서(§3.5) — 캐시에 든 동안 바뀐 값의 storage 이벤트는 오지 않거나 복원 뒤에 늦게 온다
  // (브라우저마다 다르다). 같은 탭에서 랜딩 → 방침 [동의 철회] → 뒤로 가기면 Clarity 가 이 pageshow 로 녹화를 다시 시작하므로,
  // 부른 문서가 켜는 값이 아니면 capture 단계에서 Clarity 의 처리기(나중에 붙은 non-capture)를 막고 곧바로 한 번 새로고침한다.
  // 부르지 않은 문서는 켜는 값이면 동의 창을 닫고 부르고, 창 조건(정하지 않음)이 아니면 창만 닫는다
  const onPageShow = (event) => {
    if (!event.persisted) return
    const now = judge()
    if (loaded) {
      if (now === "on") return
      event.stopImmediatePropagation()
      if (!reloading) {
        reloading = true
        location.reload()
      }
      return
    }
    if (now === "on") {
      closeModal()
      load(true)
    } else if (now !== "undecided") closeModal()
  }

  try {
    if (judge() === "on") load(false)
    else {
      forget()
      whenReady(openModal)
    }
    window.addEventListener("storage", guard(onStorage))
    window.addEventListener("pageshow", guard(onPageShow), true)
  } catch (e) {
    // 위와 같다
  }
})()
