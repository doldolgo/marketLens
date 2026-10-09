// 선물–현물 갭 탭 — /ws/gap 실데이터 (스펙 048 §3.6, 화면·조작은 002 §3.7, 구조는 docs/design/reference/tabs/GapTab.tsx).
import { useState } from 'react'
import { fmtFunding3, fmtPct, fmtUsdt, pctColor } from '../../shared/format'
import {
  Empty, GridHeader, gridRow, NumField, Seg, segOpt, SymCell, TableFrame, ToggleBtn,
  bar, count, exTag, hint, label, searchInput, type Header,
} from '../../shared/ui'
import { bool, num, oneOf, sortOf, useUrlState } from '../../shared/urlState'
import { useGapSocket } from './api'
import { aggregateCoins, fundingEta, perpName, sortCoins, spotLabel, type Mode, type SortCol } from './coins'

/** 심볼 | 현물가 USDT | 갭(가변) | 펀딩비 */
const GRID = '100px 1fr 320px 150px'

export default function GapTab({ active, now }: { active: boolean; now: number }) {
  // 탭이 보이는 동안만 구독 — 숨으면 닫고 표는 메모리에 둔다, 다시 보이면 snapshot 으로 통째 교체 (§3.6)
  const table = useGapSocket(active)
  // 필터·정렬은 URL 쿼리(g.*)에 실려 새로고침해도 같은 화면 (002 §3.5). 검색어는 메모리만 (033)
  const [q, setQ] = useState('')
  const [mode, setMode] = useUrlState<Mode>('g.mode', 'entry', oneOf(['entry', 'exit']))
  const [thr, setThr] = useUrlState('g.thr', 0.5, num)
  const [only, setOnly] = useUrlState('g.only', false, bool)
  const [sort, setSort] = useUrlState<{ col: SortCol; asc: boolean }>('g.sort', { col: 'gap', asc: false }, sortOf(['sym', 'price', 'gap', 'funding']))

  // 마지막 프레임 이후 경과 초 — 셸의 1.5초 tick(now)으로 age 가 자라 delta 가 끊기면 5초 뒤 전 행이 stale
  let elapsedSec = 0
  if (table.receivedAt > 0) elapsedSec = Math.max(0, (now - table.receivedAt) / 1000)
  const all = aggregateCoins(table.rows.values(), mode, elapsedSec)

  const meets = (g: number): boolean => {
    if (mode === 'entry') return g >= thr
    return g <= -thr
  }

  const ql = q.trim().toLowerCase()
  let rows = all.filter((r) => r.sym.toLowerCase().includes(ql))
  if (only) rows = rows.filter((r) => r.row !== null && meets(r.gap))
  rows = sortCoins(rows, sort.col, sort.asc)
  let mul = -1
  if (sort.asc) mul = 1

  function switchMode(m: Mode) {
    setMode(m)
    // 기준 전환 시 정렬 키도 따라간다: 진입=내림차순, 정리=오름차순.
    setSort({ col: 'gap', asc: m === 'exit' })
  }

  function clickSort(col: string) {
    const c = col as SortCol
    setSort((s) => {
      if (s.col === c) return { col: c, asc: !s.asc }
      return { col: c, asc: c === 'sym' }
    })
  }

  let gapHeader = '진입 갭 · 현물 → 선물'
  if (mode === 'exit') gapHeader = '정리 갭 · 현물 → 선물'
  const headers: Header[] = [
    ['sym', '심볼', 'left'], ['price', '현물가 USDT', 'right'],
    ['gap', gapHeader, 'right'], ['funding', '펀딩비', 'right'],
  ]

  return (
    <>
      <div data-area="filters" style={bar}>
        <input className="input" placeholder="심볼 검색" value={q} onChange={(e) => setQ(e.target.value)} style={searchInput} />
        <span style={label}>기준 보기</span>
        <Seg pad="4px 10px" opts={[['entry', '진입 기준'], ['exit', '정리 기준']].map(([id, l]) => segOpt(l, mode === id, () => switchMode(id as Mode)))} />
        <NumField label="하이라이트 임계값" value={thr} step={0.1} onChange={setThr} />
        <ToggleBtn on={only} label="임계 초과만" onClick={() => setOnly(!only)} />
        <span style={hint}>양의 갭 = perp &gt; 현물 → 현물 매수 + 선물 숏 · 음의 갭 = 반대 방향</span>
        <span style={count}>{rows.length} / {all.length} 코인 표시</span>
      </div>

      <TableFrame minWidth={760} area="table">
        <GridHeader cols={GRID} headers={headers} sortKey={sort.col} sortDir={mul} onSort={clickSort} />
        {all.length === 0 && <Empty>백엔드에서 갭 표를 받는 중입니다…</Empty>}
        {rows.map((r) => {
          const c = r.row
          const hot = c !== null && !r.stale && meets(r.gap)
          let priceText = '–'
          if (c && c.spotPrice > 0) priceText = fmtUsdt(c.spotPrice)
          let fundingText = '–'
          let fundingColor = 'var(--color-neutral-700)'
          if (c && c.funding !== null) {
            fundingText = fmtFunding3(c.funding)
            fundingColor = pctColor(c.funding, 3)
          }
          let gapColor = 'var(--color-neutral-700)'
          if (c) gapColor = pctColor(r.gap)
          return (
            <div key={r.sym} className="hv-row" style={gridRow(GRID, { hot, stale: r.stale })}>
              <SymCell sym={r.sym} hot={hot} />
              <div style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' }}>
                {priceText}
              </div>
              <div style={{ padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8 }}>
                <span style={exTag()}>{c ? spotLabel(c) : '–'} 현물</span>
                <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>→</span>
                <span style={exTag(true)}>{c ? perpName(c.perp) : '–'} 선물</span>
                <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 66, textAlign: 'right', color: gapColor }}>
                  {c ? fmtPct(r.gap) : '–'}
                </span>
              </div>
              <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1 }}>
                <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: fundingColor }}>{fundingText}</span>
                <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>{c ? fundingEta(c.nextFundingTs, now) : ''}</span>
              </div>
            </div>
          )
        })}
      </TableFrame>
    </>
  )
}
