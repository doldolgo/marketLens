// 차트 카드 시리즈에 넣을 점 배열 (스펙 014 §3.7·015) — 순수 함수. lightweight-charts·DOM 없이 돌아 node 로 바로 확인할 수 있다.
// Chart.tsx 는 여기서 만든 배열을 setData(전체) 또는 update(끝 봉부터)로 넘기기만 한다.
import type { UTCTimestamp } from 'lightweight-charts'
import { exchangeStates, lineTone, sameCandle, type BandTone } from './candles'
import type { Candle1m, Dir, Dom, PremiumEvent } from './types'

/** (국내, 해외) 거래소 쌍 하나의 봉. */
export interface PairSeries {
  dom: Dom
  fx: string
  /** 시각 오름차순, 이미 interval 로 접힌 봉. */
  candles: Candle1m[]
}
/** 시간축·음영·읽기 줄의 기준 쌍 — 봉이 있는 첫 쌍. 업비트에 원화 마켓이 없는 코인은 첫 쌍(업비트)이 비어 있어 이걸 축으로 잡으면 아무것도 안 보인다. */
export const axisOf = (S: PairSeries[]): PairSeries => S.find((s) => s.candles.length > 0) ?? S[0]

/** 라이브러리는 시각을 UTC 로 그린다 → 로컬(KST) 로 보이게 offset 을 더해 넣고, 읽을 때 뺀다. */
const TZ_OFF = -new Date().getTimezoneOffset() * 60
export const toChartTime = (ts: number) => (ts + TZ_OFF) as UTCTimestamp
export const fromChartTime = (t: number) => t - TZ_OFF

export function alpha(hex: string, a: number): string {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  if (!m) return hex
  return `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})`
}

/** 경로에 안 드는 입출금 줄의 투명도 배율. */
const DIM = 0.4

/** 입출금 띠 색 — 상태 3가지 × [선명, 흐림]. 봉마다 색 문자열을 만들지 않게 카드 갱신마다 한 번만 만든다. */
export type BandPalette = Record<BandTone, readonly [string, string]>

/** 식은 줄마다 쓰던 것 그대로 둔다(0.9 × 0.4 를 0.36 으로 줄여 쓰면 부동소수 문자열이 달라진다). */
export function bandPalette(blockedHex: string, unknownHex: string, okHex: string): BandPalette {
  return {
    blocked: [alpha(blockedHex, 0.9 * 1), alpha(blockedHex, 0.9 * DIM)],
    unknown: [alpha(unknownHex, 0.55 * 1), alpha(unknownHex, 0.55 * DIM)],
    open: [alpha(okHex, 0.45 + 0.2), alpha(okHex, 0.45 + 0)],
  }
}

/** 방향 경로에 안 드는 줄인가 — 김프 경로 = 해외 출금·국내 입금, 역프 = 국내 출금·해외 입금. 나머지 줄은 흐리게. */
export function bandDimmed(isFx: boolean, dir: Dir, kind: 'deposit' | 'withdraw'): boolean {
  const relevant = isFx ? (dir === 'kimp' ? 'withdraw' : 'deposit') : (dir === 'kimp' ? 'deposit' : 'withdraw')
  return kind !== relevant
}

/**
 * 카드의 시간축 = 카드에 붙은 모든 쌍의 봉 시각 합집합(오름차순). 라이브러리는 차트 안 모든 시리즈의 시각을 합쳐 봉 index 를 매기므로,
 * 쌍 하나의 봉 수로 범위를 잡으면 두 국내 거래소의 봉 수가 다른 코인에서 최신 구간이 화면 밖으로 밀린다.
 * 쌍마다 봉이 이미 시각 오름차순(014 §3.6)이라 정렬 없이 차례로 병합한다 — 같은 시각은 하나만.
 */
export function axisTimes(S: PairSeries[]): number[] {
  let out: number[] = []
  for (const s of S) {
    const a = out, b = s.candles
    const merged: number[] = []
    let i = 0, j = 0
    while (i < a.length || j < b.length) {
      const t = j >= b.length || (i < a.length && a[i] <= b[j].ts) ? a[i++] : b[j++].ts
      if (merged.length === 0 || merged[merged.length - 1] !== t) merged.push(t)
    }
    out = merged
  }
  return out
}

export interface CandlePoint { time: UTCTimestamp; open: number; high: number; low: number; close: number }
export interface LinePoint { time: UTCTimestamp; value: number }
export interface BandPoint { time: UTCTimestamp; value: number; color: string }

/** 카드 한 장의 시리즈별 점. 키는 Chart.tsx 의 시리즈 표와 같다 — 가격 `fx`·`dom:{국내}`, 띠 `{fx|국내}:{deposit|withdraw}`. */
export interface CardPoints {
  /** 국내 1개일 때만 — 여럿이면 빈 배열(선이 맡는다). */
  candle: CandlePoint[]
  /** 국내 여럿일 때만 — 거래소별 김프 종가 선. */
  prem: Map<Dom, LinePoint[]>
  price: Map<string, LinePoint[]>
  band: Map<string, BandPoint[]>
}

/**
 * 카드의 모든 시리즈 점. `fromOf(쌍)` 은 그 쌍의 봉 중 몇 번째부터 만들지(끝 봉부터 update 할 때) — 없으면 처음부터(setData).
 * 입출금 띠는 줄마다 봉당 exchangeStates 를 한 번만 불러 입금·출금 두 배열을 같이 채운다.
 */
