// 입출금 레이더 탭 (mock, 시현용) — 스펙 002 §3.10, 구조는 docs/design/reference/tabs/FlowTab.tsx.
import { useState } from 'react'
import { fmtQty, fmtTime, fmtUsd } from '../../shared/format'
import { DOM_EXS, FLOW_PRICES } from '../../shared/mock'
import type { Feed, FlowRow } from '../../shared/types'
import { Empty, Pill, Seg, segOpt, bar, card, count, gridHead, gridRow, headCell, kicker, searchInput } from '../../shared/ui'

type Pivot = { kind: 'coin'; sym: string } | { kind: 'addr'; id: string }
type Dir = 'all' | 'in' | 'out'
type Region = 'all' | 'fx' | 'dom'

const isDom = (ex: string): boolean => (DOM_EXS as readonly string[]).includes(ex)
const usdSum = (rows: FlowRow[]): number => rows.reduce((s, r) => s + (r.usd ?? 0), 0)

/** 세 가지 뷰(전체/코인/주소)의 표 컬럼 — 코인 뷰는 코인 열 생략, 주소 뷰는 주소 열 생략. */
const GRIDS = {
  base: { cols: '96px 60px 74px 1fr 148px 132px 96px 108px', min: 1000 },
  coin: { cols: '96px 60px 1fr 148px 132px 96px 108px', min: 940 },
  addr: { cols: '96px 60px 84px 1fr 148px 96px 108px', min: 900 },
}

/** 코인 칩·거래소 칩 공통 — 테두리 버튼, hover 는 전역 클래스. */
const chipBtn = {
  appearance: 'none', font: 'inherit', display: 'inline-flex', alignItems: 'baseline', cursor: 'pointer',
  borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-neutral-800)', color: 'var(--color-text)',
} as const

