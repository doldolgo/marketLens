// 공유 피드 계약 — 003(spreads)·005(history)가 의존하는 유일한 인터페이스 (스펙 002 §3.4).

/** 피드 상태. */
export type FeedStatus = 'ok' | 'stale' | 'fail'

/** 입출금 상태 — 열림 true / 막힘 false / 확인 불가 null. null 을 열림(초록)으로 해석하면 버그다. */
export type IoState = boolean | null

/** 국내×해외 페어 1개 — 003 의 GET /spreads 응답 행과 1:1. */
export interface SpreadRow {
  sym: string
  dom: string
  fx: string
  fwd: number
  rev: number
  usd: number | null
  spark: number[]
  status: FeedStatus
  age: number
  liqDom: number
  liqFx: number
  netDom: string | null
  depDom: IoState
  wdDom: IoState
  depFx: IoState
  wdFx: IoState
}

/** io 맵 항목 — 키는 "{sym}|{거래소 표시명}". */
export interface IoEntry {
  dep: IoState
  wd: IoState
  net: string
}

// ── mock 탭용 데이터 모양 (§3.6~3.10) ──────────────────────────────────────

/** 현물 항목 — off 는 기준가 대비 편차 %. */
export interface SpotItem {
  ex: string
  off: number
  status: FeedStatus
  age: number
}

/** 선물 항목 — prem 은 현물 대비 프리미엄 %, funding 은 소수 3자리 %. */
export interface PerpItem {
  ex: string
  prem: number
  funding: number
  status: FeedStatus
  age: number
}

/** 코인 1개의 mock 마켓 (갭·선선갭 탭 공용). */
export interface MockMarket {
  sym: string
  base: number
  spot: SpotItem[]
  perp: PerpItem[]
}

/** 수집 상태 (일 단위 시드). */
export type HealthState = 'ok' | 'stale' | 'down'

/** 결측 구간 — 지금 기준 몇 분 전에 시작해 몇 분 지속. */
export interface HealthGap {
  startAgoMin: number
  durMin: number
}

export interface HealthEx {
  name: string
  spot: number
  perp: number
  state: HealthState
  failRate: number
  lastRecvSec: number
  gaps: HealthGap[]
}

export interface HealthEvent {
  ageMin: number
  ex: string
  kind: string
  msg: string
}

/** 입출금 레이더 주소. */
export interface FlowAddr {
  id: string
  label: string
  short: string
  coins: string[]
  exs: string[]
}

/** 입출금 레이더 행. */
export interface FlowRow {
  addr: string
  label: string
  short: string
  sym: string
  ex: string
  dir: 'in' | 'out'
  usd: number | null
  qty: number
  state: string
  age: number
}

/** 005(기록/통계) mock 사건. start 는 epoch ms, peak 는 최고 %. */
export interface MockEvent {
  sym: string
  type: 'kimp' | 'rev'
  dom: string
  start: number
  durMin: number
  peak: number
}

/** 셸이 모든 탭에 내려주는 공유 피드 하나. */
export interface Feed {
  /** 이 스펙에서는 항상 빈 배열 — 003 이 채운다. */
  spreads: SpreadRow[]
  /** USDT/KRW 암묵환율. 0 = 아직 없음. */
  rate: number
  /** 키 "{sym}|{거래소 표시명}" → 입출금 상태. */
  io: Record<string, IoEntry>
  markets: MockMarket[]
  health: HealthEx[]
  healthEvents: HealthEvent[]
  flowAddrs: FlowAddr[]
  flowRows: FlowRow[]
  /** spreads 행 + rate 통째 교체 — 003 이 1초 폴링으로 호출. io 는 새 행들로부터 재구성. */
  replace(rows: SpreadRow[], rate: number): void
  /** 005 가 실 DB 전에 화면을 채우는 mock 사건 목록. per: 기간 선택값, now: epoch ms. */
  events(per: string, now: number): MockEvent[]
}
