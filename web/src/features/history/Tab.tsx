// 기록/통계 탭 — mock 유지, /history/* 호출 금지 (스펙 005 §3.6, 구조는 docs/design/reference/tabs/HistoryTab.tsx).
// 데이터는 002 공유 피드의 mock 사건 목록(feed.events)이고 실데이터 연결은 후속 스펙.
import { useState, type CSSProperties } from 'react'
import { fmtAgo, fmtPct, fmtTime, pctColor } from '../../shared/format'
import type { Feed, MockEvent } from '../../shared/types'
import { Empty, NumField, Pill, Seg, segOpt, card, gridHead, hint, kicker } from '../../shared/ui'

type Per = '7d' | '30d' | '90d'
type TypeFilter = 'all' | 'kimp' | 'rev'
type DomFilter = 'all' | '업비트' | '빗썸'
type SortCol = 'count' | 'maxDur' | 'avgDur' | 'maxKimp' | 'avgKimp' | 'maxRev' | 'avgRev' | 'latest'

const PER_LABEL: Record<Per, string> = { '7d': '1주', '30d': '1달', '90d': '3달' }
const PER_MS: Record<Per, number> = { '7d': 7 * 86_400_000, '30d': 30 * 86_400_000, '90d': 90 * 86_400_000 }

/** 티커 | 횟수 | 최대 지속 | 평균 지속 | 최대 김프 | 평균 김프 | 최대 역프 | 평균 역프 | 최신 */
const RANK_GRID = '64px repeat(8, 1fr)'
/** 유형 | 시작 | 종료 | 지속시간 | 최대 스프레드 */
const LOG_GRID = '70px 1fr 1fr 110px 120px'

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

/** 지속 분 → 사람이 읽는 표기. */
function fmtDur(min: number): string {
  if (min < 60) return `${Math.round(min)}분`
  return `${(min / 60).toFixed(1)}시간`
}

