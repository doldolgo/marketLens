// 선선갭(선물–선물 갭) 탭 (mock) — 스펙 002 §3.8.
import { useState } from 'react'
import { STALE_SEC } from '../../shared/config'
import { fmtFundingHr, fmtPct, pctColor } from '../../shared/format'
import type { Feed } from '../../shared/types'
import { Chip, DIM_TEXT, NumField, Toggle } from '../../shared/ui'

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

export default function PpTab({ feed }: { feed: Feed }) {
  const [q, setQ] = useState('')
  const [thrP, setThrP] = useState(0.3)
  const [thrF, setThrF] = useState(20)
  const [only, setOnly] = useState(false)
  const [sort, setSort] = useState<{ col: SortCol; asc: boolean }>({ col: 'gap', asc: false })

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

  function clickSort(col: SortCol) {
    setSort((s) => (s.col === col ? { col, asc: !s.asc } : { col, asc: col === 'sym' }))
  }

  const cols: { id: SortCol; label: string; width?: number }[] = [
    { id: 'sym', label: '심볼', width: 110 },
    { id: 'gap', label: '가격갭' },
    { id: 'fund', label: '펀딩갭', width: 210 },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 10,
          padding: '10px 16px',
          borderBottom: '1px solid var(--color-divider)',
          flex: 'none',
        }}
      >
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
        <NumField label="가격갭 임계값" value={thrP} onChange={setThrP} step={0.1} />
        <NumField label="펀딩갭 임계값 · 연" value={thrF} onChange={setThrF} step={5} />
        <Toggle label="임계 초과만" on={only} onChange={setOnly} />
        <span style={{ fontSize: 11, color: DIM_TEXT }}>
          perp ↔ perp · 싼 쪽 롱 + 비싼 쪽 숏 · 펀딩비갭은 시간당 정규화
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: DIM_TEXT }}>
          {rows.length} / {all.length} 코인 표시
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '0 16px' }}>
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
                      width: c.width,
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
            {rows.map((r) => {
              const p = r.pair
              const stale = p !== null && p.age >= STALE_SEC
              const hot = p !== null && !stale && p.gap >= thrP
              return (
                <tr
                  key={r.sym}
                  className="hv-row"
                  style={{
                    opacity: stale ? 0.45 : 1,
                    background: hot ? 'color-mix(in srgb, var(--color-accent) 10%, transparent)' : undefined,
                  }}
                >
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span style={{ fontWeight: 600, color: hot ? 'var(--color-accent)' : undefined }}>{r.sym}</span>
                  </td>
                  <td>
                    {p ? (
                      <>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <Chip tone="neutral">{p.lowEx}</Chip>
                          <span style={{ color: DIM_TEXT, fontSize: 11 }}>↔</span>
                          <Chip tone="outline">{p.highEx}</Chip>
                          <span style={{ fontWeight: 700, color: pctColor(p.gap) }}>{fmtPct(p.gap)}</span>
                        </div>
                        <div style={{ fontSize: 11, color: DIM_TEXT, marginTop: 2 }}>
                          {p.lowEx} 롱 / {p.highEx} 숏
                        </div>
                      </>
                    ) : (
                      <span style={{ color: DIM_TEXT }}>–</span>
                    )}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {p ? (
                      <>
                        <div style={{ fontSize: 13, color: pctColor(p.fundDiffHr) }}>{fmtFundingHr(p.fundDiffHr)}</div>
                        <div style={{ fontSize: 11, color: DIM_TEXT }}>해당 조합 펀딩갭</div>
                      </>
                    ) : (
                      <span style={{ color: DIM_TEXT }}>–</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
