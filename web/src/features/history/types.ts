// GET /history/streaks 응답 계약 (스펙 005 §3.4). 키는 서버가 camelCase 로 내려준다.

/** streak 구간 1개 — 기준 이상 값이 이어진 시작~끝. */
export interface Segment {
  startTs: number
  endTs: number
  /** KST 표기 문자열 — 화면은 startTs/endTs 를 로컬로 포맷하므로 쓰지 않는다. */
  start: string
  end: string
  /** end − start 초 (기록 1개면 0). */
  durationSeconds: number
  samples: number
  maxPercent: number
  avgPercent: number
}

export interface DirectionSummary {
  count: number
  maxDurationSeconds: number
  avgDurationSeconds: number
  maxPercent: number
  avgPercent: number
  segments: Segment[]
}

export interface StreaksResponse {
  base: string
  dom: string
  fx: string
  thresholdPercent: number
  maxGapSeconds: number
  startTs: number
  endTs: number
  /** fwd(김프) 방향. */
  kimp: DirectionSummary
  /** rev(역프) 방향. */
  reverse: DirectionSummary
  scanned: number
  /** 조회 구간 안 마지막 기록 시각 — "진행 중" 판정 기준 (§3.6). */
  lastUpdatedTs: number
  lastUpdated: string
  fetchedAt: number
}

/** 화면이 그리는 사건 1행 — 두 방향 segments 를 합친 뒤 유형을 붙인 것. */
export interface HistoryEvent {
  type: 'kimp' | 'rev'
  seg: Segment
}
