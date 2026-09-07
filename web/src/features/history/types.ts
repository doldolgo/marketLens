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

// ── 1분봉 (스펙 014 예정 — 아직 서버 계약 없음, 화면 시안용 mock 이 이 모양을 만든다) ──
/** (dom, base) 1분 1행. 김프 % 는 선택 방향의 원값 OHLC, 가격은 분 종가, 입출금은 분 끝 시점 상태. */
export interface Candle1m {
  /** 분 시작 epoch 초. */
  ts: number
  open: number
  high: number
  low: number
  close: number
  /** 국내 거래소 원화 종가. */
  krw: number
  /** 해외 거래소 USDT 종가. */
  usdt: number
  /** USDT/KRW 환율 종가. */
  fxRate: number
  /** 분 끝 시점 국내 입금 가능 여부. */
  depositOk: boolean
  /** 분 끝 시점 국내 출금 가능 여부. */
  withdrawOk: boolean
  /** 그 분 안에 입금 또는 출금이 막혀 있던 초 (0~60). */
  blockedSec: number
}
