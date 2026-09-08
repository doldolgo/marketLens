// 기록/통계 탭 — 전 코인 김프/역프 사건 표 + 선택 심볼 요약·타임라인·사건 로그 (스펙 013 §3.5).
// 데이터는 /history/events 하나. 방향 서브탭·기간·거래소가 쿼리이고, 심볼은 클라이언트에서 거른다.
// 참조 디자인(docs/design/reference/tabs/HistoryTab.tsx)의 김프/역프 열 분리 대신 서브탭 — 한 화면은 한 방향만.
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { exName, fmtAgo, fmtPct, fmtTime, pctColor } from '../../shared/format'
import { Empty, Pill, Seg, card, hint, kicker, searchInput, type SegOpt } from '../../shared/ui'
import { useCandles, useEvents } from './api'
import { FX_CHOICES, REAL_FXS, RES_OF_INTERVAL, RES_SEC, isMockFx } from './candles'
import FxChartCard, { ChartSync, ChartToolbar, type PairSeries } from './Chart'
import { mockCandles, mockEvents } from './mock'
import { INTERVAL_SEC, rollup, type Interval } from './rollup'
import { aggregate, durationOf, sortStats, summarize, type SortKey } from './stats'
import type { Dir, Dom, PremiumEvent } from './types'

type Per = '7d' | '30d' | '90d'
const PER_LABEL: Record<Per, string> = { '7d': '1주', '30d': '1달', '90d': '3달' }
const PER_SEC: Record<Per, number> = { '7d': 7 * 86_400, '30d': 30 * 86_400, '90d': 90 * 86_400 }
const DIR_LABEL: Record<Dir, string> = { kimp: '김프', reverse: '역프' }
const DOMS_ORDER: Dom[] = ['upbit', 'bithumb']
const NO_EVENTS: PremiumEvent[] = []
// 서브탭·수치 색은 스프레드 탭 관례 — 김프 = 상승(POS) 색, 역프 = 하락(NEG) 색
const dirColor = (dir: Dir) => pctColor(dir === 'kimp' ? 1 : -1)

/** 티커 | 상태 | 횟수 | 최대 지속 | 평균 지속 | 최대 스프레드 | 평균 스프레드 | 최신 */
const RANK_GRID = '64px 120px repeat(6, 1fr)'
/** 거래소 | 시작 | 종료 | 지속 | 최대 스프레드 */
const LOG_GRID = '70px 1fr 1fr 110px 120px'
const HEADERS: [SortKey, string][] = [
  ['ongoingSince', '상태'], ['cnt', '횟수'], ['maxDur', '최대 지속'], ['avgDur', '평균 지속'],
  ['maxPct', '최대 스프레드'], ['avgPct', '평균 스프레드'], ['last', '최신'],
]

/** 지속 초 → 사람이 읽는 표기. */
function fmtDur(sec: number): string {
  const min = sec / 60
  if (min < 60) return `${Math.round(min)}분`
  if (min < 60 * 24) return `${(min / 60).toFixed(1)}시간`
  return `${(min / 60 / 24).toFixed(1)}일`
}

