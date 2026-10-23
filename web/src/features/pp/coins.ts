// 선선갭 탭 코인 집계·정렬 (스펙 049 §3.5) — 순수 함수. 렌더마다 행을 한 번 훑어 코인별로 누산한다.
import { STALE_SEC } from '../../shared/config'
import type { PpRow } from './types'

export type SortCol = 'sym' | 'gap' | 'fund'

/** perp 원천 순서·칩 표시명 (046 §3.1). 모르는 id 는 그대로 쓰고 순서는 맨 뒤. */
const PERP_ORDER = ['binance_perp', 'bybit_perp', 'bitget_perp', 'hyperliquid_perp']
const PERP_NAMES: Record<string, string> = {
  binance_perp: 'Binance',
  bybit_perp: 'Bybit',
  bitget_perp: 'Bitget',
  hyperliquid_perp: 'Hyperliquid',
}
export function perpName(id: string): string {
  return PERP_NAMES[id] ?? id
}
function perpRank(id: string): number {
  const i = PERP_ORDER.indexOf(id)
  return i < 0 ? PERP_ORDER.length : i
}

/** 시간당 펀딩 %/h = 한 주기 퍼센트 ÷ 주기 시간. 펀딩이 없거나 주기가 없거나 0 이면 null — 거래소별 주기 상수는 쓰지 않는다. */
export function fundingPerHour(fund: number | null, interval: number | null): number | null {
  if (fund === null || interval === null || interval === 0) return null
  return fund / interval
}

/** 한 행의 한 방향 — 롱 원천·숏 원천·가격갭·펀딩갭(숏 쪽 시간당 − 롱 쪽 시간당, 양수면 그 포지션이 시간당 받는다). */
export interface Direction {
  row: PpRow
  long: string
  short: string
  gap: number
  fundGap: number | null
}

/** 코인 1개의 집계 행 — 가격갭 열·칩·펀딩갭 열·age 는 전부 채택 가격갭 방향 하나에서. fail 뿐인 코인은 `best` 가 null. */
export interface CoinRow {
  sym: string
  best: Direction | null
  /** 펀딩갭(null 제외) 최대 방향의 펀딩갭 — `임계 초과만` 토글의 연환산 비교에만 쓴다. 없으면 null. */
  maxFundGap: number | null
  /** 채택 방향 행의 age 에 마지막 프레임 이후 경과를 더한 값 — stale 판정용. */
  age: number
  stale: boolean
}

interface Acc {
  sym: string
  best: Direction | null
  maxFundGap: number | null
}

/** 가격갭이 더 큰가 — 같으면 롱 원천이 원천 순서에서 앞선 쪽, 그것도 같으면 숏 원천이 앞선 쪽. */
function beats(gap: number, long: string, short: string, cur: Direction): boolean {
  if (gap !== cur.gap) return gap > cur.gap
  const dl = perpRank(long) - perpRank(cur.long)
  if (dl !== 0) return dl < 0
  return perpRank(short) < perpRank(cur.short)
}

/**
 * `fail` 아닌 행마다 두 방향을 본다 — `longA` 방향은 (a 롱, b 숏), `longB` 방향은 (b 롱, a 숏). 코인별로 가격갭 최대 방향을
 * 채택하고(칩·가격갭·펀딩갭 열·age 가 모두 이 방향에서), 펀딩갭 최대값은 따로 둔다. `elapsedSec` 는 마지막 프레임 이후
 * 경과 초 — 셸 tick 이 age 를 키우는 것과 같은 결과다.
 */
export function aggregateCoins(rows: Iterable<PpRow>, elapsedSec: number): CoinRow[] {
  const byCoin = new Map<string, Acc>()
  const order: Acc[] = []
  for (const r of rows) {
    let acc = byCoin.get(r.sym)
    if (!acc) {
      acc = { sym: r.sym, best: null, maxFundGap: null }
      byCoin.set(r.sym, acc)
      order.push(acc)
    }
    if (r.status === 'fail') continue
    const ha = fundingPerHour(r.fundA, r.intA)
    const hb = fundingPerHour(r.fundB, r.intB)
    let fundA: number | null = null // a 롱·b 숏 방향의 펀딩갭
    let fundB: number | null = null
    if (ha !== null && hb !== null) {
      fundA = hb - ha
      fundB = ha - hb
    }
    if (!acc.best || beats(r.longA, r.a, r.b, acc.best)) {
      acc.best = { row: r, long: r.a, short: r.b, gap: r.longA, fundGap: fundA }
    }
    if (beats(r.longB, r.b, r.a, acc.best)) {
      acc.best = { row: r, long: r.b, short: r.a, gap: r.longB, fundGap: fundB }
    }
    // 두 방향의 펀딩갭은 부호만 다르다 — 큰 쪽이 그 행의 최대
    if (fundA !== null) {
      const f = Math.max(fundA, fundB as number)
      if (acc.maxFundGap === null || f > acc.maxFundGap) acc.maxFundGap = f
    }
  }
  return order.map((acc) => {
    if (!acc.best) return { sym: acc.sym, best: null, maxFundGap: null, age: 0, stale: false }
    const age = acc.best.row.age + elapsedSec
    return { sym: acc.sym, best: acc.best, maxFundGap: acc.maxFundGap, age, stale: age >= STALE_SEC }
  })
}

/** `임계 초과만` — 가격갭 ≥ 가격 임계 **또는** 연환산 펀딩갭(최대 펀딩갭 방향 × 24 × 365) ≥ 펀딩 임계 (002 §3.8). */
export function overThreshold(c: CoinRow, thrPrice: number, thrFundingYear: number): boolean {
  if (!c.best) return false
  if (c.best.gap >= thrPrice) return true
  return c.maxFundGap !== null && c.maxFundGap * 24 * 365 >= thrFundingYear
}

const COLLATOR = new Intl.Collator()
const compareText = (a: string, b: string) => COLLATOR.compare(a, b)

/** 정렬 — fail 행(채택 방향 없음)은 항상 뒤, 펀딩갭 null 은 뒤. 같은 값은 심볼 순. */
export function sortCoins(coins: CoinRow[], col: SortCol, asc: boolean): CoinRow[] {
  const dir = asc ? 1 : -1
  const keys = coins.map((c) => sortKey(c, col))
  const idx = coins.map((_, i) => i)
  idx.sort((i, j) => {
    const a = coins[i], b = coins[j]
    if ((a.best === null) !== (b.best === null)) return a.best === null ? 1 : -1
    const ka = keys[i], kb = keys[j]
    if (ka === null && kb === null) return compareText(a.sym, b.sym)
    if (ka === null) return 1
    if (kb === null) return -1
    let d: number
    if (typeof ka === 'string') d = compareText(ka, kb as string)
    else d = ka - (kb as number)
    return d * dir || compareText(a.sym, b.sym)
  })
  return idx.map((i) => coins[i])
}

function sortKey(c: CoinRow, col: SortCol): string | number | null {
  if (col === 'sym') return c.sym
  if (!c.best) return null
  if (col === 'gap') return c.best.gap
  return c.best.fundGap
}
