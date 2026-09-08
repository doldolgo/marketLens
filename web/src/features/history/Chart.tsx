// 선택 심볼 봉 차트 — lightweight-charts(TradingView 오픈소스, 캔버스). 015: 카드 1개 = 해외 거래소 1개.
//   툴바(공용, 카드 밖): 봉 종류 · 국내 체크박스(모든 카드의 선) · 해외 체크박스(카드 추가/제거)
//   카드마다 판 구성:
//     판 0: 김프 % — 국내 1개면 캔들, 2개면 업비트·빗썸 종가 선(색은 거래소에 고정). 진입 1.0·이탈 0.5·0 기준선 + 이 해외 거래소 사건 음영
//     판 1: 가격(USDT 기준) — 이 해외 거래소 선(강조색) + 국내 환산 선(원화 ÷ 환율)
//     판 2~: 입출금 띠 — 거래소마다 1판(이 해외 → 국내 순), 판 안에 입금(위)·출금(아래) 줄 2개. 막힘 = 붉음, 모름 = 회색.
//            방향 경로에 안 드는 줄(김프면 해외 입금·국내 출금)은 흐리게 — "지금 봐야 할 두 줄"이 먼저 보이게
//   카드끼리 시간축·십자선 연동(ChartSync): 한 카드에서 줌·드래그·마우스 이동이 전 카드에 같이 간다.
// 휠 줌·드래그 이동은 라이브러리 기본. 왼쪽 끝에 가까워지면 onNeedOlder 로 과거를 더 달라고 한다.
// 차트 객체는 ref 에 두고 데이터가 바뀔 때만 setData 한다 — 셸의 매초 리렌더가 캔버스를 다시 그리지 않게.
import {
  CandlestickSeries, HistogramSeries, LineSeries, LineStyle, createChart,
  type IChartApi, type ISeriesApi, type LogicalRange, type MouseEventParams, type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { exName, fmtKrw, fmtPct, fmtTime, fmtUsdt, pctColor } from '../../shared/format'
import { Pill, Seg, card, hint, kicker, type SegOpt } from '../../shared/ui'
import { FX_CHOICES, INITIAL_BARS, exchangeStates, lineTone, type BandTone } from './candles'
import { INTERVALS, INTERVAL_LABEL, type Interval } from './rollup'
import type { Candle1m, Dir, Dom, PremiumEvent } from './types'

/** 사건 룰 (013 §3.1) — 기준선 표시용. */
const ENTER_PCT = 1.0
const EXIT_PCT = 0.5
/** 왼쪽 끝에서 이만큼(봉 개수) 안으로 들어오면 과거 요청. */
const LOAD_MORE_MARGIN = 30
const CHART_H = 460
/** 판 높이 비율 — 가격 판, 입출금 줄 2개짜리 판 1개. 김프 판이 나머지. */
const PRICE_SHARE = 0.24
const BAND_SHARE = 0.09
/** 국내 거래소 선 색 — 카드가 여럿이어도 같은 거래소는 같은 색. 해외 거래소(카드 주인)는 강조색. */
const DOM_COLOR: Record<Dom, string> = { upbit: '#7fd0e8', bithumb: '#e6b45a' }
const FX_COLOR = '#d2cefd'
/** 경로에 안 드는 입출금 줄의 투명도 배율. */
const DIM = 0.4

const DIR_LABEL: Record<Dir, string> = { kimp: '김프', reverse: '역프' }
const dirColor = (dir: Dir) => pctColor(dir === 'kimp' ? 1 : -1)
const DOMS: Dom[] = ['upbit', 'bithumb']
const fxLabel = (id: string) => FX_CHOICES.find((f) => f.id === id)?.label ?? id

/** 김프 판 자동 축 범위에 0 선과 진입 1.0% 선을 항상 포함 — 지금 값이 기준선에서 얼마나 떨어졌는지가 이 차트의 목적이라서. */
type AutoscaleInfo = { priceRange: { minValue: number; maxValue: number } | null; margins?: { above: number; below: number } } | null
const premAutoscale = (orig: () => AutoscaleInfo): AutoscaleInfo => {
  const r = orig()
  if (!r?.priceRange) return r
  return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, 0), maxValue: Math.max(r.priceRange.maxValue, ENTER_PCT) } }
}
/** 입출금 판은 항상 0~2 — 아래 줄 0~0.9, 위 줄 1.1~2. 데이터에 따라 축이 흔들리면 줄 두께가 바뀌므로 고정. */
const bandAutoscale = (): AutoscaleInfo => ({ priceRange: { minValue: 0, maxValue: 2 } })
const PCT_FORMAT = { type: 'custom', minMove: 0.01, formatter: (v: number) => `${v.toFixed(2)}%` } as const

