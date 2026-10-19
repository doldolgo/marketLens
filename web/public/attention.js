// KimpTrack 화면 이용 통계 — 화면마다 data-area 영역의 보인 시간·1초 이상 보임·클릭 수(스펙 052)와 영역 안 요소별 클릭 수·마우스를
// 올려 둔 시간·보인 시간, 페이지를 떠날 때 보던 영역·내려온 깊이(스펙 061)를 세어 숨길 때 보낸다.
// 동의와 관계없이 모든 방문자(RECORD_ALL — 사람 결정 2026-10-18, 끄면 clarity.js 와 같은 판정의 동의자만). 영역·요소 이름과 숫자만 보낸다 — 입력값·화면 글자의
// 숫자(가격 등)·좌표·마우스 경로는 보내지 않고, 쿠키·저장소에 아무것도 쓰지 않는다(방문자 식별 없음). 번들 밖 정적 파일이라 oxlint
// 대상이 아니다(node --check). 예외를 밖으로 던지지 않는다 — 실패하면 통계만 없고 페이지는 그대로다.
;(() => {
  // 지금 안내 판 — clarity.js 의 NOTICE_VERSION 과 같은 값(server/tests/test_clarity.py·test_attention.py 가 묶는다).
  // 판을 올리는 PR 은 이 값도 함께 올린다 — 예전 판의 granted 는 동의가 아니다(2026-10-17 — 075, Google Analytics 를 더한 판. replay.js 와 같은 판)
  const NOTICE_VERSION = "2026-10-17-2"

  const HOST = "kimptrack.com" // www 는 apex 로 301 이고 저장값이 출처마다 따로라 이 호스트 하나만
  // 덮어 보기(053)로 이 페이지를 틀에 띄우는 관리자 화면의 출처 — 이 출처의 메시지만 받고 이 출처로만 보낸다.
  // 로컬 확인은 커밋하지 않는 시험 사본에서 이 값과 위 HOST 를 바꾼다(033 §4 의 시험 사본과 같은 방식)
  const ADMIN_ORIGIN = "https://admin.kimptrack.com"
  // 동의와 관계없이 모든 방문자를 센다(052 §3.1 — 사람 결정 2026-10-18). false 면 지금 판에 동의한 방문자만
  const RECORD_ALL = true
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
  const MAX_MS = 600000 // 구간 전체에서 영역·요소당(보인 시간·마우스 올림 각각)
  const MAX_CLICKS = 50
  const SEEN_MS = 1000
  const IDLE_MS = 300000 // 마지막 입력 뒤 5분이 지나면 쌓지 않는다
  const MIN_PX = 40
  const MOBILE_WIDTH = 768
  const MIN_VIEW = 100 // 폭이 이보다 작으면 못 잰 것(숨은 채 열림 — 뒤 탭으로 열기·미리 띄우기·배치 전 인앱 브라우저는 0)
  const SCAN_MS = 1000 // 늦게 생기는 영역·요소 찾기는 1초에 한 번까지

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
  // 영역 안의 대화형 요소 — 세기(061)와 덮어 보기(065)가 같이 쓴다
  const ITEMS =
    "a[href],button,summary,input,select,textarea,label," +
    "[role=button],[role=tab],[role=checkbox],[role=switch],[role=link]"
  // 영역 안에서 찾는 요소 — data-el(행 빼고 — 행은 클릭만 센다) 또는 대화형 요소
  const FIND = '[data-el]:not([data-el^="row:"]),' + ITEMS
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

  // 동의 상태 '켬' — 지금 판의 granted 이고 GPC 가 아니다. 저장소를 읽다 예외면 끔
  const consented = () => {
    if (navigator.globalPrivacyControl === true) return false
    try {
      return window.localStorage.getItem(KEY) === "granted" && window.localStorage.getItem(VKEY) === NOTICE_VERSION
    } catch (e) {
      return false
    }
  }
  const recording = () => RECORD_ALL || consented()

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

  // 찾은 요소의 열쇠 — 자기 data-el, 또는 data-el 조상이 없을 때 자동 열쇠(조상이 있으면 그 조상이 센다).
  // 덮어 보기(065)도 이 함수로 (영역, 열쇠)를 가진 요소를 찾는다 — 세는 것과 그리는 것이 같은 요소
  const elementKey = (el, area) => {
    const own = el.getAttribute("data-el")
    if (own !== null) return validKey(own)
    for (let n = el.parentElement; n && n !== area; n = n.parentElement) {
      if (n.getAttribute("data-el") !== null) return ""
    }
    return autoKey(el, area)
  }

  // 추적 열쇠 — 행(row:)은 빼고(행은 클릭만 센다)
  const trackKey = (el, area) => {
    const key = elementKey(el, area)
    return key.indexOf("row:") === 0 ? "" : key
  }

  // 켜는 조건(§3.2) — 호스트·목록 안 화면. 덮어 보기(053)는 주소에 kt-overlay=1 이 있고 틀 안일 때만 — 세지도 보내지도 않는다
  // (아래 세기 코드에 닿지 않는다). 열쇠 함수 뒤에 둔다 — 덮어 보기가 그 함수를 쓴다. 틀 안이거나 덮어 보기 표시만 있으면 아무것도 하지 않는다
  if (location.hostname !== HOST || !firstPage) return
  if (framed && overlayAsked) return overlay(firstPage)
  if (framed || overlayAsked) return

  // ── 세기 (052 §3.2 + 061 §3.2) ───────────────────────────────────────────────────────────

  let started = false
  let seg = null // 구간(한 번 본 화면) — 멈추면 null
  let visible = new Set() // 지금 '보임' 인 영역 id
  let visibleEls = new Set() // 지금 '보임' 인 추적 요소 "영역|열쇠"
  const near = new Set() // 화면 근처에 들어온 영역·요소(IntersectionObserver)
  const watched = new Map() // 영역 요소 → 영역 id
  const tracked = new Map() // 추적 요소 → "영역|열쇠"
  const ids = new Set() // 세는 영역 id(40개까지)
  let io = null
  let mo = null
  let frame = 0
  let scanTimer = 0
  let scanAt = 0
  let lastAt = 0 // 시간을 마지막으로 나눠 준 때
  let lastInput = 0
  let docVisible = true
  let hover = null // 지금 마우스가 머문 추적 요소 { ek, at }

  // 기기 — 768px 기준. 폭을 못 재면(숨은 채 열림) 기기 화면(screen) 폭으로, 그것도 없으면 pc (073 replay.js 와 같은 규칙)
  const deviceOf = () => {
    let wide = Number(window.innerWidth) || 0
    if (wide < MIN_VIEW) {
      try {
        wide = Number(window.screen && window.screen.width) || 0
      } catch (e) {
        wide = 0
      }
    }
    return wide >= MIN_VIEW && wide < MOBILE_WIDTH ? "mobile" : "pc"
  }

  // 구간 하나 — 기기는 시작 때 폭으로 정하고 구간 중에는 그대로. total·clicks 는 구간 전체 합(상한), pend 는 아직 안 보낸 몫.
  // els 는 요소별 (아직 안 보낸 c·h·v, 구간 합 tc·th·tv), keys 는 추적하는 "영역|열쇠"(200개까지). 추적 요소는 구간마다 새로 찾는다
  const begin = (page) => {
    seg = {
      page,
      device: deviceOf(),
      pvSent: false,
      total: {},
      clicks: {},
      seen: {},
      pend: {},
      els: new Map(),
      keys: new Set(),
      depthOn: DEPTH_PAGES.indexOf(page) >= 0,
      depth: 0,
      dSent: false,
      xSent: false,
    }
    lastAt = Date.now()
    visible = new Set()
    visibleEls = new Set()
    hover = null
    for (const el of tracked.keys()) {
      io.unobserve(el)
      near.delete(el)
    }
    tracked.clear()
    schedule()
  }

  const slot = (id) => seg.pend[id] || (seg.pend[id] = [0, 0, 0])

  const elSlot = (ek) => {
    let s = seg.els.get(ek)
    if (!s) {
      const i = ek.indexOf("|")
      s = { area: ek.slice(0, i), key: ek.slice(i + 1), c: 0, h: 0, v: 0, tc: 0, th: 0, tv: 0 }
      seg.els.set(ek, s)
    }
    return s
  }

  // 지난번부터 지금까지 — 문서가 보이고 마지막 입력 뒤 5분 안이었던 만큼만 보이는 영역·추적 요소에 나눠 준다
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
      // 같은 열쇠의 요소가 여럿이면 하나라도 보이면 보임 — visibleEls 는 열쇠 단위다
      for (const ek of visibleEls) {
        const s = elSlot(ek)
        const add = Math.min(d, MAX_MS - s.tv)
        if (add > 0) {
          s.tv += add
          s.v += add
        }
      }
    }
    lastAt = t
  }

  // 보임(§3.2) — 보이는 높이 ≥ min(높이 × 0.5, 화면 높이 × 0.5) 이고 ≥ min(40px, 높이). 영역과 추적 요소가 같은 규칙
  const shows = (r, h) => {
    const shown = Math.min(r.bottom, h) - Math.max(r.top, 0)
    return r.height > 0 && shown >= Math.min(MIN_PX, r.height) && shown >= Math.min(r.height * 0.5, h * 0.5)
  }

  // 깊이(061) — (scrollY + 화면 높이) / 문서 높이를 10% 칸으로, 끝까지(1px 안) = 10
  const depthNow = () => {
    const total = document.documentElement.scrollHeight
    const bottom = (window.scrollY || 0) + window.innerHeight
    if (!(total > 0) || bottom >= total - 1) return 10
    return Math.max(0, Math.min(10, Math.floor((bottom / total) * 10)))
  }

  const measure = () => {
    frame = 0
    if (!seg) return
    account(Date.now())
    const h = window.innerHeight
    const next = new Set()
    const nextEls = new Set()
    for (const el of near) {
      if (!el.isConnected) {
        near.delete(el)
        continue
      }
      if (!shows(el.getBoundingClientRect(), h)) continue
      if (watched.has(el)) next.add(watched.get(el))
      else if (tracked.has(el)) nextEls.add(tracked.get(el))
    }
    visible = next
    visibleEls = nextEls
    if (seg.depthOn) seg.depth = Math.max(seg.depth, depthNow())
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

  const forget = (map, el) => {
    io.unobserve(el)
    map.delete(el)
    near.delete(el)
  }

  // 찾기 — 떨어져 나간 영역·요소는 놓고, 새 영역은 id 꼴이 맞고 40개 안이면, 영역 안의 새 요소는 열쇠가 있고 (영역, 열쇠)가
  // 이 구간의 200개 안이면 지켜본다(같은 열쇠 여럿은 하나로 센다)
  const scan = () => {
    scanTimer = 0
    scanAt = Date.now()
    if (!seg) return
    for (const el of Array.from(watched.keys())) if (!el.isConnected) forget(watched, el)
    for (const el of Array.from(tracked.keys())) if (!el.isConnected) forget(tracked, el)
    for (const el of document.querySelectorAll("[data-area]")) {
      if (watched.has(el)) continue
      const id = el.getAttribute("data-area")
      if (!AREA_ID.test(id) || (!ids.has(id) && ids.size >= MAX_AREAS)) continue
      ids.add(id)
      watched.set(el, id)
      io.observe(el)
    }
    for (const [area, id] of watched) {
      for (const el of area.querySelectorAll(FIND)) {
        if (tracked.has(el)) continue
        const key = trackKey(el, area)
        if (!key) continue
        const ek = id + "|" + key
        if (!seg.keys.has(ek)) {
          if (seg.keys.size >= MAX_TRACKED) continue
          seg.keys.add(ek)
        }
        tracked.set(el, ek)
        io.observe(el)
      }
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

  const areaOf = (target) => {
    const el = target && target.closest ? target.closest("[data-area]") : null
    const id = el ? el.getAttribute("data-area") : ""
    return ids.has(id) ? [el, id] : null
  }

  // 클릭 — 대상의 가장 가까운 영역 하나에 1(052), 그리고 열쇠가 있으면 그 요소에 1(행 포함, 061). 영역 밖 클릭은 버린다.
  // 요소는 사람이 누른 것만 센다 — 페이지 스크립트가 부른 .click() 은 isTrusted 가 false 다(사건 표의 행 누름이 링크를 다시 누른다)
  const onClick = (event) => {
    if (!seg) return
    const hit = areaOf(event.target)
    if (!hit) return
    const [area, id] = hit
    const used = seg.clicks[id] || 0
    if (used < MAX_CLICKS) {
      seg.clicks[id] = used + 1
      slot(id)[1] += 1
    }
    if (event.isTrusted === false) return
    const key = hitKey(event.target, area, true)
    if (!key) return
    const s = elSlot(id + "|" + key)
    if (s.tc < MAX_CLICKS) {
      s.tc += 1
      s.c += 1
    }
  }

  // 마우스 올림(061) — PC 이고 마우스일 때 추적 요소(행 빼고) 위에 머문 시간. 문서가 보이는 동안만, 한 번 머문 시간이 300ms 미만이면
  // 버리고 30초에서 자른다. 머문 곳이 바뀌거나 창 밖으로 나가거나 문서가 숨거나 보낼 때 끝낸다
  const endHover = (t) => {
    if (!hover || !seg) {
      hover = null
      return
    }
    const d = Math.min(t - hover.at, HOVER_CUT_MS)
    const s = elSlot(hover.ek)
    hover = null
    if (d < HOVER_MIN_MS) return
    const add = Math.min(d, MAX_MS - s.th)
    if (add > 0) {
      s.th += add
      s.h += add
    }
  }

  const onOver = (event) => {
    if (!seg || seg.device !== "pc" || event.pointerType !== "mouse") return
    const t = Date.now()
    const hit = docVisible ? areaOf(event.target) : null
    const key = hit ? hitKey(event.target, hit[0], false) : ""
    const ek = key && seg.keys.has(hit[1] + "|" + key) ? hit[1] + "|" + key : ""
    if (hover && hover.ek === ek) return
    endHover(t)
    if (ek) hover = { ek, at: t }
  }

  const onOut = (event) => {
    if (!event.relatedTarget) endHover(Date.now())
  }

  // 떠난 곳(061) — 지금 보이는 높이가 가장 큰 영역(같으면 문서에서 앞). 없으면 null
  const exitArea = () => {
    const h = window.innerHeight
    let best = null
    let most = 0
    for (const [el, id] of watched) {
      if (!el.isConnected) continue
      const r = el.getBoundingClientRect()
      const shown = Math.min(r.bottom, h) - Math.max(r.top, 0)
      if (r.height > 0 && shown > most) {
        most = shown
        best = id
      }
    }
    return best
  }

  // 멈춤 — 철회를 본 뒤로는 세지도 보내지도 않는다
  const stop = () => {
    seg = null
    visible = new Set()
    visibleEls = new Set()
    hover = null
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

  // UTF-8 바이트 수 — 몸통 상한을 재는 데만
  const bytes = (s) => {
    let n = 0
    for (const ch of s) {
      const c = ch.codePointAt(0)
      n += c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4
    }
    return n
  }

  // 몸통 글자 — 16,384바이트를 넘으면 e 를 뒤에서부터(덜 누르고 덜 보인 것부터) 줄인다
  const encode = (body) => {
    const e = body.e
    body.e = []
    let total = bytes(JSON.stringify(body))
    let n = 0
    for (; n < e.length; n++) {
      const add = bytes(JSON.stringify(e[n])) + (n ? 1 : 0)
      if (total + add > MAX_BODY) break
      total += add
    }
    body.e = e.slice(0, n)
    return JSON.stringify(body)
  }

  // 보내기(052 §3.3·061 §3.3) — 직전에 동의를 다시 본다. 보낸 몫은 0 으로(구간 합·이미 보낸 seen 은 기억), pv 는 구간의 첫 보냄에만 1.
  // 값이 모두 0 인 영역·요소는 빼고, 보낼 것이 하나도 없으면 보내지 않는다. 깊이·떠난 곳은 pagehide(final)에만, 구간에 한 번
  const send = (final) => {
    if (!seg) return
    const t = Date.now()
    account(t)
    endHover(t)
    if (!recording()) return stop()
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
    const e = []
    seg.els.forEach((s) => {
      if (s.c || s.h || s.v) e.push([s.area, s.key, s.c, s.h, s.v])
      s.c = 0
      s.h = 0
      s.v = 0
    })
    e.sort((p, q) => q[2] - p[2] || q[4] - p[4])
    if (e.length > MAX_SENT) e.length = MAX_SENT
    let d = null
    let x = null
    if (final && seg.depthOn && !seg.dSent) {
      seg.dSent = true
      d = Math.max(seg.depth, depthNow())
    }
    if (final && !seg.xSent) {
      seg.xSent = true
      x = exitArea()
    }
    const pv = seg.pvSent ? 0 : 1
    if (!any && !e.length && !pv && d === null && x === null) return
    seg.pvSent = true
    post(encode({ v: 2, page: seg.page, device: seg.device, pv, a, e, d, x }))
  }

  const onVisibility = () => {
    account(Date.now())
    docVisible = document.visibilityState === "visible"
    if (!docVisible) send(false)
  }

  // 대시보드 탭이 바뀌면 지금 구간을 끝내고(보내고) 새 탭 이름으로 새 구간 — 추적 요소도 새로 찾는다. 같은 id 는 무시
  const onTab = (event) => {
    if (!seg) return
    const tab = event && event.detail
    if (TABS.indexOf(tab) < 0 || "app-" + tab === seg.page) return
    send(false)
    if (!seg) return
    begin("app-" + tab)
    scan()
  }

  const start = () => {
    if (started || !recording()) return
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
    document.addEventListener("pointerover", guard(onOver), opts)
    document.addEventListener("pointerout", guard(onOut), opts)
    document.addEventListener("visibilitychange", guard(onVisibility))
    window.addEventListener("resize", guard(schedule))
    window.addEventListener("pagehide", guard(() => send(true)))
    if (firstPage.indexOf("app-") === 0) window.addEventListener("kt:tab", guard(onTab))
  }

  try {
    if (recording()) start()
    else {
      // RECORD_ALL 이 아니고 동의도 아니면 듣기 둘 — 같은 탭의 띠 저장(clarity.js 의 kt:clarity)과 다른 탭의 저장. 켜지면 그 순간부터 센다
      window.addEventListener("kt:clarity", guard(start))
      window.addEventListener("storage", guard(start))
    }
  } catch (e) {
    // 위와 같다
  }

  // ── 덮어 보기 (053) ─────────────────────────────────────────────────────────────────────────
  // 관리자 화면이 이 페이지를 틀로 띄워 영역별 값을 postMessage 로 넘기면, 영역 위에 단계 색 상자와 꼬리표를 그린다.
  // 누름은 막는다(링크·탭 단추가 화면을 바꾸지 않게 — 화면은 관리자 고르기로만 바뀐다). 휠·터치 스크롤은 그대로.
  // 값이 오기 전에는 아무것도 그리지 않는다. 글자는 textContent 로만, 받은 값은 꼴을 검사한 수·id 만 쓴다.
  // 065 — 값 메시지의 mode 로 보기를 고른다: area(053 그대로)·element(영역은 옅은 테두리만, (영역, 열쇠)를 가진 요소마다 단계
  // 상자 — 열쇠는 세기와 같은 elementKey)·depth(정적 페이지만 — 문서 높이 10칸 띠). 새 키가 없는 메시지는 053 그대로다
  function overlay(page) {
    const SIZES = [12, 12, 14, 16, 19, 22] // 꼬리표 글씨 — 단계 0~5(많이 본 곳이 크게)
    const EL_SIZES = [11, 12, 13, 15, 17, 17] // 요소 꼬리표 글씨(065)
    // 단계 1~5 의 채움·테두리 — 옅은 파랑 → 노랑 → 주황 → 빨강, 불투명도 0.18~0.5, 테두리 2~4px. 단계 0 은 회색 점선만
    const FILL = ["", "rgba(96,165,250,.18)", "rgba(250,204,21,.26)", "rgba(251,146,60,.34)", "rgba(239,68,68,.42)", "rgba(220,38,38,.5)"]
    const LINE = ["2px dashed rgba(156,163,175,.9)", "2px solid #60a5fa", "2px solid #facc15", "3px solid #fb923c", "3px solid #ef4444", "4px solid #dc2626"]
    // 요소 상자의 채움(065) — 테두리가 단계를 말하고 칠은 옅게(불투명도 0.10 이하 — 큰 카드·차트의 내용을 가리지 않게)
    const EL_FILL = ["", "rgba(96,165,250,.06)", "rgba(250,204,21,.07)", "rgba(251,146,60,.08)", "rgba(239,68,68,.09)", "rgba(220,38,38,.1)"]
    const HATCH = "repeating-linear-gradient(45deg,rgba(107,114,128,.55) 0 2px,transparent 2px 8px)"
    const FOCUS_MS = 2000
    const MODES = ["area", "element", "depth"]
    const MAX_ELEMENTS = 300 // 받는 요소 값·알리는 요소 수(065)
    const TAG_CHARS = 80 // 요소 꼬리표 글(관리자가 만든다)은 여기서 자른다
    const TOP_TAGS = 20 // 요소 꼬리표는 순위 20 안만 — 나머지는 테두리만
    const PICK = "[data-el]," + ITEMS // 요소 찾기 — 행(row:)도(지금 DOM 에 있는 행에 그린다)
    let values = null // id → 받은 값. null 이면 아직 값이 오지 않았다 — 그리지 않는다
    let label = ""
    let mode = "area"
    let elValues = new Map() // "영역|열쇠" → 받은 요소 값(065)
    let depth = null // 도달 11칸(0~100) 또는 null(065)
    let found = [] // [요소, 영역 id, 열쇠] — 문서 차례, DOM 바뀜에 1초에 한 번 다시 찾는다(065)
    let layer = null
    let frame = 0
    let scanTimer = 0
    let scanAt = 0
    let told = null // 마지막으로 알린 영역 id·요소
    let focusId = ""
    let focusKey = "" // focus 를 받은 "영역|열쇠"(065)
    let focusEl = null
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

    // 크기 있는 영역 안의 열쇠 있는 요소(065) — 문서 차례. 영역은 가장 가까운 조상 영역(세기가 영역마다 찾는 것과 같다)
    const findElements = (areas) => {
      const idOf = new Map()
      areas.forEach((el, id) => idOf.set(el, id))
      const out = []
      for (const el of document.querySelectorAll(PICK)) {
        const area = el.parentElement ? el.parentElement.closest("[data-area]") : null
        const id = area ? idOf.get(area) : undefined
        if (id === undefined) continue
        const key = elementKey(el, area)
        if (key) out.push([el, id, key])
      }
      return out
    }

    // 관리자에게 지금 영역 id 들과 크기 있는 요소의 [영역, 열쇠](행 빼고, 300개까지)를 알린다 — 처음 한 번, 그 뒤로는 모임이
    // 바뀌었을 때만. 요소가 하나도 없으면 elements 를 싣지 않는다(053 꼴 그대로 — 관리자는 그때 '지금 화면에 없음' 을 판정하지 않는다)
    const announce = () => {
      const areas = sized()
      found = findElements(areas)
      if (values) schedule()
      const ids = Array.from(areas.keys())
      const pairs = []
      const seen = new Set()
      for (const [el, id, key] of found) {
        if (pairs.length >= MAX_ELEMENTS) break
        if (key.indexOf("row:") === 0 || seen.has(id + "|" + key)) continue // 행은 재지 않는다(대시보드 표는 행이 많다)
        const r = el.getBoundingClientRect()
        if (!(r.width > 0 && r.height > 0)) continue
        seen.add(id + "|" + key)
        pairs.push([id, key])
      }
      const sig = JSON.stringify([ids, pairs])
      if (sig === told) return
      told = sig
      const ready = { type: "kt-attention-ready", v: 1, page, areas: ids }
      if (pairs.length) ready.elements = pairs
      window.parent.postMessage(ready, ADMIN_ORIGIN)
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

    // 상자 자리 — 화면 위 그 사각형(065 의 영역 테두리·요소 상자)
    const place = (el, rect) =>
      set(el, { position: "absolute", boxSizing: "border-box", left: rect.left + "px", top: rect.top + "px", width: rect.width + "px", height: rect.height + "px" })
    const tagStyle = (size) => ({
      position: "absolute",
      left: "0",
      maxWidth: "320px",
      padding: "1px 5px",
      background: "rgba(17,24,39,.88)",
      color: "#f9fafb",
      font: "600 " + size + "px/1.3 system-ui,-apple-system,sans-serif",
      whiteSpace: "nowrap",
      overflow: "hidden",
      textOverflow: "ellipsis",
    })

    // 요소 보기의 영역(065) — 옅은 회색 테두리만, 꼬리표 없음
    const frameBox = (id, rect, now) => {
      const el = document.createElement("div")
      el.className = "kt-ov-box kt-ov-area"
      el.setAttribute("data-kt-area", id)
      place(el, rect)
      set(el, { border: "1px solid rgba(156,163,175,.45)", backgroundColor: "transparent" })
      if (id === focusId && now < focusUntil) set(el, { borderWidth: "6px", borderStyle: "solid" })
      return el
    }

    // 요소 상자(065) — 053 의 단계 테두리(단계 0 은 회색 점선만)와 옅은 칠. 꼬리표는 따로(아래 elementTag — 열쇠마다 하나)
    const elementBox = (ek, v, rect, focused) => {
      const level = v ? v.level : 0
      const el = document.createElement("div")
      el.className = "kt-ov-el kt-ov-l" + level
      el.setAttribute("data-kt-el", ek)
      place(el, rect)
      set(el, { border: LINE[level], backgroundColor: level ? EL_FILL[level] : "transparent" })
      if (focused) set(el, { borderWidth: "6px", borderStyle: "solid" })
      return el
    }

    // 요소 꼬리표(065) — '#순위 값'(값 = 관리자가 만든 tag, 표본 적음이면 ' · 표본 적음'). 상자 위(화면 위가 모자라면 상자 안 위),
    // 이미 놓은 꼬리표와 겹치면 꼬리표 높이만큼 아래로 세 번까지 — 그래도 겹치면 버린다(null — 상자는 둔다). 너비는 글자 수로 어림한다
    // (재면 꼬리표마다 배치를 다시 해야 한다 — 숫자·영문 0.62em, 그 밖 1em, 좌우 여백 5px)
    const elementTag = (ek, v, rect, placed) => {
      const size = EL_SIZES[v.level]
      const text = "#" + v.rank + " " + v.tag + (v.small ? " · 표본 적음" : "")
      let em = 0
      for (const ch of text) em += ch.charCodeAt(0) < 0x1100 ? 0.62 : 1
      const width = Math.min(320, em * size + 10)
      const height = Math.ceil(size * 1.3) + 2
      const left = rect.left
      let top = rect.top >= height ? rect.top - height : rect.top
      for (let tries = 0; placed.some((p) => left < p.right && left + width > p.left && top < p.bottom && top + height > p.top); tries++) {
        if (tries === 3) return null
        top += height
      }
      placed.push({ left, top, right: left + width, bottom: top + height })
      const tag = document.createElement("span")
      tag.className = "kt-ov-tag kt-ov-eltag"
      tag.setAttribute("data-kt-tag", ek)
      tag.textContent = text
      set(tag, tagStyle(size))
      set(tag, { left: left + "px", top: top + "px", height: height + "px", boxSizing: "border-box" })
      return tag
    }

    // 값이 있는 요소마다 상자(같은 열쇠 여럿은 모두, 행은 지금 DOM 에 있는 것만), 화면 밖은 건너뛴다. focus 받은 요소는 값이 없어도 2초.
    // 꼬리표는 순위 20 안의 (영역, 열쇠)마다 하나 — 그 열쇠의 화면에 보이는 첫 요소에, 순위 차례로 놓는다(높은 순위가 자리를 먼저)
    const elementBoxes = (out, now) => {
      const w = window.innerWidth
      const h = window.innerHeight
      const firsts = new Map()
      for (const [el, id, key] of found) {
        const ek = id + "|" + key
        const v = elValues.get(ek)
        const focused = el === focusEl && ek === focusKey && now < focusUntil
        if ((!v && !focused) || !el.isConnected) continue
        const r = el.getBoundingClientRect()
        if (!(r.width > 0 && r.height > 0) || r.bottom <= 0 || r.right <= 0 || r.top >= h || r.left >= w) continue
        out.push(elementBox(ek, v, r, focused))
        if (v && v.rank !== null && v.rank <= TOP_TAGS && !firsts.has(ek)) firsts.set(ek, [v, r])
      }
      const placed = []
      for (const [ek, [v, r]] of Array.from(firsts).sort((a, b) => a[1][0].rank - b[1][0].rank)) {
        const tag = elementTag(ek, v, r, placed)
        if (tag) out.push(tag)
      }
    }

    // 깊이 띠(065 — 정적 페이지만): 문서 높이를 10칸으로. 칸 j 의 어둡기 = (100 − 도달[j+1]) / 100 × 0.55(안 내려온 만큼 어둡게),
    // 칸 위 경계 왼쪽에 'j×10% · 도달 n%', 문서 끝에 '끝까지 n%'
    const depthTag = (text, edge) => {
      const tag = document.createElement("span")
      tag.className = "kt-ov-tag"
      tag.textContent = text
      set(tag, tagStyle(13))
      set(tag, edge === "top" ? { top: "0" } : { bottom: "0" })
      return tag
    }
    const bands = (out) => {
      const total = document.documentElement.scrollHeight
      if (!(total > 0)) return
      const y = window.scrollY || 0
      for (let j = 0; j < 10; j++) {
        const band = document.createElement("div")
        band.className = "kt-ov-depth"
        band.setAttribute("data-kt-depth", String(j))
        set(band, {
          position: "absolute",
          boxSizing: "border-box",
          left: "0",
          width: "100%",
          top: (total * j) / 10 - y + "px",
          height: total / 10 + "px",
          borderTop: "1px dashed rgba(156,163,175,.9)",
          backgroundColor: "rgba(17,24,39," + (((100 - depth[j + 1]) / 100) * 0.55).toFixed(3) + ")",
        })
        band.appendChild(depthTag(j * 10 + "% · 도달 " + Math.round(depth[j]) + "%", "top"))
        if (j === 9) band.appendChild(depthTag("끝까지 " + Math.round(depth[10]) + "%", "bottom"))
        out.push(band)
      }
    }

    // 그리기 — 스크롤·크기·DOM 바뀜마다 프레임당 한 번. 층은 맨 위 고정·누름 통과. 대시보드의 깊이 보기는 그리지 않는다(065)
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
      if (mode === "depth") {
        if (depth && DEPTH_PAGES.indexOf(page) >= 0) bands(boxes)
      } else {
        for (const [id, el] of sized()) boxes.push((mode === "element" ? frameBox : box)(id, el.getBoundingClientRect(), now))
        if (mode === "element") elementBoxes(boxes, now)
      }
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
    // 요소 값 한 줄(065) — 영역 id 꼴·열쇠 꼴(세기와 같은 validKey)·단계 0~5 정수·순위 1 이상 정수 또는 null·꼬리표 글·참거짓.
    // 하나라도 어긋나면 그 항목을 버린다
    const elementEntry = (a) => {
      if (!a || typeof a !== "object" || typeof a.area !== "string" || !AREA_ID.test(a.area) || !validKey(a.key)) return null
      if (!Number.isInteger(a.level) || a.level < 0 || a.level > 5) return null
      if (!(a.rank === null || (Number.isInteger(a.rank) && a.rank > 0))) return null
      if (typeof a.tag !== "string" || typeof a.small !== "boolean") return null
      return { level: a.level, rank: a.rank, tag: Array.from(a.tag).slice(0, TAG_CHARS).join(""), small: a.small }
    }
    // 도달 11칸(065) — 0~100 의 수 11개, 아니면 null
    const reachOf = (d) => (Array.isArray(d) && d.length === 11 && d.every((n) => finite(n) && n >= 0 && n <= 100) ? d.slice() : null)

    // 가운데로(053·065) — 이 문서 안에서만 굴린다. scrollIntoView 는 같은 사이트의 관리자 문서까지 굴려(틀 밖 페이지가 움직인다)
    // 쓰지 않는다. 요소의 스크롤 상자 조상(overflow auto·scroll 이고 넘치는 것)을 안쪽부터, 마지막에 이 창을 window.scrollTo 로 —
    // 세로는 가운데, 가로는 가까운 쪽(scrollIntoView 의 기본 inline 과 같다 — 넓은 행이 옆으로 밀리지 않게)
    const nearest = (a, b, lo, hi) => {
      if ((a < lo && b > hi) || (a >= lo && b <= hi)) return 0
      return (a < lo) === (b - a <= hi - lo) ? a - lo : b - hi
    }
    const center = (el) => {
      for (let n = el.parentElement; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
        const s = window.getComputedStyle(n)
        const y = /auto|scroll/.test(s.overflowY) && n.scrollHeight > n.clientHeight
        const x = /auto|scroll/.test(s.overflowX) && n.scrollWidth > n.clientWidth
        if (!y && !x) continue
        const box = n.getBoundingClientRect()
        const r = el.getBoundingClientRect()
        const top = box.top + (n.clientTop || 0)
        const left = box.left + (n.clientLeft || 0)
        if (y) n.scrollTop += (r.top + r.bottom) / 2 - (top + n.clientHeight / 2)
        if (x) n.scrollLeft += nearest(r.left, r.right, left, left + n.clientWidth)
      }
      const r = el.getBoundingClientRect()
      const dx = nearest(r.left, r.right, 0, window.innerWidth)
      window.scrollTo((window.scrollX || 0) + dx, (window.scrollY || 0) + (r.top + r.bottom) / 2 - window.innerHeight / 2)
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
        // 065 — 없으면 053 그대로(영역 보기·요소 없음·깊이 없음)
        mode = MODES.indexOf(d.mode) >= 0 ? d.mode : "area"
        elValues = new Map()
        for (const a of Array.isArray(d.elements) ? d.elements.slice(0, MAX_ELEMENTS) : []) {
          const v = elementEntry(a)
          if (v) elValues.set(a.area + "|" + a.key, v)
        }
        depth = reachOf(d.depth)
        schedule()
      } else if (d.type === "kt-attention-focus" && typeof d.id === "string") {
        let target = null
        if (d.key === undefined) {
          target = sized().get(d.id) || null
          if (!target) return
          focusId = d.id
          focusKey = ""
        } else {
          // 065 — 그 영역 안 그 열쇠의 첫 요소(문서 차례)
          const hit = validKey(d.key) ? found.find((f) => f[1] === d.id && f[2] === d.key && f[0].isConnected) : null
          if (!hit) return
          target = hit[0]
          focusId = ""
          focusKey = d.id + "|" + d.key
          focusEl = target
        }
        focusUntil = Date.now() + FOCUS_MS
        schedule()
        window.setTimeout(guard(schedule), FOCUS_MS)
        center(target)
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
      // 문서 높이 바뀜(그림·글꼴이 늦게 와 DOM 은 그대로일 때)에도 다시 그린다 — 깊이 띠(065)
      if (typeof window.ResizeObserver === "function") new window.ResizeObserver(guard(schedule)).observe(document.documentElement)
    } catch (e) {
      // 덮어 보기만 없고 페이지는 그대로
    }
  }
})()
