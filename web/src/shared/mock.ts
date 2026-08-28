// mock 데이터 생성 — 스펙 002 §3.6~3.10.
// 전부 문자열 시드 기반 결정론적 난수: 리로드해도 같은 모양. tick 의 흔들림만 비결정적.
import { pick, rng, shuffled, uniform } from './rand'
import type {
  FeedStatus,
  FlowAddr,
  FlowRow,
  HealthEvent,
  HealthEx,
  HealthState,
  MockEvent,
  MockMarket,
  PerpItem,
  SpotItem,
} from './types'

/** 코인 24개 (순서·기준가 USD 고정, §3.6). */
export const COINS: ReadonlyArray<readonly [string, number]> = [
  ['BTC', 118420], ['ETH', 4123], ['XRP', 2.91], ['SOL', 182.4],
  ['DOGE', 0.2134], ['ADA', 0.887], ['TRX', 0.302], ['LINK', 24.6],
  ['AVAX', 41.2], ['DOT', 8.42], ['SUI', 4.05], ['APT', 10.8],
  ['ARB', 1.12], ['OP', 2.31], ['SEI', 0.512], ['ATOM', 9.14],
  ['NEAR', 6.72], ['HBAR', 0.246], ['ETC', 31.5], ['STX', 2.04],
  ['ONDO', 1.42], ['PEPE', 0.0000162], ['WLD', 3.86], ['TIA', 6.18],
]

/** 해외 거래소 6곳. */
export const FX_EXS = ['Binance', 'Bybit', 'Bitget', 'MEXC', 'Gate.io', 'Hyperliquid'] as const

/** 국내 거래소 표시명. */
export const DOM_EXS = ['업비트', '빗썸'] as const

function round3(v: number): number {
  return Math.round(v * 1000) / 1000
}

/** status 분포: 3% fail, 6% stale, 나머지 ok. */
function drawStatus(r: () => number): FeedStatus {
  const v = r()
  if (v < 0.03) return 'fail'
  if (v < 0.09) return 'stale'
  return 'ok'
}

/** age: stale 45~345s, 그 외 0~8s. */
function drawAge(r: () => number, status: FeedStatus): number {
  return status === 'stale' ? uniform(r, 45, 345) : uniform(r, 0, 8)
}

/** 코인별 현물·선물 목록 — (코인 idx + 거래소 idx) % 3 이 1 이 아니면 현물, 2 가 아니면 선물. */
export function buildMarkets(): MockMarket[] {
  return COINS.map(([sym, base], i) => {
    const spot: SpotItem[] = []
    const perp: PerpItem[] = []
    FX_EXS.forEach((ex, j) => {
      const mod = (i + j) % 3
      if (mod !== 1) {
        const r = rng(`gap|${sym}|${ex}|spot`)
        const status = drawStatus(r)
        spot.push({ ex, off: uniform(r, -0.15, 0.15), status, age: drawAge(r, status) })
      }
      if (mod !== 2) {
        const r = rng(`gap|${sym}|${ex}|perp`)
        const status = drawStatus(r)
        perp.push({
          ex,
          prem: uniform(r, -0.7, 0.7),
          funding: round3(uniform(r, -0.04, 0.04)),
          status,
          age: drawAge(r, status),
        })
      }
    })
    return { sym, base, spot, perp }
  })
}

/** 1.5초 tick — fail 그대로, stale 은 age 만 증가(추측), ok 는 25% 확률로 흔들리고 age 0. */
export function tickMarkets(markets: MockMarket[]): void {
  for (const m of markets) {
    for (const s of m.spot) {
      if (s.status === 'fail') continue
      if (s.status === 'stale' || Math.random() >= 0.25) {
        s.age += 1.5
        continue
      }
      s.off += uniform(Math.random, -0.015, 0.015)
      s.age = 0
    }
    for (const p of m.perp) {
      if (p.status === 'fail') continue
      if (p.status === 'stale' || Math.random() >= 0.25) {
        p.age += 1.5
        continue
      }
      p.prem += uniform(Math.random, -0.03, 0.03)
      p.funding = round3(p.funding + uniform(Math.random, -0.002, 0.002))
      p.age = 0
    }
  }
}

