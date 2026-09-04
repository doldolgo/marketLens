// 실시간 스프레드 탭 — 코인 1개 = 행 1개 집계 표 (스펙 003 §3.5).
import { useState } from 'react'
import { HIGHLIGHT_PCT, STALE_SEC } from '../../shared/config'
import { fmtKrw, fmtPct, pctColor } from '../../shared/format'
import type { Feed, IoState, SpreadRow } from '../../shared/types'
import { Chip, DIM_TEXT, NumField, Seg, segOpt, Toggle } from '../../shared/ui'
import type { ApiSpreadRow } from './types'

type View = 'kimp' | 'rev'
type DomFilter = 'all' | '업비트' | '빗썸'
type SortCol = 'sym' | 'price' | 'val' | 'io' | 'net'

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

/** 입출금 태그 — true 강조색 실선, false 중립색 실선, null 은 점선(모름을 한눈에 구분). */
function IoTag({ kind, state }: { kind: '출금' | '입금'; state: IoState }) {
  const label = state === true ? '가능' : state === false ? '중단' : '?'
  const color = state === true ? 'var(--color-accent)' : state === false ? 'var(--color-neutral-500)' : DIM_TEXT
  const border =
    state === true
      ? '1px solid var(--color-accent)'
      : state === false
        ? '1px solid var(--color-neutral-500)'
        : '1px dashed var(--color-neutral-500)'
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        fontSize: 11,
        padding: '2px 8px',
        borderRadius: 'calc(var(--radius-md) * 0.75)',
        whiteSpace: 'nowrap',
        border,
        color,
      }}
    >
      {kind} {label}
    </span>
  )
}

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

  function clickSort(col: SortCol) {
    // 같은 키 재클릭 = 방향 반전. 새 키는 심볼·네트워크만 오름차순.
    setSort((s) => (s.col === col ? { col, asc: !s.asc } : { col, asc: col === 'sym' || col === 'net' }))
  }

  function switchView(v: View) {
    setView(v)
    setSort({ col: 'val', asc: false })
  }

  const cols: { id: SortCol; label: string; width?: number; align?: 'right' }[] = [
    { id: 'sym', label: '심볼', width: 130 },
    { id: 'price', label: '국내가 KRW', align: 'right' },
    { id: 'val', label: view === 'kimp' ? '김프 · 해외 → 국내' : '역프 · 국내 → 해외', width: 340 },
    { id: 'io', label: '입출금', width: 230 },
    { id: 'net', label: '네트워크', width: 120 },
  ]

  const barStyle = {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 10,
    padding: '8px 16px',
    borderBottom: '1px solid var(--color-divider)',
    flex: 'none',
  } as const

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* 필터바 1행 */}
      <div style={barStyle}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="심볼 검색"
          style={{
            width: 130,
            minHeight: 28,
            padding: '2px 10px',
            font: 'inherit',
            fontSize: 12,
            color: 'var(--color-text)',
            caretColor: 'var(--color-accent)',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-divider)',
            borderRadius: 'var(--radius-md)',
          }}
        />
        <span style={{ fontSize: 11, color: DIM_TEXT }}>기준 국내 거래소</span>
        <Seg opts={[['all', '모두'], ['업비트', '업비트'], ['빗썸', '빗썸']].map(([id, l]) => segOpt(l, domFilter === id, () => setDomFilter(id as DomFilter)))} />
        <NumField label="임계값" value={thr} onChange={setThr} step={0.1} />
        <Toggle label="임계 초과만" on={onlyThr} onChange={setOnlyThr} />
        <Toggle label="입출금 가능만" on={onlyIo} onChange={setOnlyIo} />
        <span style={{ marginLeft: 'auto', fontSize: 11, color: DIM_TEXT }}>
          {coins.length} / {all.length} 코인 표시
        </span>
      </div>

      {/* 필터바 2행 */}
      <div style={barStyle}>
        <span style={{ fontSize: 11, color: DIM_TEXT }}>기준 보기</span>
        <Seg opts={[['kimp', '김프'], ['rev', '역프']].map(([id, l]) => segOpt(l, view === id, () => switchView(id as View)))} />
        <span style={{ fontSize: 11, color: DIM_TEXT }}>
          김프 = 해외 매수 → 국내 매도 · 역프 = 국내 매수 → 해외 매도. 행 클릭 시 기록 탭으로.
        </span>
      </div>

      {/* 본문 */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '0 16px' }}>
        {feed.spreads.length === 0 ? (
          <div style={{ padding: '48px 0', textAlign: 'center', color: DIM_TEXT, fontSize: 13 }}>
            백엔드에서 스프레드를 받는 중입니다…
          </div>
        ) : coins.length === 0 ? (
          <div style={{ padding: '48px 0', textAlign: 'center', color: DIM_TEXT, fontSize: 13 }}>
            조건에 맞는 코인이 없습니다. 필터를 넓혀 보세요.
          </div>
        ) : (
          <table className="table" style={{ tableLayout: 'auto' }}>
            <thead>
              <tr>
                {cols.map((c) => {
                  const active = sort.col === c.id
                  return (
                    <th
                      key={c.id}
                      onClick={() => clickSort(c.id)}
                      className="hv-txt"
                      style={{
                        cursor: 'pointer',
                        position: 'sticky',
                        top: 0,
                        zIndex: 1,
                        background: 'var(--color-bg)',
                        whiteSpace: 'nowrap',
                        width: c.width, // 국내가 열만 가변 폭
                        textAlign: c.align ?? 'left',
                        color: active ? 'var(--color-accent)' : undefined,
                      }}
                    >
                      {c.label}
                      {active ? (sort.asc ? ' ▴' : ' ▾') : ''}
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {coins.map((c) => {
                const hot = !c.allFail && !c.allStale && c.val !== null && c.val >= thr
                const dim = c.allFail || c.allStale
                return (
                  <tr
                    key={c.sym}
                    className="hv-row"
                    onClick={() => onPick(c.sym)}
                    style={{
                      cursor: 'pointer',
                      opacity: dim ? 0.45 : 1,
                      background: hot ? 'color-mix(in srgb, var(--color-accent) 10%, transparent)' : undefined,
                    }}
                  >
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {hot && (
                        <span
                          style={{
                            display: 'inline-block',
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: 'var(--color-accent)',
                            marginRight: 8,
                            verticalAlign: 'middle',
                          }}
                        />
                      )}
                      <span style={{ fontWeight: 600, color: hot ? 'var(--color-accent)' : undefined }}>{c.sym}</span>
                    </td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {c.price !== null ? `₩${fmtKrw(c.price)}` : <span style={{ color: DIM_TEXT }}>–</span>}
                    </td>
                    <td>
                      {c.val !== null ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <Chip tone="neutral">{c.from}</Chip>
                          <span style={{ color: DIM_TEXT, fontSize: 11 }}>→</span>
                          <Chip tone="outline">{c.to}</Chip>
                          <span style={{ fontWeight: 700, color: pctColor(c.val) }}>{fmtPct(c.val)}</span>
                        </div>
                      ) : (
                        <span style={{ color: DIM_TEXT }}>–</span>
                      )}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {c.from !== null ? (
                        <span style={{ display: 'inline-flex', gap: 6 }}>
                          <IoTag kind="출금" state={c.wd} />
                          <IoTag kind="입금" state={c.dep} />
                        </span>
                      ) : (
                        <span style={{ color: DIM_TEXT }}>–</span>
                      )}
                    </td>
                    <td style={{ whiteSpace: 'nowrap', color: c.net === '–' ? DIM_TEXT : undefined }}>{c.net}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