/** 캔버스는 CSS 변수를 못 읽으므로 토큰 값을 한 번 풀어 온다. */
function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}
function alpha(hex: string, a: number): string {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  if (!m) return hex
  return `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})`
}
/** 라이브러리는 시각을 UTC 로 그린다 → 로컬(KST) 로 보이게 offset 을 더해 넣고, 읽을 때 뺀다. */
const TZ_OFF = -new Date().getTimezoneOffset() * 60
const toChartTime = (ts: number) => (ts + TZ_OFF) as UTCTimestamp
const fromChartTime = (t: number) => t - TZ_OFF

/** 오름차순 봉 배열에서 ts 와 같은 봉 (이진 탐색). */
function findAt(arr: Candle1m[], ts: number): Candle1m | null {
  let lo = 0, hi = arr.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (arr[mid].ts === ts) return arr[mid]
    if (arr[mid].ts < ts) lo = mid + 1; else hi = mid - 1
  }
  return null
}

/** (국내, 해외) 거래소 쌍 하나의 봉. */
export interface PairSeries {
  dom: Dom
  fx: string
  /** 시각 오름차순, 이미 interval 로 접힌 봉. */
  candles: Candle1m[]
}
export const pairKey = (s: { dom: string; fx: string }) => `${s.dom}|${s.fx}`
/** 시간축·음영·읽기 줄의 기준 쌍 — 봉이 있는 첫 쌍. 업비트에 원화 마켓이 없는 코인은 첫 쌍(업비트)이 비어 있어 이걸 축으로 잡으면 아무것도 안 보인다. */
const axisOf = (S: PairSeries[]): PairSeries => S.find((s) => s.candles.length > 0) ?? S[0]

// ── 카드 간 연동 ──
// 시간축: 한 카드의 논리 범위(봉 index 기준)를 나머지에 그대로 적용한다. 카드들의 봉 시각 배열은 같은 binance 청크에서 나와 index 가 맞다.
// 십자선: 마우스가 가리키는 시각을 전 카드에 알리고(읽기 줄), 다른 카드엔 그 시각의 김프 값 자리에 십자선을 그려 준다.
// 적용받은 카드도 같은 이벤트를 다시 쏘므로 "같은 값이면 무시" 로 되울림을 끊는다.
interface SyncMember {
  /** 십자선을 붙일 시리즈(판 0) — 없으면(데이터 없음) 십자선만 지운다. */
  host: () => ISeriesApi<'Candlestick' | 'Line'> | null
  at: (ts: number) => Candle1m | null
  onHover: (ts: number | null) => void
}
export class ChartSync {
  private members = new Map<IChartApi, SyncMember>()
  private lastTs: number | null = null
  private applying = false

  add(chart: IChartApi, m: SyncMember) { this.members.set(chart, m) }
  remove(chart: IChartApi) { this.members.delete(chart) }

  range(from: IChartApi, r: LogicalRange) {
    if (this.applying) return
    this.applying = true
    for (const [chart] of this.members) {
      if (chart === from) continue
      const cur = chart.timeScale().getVisibleLogicalRange()
      if (cur && Math.abs(cur.from - r.from) < 1e-6 && Math.abs(cur.to - r.to) < 1e-6) continue
      chart.timeScale().setVisibleLogicalRange(r)
    }
    this.applying = false
  }

  cross(from: IChartApi, ts: number | null) {
    if (ts === this.lastTs) return
    this.lastTs = ts
    for (const [chart, m] of this.members) {
      m.onHover(ts)
      if (chart === from) continue
      const host = m.host()
      const c = ts == null ? null : m.at(ts)
      if (host && c) chart.setCrosshairPosition(c.close, toChartTime(c.ts), host)
      else chart.clearCrosshairPosition()
    }
  }
}

