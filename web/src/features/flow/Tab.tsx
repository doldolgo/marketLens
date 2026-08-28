// 입출금 레이더 탭 (mock, 시현용) — 스펙 002 §3.10.
import { useState } from 'react'
import { fmtQty, fmtTime, fmtUsd } from '../../shared/format'
import { DOM_EXS, FLOW_PRICES } from '../../shared/mock'
import type { Feed, FlowRow } from '../../shared/types'
import { Chip, DIM_TEXT, Seg } from '../../shared/ui'

type Pivot = { kind: 'coin'; sym: string } | { kind: 'addr'; id: string }
type Dir = 'all' | 'in' | 'out'
type Region = 'all' | 'fx' | 'dom'

const isDom = (ex: string): boolean => (DOM_EXS as readonly string[]).includes(ex)
const usdSum = (rows: FlowRow[]): number => rows.reduce((s, r) => s + (r.usd ?? 0), 0)

const barStyle = {
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: 10,
  padding: '8px 16px',
  borderBottom: '1px solid var(--color-divider)',
  flex: 'none',
} as const

const kicker = {
  fontSize: 10,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'var(--color-accent)',
} as const

const cardStyle = {
  background: 'var(--color-surface)',
  borderRadius: 'var(--radius-md)',
  padding: 'var(--space-4)',
  display: 'flex',
  alignItems: 'flex-start',
  gap: 28,
  flexWrap: 'wrap',
  marginBottom: 12,
} as const

