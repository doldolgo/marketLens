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
  /** 순방향 김프 % — 서버가 체결 규모만큼 호가를 걷고 슬리피지를 뺀 **순값**이다 (003 §3.2). */
  fwd: number
  /** 역방향 김프 % — 마찬가지로 순값. */
  rev: number
  usd: number | null
  spark: number[]
  status: FeedStatus
  age: number
  /** 그 방향에서 차감된 폭(%p, 양수). 원값이 필요하면 fwd + slipFwd. */
  slipFwd: number
  slipRev: number
  /** 이 행 국내 거래소의 최우선 매수호가(KRW) — FE 는 환산하지 않고 그대로 쓴다. */
  krw: number
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

// ── 수집 상태 — GET /health/collect 응답 (011 §3.5, BE features/health/models.py 와 1:1) ──
// 셸 KPI 와 공유 피드가 알아야 해서 shared 에 둔다. 시각은 전부 epoch ms.

export type HealthState = 'ok' | 'stale' | 'down'

export type OutageKind =
  | 'timeout'
  | 'network'
  | 'rate_limit'
  | 'banned'
  | 'unavailable'
  | 'bad_request'
  | 'bad_response'

/** 실패 구간 1건 — openOutage 와 outages 항목이 같은 모양. endedAt null = 진행 중. */
export interface HealthOutage {
  exchange: string
  kind: OutageKind
  startedAt: number
  endedAt: number | null
  lastFailedAt: number
  count: number
  statusCode: number | null
  message: string
  url: string | null
  retryAfterSec: number | null
}

export interface HealthLastError {
  at: number
  kind: OutageKind
  statusCode: number | null
  message: string
}

export interface HealthExchange {
  exchange: string
  state: HealthState
  lastSuccessAt: number | null
  markets: number
  successRate1h: number
  openOutage: HealthOutage | null
  lastError: HealthLastError | null
}

export interface HealthData {
  serverStartedAt: number
  fetchedAt: number
  successRate1h: number
  /** 거래소 3곳 고정 순서 upbit·bithumb·binance. */
  exchanges: HealthExchange[]
  /** 24시간 구간 전부(진행 중 포함), startedAt 내림차순. */
  outages: HealthOutage[]
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
  /** /health/collect 마지막 응답 — 첫 응답 전 null (011). */
  health: HealthData | null
  flowAddrs: FlowAddr[]
  flowRows: FlowRow[]
  /** spreads 행 + rate 통째 교체 — 003 이 1초 폴링으로 호출. io 는 새 행들로부터 재구성. */
  replace(rows: SpreadRow[], rate: number): void
  /** 수집 상태 적용 — 011 이 5초 폴링으로 호출. */
  setHealth(data: HealthData): void
  /** 005 가 실 DB 전에 화면을 채우는 mock 사건 목록. per: 기간 선택값, now: epoch ms. */
  events(per: string, now: number): MockEvent[]
}