// ── 공용 툴바 (카드 밖) ──
export interface ToolbarProps {
  sym: string
  dir: Dir
  interval: Interval
  onInterval: (i: Interval) => void
  doms: Dom[]
  onDoms: (d: Dom[]) => void
  fxs: string[]
  onFxs: (f: string[]) => void
  /** 청크를 받는 중 — 직전 봉은 유지 (014 §3.7). */
  loading: boolean
  /** 마지막 조회 실패의 HTTP 상태(네트워크 실패 0). 없으면 null. */
  errorStatus: number | null
}

export function ChartToolbar(p: ToolbarProps) {
  const toggle = <T extends string>(list: T[], all: T[], v: T, set: (l: T[]) => void) => {
    const next = list.includes(v) ? list.filter((x) => x !== v) : all.filter((x) => x === v || list.includes(x))
    if (next.length > 0) set(next) // 최소 1개는 남긴다 — 빈 차트는 의미가 없어서
  }
  const cbStyle: CSSProperties = { accentColor: 'var(--color-accent)', width: 12, height: 12, cursor: 'pointer', margin: 0 }
  const cbLabel = (on: boolean): CSSProperties => ({ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11.5, cursor: 'pointer', color: on ? 'var(--color-neutral-300)' : 'var(--color-neutral-600)' })
  const group = <T extends string>(title: string, all: T[], list: T[], label: (v: T) => string, set: (l: T[]) => void) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
      <span style={{ ...hint, fontSize: 11 }}>{title}</span>
      <label style={cbLabel(list.length === all.length)}>
        <input type="checkbox" style={cbStyle} checked={list.length === all.length} onChange={() => set(list.length === all.length ? [all[0]] : all)} />전체
      </label>
      {all.map((v) => (
        <label key={v} style={cbLabel(list.includes(v))}>
          <input type="checkbox" style={cbStyle} checked={list.includes(v)} onChange={() => toggle(list, all, v, set)} />{label(v)}
        </label>
      ))}
    </span>
  )
  const fxChoiceLabel = (id: string) => { const f = FX_CHOICES.find((x) => x.id === id); return f?.mock ? `${f.label} (mock)` : f?.label ?? id }
  const intervalOpts: SegOpt[] = INTERVALS.map((i) => ({
    label: INTERVAL_LABEL[i], onClick: () => p.onInterval(i),
    bg: p.interval === i ? 'var(--color-neutral-900)' : 'transparent',
    color: p.interval === i ? 'var(--color-accent-300)' : 'var(--color-neutral-500)',
  }))
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)', flexWrap: 'wrap' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-4)' }}>
        <span style={{ fontFamily: 'var(--font-heading)', fontSize: 18, fontWeight: 500 }}>{p.sym}</span>
        <span style={{ fontSize: 12, color: dirColor(p.dir) }}>{DIR_LABEL[p.dir]} {INTERVAL_LABEL[p.interval]}봉</span>
        <Seg opts={intervalOpts} pad="5px 9px" />
      </span>
      {group('국내', DOMS, p.doms, exName, p.onDoms)}
      {group('해외', FX_CHOICES.map((f) => f.id), p.fxs, fxChoiceLabel, p.onFxs)}
      {p.loading && <Pill tone="accent">불러오는 중…</Pill>}
      {p.errorStatus != null && <Pill tone="warn">차트를 불러오지 못했습니다 (HTTP {p.errorStatus})</Pill>}
      <span style={{ ...hint, marginLeft: 'auto' }}>해외 거래소 1개 = 차트 1개 · 휠 = 줌 · 드래그 = 이동 · 왼쪽 끝으로 끌면 과거 로드 (모든 차트 같이 움직임)</span>
    </div>
  )
}

// ── 카드 (해외 거래소 1개) ──
export interface CardProps {
  dir: Dir
  /** 이 카드의 해외 거래소. */
  fx: string
  interval: Interval
  /** 이 해외 거래소 × 선택한 국내 거래소들, DOMS 순서. 비어 있지 않다. */
  series: PairSeries[]
  /** 이 해외 거래소·선택한 국내 거래소의 사건 — 겹치는 봉을 음영으로. */
  events: PremiumEvent[]
  /** 사용자가 왼쪽 끝 근처까지 끌었을 때 — 과거를 더 붙여 series 를 갱신하라는 신호. */
  onNeedOlder: () => void
  loading: boolean
  sync: ChartSync
}

