// /ws/gap 프레임 타입 — BE features/gap/service.py 의 표와 1:1 (스펙 048 §3.2·§3.4).
import type { FeedStatus } from '../../shared/types'

/** 현선갭 행 — 11키 고정. `spot`·`perp` 는 서버 id 이고 화면이 표시명으로 바꾼다 (§3.6). */
export interface GapRow {
  sym: string
  /** 해외 현물 거래소 id (binance·bybit·bitget·okx). */
  spot: string
  /** perp 원천 id (binance_perp·bybit_perp·bitget_perp·hyperliquid_perp). */
  perp: string
  /** 현물 마지막 체결가(USDT). fail 이면 0. */
  spotPrice: number
  /** 진입 갭 % — 현물 ask 에 사고 perp bid 에 숏. 클수록 좋다. fail 이면 0. */
  entry: number
  /** 정리 갭 % — 현물 bid 에 팔고 perp ask 에 청산. 작을수록 좋다. fail 이면 0. */
  exit: number
  /** 한 주기의 펀딩률 % (예 0.01 = 0.01%). 모르면 null. */
  funding: number | null
  /** 펀딩 주기(정수 시간). 이 탭은 표시하지 않는다 — 049 가 쓴다. */
  intervalH: number | null
  /** 다음 정산 epoch 초. 모르면 null. */
  nextFundingTs: number | null
  status: FeedStatus
  age: number
}

export interface GapResponse {
  rows: GapRow[]
  /** 지금은 항상 빈 목록 — 017 과 모양을 맞추기 위해 둔다. */
  warnings: string[]
  dataReceivedAt: number | null
  fetchedAt: number
}

/** /ws/gap 서버 → 클라이언트 메시지 — 017 §3.3 과 같은 네 종류. `removed` 원소는 `sym|spot|perp`. */
export type GapMessage =
  | ({ type: 'snapshot' } & GapResponse)
  | ({ type: 'delta'; removed: string[] } & GapResponse)
  | { type: 'heartbeat' }
  | { type: 'waiting' }
