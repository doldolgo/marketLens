// 사건 목록 → 화면 집계 (스펙 013 §3.5). 순수 함수 — React·DOM 없음이라 node 로 바로 돌려 볼 수 있다.
// 표 집계는 두 단계다: 사건이 바뀔 때만 prepareStats(역프 7일이면 5만 건을 한 번 훑는다), 렌더마다 finishStats(진행 중 사건만).
// 규칙: 횟수 = 기간 안 시작한 사건 수(거래소 전체면 합산), 평균 지속은 끝난 사건만(진행 중은 길이 미정),
// 최대 지속·점유율은 진행 중을 지금까지로 포함, 상태는 진행 중 사건이 하나라도 있으면 `진행 중`.
import type { PremiumEvent } from './types'

/** 사건의 지속 초 — 진행 중은 지금까지(서버 값 대신 셸의 now 로 매 1.5초 흐른다). */
export function durationOf(e: PremiumEvent, nowSec: number): number {
  return e.ongoing ? Math.max(0, nowSec - e.startTs) : e.durationSeconds
}

export interface SymStat {
  sym: string
  /** 진행 중 사건이 있으면 그중 가장 이른 시작(경과가 가장 긴 것), 없으면 null. */
  ongoingSince: number | null
  cnt: number
  maxDur: number
  /** 끝난 사건이 없으면 null. */
  avgDur: number | null
  maxPct: number
  avgPct: number
  /** 가장 최근 사건의 시작 시각. */
  last: number
  /** 가장 최근 사건의 국내 망 표시명(거래소 전체면 두 거래소를 합친 뒤 최근 것) — 없으면 null (024). */
  net: string | null
}

/**
 * 심볼별 집계 1단계 — 사건 목록이 바뀔 때만(60초 재조회·필터 변경) 부른다. 지금 시각에 기대지 않는 합·최대·최근 사건을
 * 한 번 훑어 모아 두고, 진행 중 사건은 시작 시각만 들고 있는다. 결과 순서는 심볼이 처음 나온 순서.
 */
export interface SymBase {
  sym: string
  cnt: number
  /** 끝난 사건의 최대 지속 — 끝난 사건이 없으면 -Infinity. */
  closedMaxDur: number
  /** 진행 중 사건의 시작 시각들(사건 순서). */
  ongoingStarts: number[]
  avgDur: number | null
  maxPct: number
  avgPct: number
  last: number
  net: string | null
}

export function prepareStats(events: PremiumEvent[]): SymBase[] {
  interface Acc extends SymBase { closedN: number; closedSum: number; pctSum: number }
  const bySym = new Map<string, Acc>()
  for (const e of events) {
    let a = bySym.get(e.base)
    if (!a) {
      a = { sym: e.base, cnt: 0, closedMaxDur: -Infinity, ongoingStarts: [], avgDur: null, maxPct: -Infinity, avgPct: 0, last: -Infinity, net: null, closedN: 0, closedSum: 0, pctSum: 0 }
      bySym.set(e.base, a)
    }
    a.cnt++
    if (e.ongoing) a.ongoingStarts.push(e.startTs)
    else {
      a.closedN++
      a.closedSum += e.durationSeconds // 사건 순서대로 더한다 — 합의 부동소수 결과가 한 번에 모으는 방식과 같게
      if (e.durationSeconds > a.closedMaxDur) a.closedMaxDur = e.durationSeconds
    }
    if (e.maxPercent > a.maxPct) a.maxPct = e.maxPercent
    a.pctSum += e.maxPercent
    // 가장 최근 사건 = 시작이 가장 늦은 것, 같은 시작이면 먼저 온 것
    if (e.startTs > a.last) { a.last = e.startTs; a.net = e.netDom }
  }
  return [...bySym.values()].map((a) => ({
    sym: a.sym, cnt: a.cnt, closedMaxDur: a.closedMaxDur, ongoingStarts: a.ongoingStarts,
    avgDur: a.closedN ? a.closedSum / a.closedN : null, maxPct: a.maxPct, avgPct: a.pctSum / a.cnt, last: a.last, net: a.net,
  }))
}

