// mock 데이터 생성 — 스펙 002 §3.6~3.9.
// 전부 문자열 시드 기반 결정론적 난수: 리로드해도 같은 모양. tick 의 흔들림만 비결정적.
import { rng, uniform } from './rand'
import type {
  FeedStatus,
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

/** 해외 거래소 7곳 — 스프레드 표의 "비교 해외 거래소" 체크박스 목록이기도 하다(이 목록 밖 이름은 URL 필터에서 버린다). OKX 는 045. */
export const FX_EXS = ['Binance', 'Bybit', 'Bitget', 'OKX', 'MEXC', 'Gate.io', 'Hyperliquid'] as const

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

/** 코인별 현물·선물 목록 — (코인 idx + 거래소 idx) % 3 이 1 이 아니면 현물, 2 가 아니면 선물.
 * 단 Hyperliquid 는 perp 전용이라 현물 목록에서 뺀다. */
export function buildMarkets(): MockMarket[] {
  return COINS.map(([sym, base], i) => {
    const spot: SpotItem[] = []
    const perp: PerpItem[] = []
    FX_EXS.forEach((ex, j) => {
      const mod = (i + j) % 3
      if (mod !== 1 && ex !== 'Hyperliquid') {
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

// ── 005 mock 사건 목록 (§3.4 events) ───────────────────────────────────────

/** 기간 선택값을 시간으로 — "24h"/"7d"/"2w" 형태를 해석, 그 외는 24h 로 본다 (추측). */
