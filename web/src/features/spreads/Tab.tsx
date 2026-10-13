// 실시간 스프레드 탭 — 코인 1개 = 행 1개 집계 표 (스펙 003 §3.5, 구조는 docs/design/reference/tabs/SpreadTab.tsx).
import { memo, useState, type CSSProperties } from 'react'
import { HIGHLIGHT_PCT } from '../../shared/config'
import { fmtKrw, fmtPct, fmtUsdt, pctColor } from '../../shared/format'
import { FX_EXS } from '../../shared/mock'
import type { IoState, SpreadRow } from '../../shared/types'
import {
  Empty, GridHeader, gridRow, NumField, Seg, segOpt, SymCell, TableFrame, ToggleBtn,
  bar, count, exTag, hint, label, searchInput, vDivider, type Header,
} from '../../shared/ui'
import { alias, bool, num, oneOf, sortOf, useUrlState, type Codec } from '../../shared/urlState'
import { aggregateCoins, slipText, sortCoins, type CoinRow, type DomFilter, type SortCol, type View } from './coins'

/** 꺼진 해외 거래소 Record ↔ 쉼표 목록. FX_EXS 밖 이름은 버린다. */
const FX_OFF_CODEC: Codec<Record<string, boolean>> = {
  parse: (s) => Object.fromEntries(s.split(',').filter((fx) => (FX_EXS as readonly string[]).includes(fx)).map((fx) => [fx, true])),
  format: (v) => FX_EXS.filter((fx) => v[fx]).join(','),
}

/** 심볼 | 변동율 | 국내가격 | 해외가격 | 해외거래소 | 국내거래소 | 김프 | 입출금 | 네트워크 — 국내가격 열만 가변 폭 (026 §3.3).
 *  김프 열은 `슬 −N.NN%p` 배지 자리를 항상 비워 둔다 — 값이 바뀔 때마다 표가 흔들리지 않게. */
const GRID = '112px 84px 1fr 112px 96px 84px 176px 148px 88px'

