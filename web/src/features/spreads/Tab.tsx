// 실시간 스프레드 탭 — 코인 1개 = 행 1개 집계 표 (스펙 003 §3.5, 구조는 docs/design/reference/tabs/SpreadTab.tsx).
import { useState } from 'react'
import { HIGHLIGHT_PCT, STALE_SEC } from '../../shared/config'
import { fmtKrw, fmtPct, pctColor } from '../../shared/format'
import { FX_EXS } from '../../shared/mock'
import type { Feed, IoState, SpreadRow } from '../../shared/types'
import {
  Empty, GridHeader, gridRow, NumField, Seg, segOpt, SymCell, TableFrame, ToggleBtn,
  bar, count, exTag, hint, label, searchInput, vDivider, type Header,
} from '../../shared/ui'
import type { ApiSpreadRow } from './types'

type View = 'kimp' | 'rev'
type DomFilter = 'all' | '업비트' | '빗썸'
/** 가격 기준 — 현재가(최우선 호가) / 슬리피지 반영(체결 규모만큼 호가를 먹는다고 가정). */
type Basis = 'mid' | 'slip'
/** 체결 규모(USD) 선택지. 기본 $10k. */
const NOTIONALS = [10000, 50000, 100000, 500000]
type SortCol = 'sym' | 'price' | 'val' | 'io' | 'net'

/** 심볼 | 국내가 KRW | 김프 | 입출금 | 네트워크 — 국내가 열만 가변 폭. */
const GRID = '112px 1fr 264px 148px 88px'
/** 슬리피지 반영이면 김프 셀에 `슬 −N.NN%p` 배지가 붙어 열을 그만큼 넓힌다 — 안 넓히면 국내가 숫자와 겹친다. */
const GRID_SLIP = '112px 1fr 344px 148px 88px'

/** 코인 1개의 집계 행. */
interface CoinRow {
  sym: string
  allFail: boolean
  allStale: boolean
  age: number
  /** 보기 기준(김프/역프) 최대 행의 값. 전부 fail 이면 null. */
  val: number | null
  from: string | null
  to: string | null
  /** 출금(출발 거래소)·입금(도착 거래소) 상태. */
  wd: IoState
  dep: IoState
  net: string
  /** 국내가 KRW — 김프 최대 행 기준. fail 이거나 usd 없으면 null. */
  price: number | null
  /** 슬리피지 차감폭(%p) — 현재가 기준이면 null. */
  slip: number | null
}

/** 슬리피지 차감이 적용된 행. fwdRaw 는 차감 전 fwd — 국내가 계산에만 쓴다. */
type CalcRow = SpreadRow & { slip?: number; fwdRaw?: number }

/** 체결 규모 대비 최우선 호가 유동성으로 한쪽 슬리피지 추정. 상한 6% (스펙 003 §3.5). */
function slipOf(notional: number, liq: number): number {
  return Math.min(6, 100 * Math.pow(notional / Math.max(liq, 1), 0.85))
}

/** 확장 키 rateAsk 는 런타임에 실려 온다(구조적 타이핑) — 없으면 셸 환율로 폴백. */
function rateOf(row: SpreadRow, fallback: number): number {
  return (row as Partial<ApiSpreadRow>).rateAsk ?? fallback
}

/** 입출금 정렬 순위: 가능(둘 다 true) > 모름 > 중단(하나라도 false). */
function ioRank(wd: IoState, dep: IoState): number {
  if (wd === false || dep === false) return 0
  if (wd === true && dep === true) return 2
  return 1
}