/** 타임라인 축 라벨 M/D (로컬). */
function fmtD(ms: number): string {
  const d = new Date(ms)
  return `${d.getMonth() + 1}/${d.getDate()}`
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

const rankCell: CSSProperties = { fontSize: 11.5, fontVariantNumeric: 'tabular-nums', textAlign: 'right', padding: '0 4px' }
const pctCell = (v: number | null): CSSProperties => ({ ...rankCell, color: v !== null ? pctColor(v) : 'var(--color-neutral-700)' })
const pctText = (v: number | null): string => (v !== null ? fmtPct(v) : '–')
const track: CSSProperties = { position: 'relative', height: 20, background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }

export default function HistoryTab({ feed, now, selSym, onSelect }: {
  feed: Feed; now: number; selSym: string; onSelect: (sym: string) => void
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

  const dir = sort.asc ? 1 : -1
  const rank = aggregate(events).sort((a, b) => {
    const ka = a[sort.col]
    const kb = b[sort.col]
    // null 은 방향과 무관하게 뒤 (§3.6)
    if (ka === null && kb === null) return a.sym.localeCompare(b.sym)
    if (ka === null) return 1
    if (kb === null) return -1
    return (ka - kb) * dir || a.sym.localeCompare(b.sym)
  })

  function clickSort(col: SortCol) {
    // 같은 열 재클릭 시 방향 반전, 기본 횟수 내림차순 (§3.6)
    setSort((s) => (s.col === col ? { col, asc: !s.asc } : { col, asc: false }))
  }

  const headers: [SortCol, string][] = [
    ['count', '횟수'], ['maxDur', '최대 지속'], ['avgDur', '평균 지속'],
    ['maxKimp', '최대 김프'], ['avgKimp', '평균 김프'], ['maxRev', '최대 역프'], ['avgRev', '평균 역프'], ['latest', '최신'],
  ]

  // 선택 티커의 사건 목록·타임라인
  const evs = events.filter((e) => e.sym === selSym).sort((a, b) => b.start - a.start)
  const kEvs = evs.filter((e) => e.type === 'kimp')
  const rEvs = evs.filter((e) => e.type === 'rev')
  const durs = evs.map((e) => e.durMin)
  const sumDur = durs.reduce((a, b) => a + b, 0)
  const spanMs = PER_MS[per]
  const t0 = now - spanMs
  // 위치·폭은 기간 대비 비율, 짧은 사건도 보이게 최소폭 보장 (§3.6)
  const mkBar = (e: MockEvent) => ({
    left: (((e.start - t0) / spanMs) * 100).toFixed(2) + '%',
    width: Math.max(0.4, ((e.durMin * 60e3) / spanMs) * 100).toFixed(2) + '%',
    title: (e.type === 'kimp' ? '김프' : '역프') + ' · ' + fmtTime(e.start) + ' 시작 · ' + fmtDur(e.durMin) + ' 지속 · 최대 ' + fmtPct(e.peak),
  })
  const barEl = (e: MockEvent, i: number, color: string) => {
    const b = mkBar(e)
    return <span key={i} title={b.title} style={{ position: 'absolute', top: 3, bottom: 3, left: b.left, width: b.width, minWidth: 2, background: color, borderRadius: 2 }} />
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
      <div style={{ padding: 'var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>

        {/* 필터바 (§3.6) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <Seg opts={(['7d', '30d', '90d'] as Per[]).map((p) => segOpt(PER_LABEL[p], per === p, () => setPer(p)))} />
          <Seg opts={[['all', '전체'], ['kimp', '김프만'], ['rev', '역프만']].map(([id, l]) => segOpt(l, type === id, () => setType(id as TypeFilter)))} />
          <Seg opts={[['all', '전체'], ['업비트', '업비트'], ['빗썸', '빗썸']].map(([id, l]) => segOpt(l, dom === id, () => setDom(id as DomFilter)))} />
          <NumField label="사건 기준 스프레드 ≥" value={thr} step={0.1} onChange={setThr} />
          <span style={{ ...hint, marginLeft: 'auto' }}>사건 = 스프레드가 기준값 이상으로 출현한 시점부터 소멸까지 · 기간 내 {events.length}건</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 'var(--space-4)', alignItems: 'start' }}>

          {/* 티커별 사건 통계 — 상위 30행 */}
          <div style={{ ...card, padding: 'var(--space-4) 0' }}>
            <div style={{ ...kicker, padding: '0 var(--space-6) var(--space-2)' }}>티커별 사건 통계 · {PER_LABEL[per]} — 열 클릭으로 정렬</div>
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: 720 }}>
                <div style={{ display: 'grid', gridTemplateColumns: RANK_GRID, padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)' }}>
                  <span style={{ fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--color-neutral-600)', padding: '7px 0' }}>티커</span>
                  {headers.map(([k, text]) => (
                    <button key={k} onClick={() => clickSort(k)} className="hv-txt"
                      style={{
                        appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 10.5,
                        letterSpacing: '0.04em', textTransform: 'uppercase', padding: '7px 4px', cursor: 'pointer',
                        textAlign: 'right', whiteSpace: 'nowrap',
                        color: k === sort.col ? 'var(--color-accent-300)' : 'var(--color-neutral-600)',
                      }}>
                      {text}{k === sort.col ? (dir < 0 ? ' ▾' : ' ▴') : ''}
                    </button>
                  ))}
                </div>
                {rank.slice(0, 30).map((x) => (
                  <button key={x.sym} onClick={() => onSelect(x.sym)} className="hv-row"
                    style={{
                      display: 'grid', gridTemplateColumns: RANK_GRID, alignItems: 'center', width: '100%',
                      appearance: 'none', border: 'none', font: 'inherit',
                      background: x.sym === selSym ? 'color-mix(in srgb, var(--color-accent) 10%, transparent)' : 'transparent',
                      padding: '0 var(--space-6)', height: 30, cursor: 'pointer', textAlign: 'left',
                      borderBottom: '1px solid color-mix(in srgb, #e9e9ed 5%, transparent)',
                    }}>
                    <span style={{ fontWeight: 500, fontSize: 12.5, color: x.sym === selSym ? 'var(--color-accent-300)' : 'var(--color-text)' }}>{x.sym}</span>
                    <span style={rankCell}>{x.count}</span>
                    <span style={{ ...rankCell, color: 'var(--color-neutral-300)' }}>{fmtDur(x.maxDur)}</span>
                    <span style={{ ...rankCell, color: 'var(--color-neutral-300)' }}>{fmtDur(x.avgDur)}</span>
                    <span style={pctCell(x.maxKimp)}>{pctText(x.maxKimp)}</span>
                    <span style={pctCell(x.avgKimp)}>{pctText(x.avgKimp)}</span>
                    <span style={pctCell(x.maxRev)}>{pctText(x.maxRev)}</span>
                    <span style={pctCell(x.avgRev)}>{pctText(x.avgRev)}</span>
                    <span style={{ ...rankCell, fontSize: 11, color: 'var(--color-neutral-500)' }}>{fmtAgo((now - x.latest) / 1000)}</span>
                  </button>
                ))}
              </div>
            </div>
            {rank.length === 0 && <Empty size={12}>기준을 만족하는 사건이 없습니다 — 임계값을 낮춰보세요</Empty>}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            {/* 선택 티커 요약 + 타임라인 */}
            <div style={{ ...card, padding: 'var(--space-6)' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
                <span style={{ fontFamily: 'var(--font-heading)', fontSize: 22, fontWeight: 500 }}>{selSym}</span>
                <span style={hint}>왼쪽 목록에서 티커를 클릭해 선택</span>
              </div>
              <div style={{ display: 'flex', gap: 'var(--space-8)', flexWrap: 'wrap', marginBottom: 'var(--space-6)' }}>
                <div>
                  <div style={{ ...kicker, marginBottom: 2 }}>총 사건</div>
                  <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{evs.length}건 <span style={{ fontSize: 11, color: 'var(--color-neutral-500)' }}>김프 {kEvs.length} · 역프 {rEvs.length}</span></div>
                </div>
                <div>
                  <div style={{ ...kicker, marginBottom: 2 }}>평균 지속</div>
                  <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{durs.length ? fmtDur(sumDur / durs.length) : '–'}</div>
                </div>
                <div>
                  <div style={{ ...kicker, marginBottom: 2 }}>최장 지속</div>
                  <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{durs.length ? fmtDur(Math.max(...durs)) : '–'}</div>
                </div>
                <div>
                  <div style={{ ...kicker, marginBottom: 2 }}>기간 점유율</div>
                  <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{durs.length ? ((sumDur * 60e3) / spanMs * 100).toFixed(1) + '%' : '–'}</div>
                </div>
              </div>
              {/* 타임라인 2줄 — 김프 accent / 역프 neutral, 축 라벨은 기간을 5등분한 날짜 */}
              <div style={{ display: 'grid', gridTemplateColumns: '40px 1fr', gap: '6px 10px', alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: 'var(--color-accent-300)' }}>김프</span>
                <div style={track}>{kEvs.map((e, i) => barEl(e, i, 'var(--color-accent-500)'))}</div>
                <span style={{ fontSize: 11, color: 'var(--color-neutral-400)' }}>역프</span>
                <div style={track}>{rEvs.map((e, i) => barEl(e, i, 'var(--color-neutral-600)'))}</div>
                <span />
                <div style={{ position: 'relative', height: 14 }}>
                  {[0, 0.25, 0.5, 0.75, 1].map((f) => (
                    <span key={f} style={{ position: 'absolute', left: (f * 100).toFixed(0) + '%', transform: 'translateX(-50%)', fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>{fmtD(t0 + f * spanMs)}</span>
                  ))}
                </div>
              </div>
            </div>

            {/* 사건 로그 — 최근 20건 */}
            <div style={{ ...card, padding: 'var(--space-2) 0' }}>
              <div style={{ ...kicker, padding: 'var(--space-4) var(--space-6) var(--space-2)' }}>사건 로그 · {selSym} 최근 20건</div>
              <div style={gridHead(LOG_GRID)}>
                <span style={{ padding: '6px 8px 6px 0' }}>유형</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>시작</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>종료</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>지속시간</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>최대 스프레드</span>
              </div>
              {evs.slice(0, 20).map((e, i) => {
                const endT = e.start + e.durMin * 60e3
                const ongoing = endT > now
                return (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: LOG_GRID, alignItems: 'center', height: 32, padding: '0 var(--space-6)', borderBottom: '1px solid color-mix(in srgb, #e9e9ed 6%, transparent)' }}>
                    <Pill tone={e.type === 'kimp' ? 'accent' : 'neutral'} style={e.type === 'kimp' ? { borderColor: 'var(--color-accent-700)' } : { borderColor: 'var(--color-neutral-700)' }}>
                      {e.type === 'kimp' ? '김프' : '역프'}
                    </Pill>
                    <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' }}>{fmtTime(e.start)}</span>
                    <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: ongoing ? 'var(--color-accent-300)' : 'var(--color-neutral-400)' }}>{ongoing ? '진행 중' : fmtTime(endT)}</span>
                    <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtDur(ongoing ? (now - e.start) / 60e3 : e.durMin)}</span>
                    <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 500, color: pctColor(e.peak) }}>{fmtPct(e.peak)}</span>
                  </div>
                )
              })}
              {evs.length === 0 && <Empty size={12}>기간 내 사건 없음</Empty>}
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}
