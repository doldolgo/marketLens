// 선선갭(선물–선물 갭) 탭 — /ws/pp 실데이터 (스펙 049 §3.5, 화면·조작은 002 §3.8, 구조는 docs/design/reference/tabs/PPTab.tsx).
// 폰(폭 719px 이하)은 필터 접기·정렬 줄·3줄 카드 행 (072 §3.4·§3.5).
import { useState } from 'react'
import { fmtFundingHr, fmtPct, pctColor } from '../../shared/format'
import {
  Empty, FoldButton, GridHeader, gridRow, NumField, SortBar, SymCell, TableFrame, ToggleBtn,
  bar, cardLeft, cardRight, cardRow, count, exTag, foldPanel, hint, phoneBar, phoneSearch, searchInput, type Header,
} from '../../shared/ui'
import { bool, num, sortOf, useUrlState } from '../../shared/urlState'
import { useViewport } from '../../shared/viewport'
import { usePpSocket } from './api'
import { aggregateCoins, overThreshold, perpName, sortCoins, type SortCol } from './coins'

/** 심볼 | 가격갭(가변) | 펀딩갭. 행 높이는 참조대로 48px(2줄 셀). */
const GRID = '100px 2.2fr 150px'
const ROW_H = 48
/** 기본 임계값 — 폰 필터 단추의 숫자(072 §3.4)가 이 값과 견준다 */
const THR_P = 0.3
const THR_F = 20

export default function PpTab({ active, now }: { active: boolean; now: number }) {
  // 탭이 보이는 동안만 구독 — 숨으면 닫고 표는 메모리에 둔다, 다시 보이면 snapshot 으로 통째 교체 (049 §3.5)
  const table = usePpSocket(active)
  const { phone } = useViewport()
  // 폰 필터 접기 — 메모리만, 기본 접힘 (072 §3.4)
  const [foldOpen, setFoldOpen] = useState(false)
  // 필터·정렬은 URL 쿼리(p.*)에 실려 새로고침해도 같은 화면 (002 §3.5). 검색어는 메모리만 — Clarity 가 주소를 통째로 싣는다 (033)
  const [q, setQ] = useState('')
  const [thrP, setThrP] = useUrlState('p.thrp', THR_P, num)
  const [thrF, setThrF] = useUrlState('p.thrf', THR_F, num)
  const [only, setOnly] = useUrlState('p.only', false, bool)
  const [sort, setSort] = useUrlState<{ col: SortCol; asc: boolean }>('p.sort', { col: 'gap', asc: false }, sortOf(['sym', 'gap', 'fund']))

  // 마지막 프레임 이후 경과 초 — 셸의 1.5초 tick(now)으로 age 가 자라 delta 가 끊기면 5초 뒤 전 행이 stale
  let elapsedSec = 0
  if (table.receivedAt > 0) elapsedSec = Math.max(0, (now - table.receivedAt) / 1000)
  const all = aggregateCoins(table.rows.values(), elapsedSec)

  const ql = q.trim().toLowerCase()
  let rows = all.filter((r) => r.sym.toLowerCase().includes(ql))
  if (only) rows = rows.filter((r) => overThreshold(r, thrP, thrF))
  rows = sortCoins(rows, sort.col, sort.asc)
  const mul = sort.asc ? 1 : -1

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
      {/* 바 상자는 하나 — 영역·열쇠는 화면 안에서 하나뿐(061). 폰은 검색·필터 단추 한 줄 + 펼친 묶음 */}
      <div data-area="filters" style={phone ? phoneBar : bar}>
        {searchEl}
        {phone ? (
          <>
            <FoldButton open={foldOpen} active={Number(thrP !== THR_P) + Number(thrF !== THR_F) + Number(only)} onClick={() => setFoldOpen(!foldOpen)} el="filter-toggle" />
            {foldOpen && <div style={foldPanel}>{thrPField}{thrFField}{onlyBtn}{pairHint}</div>}
          </>
        ) : (
          <>
            {thrPField}
            {thrFField}
            {onlyBtn}
            {pairHint}
            <span style={count}>{rows.length} / {all.length} 코인 표시</span>
          </>
        )}
      </div>

      <TableFrame minWidth={760} area="table">
        {phone
          ? <SortBar headers={headers} sortKey={sort.col} sortDir={mul} onSort={clickSort} right={`${rows.length} / ${all.length} 코인`} els={['sort:col', 'sort:dir']} />
          : <GridHeader cols={GRID} headers={headers} sortKey={sort.col} sortDir={mul} onSort={clickSort} />}
        {all.length === 0 && <Empty>백엔드에서 선선갭 표를 받는 중입니다…</Empty>}
        {rows.map((r) => {
          const d = r.best
          const hot = d !== null && !r.stale && d.gap >= thrP
          const longName = d ? perpName(d.long) : '–'
          const shortName = d ? perpName(d.short) : '–'
          const chips = (
            <>
              <span style={exTag(true, true)}>{longName}</span>
              <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>↔</span>
              <span style={exTag(true, true)}>{shortName}</span>
            </>
          )
          // 음수 가격갭은 파랑 그대로 — 최우선 호가로는 벌어진 게 없다는 뜻이다 (049 §3.5)
          const gapText = (
            <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 84, textAlign: 'right', color: d ? pctColor(d.gap) : 'var(--color-neutral-700)' }}>
              {d ? fmtPct(d.gap) : '–'}
            </span>
          )
          const legs = (
            <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', whiteSpace: 'nowrap' }}>
              {d ? `${longName} 롱 / ${shortName} 숏` : '–'}
            </span>
          )
          let fundText = '–'
          let fundColor = 'var(--color-neutral-700)'
          if (d && d.fundGap !== null) {
            fundText = fmtFundingHr(d.fundGap)
            fundColor = pctColor(d.fundGap, 4)
          }
          const fund = <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: fundColor }}>{fundText}</span>
          const fundNote = <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>해당 조합 펀딩갭</span>
          // 폰 3줄 카드 — 1줄 = 심볼 / 가격갭, 2줄 = 두 칩 / 펀딩갭, 3줄 = 롱·숏 / 설명 (072 §3.5)
          if (phone) {
            return (
              <div key={r.sym} className="hv-row" style={cardRow({ hot, stale: r.stale })}>
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
            <div key={r.sym} className="hv-row" style={gridRow(GRID, { hot, stale: r.stale, height: ROW_H })}>
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
