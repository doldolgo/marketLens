// GET /spreads 응답 타입 — BE features/spreads/models.py 와 1:1 (스펙 003 §3.2). patch 항목은 features/spreads/hub.py (017).
// 행은 002 shared 의 SpreadRow 17키와 정확히 같다 — 확장 키가 없다.
import type { SpreadRow } from '../../shared/types'

/** HTTP 응답 키는 camelCase (architecture.md 계약 규칙). */
export interface SpreadsResponse {
  rate: number
  /** 이 응답의 슬리피지가 계산된 체결 규모(USD) — 화면은 $1,000 고정 (017). */
  notional: number
  rows: SpreadRow[]
  /** USDT 시세 미갱신 경고 — 없으면 빈 배열 (008). */
  warnings: string[]
  dataReceivedAt: number | null
  fetchedAt: number
}

/**
 * patch 항목 하나 — 행 키 셋(서버 id) + 직전과 다른 필드 + age (017 §3.3). spark 는 셋 중 하나만 온다:
 * `sparkEnd` 끝값 바꾸기(같은 분), `sparkPush` 앞 하나 버리고 붙이기(분이 바뀜), `spark` 목록 통째.
 */
export type SpreadPatch = Pick<SpreadRow, 'sym' | 'dom' | 'fx' | 'age'>
  & Partial<Omit<SpreadRow, 'sym' | 'dom' | 'fx' | 'age'>>
  & { sparkEnd?: number; sparkPush?: number }

/** /ws/spreads 서버 → 클라이언트 메시지 (스펙 017 §3.3). `?v=2` 로 붙으면 새 표마다 delta 대신 patch 가 온다. */
export type SpreadsMessage =
  | ({ type: 'snapshot' } & SpreadsResponse)
  | ({ type: 'delta'; removed: string[] } & SpreadsResponse)
  | ({ type: 'patch'; patch: SpreadPatch[]; removed: string[] } & SpreadsResponse)
  | { type: 'heartbeat' }
  | { type: 'waiting' }
