// KimpTrack 화면 이동 기록(스펙 073) — 방문자의 마우스·손가락 위치, 누른 곳, 스크롤, 보던 영역, 탭 바꿈을 시각과 함께
// 2초마다 묶어 /api/replay 로 보낸다. 봇 판별·차단을 위해 동의와 관계없이 모든 방문을 기록하고(사람 결정 2026-10-18, RECORD_ALL),
// 묶음마다 그 방문의 화면 분석 동의 상태(c — clarity.js 와 같은 판정)를 함께 보낸다. 관리자 화면이 그 상태를 보여 준다.
// 입력값·누른 키·화면 글자·그림은 보내지 않고, 쿠키·저장소에 아무것도 쓰지 않는다(조각 번호는 메모리에만).
// 재생 쪽(스펙 074 §3.4): 관리자 화면이 이 페이지를 kt-replay=1 틀로 띄우면 기록하지 않고, 관리자가 보낸 그 시각의 상태(커서·누름·
// 스크롤·영역)만 그린다. 번들 밖 정적 파일이라 oxlint 대상이 아니다(node --check). 예외를 밖으로 던지지 않는다.
;(() => {
  // 지금 안내 판 — clarity.js·attention.js·방침 동의 스크립트와 같은 값(076). 판을 올리는 PR 은 이 값도 함께 올린다
  const NOTICE_VERSION = "2026-10-24"

  const HOST = "kimptrack.com"
  // 재생 틀을 띄우는 관리자 화면의 출처 — 이 출처의 메시지만 받고 이 출처로만 보낸다(053 과 같은 값).
  // 로컬 확인은 커밋하지 않는 시험 사본에서 이 값과 위 HOST 를 바꾼다(065 §4 의 시험 사본)
  const ADMIN_ORIGIN = "https://admin.kimptrack.com"
  // 동의와 관계없이 모든 방문을 기록한다(봇 판별·차단 — 073 §3.1). false 면 지금 판에 동의한 방문자만 기록한다
  const RECORD_ALL = true
  const KEY = "kt.analytics"
  const VKEY = "kt.analytics.v"
  const UKEY = "kt.analytics.until" // 079 — later 의 다시 물을 시각(epoch ms 글자)
  const LATER_MS = 86400000
  const POST_URL = "/api/replay"
  // 화면 이름 — attention.js 와 같은 목록(052)
  const TABS = ["spread", "history", "gap", "pp", "health", "flow"]
  const STATIC = new Map([
    ["/", "landing"],
    ["/privacy", "privacy"],
    ["/kimp-chart", "kimp-chart"],
    ["/kimp-history", "kimp-history"],
  ])
  const AREA_ID = /^[a-z][a-z0-9-]{0,31}$/
  const MOBILE_WIDTH = 768
  // 화면 크기의 한 변이 이보다 작으면 아직 못 잰 것으로 본다 — 숨은 채 열린 페이지(뒤 탭·미리 띄우기·배치 전 인앱 브라우저)는 0
  const MIN_VIEW = 100

  const MOVE_MS = 100 // 포인터 위치는 0.1초에 한 번까지
  const MOVE_PX = 4 // 직전 보낸 위치에서 이만큼 움직였을 때만
  const SCROLL_MS = 200
  const RESIZE_MS = 500
  const AREA_MS = 1000 // 보던 영역은 1초마다 본다
  const SEND_MS = 2000
  const MAX_EV = 600 // 한 묶음의 사건
  const MAX_BODY = 16384
  const MAX_SEQ = 3600 // 조각의 묶음 수
  const MAX_AGE = 7200000 // 조각은 2시간까지
  const MAX_XY = 20000
  const MAX_SCROLL = 1000000

  const guard = (fn) => (event) => {
    try {
      fn(event)
    } catch (e) {
      // 기록만 없고 페이지는 그대로
    }
  }

  const tabPage = (tab) => "app-" + (TABS.indexOf(tab) >= 0 ? tab : "spread")
  const params = new URLSearchParams(location.search)
  const firstPage = location.pathname.indexOf("/app/") === 0 ? tabPage(params.get("tab")) : STATIC.get(location.pathname) || ""
  let framed = true
  try {
    framed = window.top !== window
  } catch (e) {
    // 다른 출처의 틀 — 틀 안이다
  }

  // 화면 분석 동의 상태(073 §3.4 의 c) — clarity.js 의 판정과 같은 값: granted(지금 판의 동의) · denied(판 있는 거부) ·
  // later(하루 동안 안 보기 — 다시 물을 시각 전) · unset(정하지 않음 — 없음·다른 판·지난 later·판 없는 예전 거부, 저장소를 못 읽음) ·
  // gpc(GPC 신호 — 동의보다 앞선다)
  const consentState = () => {
    if (navigator.globalPrivacyControl === true) return "gpc"
    let value
    let version
    let until
    try {
      value = window.localStorage.getItem(KEY)
      version = window.localStorage.getItem(VKEY)
      until = window.localStorage.getItem(UKEY)
    } catch (e) {
      return "unset"
    }
    if (value === "granted" && version === NOTICE_VERSION) return "granted"
    if (value === "denied" && version) return "denied"
    if (value === "later" && until) {
      const at = Number(until)
      const now = Date.now()
      if (at > now && at <= now + LATER_MS) return "later"
    }
    return "unset"
  }
  const recording = () => RECORD_ALL || consentState() === "granted"

  const clip = (n, hi) => Math.max(0, Math.min(hi, Math.round(Number(n) || 0)))
  const areaIdOf = (el) => {
    const host = el && typeof el.closest === "function" ? el.closest("[data-area]") : null
    const id = host ? host.getAttribute("data-area") : ""
    return id && AREA_ID.test(id) ? id : ""
  }

  // 영역 안에서 스크롤되는 요소들 — 영역 자신부터 문서 순서. 기록(n 정하기)과 재생(n 으로 찾기)이 같은 규칙을 쓴다(073 §3.2)
  const scrollables = (area) => {
    const out = []
    const all = [area, ...area.querySelectorAll("*")]
    for (const el of all) {
      if (el.scrollHeight > el.clientHeight + 1) {
        const oy = window.getComputedStyle(el).overflowY
        if (oy === "auto" || oy === "scroll") out.push(el)
      }
    }
    return out
  }
  const areaEl = (id) => document.querySelector('[data-area="' + id + '"]')

  // 켜는 조건 — 호스트·목록 안 화면. 틀 안이면 재생 쪽(kt-replay=1)만, 아니면 아무것도 하지 않는다
  if (location.hostname !== HOST || !firstPage) return
  if (framed) {
    if (params.get("kt-replay") === "1") player()
    return
  }

  // ── 기록 (073 §3.1~§3.3) ──────────────────────────────────────────────────────────────

  let rec = null // 지금 조각
  let timers = []
  let listening = false
  const pending = new Map() // 미뤄 둔 마지막 값(꼬리) — 열쇠 → 타이머

  const sidOf = () => {
    const bytes = new Uint8Array(8)
    try {
      window.crypto.getRandomValues(bytes)
    } catch (e) {
      for (let i = 0; i < 8; i++) bytes[i] = Math.floor(Math.random() * 256)
    }
    return Array.from(bytes, (b) => (b < 16 ? "0" : "") + b.toString(16)).join("")
  }
  const elapsed = () => Date.now() - rec.t0

  const push = (ev) => {
    if (!rec || rec.ended) return
    if (elapsed() > MAX_AGE) return end()
    ev[0] = clip(elapsed(), MAX_AGE)
    rec.ev.push(ev)
    if (rec.ev.length >= MAX_EV) flush()
  }

  // 꼬리 있는 거르기 — gap 안에 또 오면 마지막 값을 gap 끝에 한 번 보낸다(스크롤·크기의 마지막 값은 반드시 간다)
  const throttle = (key, gap, make) => {
    const last = rec.lastAt.get(key) || 0
    const now = Date.now()
    if (now - last >= gap && !pending.has(key)) {
      rec.lastAt.set(key, now)
      const ev = make()
      if (ev) push(ev)
      return
    }
    if (pending.has(key)) return
    pending.set(
      key,
      window.setTimeout(
        guard(() => {
          pending.delete(key)
          if (!rec) return
          rec.lastAt.set(key, Date.now())
          const ev = make()
          if (ev) push(ev)
        }),
        Math.max(0, gap - (now - last)),
      ),
    )
  }

  const onMove = (event) => {
    if (!rec) return
    rec.mx = event.clientX
    rec.my = event.clientY
    throttle("m", MOVE_MS, () => {
      const x = clip(rec.mx, MAX_XY)
      const y = clip(rec.my, MAX_XY)
      if (rec.px !== null && Math.abs(x - rec.px) < MOVE_PX && Math.abs(y - rec.py) < MOVE_PX) return null
      rec.px = x
      rec.py = y
      return [0, "m", x, y]
    })
  }

  const onClick = (event) => {
    if (!rec) return
    let x = event.clientX
    let y = event.clientY
    // 키보드로 누른 단추(detail 0)는 좌표가 0 — 그 요소의 가운데로
    if (event.detail === 0 && event.target && typeof event.target.getBoundingClientRect === "function") {
      const r = event.target.getBoundingClientRect()
      x = r.left + r.width / 2
      y = r.top + r.height / 2
    }
    push([0, "c", clip(x, MAX_XY), clip(y, MAX_XY), areaIdOf(event.target)])
  }

  const onScroll = (event) => {
    if (!rec) return
    const t = event.target
    if (!t || t === document || t === document.documentElement || t === document.body) {
      throttle("s", SCROLL_MS, () => [0, "s", clip(window.scrollY, MAX_SCROLL)])
      return
    }
    const id = areaIdOf(t)
    if (!id) return
    let key = rec.elKeys.get(t)
    if (!key) {
      const area = areaEl(id)
      const n = area ? scrollables(area).indexOf(t) : -1
      if (n < 0 || n > 199) return
      key = { id, n, k: "e" + rec.elKeys.size }
      rec.elKeys.set(t, key)
    }
    throttle(key.k, SCROLL_MS, () => [0, "e", key.id, key.n, clip(t.scrollTop, MAX_SCROLL)])
  }

  // 화면 크기 r — 직전에 남긴 크기(머리 또는 r)와 다를 때만. 0 은 남기지 않는다(못 잰 크기 — 잴 수 있게 되면 그때 r)
  const sizeEvent = () => {
    const w = clip(window.innerWidth, MAX_XY)
    const h = clip(window.innerHeight, MAX_XY)
    if (!w || !h || (w === rec.vw && h === rec.vh)) return null
    rec.vw = w
    rec.vh = h
    return [0, "r", w, h]
  }
  const noteSize = () => {
    const ev = sizeEvent()
    if (ev) push(ev)
  }

  const onResize = () => {
    if (!rec) return
    throttle("r", RESIZE_MS, sizeEvent)
  }

  const onVisibility = () => {
    if (!rec) return
    const shown = document.visibilityState === "visible"
    push([0, "v", shown ? 1 : 0])
    if (shown) noteSize() // 숨은 채 열렸으면 처음 보일 때 실제 크기
    else flush(true)
  }

  const onTab = (event) => {
    if (!rec) return
    const tab = event && event.detail
    if (TABS.indexOf(tab) < 0) return
    const page = "app-" + tab
    if (page === rec.now) return
    rec.now = page
    push([0, "p", page])
  }

  // 가장 크게 보이는 영역 — 보이는 높이 기준, 같으면 문서에서 앞의 것. 바뀌었을 때만
  const lookArea = () => {
    if (!rec || document.visibilityState !== "visible") return
    // 아직 못 잰 크기면 1초마다 다시 잰다 — 크기가 생겨도 resize·visibilitychange 가 오지 않는 브라우저가 있다
    if (rec.vw < MIN_VIEW || rec.vh < MIN_VIEW) noteSize()
    let best = ""
    let most = 0
    const vh = window.innerHeight
    for (const el of document.querySelectorAll("[data-area]")) {
      const id = el.getAttribute("data-area")
      if (!id || !AREA_ID.test(id)) continue
      const r = el.getBoundingClientRect()
      const seen = Math.min(vh, r.bottom) - Math.max(0, r.top)
      if (seen > most) {
        most = seen
        best = id
      }
    }
    if (best && best !== rec.area) {
      rec.area = best
      push([0, "a", best])
    }
  }

  const bytes = (s) => {
    let n = 0
    for (const ch of s) {
      const c = ch.codePointAt(0)
      n += c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4
    }
    return n
  }

  const post = (body) => {
    let queued = false
    try {
      queued = typeof navigator.sendBeacon === "function" && navigator.sendBeacon(POST_URL, body) === true
    } catch (e) {
      // 아래 fetch 로
    }
    if (queued) return
    const sent = window.fetch(POST_URL, { method: "POST", body, keepalive: true, credentials: "omit" })
    if (sent && typeof sent.catch === "function") sent.catch(() => {})
  }

  // 묶음 하나 — 몸통이 16,384바이트를 넘으면 넘는 사건은 다음 묶음으로 미룬다
  const batch = () => {
    const head = { v: 1, sid: rec.sid, seq: rec.seq, page: rec.page, dev: rec.dev, w: rec.w, h: rec.h, c: consentState(), ev: [] }
    let total = bytes(JSON.stringify(head))
    let n = 0
    for (; n < rec.ev.length && n < MAX_EV; n++) {
      const add = bytes(JSON.stringify(rec.ev[n])) + (n ? 1 : 0)
      if (total + add > MAX_BODY) break
      total += add
    }
    if (!n) n = 1 // 사건 하나가 상한을 넘을 일은 없다(가장 긴 사건 < 100바이트) — 막힘 방지
    head.ev = rec.ev.slice(0, n)
    rec.ev = rec.ev.slice(n)
    return JSON.stringify(head)
  }

  // 보내기 — 직전에 기록 조건을 다시 본다(RECORD_ALL 이 아니면 동의가 꺼질 때 멈춘다). all 이면(숨김·떠남) 쌓인 것을 모두, 아니면 한 묶음
  const flush = (all) => {
    if (!rec || !rec.ev.length) return
    if (!recording()) return stop()
    do {
      if (rec.seq >= MAX_SEQ) return end()
      post(batch())
      rec.seq += 1
    } while (all && rec.ev.length)
  }

  const end = () => {
    if (!rec) return
    rec.ended = true
    rec.ev = []
    for (const id of timers) window.clearInterval(id)
    timers = []
  }
  const stop = () => {
    end()
    rec = null
    pending.forEach((id) => window.clearTimeout(id))
    pending.clear()
  }

  // 기기 — 052 와 같은 768px 기준. 화면 폭을 못 재면(숨은 채 열림) 기기 화면(screen) 폭으로, 그것도 없으면 pc
  const devOf = (w) => {
    let wide = w
    if (wide < MIN_VIEW) {
      try {
        wide = clip(window.screen && window.screen.width, MAX_XY)
      } catch (e) {
        wide = 0
      }
    }
    return wide >= MIN_VIEW && wide < MOBILE_WIDTH ? "mobile" : "pc"
  }

  const start = () => {
    if (rec || !recording()) return
    // 머리 w·h 는 잰 그대로 — 숨은 채 열렸으면 0(모름, 관리자 재생은 첫 r 이나 기기 기본값을 쓴다)
    const w = clip(window.innerWidth, MAX_XY)
    const h = clip(window.innerHeight, MAX_XY)
    rec = {
      sid: sidOf(),
      t0: Date.now(),
      seq: 0,
      page: firstPage,
      now: firstPage,
      dev: devOf(w),
      w,
      h,
      vw: w, // 직전에 남긴 크기(머리 또는 r)
      vh: h,
      ev: [],
      ended: false,
      lastAt: new Map(),
      elKeys: new Map(),
      mx: 0,
      my: 0,
      px: null,
      py: null,
      area: "",
    }
    if (window.scrollY > 0) push([0, "s", clip(window.scrollY, MAX_SCROLL)])
    timers = [window.setInterval(guard(() => flush(false)), SEND_MS), window.setInterval(guard(lookArea), AREA_MS)]
    if (listening) return
    listening = true
    const opts = { capture: true, passive: true }
    document.addEventListener("pointermove", guard(onMove), opts)
    document.addEventListener("pointerdown", guard(onMove), opts)
    document.addEventListener("click", guard(onClick), true)
    document.addEventListener("scroll", guard(onScroll), opts)
    document.addEventListener("visibilitychange", guard(onVisibility))
    window.addEventListener("resize", guard(onResize))
    window.addEventListener("pagehide", guard(() => flush(true)))
    if (firstPage.indexOf("app-") === 0) window.addEventListener("kt:tab", guard(onTab))
  }

  try {
    if (recording()) start()
    // RECORD_ALL 이 아니면 동의가 페이지 중간에 켜질 때부터 — 같은 탭의 저장(clarity.js 의 kt:clarity)과 다른 탭의 저장
    window.addEventListener("kt:clarity", guard(start))
    window.addEventListener("storage", guard(start))
  } catch (e) {
    // 기록만 없고 페이지는 그대로
  }

  // ── 재생 쪽 (074 §3.4) ─────────────────────────────────────────────────────────────────
  // 관리자 출처의 메시지만 받아 그 시각의 상태를 그린다 — 커서·누름 원·영역 테두리·문서와 영역 안 스크롤. 사건을 모으지 않는다.
  // 누름·입력·휠·터치 스크롤은 막는다(관리자가 화면을 움직이지 않게 — 화면은 재생이 정한다)
  function player() {
    const Z = "2147483000"
    let layer = null
    let cursor = null
    let outline = null
    const found = new Map() // "영역|n" → 요소(다시 그릴 때 찾기를 줄인다)

    const set = (el, props) => {
      for (const k of Object.keys(props)) el.style[k] = props[k]
    }
    const finite = (v) => typeof v === "number" && isFinite(v)

    const ensure = () => {
      if (layer && layer.isConnected) return
      layer = document.createElement("div")
      set(layer, { position: "fixed", inset: "0", pointerEvents: "none", zIndex: Z, overflow: "hidden" })
      outline = document.createElement("div")
      set(outline, { position: "fixed", display: "none", border: "2px solid rgba(145,132,217,.85)", borderRadius: "6px", background: "rgba(145,132,217,.06)", pointerEvents: "none" })
      cursor = document.createElement("div")
      set(cursor, { position: "fixed", display: "none", pointerEvents: "none", width: "0", height: "0" })
      layer.append(outline, cursor)
      ;(document.body || document.documentElement).append(layer)
    }

    // 커서 모양 — 마우스는 흰 테두리 검은 화살표, 손가락은 반투명 원
    let shape = ""
    const drawCursor = (touch) => {
      const want = touch ? "touch" : "arrow"
      if (shape === want) return
      shape = want
      cursor.replaceChildren()
      if (touch) {
        const dot = document.createElement("div")
        set(dot, { position: "absolute", left: "-14px", top: "-14px", width: "28px", height: "28px", borderRadius: "50%", background: "rgba(80,80,90,.35)", border: "2px solid rgba(255,255,255,.9)", boxShadow: "0 1px 4px rgba(0,0,0,.4)" })
        cursor.append(dot)
        return
      }
      const NS = "http://www.w3.org/2000/svg"
      const svg = document.createElementNS(NS, "svg")
      svg.setAttribute("width", "22")
      svg.setAttribute("height", "28")
      svg.setAttribute("viewBox", "0 0 22 28")
      const path = document.createElementNS(NS, "path")
      path.setAttribute("d", "M2 2 L2 23 L7.5 17.5 L11 26 L14.5 24.5 L11 16.5 L19 16.5 Z")
      path.setAttribute("fill", "#111")
      path.setAttribute("stroke", "#fff")
      path.setAttribute("stroke-width", "2")
      path.setAttribute("stroke-linejoin", "round")
      svg.append(path)
      set(svg, { position: "absolute", left: "-2px", top: "-2px", filter: "drop-shadow(0 1px 2px rgba(0,0,0,.5))" })
      cursor.append(svg)
    }

    const ripple = (x, y) => {
      const r = document.createElement("div")
      set(r, { position: "fixed", left: x - 18 + "px", top: y - 18 + "px", width: "36px", height: "36px", borderRadius: "50%", border: "3px solid rgba(239,68,68,.9)", background: "rgba(239,68,68,.18)", pointerEvents: "none", transition: "transform .6s ease-out, opacity .6s ease-out", transform: "scale(.4)", opacity: "1" })
      layer.append(r)
      window.requestAnimationFrame(() => set(r, { transform: "scale(1.6)", opacity: "0" }))
      window.setTimeout(guard(() => r.remove()), 650)
    }

    const scrollIn = (id, n, top) => {
      const k = id + "|" + n
      let el = found.get(k)
      if (!el || !el.isConnected) {
        const area = areaEl(id)
        el = area ? scrollables(area)[n] : null
        if (!el) return
        found.set(k, el)
      }
      el.scrollTop = top
    }

    const onMessage = (event) => {
      if (event.origin !== ADMIN_ORIGIN || event.source !== window.parent) return
      const d = event.data
      if (!d || typeof d !== "object" || d.type !== "kt-replay" || d.v !== 1) return
      ensure()
      if (finite(d.sy) && d.sy >= 0) window.scrollTo(0, Math.min(d.sy, MAX_SCROLL))
      if (Array.isArray(d.els)) {
        for (const e of d.els.slice(0, 40)) {
          if (Array.isArray(e) && typeof e[0] === "string" && AREA_ID.test(e[0]) && Number.isInteger(e[1]) && e[1] >= 0 && e[1] < 200 && finite(e[2]) && e[2] >= 0) {
            scrollIn(e[0], e[1], Math.min(e[2], MAX_SCROLL))
          }
        }
      }
      if (finite(d.x) && finite(d.y)) {
        const x = clip(d.x, MAX_XY)
        const y = clip(d.y, MAX_XY)
        drawCursor(d.touch === true)
        set(cursor, { display: "block", left: x + "px", top: y + "px" })
        if (d.click === true) ripple(x, y)
      } else {
        set(cursor, { display: "none" })
      }
      const box = typeof d.area === "string" && AREA_ID.test(d.area) ? areaEl(d.area) : null
      if (box) {
        const r = box.getBoundingClientRect()
        set(outline, { display: "block", left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" })
      } else {
        set(outline, { display: "none" })
      }
    }

    // 누름·입력·휠·터치 스크롤 막기 — 재생 화면을 관리자가 움직이지 않게
    const block = (event) => {
      event.preventDefault()
      event.stopPropagation()
    }

    let told = 0
    const announce = () => {
      told += 1
      window.parent.postMessage({ type: "kt-replay-ready", v: 1, page: firstPage }, ADMIN_ORIGIN)
    }

    try {
      window.addEventListener("message", guard(onMessage))
      for (const type of ["click", "mousedown", "pointerdown", "touchstart", "keydown", "submit", "auxclick", "contextmenu"]) {
        document.addEventListener(type, guard(block), true)
      }
      for (const type of ["wheel", "touchmove"]) document.addEventListener(type, guard(block), { capture: true, passive: false })
      if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", guard(announce))
      else announce()
      // 대시보드는 React 가 뒤에 그린다 — 영역이 처음 생기면 한 번 더 알린다
      if (!document.querySelector("[data-area]")) {
        const mo = new window.MutationObserver(
          guard(() => {
            if (!document.querySelector("[data-area]")) return
            mo.disconnect()
            if (told < 2) announce()
          }),
        )
        mo.observe(document.documentElement, { childList: true, subtree: true })
      }
    } catch (e) {
      // 재생만 없고 페이지는 그대로
    }
  }
})()