function aggregate(
  feed: Feed, domFilter: DomFilter, fxOff: Record<string, boolean>, view: View, basis: Basis, notional: number,
): CoinRow[] {
  const byCoin = new Map<string, CalcRow[]>()
  for (const r0 of feed.spreads) {
    if (domFilter !== 'all' && r0.dom !== domFilter) continue
    if (fxOff[r0.fx]) continue
    let r: CalcRow = r0
    if (basis === 'slip') {
      // 매수·매도 양측 슬리피지를 합산해 김프·역프에서 차감. 이후 판단은 전부 차감된 값 기준.
      const sl = slipOf(notional, r0.liqFx) + slipOf(notional, r0.liqDom)
      r = { ...r0, fwd: Math.round((r0.fwd - sl) * 100) / 100, rev: Math.round((r0.rev - sl) * 100) / 100, slip: sl, fwdRaw: r0.fwd }
    }
    const list = byCoin.get(r.sym)
    if (list) list.push(r)
    else byCoin.set(r.sym, [r])
  }
  const out: CoinRow[] = []
  for (const [sym, rows] of byCoin) {
    const live = rows.filter((r) => r.status !== 'fail')
    let fwdBest: CalcRow | null = null
    let revBest: CalcRow | null = null
    for (const r of live) {
      if (!fwdBest || r.fwd > fwdBest.fwd) fwdBest = r
      if (!revBest || r.rev > revBest.rev) revBest = r
    }
    const best = view === 'kimp' ? fwdBest : revBest
    const age = live.length ? Math.min(...live.map((r) => r.age)) : 0
    let price: number | null = null
    if (fwdBest && fwdBest.usd) {
      const rate = rateOf(fwdBest, feed.rate)
      // 체결 비용은 국내 시세를 바꾸지 않으므로 차감 전 fwd 로 환산
      if (rate > 0) price = fwdBest.usd * rate * (1 + (fwdBest.fwdRaw ?? fwdBest.fwd) / 100)
    }
    out.push({
      sym,
      allFail: live.length === 0,
      allStale: live.length > 0 && live.every((r) => r.age >= STALE_SEC),
      age,
      val: best ? (view === 'kimp' ? best.fwd : best.rev) : null,
      // 김프 = 해외 → 국내, 역프 = 국내 → 해외. 출금 거래소는 출발, 입금 거래소는 도착.
      from: best ? (view === 'kimp' ? best.fx : best.dom) : null,
      to: best ? (view === 'kimp' ? best.dom : best.fx) : null,
      wd: best ? (view === 'kimp' ? best.wdFx : best.wdDom) : null,
      dep: best ? (view === 'kimp' ? best.depDom : best.depFx) : null,
      net: best ? (best.netDom ?? '–') : '–',
      price,
      slip: best?.slip ?? null,
    })
  }
  return out
}

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

