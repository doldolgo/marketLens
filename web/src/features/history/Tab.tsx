// 기록/통계 탭 — mock 유지, /history/* 호출 금지 (스펙 005 §3.6).
// 데이터는 002 공유 피드의 mock 사건 목록(feed.events)이고 실데이터 연결은 후속 스펙.
import { useState } from 'react'
import { fmtAgo, fmtPct, fmtTime, pctColor } from '../../shared/format'
import type { Feed, MockEvent } from '../../shared/types'
import { DIM_TEXT, NumField, Seg } from '../../shared/ui'

type Per = '7d' | '30d' | '90d'
type TypeFilter = 'all' | 'kimp' | 'rev'
type DomFilter = 'all' | '업비트' | '빗썸'
type SortCol = 'sym' | 'count' | 'maxDur' | 'avgDur' | 'maxKimp' | 'avgKimp' | 'maxRev' | 'avgRev' | 'latest'

const PER_LABEL: Record<Per, string> = { '7d': '1주', '30d': '1달', '90d': '3달' }
const PER_MS: Record<Per, number> = { '7d': 7 * 86_400_000, '30d': 30 * 86_400_000, '90d': 90 * 86_400_000 }

/** 심볼 1개의 집계 행 — 사건이 없는 방향의 통계는 null(표시는 –, 정렬은 뒤). */
interface SymStat {
  sym: string
  count: number
  maxDur: number
  avgDur: number
  maxKimp: number | null
  avgKimp: number | null
  maxRev: number | null
  avgRev: number | null
  latest: number
}

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
} as const

/** 지속 분 → 사람이 읽는 표기. */
function fmtDur(min: number): string {
  if (min < 60) return `${Math.round(min)}분`
  return `${(min / 60).toFixed(1)}시간`
}

function aggregate(events: MockEvent[]): SymStat[] {
  const bySym = new Map<string, MockEvent[]>()
  for (const e of events) {
    const list = bySym.get(e.sym)
    if (list) list.push(e)
    else bySym.set(e.sym, [e])
  }
  const out: SymStat[] = []
  for (const [sym, list] of bySym) {
    const kimp = list.filter((e) => e.type === 'kimp')
    const rev = list.filter((e) => e.type === 'rev')
    out.push({
      sym,
      count: list.length,
      maxDur: Math.max(...list.map((e) => e.durMin)),
      avgDur: list.reduce((s, e) => s + e.durMin, 0) / list.length,
      maxKimp: kimp.length ? Math.max(...kimp.map((e) => e.peak)) : null,
      avgKimp: kimp.length ? kimp.reduce((s, e) => s + e.peak, 0) / kimp.length : null,
      // 역프 peak 는 음수 — "최대 역프" 는 가장 크게 벌어진 값(최솟값)이다
      maxRev: rev.length ? Math.min(...rev.map((e) => e.peak)) : null,
      avgRev: rev.length ? rev.reduce((s, e) => s + e.peak, 0) / rev.length : null,
      latest: Math.max(...list.map((e) => e.start)),
    })
  }
  return out
}

function PctCell({ v }: { v: number | null }) {
  if (v === null) return <span style={{ color: DIM_TEXT }}>–</span>
  return <span style={{ color: pctColor(v) }}>{fmtPct(v)}</span>
}

/** 타임라인 1줄 — 기간 대비 위치·폭 비율, 짧은 사건도 보이게 최소폭 보장 (§3.6). */
function TimelineRow(props: { label: string; color: string; events: MockEvent[]; now: number; periodMs: number }) {
  const startOfPeriod = props.now - props.periodMs
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 10, width: 30, color: DIM_TEXT, flex: 'none' }}>{props.label}</span>
      <div
        style={{
          position: 'relative',
          flex: 1,
          height: 12,
          background: 'color-mix(in srgb, var(--color-text) 6%, transparent)',
          borderRadius: 3,
          overflow: 'hidden',
        }}
      >
        {props.events.map((e, i) => {
          const left = Math.max(0, ((e.start - startOfPeriod) / props.periodMs) * 100)
          const width = Math.max(0.6, ((e.durMin * 60_000) / props.periodMs) * 100)
          return (
            <span
              key={i}
              style={{
                position: 'absolute',
                top: 0,
                bottom: 0,
                left: `${Math.min(left, 99.4)}%`,
                width: `${width}%`,
                background: props.color,
                borderRadius: 2,
              }}
            />
          )
        })}
      </div>
    </div>
  )
}