export function cardPoints(S: PairSeries[], dir: Dir, palette: BandPalette, fromOf: (s: PairSeries) => number = () => 0): CardPoints {
  const axis = axisOf(S)
  const single = S.length === 1
  const line = (s: PairSeries, value: (c: Candle1m) => number): LinePoint[] => {
    const cs = s.candles
    const out: LinePoint[] = []
    for (let i = fromOf(s); i < cs.length; i++) out.push({ time: toChartTime(cs[i].ts), value: value(cs[i]) })
    return out
  }
  const candle: CandlePoint[] = []
  if (single) {
    const cs = axis.candles
    for (let i = fromOf(axis); i < cs.length; i++) {
      const c = cs[i]
      candle.push({ time: toChartTime(c.ts), open: c.open, high: c.high, low: c.low, close: c.close })
    }
  }
  const prem = new Map<Dom, LinePoint[]>()
  if (!single) for (const s of S) prem.set(s.dom, line(s, (c) => c.close))
  const price = new Map<string, LinePoint[]>()
  price.set('fx', line(axis, (c) => c.usdt))
  for (const s of S) price.set(`dom:${s.dom}`, line(s, (c) => c.krw / c.fxRate))
  const band = new Map<string, BandPoint[]>()
  const bandRow = (id: string, from: PairSeries, isFx: boolean) => {
    const dimDep = bandDimmed(isFx, dir, 'deposit') ? 1 : 0
    const dimWd = bandDimmed(isFx, dir, 'withdraw') ? 1 : 0
    const dep: BandPoint[] = []
    const wd: BandPoint[] = []
    const cs = from.candles
    for (let i = fromOf(from); i < cs.length; i++) {
      const c = cs[i]
      const time = toChartTime(c.ts)
      const e = exchangeStates(c, dir)
      dep.push({ time, value: 2, color: palette[lineTone(isFx ? e.fxDeposit : e.domDeposit)][dimDep] })
      wd.push({ time, value: 0.9, color: palette[lineTone(isFx ? e.fxWithdraw : e.domWithdraw)][dimWd] })
    }
    band.set(`${id}:deposit`, dep)
    band.set(`${id}:withdraw`, wd)
  }
  bandRow('fx', axis, true)
  for (const s of S) bandRow(s.dom, s, false)
  return { candle, prem, price, band }
}

/**
 * 새 봉 배열이 직전 배열 끝에만 붙은 것인가 — 맞으면 끝 봉부터 update 할 시작 index(직전 마지막 봉 자리), 아니면 null(setData).
 * 직전 마지막 봉 앞까지는 시각·값이 모두 같아야 하고(늦게 쓰인 중간 봉·접힌 버킷 값 변화·심볼 전환은 여기서 걸린다),
 * 직전 마지막 봉은 시각만 같으면 된다(접는 중인 버킷은 값이 바뀐다 — update 가 같은 시각을 덮어쓴다).
 */
export function appendStart(prev: Candle1m[], next: Candle1m[]): number | null {
  const p = prev.length
  if (p === 0 || next.length < p) return null
  if (next[p - 1].ts !== prev[p - 1].ts) return null
  for (let i = 0; i < p - 1; i++) if (!sameCandle(prev[i], next[i])) return null
  return p - 1
}

/** 사건 음영 점 — 칠하는 봉은 값 1·색, 안 칠하는 봉은 시각만(빈 칸). */
export type ShadePoint = BandPoint | { time: UTCTimestamp }

/**
 * 사건 음영 (013 §3.5·014 §3.7) — 사건 구간 [startTs, endTs)(진행 중은 끝없음)이 봉 구간 [ts, ts + barSec) 와 겹치면 칠한다:
 * startTs < ts + barSec 이고 (endTs 없음 또는 endTs > ts). 봉 시작 시각만 보면 봉 길이 안에서 시작·끝난 사건이 긴 봉에서 빠진다.
 * 사건 구간을 시작순으로 정렬해 겹침을 합친 뒤 오름차순 봉과 한 번에 훑는다 — 봉마다 사건 전체를 보면 봉 × 사건이라
 * 과거를 끌어 붙일수록(봉·사건이 같이 는다) 제곱으로 느려진다. 봉은 ts 오름차순이어야 한다(청크 병합·rollup 이 지킨다).
 */
export function shadePoints(candles: Candle1m[], events: PremiumEvent[], barSec: number, color: string): ShadePoint[] {
  const iv: [number, number][] = []
  for (const e of events) {
    const end = e.endTs == null ? Infinity : e.endTs
    if (end > e.startTs) iv.push([e.startTs, end]) // 길이 0 이하 구간은 어떤 봉과도 겹치지 않는다
  }
  iv.sort((a, b) => a[0] - b[0])
  const merged: [number, number][] = []
  for (const [s, t] of iv) {
    const last = merged[merged.length - 1]
    if (last && s <= last[1]) { if (t > last[1]) last[1] = t } else merged.push([s, t])
  }
  const out: ShadePoint[] = []
  let k = 0
  for (const c of candles) {
    const ts = c.ts
    // 이 봉 시작 전에 끝난 구간은 뒤 봉(더 늦게 시작)과도 겹치지 않는다
    while (k < merged.length && merged[k][1] <= ts) k++
    const time = toChartTime(ts)
    out.push(k < merged.length && merged[k][0] < ts + barSec ? { time, value: 1, color } : { time })
  }
  return out
}