// 세 상태를 세 모양으로 그린다. 확인 불가(null)를 초록(열림)으로 칠하지 않고, 중단과도 다르게(점선) 그린다.
const okC = 'var(--color-accent-300)'
const badC = 'var(--color-neutral-600)'
const unkC = 'var(--color-neutral-500)'
const tagStyle = (state: IoState) => ({
  fontSize: 10, padding: '2px 6px', borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap' as const,
  border: state === null ? '1px dashed var(--color-neutral-800)' : `1px solid ${state ? 'var(--color-accent-800)' : 'var(--color-neutral-800)'}`,
  color: state === null ? unkC : state ? okC : badC,
})
// 확인 불가는 '?' 로 — "가능/중단" 어느 쪽으로도 읽히면 안 된다
const ioLabel = (kind: string, state: IoState) => (state === null ? `${kind} ?` : state ? `${kind} 가능` : `${kind} 중단`)
// 비교 해외 거래소 체크박스 — 색은 켜짐/꺼짐에 따라 호출부에서 덧씌운다
const fxCheck = { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' } as const
const checkbox = { accentColor: 'var(--color-accent)', width: 13, height: 13, cursor: 'pointer' } as const

/** 행 1개가 화면에 쓰는 값 — 전부 문자열·불리언 같은 원시값이라, memo 의 얕은 비교가 곧 "보이는 글자·색이 바뀌었나" 다.
 *  매초 delta 가 와도 글자가 그대로인 행(보통 절반 넘게)은 다시 그리지 않는다. 화면에 쓰는 값은 빠짐없이 여기에 싣는다. */
interface LineProps {
  sym: string
  hot: boolean
  stale: boolean
  chgText: string
  chgColor: string
  priceText: string
  usdText: string
  fxEx: string
  domEx: string
  slipText: string
  valText: string
  valColor: string
  netDiff: boolean
  wd: IoState
  dep: IoState
  net: string
}

function lineProps(c: CoinRow, thr: number): LineProps {
  return {
    sym: c.sym,
    hot: !c.allFail && !c.allStale && c.val !== null && c.val >= thr,
    stale: c.allStale,
    // 변동율 — 기준가 없음(null)은 0% 가 아니라 `–` (026 §3.2)
    chgText: c.chg !== null ? fmtPct(c.chg) : '–',
    chgColor: c.chg !== null ? pctColor(c.chg) : 'var(--color-neutral-700)',
    priceText: c.price !== null ? '₩' + fmtKrw(c.price) : '–',
    usdText: c.usd !== null ? '$' + fmtUsdt(c.usd) : '–',
    fxEx: c.fxEx ?? '–',
    domEx: c.domEx ?? '–',
    slipText: slipText(c.slip),
    valText: c.val !== null ? fmtPct(c.val) : '–',
    valColor: c.val !== null ? pctColor(c.val) : 'var(--color-neutral-700)',
    netDiff: c.netDiff,
    wd: c.wd,
    dep: c.dep,
    net: c.net,
  }
}

// 행 스타일 — delta 1장마다 행 수백 개(실측 407행 중 149~325행)가 다시 그려지는 자리라, 값이 정해진 스타일 객체는 모듈에서 한 번만
// 만든다. React 는 style 이 이전과 같은 객체면 비교 없이 넘어가고, 새 객체면 키마다 이전 값과 견준다. 값·키 순서는 렌더마다 만들던
// 인라인 객체와 같게 둔다 — DOM 의 style 글자가 같아야 하므로 키를 더하거나 순서를 바꿀 때는 아래 상수도 같이 본다
const rowStyle = (hot: boolean, stale: boolean): CSSProperties => (
  { ...gridRow(GRID, { hot, stale }), cursor: 'pointer', contentVisibility: 'auto', containIntrinsicSize: 'auto 40px' }
)
/** [hot][stale] — 화면 밖 행은 스타일·레이아웃·페인트를 건너뛴다(DOM 에는 남아 찾기·복사·접근성은 그대로). 행 높이가 40px 고정이라 자리 추정이 정확하다 */
const ROW_STYLE = [[rowStyle(false, false), rowStyle(false, true)], [rowStyle(true, false), rowStyle(true, true)]] as const
const priceCell: CSSProperties = { padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
const usdCell: CSSProperties = { padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' }
const exCell: CSSProperties = { padding: '0 8px', display: 'flex', alignItems: 'center' }
const EX_TAG = exTag()
const valCell: CSSProperties = { padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8 }
const slipStyle: CSSProperties = { fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)', whiteSpace: 'nowrap' }
const ioCell: CSSProperties = { padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 5 }
const netCell: CSSProperties = { padding: '0 8px', textAlign: 'right', fontSize: 11, color: 'var(--color-neutral-500)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }
const TAG_OPEN = tagStyle(true)
const TAG_SHUT = tagStyle(false)
const TAG_UNKNOWN = tagStyle(null)
const ioTag = (state: IoState) => (state === null ? TAG_UNKNOWN : state ? TAG_OPEN : TAG_SHUT)
// 색이 값마다 다른 두 칸(변동율·김프 값)은 색 문자열마다 한 번 만든다. 색은 pctColor 의 세 가지와 값 없음(neutral-700)뿐이라
// 칸마다 많아야 4개다 — 색 계산이 값에 따라 연속으로 바뀌게 되면 이 캐시가 끝없이 자라므로 그때는 캐시를 걷어낸다
const chgCells = new Map<string, CSSProperties>()
function chgCell(color: string): CSSProperties {
  let s = chgCells.get(color)
  if (!s) chgCells.set(color, (s = { padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color }))
  return s
}
const valTexts = new Map<string, CSSProperties>()
function valTextStyle(color: string): CSSProperties {
  let s = valTexts.get(color)
  if (!s) valTexts.set(color, (s = { fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 72, textAlign: 'right', color }))
  return s
}

const CoinLine = memo(function CoinLine(p: LineProps & { onPick: (sym: string) => void }) {
  return (
    <div onClick={() => p.onPick(p.sym)} className="hv-row" data-el={`row:${p.sym}`} style={ROW_STYLE[p.hot ? 1 : 0][p.stale ? 1 : 0]}>
      <SymCell sym={p.sym} hot={p.hot} />
      <div style={chgCell(p.chgColor)}>
        {p.chgText}
      </div>
      <div style={priceCell}>
        {p.priceText}
      </div>
      <div style={usdCell}>
        {p.usdText}
      </div>
      <div style={exCell}>
        <span style={EX_TAG}>{p.fxEx}</span>
      </div>
      <div style={exCell}>
        <span style={EX_TAG}>{p.domEx}</span>
      </div>
      <div style={valCell}>
        <span style={slipStyle}>
          {p.slipText}
        </span>
        <span style={valTextStyle(p.valColor)}>
          {p.valText}
        </span>
      </div>
      <div style={ioCell}>
        {/* 다름이면 어느 방향이든 옮길 길이 없다 — "중단" 두 개로 보이면 안 되므로 태그 하나 */}
        {p.netDiff ? (
          <span style={TAG_SHUT}>네트워크 다름</span>
        ) : (
          <>
            <span style={ioTag(p.wd)}>{ioLabel('출금', p.wd)}</span>
            <span style={ioTag(p.dep)}>{ioLabel('입금', p.dep)}</span>
          </>
        )}
      </div>
      <div style={netCell}>
        {p.net}
      </div>
    </div>
  )
})

/**
 * 표는 셸의 리렌더(1.5초 tick·5초 수집 상태 폴링·매초 delta) 가운데 아래 셋이 그대로인 것은 건너뛴다(memo 얕은 비교).
 * 표가 피드에서 읽는 것은 행 배열(값)과, 그 행의 age 로 내리는 stale 판정뿐이다 — 판정이 바뀌는 tick 에는 staleTick 이 오른다.
 * 나중에 표가 age·now 를 stale 판정 말고 다른 곳(예: 'N초 전' 글자)에 쓰게 되면 staleTick 조건(shared/feed 의 tickFeed)을 같이 넓힌다.
 */
interface Props {
  /** 피드 행 배열 — snapshot·delta 마다 새 배열이다. 참조가 그대로면 행 값도 그대로다(tick 이 키우는 age 말고는). */
  rows: SpreadRow[]
  /** tick 이 행의 stale 판정을 바꿨을 때만 오른다 — 본문에서는 읽지 않는다(memo 비교용). */
  staleTick: number
  /** 셸이 고정 참조로 준다 — 바뀌면 모든 행이 다시 그려진다. */
  onPick: (sym: string) => void
}

export default memo(function SpreadsTab({ rows, onPick }: Props) {
  // 필터·정렬은 URL 쿼리(s.*)에 실려 새로고침해도 같은 화면 (002 §3.5). 검색어는 메모리만 — Clarity 가 주소를 통째로 싣는다 (033)
  const [q, setQ] = useState('')
  const [domFilter, setDomFilter] = useUrlState<DomFilter>('s.dom', 'all', alias([['all', 'all'], ['upbit', '업비트'], ['bithumb', '빗썸']]))
  const [view, setView] = useUrlState<View>('s.view', 'kimp', oneOf(['kimp', 'rev']))
  const [thr, setThr] = useUrlState('s.thr', HIGHLIGHT_PCT, num)
  const [onlyThr, setOnlyThr] = useUrlState('s.only', false, bool)
  const [onlyIo, setOnlyIo] = useUrlState('s.io', false, bool)
  const [onlyNet, setOnlyNet] = useUrlState('s.net', false, bool)
  const [sort, setSort] = useUrlState<{ col: SortCol; asc: boolean }>('s.sort', { col: 'val', asc: false }, sortOf(['sym', 'chg', 'price', 'usd', 'fxEx', 'domEx', 'val', 'io', 'net']))
  /** 체크 해제된 해외 거래소 — 키가 있으면 제외. 비어 있으면 전부 켜짐. URL 엔 꺼진 이름 목록으로. */
  const [fxOff, setFxOff] = useUrlState<Record<string, boolean>>('s.fxoff', {}, FX_OFF_CODEC)
  const fxAllOn = FX_EXS.every((fx) => !fxOff[fx])

  const all = aggregateCoins(rows, domFilter, fxOff, view)

  const ql = q.trim().toLowerCase()
  let coins = all.filter((c) => c.sym.toLowerCase().includes(ql))
  if (onlyThr) coins = coins.filter((c) => c.val !== null && c.val >= thr)
  // null 은 열림이 아니다 — 출금·입금 둘 다 true 일 때만 통과 (§3.5)
  if (onlyIo) coins = coins.filter((c) => c.wd === true && c.dep === true)
  // 모름·다름 둘 다 빠진다 — 맞춘 망 이름이 있는 행만
  if (onlyNet) coins = coins.filter((c) => c.netFx !== null)

  const dir = sort.asc ? 1 : -1
  coins = sortCoins(coins, sort.col, sort.asc)

  function clickSort(col: string) {
    const c = col as SortCol
    // 같은 키 재클릭 = 방향 반전. 새 키는 이름 열(심볼·네트워크·거래소)만 오름차순.
    const nameCol = c === 'sym' || c === 'net' || c === 'fxEx' || c === 'domEx'
    setSort((s) => (s.col === c ? { col: c, asc: !s.asc } : { col: c, asc: nameCol }))
  }

  function switchView(v: View) {
    setView(v)
    setSort({ col: 'val', asc: false })
  }

  // 넷째 칸은 061 요소 열쇠(data-el)
  const headers: Header[] = [
    ['sym', '심볼', 'left', 'sort:sym'], ['chg', '변동율', 'right', 'sort:chg'], ['price', '국내가격', 'right', 'sort:price'],
    ['usd', '해외가격', 'right', 'sort:usd'], ['fxEx', '해외거래소', 'left', 'sort:fxex'], ['domEx', '국내거래소', 'left', 'sort:domex'],
    ['val', view === 'kimp' ? '김프' : '역프', 'right', 'sort:val'], ['io', '입출금', 'right', 'sort:io'], ['net', '네트워크', 'right', 'sort:net'],
  ]

  return (
    <>
      {/* 필터바 — 2행은 flexBasis 100% 로 같은 바 안에서 줄을 바꾼다. data-el 은 061 요소 열쇠(모양·동작과 무관) */}
      <div data-area="filters" style={bar}>
        <input className="input" placeholder="심볼 검색" value={q} onChange={(e) => setQ(e.target.value)} style={searchInput} data-el="search" />
        <span style={label}>기준 국내 거래소</span>
        <Seg opts={[['all', '모두', 'dom:all'], ['업비트', '업비트', 'dom:upbit'], ['빗썸', '빗썸', 'dom:bithumb']].map(([id, l, el]) => segOpt(l, domFilter === id, () => setDomFilter(id as DomFilter), el))} />
        <NumField label="하이라이트 임계값" value={thr} step={0.1} onChange={setThr} el="threshold" />
        <ToggleBtn on={onlyThr} label="임계 초과만" onClick={() => setOnlyThr(!onlyThr)} el="only-over" />
        <label data-el="only-io" style={{ ...fxCheck, color: onlyIo ? 'var(--color-accent-300)' : 'var(--color-neutral-300)' }}>
          <input type="checkbox" checked={onlyIo} style={checkbox} onChange={() => setOnlyIo(!onlyIo)} />입출금 열림
        </label>
        <label data-el="only-net" style={{ ...fxCheck, color: onlyNet ? 'var(--color-accent-300)' : 'var(--color-neutral-300)' }}>
          <input type="checkbox" checked={onlyNet} style={checkbox} onChange={() => setOnlyNet(!onlyNet)} />네트워크 같음
        </label>
        <span style={count}>{coins.length} / {all.length} 코인 표시</span>
        <div style={{ flexBasis: '100%', display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <span style={label}>기준 보기</span>
          <Seg pad="4px 10px" opts={[['kimp', '김프 기준', 'view:kimp'], ['rev', '역프 기준', 'view:rev']].map(([id, l, el]) => segOpt(l, view === id, () => switchView(id as View), el))} />
          {vDivider}
          {/* 차감은 항상 적용된다 — 가격 기준 세그먼트도, 체결 규모 선택지도 없다 (§3.5, 017 로 $1,000 고정) */}
          <span style={label}>체결 규모</span>
          <span style={hint}>$1,000 호가창 시장가 체결 기준 · 매수·매도 양측 슬리피지 차감</span>
          {vDivider}
          <span style={label}>비교 해외 거래소</span>
          <label data-el="fx:all" style={{ ...fxCheck, color: fxAllOn ? 'var(--color-accent-300)' : 'var(--color-neutral-400)' }}>
            <input type="checkbox" checked={fxAllOn} style={checkbox}
              onChange={() => setFxOff(fxAllOn ? Object.fromEntries(FX_EXS.map((fx) => [fx, true])) : {})} />모두
          </label>
          {vDivider}
          {FX_EXS.map((fx) => (
            <label key={fx} data-el={`fx:${fx.toLowerCase().replace(/[^a-z0-9]/g, '')}`} style={{ ...fxCheck, color: fxOff[fx] ? 'var(--color-neutral-600)' : 'var(--color-neutral-300)' }}>
              <input type="checkbox" checked={!fxOff[fx]} style={checkbox} onChange={() => setFxOff({ ...fxOff, [fx]: !fxOff[fx] })} />{fx}
            </label>
          ))}
        </div>
      </div>

      <TableFrame minWidth={1040} area="table">
        <GridHeader cols={GRID} headers={headers} sortKey={sort.col} sortDir={dir} onSort={clickSort} />
        {rows.length === 0 && <Empty>백엔드에서 스프레드를 받는 중입니다…</Empty>}
        {rows.length > 0 && coins.length === 0 && <Empty>조건에 맞는 코인이 없습니다. 필터를 넓혀 보세요.</Empty>}
        {coins.map((c) => <CoinLine key={c.sym} {...lineProps(c, thr)} onPick={onPick} />)}
      </TableFrame>
    </>
  )
})
