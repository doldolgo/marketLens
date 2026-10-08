// KimpTrack 화면 영역 이용 통계 — 화면마다 data-area 영역의 보인 시간·1초 이상 보임·클릭 수를 세어 숨길 때 보낸다 (스펙 052).
// 화면 분석에 동의한 방문자만(clarity.js 와 같은 판정 — 지금 판의 granted·GPC 아님). 마우스 위치·좌표·스크롤 경로·글자·입력값은
// 보내지 않고, 쿠키·저장소에 아무것도 쓰지 않는다(방문자 식별 없음). 번들 밖 정적 파일이라 oxlint 대상이 아니다(node --check).
// 예외를 밖으로 던지지 않는다 — 실패하면 통계만 없고 페이지는 그대로다.
;(() => {
  // 지금 안내 판 — clarity.js 의 NOTICE_VERSION 과 같은 값(server/tests/test_clarity.py·test_attention.py 가 묶는다).
  // 판을 올리는 PR 은 이 값도 함께 올린다 — 예전 판의 granted 는 동의가 아니다
  const NOTICE_VERSION = "2026-10-09"

  const HOST = "kimptrack.com" // www 는 apex 로 301 이고 저장값이 출처마다 따로라 이 호스트 하나만
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
  // 켜는 조건(§3.2) — 호스트·틀 밖·덮어 보기 아님·목록 안 화면. 하나라도 아니면 아무것도 하지 않는다
  if (location.hostname !== HOST || framed || params.get("kt-overlay") === "1" || !firstPage) return

  // 동의 상태 '켬' — 지금 판의 granted 이고 GPC 가 아니다. 저장소를 읽다 예외면 끔
  const consented = () => {
    if (navigator.globalPrivacyControl === true) return false
    try {
      return window.localStorage.getItem(KEY) === "granted" && window.localStorage.getItem(VKEY) === NOTICE_VERSION
    } catch (e) {
      return false
    }
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
})()