// ── 수집 상태 (§3.9, 일 단위 시드) ─────────────────────────────────────────

/** 거래소 8곳 (현물/선물 구독 수 고정). */
export const HEALTH_EXS: ReadonlyArray<readonly [string, number, number]> = [
  ['업비트', 132, 0], ['빗썸', 98, 0], ['Binance', 210, 168], ['Bybit', 174, 152],
  ['Bitget', 140, 128], ['MEXC', 188, 0], ['Gate.io', 164, 96], ['Hyperliquid', 0, 118],
]

export function buildHealth(dateSeed: string): { cards: HealthEx[]; events: HealthEvent[] } {
  const cards: HealthEx[] = HEALTH_EXS.map(([name, spot, perp]) => {
    const r = rng(`health|${dateSeed}|${name}`)
    const v = r()
    const state: HealthState = v < 0.72 ? 'ok' : v < 0.9 ? 'stale' : 'down'
    const failRate = state === 'down' ? 100 : state === 'stale' ? uniform(r, 2, 8) : uniform(r, 0, 0.8)
    const lastRecvSec =
      state === 'down' ? uniform(r, 30 * 60, 90 * 60) : state === 'stale' ? uniform(r, 60, 460) : uniform(r, 0, 5)
    const nGaps = state === 'down' ? 3 : state === 'stale' ? 2 : r() < 0.5 ? 0 : 1
    const gaps = []
    for (let k = 0; k < nGaps; k++) {
      // 구간 길이는 스펙에 없어 4~28분으로 정했다.
      const durMin = uniform(r, 4, 28)
      gaps.push({ startAgoMin: uniform(r, durMin, 1440), durMin })
    }
    // down 은 마지막 수신 이후 지금까지가 결측 구간.
    if (state === 'down') gaps.push({ startAgoMin: lastRecvSec / 60, durMin: lastRecvSec / 60 })
    return { name, spot, perp, state, failRate, lastRecvSec, gaps }
  })

  const r = rng(`health|${dateSeed}|events`)
  const events: HealthEvent[] = []
  for (let k = 0; k < 12; k++) {
    const ex = pick(r, HEALTH_EXS)[0]
    const kv = r()
    const kind = kv < 0.4 ? '재연결' : kv < 0.65 ? '지연' : kv < 0.85 ? 'rate limit' : '구독 실패'
    const ageMin = uniform(r, 2, 1400)
    const n = Math.round(uniform(r, 2, 45))
    const msg =
      kind === '재연결' ? `WebSocket 재연결 완료 · 끊김 ${n}초`
      : kind === '지연' ? `호가 수신 지연 ${n}초 관측`
      : kind === 'rate limit' ? `REST 429 응답 — ${n}초 백오프 후 재개`
      : `${Math.max(1, Math.round(n / 3))}개 마켓 구독 실패 · 재시도 예약`
    events.push({ ageMin, ex, kind, msg })
  }
  events.sort((a, b) => a.ageMin - b.ageMin) // 최신순
  return { cards, events }
}

// ── 입출금 레이더 (§3.10, 시현용) ──────────────────────────────────────────

/** 레이더 코인 가격표 9종 — §3.6 기준가와 같은 값. */
export const FLOW_PRICES: Record<string, number> = {
  BTC: 118420, ETH: 4123, XRP: 2.91, SOL: 182.4, DOGE: 0.2134,
  TRX: 0.302, ADA: 0.887, LINK: 24.6, AVAX: 41.2,
}

