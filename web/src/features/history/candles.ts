// 봉 → 계층·청크 계산 (순수 함수) — 스펙 014 §3.6~3.7. 서버 계층은 1m·5m·1h·4h·1d 다섯 개(닫힌 창만 저장),
// 화면의 봉 8종은 계층 하나를 골라 그 안에서 접는다(rollup.ts). 서버는 요청당 1,440창 상한이 있어
// 그 길이를 청크로 삼아 (쌍, 방향, 계층, 청크 시작) 단위로 받아 두고, 없는 청크만 새로 부른다.
import type { Interval } from './rollup'
import type { Candle1m, Dir, Dom, Res } from './types'

/** 처음 보이는 봉 개수(봉 종류 무관). 이후는 사용자가 줌·이동. */
export const INITIAL_BARS = 360

/** 차트에서 고를 수 있는 해외 거래소 — 서버가 수집하는 해외 거래소는 binance 뿐(§2). `mock` 은 015 UI 시안용:
 *  binance 실봉을 변형해 그린다(mock.ts). 수집 스펙이 생기면 mock 을 지우고 실데이터로 바꾼다. */
export const FX_CHOICES: { id: string; label: string; mock?: boolean }[] = [
  { id: 'binance', label: 'Binance' },
  { id: 'bybit', label: 'Bybit', mock: true },
  { id: 'mexc', label: 'MEXC', mock: true },
]
export const isMockFx = (id: string) => FX_CHOICES.some((f) => f.id === id && f.mock)
/** 서버에서 실제로 받는 해외 거래소 — mock 은 이걸 변형해 만든다. */
export const REAL_FXS = FX_CHOICES.filter((f) => !f.mock).map((f) => f.id)

/** 봉 종류 → 서버 계층. 계층 안에서만 접는다(3m 은 1m 셋, 15m·30m 은 5m 셋·여섯). */
export const RES_OF_INTERVAL: Record<Interval, Res> = {
  '1m': '1m', '3m': '1m', '5m': '5m', '15m': '5m', '30m': '5m', '1h': '1h', '4h': '4h', '1d': '1d',
}
export const RES_SEC: Record<Res, number> = { '1m': 60, '5m': 300, '1h': 3600, '4h': 14_400, '1d': 86_400 }
/** 요청당 상한(창 수) — 서버 §3.6 과 같은 값. 청크 길이 = 상한 × 창 길이. */
export const LIMIT_WINDOWS = 1440
export const resLimitSec = (res: Res) => LIMIT_WINDOWS * RES_SEC[res]
/** 계층 보관 기간(초) — 과거 로드 상한. 1d 는 무제한(null). */
export const RES_RETENTION_SEC: Record<Res, number | null> = {
  '1m': 7 * 86_400, '5m': 30 * 86_400, '1h': 90 * 86_400, '4h': 365 * 86_400, '1d': null,
}
/** 서버 창 정렬은 KST 벽시계(§3.4) — 청크·접기도 같은 규칙. 브라우저 시간대와 무관하게 상수. */
export const KST_OFFSET_SEC = 32_400

/** ts 가 속한 청크의 시작 — KST 자정 정렬. */
export function chunkStart(ts: number, res: Res): number {
  const L = resLimitSec(res)
  return Math.floor((ts + KST_OFFSET_SEC) / L) * L - KST_OFFSET_SEC
}

export interface ChunkRef { dom: Dom; fx: string; base: string; dir: Dir; res: Res; start: number }
export const chunkKey = (c: ChunkRef) => `${c.dom}|${c.fx}|${c.base}|${c.dir}|${c.res}|${c.start}`

/**
 * 지금 화면이 필요로 하는 청크 시작들(오래된 것부터, 마지막이 최신 청크).
 * 처음엔 INITIAL_BARS × 봉 초를 덮는 청크들, 왼쪽으로 끌 때마다 `older` 개를 더 앞에 붙인다.
 * 계층 보관 기간 밖 청크는 뺀다 — `oldestReached` 는 더 앞 청크에 기록이 있을 수 없다는 뜻(과거 로드를 멈춘다).
 */
export function neededChunks(nowSec: number, res: Res, intervalSec: number, older: number): { starts: number[]; oldestReached: boolean } {
  const L = resLimitSec(res)
  const latest = chunkStart(nowSec, res)
  let first = chunkStart(nowSec - INITIAL_BARS * intervalSec, res) - older * L
  const retention = RES_RETENTION_SEC[res]
  // 보관 기간의 시작이 든 청크까지는 부분적으로 기록이 있을 수 있으므로 그 청크는 받고, 그보다 앞은 없다
  const floor = retention == null ? null : chunkStart(nowSec - retention, res)
  let oldestReached = false
  if (floor != null && first <= floor) { first = floor; oldestReached = true }
  const starts: number[] = []
  for (let s = first; s <= latest; s += L) starts.push(s)
  return { starts, oldestReached }
}

/** 거래소별 줄 1개의 색(015 §띠): 창 끝 상태만 본다 — 막힌 초는 경로 단위(`blockedSec`)라 줄마다 나눌 수 없어 연한 붉음은 여기 없다. */
export type BandTone = 'open' | 'blocked' | 'unknown'
export const lineTone = (ok: boolean | null | undefined): BandTone => (ok == null ? 'unknown' : ok ? 'open' : 'blocked')

/** 거래소별 입출금 4상태. 서버가 4상태를 안 주면 경로 2상태(김프 = 해외 출금·국내 입금, 역프 = 국내 출금·해외 입금)로 채우고 나머지는 모름. */
export interface ExchangeStates { domDeposit: boolean | null; domWithdraw: boolean | null; fxDeposit: boolean | null; fxWithdraw: boolean | null }
export function exchangeStates(c: Candle1m, dir: Dir): ExchangeStates {
  if (c.domDepositOk !== undefined) {
    return { domDeposit: c.domDepositOk, domWithdraw: c.domWithdrawOk ?? null, fxDeposit: c.fxDepositOk ?? null, fxWithdraw: c.fxWithdrawOk ?? null }
  }
  return dir === 'kimp'
    ? { domDeposit: c.depositOk, domWithdraw: null, fxDeposit: null, fxWithdraw: c.withdrawOk }
    : { domDeposit: null, domWithdraw: c.withdrawOk, fxDeposit: c.depositOk, fxWithdraw: null }
}
