// 선선갭(선물–선물 갭) 탭 (mock) — 스펙 002 §3.8, 구조는 docs/design/reference/tabs/PPTab.tsx.
import { useState } from 'react'
import { STALE_SEC } from '../../shared/config'
import { fmtFundingHr, fmtPct, pctColor } from '../../shared/format'
import type { Feed } from '../../shared/types'
import {
  GridHeader, gridRow, NumField, SymCell, TableFrame, ToggleBtn,
  bar, count, exTag, hint, searchInput, type Header,
} from '../../shared/ui'

/** 심볼 | 가격갭(가변) | 펀딩갭. 행 높이는 참조대로 48px(2줄 셀). */
const GRID = '100px 2.2fr 150px'
const ROW_H = 48

/** 펀딩 주기(시간): Hyperliquid 1, Bitget 4, 그 외 8 — 시간당 정규화용. */
function cyc(ex: string): number {
  return ex === 'Hyperliquid' ? 1 : ex === 'Bitget' ? 4 : 8
}

interface Pair {
  lowEx: string
  highEx: string
  gap: number
  fundDiffHr: number
  age: number
}

interface Row {
  sym: string
  pair: Pair | null
  maxFundHr: number
}

type SortCol = 'sym' | 'gap' | 'fund'

/** 거래소 칩 — 선선갭은 양쪽 다 accent, 참조대로 한 치수 작게. */

export default function PpTab({ feed }: { feed: Feed }) {
  const [q, setQ] = useState('')
  const [thrP, setThrP] = useState(0.3)
  const [thrF, setThrF] = useState(20)
  const [only, setOnly] = useState(false)
  const [sort, setSort] = useState<{ col: SortCol; asc: boolean }>({ col: 'gap', asc: false })

  // fail 아닌 선물 거래소 모든 쌍 — 최대 가격갭 쌍과 최대 펀딩갭(시간당) 값을 각각 채택.
  const all: Row[] = feed.markets.map((m) => {
    const perps = m.perp.filter((p) => p.status !== 'fail')
    let pair: Pair | null = null
    let maxFundHr = 0
    for (let i = 0; i < perps.length; i++) {
      for (let j = i + 1; j < perps.length; j++) {
        const a = perps[i]
        const b = perps[j]
        const [low, high] = a.prem <= b.prem ? [a, b] : [b, a]
        const gap = ((1 + high.prem / 100) / (1 + low.prem / 100) - 1) * 100
        const fundDiffHr = high.funding / cyc(high.ex) - low.funding / cyc(low.ex)
        maxFundHr = Math.max(maxFundHr, Math.abs(fundDiffHr))
        if (!pair || gap > pair.gap) {
          pair = { lowEx: low.ex, highEx: high.ex, gap, fundDiffHr, age: Math.max(a.age, b.age) }
        }
      }
    }
    return { sym: m.sym, pair, maxFundHr }
  })

  const ql = q.trim().toLowerCase()
  let rows = all.filter((r) => r.sym.toLowerCase().includes(ql))
  if (only) {
    // 가격갭 ≥ 임계 또는 연환산(×24×365) 펀딩갭 ≥ 임계.
    rows = rows.filter((r) => r.pair !== null && (r.pair.gap >= thrP || r.maxFundHr * 24 * 365 >= thrF))
  }

  const mul = sort.asc ? 1 : -1
  rows = [...rows].sort((a, b) => {
    if (!a.pair && !b.pair) return a.sym.localeCompare(b.sym)
    if (!a.pair) return 1
    if (!b.pair) return -1
    const d =
      sort.col === 'sym' ? a.sym.localeCompare(b.sym)
      : sort.col === 'gap' ? a.pair.gap - b.pair.gap
      : Math.abs(a.pair.fundDiffHr) - Math.abs(b.pair.fundDiffHr)
    return d * mul
  })

  function clickSort(col: string) {
    const c = col as SortCol
    setSort((s) => (s.col === c ? { col: c, asc: !s.asc } : { col: c, asc: c === 'sym' }))
  }

  const headers: Header[] = [['sym', '심볼', 'left'], ['gap', '가격갭', 'right'], ['fund', '펀딩갭', 'right']]

  return (
    <>
      <div style={bar}>
        <input className="input" placeholder="심볼 검색" value={q} onChange={(e) => setQ(e.target.value)} style={searchInput} />
        <NumField label="가격갭 임계값" value={thrP} step={0.1} onChange={setThrP} />
        <NumField label="펀딩갭 임계값 · 연" value={thrF} step={5} onChange={setThrF} />
        <ToggleBtn on={only} label="임계 초과만" onClick={() => setOnly(!only)} />
        <span style={hint}>perp ↔ perp · 싼 쪽 롱 + 비싼 쪽 숏 · 펀딩비갭은 시간당 정규화</span>
        <span style={count}>{rows.length} / {all.length} 코인 표시</span>
      </div>

      <TableFrame minWidth={760}>
        <GridHeader cols={GRID} headers={headers} sortKey={sort.col} sortDir={mul} onSort={clickSort} />
        {rows.map((r) => {
          const p = r.pair
          const stale = p !== null && p.age >= STALE_SEC
          const hot = p !== null && !stale && p.gap >= thrP
          return (
            <div key={r.sym} className="hv-row" style={gridRow(GRID, { hot, stale, height: ROW_H })}>
              <SymCell sym={r.sym} hot={hot} />
              <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={exTag(true, true)}>{p ? p.lowEx : '–'}</span>
                  <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>↔</span>
                  <span style={exTag(true, true)}>{p ? p.highEx : '–'}</span>
                  <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 84, textAlign: 'right', color: p ? pctColor(p.gap) : 'var(--color-neutral-700)' }}>
                    {p ? fmtPct(p.gap) : '–'}
                  </span>
                </div>
                <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', whiteSpace: 'nowrap' }}>
                  {p ? `${p.lowEx} 롱 / ${p.highEx} 숏` : '–'}
                </span>
              </div>
              <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1 }}>
                <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: p ? pctColor(p.fundDiffHr) : 'var(--color-neutral-700)' }}>{p ? fmtFundingHr(p.fundDiffHr) : '–'}</span>
                <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>해당 조합 펀딩갭</span>
              </div>
            </div>
          )
        })}
      </TableFrame>
    </>
  )
}
