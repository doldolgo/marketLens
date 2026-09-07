// GET /history/events 응답 계약 (스펙 013 §3.4). 키는 서버가 camelCase 로 내려준다.

export type Dir = 'kimp' | 'reverse'
export type Dom = 'upbit' | 'bithumb'

/** 사건 1건 — 원값이 1.0% 이상으로 진입해 0.5% 이하로 이탈할 때까지, 1분 초과인 것. */
export interface PremiumEvent {
  base: string
  dom: Dom
  fx: string
  dir: Dir
  startTs: number
  /** 진행 중이면 null. */
  endTs: number | null
  /** 진행 중이면 서버 기준 지금 − startTs. */
  durationSeconds: number
  ongoing: boolean
  maxPercent: number
  maxTs: number
  samples: number
}

export interface EventsResponse {
  startTs: number
  endTs: number
  count: number
  fetchedAt: number
  events: PremiumEvent[]
}
