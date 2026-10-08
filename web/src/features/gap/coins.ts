// 선물–현물 갭 탭 코인 집계·정렬 (스펙 048 §3.6) — 순수 함수. 렌더마다 행을 한 번 훑어 코인별로 누산한다.
import { STALE_SEC } from '../../shared/config'
import { exName } from '../../shared/format'
import type { GapRow } from './types'

export type Mode = 'entry' | 'exit'
export type SortCol = 'sym' | 'price' | 'gap' | 'funding'

/** perp 원천 id → 칩 표시명 (048 §3.1). 모르는 id 는 그대로. 수집 상태 탭 카드의 `Binance perp`(046) 와 다른 자리라 따로 둔다. */
const PERP_NAMES: Record<string, string> = {
  binance_perp: 'Binance',
  bybit_perp: 'Bybit',
  bitget_perp: 'Bitget',
  hyperliquid_perp: 'Hyperliquid',
}
export function perpName(id: string): string {
  return PERP_NAMES[id] ?? id
}

/** 코인 1개의 집계 행 — 보기 기준의 채택 행 하나에서 전부 가져온다. fail 뿐인 코인은 `row` 가 null. */
export interface CoinRow {
  sym: string
  row: GapRow | null
  /** 채택 행의 갭 — 진입 기준 entry, 정리 기준 exit. */
  gap: number
  /** 채택 행의 age 에 마지막 프레임 이후 경과를 더한 값 — stale 판정·표시용. */
  age: number
  stale: boolean
}

interface Acc {
  sym: string
  entryBest: GapRow | null
  exitBest: GapRow | null
}

/**
 * `fail` 아닌 행 중 코인별 `entry` 최대 행(진입)과 `exit` 최소 행(정리)을 고른다. 보기 기준의 채택 행 하나에서
 * 현물가·칩·갭·펀딩·age 를 전부 가져온다 — 다른 행의 값을 섞지 않는다(003 §3.5 와 같은 이유).
 * `elapsedSec` 는 마지막 프레임 이후 경과 초 — 셸 tick 이 age 를 키우는 것과 같은 결과다.
 */
export function aggregateCoins(rows: Iterable<GapRow>, mode: Mode, elapsedSec: number): CoinRow[] {
  const byCoin = new Map<string, Acc>()
  const order: Acc[] = []
  for (const r of rows) {
    let a = byCoin.get(r.sym)
    if (!a) {
      a = { sym: r.sym, entryBest: null, exitBest: null }
      byCoin.set(r.sym, a)
      order.push(a)
    }
    if (r.status === 'fail') continue
    if (!a.entryBest || r.entry > a.entryBest.entry) a.entryBest = r
    if (!a.exitBest || r.exit < a.exitBest.exit) a.exitBest = r
  }
  const entry = mode === 'entry'
  return order.map((a) => {
    let best: GapRow | null
    if (entry) best = a.entryBest
    else best = a.exitBest
    if (!best) return { sym: a.sym, row: null, gap: 0, age: 0, stale: false }
    let gap: number
    if (entry) gap = best.entry
    else gap = best.exit
    const age = best.age + elapsedSec
    return { sym: a.sym, row: best, gap, age, stale: age >= STALE_SEC }
  })
}

const COLLATOR = new Intl.Collator()
const compareText = (a: string, b: string) => COLLATOR.compare(a, b)

/** 정렬 — fail 행(채택 행 없음)은 항상 뒤, 펀딩 null 은 뒤. 같은 값은 심볼 순. */
export function sortCoins(coins: CoinRow[], col: SortCol, asc: boolean): CoinRow[] {
  const dir = asc ? 1 : -1
  const keys = coins.map((c) => sortKey(c, col))
  const idx = coins.map((_, i) => i)
  idx.sort((i, j) => {
    const a = coins[i], b = coins[j]
    if ((a.row === null) !== (b.row === null)) return a.row === null ? 1 : -1
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
  if (!c.row) return null
  if (col === 'price') return c.row.spotPrice
  if (col === 'gap') return c.gap
  return c.row.funding
}

/** 칩 글자 — 현물은 003 §3.4 사전(`exName`), perp 는 위 사전. */
export function spotLabel(r: GapRow): string {
  return exName(r.spot)
}

/** 남은 시간 — `펀딩 N분 후`·`N시간 M분 후`. null 이거나 지났으면 `–` (§3.6). */
export function fundingEta(nextFundingTs: number | null, nowMs: number): string {
  if (nextFundingTs === null) return '–'
  const remainMs = nextFundingTs * 1000 - nowMs
  if (remainMs <= 0) return '–'
  const remainMin = Math.ceil(remainMs / 60_000)
  if (remainMin < 60) return `펀딩 ${remainMin}분 후`
  return `펀딩 ${Math.floor(remainMin / 60)}시간 ${remainMin % 60}분 후`
}
