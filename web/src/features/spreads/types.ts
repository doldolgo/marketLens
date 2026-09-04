// GET /spreads 응답 타입 — BE features/spreads/models.py 와 1:1 (스펙 003 §3.2).
// 행은 002 shared 의 SpreadRow 17키와 정확히 같다 — 확장 키가 없다.
import type { SpreadRow } from '../../shared/types'

/** HTTP 응답 키는 camelCase (architecture.md 계약 규칙). */
export interface SpreadsResponse {
  rate: number
  /** 이 응답의 슬리피지가 계산된 체결 규모(USD). */
  notional: number
  rows: SpreadRow[]
  /** USDT 시세 미갱신 경고 — 없으면 빈 배열 (008). */
  warnings: string[]
  dataReceivedAt: number | null
  fetchedAt: number
}
