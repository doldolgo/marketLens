// 기록/통계 탭 — 전 코인 김프/역프 사건 표 + 선택 심볼 요약·타임라인·사건 로그 (스펙 013 §3.5).
// 표·우측 column 은 /history/events 전 코인 조회(방향·기간·국내 거래소가 쿼리, 심볼·해외 거래소는 클라이언트에서 거른다).
// 차트 음영은 선택 심볼만 차트 범위로 따로 조회 — 표 필터와 차트 설정은 서로 독립(맨 위 표 설정 → 표 → 차트 설정 → 차트 순서).
// 참조 디자인(docs/design/reference/tabs/HistoryTab.tsx)의 김프/역프 열 분리 대신 서브탭 — 한 화면은 한 방향만.
// 폭 1079px 이하는 표·요약 두 칸을 한 칸으로 쌓고, 폰(719px 이하)은 필터 접기·정렬 줄·카드 행 (072 §3.4~§3.6).
import { memo, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { exName, fmtAgo, fmtPct, fmtTime, pctColor } from '../../shared/format'
import {
  Empty, FoldButton, Pill, Seg, SortBar, bodyScroll, card, cardLeft, cardRight, cardRow, cardSub, foldPanel, hint, kicker, phoneSearch, searchInput, SYMBOL_INPUT, touchSearch,
  type Header, type SegOpt,
} from '../../shared/ui'
import { alias, isSymbol, list, oneOf, useUrlState, type Codec } from '../../shared/urlState'
import { useViewport } from '../../shared/viewport'
import { useCandles, useEvents } from './api'
import { FX_CHOICES, REAL_FXS, RES_OF_INTERVAL, RES_SEC, isMockFx, neededChunks } from './candles'
import FxChartCard, { ChartSync, ChartToolbar } from './Chart'
import type { PairSeries } from './chartData'
import { mockCandles, mockEvents } from './mock'
import { NET_NONE, netPath } from './network'
import { INTERVALS, INTERVAL_SEC, rollup, type Interval } from './rollup'
import { durationOf, finishStats, fmtDur, prepareStats, sortStats, summarize, type SortKey } from './stats'
import type { SpreadRow } from '../../shared/types'
import type { Dir, Dom, PremiumEvent } from './types'

/** 빈 카드에 띄울 안내 — 이 코인이 다른 해외 거래소에만 있으면 그 이름을 알려 준다. 모르면(피드 없음) null → 기본 문구 */
function emptyHintFor(sym: string, fx: string, availableFxs: string[]): string | null {
  if (availableFxs.length === 0) return null
  if (availableFxs.includes(fx)) return null
  const names = availableFxs.map((id) => FX_CHOICES.find((f) => f.id === id)?.label ?? id).join('·')
  return `${sym} 은 ${names} 에만 있습니다`
}

type Per = '1h' | '4h' | '8h' | '12h' | '24h' | '3d' | '7d' | '30d' | '90d'
/** 기본 기간 — 폰 필터 단추의 숫자(072 §3.4)도 이 값과 견준다 */
const PER_DEFAULT: Per = '7d'
const PERS: Per[] = ['1h', '4h', '8h', '12h', '24h', '3d', '7d', '30d', '90d']
const PER_LABEL: Record<Per, string> = {
  '1h': '1시간', '4h': '4시간', '8h': '8시간', '12h': '12시간', '24h': '24시간',
  '3d': '3일', '7d': '1주', '30d': '1달', '90d': '3달',
}
const PER_SEC: Record<Per, number> = {
  '1h': 3_600, '4h': 4 * 3_600, '8h': 8 * 3_600, '12h': 12 * 3_600, '24h': 24 * 3_600,
  '3d': 3 * 86_400, '7d': 7 * 86_400, '30d': 30 * 86_400, '90d': 90 * 86_400,
}
const DIR_LABEL: Record<Dir, string> = { kimp: '김프', reverse: '역프' }
const DOMS_ORDER: Dom[] = ['upbit', 'bithumb']
const NO_EVENTS: PremiumEvent[] = []
// 서브탭·수치 색은 스프레드 탭 관례 — 김프 = 상승(POS) 색, 역프 = 하락(NEG) 색
const dirColor = (dir: Dir) => pctColor(dir === 'kimp' ? 1 : -1)

/** 티커 | 상태 | 횟수 | 최대 지속 | 평균 지속 | 최대 스프레드 | 평균 스프레드 | 망 | 최신 */
const RANK_GRID = '64px 120px repeat(7, 1fr)'
/** 순위 표의 최소 폭이자, 한 칸으로 쌓인 배치에서 열 표로 그릴 칸 폭 하한 — 더 좁으면 폰과 같은 정렬 줄 + 3줄 카드(072 §3.9).
 * 2026-10-23 실측으로 아홉 열이 640px 까지 겹치지 않아 20px 여유를 뒀다. 예전 800 은 태블릿 세로에서 마지막 열을 가로 스크롤 뒤로 숨겼다 */
const RANK_TABLE_MIN = 660
/** 거래소 | 시작 | 종료 | 지속 | 최대 스프레드 | 망 */
const LOG_GRID = '108px 1fr 1fr 78px 100px 1.2fr'
/** 사건 로그를 열 표로 그릴 칸 폭 하한 — 더 좁으면 폰과 같은 2줄 카드로 그린다(072 §3.9). 넓은 화면의 오른쪽 칸(3:2 의 2)은
 * 화면 폭 1,560px 아래에서 이보다 좁아져 시작·종료·네트워크 머리 글자가 한 자씩 꺾였다 — 그래서 화면이 아니라 칸 폭으로 고른다 */
const LOG_TABLE_MIN = 600

/** 요소 폭이 min 보다 좁은가 — 경계를 넘을 때만 다시 그린다(같은 값이면 상태가 그대로라 React 가 건너뛴다). */
function useNarrowerThan(min: number): [(el: HTMLElement | null) => void, boolean] {
  const [el, setEl] = useState<HTMLElement | null>(null)
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < min))
    ro.observe(el)
    return () => ro.disconnect()
  }, [el, min])
  return [setEl, narrow]
}
/** 셋째 칸은 061 화면 이용 통계의 요소 열쇠(data-el) */
const HEADERS: [SortKey, string, string][] = [
  ['ongoingSince', '상태', 'sort:ongoingsince'], ['cnt', '횟수', 'sort:cnt'], ['maxDur', '최대 지속', 'sort:maxdur'],
  ['avgDur', '평균 지속', 'sort:avgdur'], ['maxPct', '최대 스프레드', 'sort:maxpct'], ['avgPct', '평균 스프레드', 'sort:avgpct'],
  ['net', '네트워크', 'sort:net'], ['last', '최신', 'sort:last'],
]
/** 폰 정렬 줄의 열 목록 — 넓은 표의 정렬 가능한 열 전부(티커 + HEADERS) */
const SORT_HEADERS: Header[] = [['sym', '티커', 'left'], ...HEADERS.map(([k, text]): Header => [k, text, 'right'])]
/** 표 정렬 URL 표기 `열:asc|desc` — 열은 HEADERS 의 키만. */
const SORT_CODEC: Codec<{ key: SortKey; dir: number }> = {
  parse: (s) => {
    const [k, d] = s.split(':')
    if (!HEADERS.some(([key]) => key === k) || (d !== 'asc' && d !== 'desc')) return undefined
    return { key: k as SortKey, dir: d === 'asc' ? 1 : -1 }
  },
  format: (v) => `${v.key}:${v.dir === 1 ? 'asc' : 'desc'}`,
}

