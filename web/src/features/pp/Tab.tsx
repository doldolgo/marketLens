// 선선갭(선물–선물 갭) 탭 (mock) — 스펙 002 §3.8, 구조는 docs/design/reference/tabs/PPTab.tsx.
// 폰(폭 719px 이하)은 필터 접기·정렬 줄·3줄 카드 행 (072 §3.4·§3.5).
import { useState } from 'react'
import { STALE_SEC } from '../../shared/config'
import { fmtFundingHr, fmtPct, pctColor } from '../../shared/format'
import type { Feed } from '../../shared/types'
import {
  FoldButton, GridHeader, gridRow, NumField, SortBar, SymCell, TableFrame, ToggleBtn,
  bar, cardLeft, cardRight, cardRow, count, exTag, foldPanel, hint, phoneBar, phoneSearch, searchInput, type Header,
} from '../../shared/ui'
import { bool, num, sortOf, useUrlState } from '../../shared/urlState'
import { useViewport } from '../../shared/viewport'

/** 심볼 | 가격갭(가변) | 펀딩갭. 행 높이는 참조대로 48px(2줄 셀). */
const GRID = '100px 2.2fr 150px'
const ROW_H = 48
/** 기본 임계값 — 폰 필터 단추의 숫자(072 §3.4)가 이 값과 견준다 */
const THR_P = 0.3
const THR_F = 20

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
  const { phone } = useViewport()
  // 폰 필터 접기 — 메모리만, 기본 접힘 (072 §3.4)
  const [foldOpen, setFoldOpen] = useState(false)
  // 필터·정렬은 URL 쿼리(p.*)에 실려 새로고침해도 같은 화면 (002 §3.5). 검색어는 메모리만 — Clarity 가 주소를 통째로 싣는다 (033)
  const [q, setQ] = useState('')
  const [thrP, setThrP] = useUrlState('p.thrp', THR_P, num)
  const [thrF, setThrF] = useUrlState('p.thrf', THR_F, num)
  const [only, setOnly] = useUrlState('p.only', false, bool)
  const [sort, setSort] = useUrlState<{ col: SortCol; asc: boolean }>('p.sort', { col: 'gap', asc: false }, sortOf(['sym', 'gap', 'fund']))

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

  // 넷째 칸은 061 요소 열쇠(data-el)
  const headers: Header[] = [['sym', '심볼', 'left', 'sort:sym'], ['gap', '가격갭', 'right', 'sort:gap'], ['fund', '펀딩갭', 'right', 'sort:fund']]

  // 조작 조각 — 넓은 바와 폰 바가 같은 조각을 쓴다. data-el 은 061 화면 이용 통계의 요소 열쇠(모양·동작과 무관)
  const searchEl = <input className="input" placeholder="심볼 검색" value={q} onChange={(e) => setQ(e.target.value)} style={phone ? phoneSearch : searchInput} data-el="search" />
  const thrPField = <NumField label="가격갭 임계값" value={thrP} step={0.1} onChange={setThrP} el="threshold-price" />
  const thrFField = <NumField label="펀딩갭 임계값 · 연" value={thrF} step={5} onChange={setThrF} el="threshold-funding" />
  const onlyBtn = <ToggleBtn on={only} label="임계 초과만" onClick={() => setOnly(!only)} el="only-over" />
  const pairHint = <span style={hint}>perp ↔ perp · 싼 쪽 롱 + 비싼 쪽 숏 · 펀딩비갭은 시간당 정규화</span>

  return (
    <>
      {phone ? (
        <div data-area="filters" style={phoneBar}>
          {searchEl}
          <FoldButton open={foldOpen} active={Number(thrP !== THR_P) + Number(thrF !== THR_F) + Number(only)} onClick={() => setFoldOpen(!foldOpen)} />
          {foldOpen && <div style={foldPanel}>{thrPField}{thrFField}{onlyBtn}{pairHint}</div>}
        </div>
      ) : (
        <div data-area="filters" style={bar}>
          {searchEl}
          {thrPField}
          {thrFField}
          {onlyBtn}
          {pairHint}
          <span style={count}>{rows.length} / {all.length} 코인 표시</span>
        </div>
      )}

      <TableFrame minWidth={760} area="table">
        {phone
          ? <SortBar headers={headers} sortKey={sort.col} sortDir={mul} onSort={clickSort} right={`${rows.length} / ${all.length} 코인`} />
          : <GridHeader cols={GRID} headers={headers} sortKey={sort.col} sortDir={mul} onSort={clickSort} />}
        {rows.map((r) => {
          const p = r.pair
          const stale = p !== null && p.age >= STALE_SEC
          const hot = p !== null && !stale && p.gap >= thrP
          const chips = (
            <>
              <span style={exTag(true, true)}>{p ? p.lowEx : '–'}</span>
              <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>↔</span>
              <span style={exTag(true, true)}>{p ? p.highEx : '–'}</span>
            </>
          )
          const gapText = (
            <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 84, textAlign: 'right', color: p ? pctColor(p.gap) : 'var(--color-neutral-700)' }}>
              {p ? fmtPct(p.gap) : '–'}
            </span>
          )
          const legs = (
            <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', whiteSpace: 'nowrap' }}>
              {p ? `${p.lowEx} 롱 / ${p.highEx} 숏` : '–'}
            </span>
          )
          const fund = <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: p ? pctColor(p.fundDiffHr, 4) : 'var(--color-neutral-700)' }}>{p ? fmtFundingHr(p.fundDiffHr) : '–'}</span>
          const fundNote = <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>해당 조합 펀딩갭</span>
          // 폰 3줄 카드 — 1줄 = 심볼 / 가격갭, 2줄 = 두 칩 / 펀딩갭, 3줄 = 롱·숏 / 설명 (072 §3.5)
          if (phone) {
            return (
              <div key={r.sym} className="hv-row" style={cardRow({ hot, stale })}>
                <span style={{ fontWeight: 600, fontSize: 14, color: hot ? 'var(--color-accent-300)' : 'var(--color-text)' }}>{r.sym}</span>
                <div style={cardRight}>{gapText}</div>
                <div style={cardLeft}>{chips}</div>
                <div style={cardRight}>{fund}</div>
                <div style={cardLeft}>{legs}</div>
                <div style={cardRight}>{fundNote}</div>
              </div>
            )
          }
          return (
            <div key={r.sym} className="hv-row" style={gridRow(GRID, { hot, stale, height: ROW_H })}>
              <SymCell sym={r.sym} hot={hot} />
              <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {chips}
                  {gapText}
                </div>
                {legs}
              </div>
              <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1 }}>
                {fund}
                {fundNote}
              </div>
            </div>
          )
        })}
      </TableFrame>
    </>
  )
}