export default function FlowTab({ feed, now }: { feed: Feed; now: number }) {
  const [q, setQ] = useState('')
  const [miss, setMiss] = useState(false)
  const [dir, setDir] = useState<Dir>('all')
  const [region, setRegion] = useState<Region>('all')
  const [exSel, setExSel] = useState<string[]>([])
  const [stack, setStack] = useState<Pivot[]>([])

  const rowsAll = feed.flowRows
  const top = stack.length ? stack[stack.length - 1] : null

  // 필터 파이프라인: 드릴다운 → 방향 → 권역 → 거래소 칩.
  const drillRows = rowsAll.filter((r) =>
    !top ? true : top.kind === 'coin' ? r.sym === top.sym : r.addr === top.id,
  )
  const dirRows = dir === 'all' ? drillRows : drillRows.filter((r) => r.dir === dir)
  const scopeRows = region === 'all' ? dirRows : dirRows.filter((r) => (region === 'dom') === isDom(r.ex))
  const shown = exSel.length ? scopeRows.filter((r) => exSel.includes(r.ex)) : scopeRows

  // 거래소 칩: 현재 범위의 건수, 국내 우선 → 건수순, 다중 선택.
  const exCounts = new Map<string, number>()
  for (const r of scopeRows) exCounts.set(r.ex, (exCounts.get(r.ex) ?? 0) + 1)
  const exChips = [...exCounts.entries()].sort((a, b) => {
    const da = isDom(a[0]) ? 0 : 1
    const db = isDom(b[0]) ? 0 : 1
    return da - db || b[1] - a[1]
  })

  const anyFilter = dir !== 'all' || region !== 'all' || exSel.length > 0

  // 요약 (표시 행 기준, null usd 는 0 으로 합산).
  const fxOut = shown.filter((r) => r.dir === 'out' && !isDom(r.ex))
  const domIn = shown.filter((r) => r.dir === 'in' && isDom(r.ex))
  const inRows = shown.filter((r) => r.dir === 'in')
  const summary = [
    { label: '해외 출금', value: `${fxOut.length}건 · ${fmtUsd(usdSum(fxOut))}`, color: 'var(--color-text)' },
    { label: '국내 입금', value: `${domIn.length}건 · ${fmtUsd(usdSum(domIn))}`, color: 'var(--color-accent-300)' },
    { label: '입금 / 출금', value: `${inRows.length} / ${shown.length - inRows.length}건`, color: 'var(--color-neutral-300)' },
    { label: '표시 총액', value: fmtUsd(usdSum(shown)), color: 'var(--color-neutral-300)' },
  ]

  function submitSearch() {
    const s = q.trim()
    setMiss(false)
    if (!s) {
      setStack([])
      return
    }
    const upper = s.toUpperCase()
    if (upper in FLOW_PRICES) {
      setStack([{ kind: 'coin', sym: upper }])
      return
    }
    const lower = s.toLowerCase()
    const addr = feed.flowAddrs.find(
      (a) => a.id.toLowerCase().includes(lower) || a.label.toLowerCase().includes(lower),
    )
    if (addr) {
      setStack([{ kind: 'addr', id: addr.id }])
      return
    }
    setMiss(true)
  }

  function pivot(p: Pivot) {
    setMiss(false)
    setStack((st) => {
      const last = st.length ? st[st.length - 1] : null
      if (last && last.kind === p.kind) return [...st.slice(0, -1), p]
      return [...st, p]
    })
  }

  const shortOf = (id: string): string => feed.flowAddrs.find((a) => a.id === id)?.short ?? id

  // 최상위 코인 칩: 건수 내림차순.
  const coinCounts = new Map<string, number>()
  for (const r of rowsAll) coinCounts.set(r.sym, (coinCounts.get(r.sym) ?? 0) + 1)
  const coinChips = [...coinCounts.entries()].sort((a, b) => b[1] - a[1])

  // 브레드크럼: 전체 → 코인 → 주소. 마지막 단계만 accent.
  const crumbs = [
    { label: '전체', color: stack.length ? 'var(--color-neutral-400)' : 'var(--color-text)', sep: stack.length ? '→' : '', onClick: () => setStack([]) },
    ...stack.map((p, i) => ({
      label: p.kind === 'coin' ? p.sym : shortOf(p.id),
      color: i === stack.length - 1 ? 'var(--color-accent-300)' : 'var(--color-neutral-400)',
      sep: i === stack.length - 1 ? '' : '→',
      onClick: () => setStack(stack.slice(0, i + 1)),
    })),
  ]

  const viewKind = top ? top.kind : 'base'
  const grid = GRIDS[viewKind]
  const showCoinCol = viewKind !== 'coin'
  const showAddrCol = viewKind !== 'addr'

  return (
    <>
      {/* 바 1: 검색 · 방향 분절 · 표시 건수 */}
      <div style={bar}>
        <input className="input" placeholder="코인 심볼 또는 주소 검색 후 Enter" value={q}
          onChange={(e) => { setQ(e.target.value); setMiss(false) }}
          onKeyDown={(e) => { if (e.key === 'Enter') submitSearch() }}
          style={{ ...searchInput, width: 270 }} />
        <Seg opts={[['all', '전체'], ['in', '입금'], ['out', '출금']].map(([id, l]) => segOpt(l, dir === id, () => setDir(id as Dir)))} />
        {miss && <span style={{ fontSize: 11.5, color: 'var(--color-neutral-500)' }}>일치하는 코인·주소 없음</span>}
        <span style={count}>{shown.length} / {rowsAll.length}건 표시</span>
      </div>

      {/* 바 2: 권역 · 거래소 칩 · 필터 초기화 */}
      <div style={{ ...bar, padding: 'var(--space-2) var(--space-6)' }}>
        <span style={kicker}>권역</span>
        <Seg pad="4px 11px" opts={[['all', '전체'], ['fx', '해외'], ['dom', '국내']].map(([id, l]) => segOpt(l, region === id, () => setRegion(id as Region)))} />
        <span style={{ ...kicker, marginLeft: 'var(--space-2)' }}>거래소</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          {exChips.map(([ex, n]) => {
            const on = exSel.includes(ex)
            return (
              <button key={ex} className="hv-bd"
                onClick={() => setExSel((sel) => (sel.includes(ex) ? sel.filter((e) => e !== ex) : [...sel, ex]))}
                style={{
                  ...chipBtn, gap: 6, fontSize: 11.5, padding: '3px 10px',
                  border: `1px solid ${on ? 'var(--color-accent)' : 'var(--color-neutral-800)'}`,
                  background: on ? 'var(--color-neutral-900)' : 'transparent',
                  color: on ? 'var(--color-accent-300)' : 'var(--color-neutral-300)',
                }}>
                {ex}<span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)' }}>{n}건</span>
              </button>
            )
          })}
        </div>
        {anyFilter && (
          <button className="hv-txt" onClick={() => { setDir('all'); setRegion('all'); setExSel([]) }}
            style={{ appearance: 'none', font: 'inherit', background: 'none', border: 'none', fontSize: 11.5, padding: 0, cursor: 'pointer', color: 'var(--color-neutral-500)' }}>
            필터 초기화
          </button>
        )}
      </div>

      {/* 바 3: 요약 */}
      <div style={{ ...bar, gap: 'var(--space-6)' }}>
        {summary.map((k) => (
          <span key={k.label} style={{ display: 'inline-flex', flexDirection: 'column', gap: 1 }}>
            <span style={kicker}>{k.label}</span>
            <span style={{ fontSize: 14, fontVariantNumeric: 'tabular-nums', color: k.color }}>{k.value}</span>
          </span>
        ))}
      </div>

      {/* 바 4: 브레드크럼 */}
      <div style={{ ...bar, gap: 'var(--space-3)', padding: 'var(--space-2) var(--space-6)' }}>
        {stack.length > 0 && (
          <button className="hv-bd-txt" onClick={() => setStack((st) => st.slice(0, -1))}
            style={{ ...chipBtn, fontSize: 11.5, padding: '3px 10px', background: 'transparent', color: 'var(--color-neutral-300)' }}>
            ← 뒤로
          </button>
        )}
        {crumbs.map((c, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <button className="hv-txt" onClick={c.onClick}
              style={{ appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 12, padding: 0, cursor: 'pointer', color: c.color }}>
              {c.label}
            </button>
            <span style={{ fontSize: 11, color: 'var(--color-neutral-700)' }}>{c.sep}</span>
          </span>
        ))}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--color-neutral-600)' }}>행의 주소·코인을 클릭하면 그 기준으로 피벗</span>
      </div>

      {/* 본문 */}
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        <div style={{ maxWidth: 1240, margin: '0 auto', padding: 'var(--space-4) var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>

          {!top && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
              <span style={kicker}>코인별 입출금 건수 — 클릭해 코인 뷰로</span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
                {coinChips.map(([sym, n]) => (
                  <button key={sym} className="hv-bd-txt" onClick={() => pivot({ kind: 'coin', sym })}
                    style={{ ...chipBtn, gap: 7, fontSize: 12.5, padding: '5px 12px', background: 'var(--color-surface)' }}>
                    {sym}<span style={{ fontSize: 11, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)' }}>{n}건</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {top?.kind === 'coin' && <CoinCard sym={top.sym} rows={drillRows} />}
          {top?.kind === 'addr' && <AddrCard id={top.id} rows={drillRows} feed={feed} onCoin={(sym) => pivot({ kind: 'coin', sym })} />}

          {/* 입출금 표 */}
          <div style={{ ...card, padding: 'var(--space-2) 0' }}>
            <div style={{ fontSize: 10, letterSpacing: '0.04em', color: 'var(--color-neutral-600)', padding: 'var(--space-4) var(--space-6) var(--space-2)' }}>
              {top ? (top.kind === 'coin' ? `${top.sym} 최근 입출금` : `${shortOf(top.id)} 전체 입출금 · 코인 무관`) : '전체 최근 입출금 · 최신순'}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: grid.min }}>
                <div style={gridHead(grid.cols)}>
                  <span style={{ padding: '6px 8px 6px 0' }}>시각</span>
                  <span style={headCell}>방향</span>
                  {showCoinCol && <span style={headCell}>코인</span>}
                  {showAddrCol && <span style={headCell}>주소</span>}
                  <span style={{ ...headCell, textAlign: 'right' }}>수량</span>
                  <span style={{ ...headCell, textAlign: 'right' }}>USD</span>
                  <span style={headCell}>거래소</span>
                  <span style={headCell}>상태</span>
                </div>
                {shown.map((r, i) => {
                  const done = r.state === 'sweep 확정' || r.state === '확정'
                  return (
                    <div key={i} className="hv-row4" style={gridRow(grid.cols, { height: 38, rule: 6 })}>
                      <span style={{ padding: '0 8px 0 0', fontSize: 11.5, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)' }}>{fmtTime(now - r.age * 1000)}</span>
                      <Pill tone={r.dir === 'in' ? 'accent' : 'neutral'} style={{ margin: '0 8px' }}>{r.dir === 'in' ? '입금' : '출금'}</Pill>
                      {showCoinCol && (
                        <button className="hv-txt" onClick={() => pivot({ kind: 'coin', sym: r.sym })}
                          style={{ justifySelf: 'start', margin: '0 8px', appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 12.5, fontWeight: 500, padding: 0, cursor: 'pointer', color: 'var(--color-text)' }}>
                          {r.sym}
                        </button>
                      )}
                      {showAddrCol && (
                        <button className="hv-txt" onClick={() => pivot({ kind: 'addr', id: r.addr })}
                          style={{ justifySelf: 'start', margin: '0 8px', appearance: 'none', background: 'none', border: 'none', font: 'inherit', padding: 0, cursor: 'pointer', display: 'inline-flex', alignItems: 'baseline', gap: 8, textAlign: 'left', color: 'var(--color-text)' }}>
                          <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>{r.short}</span>
                          <span style={{ fontSize: 10.5, color: 'var(--color-neutral-500)' }}>{r.label}</span>
                        </button>
                      )}
                      <span style={{ padding: '0 8px', textAlign: 'right', fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' }}>{fmtQty(r.qty)}</span>
                      <span style={{
                        padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12.5,
                        fontWeight: r.usd !== null && r.usd >= 1_000_000 ? 600 : 400,
                        color: r.usd === null ? 'var(--color-neutral-600)' : r.usd >= 1_000_000 ? 'var(--color-accent-300)' : 'var(--color-text)',
                      }}>{fmtUsd(r.usd)}</span>
                      <span style={{ padding: '0 8px', fontSize: 11.5, color: 'var(--color-neutral-300)' }}>{r.ex}</span>
                      {/* 완료 상태는 회색, 진행 중은 accent */}
                      <Pill tone={done ? 'neutral' : 'accent'} style={{ margin: '0 8px', ...(done ? { color: 'var(--color-neutral-300)' } : null) }}>{r.state}</Pill>
                    </div>
                  )
                })}
              </div>
            </div>
            {shown.length === 0 && <Empty size={12}>해당 조건의 입출금 내역 없음</Empty>}
          </div>

        </div>
      </div>

      {/* 전용 푸터 — flow 탭에서는 셸 푸터를 대체한다 */}
      <footer style={{ flex: 'none', display: 'flex', gap: 'var(--space-6)', padding: 'var(--space-2) var(--space-6)', borderTop: '1px solid var(--color-divider)', fontSize: 11, color: 'var(--color-neutral-600)', flexWrap: 'wrap' }}>
        <span>주소 + entity 라벨까지 — 온체인상 개인 신원은 확인 불가</span>
        <span style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}>⚠️ 시현용 mock 데이터 · 실제 온체인 연동 아님</span>
      </footer>
    </>
  )
}

/** 카드 공통 프레임 — surface 카드 안에 블록을 가로로 나열. */
const cardStyle = { ...card, padding: 'var(--space-4) var(--space-6)', display: 'flex', gap: 'var(--space-8)', flexWrap: 'wrap', alignItems: 'flex-start' } as const

/** 코인 뷰 카드 — 건수·총액 / 총 입금 / 총 출금 / 거래소별 분포 막대. */
function CoinCard({ sym, rows }: { sym: string; rows: FlowRow[] }) {
  const inRows = rows.filter((r) => r.dir === 'in')
  const outRows = rows.filter((r) => r.dir === 'out')
  const exCounts = new Map<string, number>()
  for (const r of rows) exCounts.set(r.ex, (exCounts.get(r.ex) ?? 0) + 1)
  const dist = [...exCounts.entries()].sort((a, b) => b[1] - a[1])
  const maxN = Math.max(1, ...exCounts.values())
  return (
    <div style={cardStyle}>
      <div style={{ minWidth: 150 }}>
        <div style={{ ...kicker, marginBottom: 3 }}>코인</div>
        <div style={{ fontFamily: 'var(--font-heading)', fontSize: 24, fontWeight: 500 }}>{sym}</div>
        <div style={{ fontSize: 11.5, color: 'var(--color-neutral-500)', fontVariantNumeric: 'tabular-nums' }}>{rows.length}건 · {fmtUsd(usdSum(rows))}</div>
      </div>
      <div>
        <div style={{ ...kicker, marginBottom: 3 }}>총 입금</div>
        <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums', color: 'var(--color-accent-300)' }}>{inRows.length}건</div>
        <div style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-400)' }}>{fmtUsd(usdSum(inRows))}</div>
      </div>
      <div>
        <div style={{ ...kicker, marginBottom: 3 }}>총 출금</div>
        <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{outRows.length}건</div>
        <div style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-400)' }}>{fmtUsd(usdSum(outRows))}</div>
      </div>
      <div style={{ flex: 1, minWidth: 260 }}>
        <div style={{ ...kicker, marginBottom: 6 }}>거래소별 분포</div>
        <div style={{ display: 'grid', gridTemplateColumns: '74px 1fr 44px', gap: '5px 10px', alignItems: 'center' }}>
          {dist.map(([ex, n]) => (
            <span key={ex} style={{ display: 'contents' }}>
              <span style={{ fontSize: 11.5, color: 'var(--color-neutral-400)' }}>{ex}</span>
              <span style={{ position: 'relative', height: 8, background: 'var(--color-bg)', borderRadius: 4, overflow: 'hidden' }}>
                <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: ((n / maxN) * 100).toFixed(0) + '%', background: 'var(--color-accent-500)', borderRadius: 4 }} />
              </span>
              <span style={{ fontSize: 11, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', textAlign: 'right' }}>{n}건</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

/** 주소 뷰 카드 — 총 거래 / 총액 / 주 이용 거래소 / 다룬 코인 칩(클릭 → 코인 뷰). */
function AddrCard({ id, rows, feed, onCoin }: { id: string; rows: FlowRow[]; feed: Feed; onCoin: (sym: string) => void }) {
  const addr = feed.flowAddrs.find((a) => a.id === id)
  const inN = rows.filter((r) => r.dir === 'in').length
  const exCounts = new Map<string, number>()
  for (const r of rows) exCounts.set(r.ex, (exCounts.get(r.ex) ?? 0) + 1)
  const dist = [...exCounts.entries()].sort((a, b) => b[1] - a[1])
  const coinCounts = new Map<string, number>()
  for (const r of rows) coinCounts.set(r.sym, (coinCounts.get(r.sym) ?? 0) + 1)
  const coins = [...coinCounts.entries()].sort((a, b) => b[1] - a[1])
  return (
    <div style={cardStyle}>
      <div style={{ minWidth: 190 }}>
        <div style={{ ...kicker, marginBottom: 3 }}>주소</div>
        <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 16 }}>{addr?.short ?? id}</div>
        <div style={{ fontSize: 11.5, color: 'var(--color-accent-300)', marginTop: 2 }}>{addr?.label ?? 'Unknown'}</div>
      </div>
      <div>
        <div style={{ ...kicker, marginBottom: 3 }}>총 거래</div>
        <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{rows.length}건</div>
        <div style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-400)' }}>입금 {inN} · 출금 {rows.length - inN}</div>
      </div>
      <div>
        <div style={{ ...kicker, marginBottom: 3 }}>총액</div>
        <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{fmtUsd(usdSum(rows))}</div>
      </div>
      <div>
        <div style={{ ...kicker, marginBottom: 3 }}>주 이용 거래소</div>
        <div style={{ fontSize: 17 }}>{dist.length ? dist[0][0] : '–'}</div>
        <div style={{ fontSize: 12, color: 'var(--color-neutral-400)' }}>
          {dist.length > 1 ? `외 ${dist.length - 1}곳 · ${dist.slice(1).map(([ex]) => ex).join(', ')}` : '단일 거래소'}
        </div>
      </div>
      <div style={{ flex: 1, minWidth: 220 }}>
        <div style={{ ...kicker, marginBottom: 6 }}>다룬 코인 — 클릭해 코인 뷰로</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {coins.map(([sym, n]) => (
            <button key={sym} className="hv-bd-txt" onClick={() => onCoin(sym)}
              style={{ ...chipBtn, gap: 6, fontSize: 12, padding: '3px 10px', background: 'var(--color-bg)' }}>
              {sym}<span style={{ fontSize: 10.5, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)' }}>{n}건</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
