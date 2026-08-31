// 선물–현물 갭 탭 (mock) — 스펙 002 §3.7.
import { useState } from 'react'
import { STALE_SEC } from '../../shared/config'
import { fmtFunding3, fmtPct, fmtUsdt, pctColor } from '../../shared/format'
import type { Feed } from '../../shared/types'
import { Chip, DIM_TEXT, NumField, Seg, Toggle } from '../../shared/ui'

interface Combo {
  spotEx: string
  perpEx: string
  gap: number
  funding: number
  price: number
  age: number
}

interface Row {
  sym: string
  entry: Combo | null
  exit: Combo | null
}

type SortCol = 'sym' | 'price' | 'gap' | 'funding'
type Mode = 'entry' | 'exit'

/** 펀딩 주기: Hyperliquid 1h, Bitget 4h(선선갭 탭과 동일), 그 외 8h. 남은 시간은 epoch 기준 UTC 정시 경계. */
function fundingEta(ex: string, now: number): string {
  const cycMs = (ex === 'Hyperliquid' ? 1 : ex === 'Bitget' ? 4 : 8) * 3_600_000
  const remainMin = Math.ceil((cycMs - (now % cycMs)) / 60_000)
  if (remainMin < 60) return `펀딩 ${remainMin}분 후`
  return `펀딩 ${Math.floor(remainMin / 60)}시간 ${remainMin % 60}분 후`
}

export default function GapTab({ feed, now }: { feed: Feed; now: number }) {
  const [q, setQ] = useState('')
  const [mode, setMode] = useState<Mode>('entry')
  const [thr, setThr] = useState(0.5)
  const [only, setOnly] = useState(false)
  const [sort, setSort] = useState<{ col: SortCol; asc: boolean }>({ col: 'gap', asc: false })

  // fail 아닌 현물×선물 모든 조합 중 최대(진입)·최소(정리) 갭을 채택.
  const all: Row[] = feed.markets.map((m) => {
    let entry: Combo | null = null
    let exit: Combo | null = null
    for (const s of m.spot) {
      if (s.status === 'fail') continue
      for (const p of m.perp) {
        if (p.status === 'fail') continue
        const gap = ((1 + p.prem / 100) / (1 + s.off / 100) - 1) * 100
        const c: Combo = {
          spotEx: s.ex,
          perpEx: p.ex,
          gap,
          funding: p.funding,
          price: m.base * (1 + s.off / 100),
          age: Math.max(s.age, p.age),
        }
        if (!entry || gap > entry.gap) entry = c
        if (!exit || gap < exit.gap) exit = c
      }
    }
    return { sym: m.sym, entry, exit }
  })

  const combo = (r: Row): Combo | null => (mode === 'entry' ? r.entry : r.exit)
  const meets = (g: number): boolean => (mode === 'entry' ? g >= thr : g <= -thr)

  const ql = q.trim().toLowerCase()
  let rows = all.filter((r) => r.sym.toLowerCase().includes(ql))
  if (only) {
    rows = rows.filter((r) => {
      const c = combo(r)
      return c !== null && meets(c.gap)
    })
  }

  const mul = sort.asc ? 1 : -1
  rows = [...rows].sort((a, b) => {
    const ca = combo(a)
    const cb = combo(b)
    // fail 행·값 없는 행은 항상 뒤.
    if (!ca && !cb) return a.sym.localeCompare(b.sym)
    if (!ca) return 1
    if (!cb) return -1
    const d =
      sort.col === 'sym' ? a.sym.localeCompare(b.sym)
      : sort.col === 'price' ? ca.price - cb.price
      : sort.col === 'gap' ? ca.gap - cb.gap
      : ca.funding - cb.funding
    return d * mul
  })

  function switchMode(m: Mode) {
    setMode(m)
    // 기준 전환 시 정렬 키도 따라간다: 진입=내림차순, 정리=오름차순.
    setSort({ col: 'gap', asc: m === 'exit' })
  }

  function clickSort(col: SortCol) {
    setSort((s) => (s.col === col ? { col, asc: !s.asc } : { col, asc: col === 'sym' }))
  }

  const cols: { id: SortCol; label: string; width?: number; align?: 'right' }[] = [
    { id: 'sym', label: '심볼', width: 110 },
    { id: 'price', label: '현물가 USDT', width: 150, align: 'right' },
    { id: 'gap', label: mode === 'entry' ? '진입 갭 · 현물 → 선물' : '정리 갭 · 현물 → 선물' },
    { id: 'funding', label: '펀딩비', width: 170 },
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
        <Seg
          options={[
            { id: 'entry', label: '진입 기준' },
            { id: 'exit', label: '정리 기준' },
          ]}
          value={mode}
          onChange={(id) => switchMode(id as Mode)}
        />
        <NumField label="하이라이트 임계값" value={thr} onChange={setThr} step={0.1} />
        <Toggle label="임계 초과만" on={only} onChange={setOnly} />
        <span style={{ fontSize: 11, color: DIM_TEXT }}>
          양의 갭 = perp &gt; 현물 → 현물 매수 + 선물 숏 · 음의 갭 = 반대 방향
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
            {rows.map((r) => {
              const c = combo(r)
              const stale = c !== null && c.age >= STALE_SEC
              const hot = c !== null && !stale && meets(c.gap)
              const dotColor = !c ? 'var(--color-up)' : stale ? 'var(--color-warn)' : 'var(--color-ok)'
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
                    <span
                      style={{
                        display: 'inline-block',
                        width: 6,
                        height: 6,
                        borderRadius: '50%',
                        background: dotColor,
                        marginRight: 8,
                        verticalAlign: 'middle',
                      }}
                    />
                    <span style={{ fontWeight: 600, color: hot ? 'var(--color-accent)' : undefined }}>{r.sym}</span>
                  </td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {c ? fmtUsdt(c.price) : <span style={{ color: DIM_TEXT }}>–</span>}
                  </td>
                  <td>
                    {c ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <Chip tone="neutral">{c.spotEx} 현물</Chip>
                        <span style={{ color: DIM_TEXT, fontSize: 11 }}>→</span>
                        <Chip tone="outline">{c.perpEx} 선물</Chip>
                        <span style={{ fontWeight: 700, color: pctColor(c.gap) }}>{fmtPct(c.gap)}</span>
                      </div>
                    ) : (
                      <span style={{ color: DIM_TEXT }}>–</span>
                    )}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {c ? (
                      <>
                        <div style={{ fontSize: 13, color: pctColor(c.funding) }}>{fmtFunding3(c.funding)}</div>
                        <div style={{ fontSize: 11, color: DIM_TEXT }}>{fundingEta(c.perpEx, now)}</div>
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
