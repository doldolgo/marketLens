// 선택 심볼 봉 차트 — lightweight-charts(TradingView 오픈소스, 캔버스). 판 구성:
//   판 0: 김프 % — 거래소 쌍이 하나면 캔들, 여럿이면 쌍별 종가 선. 진입 1.0·이탈 0.5·0 기준선 + 사건 구간 음영
//   판 1: 가격(USDT 기준) — 선택한 거래소마다 선 1개. 국내는 원화 ÷ 환율로 환산
//   판 2~: 입출금 띠 — 선택한 국내 거래소마다 1판, 방향 경로(김프 = 해외 출금 → 국내 입금)의 두 끝 기준. 막힘 = 붉은 막대, 모름 = 회색
// 휠 줌·드래그 이동은 라이브러리 기본. 왼쪽 끝에 가까워지면 onNeedOlder 로 과거를 더 달라고 한다.
// 차트 객체는 ref 에 두고 데이터가 바뀔 때만 setData 한다 — 셸의 매초 리렌더가 캔버스를 다시 그리지 않게.
import {
  CandlestickSeries, HistogramSeries, LineSeries, LineStyle, createChart,
  type IChartApi, type ISeriesApi, type LogicalRange, type MouseEventParams, type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { exName, fmtKrw, fmtPct, fmtTime, fmtUsdt, pctColor } from '../../shared/format'
import { Pill, Seg, card, hint, kicker, type SegOpt } from '../../shared/ui'
import { FX_CHOICES, INITIAL_BARS, bandTone } from './candles'
import { INTERVALS, INTERVAL_LABEL, INTERVAL_SEC, type Interval } from './rollup'
import type { Candle1m, Dir, Dom, PremiumEvent } from './types'

/** 사건 룰 (013 §3.1) — 기준선 표시용. */
const ENTER_PCT = 1.0
const EXIT_PCT = 0.5
/** 왼쪽 끝에서 이만큼(봉 개수) 안으로 들어오면 과거 요청. */
const LOAD_MORE_MARGIN = 30
const CHART_H = 420
/** 여러 쌍·거래소를 겹쳐 그릴 때 선 색 (순서대로). 첫 둘은 테마 토큰과 맞춘다. */
const PALETTE = ['#d2cefd', '#e9e9ed', '#e6b45a', '#5ecfb1', '#f08fd0', '#9bd35e', '#6f9bee', '#e0697d', '#c9a27e', '#7fd0e8', '#b8b8c8', '#f2a270']

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

export interface ChartProps {
  sym: string
  dir: Dir
  doms: Dom[]
  fxs: string[]
  onDoms: (d: Dom[]) => void
  onFxs: (f: string[]) => void
  interval: Interval
  onInterval: (i: Interval) => void
  /** 선택한 doms × fxs 순서대로. 비어 있지 않다. */
  series: PairSeries[]
  /** 선택한 국내 거래소들의 사건 — 겹치는 봉을 음영으로. */
  events: PremiumEvent[]
  /** 사용자가 왼쪽 끝 근처까지 끌었을 때 — 과거를 더 붙여 series 를 갱신하라는 신호. */
  onNeedOlder: () => void
  /** 청크를 받는 중 — 헤더에 표시, 직전 봉은 유지 (014 §3.7). */
  loading: boolean
  /** 마지막 조회 실패의 HTTP 상태(네트워크 실패 0). 없으면 null. */
  errorStatus: number | null
}

interface Refs {
  chart: IChartApi
  candle: ISeriesApi<'Candlestick'>
  shade: ISeriesApi<'Histogram'>
  /** 선택 구성(쌍 집합·방향)이 바뀔 때만 다시 만드는 시리즈. 평소엔 setData 만 — 지우고 다시 만들면 시간축 index 가 흔들려
   *  라이브러리가 범위 이벤트를 쏘고, 그게 과거 로드 연쇄를 일으킨다. */
  premLines: Map<string, ISeriesApi<'Line'>>
  priceLines: Map<string, ISeriesApi<'Line'>>
  dwHists: Map<string, ISeriesApi<'Histogram'>>
  /** 위 시리즈들을 만든 구성 키. */
  configKey: string | null
}

export default function PremiumChart(p: ChartProps) {
  const boxRef = useRef<HTMLDivElement>(null)
  const refs = useRef<Refs | null>(null)
  const seriesRef = useRef<PairSeries[]>([])
  const viewKeyRef = useRef<string | null>(null) // 쌍 구성·봉 종류 — 바뀌면 보이는 범위를 오른쪽 끝으로 리셋
  const firstTsRef = useRef<number | null>(null) // 직전 데이터의 첫 봉 — 과거가 앞에 붙었는지 판정
  const askedRef = useRef(false) // onNeedOlder 중복 호출 방지, 데이터가 바뀌면 풀림
  const busyRef = useRef(false) // 우리가 setData 하는 동안 라이브러리가 쏘는 범위 이벤트는 사용자 스크롤이 아니다
  const onNeedOlderRef = useRef(p.onNeedOlder)
  onNeedOlderRef.current = p.onNeedOlder
  const [hoverTs, setHoverTs] = useState<number | null>(null)
  const color = dirColor(p.dir)

  // ── 차트 1회 생성 (정적 시리즈 = 캔들·음영)
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

    // 십자선 → 읽기 줄. 캔버스 밖 React 상태는 헤더 줄만 다시 그린다.
    const onCross = (param: MouseEventParams) => {
      setHoverTs(param.time == null ? null : fromChartTime(param.time as number))
    }
    chart.subscribeCrosshairMove(onCross)
    // 왼쪽 끝 근처 → 과거 요청 (한 번만, 데이터가 바뀌면 다시 허용)
    const onRange = (r: LogicalRange | null) => {
      if (!r || askedRef.current || busyRef.current) return
      if (r.from < LOAD_MORE_MARGIN) { askedRef.current = true; onNeedOlderRef.current() }
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange)

    refs.current = { chart, candle, shade, premLines: new Map(), priceLines: new Map(), dwHists: new Map(), configKey: null }
    return () => {
      chart.unsubscribeCrosshairMove(onCross)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange)
      chart.remove()
      refs.current = null
      viewKeyRef.current = null
      firstTsRef.current = null
    }
  }, [])

  // ── 데이터·선택이 바뀔 때 시리즈를 채운다. 구성(쌍 집합·방향)이 같으면 setData 만, 다르면 동적 시리즈를 다시 만든다.
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
    const ok = alpha(cssVar('--color-neutral-800', '#2e3040'), 0.45)
    const unknown = alpha(cssVar('--color-neutral-600', '#6a6a78'), 0.55)
    const windowSec = INTERVAL_SEC[p.interval]
    const bandColor: Record<ReturnType<typeof bandTone>, string> = { open: ok, blocked: alpha(blocked, 0.9), partial: alpha(blocked, 0.5), unknown }

    const single = S.length === 1
    const base = S[0].candles
    const doms = [...new Set(S.map((s) => s.dom))]
    const fxs = [...new Set(S.map((s) => s.fx))]
    const priceSpecs: { key: string; pick: (c: Candle1m) => number; from: PairSeries }[] = [
      ...doms.map((d) => ({ key: `dom:${d}`, pick: (c: Candle1m) => c.krw / c.fxRate, from: S.find((s) => s.dom === d)! })),
      ...fxs.map((f) => ({ key: `fx:${f}`, pick: (c: Candle1m) => c.usdt, from: S.find((s) => s.fx === f)! })),
    ]
    const configKey = `${S.map(pairKey).join(',')}|${p.dir}`

    if (r.configKey !== configKey) {
      // 구성이 바뀜 → 동적 시리즈 재생성
      for (const s of [...r.premLines.values(), ...r.priceLines.values(), ...r.dwHists.values()]) r.chart.removeSeries(s)
      r.premLines.clear(); r.priceLines.clear(); r.dwHists.clear()
      // 판 0: 쌍 여럿 → 쌍별 종가 선 (하나면 정적 캔들이 맡는다)
      if (!single) {
        S.forEach((s, i) => {
          r.premLines.set(pairKey(s), r.chart.addSeries(LineSeries, {
            color: PALETTE[i % PALETTE.length], lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
            title: `${exName(s.dom)}·${fxLabel(s.fx)}`, priceFormat: PCT_FORMAT, autoscaleInfoProvider: premAutoscale,
          }, 0))
        })
      }
      // 판 1: 거래소별 USDT 가격 선
      priceSpecs.forEach((ps, i) => {
        const label = ps.key.startsWith('dom:') ? exName(ps.from.dom) : fxLabel(ps.from.fx)
        r.priceLines.set(ps.key, r.chart.addSeries(LineSeries, {
          color: PALETTE[(i + 1) % PALETTE.length], lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: label,
          priceFormat: { type: 'custom', minMove: 0.0001, formatter: fmtUsdt },
        }, 1))
      })
      // 판 2~: 국내 거래소별 입출금 띠 — 라벨은 방향 경로(김프 `{해외} 출금 → {국내} 입금`, 역프 반대)
      const fxName = fxs.map(fxLabel).join('/')
      doms.forEach((d, i) => {
        const title = p.dir === 'kimp' ? `${fxName} 출금 → ${exName(d)} 입금` : `${exName(d)} 출금 → ${fxName} 입금`
        const h = r.chart.addSeries(HistogramSeries, {
          priceLineVisible: false, lastValueVisible: false, base: 0, title,
          priceFormat: { type: 'custom', minMove: 1, formatter: () => '' },
        }, 2 + i)
        h.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0 } })
        r.dwHists.set(d, h)
      })
      // 기준선(진입·이탈·0) — 데이터가 빈 시리즈의 기준선은 그려지지 않으므로 캔들 또는 첫 쌍 선에 붙인다
      r.candle.priceLines().forEach((l) => r.candle.removePriceLine(l))
      const host: ISeriesApi<'Candlestick' | 'Line'> = single ? r.candle : r.premLines.get(pairKey(S[0]))!
      host.createPriceLine({ price: ENTER_PCT, color: dirHex, lineStyle: LineStyle.Dashed, lineWidth: 1, title: `진입 ${ENTER_PCT.toFixed(1)}%` })
      host.createPriceLine({ price: EXIT_PCT, color: cssVar('--color-neutral-500', '#8a8a96'), lineStyle: LineStyle.SparseDotted, lineWidth: 1, title: `이탈 ${EXIT_PCT.toFixed(1)}%` })
      host.createPriceLine({ price: 0, color: cssVar('--color-neutral-600', '#6a6a78'), lineStyle: LineStyle.Solid, lineWidth: 1, title: '' })
      // 판 높이 비율 — 시리즈를 지웠다 만들면 판도 다시 생기므로 여기서
      const panes = r.chart.panes()
      const dwShare = 0.07 * doms.length
      panes[0]?.setStretchFactor(1 - 0.28 - dwShare)
      panes[1]?.setStretchFactor(0.28)
      for (let i = 2; i < panes.length; i++) panes[i]?.setStretchFactor(0.07)
      r.configKey = configKey
    }

    // 데이터 채우기 (구성 무관, 매번)
    r.candle.setData(single ? base.map((c) => ({ time: toChartTime(c.ts), open: c.open, high: c.high, low: c.low, close: c.close })) : [])
    for (const s of S) r.premLines.get(pairKey(s))?.setData(s.candles.map((c) => ({ time: toChartTime(c.ts), value: c.close })))
    for (const ps of priceSpecs) r.priceLines.get(ps.key)?.setData(ps.from.candles.map((c) => ({ time: toChartTime(c.ts), value: ps.pick(c) })))
    for (const d of doms) {
      const from = S.find((s) => s.dom === d)!
      r.dwHists.get(d)?.setData(from.candles.map((c) => ({ time: toChartTime(c.ts), value: 1, color: bandColor[bandTone(c, windowSec)] })))
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
  }, [p.series, p.dir, p.interval])

  // ── 사건 음영만 따로 — 60초 재조회로 사건이 바뀔 때 시리즈 전체를 다시 채우지 않게. 첫 쌍의 봉 시각을 축으로,
  //    어느 국내 거래소 사건이든 하나라도 걸리면 칠한다
  useEffect(() => {
    const r = refs.current
    if (!r || p.series.length === 0) return
    const shadeColor = alpha(cssVar(p.dir === 'kimp' ? '--color-up' : '--color-down', '#e0697d'), 0.13)
    const inEvent = (ts: number) => p.events.some((e) => ts >= e.startTs && (e.endTs == null || ts < e.endTs))
    r.shade.setData(p.series[0].candles.map((c) => inEvent(c.ts) ? { time: toChartTime(c.ts), value: 1, color: shadeColor } : { time: toChartTime(c.ts) }))
  }, [p.series, p.events, p.dir])

  // ── 읽기 줄: 십자선 시각(없으면 마지막 봉)의 쌍별 김프·거래소별 가격·입출금
  const S = p.series
  const base = S[0]?.candles ?? []
  const ts = hoverTs ?? base[base.length - 1]?.ts ?? null
  const at = (s: PairSeries) => (ts == null ? null : findAt(s.candles, ts))
  const single = S.length === 1
  const c0 = single ? at(S[0]) : null
  const doms = [...new Set(S.map((s) => s.dom))]
  const fxs = [...new Set(S.map((s) => s.fx))]

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
  const intervalOpts: SegOpt[] = INTERVALS.map((i) => ({
    label: INTERVAL_LABEL[i], onClick: () => p.onInterval(i),
    bg: p.interval === i ? 'var(--color-neutral-900)' : 'transparent',
    color: p.interval === i ? 'var(--color-accent-300)' : 'var(--color-neutral-500)',
  }))
  const num: CSSProperties = { fontWeight: 500 }
  const sep = <span style={{ color: 'var(--color-neutral-700)' }}>|</span>

  return (
    <div style={{ ...card, padding: 'var(--space-4) var(--space-6)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap', marginBottom: 'var(--space-2)' }}>
        <span style={{ fontFamily: 'var(--font-heading)', fontSize: 18, fontWeight: 500 }}>{p.sym}</span>
        <span style={{ fontSize: 12, color }}>{DIR_LABEL[p.dir]} {INTERVAL_LABEL[p.interval]}봉</span>
        <Seg opts={intervalOpts} pad="5px 9px" />
        {p.loading && <Pill tone="accent">불러오는 중…</Pill>}
        {p.errorStatus != null && <Pill tone="warn">차트를 불러오지 못했습니다 (HTTP {p.errorStatus})</Pill>}
        <span style={{ ...hint, marginLeft: 'auto' }}>휠 = 줌 · 드래그 = 이동 · 왼쪽 끝으로 끌면 과거 로드</span>
      </div>
      {/* 거래소 선택 — 국내·해외 각각 체크박스, 여러 개 가능. 쌍이 하나면 캔들, 여럿이면 선 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)', flexWrap: 'wrap', marginBottom: 'var(--space-3)' }}>
        {group('국내', DOMS, p.doms, exName, p.onDoms)}
        {group('해외', FX_CHOICES.map((f) => f.id), p.fxs, fxLabel, p.onFxs)}
        <span style={hint}>{S.length}쌍 {single ? '· 캔들' : '· 쌍별 종가 선'}</span>
      </div>
      <div style={{ display: 'flex', gap: 'var(--space-6)', flexWrap: 'wrap', fontSize: 11.5, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)', marginBottom: 'var(--space-3)', minHeight: 16 }}>
        {ts == null ? <span style={hint}>데이터 없음</span> : (
          <>
            <span style={{ color: 'var(--color-neutral-500)' }}>{fmtTime(ts * 1000)}</span>
            {single && c0 && (
              <>
                <span>시 <b style={{ ...num, color }}>{fmtPct(c0.open)}</b></span>
                <span>고 <b style={{ ...num, color }}>{fmtPct(c0.high)}</b></span>
                <span>저 <b style={{ ...num, color }}>{fmtPct(c0.low)}</b></span>
                <span>종 <b style={{ ...num, color }}>{fmtPct(c0.close)}</b></span>
              </>
            )}
            {!single && S.map((s, i) => {
              const c = at(s)
              return <span key={pairKey(s)}>{exName(s.dom)}·{fxLabel(s.fx)} <b style={{ ...num, color: PALETTE[i % PALETTE.length] }}>{c ? fmtPct(c.close) : '–'}</b></span>
            })}
            {sep}
            {doms.map((d) => {
              const c = at(S.find((s) => s.dom === d)!)
              return <span key={d}>{exName(d)} <b style={{ ...num, color: 'var(--color-text)' }}>{c ? fmtUsdt(c.krw / c.fxRate) : '–'}</b>{c && <span style={{ color: 'var(--color-neutral-500)' }}> ₩{fmtKrw(c.krw)}</span>}</span>
            })}
            {fxs.map((f) => {
              const c = at(S.find((s) => s.fx === f)!)
              return <span key={f}>{fxLabel(f)} <b style={{ ...num, color: 'var(--color-accent-300)' }}>{c ? fmtUsdt(c.usdt) : '–'}</b></span>
            })}
            {c0 && <span style={{ color: 'var(--color-neutral-500)' }}>USDT {fmtKrw(c0.fxRate)}원</span>}
            {sep}
            {doms.map((d) => {
              const c = at(S.find((s) => s.dom === d)!)
              if (!c) return null
              const st = (okv: boolean | null) => (
                <b style={{ ...num, color: okv == null ? 'var(--color-neutral-500)' : okv ? 'var(--color-neutral-300)' : 'var(--color-up)' }}>{okv == null ? '모름' : okv ? '가능' : '막힘'}</b>
              )
              const fxName = fxs.map(fxLabel).join('/')
              const path = p.dir === 'kimp'
                ? <>{fxName} 출금 {st(c.withdrawOk)} → {exName(d)} 입금 {st(c.depositOk)}</>
                : <>{exName(d)} 출금 {st(c.withdrawOk)} → {fxName} 입금 {st(c.depositOk)}</>
              return <span key={d}>{path}{c.blockedSec > 0 && <span style={{ color: 'var(--color-neutral-500)' }}> · 막힘 {c.blockedSec}초</span>}</span>
            })}
          </>
        )}
      </div>

      <div style={{ position: 'relative' }}>
        <div ref={boxRef} style={{ width: '100%', height: CHART_H }} />
        {S.every((s) => s.candles.length === 0) && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none', color: 'var(--color-neutral-500)', fontSize: 12 }}>
            {p.loading ? '불러오는 중…' : '기간 내 기록 없음'}
          </div>
        )}
      </div>

      <div style={{ ...kicker, marginTop: 'var(--space-2)' }}>
        위 = {DIR_LABEL[p.dir]} 원값 {INTERVAL_LABEL[p.interval]} {single ? '시/고/저/종' : '종가(쌍별)'}, 음영 = 사건 구간 · 가운데 = USDT 기준 거래소별 가격(국내는 환율 환산) · 아래 띠 = {DIR_LABEL[p.dir]} 경로 입출금 — 붉음 = 막힘(연함 = 봉 일부만), 회색 = 모름
      </div>
    </div>
  )
}
