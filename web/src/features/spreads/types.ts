// GET /spreads 응답 타입 — BE features/spreads/models.py 와 1:1 (스펙 003 §3.2).
// 002 shared 의 SpreadRow 에는 rateAsk·rateBid 가 없다(shared 수정 금지) — 확장 타입으로
// 2키를 더하고, TS 구조적 타이핑으로 확장 객체를 공유 피드에 그대로 넘긴다.
import type { SpreadRow } from '../../shared/types'

/** 응답 행 18키 = 002 SpreadRow 16키 + 이 행 국내 거래소의 USDT 매수/매도 환율 2키. */
export interface ApiSpreadRow extends SpreadRow {
  rateAsk: number
  rateBid: number
}

/** 최상위 키는 snake_case (architecture.md 계약 규칙). */
export interface SpreadsResponse {
  rate: number
  rows: ApiSpreadRow[]
  data_received_at: number | null
  fetched_at: number
}