/** 주소 15개 라벨 — 스펙 예시 라벨 + 개인지갑·Unknown 반복. */
const FLOW_LABELS = [
  'Wintermute', 'Jump Trading', 'Cumberland', 'GSR Markets',
  'Bybit 출금', 'Upbit 입금집계', '개인지갑', 'Unknown',
  '개인지갑', 'Unknown', '개인지갑', 'Unknown',
  '개인지갑', 'Unknown', '개인지갑',
]

export function buildFlow(): { addrs: FlowAddr[]; rows: FlowRow[] } {
  const coins = Object.keys(FLOW_PRICES)
  const exs = [...DOM_EXS, ...FX_EXS]

  const addrs: FlowAddr[] = FLOW_LABELS.map((label, i) => {
    const r = rng(`flow|addr|${i}`)
    let id = '0x'
    for (let k = 0; k < 40; k++) id += '0123456789abcdef'[Math.floor(r() * 16)]
    const myCoins = shuffled(coins, r).slice(0, 2 + Math.floor(r() * 3))
    const myExs = shuffled(exs, r).slice(0, 2 + Math.floor(r() * 3))
    return { id, label, short: `${id.slice(0, 6)}…${id.slice(-4)}`, coins: myCoins, exs: myExs }
  })

  const rows: FlowRow[] = []
  addrs.forEach((a, i) => {
    const r = rng(`flow|rows|${i}`)
    const n = 2 + Math.floor(r() * 3) // 주소당 2~4행
    for (let k = 0; k < n; k++) {
      // 코인은 주소 취급 코인을 순환 배정해 9종이 모두 화면에 나오게 한다.
      const sym = a.coins[k % a.coins.length]
      const ex = pick(r, a.exs)
      const dir: 'in' | 'out' = r() < 0.6 ? 'in' : 'out'
      const amount = Math.round(uniform(r, 25_000, 17_000_000) / 100) * 100 // 100달러 단위
      const usd = r() < 0.055 ? null : amount
      // usd 가 null(미확인)이어도 수량은 감춰진 금액 기준으로 계산한다 (추측).
      const qty = amount / FLOW_PRICES[sym]
      const state = dir === 'in' ? (r() < 0.5 ? '입금 감지' : 'sweep 확정') : r() < 0.5 ? '브로드캐스트' : '확정'
      const age = uniform(r, 9, 4.7 * 3600)
      rows.push({ addr: a.id, label: a.label, short: a.short, sym, ex, dir, usd, qty, state, age })
    }
  })
  rows.sort((x, y) => x.age - y.age) // 전체 age 오름차순
  return { addrs, rows }
}

// ── 005 mock 사건 목록 (§3.4 events) ───────────────────────────────────────

/** 기간 선택값을 시간으로 — "24h"/"7d"/"2w" 형태를 해석, 그 외는 24h 로 본다 (추측). */
function perHours(per: string): number {
  const m = /^(\d+)\s*([hdw])$/i.exec(per.trim())
  if (!m) return 24
  const n = Number(m[1])
  const u = m[2].toLowerCase()
  return u === 'h' ? n : u === 'd' ? n * 24 : n * 168
}

export function makeEvents(per: string, now: number): MockEvent[] {
  const r = rng(`events|${per}`)
  const hours = perHours(per)
  const n = 18 + Math.floor(r() * 12)
  const out: MockEvent[] = []
  for (let k = 0; k < n; k++) {
    const [sym] = pick(r, COINS)
    const type: 'kimp' | 'rev' = r() < 0.6 ? 'kimp' : 'rev'
    const dom = pick(r, DOM_EXS)
    const start = now - Math.floor(r() * hours * 3_600_000)
    const durMin = Math.round(uniform(r, 3, 180))
    const peak = type === 'kimp' ? round3(uniform(r, 1.5, 6)) : -round3(uniform(r, 1.5, 4))
    out.push({ sym, type, dom, start, durMin, peak })
  }
  out.sort((a, b) => b.start - a.start)
  return out
}
