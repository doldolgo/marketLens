// 선물–현물 갭 탭 (mock) — 스펙 002 §3.7, 구조는 docs/design/reference/tabs/GapTab.tsx.
import { useState } from 'react'
import { STALE_SEC } from '../../shared/config'
import { fmtFunding3, fmtPct, fmtUsdt, pctColor } from '../../shared/format'
import type { Feed } from '../../shared/types'
import {
  GridHeader, gridRow, NumField, Seg, segOpt, SymCell, TableFrame, ToggleBtn,
  bar, count, exTag, hint, label, searchInput, type Header,
} from '../../shared/ui'

/** 심볼 | 현물가 USDT | 갭(가변) | 펀딩비 */
const GRID = '100px 1fr 320px 150px'

interface Combo {
  spotEx: string
  perpEx: string
  gap: number
  funding: number
  price: number
  age: number
}

interface Row {
  sym: string
  entry: Combo | null
  exit: Combo | null
}

type SortCol = 'sym' | 'price' | 'gap' | 'funding'
type Mode = 'entry' | 'exit'

/** 펀딩 주기: Hyperliquid 1h, Bitget 4h(선선갭 탭과 동일), 그 외 8h. 남은 시간은 epoch 기준 UTC 정시 경계. */
function fundingEta(ex: string, now: number): string {
  const cycMs = (ex === 'Hyperliquid' ? 1 : ex === 'Bitget' ? 4 : 8) * 3_600_000
  const remainMin = Math.ceil((cycMs - (now % cycMs)) / 60_000)
  if (remainMin < 60) return `펀딩 ${remainMin}분 후`
  return `펀딩 ${Math.floor(remainMin / 60)}시간 ${remainMin % 60}분 후`
}

export default function GapTab({ feed, now }: { feed: Feed; now: number }) {
  const [q, setQ] = useState('')
  const [mode, setMode] = useState<Mode>('entry')
  const [thr, setThr] = useState(0.5)
  const [only, setOnly] = useState(false)
  const [sort, setSort] = useState<{ col: SortCol; asc: boolean }>({ col: 'gap', asc: false })

  // fail 아닌 현물×선물 모든 조합 중 최대(진입)·최소(정리) 갭을 채택.
  const all: Row[] = feed.markets.map((m) => {
    let entry: Combo | null = null
    let exit: Combo | null = null
    for (const s of m.spot) {
      if (s.status === 'fail') continue
      for (const p of m.perp) {
        if (p.status === 'fail') continue
        const gap = ((1 + p.prem / 100) / (1 + s.off / 100) - 1) * 100
        const c: Combo = {
          spotEx: s.ex,
          perpEx: p.ex,
          gap,
          funding: p.funding,
          price: m.base * (1 + s.off / 100),
          age: Math.max(s.age, p.age),
        }
        if (!entry || gap > entry.gap) entry = c
        if (!exit || gap < exit.gap) exit = c
      }
    }
    return { sym: m.sym, entry, exit }
  })

  const combo = (r: Row): Combo | null => (mode === 'entry' ? r.entry : r.exit)
  const meets = (g: number): boolean => (mode === 'entry' ? g >= thr : g <= -thr)

  const ql = q.trim().toLowerCase()
  let rows = all.filter((r) => r.sym.toLowerCase().includes(ql))
  if (only) {
    rows = rows.filter((r) => {
      const c = combo(r)
      return c !== null && meets(c.gap)
    })
  }

  const mul = sort.asc ? 1 : -1
  rows = [...rows].sort((a, b) => {
    const ca = combo(a)
    const cb = combo(b)
    // fail 행·값 없는 행은 항상 뒤.
    if (!ca && !cb) return a.sym.localeCompare(b.sym)
    if (!ca) return 1
    if (!cb) return -1
    const d =
      sort.col === 'sym' ? a.sym.localeCompare(b.sym)
      : sort.col === 'price' ? ca.price - cb.price
      : sort.col === 'gap' ? ca.gap - cb.gap
      : ca.funding - cb.funding
    return d * mul
  })

  function switchMode(m: Mode) {
    setMode(m)
    // 기준 전환 시 정렬 키도 따라간다: 진입=내림차순, 정리=오름차순.
    setSort({ col: 'gap', asc: m === 'exit' })
  }

  function clickSort(col: string) {
    const c = col as SortCol
    setSort((s) => (s.col === c ? { col: c, asc: !s.asc } : { col: c, asc: c === 'sym' }))
  }

  const headers: Header[] = [
    ['sym', '심볼', 'left'], ['price', '현물가 USDT', 'right'],
    ['gap', mode === 'entry' ? '진입 갭 · 현물 → 선물' : '정리 갭 · 현물 → 선물', 'right'], ['funding', '펀딩비', 'right'],
  ]

  return (
    <>
      <div style={bar}>
        <input className="input" placeholder="심볼 검색" value={q} onChange={(e) => setQ(e.target.value)} style={searchInput} />
        <span style={label}>기준 보기</span>
        <Seg pad="4px 10px" opts={[['entry', '진입 기준'], ['exit', '정리 기준']].map(([id, l]) => segOpt(l, mode === id, () => switchMode(id as Mode)))} />
        <NumField label="하이라이트 임계값" value={thr} step={0.1} onChange={setThr} />
        <ToggleBtn on={only} label="임계 초과만" onClick={() => setOnly(!only)} />
        <span style={hint}>양의 갭 = perp &gt; 현물 → 현물 매수 + 선물 숏 · 음의 갭 = 반대 방향</span>
        <span style={count}>{rows.length} / {all.length} 코인 표시</span>
      </div>

      <TableFrame minWidth={760}>
        <GridHeader cols={GRID} headers={headers} sortKey={sort.col} sortDir={mul} onSort={clickSort} />
        {rows.map((r) => {
          const c = combo(r)
          const stale = c !== null && c.age >= STALE_SEC
          const hot = c !== null && !stale && meets(c.gap)
          return (
            <div key={r.sym} className="hv-row" style={gridRow(GRID, { hot, stale })}>
              <SymCell sym={r.sym} hot={hot} />
              <div style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' }}>
                {c ? fmtUsdt(c.price) : '–'}
              </div>
              <div style={{ padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8 }}>
                <span style={exTag()}>{c ? c.spotEx : '–'} 현물</span>
                <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>→</span>
                <span style={exTag(true)}>{c ? c.perpEx : '–'} 선물</span>
                <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 66, textAlign: 'right', color: c ? pctColor(c.gap) : 'var(--color-neutral-700)' }}>
                  {c ? fmtPct(c.gap) : '–'}
                </span>
              </div>
              <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1 }}>
                <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: c ? pctColor(c.funding) : 'var(--color-neutral-700)' }}>{c ? fmtFunding3(c.funding) : '–'}</span>
                <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>{c ? fundingEta(c.perpEx, now) : ''}</span>
              </div>
            </div>
          )
        })}
      </TableFrame>
    </>
  )
}