export default function SpreadsTab({ feed, onPick }: { feed: Feed; onPick: (sym: string) => void }) {
  const [q, setQ] = useState('')
  const [domFilter, setDomFilter] = useState<DomFilter>('all')
  const [view, setView] = useState<View>('kimp')
  const [thr, setThr] = useState(HIGHLIGHT_PCT)
  const [onlyThr, setOnlyThr] = useState(false)
  const [onlyIo, setOnlyIo] = useState(false)
  const [sort, setSort] = useState<{ col: SortCol; asc: boolean }>({ col: 'val', asc: false })
  const [basis, setBasis] = useState<Basis>('mid')
  const [notional, setNotional] = useState(NOTIONALS[0])
  /** 체크 해제된 해외 거래소 — 키가 있으면 제외. 비어 있으면 전부 켜짐. */
  const [fxOff, setFxOff] = useState<Record<string, boolean>>({})
  const fxAllOn = FX_EXS.every((fx) => !fxOff[fx])

  const all = aggregate(feed, domFilter, fxOff, view, basis, notional)

  const ql = q.trim().toLowerCase()
  let coins = all.filter((c) => c.sym.toLowerCase().includes(ql))
  if (onlyThr) coins = coins.filter((c) => c.val !== null && c.val >= thr)
  // null 은 열림이 아니다 — 출금·입금 둘 다 true 일 때만 통과 (§3.5)
  if (onlyIo) coins = coins.filter((c) => c.wd === true && c.dep === true)

  const dir = sort.asc ? 1 : -1
  coins = [...coins].sort((a, b) => {
    // 전부 fail 인 코인은 항상 맨 뒤
    if (a.allFail !== b.allFail) return a.allFail ? 1 : -1
    const key = (c: CoinRow): number | string | null =>
      sort.col === 'val' ? c.val
      : sort.col === 'price' ? c.price
      : sort.col === 'io' ? ioRank(c.wd, c.dep)
      : sort.col === 'net' ? (c.net === '–' ? null : c.net)
      : c.sym
    const ka = key(a)
    const kb = key(b)
    // null 값은 뒤
    if (ka === null && kb === null) return a.sym.localeCompare(b.sym)
    if (ka === null) return 1
    if (kb === null) return -1
    const d = typeof ka === 'string' ? ka.localeCompare(kb as string) : ka - (kb as number)
    return d * dir || a.sym.localeCompare(b.sym)
  })

  function clickSort(col: string) {
    const c = col as SortCol
    // 같은 키 재클릭 = 방향 반전. 새 키는 심볼·네트워크만 오름차순.
    setSort((s) => (s.col === c ? { col: c, asc: !s.asc } : { col: c, asc: c === 'sym' || c === 'net' }))
  }

  function switchView(v: View) {
    setView(v)
    setSort({ col: 'val', asc: false })
  }

  const grid = basis === 'slip' ? GRID_SLIP : GRID
  const headers: Header[] = [
    ['sym', '심볼', 'left'], ['price', '국내가 KRW', 'right'],
    ['val', view === 'kimp' ? '김프' : '역프', 'right'], ['io', '입출금', 'right'], ['net', '네트워크', 'right'],
  ]

  return (
    <>
      {/* 필터바 — 2행은 flexBasis 100% 로 같은 바 안에서 줄을 바꾼다 */}
      <div style={bar}>
        <input className="input" placeholder="심볼 검색" value={q} onChange={(e) => setQ(e.target.value)} style={searchInput} />
        <span style={label}>기준 국내 거래소</span>
        <Seg opts={[['all', '모두'], ['업비트', '업비트'], ['빗썸', '빗썸']].map(([id, l]) => segOpt(l, domFilter === id, () => setDomFilter(id as DomFilter)))} />
        <NumField label="하이라이트 임계값" value={thr} step={0.1} onChange={setThr} />
        <ToggleBtn on={onlyThr} label="임계 초과만" onClick={() => setOnlyThr(!onlyThr)} />
        <ToggleBtn on={onlyIo} label="입출금 가능만" onClick={() => setOnlyIo(!onlyIo)} />
        <span style={count}>{coins.length} / {all.length} 코인 표시</span>
        <div style={{ flexBasis: '100%', display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <span style={label}>기준 보기</span>
          <Seg pad="4px 10px" opts={[['kimp', '김프 기준'], ['rev', '역프 기준']].map(([id, l]) => segOpt(l, view === id, () => switchView(id as View)))} />
          {vDivider}
          <span style={label}>가격 기준</span>
          <Seg pad="4px 10px" opts={[['mid', '현재가'], ['slip', '슬리피지 반영']].map(([id, l]) => segOpt(l, basis === id, () => setBasis(id as Basis)))} />
          {basis === 'slip' && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <span style={label}>체결 규모</span>
              <Seg pad="4px 9px" opts={NOTIONALS.map((v) => segOpt('$' + v / 1000 + 'k', notional === v, () => setNotional(v)))} />
            </span>
          )}
          <span style={hint}>
            {basis === 'slip' ? '호가창 시장가 체결 기준 · 매수·매도 양측 슬리피지 차감' : '최우선 호가 기준 · 체결 비용 미반영'}
          </span>
          {vDivider}
          <span style={label}>비교 해외 거래소</span>
          <label style={{ ...fxCheck, color: fxAllOn ? 'var(--color-accent-300)' : 'var(--color-neutral-400)' }}>
            <input type="checkbox" checked={fxAllOn} style={checkbox}
              onChange={() => setFxOff(fxAllOn ? Object.fromEntries(FX_EXS.map((fx) => [fx, true])) : {})} />모두
          </label>
          {vDivider}
          {FX_EXS.map((fx) => (
            <label key={fx} style={{ ...fxCheck, color: fxOff[fx] ? 'var(--color-neutral-600)' : 'var(--color-neutral-300)' }}>
              <input type="checkbox" checked={!fxOff[fx]} style={checkbox} onChange={() => setFxOff({ ...fxOff, [fx]: !fxOff[fx] })} />{fx}
            </label>
          ))}
        </div>
      </div>

      <TableFrame minWidth={820}>
        <GridHeader cols={grid} headers={headers} sortKey={sort.col} sortDir={dir} onSort={clickSort} />
        {feed.spreads.length === 0 && <Empty>백엔드에서 스프레드를 받는 중입니다…</Empty>}
        {feed.spreads.length > 0 && coins.length === 0 && <Empty>조건에 맞는 코인이 없습니다. 필터를 넓혀 보세요.</Empty>}
        {coins.map((c) => {
          const hot = !c.allFail && !c.allStale && c.val !== null && c.val >= thr
          return (
            <div key={c.sym} onClick={() => onPick(c.sym)} className="hv-row"
              style={{ ...gridRow(grid, { hot, stale: c.allStale }), cursor: 'pointer' }}>
              <SymCell sym={c.sym} hot={hot} />
              <div style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                {c.price !== null ? '₩' + fmtKrw(c.price) : '–'}
              </div>
              <div style={{ padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8 }}>
                <span style={exTag()}>{c.from ?? '–'}</span>
                <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>→</span>
                <span style={exTag()}>{c.to ?? '–'}</span>
                <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)', whiteSpace: 'nowrap' }}>
                  {c.slip !== null ? '슬 −' + c.slip.toFixed(2) + '%p' : ''}
                </span>
                <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 72, textAlign: 'right', color: c.val !== null ? pctColor(c.val) : 'var(--color-neutral-700)' }}>
                  {c.val !== null ? fmtPct(c.val) : '–'}
                </span>
              </div>
              <div style={{ padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 5 }}>
                <span style={tagStyle(c.wd)}>{ioLabel('출금', c.wd)}</span>
                <span style={tagStyle(c.dep)}>{ioLabel('입금', c.dep)}</span>
              </div>
              <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 11, color: 'var(--color-neutral-500)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {c.net}
              </div>
            </div>
          )
        })}
      </TableFrame>
    </>
  )
}
