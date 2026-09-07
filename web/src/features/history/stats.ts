// 사건 목록 → 화면 집계 (스펙 013 §3.5). 순수 함수 — React·DOM 없음이라 node 로 바로 돌려 볼 수 있다.
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
}

export function aggregate(events: PremiumEvent[], nowSec: number): SymStat[] {
  const bySym = new Map<string, PremiumEvent[]>()
  for (const e of events) {
    const list = bySym.get(e.base)
    if (list) list.push(e)
    else bySym.set(e.base, [e])
  }
  const out: SymStat[] = []
  for (const [sym, es] of bySym) {
    const ongoing = es.filter((e) => e.ongoing)
    const closed = es.filter((e) => !e.ongoing)
    const durs = es.map((e) => durationOf(e, nowSec))
    out.push({
      sym,
      ongoingSince: ongoing.length ? Math.min(...ongoing.map((e) => e.startTs)) : null,
      cnt: es.length,
      maxDur: Math.max(...durs),
      avgDur: closed.length ? closed.reduce((a, e) => a + e.durationSeconds, 0) / closed.length : null,
      maxPct: Math.max(...es.map((e) => e.maxPercent)),
      avgPct: es.reduce((a, e) => a + e.maxPercent, 0) / es.length,
      last: Math.max(...es.map((e) => e.startTs)),
    })
  }
  return out
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
  /** Σ지속 / 기간 (0~1) — 진행 중은 지금까지로. */
  share: number | null
  ongoingSince: number | null
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
    share: durs.reduce((a, b) => a + b, 0) / periodSec,
    ongoingSince: ongoing.length ? Math.min(...ongoing.map((e) => e.startTs)) : null,
  }
}
