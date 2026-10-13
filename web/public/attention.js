// KimpTrack 화면 영역 이용 통계 — 화면마다 data-area 영역의 보인 시간·1초 이상 보임·클릭 수를 세어 숨길 때 보낸다 (스펙 052).
// 화면 분석에 동의한 방문자만(clarity.js 와 같은 판정 — 지금 판의 granted·GPC 아님). 마우스 위치·좌표·스크롤 경로·글자·입력값은
// 보내지 않고, 쿠키·저장소에 아무것도 쓰지 않는다(방문자 식별 없음). 번들 밖 정적 파일이라 oxlint 대상이 아니다(node --check).
// 예외를 밖으로 던지지 않는다 — 실패하면 통계만 없고 페이지는 그대로다.
;(() => {
  // 지금 안내 판 — clarity.js 의 NOTICE_VERSION 과 같은 값(server/tests/test_clarity.py·test_attention.py 가 묶는다).
  // 판을 올리는 PR 은 이 값도 함께 올린다 — 예전 판의 granted 는 동의가 아니다(2026-10-09-2 — 060, 같은 날 두 번째 판)
  const NOTICE_VERSION = "2026-10-09-2"

  const HOST = "kimptrack.com" // www 는 apex 로 301 이고 저장값이 출처마다 따로라 이 호스트 하나만
  // 덮어 보기(053)로 이 페이지를 틀에 띄우는 관리자 화면의 출처 — 이 출처의 메시지만 받고 이 출처로만 보낸다.
  // 로컬 확인은 커밋하지 않는 시험 사본에서 이 값과 위 HOST 를 바꾼다(033 §4 의 시험 사본과 같은 방식)
  const ADMIN_ORIGIN = "https://admin.kimptrack.com"
  const KEY = "kt.analytics"
  const VKEY = "kt.analytics.v"
  const BEACON_URL = "/api/attention"
  // 화면 이름(§3.1) — 서버 상수 PAGES 와 같다. 대시보드 탭 id 는 web/src/App.tsx 의 TabId
  const TABS = ["spread", "history", "gap", "pp", "health", "flow"]
  const STATIC = new Map([
    ["/", "landing"],
    ["/privacy", "privacy"],
    ["/kimp-chart", "kimp-chart"],
    ["/kimp-history", "kimp-history"],
  ])
  const AREA_ID = /^[a-z][a-z0-9-]{0,31}$/
  const MAX_AREAS = 40
  const MAX_MS = 600000 // 구간 전체에서 영역당
  const MAX_CLICKS = 50
  const SEEN_MS = 1000
  const IDLE_MS = 300000 // 마지막 입력 뒤 5분이 지나면 쌓지 않는다
  const MIN_PX = 40
  const MOBILE_WIDTH = 768
  const SCAN_MS = 1000 // 늦게 생기는 영역 찾기는 1초에 한 번까지

  // 요소(061) — 영역 안에서 사람이 누르거나 보는 낱낱. 열쇠는 data-el 이 먼저, 없으면 대화형 요소의 <종류>:<이름>
  const MAX_TRACKED = 200 // 화면(구간)당 보인 시간·마우스 올림을 세는 서로 다른 (영역, 열쇠) — 문서에서 처음 찾은 순서
  const MAX_SENT = 150 // 몸통 e 항목 — (클릭, 보인 시간) 큰 순
  const MAX_BODY = 16384 // 몸통 바이트 — 넘치면 e 를 뒤에서부터 줄인다
  const KEY_MAX = 64
  const TEXT_CHARS = 24 // 보이는 글자로 만든 이름은 앞 24자
  const HOVER_MIN_MS = 300 // 한 번 머문 시간이 이보다 짧으면 버린다
  const HOVER_CUT_MS = 30000 // 한 번 머문 시간은 여기서 자른다
  const DEPTH_PAGES = ["landing", "privacy", "kimp-chart", "kimp-history"] // 깊이는 문서가 스크롤되는 정적 페이지만
  // role 이 태그보다 먼저 — <button role="tab"> 은 tab
  const ROLES = new Map([
    ["link", "link"],
    ["button", "btn"],
    ["tab", "tab"],
    ["checkbox", "check"],
    ["switch", "check"],
  ])
  // 영역 안에서 찾는 요소 — data-el(행 빼고 — 행은 클릭만 센다) 또는 대화형 요소
  const FIND =
    '[data-el]:not([data-el^="row:"]),a[href],button,summary,input,select,textarea,label,' +
    "[role=button],[role=tab],[role=checkbox],[role=switch],[role=link]"
  // 열쇠에 못 쓰는 글자 — 제어 문자·| ·짝 없는 대리 문자(UTF-8 로 못 적는다)
  const BAD_KEY = /[\u0000-\u001f\u007f-\u009f|]|[\ud800-\udbff](?![\udc00-\udfff])|(?:^|[^\ud800-\udbff])[\udc00-\udfff]/

  // 이벤트 처리기도 밖으로 던지지 않는다
  const guard = (fn) => (event) => {
    try {
      fn(event)
    } catch (e) {
      // 통계만 없고 페이지는 그대로
    }
  }

  // 대시보드 탭은 App 과 같은 규칙 — 없거나 목록 밖이면 spread
  const tabPage = (tab) => "app-" + (TABS.indexOf(tab) >= 0 ? tab : "spread")
  const params = new URLSearchParams(location.search)
  const firstPage = location.pathname.indexOf("/app/") === 0 ? tabPage(params.get("tab")) : STATIC.get(location.pathname) || ""
  let framed = true
  try {
    framed = window.top !== window
  } catch (e) {
    // 다른 출처의 틀 — 틀 안이다
  }
  const overlayAsked = params.get("kt-overlay") === "1"
  if (location.hostname !== HOST || !firstPage) return
  // 덮어 보기(053) — 주소에 kt-overlay=1 이 있고 틀 안일 때만. 세지도 보내지도 않는다(아래 세기 코드에 닿지 않는다)
  if (framed && overlayAsked) return overlay(firstPage)
  // 켜는 조건(§3.2) — 호스트·틀 밖·덮어 보기 아님·목록 안 화면. 하나라도 아니면 아무것도 하지 않는다
  if (framed || overlayAsked) return

  // 동의 상태 '켬' — 지금 판의 granted 이고 GPC 가 아니다. 저장소를 읽다 예외면 끔
  const consented = () => {
    if (navigator.globalPrivacyControl === true) return false
    try {
      return window.localStorage.getItem(KEY) === "granted" && window.localStorage.getItem(VKEY) === NOTICE_VERSION
    } catch (e) {
      return false
    }
  }

  // ── 열쇠 (061 §3.1) ──────────────────────────────────────────────────────────────────────

  const validKey = (k) => (typeof k === "string" && k.length > 0 && k.length <= KEY_MAX && !BAD_KEY.test(k) ? k : "")
  const tagOf = (el) => String(el.tagName || "").toLowerCase()

  // 글자 다듬기 — 제어 문자·| 는 공백으로, 공백은 하나로. 화면에 보이는 글자(screen)면 숫자(가격 등)는 # 로 가린다
  const clean = (s, screen) => {
    const t = String(s == null ? "" : s)
      .replace(/[\u0000-\u001f\u007f-\u009f|]/g, " ")
      .replace(/\s+/g, " ")
      .trim()
    return screen ? t.replace(/\d[\d,.]*/g, "#") : t
  }

  // <종류>:<이름> — 이름은 앞 chars 글자(코드 포인트라 이모지 반쪽이 남지 않는다), 열쇠는 64자 안으로
  const join = (kind, name, chars) => {
    const cps = Array.from(name).slice(0, chars)
    let key = kind + ":" + cps.join("").trim()
    while (key.length > KEY_MAX && cps.length) {
      cps.pop()
      key = kind + ":" + cps.join("").trim()
    }
    return key.length > kind.length + 1 ? validKey(key) : ""
  }

  // 대화형 요소의 종류 — role 이 먼저, 아니면 태그. 대화형이 아니면 ""
  const kindOf = (el) => {
    const role = el.getAttribute("role")
    if (role && ROLES.has(role)) return ROLES.get(role)
    const tag = tagOf(el)
    if (tag === "a") return el.hasAttribute("href") ? "link" : ""
    if (tag === "button") return "btn"
    if (tag === "summary") return "faq"
    if (tag === "input") return /^(checkbox|radio)$/i.test(el.getAttribute("type") || "") ? "check" : "input"
    if (tag === "select") return "select"
    if (tag === "textarea") return "input"
    if (tag === "label") return "label"
    return ""
  }

  // 링크 이름 — 같은 출처는 경로만, 다른 출처는 호스트 + 경로(쿼리·조각 뺌). http(s) 가 아니면 주소 종류만(메일 주소를 싣지 않게)
  const linkPath = (el) => {
    const href = el.getAttribute("href")
    if (href === null) return ""
    try {
      const u = new URL(href, location.href)
      if (u.protocol !== "http:" && u.protocol !== "https:") return u.protocol.slice(0, -1)
      return u.origin === location.origin ? u.pathname : u.host + u.pathname
    } catch (e) {
      return ""
    }
  }

  // 자동 열쇠 — 이름은 aria-label → (링크) 경로 → (입력칸) name·placeholder·type → 보이는 글자 앞 24자. 입력칸의 값은 읽지 않는다.
  // 라벨은 그 입력칸의 열쇠다(라벨을 누르면 브라우저가 입력칸을 한 번 더 누른다)
  const autoKey = (el, area) => {
    const kind = kindOf(el)
    if (!kind) return ""
    if (kind === "label") {
      const c = el.control
      return c && c !== el && tagOf(c) !== "label" ? keyIn(c, area) : ""
    }
    const aria = clean(el.getAttribute("aria-label"), true)
    if (aria) return join(kind, aria, KEY_MAX)
    if (kind === "link") {
      const path = clean(linkPath(el), false)
      if (path) return join(kind, path, KEY_MAX)
    }
    const tag = tagOf(el)
    if (tag === "input" || tag === "select" || tag === "textarea") {
      const type = tag === "input" ? clean(el.getAttribute("type"), false).toLowerCase() || "text" : tag
      return join(kind, clean(el.getAttribute("name"), false) || clean(el.getAttribute("placeholder"), true) || type, KEY_MAX)
    }
    return join(kind, clean(el.textContent, true), TEXT_CHARS)
  }

  // el 이 영역 안에서 받는 열쇠 — 그 요소나 가장 가까운 조상(영역 안)의 data-el, 없으면 자동 열쇠. 영역 밖이면 ""
  function keyIn(el, area) {
    let tagged = null
    for (let n = el; n; n = n.parentElement) {
      if (n === area) return tagged !== null ? validKey(tagged) : autoKey(el, area)
      if (tagged === null) tagged = n.getAttribute("data-el")
    }
    return ""
  }

  // 누르거나 마우스를 올린 곳의 열쇠 — 대상에서 영역까지 올라가며 가장 가까운 data-el, 없으면 처음 만난 대화형 요소의 자동 열쇠.
  // 누름(clicking)에서 라벨의 입력칸 밖을 눌렀으면 "" — 브라우저가 이어서 입력칸을 누르고 그 누름이 같은 열쇠로 센다(한 번 = 1)
  const hitKey = (target, area, clicking) => {
    let tagged = null
    let first = null
    const passed = []
    for (let n = target; n; n = n.parentElement) {
      if (n === area) return tagged !== null ? validKey(tagged) : first ? autoKey(first, area) : ""
      if (clicking && !first && tagOf(n) === "label" && n.control && passed.indexOf(n.control) < 0) return ""
      passed.push(n)
      if (tagged === null) tagged = n.getAttribute("data-el")
      if (!first && kindOf(n)) first = n
    }
    return ""
  }

  // 찾은 요소의 추적 열쇠 — 자기 data-el, 또는 data-el 조상이 없을 때 자동 열쇠(조상이 있으면 그 조상이 센다). 행(row:)은 빼고
  const trackKey = (el, area) => {
    const own = el.getAttribute("data-el")
    let key = ""
    if (own !== null) key = validKey(own)
    else {
      for (let n = el.parentElement; n && n !== area; n = n.parentElement) {
        if (n.getAttribute("data-el") !== null) return ""
      }
      key = autoKey(el, area)
    }
    return key.indexOf("row:") === 0 ? "" : key
  }

  let started = false
  let seg = null // 구간(한 번 본 화면) — 멈추면 null
  let visible = new Set() // 지금 '보임' 인 영역 id
  const near = new Set() // 화면 근처에 들어온 요소(IntersectionObserver)
  const watched = new Map() // 요소 → 영역 id
  const ids = new Set() // 세는 영역 id(40개까지)
  let io = null
  let mo = null
  let frame = 0
  let scanTimer = 0
  let scanAt = 0
  let lastAt = 0 // 시간을 마지막으로 나눠 준 때
  let lastInput = 0
  let docVisible = true

  // 구간 하나 — 기기는 시작 때 폭으로 정하고 구간 중에는 그대로. total·clicks 는 구간 전체 합(상한), pend 는 아직 안 보낸 몫
  const begin = (page) => {
    seg = {
      page,
      device: window.innerWidth < MOBILE_WIDTH ? "mobile" : "pc",
      pvSent: false,
      total: {},
      clicks: {},
      seen: {},
      pend: {},
    }
    lastAt = Date.now()
    visible = new Set()
    schedule()
  }

  const slot = (id) => seg.pend[id] || (seg.pend[id] = [0, 0, 0])

  // 지난번부터 지금까지 — 문서가 보이고 마지막 입력 뒤 5분 안이었던 만큼만 보이는 영역에 나눠 준다
  const account = (t) => {
    if (!seg) return
    const until = Math.min(t, lastInput + IDLE_MS)
    if (docVisible && until > lastAt) {
      const d = until - lastAt
      for (const id of visible) {
        const used = seg.total[id] || 0
        const add = Math.min(d, MAX_MS - used)
        if (add > 0) {
          seg.total[id] = used + add
          slot(id)[0] += add
        }
        if (!seg.seen[id] && seg.total[id] >= SEEN_MS) {
          seg.seen[id] = true
          slot(id)[2] = 1
        }
      }
    }
    lastAt = t
  }

  // 보임(§3.2) — 보이는 높이 ≥ min(영역 높이 × 0.5, 화면 높이 × 0.5) 이고 ≥ min(40px, 영역 높이)
  const measure = () => {
    frame = 0
    if (!seg) return
    account(Date.now())
    const h = window.innerHeight
    const next = new Set()
    for (const el of near) {
      if (!el.isConnected) {
        near.delete(el)
        continue
      }
      const r = el.getBoundingClientRect()
      const shown = Math.min(r.bottom, h) - Math.max(r.top, 0)
      if (r.height > 0 && shown >= Math.min(MIN_PX, r.height) && shown >= Math.min(r.height * 0.5, h * 0.5)) next.add(watched.get(el))
    }
    visible = next
  }

  // 스크롤·크기·DOM 바뀜 — 애니메이션 프레임당 한 번 다시 잰다
  function schedule() {
    if (!frame && seg) frame = window.requestAnimationFrame(guard(measure))
  }

  const onIntersect = (entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) near.add(entry.target)
      else near.delete(entry.target)
    }
    schedule()
  }

  // 영역 찾기 — 떨어져 나간 요소는 놓고, 새 요소는 id 꼴이 맞고 40개 안이면 지켜본다
  const scan = () => {
    scanTimer = 0
    scanAt = Date.now()
    if (!seg) return
    for (const el of Array.from(watched.keys())) {
      if (!el.isConnected) {
        io.unobserve(el)
        watched.delete(el)
        near.delete(el)
      }
    }
    for (const el of document.querySelectorAll("[data-area]")) {
      if (watched.has(el)) continue
      const id = el.getAttribute("data-area")
      if (!AREA_ID.test(id) || (!ids.has(id) && ids.size >= MAX_AREAS)) continue
      ids.add(id)
      watched.set(el, id)
      io.observe(el)
    }
    schedule()
  }

  const onMutate = () => {
    schedule()
    if (scanTimer || !seg) return
    scanTimer = window.setTimeout(guard(scan), Math.max(0, scanAt + SCAN_MS - Date.now()))
  }

  const onInput = () => {
    const t = Date.now()
    account(t)
    lastInput = t
  }

  const onScroll = () => {
    onInput()
    schedule()
  }

  // 클릭 — 대상의 가장 가까운 영역 하나에 1, 영역 밖 클릭은 버린다
  const onClick = (event) => {
    if (!seg) return
    const el = event.target && event.target.closest ? event.target.closest("[data-area]") : null
    if (!el) return
    const id = el.getAttribute("data-area")
    if (!ids.has(id)) return
    const used = seg.clicks[id] || 0
    if (used >= MAX_CLICKS) return
    seg.clicks[id] = used + 1
    slot(id)[1] += 1
  }

  // 멈춤 — 철회를 본 뒤로는 세지도 보내지도 않는다
  const stop = () => {
    seg = null
    visible = new Set()
    if (io) io.disconnect()
    if (mo) mo.disconnect()
  }

  // 비콘 — 문자열이라 text/plain(미리 묻기 없음). sendBeacon 이 없거나 false 면 fetch 한 번, 실패해도 다시 보내지 않는다
  const post = (body) => {
    let queued = false
    try {
      queued = typeof navigator.sendBeacon === "function" && navigator.sendBeacon(BEACON_URL, body) === true
    } catch (e) {
      // 아래 fetch 로
    }
    if (queued) return
    const sent = window.fetch(BEACON_URL, { method: "POST", body, keepalive: true, credentials: "omit" })
    if (sent && typeof sent.catch === "function") sent.catch(() => {})
  }

  // 보내기(§3.3) — 직전에 동의를 다시 본다. 보낸 몫은 0 으로(구간 합·이미 보낸 seen 은 기억), pv 는 구간의 첫 보냄에만 1.
  // 값이 모두 0 인 영역은 빼고, 영역도 pv 도 없으면 보내지 않는다. 영역 40개 × id 32자라 몸통은 4,096바이트를 넘지 않는다
  const send = () => {
    if (!seg) return
    account(Date.now())
    if (!consented()) return stop()
    const a = {}
    let any = false
    for (const id of Object.keys(seg.pend)) {
      const v = seg.pend[id]
      if (v[0] || v[1] || v[2]) {
        a[id] = v
        any = true
      }
    }
    seg.pend = {}
    const pv = seg.pvSent ? 0 : 1
    if (!any && !pv) return
    seg.pvSent = true
    post(JSON.stringify({ v: 1, page: seg.page, device: seg.device, pv, a }))
  }

  const onVisibility = () => {
    account(Date.now())
    docVisible = document.visibilityState === "visible"
    if (!docVisible) send()
  }

  // 대시보드 탭이 바뀌면 지금 구간을 끝내고(보내고) 새 탭 이름으로 새 구간. 같은 id 는 무시
  const onTab = (event) => {
    if (!seg) return
    const tab = event && event.detail
    if (TABS.indexOf(tab) < 0 || "app-" + tab === seg.page) return
    send()
    if (seg) begin("app-" + tab)
  }

  const start = () => {
    if (started || !consented()) return
    started = true
    lastInput = Date.now() // 페이지 연 때(또는 동의가 켜진 때)도 입력이다
    docVisible = document.visibilityState === "visible"
    io = new window.IntersectionObserver(guard(onIntersect), { threshold: 0 })
    mo = new window.MutationObserver(guard(onMutate))
    begin(firstPage)
    scan()
    mo.observe(document.documentElement, { childList: true, subtree: true })
    const opts = { capture: true, passive: true }
    for (const type of ["pointermove", "pointerdown", "wheel", "keydown", "touchstart"]) {
      document.addEventListener(type, guard(onInput), opts)
    }
    document.addEventListener("scroll", guard(onScroll), opts)
    document.addEventListener("click", guard(onClick), true)
    document.addEventListener("visibilitychange", guard(onVisibility))
    window.addEventListener("resize", guard(schedule))
    window.addEventListener("pagehide", guard(send))
    if (firstPage.indexOf("app-") === 0) window.addEventListener("kt:tab", guard(onTab))
  }

  try {
    if (consented()) start()
    else {
      // 동의만 아니면 듣기 둘 — 같은 탭의 띠 저장(clarity.js 의 kt:clarity)과 다른 탭의 저장. 켜지면 그 순간부터 센다
      window.addEventListener("kt:clarity", guard(start))
      window.addEventListener("storage", guard(start))
    }
  } catch (e) {
    // 위와 같다
  }

  // ── 덮어 보기 (053) ─────────────────────────────────────────────────────────────────────────
  // 관리자 화면이 이 페이지를 틀로 띄워 영역별 값을 postMessage 로 넘기면, 영역 위에 단계 색 상자와 꼬리표를 그린다.
  // 누름은 막는다(링크·탭 단추가 화면을 바꾸지 않게 — 화면은 관리자 고르기로만 바뀐다). 휠·터치 스크롤은 그대로.
  // 값이 오기 전에는 아무것도 그리지 않는다. 글자는 textContent 로만, 받은 값은 꼴을 검사한 수·id 만 쓴다
  function overlay(page) {
    const SIZES = [12, 12, 14, 16, 19, 22] // 꼬리표 글씨 — 단계 0~5(많이 본 곳이 크게)
    // 단계 1~5 의 채움·테두리 — 옅은 파랑 → 노랑 → 주황 → 빨강, 불투명도 0.18~0.5, 테두리 2~4px. 단계 0 은 회색 점선만
    const FILL = ["", "rgba(96,165,250,.18)", "rgba(250,204,21,.26)", "rgba(251,146,60,.34)", "rgba(239,68,68,.42)", "rgba(220,38,38,.5)"]
    const LINE = ["2px dashed rgba(156,163,175,.9)", "2px solid #60a5fa", "2px solid #facc15", "3px solid #fb923c", "3px solid #ef4444", "4px solid #dc2626"]
    const HATCH = "repeating-linear-gradient(45deg,rgba(107,114,128,.55) 0 2px,transparent 2px 8px)"
    const FOCUS_MS = 2000
    let values = null // id → 받은 값. null 이면 아직 값이 오지 않았다 — 그리지 않는다
    let label = ""
    let layer = null
    let frame = 0
    let scanTimer = 0
    let scanAt = 0
    let told = null // 마지막으로 알린 영역 id 들
    let focusId = ""
    let focusUntil = 0

    const set = (el, props) => {
      for (const k of Object.keys(props)) el.style[k] = props[k]
    }

    // 누름 막기 — window 캡처 단계에서 가장 먼저 받아 페이지의 처리기·기본 동작(링크 이동·제출)에 닿지 않게
    const block = (event) => {
      event.preventDefault()
      event.stopPropagation()
    }
    for (const type of ["click", "auxclick", "submit", "dblclick"]) window.addEventListener(type, block, true)

    // 지금 크기 있는 영역 — id 꼴이 맞는 것, 문서 순서, 같은 id 는 크기 있는 첫 요소(대시보드의 숨은 탭은 크기 0)
    const sized = () => {
      const out = new Map()
      for (const el of document.querySelectorAll("[data-area]")) {
        const id = el.getAttribute("data-area")
        if (!AREA_ID.test(id) || out.has(id)) continue
        const r = el.getBoundingClientRect()
        if (r.width > 0 && r.height > 0) out.set(id, el)
        if (out.size >= MAX_AREAS) break
      }
      return out
    }

    // 관리자에게 지금 영역 id 들을 알린다 — 처음 한 번, 그 뒤로는 모임이 바뀌었을 때만
    const announce = () => {
      const ids = Array.from(sized().keys())
      const key = ids.join(" ")
      if (key === told) return
      told = key
      window.parent.postMessage({ type: "kt-attention-ready", v: 1, page, areas: ids }, ADMIN_ORIGIN)
    }

    const tagText = (v) => {
      const tail = v && v.small ? " · 표본 적음" : ""
      if (!v || v.rank === null) return "기록 없음" + tail
      return "#" + v.rank + " · 평균 " + v.t.toFixed(1) + "초 · 도달 " + v.r + "% · 클릭 " + v.c.toFixed(1) + tail
    }

    // 상자 하나 — 영역의 화면 위 자리에. 꼬리표는 상자 왼쪽 위(상자 위쪽이 화면 밖이면 화면 맨 위에 붙인다)
    const box = (id, rect, now) => {
      const v = values.get(id)
      const level = v ? v.level : 0
      const el = document.createElement("div")
      el.className = "kt-ov-box kt-ov-l" + level + (v && v.low ? " kt-ov-low" : "")
      el.setAttribute("data-kt-area", id)
      set(el, {
        position: "absolute",
        boxSizing: "border-box",
        left: rect.left + "px",
        top: rect.top + "px",
        width: rect.width + "px",
        height: rect.height + "px",
        border: LINE[level],
        backgroundColor: level ? FILL[level] : "transparent",
        backgroundImage: v && v.low ? HATCH : "none",
        overflow: "hidden", // 꼬리표가 상자 밖(화면 맨 위에 붙인 자리)으로 나가지 않게
      })
      if (id === focusId && now < focusUntil) set(el, { borderWidth: "6px", borderStyle: "solid" })
      const tag = document.createElement("span")
      tag.className = "kt-ov-tag"
      tag.textContent = tagText(v)
      set(tag, {
        position: "absolute",
        left: "0",
        top: Math.max(0, -rect.top) + "px",
        maxWidth: "100%",
        padding: "2px 6px",
        background: "rgba(17,24,39,.88)",
        color: "#f9fafb",
        font: "600 " + SIZES[level] + "px/1.3 system-ui,-apple-system,sans-serif",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      })
      el.appendChild(tag)
      return el
    }

    // 그리기 — 스크롤·크기·DOM 바뀜마다 프레임당 한 번. 층은 맨 위 고정·누름 통과
    const draw = () => {
      frame = 0
      if (!values) return
      if (!layer) {
        layer = document.createElement("div")
        layer.id = "kt-overlay"
        set(layer, { position: "fixed", left: "0", top: "0", width: "100%", height: "100%", pointerEvents: "none", zIndex: "2147483647", overflow: "hidden" })
        ;(document.body || document.documentElement).appendChild(layer)
      }
      if (label) layer.setAttribute("aria-label", label)
      const now = Date.now()
      const boxes = []
      for (const [id, el] of sized()) boxes.push(box(id, el.getBoundingClientRect(), now))
      layer.replaceChildren(...boxes)
    }

    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(guard(draw))
    }

    const onMutate = () => {
      schedule()
      if (scanTimer) return
      scanTimer = window.setTimeout(
        guard(() => {
          scanTimer = 0
          scanAt = Date.now()
          announce()
        }),
        Math.max(0, scanAt + SCAN_MS - Date.now()),
      )
    }

    const finite = (n) => typeof n === "number" && isFinite(n)
    // 받은 값 한 줄 — 꼴이 맞는 것만(단계 0~5 정수·순위 1 이상 정수 또는 null·수 셋·참거짓 둘)
    const entry = (a) => {
      if (!a || typeof a.id !== "string" || !AREA_ID.test(a.id)) return null
      const level = a.level
      if (!Number.isInteger(level) || level < 0 || level > 5) return null
      if (!(a.rank === null || (Number.isInteger(a.rank) && a.rank > 0))) return null
      if (!finite(a.t) || !finite(a.r) || !finite(a.c)) return null
      return { level, rank: a.rank, t: a.t, r: Math.round(a.r), c: a.c, low: a.low === true, small: a.small === true }
    }

    // 관리자 메시지 — 출처와 창(부모)을 모두 확인하고, 다르면 무시
    const onMessage = (event) => {
      if (event.origin !== ADMIN_ORIGIN || event.source !== window.parent) return
      const d = event.data
      if (!d || typeof d !== "object") return
      if (d.type === "kt-attention" && d.v === 1 && Array.isArray(d.areas)) {
        const next = new Map()
        for (const a of d.areas.slice(0, MAX_AREAS)) {
          const v = entry(a)
          if (v) next.set(a.id, v)
        }
        values = next
        label = typeof d.label === "string" ? d.label.slice(0, 200) : ""
        schedule()
      } else if (d.type === "kt-attention-focus" && typeof d.id === "string") {
        const el = sized().get(d.id)
        if (!el) return
        el.scrollIntoView({ block: "center" })
        focusId = d.id
        focusUntil = Date.now() + FOCUS_MS
        schedule()
        window.setTimeout(guard(schedule), FOCUS_MS)
      }
    }

    try {
      window.addEventListener("message", guard(onMessage))
      document.addEventListener("scroll", guard(schedule), { capture: true, passive: true })
      window.addEventListener("resize", guard(schedule))
      new window.MutationObserver(guard(onMutate)).observe(document.documentElement, { childList: true, subtree: true })
      // defer 로 실려 문서는 이미 읽혔다 — 곧바로 한 번(대시보드는 React 가 뒤에 그려 모임이 바뀌면 다시 알린다)
      if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", guard(announce))
      else announce()
    } catch (e) {
      // 덮어 보기만 없고 페이지는 그대로
    }
  }
})()