/** 거래소별 분포 막대 색 순환. */
const BAR_COLORS = [
  'var(--color-accent)',
  'var(--color-accent-2-500)',
  'var(--color-accent-600)',
  'var(--color-neutral-500)',
  'var(--color-accent-2-700)',
  'var(--color-neutral-700)',
  'var(--color-accent-800)',
  'var(--color-neutral-800)',
]

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

  const showCoinCol = !(top?.kind === 'coin')
  const showAddrCol = !(top?.kind === 'addr')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* 바 1: 검색 · 방향 분절 · 표시 건수 */}
      <div style={barStyle}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submitSearch()
          }}
          placeholder="코인 심볼 또는 주소 검색 후 Enter"
          style={{
            width: 230,
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
        {miss && <span style={{ fontSize: 12, color: 'var(--color-warn)' }}>일치하는 코인·주소 없음</span>}
        <Seg
          options={[
            { id: 'all', label: '전체' },
            { id: 'in', label: '입금' },
            { id: 'out', label: '출금' },
          ]}
          value={dir}
          onChange={(id) => setDir(id as Dir)}
        />
        <span style={{ marginLeft: 'auto', fontSize: 11, color: DIM_TEXT }}>
          {shown.length} / {rowsAll.length}건 표시
        </span>
      </div>

      {/* 바 2: 권역 · 거래소 칩 · 필터 초기화 */}
      <div style={barStyle}>
        <span style={{ fontSize: 11, color: DIM_TEXT }}>권역</span>
        <Seg
          options={[
            { id: 'all', label: '전체' },
            { id: 'fx', label: '해외' },
            { id: 'dom', label: '국내' },
          ]}
          value={region}
          onChange={(id) => setRegion(id as Region)}
        />
        <span style={{ fontSize: 11, color: DIM_TEXT }}>거래소</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {exChips.map(([ex, n]) => (
            <Chip
              key={ex}
              tone="neutral"
              active={exSel.includes(ex)}
              onClick={() => setExSel((sel) => (sel.includes(ex) ? sel.filter((e) => e !== ex) : [...sel, ex]))}
            >
              {ex} {n}
            </Chip>
          ))}
        </div>
        {anyFilter && (
          <button
            type="button"
            className="hv-txt"
            onClick={() => {
              setDir('all')
              setRegion('all')
              setExSel([])
            }}
            style={{
              font: 'inherit',
              fontSize: 12,
              cursor: 'pointer',
              border: 'none',
              background: 'transparent',
              color: 'var(--color-accent)',
              marginLeft: 'auto',
            }}
          >
            필터 초기화
          </button>
        )}
      </div>

      {/* 바 3: 요약 */}
      <div style={{ ...barStyle, gap: 28 }}>
        <div>
          <div style={kicker}>해외 출금</div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {fxOut.length}건 · {fmtUsd(usdSum(fxOut))}
          </div>
        </div>
        <div>
          <div style={kicker}>국내 입금</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--color-accent)' }}>
            {domIn.length}건 · {fmtUsd(usdSum(domIn))}
          </div>
        </div>
        <div>
          <div style={kicker}>입금 / 출금</div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {inRows.length} / {shown.length - inRows.length}건
          </div>
        </div>
        <div>
          <div style={kicker}>표시 총액</div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{fmtUsd(usdSum(shown))}</div>
        </div>
      </div>

      {/* 바 4: 브레드크럼 */}
      <div style={barStyle}>
        {stack.length > 0 && (
          <button
            type="button"
            className="hv-txt"
            onClick={() => setStack((st) => st.slice(0, -1))}
            style={{
              font: 'inherit',
              fontSize: 12,
              cursor: 'pointer',
              border: 'none',
              background: 'transparent',
              color: 'var(--color-accent)',
            }}
          >
            ← 뒤로
          </button>
        )}
        <span
          className="hv-txt"
          onClick={() => setStack([])}
          style={{ fontSize: 12, cursor: 'pointer', color: stack.length ? DIM_TEXT : 'var(--color-text)' }}
        >
          전체
        </span>
        {stack.map((p, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            <span style={{ color: DIM_TEXT, fontSize: 11 }}>→</span>
            <span
              className="hv-txt"
              onClick={() => setStack(stack.slice(0, i + 1))}
              style={{
                fontSize: 12,
                cursor: 'pointer',
                color: i === stack.length - 1 ? 'var(--color-text)' : DIM_TEXT,
              }}
            >
              {p.kind === 'coin' ? p.sym : shortOf(p.id)}
            </span>
          </span>
        ))}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: DIM_TEXT }}>
          행의 주소·코인을 클릭하면 그 기준으로 피벗
        </span>
      </div>

      {/* 본문 */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '12px 16px' }}>
        {!top && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
            {coinChips.map(([sym, n]) => (
              <Chip key={sym} tone="neutral" onClick={() => pivot({ kind: 'coin', sym })}>
                {sym} {n}건
              </Chip>
            ))}
          </div>
        )}

        {top?.kind === 'coin' && <CoinCard sym={top.sym} rows={rowsAll} />}
        {top?.kind === 'addr' && <AddrCard id={top.id} feed={feed} />}

        {shown.length === 0 ? (
          <div style={{ padding: '48px 0', textAlign: 'center', color: DIM_TEXT, fontSize: 13 }}>
            해당 조건의 입출금 내역 없음
          </div>
        ) : (
          <table className="table" style={{ tableLayout: 'auto' }}>
            <thead>
              <tr>
                {['시각', '방향', ...(showCoinCol ? ['코인'] : []), ...(showAddrCol ? ['주소'] : []), '수량', 'USD', '거래소', '상태'].map(
                  (h, i) => (
                    <th
                      key={i}
                      style={{
                        position: 'sticky',
                        top: 0,
                        zIndex: 1,
                        background: 'var(--color-bg)',
                        whiteSpace: 'nowrap',
                        textAlign: h === '수량' || h === 'USD' ? 'right' : 'left',
                      }}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {shown.map((r, i) => (
                <tr key={i} className="hv-row">
                  <td style={{ whiteSpace: 'nowrap', color: DIM_TEXT }}>{fmtTime(now - r.age * 1000)}</td>
                  <td>
                    <Chip tone={r.dir === 'in' ? 'accent' : 'neutral'}>{r.dir === 'in' ? '입금' : '출금'}</Chip>
                  </td>
                  {showCoinCol && (
                    <td>
                      <span
                        className="hv-txt"
                        onClick={() => pivot({ kind: 'coin', sym: r.sym })}
                        style={{ cursor: 'pointer', fontWeight: 600 }}
                      >
                        {r.sym}
                      </span>
                    </td>
                  )}
                  {showAddrCol && (
                    <td>
                      <span
                        className="hv-txt"
                        onClick={() => pivot({ kind: 'addr', id: r.addr })}
                        style={{ cursor: 'pointer' }}
                      >
                        {r.short}
                      </span>
                      <div style={{ fontSize: 10, color: DIM_TEXT }}>{r.label}</div>
                    </td>
                  )}
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtQty(r.qty)}</td>
                  <td
                    style={{
                      textAlign: 'right',
                      whiteSpace: 'nowrap',
                      ...(r.usd === null
                        ? { color: DIM_TEXT }
                        : r.usd >= 1_000_000
                          ? { color: 'var(--color-accent)', fontWeight: 700 }
                          : null),
                    }}
                  >
                    {fmtUsd(r.usd)}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{r.ex}</td>
                  <td>
                    <Chip tone={r.state === 'sweep 확정' || r.state === '확정' ? 'neutral' : 'accent'}>{r.state}</Chip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 전용 푸터 — flow 탭에서는 셸 푸터를 대체한다 */}
      <footer
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '8px 16px',
          borderTop: '1px solid var(--color-divider)',
          fontSize: 11,
          color: DIM_TEXT,
          flex: 'none',
        }}
      >
        <span>주소 + entity 라벨까지 — 온체인상 개인 신원은 확인 불가</span>
        <span style={{ marginLeft: 'auto' }}>⚠️ 시현용 mock 데이터 · 실제 온체인 연동 아님</span>
      </footer>
    </div>
  )
}

/** 코인 뷰 카드 — 건수·총액 / 총 입금 / 총 출금 / 거래소별 분포 막대. */
function CoinCard({ sym, rows }: { sym: string; rows: FlowRow[] }) {
  const mine = rows.filter((r) => r.sym === sym)
  const inRows = mine.filter((r) => r.dir === 'in')
  const outRows = mine.filter((r) => r.dir === 'out')
  const exCounts = new Map<string, number>()
  for (const r of mine) exCounts.set(r.ex, (exCounts.get(r.ex) ?? 0) + 1)
  const dist = [...exCounts.entries()].sort((a, b) => b[1] - a[1])
  return (
    <div style={cardStyle}>
      <div>
        <div style={kicker}>코인</div>
        <div style={{ fontSize: 20, fontWeight: 700 }}>{sym}</div>
        <div style={{ fontSize: 12, color: DIM_TEXT }}>
          {mine.length}건 · {fmtUsd(usdSum(mine))}
        </div>
      </div>
      <div>
        <div style={kicker}>총 입금</div>
        <div style={{ fontSize: 15, fontWeight: 600 }}>
          {inRows.length}건 · {fmtUsd(usdSum(inRows))}
        </div>
      </div>
      <div>
        <div style={kicker}>총 출금</div>
        <div style={{ fontSize: 15, fontWeight: 600 }}>
          {outRows.length}건 · {fmtUsd(usdSum(outRows))}
        </div>
      </div>
      <div style={{ flex: 1, minWidth: 260 }}>
        <div style={kicker}>거래소별 분포</div>
        <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', margin: '6px 0' }}>
          {dist.map(([ex, n], i) => (
            <span key={ex} style={{ width: `${(n / mine.length) * 100}%`, background: BAR_COLORS[i % BAR_COLORS.length] }} />
          ))}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 12px', fontSize: 11, color: DIM_TEXT }}>
          {dist.map(([ex, n], i) => (
            <span key={ex} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 2,
                  background: BAR_COLORS[i % BAR_COLORS.length],
                  display: 'inline-block',
                }}
              />
              {ex} {n}건
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

/** 주소 뷰 카드 — 총 거래 / 총액 / 주 이용 거래소 / 다룬 코인 칩. */
function AddrCard({ id, feed }: { id: string; feed: Feed }) {
  const addr = feed.flowAddrs.find((a) => a.id === id)
  const mine = feed.flowRows.filter((r) => r.addr === id)
  const inN = mine.filter((r) => r.dir === 'in').length
  const exCounts = new Map<string, number>()
  for (const r of mine) exCounts.set(r.ex, (exCounts.get(r.ex) ?? 0) + 1)
  const dist = [...exCounts.entries()].sort((a, b) => b[1] - a[1])
  const mainEx = dist.length ? dist[0][0] : '–'
  const coins = [...new Set(mine.map((r) => r.sym))]
  return (
    <div style={cardStyle}>
      <div>
        <div style={kicker}>주소</div>
        <div style={{ fontSize: 17, fontWeight: 700 }}>{addr?.short ?? id}</div>
        <div style={{ fontSize: 12, color: DIM_TEXT }}>{addr?.label ?? 'Unknown'}</div>
      </div>
      <div>
        <div style={kicker}>총 거래</div>
        <div style={{ fontSize: 15, fontWeight: 600 }}>
          입금 {inN} · 출금 {mine.length - inN}
        </div>
      </div>
      <div>
        <div style={kicker}>총액</div>
        <div style={{ fontSize: 15, fontWeight: 600 }}>{fmtUsd(usdSum(mine))}</div>
      </div>
      <div>
        <div style={kicker}>주 이용 거래소</div>
        <div style={{ fontSize: 15, fontWeight: 600 }}>
          {mainEx}
          {dist.length > 1 && <span style={{ fontSize: 12, color: DIM_TEXT }}> 외 {dist.length - 1}곳</span>}
        </div>
      </div>
      <div>
        <div style={{ ...kicker, marginBottom: 6 }}>다룬 코인</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {coins.map((c) => (
            <Chip key={c} tone="neutral">
              {c}
            </Chip>
          ))}
        </div>
      </div>
    </div>
  )
}