/** 심볼별 집계 2단계 — 렌더마다. 진행 중 사건의 지속(지금까지)만 반영해 최대 지속·상태를 채운다. */
export function finishStats(base: SymBase[], nowSec: number): SymStat[] {
  return base.map((b) => {
    let maxDur = b.closedMaxDur
    let since: number | null = null
    for (const st of b.ongoingStarts) {
      const d = Math.max(0, nowSec - st)
      if (d > maxDur) maxDur = d
      if (since === null || st < since) since = st
    }
    return { sym: b.sym, ongoingSince: since, cnt: b.cnt, maxDur, avgDur: b.avgDur, maxPct: b.maxPct, avgPct: b.avgPct, last: b.last, net: b.net }
  })
}

export type SortKey = keyof SymStat

/** 헤더 정렬 — dir −1 내림차순. 상태 열은 진행 중이 먼저(경과 긴 순). null 은 항상 뒤. */
export function sortStats(stats: SymStat[], key: SortKey, dir: number): SymStat[] {
  const val = (s: SymStat): number | string | null => {
    // 진행 중(startTs 작을수록 오래됨)을 큰 값으로 바꿔 "내림차순 = 진행 중 먼저" 가 되게
    if (key === 'ongoingSince') return s.ongoingSince == null ? null : -s.ongoingSince
    return s[key]
  }
  return [...stats].sort((a, b) => {
    const va = val(a), vb = val(b)
    if (va == null || vb == null) return va == null ? (vb == null ? 0 : 1) : -1
    if (typeof va === 'string' || typeof vb === 'string') return String(va).localeCompare(String(vb)) * dir
    return (va - vb) * dir
  })
}

export interface Summary {
  total: number
  /** 끝난 사건만의 평균 — 없으면 null. */
  avgDur: number | null
  maxDur: number | null
  /** 기간 [지금 − 기간, 지금] 안에서 사건 구간을 합친(겹침 제거) 길이 / 기간 (0~1) — 진행 중은 지금까지로. */
  share: number | null
  ongoingSince: number | null
}

/**
 * 기간 점유율 — 사건 구간 [startTs, startTs + 지속](진행 중은 지금까지)을 기간 안으로 자르고, 시작순으로 정렬해 겹침을 합친 길이 / 기간.
 * 거래소를 합쳐 보면 같은 코인의 두 거래소 사건이 같은 시각에 겹치므로 지속을 그냥 더하면 100% 를 넘는다.
 */
function periodShare(events: PremiumEvent[], nowSec: number, periodSec: number): number {
  const t0 = nowSec - periodSec
  const iv: [number, number][] = []
  for (const e of events) {
    const s = Math.max(t0, e.startTs)
    const t = Math.min(nowSec, e.startTs + durationOf(e, nowSec))
    if (t > s) iv.push([s, t])
  }
  iv.sort((a, b) => a[0] - b[0])
  let covered = 0
  let curS = 0, curT = -Infinity
  for (const [s, t] of iv) {
    if (s > curT) { if (curT > curS) covered += curT - curS; curS = s; curT = t } else if (t > curT) curT = t
  }
  if (curT > curS) covered += curT - curS
  return covered / periodSec
}

export function summarize(events: PremiumEvent[], nowSec: number, periodSec: number): Summary {
  if (events.length === 0) return { total: 0, avgDur: null, maxDur: null, share: null, ongoingSince: null }
  const closed = events.filter((e) => !e.ongoing)
  const ongoing = events.filter((e) => e.ongoing)
  const durs = events.map((e) => durationOf(e, nowSec))
  return {
    total: events.length,
    avgDur: closed.length ? closed.reduce((a, e) => a + e.durationSeconds, 0) / closed.length : null,
    maxDur: Math.max(...durs),
    share: periodShare(events, nowSec, periodSec),
    ongoingSince: ongoing.length ? Math.min(...ongoing.map((e) => e.startTs)) : null,
  }
}

/**
 * 지속 초 → 사람이 읽는 표기: 1시간 미만 `N분`(정수), 하루 미만 `N.N시간`, 그 이상 `N.N일`. 먼저 표시 단위로 반올림하고,
 * 반올림한 값이 다음 단위 경계에 닿으면 다음 단위로 올린다 — 3,599초 → `1.0시간`(`60분` 아님), 86,399초 → `1.0일`(`24.0시간` 아님).
 */
export function fmtDur(sec: number): string {
  const min = sec / 60
  const m = Math.round(min)
  if (m < 60) return `${m}분`
  const h = (min / 60).toFixed(1)
  if (Number(h) < 24) return `${h}시간`
  return `${(min / 60 / 24).toFixed(1)}일`
}
