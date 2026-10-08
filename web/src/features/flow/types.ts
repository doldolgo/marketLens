// 입출금 레이더 API 응답 계약 (스펙 050 §3.6·§3.7). 키는 서버가 camelCase 로 내려준다.

/** 순유입 집계 창 — `window` 쿼리값 그대로 (§3.6). */
export type FlowWindow = '1h' | '6h' | '24h'
/** 최근 전송 표의 방향 필터 — `dir` 쿼리값 그대로 (§3.7). */
export type FlowDir = 'all' | 'in' | 'out'

/** 감지기 상태 (§3.6 `feed`). 감지기가 꺼져 있으면 connected false·lastBlock null·lagSec null·집합 크기 0. */
export interface FlowFeed {
  /** 소켓이 열려 있고 30초 안에 newHeads 를 받았는가. */
  connected: boolean
  lastBlock: number | null
  /** 지금 − 마지막 블록 시각(초). */
  lagSec: number | null
  /** 입금주소 집합 크기(씨앗 + 자가 확장). */
  depositAddrs: number
  /** 핫월렛 집합 크기(씨앗 + 자가 확장). */
  hotWallets: number
  /** 구독하는 ERC-20 컨트랙트 수(씨앗 행 수, 감지기 꺼짐이면 0). */
  contracts: number
}

/** GET /flow/netflow 행 1개 = 코인 1개 (§3.6). 정렬은 서버가 |netKrw| 내림차순, null 은 뒤에서 |netAmount| 내림차순. */
export interface NetflowRow {
  symbol: string
  inCount: number
  inAmount: number
  outCount: number
  outAmount: number
  /** 창 안 입금 수량 − 출금 수량. */
  netAmount: number
  /** netAmount × 업비트 KRW 현재가. 현재가 없으면 null. */
  netKrw: number | null
  /** 그 코인의 마지막 전송 블록 시각(초). */
  lastTs: number
}

/** GET /flow/netflow 응답 (§3.6). */
export interface NetflowResponse {
  window: FlowWindow
  /** 서버 기준 응답 시각(초). */
  asOf: number
  feed: FlowFeed
  rows: NetflowRow[]
}

/** GET /flow/recent 행 1개 = 전송 1건 (§3.7). 주소는 전체 42자 — 축약은 화면이 한다. */
export interface RecentRow {
  /** 블록 시각(초). */
  ts: number
  block: number
  /** head − block ≥ 2 (§3.3). false 면 화면이 `확정 전` 칩을 단다. */
  confirmed: boolean
  dir: 'in' | 'out'
  symbol: string
  amount: number
  /** 수량 × 업비트 KRW 현재가. 현재가 없으면 null. */
  krw: number | null
  /** 입금이면 입금주소, 출금이면 핫월렛. */
  addr: string
  /** 입금이면 보낸 쪽, 출금이면 받는 쪽. */
  counterparty: string
  txHash: string
}

/** GET /flow/recent 응답 (§3.7). 최근 24시간 안에서 최신순. */
export interface RecentResponse {
  asOf: number
  /** 서버가 아는 최신 블록 번호. */
  head: number
  rows: RecentRow[]
}
