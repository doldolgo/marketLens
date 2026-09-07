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

// ── GET /history/candles 응답 계약 (스펙 014 §3.6) ──
export type Res = '1m' | '5m' | '1h' | '4h' | '1d'

/** 봉 1개 — 창 시작 ts(KST 정렬). 김프 % 는 선택 방향의 원값 OHLC, 가격은 창 종가, 입출금은 방향 경로의 두 끝(창 끝 시점). */
export interface Candle1m {
  /** 창 시작 epoch 초. */
  ts: number
  open: number
  high: number
  low: number
  close: number
  /** 국내 거래소 원화 종가. */
  krw: number
  /** 해외 거래소 USDT 종가. */
  usdt: number
  /** USDT 시세 중간값(원). */
  fxRate: number
  /** 경로의 입금 쪽(김프 = 국내, 역프 = 해외) 가능 여부. null = 모름(조회 실패). */
  depositOk: boolean | null
  /** 경로의 출금 쪽(김프 = 해외, 역프 = 국내) 가능 여부. null = 모름. */
  withdrawOk: boolean | null
  /** 그 창 안에 경로가 막혀 있던 초(0~창 길이). 모름은 세지 않는다. */
  blockedSec: number
  /** 창에 든 틱 수. */
  samples: number
}

export interface CandlesResponse {
  base: string
  res: Res
  dom: Dom
  fx: string
  dir: Dir
  startTs: number
  endTs: number
  count: number
  fetchedAt: number
  candles: Candle1m[]
}