interface Refs {
  chart: IChartApi
  candle: ISeriesApi<'Candlestick'>
  shade: ISeriesApi<'Histogram'>
  /** 선택 구성(국내 집합·방향)이 바뀔 때만 다시 만드는 시리즈. 평소엔 setData 만 — 지우고 다시 만들면 시간축 index 가 흔들려
   *  라이브러리가 범위 이벤트를 쏘고, 그게 과거 로드 연쇄를 일으킨다. */
  premLines: Map<Dom, ISeriesApi<'Line'>>
  priceLines: Map<string, ISeriesApi<'Line'>>
  /** 입출금 줄: 키 = `{거래소}:{deposit|withdraw}`. */
  bandLines: Map<string, ISeriesApi<'Histogram'>>
  /** 위 시리즈들을 만든 구성 키. */
  configKey: string | null
}

/** 입출금 띠의 행 하나 — 거래소 1개(해외 = 'fx', 국내 = dom id). */
interface BandRow { id: string; label: string; pick: (c: Candle1m) => { deposit: boolean | null; withdraw: boolean | null }; from: PairSeries }

export default function FxChartCard(p: CardProps) {
  const boxRef = useRef<HTMLDivElement>(null)
  const refs = useRef<Refs | null>(null)
  const seriesRef = useRef<PairSeries[]>([])
  const viewKeyRef = useRef<string | null>(null) // 국내 구성·봉 종류 — 바뀌면 보이는 범위를 오른쪽 끝으로 리셋
  const firstTsRef = useRef<number | null>(null) // 직전 데이터의 첫 봉 — 과거가 앞에 붙었는지 판정
  const askedRef = useRef(false) // onNeedOlder 중복 호출 방지, 데이터가 바뀌면 풀림
  const busyRef = useRef(false) // 우리가 setData 하는 동안 라이브러리가 쏘는 범위 이벤트는 사용자 스크롤이 아니다
  const onNeedOlderRef = useRef(p.onNeedOlder)
  onNeedOlderRef.current = p.onNeedOlder
  const [hoverTs, setHoverTs] = useState<number | null>(null)
  const color = dirColor(p.dir)
  const mock = FX_CHOICES.find((f) => f.id === p.fx)?.mock === true

  // ── 차트 1회 생성 (정적 시리즈 = 캔들·음영) + 연동 등록
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const text = cssVar('--color-neutral-500', '#8a8a96')
    const grid = alpha(cssVar('--color-neutral-800', '#2e3040'), 0.6)
    const border = cssVar('--color-neutral-800', '#2e3040')
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' }, textColor: text, fontSize: 10.5,
        attributionLogo: false,
        panes: { separatorColor: border, separatorHoverColor: alpha(border, 0.6), enableResize: false },
      },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: border },
      timeScale: { borderColor: border, timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: { mode: 0 }, // 0 = Normal(자유) — 캔들 자석 없이 봉 단위로 읽는다
      localization: { locale: 'ko-KR', timeFormatter: (t: number) => fmtTime(fromChartTime(t) * 1000) },
    })
    const up = cssVar('--color-up', '#e0697d')
    const down = cssVar('--color-down', '#6f9bee')
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false,
      priceFormat: PCT_FORMAT, autoscaleInfoProvider: premAutoscale,
    }, 0)
    // 사건 음영: 판 0 위에 겹치는 히스토그램(자기 축 없음, 위아래 여백 0 → 판 전체 높이)
    const shade = chart.addSeries(HistogramSeries, { priceScaleId: '', priceLineVisible: false, lastValueVisible: false, base: 0 }, 0)
    shade.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 } })

    const r: Refs = { chart, candle, shade, premLines: new Map(), priceLines: new Map(), bandLines: new Map(), configKey: null }
    refs.current = r
    // 십자선 → 연동(전 카드의 읽기 줄 + 다른 카드의 십자선). 캔버스 밖 React 상태는 헤더 줄만 다시 그린다.
    const onCross = (param: MouseEventParams) => {
      p.sync.cross(chart, param.time == null ? null : fromChartTime(param.time as number))
    }
    chart.subscribeCrosshairMove(onCross)
    // 범위 → 연동 + 왼쪽 끝 근처면 과거 요청 (한 번만, 데이터가 바뀌면 다시 허용)
    const onRange = (range: LogicalRange | null) => {
      if (!range || busyRef.current) return
      p.sync.range(chart, range)
      if (askedRef.current) return
      if (range.from < LOAD_MORE_MARGIN) { askedRef.current = true; onNeedOlderRef.current() }
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange)
    p.sync.add(chart, {
      host: () => {
        const S = seriesRef.current
        if (S.length === 0) return null
        return S.length === 1 ? r.candle : r.premLines.get(axisOf(S).dom) ?? null
      },
      at: (ts) => (seriesRef.current.length ? findAt(axisOf(seriesRef.current).candles, ts) : null),
      onHover: setHoverTs,
    })
    return () => {
      p.sync.remove(chart)
      chart.unsubscribeCrosshairMove(onCross)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange)
      chart.remove()
      refs.current = null
      viewKeyRef.current = null
      firstTsRef.current = null
    }
    // sync 는 탭이 사는 동안 하나다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── 데이터·선택이 바뀔 때 시리즈를 채운다. 구성(국내 집합·방향)이 같으면 setData 만, 다르면 동적 시리즈를 다시 만든다.
  //    앞에 과거가 붙었으면 보이던 시간 범위를 복원해 화면이 튀지 않게
  //    (라이브러리는 setData 뒤 index 기준 범위를 유지하므로 앞에 N 개가 붙으면 화면이 N 개 앞으로 밀린다).
  useEffect(() => {
    const r = refs.current
    if (!r || p.series.length === 0) return
    const S = p.series
    seriesRef.current = S
    askedRef.current = false
    busyRef.current = true
    const keepRange = r.chart.timeScale().getVisibleRange() // setData 전에 시각 기준으로 잡아둔다
    const dirHex = cssVar(p.dir === 'kimp' ? '--color-up' : '--color-down', '#e0697d')
    const blocked = cssVar('--color-up', '#e0697d')
    const okHex = cssVar('--color-neutral-800', '#2e3040')
    const unknownHex = cssVar('--color-neutral-600', '#6a6a78')
    const toneColor = (t: BandTone, dim: boolean) => {
      const k = dim ? DIM : 1
      return t === 'blocked' ? alpha(blocked, 0.9 * k) : t === 'unknown' ? alpha(unknownHex, 0.55 * k) : alpha(okHex, 0.45 + (dim ? 0 : 0.2))
    }

    const single = S.length === 1
    const axis = axisOf(S)
    const base = axis.candles
    const doms = S.map((s) => s.dom)
    const priceSpecs: { key: string; label: string; color: string; pick: (c: Candle1m) => number; from: PairSeries }[] = [
      { key: 'fx', label: fxLabel(p.fx), color: FX_COLOR, pick: (c) => c.usdt, from: axis },
      ...S.map((s) => ({ key: `dom:${s.dom}`, label: exName(s.dom), color: DOM_COLOR[s.dom], pick: (c: Candle1m) => c.krw / c.fxRate, from: s })),
    ]
    // 입출금 행: 이 해외 거래소 → 국내 순. 김프 경로 = 해외 출금·국내 입금, 역프 = 국내 출금·해외 입금 — 나머지 줄은 흐리게
    const rows: BandRow[] = [
      { id: 'fx', label: fxLabel(p.fx), from: axis, pick: (c) => { const e = exchangeStates(c, p.dir); return { deposit: e.fxDeposit, withdraw: e.fxWithdraw } } },
      ...S.map((s) => ({ id: s.dom, label: exName(s.dom), from: s, pick: (c: Candle1m) => { const e = exchangeStates(c, p.dir); return { deposit: e.domDeposit, withdraw: e.domWithdraw } } })),
    ]
    const dimmed = (row: BandRow, kind: 'deposit' | 'withdraw') => {
      const relevant = row.id === 'fx' ? (p.dir === 'kimp' ? 'withdraw' : 'deposit') : (p.dir === 'kimp' ? 'deposit' : 'withdraw')
      return kind !== relevant
    }
    // 축 쌍이 바뀌면(로딩 중엔 다 비어 첫 쌍, 뒤에 빗썸만 도착) 기준선을 붙일 호스트도 바뀌어야 하므로 구성 키에 넣는다
    const configKey = `${doms.join(',')}|${p.dir}|${axis.dom}`

    if (r.configKey !== configKey) {
      // 구성이 바뀜 → 동적 시리즈 재생성
      for (const s of [...r.premLines.values(), ...r.priceLines.values(), ...r.bandLines.values()]) r.chart.removeSeries(s)
      r.premLines.clear(); r.priceLines.clear(); r.bandLines.clear()
      // 판 0: 국내 여럿 → 거래소별 종가 선 (하나면 정적 캔들이 맡는다)
      if (!single) {
        for (const s of S) {
          r.premLines.set(s.dom, r.chart.addSeries(LineSeries, {
            color: DOM_COLOR[s.dom], lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
            priceFormat: PCT_FORMAT, autoscaleInfoProvider: premAutoscale,
          }, 0))
        }
      }
      // 판 1: 거래소별 USDT 가격 선
      for (const ps of priceSpecs) {
        r.priceLines.set(ps.key, r.chart.addSeries(LineSeries, {
          color: ps.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
          priceFormat: { type: 'custom', minMove: 0.0001, formatter: fmtUsdt },
        }, 1))
      }
      // 판 2~: 거래소별 입출금 — 판 하나에 입금(위 1.1~2)·출금(아래 0~0.9) 히스토그램 2개. 축 라벨은 제목만(값 없음)
      rows.forEach((row, i) => {
        const pane = 2 + i
        const mk = (kind: 'deposit' | 'withdraw', base: number) => r.chart.addSeries(HistogramSeries, {
          priceLineVisible: false, lastValueVisible: true, base, title: `${row.label} ${kind === 'deposit' ? '입금' : '출금'}`,
          color: toneColor('open', dimmed(row, kind)),
          priceFormat: { type: 'custom', minMove: 1, formatter: () => '' }, autoscaleInfoProvider: bandAutoscale,
        }, pane)
        const dep = mk('deposit', 1.1)
        const wd = mk('withdraw', 0)
        dep.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.06 } })
        r.bandLines.set(`${row.id}:deposit`, dep)
        r.bandLines.set(`${row.id}:withdraw`, wd)
      })
      // 기준선(진입·이탈·0) — 데이터가 빈 시리즈의 기준선은 그려지지 않으므로 캔들 또는 첫 국내 선에 붙인다
      r.candle.priceLines().forEach((l) => r.candle.removePriceLine(l))
      const host: ISeriesApi<'Candlestick' | 'Line'> = single ? r.candle : r.premLines.get(axis.dom)!
      host.createPriceLine({ price: ENTER_PCT, color: dirHex, lineStyle: LineStyle.Dashed, lineWidth: 1, title: `진입 ${ENTER_PCT.toFixed(1)}%` })
      host.createPriceLine({ price: EXIT_PCT, color: cssVar('--color-neutral-500', '#8a8a96'), lineStyle: LineStyle.SparseDotted, lineWidth: 1, title: `이탈 ${EXIT_PCT.toFixed(1)}%` })
      host.createPriceLine({ price: 0, color: cssVar('--color-neutral-600', '#6a6a78'), lineStyle: LineStyle.Solid, lineWidth: 1, title: '' })
      // 판 높이 비율 — 시리즈를 지웠다 만들면 판도 다시 생기므로 여기서
      const panes = r.chart.panes()
      panes[0]?.setStretchFactor(1 - PRICE_SHARE - BAND_SHARE * rows.length)
      panes[1]?.setStretchFactor(PRICE_SHARE)
      for (let i = 2; i < panes.length; i++) panes[i]?.setStretchFactor(BAND_SHARE)
      r.configKey = configKey
    }

    // 데이터 채우기 (구성 무관, 매번)
    r.candle.setData(single ? base.map((c) => ({ time: toChartTime(c.ts), open: c.open, high: c.high, low: c.low, close: c.close })) : [])
    for (const s of S) r.premLines.get(s.dom)?.setData(s.candles.map((c) => ({ time: toChartTime(c.ts), value: c.close })))
    for (const ps of priceSpecs) r.priceLines.get(ps.key)?.setData(ps.from.candles.map((c) => ({ time: toChartTime(c.ts), value: ps.pick(c) })))
    for (const row of rows) {
      for (const kind of ['deposit', 'withdraw'] as const) {
        const dim = dimmed(row, kind)
        const value = kind === 'deposit' ? 2 : 0.9
        r.bandLines.get(`${row.id}:${kind}`)?.setData(row.from.candles.map((c) => ({ time: toChartTime(c.ts), value, color: toneColor(lineTone(row.pick(c)[kind]), dim) })))
      }
    }

    // 보이는 범위: 구성·봉 종류가 바뀌면 오른쪽 끝 최근 INITIAL_BARS 개, 과거가 앞에 붙었으면 그대로
    const viewKey = `${configKey}|${p.interval}`
    const ts0 = base[0]?.ts ?? null
    const prev = firstTsRef.current
    if (viewKeyRef.current !== viewKey || prev == null || ts0 == null || ts0 > prev) {
      const n = base.length
      const apply = () => {
        const cur = refs.current
        if (!cur || seriesRef.current !== S) return // 그새 데이터가 또 바뀌었으면 그쪽 effect 가 처리
        if (cur.chart.paneSize(0).width <= 0) { requestAnimationFrame(apply); return } // autoSize 라 마운트 직후엔 너비 0
        cur.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - INITIAL_BARS), to: n + 4 })
      }
      apply()
    } else if (ts0 < prev && keepRange) {
      r.chart.timeScale().setVisibleRange(keepRange)
    }
    viewKeyRef.current = viewKey
    firstTsRef.current = ts0
    // 라이브러리의 범위 이벤트는 다음 프레임에 도착하므로 두 프레임 뒤에 잠금을 푼다
    requestAnimationFrame(() => requestAnimationFrame(() => { busyRef.current = false }))
  }, [p.series, p.dir, p.interval, p.fx])

  // ── 사건 음영만 따로 — 60초 재조회로 사건이 바뀔 때 시리즈 전체를 다시 채우지 않게. 첫 쌍의 봉 시각을 축으로,
  //    이 카드 사건이 하나라도 걸리면 칠한다
  useEffect(() => {
    const r = refs.current
    if (!r || p.series.length === 0) return
    const shadeColor = alpha(cssVar(p.dir === 'kimp' ? '--color-up' : '--color-down', '#e0697d'), 0.13)
    const inEvent = (ts: number) => p.events.some((e) => ts >= e.startTs && (e.endTs == null || ts < e.endTs))
    r.shade.setData(axisOf(p.series).candles.map((c) => inEvent(c.ts) ? { time: toChartTime(c.ts), value: 1, color: shadeColor } : { time: toChartTime(c.ts) }))
  }, [p.series, p.events, p.dir])

  // ── 읽기 줄: 십자선 시각(없으면 마지막 봉)의 국내별 김프·거래소별 가격·거래소별 입출금
  const S = p.series
  const axis = S.length ? axisOf(S) : null
  const base = axis?.candles ?? []
  const ts = hoverTs ?? base[base.length - 1]?.ts ?? null
  const at = (s: PairSeries) => (ts == null ? null : findAt(s.candles, ts))
  const single = S.length === 1
  const c0 = axis ? at(axis) : null
  const num: CSSProperties = { fontWeight: 500 }
  const sep = <span style={{ color: 'var(--color-neutral-700)' }}>|</span>
  const st = (okv: boolean | null, dim: boolean) => (
    <b style={{ ...num, opacity: dim ? 0.45 : 1, color: okv == null ? 'var(--color-neutral-500)' : okv ? 'var(--color-neutral-300)' : 'var(--color-up)' }}>{okv == null ? '모름' : okv ? '가능' : '막힘'}</b>
  )
  const fxRelevant = p.dir === 'kimp' ? 'withdraw' : 'deposit'
  const dwCell = (label: string, deposit: boolean | null, withdraw: boolean | null, relevant: 'deposit' | 'withdraw') => (
    <span>{label} 입금 {st(deposit, relevant !== 'deposit')} · 출금 {st(withdraw, relevant !== 'withdraw')}</span>
  )

  return (
    <div style={{ ...card, padding: 'var(--space-6) var(--space-8) var(--space-6)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap', marginBottom: 'var(--space-3)' }}>
        <span style={{ fontFamily: 'var(--font-heading)', fontSize: 16, fontWeight: 500, color: FX_COLOR }}>{fxLabel(p.fx)}</span>
        <span style={{ fontSize: 12, color }}>{DIR_LABEL[p.dir]} · {S.map((s) => exName(s.dom)).join('·')}</span>
        {mock && <Pill tone="warn">MOCK — binance 봉을 변형한 시안 데이터</Pill>}
        <span style={{ ...hint, marginLeft: 'auto' }}>{single ? '캔들' : '국내 거래소별 종가 선'}</span>
      </div>
      {/* 읽기 줄 2줄 — 위: 시각·김프·가격, 아래: 거래소별 입출금. 한 줄에 몰면 폭에 따라 꺾여 읽기 어렵다 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', fontSize: 11.5, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)', marginBottom: 'var(--space-4)', minHeight: 36 }}>
        {ts == null ? <span style={hint}>데이터 없음</span> : (
          <>
          <div style={{ display: 'flex', gap: 'var(--space-6)', flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--color-neutral-500)' }}>{fmtTime(ts * 1000)}</span>
            {single && c0 && (
              <>
                <span>시 <b style={{ ...num, color }}>{fmtPct(c0.open)}</b></span>
                <span>고 <b style={{ ...num, color }}>{fmtPct(c0.high)}</b></span>
                <span>저 <b style={{ ...num, color }}>{fmtPct(c0.low)}</b></span>
                <span>종 <b style={{ ...num, color }}>{fmtPct(c0.close)}</b></span>
              </>
            )}
            {!single && S.map((s) => {
              const c = at(s)
              return <span key={s.dom}>{exName(s.dom)} <b style={{ ...num, color: DOM_COLOR[s.dom] }}>{c ? fmtPct(c.close) : '–'}</b></span>
            })}
            {sep}
            <span>{fxLabel(p.fx)} <b style={{ ...num, color: FX_COLOR }}>{c0 ? fmtUsdt(c0.usdt) : '–'}</b></span>
            {S.map((s) => {
              const c = at(s)
              return <span key={s.dom}>{exName(s.dom)} <b style={{ ...num, color: DOM_COLOR[s.dom] }}>{c ? fmtUsdt(c.krw / c.fxRate) : '–'}</b>{c && <span style={{ color: 'var(--color-neutral-500)' }}> ₩{fmtKrw(c.krw)}</span>}</span>
            })}
            {c0 && <span style={{ color: 'var(--color-neutral-500)' }}>USDT {fmtKrw(c0.fxRate)}원</span>}
          </div>
          <div style={{ display: 'flex', gap: 'var(--space-6)', flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--color-neutral-500)' }}>입출금</span>
            {c0 && (() => { const e = exchangeStates(c0, p.dir); return dwCell(fxLabel(p.fx), e.fxDeposit, e.fxWithdraw, fxRelevant) })()}
            {S.map((s) => {
              const c = at(s)
              if (!c) return null
              const e = exchangeStates(c, p.dir)
              return (
                <span key={s.dom}>
                  {dwCell(exName(s.dom), e.domDeposit, e.domWithdraw, fxRelevant === 'withdraw' ? 'deposit' : 'withdraw')}
                  {c.blockedSec > 0 && <span style={{ color: 'var(--color-neutral-500)' }}> · 경로 막힘 {c.blockedSec}초</span>}
                </span>
              )
            })}
          </div>
          </>
        )}
      </div>

      {/* 캔버스 좌우 여백 — 카드 테두리에 붙지 않게 */}
      <div style={{ position: 'relative', padding: '0 var(--space-4)' }}>
        <div ref={boxRef} style={{ width: '100%', height: CHART_H }} />
        {S.every((s) => s.candles.length === 0) && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none', color: 'var(--color-neutral-500)', fontSize: 12 }}>
            {p.loading ? '불러오는 중…' : '기간 내 기록 없음'}
          </div>
        )}
      </div>

      <div style={{ ...kicker, marginTop: 'var(--space-4)', padding: '0 var(--space-4)' }}>
        위 = {fxLabel(p.fx)} 기준 {DIR_LABEL[p.dir]} 원값 {INTERVAL_LABEL[p.interval]} {single ? '시/고/저/종' : '종가(국내별)'}, 음영 = 사건 구간 · 가운데 = USDT 기준 가격(국내는 환율 환산) · 아래 = 거래소별 입출금, 판마다 위 줄 입금·아래 줄 출금 — 붉음 = 막힘, 회색 = 모름, 흐림 = {DIR_LABEL[p.dir]} 경로 밖
      </div>
    </div>
  )
}
