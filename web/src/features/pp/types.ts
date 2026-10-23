// /ws/pp 프레임 타입 — BE features/pp/service.py 의 표와 1:1 (스펙 049 §3.2·§3.3).
import type { FeedStatus } from '../../shared/types'

/** 선선갭 행 — 13키 고정, 순서 없는 perp 원천 쌍 하나. `a`·`b` 는 서버 id 이고 `a` 가 원천 순서에서 앞이다. */
export interface PpRow {
  sym: string
  /** perp 원천 id (binance_perp·bybit_perp·bitget_perp·hyperliquid_perp). */
  a: string
  b: string
  /** a 의 ask 에 롱·b 의 bid 에 숏 할 때의 가격갭 % — 양수면 b 가 비싸다. fail 이면 0. */
  longA: number
  /** b 의 ask 에 롱·a 의 bid 에 숏 할 때의 가격갭 %. fail 이면 0. */
  longB: number
  /** 한 주기의 펀딩률 % (예 0.01 = 0.01%). 모르면 null. */
  fundA: number | null
  /** 펀딩 주기(정수 시간) — 시간당 정규화에 쓴다. 모르면 null. */
  intA: number | null
  /** 다음 정산 epoch 초. 모르면 null. */
  nextA: number | null
  fundB: number | null
  intB: number | null
  nextB: number | null
  status: FeedStatus
  age: number
}

export interface PpResponse {
  rows: PpRow[]
  /** 지금은 항상 빈 목록 — 017 과 모양을 맞추기 위해 둔다. */
  warnings: string[]
  dataReceivedAt: number | null
  fetchedAt: number
}

/** /ws/pp 서버 → 클라이언트 메시지 — 017 §3.3 과 같은 네 종류. `removed` 원소는 `sym|a|b`. */
export type PpMessage =
  | ({ type: 'snapshot' } & PpResponse)
  | ({ type: 'delta'; removed: string[] } & PpResponse)
  | { type: 'heartbeat' }
  | { type: 'waiting' }
