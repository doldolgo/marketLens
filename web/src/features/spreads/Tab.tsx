// 실시간 스프레드 탭 — 코인 1개 = 행 1개 집계 표 (스펙 003 §3.5, 구조는 docs/design/reference/tabs/SpreadTab.tsx).
import { useState } from 'react'
import { HIGHLIGHT_PCT, STALE_SEC } from '../../shared/config'
import { fmtKrw, fmtPct, pctColor } from '../../shared/format'
import type { Feed, IoState, SpreadRow } from '../../shared/types'
import {
  Empty, GridHeader, gridRow, NumField, Seg, segOpt, SymCell, TableFrame, ToggleBtn,
  bar, count, exTag, hint, label, searchInput, type Header,
} from '../../shared/ui'
import type { ApiSpreadRow } from './types'

type View = 'kimp' | 'rev'
type DomFilter = 'all' | '업비트' | '빗썸'
type SortCol = 'sym' | 'price' | 'val' | 'io' | 'net'

/** 심볼 | 국내가 KRW | 김프 | 입출금 | 네트워크 — 국내가 열만 가변 폭. */
const GRID = '112px 1fr 264px 148px 88px'

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

function aggregate(feed: Feed, domFilter: DomFilter, view: View): CoinRow[] {
  const byCoin = new Map<string, SpreadRow[]>()
  for (const r of feed.spreads) {
    if (domFilter !== 'all' && r.dom !== domFilter) continue
    const list = byCoin.get(r.sym)
    if (list) list.push(r)
    else byCoin.set(r.sym, [r])
  }
  const out: CoinRow[] = []
  for (const [sym, rows] of byCoin) {
    const live = rows.filter((r) => r.status !== 'fail')
    let fwdBest: SpreadRow | null = null
    let revBest: SpreadRow | null = null
    for (const r of live) {
      if (!fwdBest || r.fwd > fwdBest.fwd) fwdBest = r
      if (!revBest || r.rev > revBest.rev) revBest = r
    }
    const best = view === 'kimp' ? fwdBest : revBest
    const age = live.length ? Math.min(...live.map((r) => r.age)) : 0
    let price: number | null = null
    if (fwdBest && fwdBest.usd) {
      const rate = rateOf(fwdBest, feed.rate)
      if (rate > 0) price = fwdBest.usd * rate * (1 + fwdBest.fwd / 100)
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

export default function SpreadsTab({ feed, onPick }: { feed: Feed; onPick: (sym: string) => void }) {
  const [q, setQ] = useState('')
  const [domFilter, setDomFilter] = useState<DomFilter>('all')
  const [view, setView] = useState<View>('kimp')
  const [thr, setThr] = useState(HIGHLIGHT_PCT)
  const [onlyThr, setOnlyThr] = useState(false)
  const [onlyIo, setOnlyIo] = useState(false)
  const [sort, setSort] = useState<{ col: SortCol; asc: boolean }>({ col: 'val', asc: false })

  const all = aggregate(feed, domFilter, view)

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
          <span style={hint}>김프 = 해외 매수 → 국내 매도 · 역프 = 국내 매수 → 해외 매도 · 행 클릭 시 기록 탭으로</span>
        </div>
      </div>

      <TableFrame minWidth={820}>
        <GridHeader cols={GRID} headers={headers} sortKey={sort.col} sortDir={dir} onSort={clickSort} />
        {feed.spreads.length === 0 && <Empty>백엔드에서 스프레드를 받는 중입니다…</Empty>}
        {feed.spreads.length > 0 && coins.length === 0 && <Empty>조건에 맞는 코인이 없습니다. 필터를 넓혀 보세요.</Empty>}
        {coins.map((c) => {
          const hot = !c.allFail && !c.allStale && c.val !== null && c.val >= thr
          return (
            <div key={c.sym} onClick={() => onPick(c.sym)} className="hv-row"
              style={{ ...gridRow(GRID, { hot, stale: c.allStale }), cursor: 'pointer' }}>
              <SymCell sym={c.sym} hot={hot} />
              <div style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                {c.price !== null ? '₩' + fmtKrw(c.price) : '–'}
              </div>
              <div style={{ padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8 }}>
                <span style={exTag()}>{c.from ?? '–'}</span>
                <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>→</span>
                <span style={exTag()}>{c.to ?? '–'}</span>
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