/** 타임라인 축 라벨 (로컬). 기간이 하루 이하면 M/D 는 5칸이 전부 같은 날이라 시각으로, 1주 미만이면 날짜+시각, 그 이상은 M/D. */
function fmtAxis(ms: number, periodSec: number): string {
  const d = new Date(ms)
  const md = `${d.getMonth() + 1}/${d.getDate()}`
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (periodSec <= 86_400) return hm
  if (periodSec < 7 * 86_400) return `${md} ${hm}`
  return md
}

const rankCell: CSSProperties = { fontSize: 11.5, fontVariantNumeric: 'tabular-nums', textAlign: 'right', padding: '0 4px' }
const numCell: CSSProperties = { padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

function StatusPill({ since, nowSec }: { since: number | null; nowSec: number }) {
  if (since == null) return <Pill tone="neutral">끝남</Pill>
  return <Pill tone="accent">진행 중 · {fmtDur(nowSec - since)}</Pill>
}

/** 타임라인 막대 하나 — 받는 값이 전부 문자열이라, 글자·위치가 그대로인 막대(끝난 사건 대부분)는 셸의 1.5초 리렌더에서 다시 그리지 않는다. */
const TimelineBar = memo(function TimelineBar({ title, left, width, color }: { title: string; left: string; width: string; color: string }) {
  return (
    <span title={title}
      style={{ position: 'absolute', top: 3, bottom: 3, minWidth: 2, borderRadius: 2, background: color, left, width }} />
  )
})

/** 선택 코인이 있는 해외 거래소(피드 기준, FX_CHOICES 순서). 피드가 아직 없으면 빈 배열 = 모름.
 *  피드 행의 fx 는 표시명(exName, 'Bitget')이라 id 도 표시명으로 바꿔 비교한다 */
function availableFxsOf(spreads: SpreadRow[], sym: string): string[] {
  const has = new Set<string>()
  for (const r of spreads) if (r.sym === sym) has.add(r.fx)
  return REAL_FXS.filter((id) => has.has(exName(id)))
}

interface Props {
  now: number; selSym: string; onSelect: (sym: string) => void
  /** 스프레드 피드의 현재 행 — 선택 코인이 어느 해외 거래소에 있는지 알아내는 데만 쓴다 (014 §3.7). */
  spreads: SpreadRow[]
  /** 이 탭이 보이는 중 — 아닐 때는 사건·봉 60초 재조회를 멈춘다 (013 §3.5·014 §3.7). */
  active: boolean
}

/**
 * 셸은 delta(초당 1회)와 5초 수집 상태 폴링마다 다시 그리고, 그때마다 spreads 는 새 배열이다. 이 탭이 행에서 읽는 것은
 * '선택 코인이 있는 해외 거래소' 목록 하나뿐이고 그 목록은 거의 바뀌지 않으므로, 목록이 같으면 탭 전체(순위표·타임라인·차트 카드)를
 * 다시 그리지 않는다. 진행 중 사건의 지속 시간은 now 가 바뀌는 1.5초 tick 에 그대로 다시 그린다.
 * 묶음: 이 탭이 spreads 에서 다른 값을 읽게 되면 이 비교도 같이 고친다.
 */
function sameProps(a: Props, b: Props): boolean {
  return a.now === b.now && a.selSym === b.selSym && a.onSelect === b.onSelect && a.active === b.active
    && (a.spreads === b.spreads || availableFxsOf(a.spreads, a.selSym).join() === availableFxsOf(b.spreads, b.selSym).join())
}

export default memo(HistoryTab, sameProps)

function HistoryTab({ now, selSym, onSelect, spreads, active }: Props) {
  const { phone, compact, docScroll, touch } = useViewport()
  const [logRef, logNarrow] = useNarrowerThan(LOG_TABLE_MIN)
  const [rankRef, rankNarrow] = useNarrowerThan(RANK_TABLE_MIN)
  // 넓은 배치(두 칸)는 표가 최소 폭으로 왼쪽 칸을 잡고 오른쪽 칸이 남은 폭을 쓴다 — 카드로 바꾸지 않는다. 칸 폭으로 고르는 것은 한 칸으로 쌓일 때만이라
  // 카드·표를 오가며 칸 폭이 바뀌어도(표의 최소 폭이 칸을 넓힌다) 창 크기를 바꾼 순서와 무관하게 같은 모양이 된다
  const rankCards = phone || (compact && rankNarrow)
  // 폰 필터 접기 — 메모리만, 기본 접힘 (072 §3.4)
  const [foldOpen, setFoldOpen] = useState(false)
  // 방향·기간·거래소·정렬·차트 선택은 URL 쿼리(h.*)에 실려 새로고침해도 같은 화면 (002 §3.5). 검색 입력은 Enter 전까지 임시라 제외
  const [dir, setDir] = useUrlState<Dir>('h.dir', 'kimp', oneOf(['kimp', 'reverse']))
  const [per, setPer] = useUrlState<Per>('h.per', PER_DEFAULT, oneOf(PERS))
  const [dom, setDom] = useUrlState<Dom | null>('h.dom', null, alias([['all', null], ['upbit', 'upbit'], ['bithumb', 'bithumb']]))
  // 표의 해외 거래소 필터 — API 에 fx 파라미터가 없어 응답을 클라이언트에서 거른다. 차트 카드 선택(h.fx)과는 별개
  const [fxf, setFxf] = useUrlState<string | null>('h.fxf', null, alias([['all', null], ...REAL_FXS.map((id): [string, string | null] => [id, id])]))
  const [sort, setSort] = useUrlState<{ key: SortKey; dir: number }>('h.sort', { key: 'cnt', dir: -1 }, SORT_CODEC)
  const { key: sortKey, dir: sortDir } = sort
  // 심볼 검색 — Enter 로 선택 (표 클릭과 같은 onSelect). 영문 대문자·숫자 1~20자일 때만 — 아니면 고르지 않고 입력칸을 그대로 둔다 (033)
  const [q, setQ] = useState('')
  // 차트 거래소 선택(국내·해외 각각 여러 개). 위 표 필터와는 별개 — 기본은 국내 둘 다라서 빗썸에만 있는 코인(HEMI 등)도 빈 화면이 되지 않는다.
  // 툴바 체크박스로만 좁힌다
  const [chartDoms, setChartDoms] = useUrlState<Dom[]>('h.doms', DOMS_ORDER, list(oneOf(DOMS_ORDER)))
  // 접힌 해외 카드(URL h.fxc). 카드는 이 코인이 있는 해외 거래소마다 하나씩 항상 있고, 접기만 고른다. mock(MEXC)은 시안이라 기본 접힘
  const [collapsedFxs, setCollapsedFxs] = useUrlState<string[]>('h.fxc', FX_CHOICES.filter((f) => f.mock).map((f) => f.id), list(oneOf(FX_CHOICES.map((f) => f.id))))
  const toggleFx = (id: string) => setCollapsedFxs(collapsedFxs.includes(id) ? collapsedFxs.filter((x) => x !== id) : [...collapsedFxs, id])
  // 선택 코인이 있는 해외 거래소 — 위 sameProps 와 같은 함수로 구한다
  const availableFxs = useMemo(() => availableFxsOf(spreads, selSym), [spreads, selSym])
  const mockBase = REAL_FXS[0] // mock 카드의 재료 봉 — binance
  // 보여줄 카드 = 이 코인이 있는 해외 거래소(FX_CHOICES 순서). 피드가 아직 없으면 전부. mock 은 Binance 가 있을 때만
  const shownFxs = FX_CHOICES.map((f) => f.id).filter((id) => availableFxs.length === 0 || availableFxs.includes(isMockFx(id) ? mockBase : id))
  const openFxs = shownFxs.filter((id) => !collapsedFxs.includes(id))
  // 카드 간 시간축·십자선 연동 — 탭이 사는 동안 하나
  const [sync] = useState(() => new ChartSync())
  // 봉 종류 — 계층(1m·5m·1h·4h·1d) 하나를 골라 그 안에서 접는다 (candles.ts·rollup.ts)
  const [interval, setInterval_] = useUrlState<Interval>('h.iv', '1m', oneOf(INTERVALS))
  const res = RES_OF_INTERVAL[interval]
  // 왼쪽으로 끌어 더 붙인 청크 수 — (심볼, 쌍, 방향, 계층) 이 바뀌면 0 부터. effect 로 맞추면 옛 값으로 한 번 그리고 다시 그리는
  // 2단계가 되어 차트 범위가 어긋나므로, 같은 렌더에서 바로 계산한다.
  const chartKey = `${selSym}:${chartDoms.join('+')}:${dir}:${res}`
  const [olderState, setOlderState] = useState({ key: chartKey, n: 0 })
  const older = olderState.key === chartKey ? olderState.n : 0

  const nowSec = Math.floor(now / 1000)
  const periodSec = PER_SEC[per]
  const { result, loading } = useEvents({ dir, dom, periodSec }, active)
  // 실패·미도착 때 `[]` 를 매 렌더 새로 만들면 아래 memo 들이 초마다 깨져 차트가 초마다 다시 그려진다 → 고정 빈 배열
  const allEvents: PremiumEvent[] = result?.kind === 'ok' ? result.data.events : NO_EVENTS
  // 해외 거래소 필터 — 국내 필터(API dom)와 같은 범위로 탭 전체(표·요약·로그·차트 음영)에 걸린다. 60초 재조회·필터 변경 때만 새 배열
  const events = useMemo(() => (fxf === null ? allEvents : allEvents.filter((e) => e.fx === fxf)), [allEvents, fxf])

  // 좌 표: 심볼별 집계 → 정렬 → 상위 30. 합·최대는 사건이 바뀔 때만 모으고, 렌더마다는 진행 중 사건의 지속만 더한다
  const prepared = useMemo(() => prepareStats(events), [events])
  const rank = sortStats(finishStats(prepared, nowSec), sortKey, sortDir).slice(0, 30)
  const onSort = (k: SortKey) => {
    if (k === sortKey) { setSort({ key: k, dir: -sortDir }); return }
    // 망 열은 문자열이라 첫 클릭이 오름차순, 수치 열은 내림차순 (024 §3.8)
    if (k === 'net') { setSort({ key: k, dir: 1 }); return }
    setSort({ key: k, dir: -1 })
  }

  // 우 column: 선택 심볼의 사건(최신순)·요약·타임라인
  // events 는 60초 재조회 때만 새 배열 — 매초 리렌더에서 같은 참조를 유지해 차트 setData 가 초마다 돌지 않게 memo
  const mine = useMemo(() => events.filter((e) => e.base === selSym).sort((a, b) => b.startTs - a.startTs), [events, selSym])
  // 타임라인 막대의 key·툴팁은 사건마다 한 번 — 끝난 사건은 완성된 문자열, 진행 중은 지속 앞뒤 조각만(지속은 렌더마다 흐른다)
  const bars = useMemo(() => mine.map((e) => {
    const head = `${exName(e.dom)} · ${fmtTime(e.startTs * 1000)} 시작 · `
    const tail = ` 지속 · 최대 ${fmtPct(e.maxPercent)}`
    return { e, key: `${e.dom}-${e.fx}-${e.startTs}`, head, tail, title: e.ongoing ? null : head + fmtDur(e.durationSeconds) + tail }
  }), [mine])
  const sum = summarize(mine, nowSec, periodSec)
  const t0Sec = nowSec - periodSec
  const color = dirColor(dir)

  // 차트 데이터 — /history/candles 청크(쌍별)를 받아 봉 종류로 접는다. 접기까지 여기서 끝내 차트는 그리기만 한다.
  // 실데이터 해외 거래소(binance·bybit·bitget·okx)는 펼친 카드만 부른다. mock 카드(MEXC)는 binance 봉을 변형해 만드므로 그때는 binance 도 부른다(015 시안, mock.ts)
  const domsKey = chartDoms.join('+')
  const fxsKey = openFxs.join('+')
  const realFxsToFetch = REAL_FXS.filter((id) => openFxs.includes(id) || (id === mockBase && openFxs.some(isMockFx)))
  const { pairs, loading: candlesLoading, errorStatus: candlesError, oldestReached } = useCandles({
    base: selSym, dir, doms: DOMS_ORDER.filter((x) => chartDoms.includes(x)), fxs: realFxsToFetch, interval, older,
  }, active)
  // 카드 = 해외 1개 × 선택한 국내 전부. 접힌 카드는 봉이 없으니(안 불렀다) 헤더에 쓸 국내 목록만 빈 봉으로 채운다
  const cards = useMemo<{ fx: string; series: PairSeries[] }[]>(() => {
    return openFxs.map((fx) => ({
      fx,
      series: pairs.filter((p) => p.fx === (isMockFx(fx) ? mockBase : fx)).map((p) => {
        const raw = isMockFx(fx) ? mockCandles(fx, p.candles, dir, RES_SEC[res]) : p.candles
        return { dom: p.dom, fx, candles: rollup(raw, INTERVAL_SEC[interval], RES_SEC[res]) }
      }),
    }))
  }, [pairs, fxsKey, dir, interval, res])
  // 차트 음영은 표 필터(기간·국내·해외)와 무관하게 선택 심볼의 사건을 차트가 보여주는 범위(가장 오래된 청크 시작 ~ 지금)로 따로 받는다.
  // 청크 시작은 경계에 맞춰 떨어지므로 매초 리렌더에도 값이 그대로 — 과거 로드(older)·봉 종류가 바뀔 때만 재조회
  const chartStartSec = neededChunks(nowSec, res, INTERVAL_SEC[interval], older).starts[0]
  const { result: chartResult } = useEvents({ dir, dom: null, periodSec: 0, startSec: chartStartSec, base: selSym }, active)
  const chartAll: PremiumEvent[] = chartResult?.kind === 'ok' ? chartResult.data.events : NO_EVENTS
  const chartEvents = useMemo(() => chartAll.filter((e) => chartDoms.includes(e.dom)), [chartAll, domsKey])
  // 카드별 사건 — 렌더마다 새 배열을 만들면 카드의 음영 effect 가 초마다 돌므로 memo (014 교훈)
  const cardEvents = useMemo<Record<string, PremiumEvent[]>>(
    () => Object.fromEntries(FX_CHOICES.map((f) => [f.id, isMockFx(f.id) ? mockEvents(f.id, chartEvents) : chartEvents.filter((e) => e.fx === f.id)])),
    [chartEvents],
  )
  const needOlder = () => { if (!oldestReached) setOlderState({ key: chartKey, n: older + 1 }) }

  // el — 061 요소 열쇠(data-el, 모양·동작과 무관)
  const dirOpts: SegOpt[] = (['kimp', 'reverse'] as Dir[]).map((d) => ({
    label: DIR_LABEL[d], onClick: () => setDir(d),
    bg: dir === d ? 'var(--color-neutral-900)' : 'transparent',
    color: dir === d ? dirColor(d) : 'var(--color-neutral-500)',
    el: `dir:${d}`,
  }))
  const seg = (label: string, active: boolean, onClick: () => void, el: string): SegOpt => ({
    label, onClick,
    bg: active ? 'var(--color-neutral-900)' : 'transparent',
    color: active ? 'var(--color-accent-300)' : 'var(--color-neutral-500)',
    el,
  })
  // 필터 앞 작은 제목 — 차트 툴바의 `국내`·`해외` 와 같은 모양이라 두 설정 줄이 같은 문법으로 읽힌다
  // 폰은 묶음이 줄 폭을 넘지 않게 하고 분절 버튼이 그 안에서 가로로 스크롤된다(기간 9개, 072 §3.4)
  const group = (title: string, body: ReactNode) => (
    <span style={phone ? { display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, maxWidth: '100%' } : { display: 'inline-flex', alignItems: 'center', gap: 10 }}>
      <span style={{ ...hint, fontSize: 11, flex: 'none' }}>{title}</span>{body}
    </span>
  )
  const perGroup = group('기간', <Seg opts={PERS.map((p) => seg(PER_LABEL[p], per === p, () => setPer(p), `period:${p}`))} />)
  const domGroup = group('국내', <Seg opts={[
    seg('전체', dom === null, () => setDom(null), 'dom:all'),
    seg('업비트', dom === 'upbit', () => setDom('upbit'), 'dom:upbit'),
    seg('빗썸', dom === 'bithumb', () => setDom('bithumb'), 'dom:bithumb'),
  ]} />)
  const fxGroup = group('해외', <Seg opts={[
    seg('전체', fxf === null, () => setFxf(null), 'fx:all'),
    ...REAL_FXS.map((id) => seg(exName(id), fxf === id, () => setFxf(id), `fx:${id}`)),
  ]} />)
  const searchEl = (
    <input className="input" placeholder="심볼 검색 → Enter" value={q} style={phone ? phoneSearch : touch ? touchSearch : searchInput} data-el="search" enterKeyHint="search" {...SYMBOL_INPUT}
      onChange={(e) => setQ(e.target.value.toUpperCase())}
      onKeyDown={(e) => {
        const s = q.trim().toUpperCase()
        if (e.key === 'Enter' && isSymbol(s)) { onSelect(s); setQ('') }
      }} />
  )
  const eventDef = <>사건 = 원값 {DIR_LABEL[dir]} 1.0% 진입 → 0.5% 이탈, 1분 이하 제외</>
  const loadingNote = loading && <span style={{ color: 'var(--color-accent-300)', marginLeft: 8 }}>조회 중…</span>

  return (
    <div style={bodyScroll(docScroll)}>
      {/* 폭 92% 가운데 정렬 — 양옆에 여백을 조금 둬 차트 카드가 화면 끝까지 꽉 차지 않게. 필터바·표도 같이 좁혀 줄을 맞춘다. 폰은 화면 폭 전체 */}
      <div style={phone
        ? { padding: 'var(--space-3)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }
        : { width: '92%', margin: '0 auto', padding: 'var(--space-6) 0', display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>

        {/* 방향 서브탭 + 표 필터바 (§3.5) — 아래 표·우측 column 에만 걸린다. 차트 설정은 맨 아래 차트 툴바에 따로.
            data-area 는 052 화면 영역 이용 통계의 영역(필터 둘·표·요약·로그·차트) */}
        {/* 필터 상자는 하나 — 영역·열쇠는 화면 안에서 하나뿐(061). 폰은 방향·검색·필터 단추 한 줄 + 펼친 묶음 + 건수 줄 */}
        <div data-area="filters" style={{ display: 'flex', alignItems: 'center', gap: phone ? 'var(--space-3)' : 'var(--space-4)', flexWrap: 'wrap' }}>
        {phone ? (
          <>
            <Seg opts={dirOpts} pad="6px 14px" />
            {searchEl}
            <FoldButton open={foldOpen} active={Number(per !== PER_DEFAULT) + Number(dom !== null) + Number(fxf !== null)} onClick={() => setFoldOpen(!foldOpen)} el="filter-toggle" />
            {foldOpen && (
              <div style={foldPanel}>
                {perGroup}
                {domGroup}
                {fxGroup}
                <span style={hint}>{eventDef}</span>
              </div>
            )}
            <span style={{ ...hint, flexBasis: '100%' }}>{PER_LABEL[per]} · {events.length}건{loadingNote}</span>
          </>
        ) : (
          <>
            <Seg opts={dirOpts} pad="6px 16px" />
            {perGroup}
            {domGroup}
            {fxGroup}
            {searchEl}
            <span style={{ ...hint, marginLeft: 'auto' }}>
              {eventDef} · 기간 내 {events.length}건
              {loadingNote}
            </span>
          </>
        )}
        </div>

        {/* 표 | 요약·로그 두 칸 — 폭 1079px 이하는 한 칸으로 쌓는다 (072 §3.6) */}
        <div style={{ display: 'grid', gridTemplateColumns: compact ? 'minmax(0, 1fr)' : '3fr 2fr', gap: 'var(--space-4)', alignItems: 'start' }}>

          {/* 좌: 티커별 사건 표 */}
          <div ref={rankRef} data-area="table" style={{ ...card, padding: 'var(--space-4) 0' }}>
            <div style={{ ...kicker, padding: '0 var(--space-6) var(--space-2)' }}>티커별 {DIR_LABEL[dir]} 사건 · {PER_LABEL[per]}{rankCards ? '' : ' — 열 클릭으로 정렬'}</div>
            {rankCards ? (
              // 폰·좁은 칸 — 정렬 줄 + 3줄 카드 행: 티커·상태 / 최대 스프레드, 횟수·지속 / 평균 스프레드, 네트워크·최신 (072 §3.5·§3.9)
              <>
                <SortBar headers={SORT_HEADERS} sortKey={sortKey} sortDir={sortDir} onSort={(k) => onSort(k as SortKey)} els={['sort:col', 'sort:dir']} />
                {rank.map((x) => (
                  <button key={x.sym} onClick={() => onSelect(x.sym)} className="hv-row" data-el={`row:${x.sym}`}
                    style={{
                      ...cardRow({ rule: 5 }), ...(phone ? null : { padding: 'var(--space-3) var(--space-6)' }),
                      width: '100%', appearance: 'none', border: 'none', font: 'inherit', color: 'inherit', cursor: 'pointer', textAlign: 'left',
                      borderBottom: '1px solid color-mix(in srgb, #e9e9ed 5%, transparent)',
                      background: x.sym === selSym ? 'color-mix(in srgb, var(--color-accent) 10%, transparent)' : 'transparent',
                    }}>
                    <span style={cardLeft}>
                      <span style={{ fontWeight: 600, fontSize: 14, color: x.sym === selSym ? 'var(--color-accent-300)' : 'var(--color-text)' }}>{x.sym}</span>
                      <StatusPill since={x.ongoingSince} nowSec={nowSec} />
                    </span>
                    <span style={cardRight}>
                      <span style={{ fontSize: 10, color: 'var(--color-neutral-500)' }}>최대</span>
                      <span style={{ fontSize: 14, fontWeight: 600, color }}>{fmtPct(x.maxPct)}</span>
                    </span>
                    <span style={cardSub}>{x.cnt}회 · 최대 {fmtDur(x.maxDur)} · 평균 {x.avgDur != null ? fmtDur(x.avgDur) : '–'}</span>
                    <span style={{ ...cardRight, fontSize: 11.5 }}>
                      <span style={{ fontSize: 10, color: 'var(--color-neutral-500)' }}>평균</span>
                      <span style={{ color }}>{fmtPct(x.avgPct)}</span>
                    </span>
                    <span style={cardSub} title={x.net ?? undefined}>{x.net ?? NET_NONE} · {fmtAgo(nowSec - x.last)}</span>
                  </button>
                ))}
              </>
            ) : (
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: RANK_TABLE_MIN }}>
                <div style={{ display: 'grid', gridTemplateColumns: RANK_GRID, padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)' }}>
                  <button onClick={() => onSort('sym')} className="hv-txt" data-el="sort:sym"
                    style={{ appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', padding: '7px 0', cursor: 'pointer', textAlign: 'left', color: sortKey === 'sym' ? 'var(--color-accent-300)' : 'var(--color-neutral-600)' }}>
                    티커{sortKey === 'sym' ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}
                  </button>
                  {HEADERS.map(([k, label, el]) => (
                    <button key={k} onClick={() => onSort(k)} className="hv-txt" data-el={el}
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
                  <button key={x.sym} onClick={() => onSelect(x.sym)} className="hv-row" data-el={`row:${x.sym}`}
                    style={{
                      display: 'grid', gridTemplateColumns: RANK_GRID, alignItems: 'center', width: '100%',
                      // color — 버튼 기본 글자색을 쓰지 않는다(iOS 는 시스템 파랑, 데스크톱 밝은 화면 선언이면 검정) — 횟수 칸이 이 색을 물려받는다 (072 §3.9)
                      appearance: 'none', border: 'none', font: 'inherit', color: 'inherit',
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
                    <span style={{ ...rankCell, color: 'var(--color-neutral-300)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={x.net ?? undefined}>{x.net ?? NET_NONE}</span>
                    <span style={{ ...rankCell, fontSize: 11, color: 'var(--color-neutral-500)' }}>{fmtAgo(nowSec - x.last)}</span>
                  </button>
                ))}
              </div>
            </div>
            )}
            {result?.kind === 'error' && <Empty size={12}>기록을 불러오지 못했습니다 (HTTP {result.status})</Empty>}
            {result?.kind !== 'error' && rank.length === 0 && !loading && <Empty size={12}>기간 내 사건 없음</Empty>}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            {/* 우: 선택 심볼 요약 + 타임라인 1줄 */}
            <div data-area="summary" style={{ ...card, padding: phone ? 'var(--space-4)' : 'var(--space-6)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', marginBottom: 'var(--space-4)', flexWrap: 'wrap' }}>
                <span style={{ fontFamily: 'var(--font-heading)', fontSize: 22, fontWeight: 500 }}>{selSym}</span>
                <StatusPill since={sum.ongoingSince} nowSec={nowSec} />
                {/* 한 칸으로 쌓이면 표가 위에 있다 */}
                <span style={{ ...hint, marginLeft: 'auto' }}>{compact ? '위 표에서 티커를 눌러 선택' : '왼쪽 표에서 티커를 클릭해 선택'}</span>
              </div>
              <div style={phone
                ? { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 'var(--space-4)', marginBottom: 'var(--space-6)' }
                : { display: 'flex', gap: 'var(--space-8)', flexWrap: 'wrap', marginBottom: 'var(--space-6)' }}>
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
                  {bars.map(({ e, key, head, tail, title }) => {
                    const dur = durationOf(e, nowSec)
                    return (
                      <TimelineBar key={key} title={title ?? head + fmtDur(dur) + tail} color={color}
                        left={`${((e.startTs - t0Sec) / periodSec * 100).toFixed(2)}%`}
                        width={`${Math.max(0.4, dur / periodSec * 100).toFixed(2)}%`} />
                    )
                  })}
                </div>
                <span />
                <div style={{ position: 'relative', height: 14 }}>
                  {[0, 0.25, 0.5, 0.75, 1].map((f) => (
                    <span key={f} style={{ position: 'absolute', left: `${(f * 100).toFixed(0)}%`, transform: 'translateX(-50%)', fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>
                      {fmtAxis((t0Sec + f * periodSec) * 1000, periodSec)}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* 우: 사건 로그 — 최근 20건 */}
            <div ref={logRef} data-area="list" style={{ ...card, padding: 'var(--space-2) 0' }}>
              <div style={{ ...kicker, padding: 'var(--space-4) var(--space-6) var(--space-2)' }}>사건 로그 · {selSym} 최근 20건</div>
              {phone || logNarrow ? (
                // 폰·좁은 칸 — 머리 줄 없이 2줄 카드: 거래소·네트워크 / 최대 스프레드, 시작 → 종료 · 지속 (072 §3.5·§3.9). 좁은 칸은 제목과 같은 안쪽 여백
                <div style={{ borderTop: '1px solid var(--color-neutral-800)' }}>
                  {mine.slice(0, 20).map((e) => (
                    <div key={`${e.dom}-${e.fx}-${e.startTs}`} style={{
                      ...cardRow({ rule: 6 }), ...(phone ? null : { padding: 'var(--space-3) var(--space-6)' }),
                      background: e.ongoing ? 'color-mix(in srgb, var(--color-accent) 6%, transparent)' : 'transparent',
                    }}>
                      <span style={cardLeft}>
                        <Pill tone="neutral">{exName(e.dom)} · {exName(e.fx)}</Pill>
                        <span style={{ ...cardSub, color: 'var(--color-neutral-300)' }} title={netPath(e.dir, e.netDom, e.netFx)}>{netPath(e.dir, e.netDom, e.netFx)}</span>
                      </span>
                      <span style={{ ...cardRight, fontSize: 13, fontWeight: 500, color }}>{fmtPct(e.maxPercent)}</span>
                      <span style={{ ...cardSub, gridColumn: '1 / -1' }}>
                        {fmtTime(e.startTs * 1000)} → <span style={{ color: e.ongoing ? 'var(--color-accent-300)' : undefined }}>{e.ongoing ? '진행 중' : fmtTime((e.endTs ?? e.startTs) * 1000)}</span> · {fmtDur(durationOf(e, nowSec))}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
              <>
              <div style={{ display: 'grid', gridTemplateColumns: LOG_GRID, padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)', fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--color-neutral-600)' }}>
                <span style={{ padding: '6px 8px 6px 0' }}>거래소</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>시작</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>종료</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>지속시간</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>최대 스프레드</span>
                <span style={{ padding: '6px 0 6px 8px', textAlign: 'right' }}>네트워크</span>
              </div>
              {mine.slice(0, 20).map((e) => (
                <div key={`${e.dom}-${e.fx}-${e.startTs}`} style={{
                  display: 'grid', gridTemplateColumns: LOG_GRID, alignItems: 'center', height: 32, padding: '0 var(--space-6)',
                  borderBottom: '1px solid color-mix(in srgb, #e9e9ed 6%, transparent)',
                  // 진행 중 행은 옅은 accent 배경 — 끝난 행과 한눈에 구분되게 (§3.5)
                  background: e.ongoing ? 'color-mix(in srgb, var(--color-accent) 6%, transparent)' : 'transparent',
                }}>
                  <Pill tone="neutral">{exName(e.dom)} · {exName(e.fx)}</Pill>
                  <span style={{ ...numCell, color: 'var(--color-neutral-300)' }}>{fmtTime(e.startTs * 1000)}</span>
                  <span style={{ ...numCell, color: e.ongoing ? 'var(--color-accent-300)' : 'var(--color-neutral-400)' }}>{e.ongoing ? '진행 중' : fmtTime((e.endTs ?? e.startTs) * 1000)}</span>
                  <span style={numCell}>{fmtDur(durationOf(e, nowSec))}</span>
                  <span style={{ ...numCell, fontWeight: 500, color }}>{fmtPct(e.maxPercent)}</span>
                  {/* 경로 방향대로 `보내는 네트워크 → 받는 네트워크` — 배포 전 사건은 `–` (024 §3.8) */}
                  <span style={{ ...numCell, paddingRight: 0, color: 'var(--color-neutral-300)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={netPath(e.dir, e.netDom, e.netFx)}>{netPath(e.dir, e.netDom, e.netFx)}</span>
                </div>
              ))}
              </>
              )}
              {mine.length === 0 && <Empty size={12}>기간 내 사건 없음</Empty>}
            </div>
          </div>
        </div>

        {/* 선택 심볼 봉 차트(표 아래) — 설정은 바로 위 툴바만 따른다. 해외 거래소 1개 = 카드 1개, 김프 + 가격 + 거래소별 입출금 (스펙 014 §3.7 · 015) */}
        <ChartToolbar sym={selSym} dir={dir} interval={interval} onInterval={setInterval_}
          doms={chartDoms} onDoms={setChartDoms}
          loading={candlesLoading} errorStatus={candlesError} />
        {/* 카드 사이는 다른 블록보다 넓게 — 카드가 붙어 있으면 한 덩어리로 보여 답답하다 */}
        <div data-area="chart" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>
          {shownFxs.map((fx) => {
            const open = cards.find((c) => c.fx === fx)
            // 접힌 카드의 series 는 헤더의 국내 목록용 빈 봉
            const series = open?.series ?? DOMS_ORDER.filter((x) => chartDoms.includes(x)).map((dom) => ({ dom, fx, candles: [] }))
            if (series.length === 0) return null
            return (
              <FxChartCard key={fx} fx={fx} dir={dir} interval={interval} collapsed={!open} onToggle={() => toggleFx(fx)}
                series={series} events={cardEvents[fx] ?? NO_EVENTS} onNeedOlder={needOlder} loading={candlesLoading} sync={sync}
                emptyHint={emptyHintFor(selSym, fx, availableFxs)} />
            )
          })}
        </div>

      </div>
    </div>
  )
}