/** 축 라벨 M/D (로컬). */
function fmtMd(ms: number): string {
  const d = new Date(ms)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

const rankCell: CSSProperties = { fontSize: 11.5, fontVariantNumeric: 'tabular-nums', textAlign: 'right', padding: '0 4px' }
const numCell: CSSProperties = { padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

function StatusPill({ since, nowSec }: { since: number | null; nowSec: number }) {
  if (since == null) return <Pill tone="neutral">끝남</Pill>
  return <Pill tone="accent">진행 중 · {fmtDur(nowSec - since)}</Pill>
}

export default function HistoryTab({ now, selSym, onSelect }: {
  now: number; selSym: string; onSelect: (sym: string) => void
}) {
  const [dir, setDir] = useState<Dir>('kimp')
  const [per, setPer] = useState<Per>('7d')
  const [dom, setDom] = useState<Dom | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('cnt')
  const [sortDir, setSortDir] = useState(-1)
  // 심볼 검색 — Enter 로 선택 (표 클릭과 같은 onSelect)
  const [q, setQ] = useState('')
  // 차트 거래소 선택(국내·해외 각각 여러 개). 국내는 위 필터를 그대로 따른다 — 전체면 둘 다, 하나면 그 하나.
  // 빗썸에만 있는 코인(HEMI 등)이 기본 선택 업비트 때문에 빈 화면이 되지 않게. 툴바 체크박스는 그 뒤 더 좁힐 때만
  const [chartDoms, setChartDoms] = useState<Dom[]>(DOMS_ORDER)
  const [chartFxs, setChartFxs] = useState<string[]>(['binance'])
  useEffect(() => { setChartDoms(dom ? [dom] : DOMS_ORDER) }, [dom])
  // 카드 간 시간축·십자선 연동 — 탭이 사는 동안 하나
  const [sync] = useState(() => new ChartSync())
  // 봉 종류 — 계층(1m·5m·1h·4h·1d) 하나를 골라 그 안에서 접는다 (candles.ts·rollup.ts)
  const [interval, setInterval_] = useState<Interval>('1m')
  const res = RES_OF_INTERVAL[interval]
  // 왼쪽으로 끌어 더 붙인 청크 수 — (심볼, 쌍, 방향, 계층) 이 바뀌면 0 부터. effect 로 맞추면 옛 값으로 한 번 그리고 다시 그리는
  // 2단계가 되어 차트 범위가 어긋나므로, 같은 렌더에서 바로 계산한다.
  const chartKey = `${selSym}:${chartDoms.join('+')}:${dir}:${res}`
  const [olderState, setOlderState] = useState({ key: chartKey, n: 0 })
  const older = olderState.key === chartKey ? olderState.n : 0

  const nowSec = Math.floor(now / 1000)
  const periodSec = PER_SEC[per]
  const { result, loading } = useEvents({ dir, dom, periodSec })
  // 실패·미도착 때 `[]` 를 매 렌더 새로 만들면 아래 memo 들이 초마다 깨져 차트가 초마다 다시 그려진다 → 고정 빈 배열
  const events: PremiumEvent[] = result?.kind === 'ok' ? result.data.events : NO_EVENTS

  // 좌 표: 심볼별 집계 → 정렬 → 상위 30
  const rank = sortStats(aggregate(events, nowSec), sortKey, sortDir).slice(0, 30)
  const onSort = (k: SortKey) => {
    if (k === sortKey) setSortDir(-sortDir)
    else { setSortKey(k); setSortDir(-1) }
  }

  // 우 column: 선택 심볼의 사건(최신순)·요약·타임라인
  // events 는 60초 재조회 때만 새 배열 — 매초 리렌더에서 같은 참조를 유지해 차트 setData 가 초마다 돌지 않게 memo
  const mine = useMemo(() => events.filter((e) => e.base === selSym).sort((a, b) => b.startTs - a.startTs), [events, selSym])
  const sum = summarize(mine, nowSec, periodSec)
  const t0Sec = nowSec - periodSec
  const color = dirColor(dir)

  // 차트 데이터 — /history/candles 청크(쌍별)를 받아 봉 종류로 접는다. 접기까지 여기서 끝내 차트는 그리기만 한다.
  // 서버가 주는 해외 거래소는 binance 뿐이라 항상 그것만 부르고, mock 해외 거래소 카드는 그 봉을 변형해 만든다(015 시안, mock.ts)
  const domsKey = chartDoms.join('+')
  const fxsKey = chartFxs.join('+')
  const { pairs, loading: candlesLoading, errorStatus: candlesError, oldestReached } = useCandles({
    base: selSym, dir, doms: DOMS_ORDER.filter((x) => chartDoms.includes(x)), fxs: REAL_FXS, interval, older,
  })
  // 카드 순서는 선택 순서가 아니라 FX_CHOICES 순서. 카드 = 해외 1개 × 선택한 국내 전부
  const cards = useMemo<{ fx: string; series: PairSeries[] }[]>(() => {
    const fxs = FX_CHOICES.map((f) => f.id).filter((id) => chartFxs.includes(id))
    return fxs.map((fx) => ({
      fx,
      series: pairs.filter((p) => p.fx === REAL_FXS[0]).map((p) => {
        const raw = isMockFx(fx) ? mockCandles(fx, p.candles, dir, RES_SEC[res]) : p.candles
        return { dom: p.dom, fx, candles: rollup(raw, INTERVAL_SEC[interval], RES_SEC[res]) }
      }),
    }))
  }, [pairs, fxsKey, dir, interval, res])
  const chartEvents = useMemo(() => mine.filter((e) => chartDoms.includes(e.dom)), [mine, domsKey])
  // 카드별 사건 — 렌더마다 새 배열을 만들면 카드의 음영 effect 가 초마다 돌므로 memo (014 교훈)
  const cardEvents = useMemo<Record<string, PremiumEvent[]>>(
    () => Object.fromEntries(FX_CHOICES.map((f) => [f.id, isMockFx(f.id) ? mockEvents(f.id, chartEvents) : chartEvents.filter((e) => e.fx === f.id)])),
    [chartEvents],
  )
  const needOlder = () => { if (!oldestReached) setOlderState({ key: chartKey, n: older + 1 }) }

  const dirOpts: SegOpt[] = (['kimp', 'reverse'] as Dir[]).map((d) => ({
    label: DIR_LABEL[d], onClick: () => setDir(d),
    bg: dir === d ? 'var(--color-neutral-900)' : 'transparent',
    color: dir === d ? dirColor(d) : 'var(--color-neutral-500)',
  }))
  const seg = (label: string, active: boolean, onClick: () => void): SegOpt => ({
    label, onClick,
    bg: active ? 'var(--color-neutral-900)' : 'transparent',
    color: active ? 'var(--color-accent-300)' : 'var(--color-neutral-500)',
  })

  return (
    <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
      {/* 폭 92% 가운데 정렬 — 양옆에 여백을 조금 둬 차트 카드가 화면 끝까지 꽉 차지 않게. 필터바·표도 같이 좁혀 줄을 맞춘다 */}
      <div style={{ width: '92%', margin: '0 auto', padding: 'var(--space-6) 0', display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>

        {/* 방향 서브탭 + 필터바 (§3.5) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <Seg opts={dirOpts} pad="6px 16px" />
          <Seg opts={(['7d', '30d', '90d'] as Per[]).map((p) => seg(PER_LABEL[p], per === p, () => setPer(p)))} />
          <Seg opts={[
            seg('전체', dom === null, () => setDom(null)),
            seg('업비트', dom === 'upbit', () => setDom('upbit')),
            seg('빗썸', dom === 'bithumb', () => setDom('bithumb')),
          ]} />
          <input className="input" placeholder="심볼 검색 → Enter" value={q} style={searchInput}
            onChange={(e) => setQ(e.target.value.toUpperCase())}
            onKeyDown={(e) => { if (e.key === 'Enter' && q.trim()) { onSelect(q.trim()); setQ('') } }} />
          <span style={{ ...hint, marginLeft: 'auto' }}>
            사건 = 원값 {DIR_LABEL[dir]} 1.0% 진입 → 0.5% 이탈, 1분 이하 제외 · 기간 내 {events.length}건
            {loading && <span style={{ color: 'var(--color-accent-300)', marginLeft: 8 }}>조회 중…</span>}
          </span>
        </div>

        {/* 선택 심볼 봉 차트 — 해외 거래소 1개 = 카드 1개, 김프 + 가격 + 거래소별 입출금 (스펙 014 §3.7 · 015) */}
        <ChartToolbar sym={selSym} dir={dir} interval={interval} onInterval={setInterval_}
          doms={chartDoms} onDoms={setChartDoms} fxs={chartFxs} onFxs={setChartFxs}
          loading={candlesLoading} errorStatus={candlesError} />
        {/* 카드 사이는 다른 블록보다 넓게 — 카드가 붙어 있으면 한 덩어리로 보여 답답하다 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>
          {cards.filter((c) => c.series.length > 0).map((c) => (
            <FxChartCard key={c.fx} fx={c.fx} dir={dir} interval={interval}
              series={c.series} events={cardEvents[c.fx] ?? NO_EVENTS} onNeedOlder={needOlder} loading={candlesLoading} sync={sync} />
          ))}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 'var(--space-4)', alignItems: 'start' }}>

          {/* 좌: 티커별 사건 표 */}
          <div style={{ ...card, padding: 'var(--space-4) 0' }}>
            <div style={{ ...kicker, padding: '0 var(--space-6) var(--space-2)' }}>티커별 {DIR_LABEL[dir]} 사건 · {PER_LABEL[per]} — 열 클릭으로 정렬</div>
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: 720 }}>
                <div style={{ display: 'grid', gridTemplateColumns: RANK_GRID, padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)' }}>
                  <button onClick={() => onSort('sym')} className="hv-txt"
                    style={{ appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', padding: '7px 0', cursor: 'pointer', textAlign: 'left', color: sortKey === 'sym' ? 'var(--color-accent-300)' : 'var(--color-neutral-600)' }}>
                    티커{sortKey === 'sym' ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}
                  </button>
                  {HEADERS.map(([k, label]) => (
                    <button key={k} onClick={() => onSort(k)} className="hv-txt"
                      style={{
                        appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 10.5,
                        letterSpacing: '0.04em', textTransform: 'uppercase', padding: '7px 4px', cursor: 'pointer',
                        textAlign: k === 'ongoingSince' ? 'left' : 'right', whiteSpace: 'nowrap',
                        color: k === sortKey ? 'var(--color-accent-300)' : 'var(--color-neutral-600)',
                      }}>
                      {label}{k === sortKey ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}
                    </button>
                  ))}
                </div>
                {rank.map((x) => (
                  <button key={x.sym} onClick={() => onSelect(x.sym)} className="hv-row"
                    style={{
                      display: 'grid', gridTemplateColumns: RANK_GRID, alignItems: 'center', width: '100%',
                      appearance: 'none', border: 'none', font: 'inherit',
                      background: x.sym === selSym ? 'color-mix(in srgb, var(--color-accent) 10%, transparent)' : 'transparent',
                      padding: '0 var(--space-6)', height: 30, cursor: 'pointer', textAlign: 'left',
                      borderBottom: '1px solid color-mix(in srgb, #e9e9ed 5%, transparent)',
                    }}>
                    <span style={{ fontWeight: 500, fontSize: 12.5, color: x.sym === selSym ? 'var(--color-accent-300)' : 'var(--color-text)' }}>{x.sym}</span>
                    <span style={{ padding: '0 4px' }}><StatusPill since={x.ongoingSince} nowSec={nowSec} /></span>
                    <span style={rankCell}>{x.cnt}</span>
                    <span style={{ ...rankCell, color: 'var(--color-neutral-300)' }}>{fmtDur(x.maxDur)}</span>
                    <span style={{ ...rankCell, color: 'var(--color-neutral-300)' }}>{x.avgDur != null ? fmtDur(x.avgDur) : '–'}</span>
                    <span style={{ ...rankCell, color }}>{fmtPct(x.maxPct)}</span>
                    <span style={{ ...rankCell, color }}>{fmtPct(x.avgPct)}</span>
                    <span style={{ ...rankCell, fontSize: 11, color: 'var(--color-neutral-500)' }}>{fmtAgo(nowSec - x.last)}</span>
                  </button>
                ))}
              </div>
            </div>
            {result?.kind === 'error' && <Empty size={12}>기록을 불러오지 못했습니다 (HTTP {result.status})</Empty>}
            {result?.kind !== 'error' && rank.length === 0 && !loading && <Empty size={12}>기간 내 사건 없음</Empty>}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            {/* 우: 선택 심볼 요약 + 타임라인 1줄 */}
            <div style={{ ...card, padding: 'var(--space-6)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
                <span style={{ fontFamily: 'var(--font-heading)', fontSize: 22, fontWeight: 500 }}>{selSym}</span>
                <StatusPill since={sum.ongoingSince} nowSec={nowSec} />
                <span style={{ ...hint, marginLeft: 'auto' }}>왼쪽 표에서 티커를 클릭해 선택</span>
              </div>
              <div style={{ display: 'flex', gap: 'var(--space-8)', flexWrap: 'wrap', marginBottom: 'var(--space-6)' }}>
                {([
                  ['총 사건', sum.total ? `${sum.total}건` : '–'],
                  ['평균 지속', sum.avgDur != null ? fmtDur(sum.avgDur) : '–'],
                  ['최장 지속', sum.maxDur != null ? fmtDur(sum.maxDur) : '–'],
                  ['기간 점유율', sum.share != null ? `${(sum.share * 100).toFixed(1)}%` : '–'],
                ] as [string, string][]).map(([label, value]) => (
                  <div key={label}>
                    <div style={{ ...kicker, marginBottom: 2 }}>{label}</div>
                    <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
                  </div>
                ))}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '40px 1fr', gap: '6px 10px', alignItems: 'center' }}>
                <span style={{ fontSize: 11, color }}>{DIR_LABEL[dir]}</span>
                <div style={{ position: 'relative', height: 20, background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
                  {mine.map((e) => {
                    const dur = durationOf(e, nowSec)
                    return (
                      <span key={`${e.dom}-${e.startTs}`}
                        title={`${exName(e.dom)} · ${fmtTime(e.startTs * 1000)} 시작 · ${fmtDur(dur)} 지속 · 최대 ${fmtPct(e.maxPercent)}`}
                        style={{
                          position: 'absolute', top: 3, bottom: 3, minWidth: 2, borderRadius: 2, background: color,
                          left: `${((e.startTs - t0Sec) / periodSec * 100).toFixed(2)}%`,
                          width: `${Math.max(0.4, dur / periodSec * 100).toFixed(2)}%`,
                        }} />
                    )
                  })}
                </div>
                <span />
                <div style={{ position: 'relative', height: 14 }}>
                  {[0, 0.25, 0.5, 0.75, 1].map((f) => (
                    <span key={f} style={{ position: 'absolute', left: `${(f * 100).toFixed(0)}%`, transform: 'translateX(-50%)', fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>
                      {fmtMd((t0Sec + f * periodSec) * 1000)}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* 우: 사건 로그 — 최근 20건 */}
            <div style={{ ...card, padding: 'var(--space-2) 0' }}>
              <div style={{ ...kicker, padding: 'var(--space-4) var(--space-6) var(--space-2)' }}>사건 로그 · {selSym} 최근 20건</div>
              <div style={{ display: 'grid', gridTemplateColumns: LOG_GRID, padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)', fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--color-neutral-600)' }}>
                <span style={{ padding: '6px 8px 6px 0' }}>거래소</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>시작</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>종료</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>지속시간</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>최대 스프레드</span>
              </div>
              {mine.slice(0, 20).map((e) => (
                <div key={`${e.dom}-${e.startTs}`} style={{
                  display: 'grid', gridTemplateColumns: LOG_GRID, alignItems: 'center', height: 32, padding: '0 var(--space-6)',
                  borderBottom: '1px solid color-mix(in srgb, #e9e9ed 6%, transparent)',
                  // 진행 중 행은 옅은 accent 배경 — 끝난 행과 한눈에 구분되게 (§3.5)
                  background: e.ongoing ? 'color-mix(in srgb, var(--color-accent) 6%, transparent)' : 'transparent',
                }}>
                  <Pill tone="neutral">{exName(e.dom)}</Pill>
                  <span style={{ ...numCell, color: 'var(--color-neutral-300)' }}>{fmtTime(e.startTs * 1000)}</span>
                  <span style={{ ...numCell, color: e.ongoing ? 'var(--color-accent-300)' : 'var(--color-neutral-400)' }}>{e.ongoing ? '진행 중' : fmtTime((e.endTs ?? e.startTs) * 1000)}</span>
                  <span style={numCell}>{fmtDur(durationOf(e, nowSec))}</span>
                  <span style={{ ...numCell, fontWeight: 500, color }}>{fmtPct(e.maxPercent)}</span>
                </div>
              ))}
              {mine.length === 0 && <Empty size={12}>기간 내 사건 없음</Empty>}
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}