export default function HistoryTab({
  feed,
  now,
  selSym,
  onSelect,
}: {
  feed: Feed
  now: number
  selSym: string
  onSelect: (sym: string) => void
}) {
  const [per, setPer] = useState<Per>('30d')
  const [type, setType] = useState<TypeFilter>('all')
  const [dom, setDom] = useState<DomFilter>('all')
  const [thr, setThr] = useState(1.0)
  const [sort, setSort] = useState<{ col: SortCol; asc: boolean }>({ col: 'count', asc: false })

  // 필터 = peak ≥ 기준(역프는 음수라 크기로 비교) && 유형 && 거래소 (§3.6)
  const events = feed
    .events(per, now)
    .filter((e) => Math.abs(e.peak) >= thr && (type === 'all' || e.type === type) && (dom === 'all' || e.dom === dom))

  const stats = aggregate(events)
  const dir = sort.asc ? 1 : -1
  const sorted = [...stats].sort((a, b) => {
    const key = (s: SymStat): number | string | null =>
      sort.col === 'sym' ? s.sym
      : sort.col === 'count' ? s.count
      : sort.col === 'maxDur' ? s.maxDur
      : sort.col === 'avgDur' ? s.avgDur
      : sort.col === 'maxKimp' ? s.maxKimp
      : sort.col === 'avgKimp' ? s.avgKimp
      : sort.col === 'maxRev' ? s.maxRev
      : sort.col === 'avgRev' ? s.avgRev
      : s.latest
    const ka = key(a)
    const kb = key(b)
    // null 은 방향과 무관하게 뒤 (§3.6)
    if (ka === null && kb === null) return a.sym.localeCompare(b.sym)
    if (ka === null) return 1
    if (kb === null) return -1
    const d = typeof ka === 'string' ? ka.localeCompare(kb as string) : ka - (kb as number)
    return d * dir || a.sym.localeCompare(b.sym)
  })
  const top30 = sorted.slice(0, 30)

  function clickSort(col: SortCol) {
    // 같은 열 재클릭 시 방향 반전, 기본은 횟수 내림차순 (§3.6)
    setSort((s) => (s.col === col ? { col, asc: !s.asc } : { col, asc: col === 'sym' }))
  }

  // 우측 — 선택된 심볼의 요약·타임라인·로그
  const mine = events.filter((e) => e.sym === selSym)
  const kimpMine = mine.filter((e) => e.type === 'kimp')
  const revMine = mine.filter((e) => e.type === 'rev')
  const periodMs = PER_MS[per]
  const totalDurMin = mine.reduce((s, e) => s + e.durMin, 0)
  const occupancy = (totalDurMin * 60_000 * 100) / periodMs
  const avgDur = mine.length ? totalDurMin / mine.length : 0
  const maxDur = mine.length ? Math.max(...mine.map((e) => e.durMin)) : 0
  const log = [...mine].sort((a, b) => b.start - a.start).slice(0, 20)

  const cols: { id: SortCol; label: string }[] = [
    { id: 'sym', label: '티커' },
    { id: 'count', label: '횟수' },
    { id: 'maxDur', label: '최대 지속' },
    { id: 'avgDur', label: '평균 지속' },
    { id: 'maxKimp', label: '최대 김프' },
    { id: 'avgKimp', label: '평균 김프' },
    { id: 'maxRev', label: '최대 역프' },
    { id: 'avgRev', label: '평균 역프' },
    { id: 'latest', label: '최신' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* 필터바 (§3.6) */}
      <div style={barStyle}>
        <span style={{ fontSize: 11, color: DIM_TEXT }}>기간</span>
        <Seg
          options={[
            { id: '7d', label: '1주' },
            { id: '30d', label: '1달' },
            { id: '90d', label: '3달' },
          ]}
          value={per}
          onChange={(id) => setPer(id as Per)}
        />
        <Seg
          options={[
            { id: 'all', label: '전체' },
            { id: 'kimp', label: '김프만' },
            { id: 'rev', label: '역프만' },
          ]}
          value={type}
          onChange={(id) => setType(id as TypeFilter)}
        />
        <Seg
          options={[
            { id: 'all', label: '전체' },
            { id: '업비트', label: '업비트' },
            { id: '빗썸', label: '빗썸' },
          ]}
          value={dom}
          onChange={(id) => setDom(id as DomFilter)}
        />
        <NumField label="사건 기준 스프레드 ≥" value={thr} onChange={setThr} step={0.1} />
        <span style={{ marginLeft: 'auto', fontSize: 11, color: DIM_TEXT }}>
          사건 = 스프레드가 기준값 이상으로 출현한 시점부터 소멸까지 · 기간 내 {events.length}건
        </span>
      </div>

      {/* 본문 — 좌 통계 표 · 우 요약/타임라인/로그 */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: 12, padding: '12px 16px', overflow: 'hidden' }}>
        <div style={{ ...cardStyle, flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ ...kicker, marginBottom: 8 }}>
            티커별 사건 통계 · {PER_LABEL[per]} — 열 클릭으로 정렬
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            {top30.length === 0 ? (
              <div style={{ padding: '48px 0', textAlign: 'center', color: DIM_TEXT, fontSize: 13 }}>
                기준을 만족하는 사건이 없습니다 — 임계값을 낮춰보세요
              </div>
            ) : (
              <table className="table" style={{ tableLayout: 'auto', fontSize: 13 }}>
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
                            background: 'var(--color-surface)',
                            whiteSpace: 'nowrap',
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
                  {top30.map((s) => {
                    const selected = s.sym === selSym
                    return (
                      <tr
                        key={s.sym}
                        className="hv-row"
                        onClick={() => onSelect(s.sym)}
                        style={{
                          cursor: 'pointer',
                          background: selected
                            ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)'
                            : undefined,
                        }}
                      >
                        <td style={{ fontWeight: 600, color: selected ? 'var(--color-accent)' : undefined }}>
                          {s.sym}
                        </td>
                        <td>{s.count}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmtDur(s.maxDur)}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmtDur(s.avgDur)}</td>
                        <td style={{ whiteSpace: 'nowrap' }}><PctCell v={s.maxKimp} /></td>
                        <td style={{ whiteSpace: 'nowrap' }}><PctCell v={s.avgKimp} /></td>
                        <td style={{ whiteSpace: 'nowrap' }}><PctCell v={s.maxRev} /></td>
                        <td style={{ whiteSpace: 'nowrap' }}><PctCell v={s.avgRev} /></td>
                        <td style={{ whiteSpace: 'nowrap', color: DIM_TEXT }}>{fmtAgo((now - s.latest) / 1000)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div style={{ width: 380, flex: 'none', display: 'flex', flexDirection: 'column', gap: 12, overflow: 'auto' }}>
          {/* 요약 카드 (§3.6) */}
          <div style={cardStyle}>
            <div style={kicker}>선택된 심볼</div>
            <div style={{ fontSize: 20, fontWeight: 700, margin: '2px 0 8px' }}>{selSym}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 24px' }}>
              <div>
                <div style={{ fontSize: 10, color: DIM_TEXT }}>총 사건</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {mine.length}건{' '}
                  <span style={{ fontSize: 11, color: DIM_TEXT }}>
                    김프 {kimpMine.length} · 역프 {revMine.length}
                  </span>
                </div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: DIM_TEXT }}>평균 · 최장 지속</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {mine.length ? `${fmtDur(avgDur)} · ${fmtDur(maxDur)}` : '–'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: DIM_TEXT }}>기간 점유율</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{occupancy.toFixed(2)}%</div>
              </div>
            </div>
            {/* 타임라인 2줄 — 김프 accent / 역프 neutral (§3.6) */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 12 }}>
              <TimelineRow label="김프" color="var(--color-accent)" events={kimpMine} now={now} periodMs={periodMs} />
              <TimelineRow
                label="역프"
                color="var(--color-neutral-500)"
                events={revMine}
                now={now}
                periodMs={periodMs}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', paddingLeft: 38, fontSize: 9, color: DIM_TEXT }}>
                <span>0%</span>
                <span>25%</span>
                <span>50%</span>
                <span>75%</span>
                <span>100%</span>
              </div>
            </div>
          </div>

          {/* 사건 로그 (§3.6) */}
          <div style={{ ...cardStyle, flex: 1 }}>
            <div style={{ ...kicker, marginBottom: 8 }}>사건 로그 · {selSym} 최근 20건</div>
            {log.length === 0 ? (
              <div style={{ padding: '32px 0', textAlign: 'center', color: DIM_TEXT, fontSize: 12 }}>
                기간 내 사건 없음
              </div>
            ) : (
              <table className="table" style={{ tableLayout: 'auto', fontSize: 12 }}>
                <thead>
                  <tr>
                    {['유형', '시작', '종료', '지속', '최대 스프레드'].map((h) => (
                      <th key={h} style={{ whiteSpace: 'nowrap' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {log.map((e, i) => {
                    const endMs = e.start + e.durMin * 60_000
                    return (
                      <tr key={i}>
                        <td style={{ color: e.type === 'kimp' ? 'var(--color-accent)' : DIM_TEXT }}>
                          {e.type === 'kimp' ? '김프' : '역프'}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmtTime(e.start)}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          {endMs > now ? <span style={{ color: 'var(--color-ok)' }}>진행 중</span> : fmtTime(endMs)}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmtDur(e.durMin)}</td>
                        <td style={{ whiteSpace: 'nowrap', color: pctColor(e.peak) }}>{fmtPct(e.peak)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
