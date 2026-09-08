// 계층 봉 → 상위 봉 접기 (순수 함수). 화면의 봉 8종은 서버 계층(1m·5m·1h·4h·1d) 하나를 골라 그 안에서 접는다 —
// 3m 은 1m 셋, 15m·30m 은 5m 셋·여섯(스펙 014 §3.7). 계층과 같은 봉이면 그대로 지나간다.
import { KST_OFFSET_SEC } from './candles'
import type { Candle1m } from './types'

export type Interval = '1m' | '3m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d'
export const INTERVALS: Interval[] = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']
export const INTERVAL_SEC: Record<Interval, number> = {
  '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14_400, '1d': 86_400,
}
export const INTERVAL_LABEL: Record<Interval, string> = {
  '1m': '1분', '3m': '3분', '5m': '5분', '15m': '15분', '30m': '30분', '1h': '1시간', '4h': '4시간', '1d': '1일',
}

/** KST 자정·정시에 맞춰 버킷을 자른다 — 서버 창 정렬(014 §3.4)과 같아야 1일봉이 서버 1d 와 같은 경계를 갖는다. */
function bucketStart(ts: number, step: number): number {
  return Math.floor((ts + KST_OFFSET_SEC) / step) * step - KST_OFFSET_SEC
}

/**
 * 오름차순 계층 봉(창 길이 base 초)을 step 초 버킷으로 접는다. 김프 % 는 시/고/저/종 규칙, 가격·환율·입출금 상태는 버킷 마지막 봉의 값,
 * blockedSec·samples 는 버킷 안 합계(blockedSec 상한 = step). 빈 버킷은 만들지 않는다(봉이 없으면 그 구간은 그냥 비어 있다).
 */
export function rollup(candles: Candle1m[], step: number, base = 60): Candle1m[] {
  if (step <= base) return candles
  const out: Candle1m[] = []
  let cur: Candle1m | null = null
  for (const c of candles) {
    const b = bucketStart(c.ts, step)
    if (cur && cur.ts === b) {
      cur.high = Math.max(cur.high, c.high)
      cur.low = Math.min(cur.low, c.low)
      cur.close = c.close
      cur.krw = c.krw; cur.usdt = c.usdt; cur.fxRate = c.fxRate
      cur.depositOk = c.depositOk; cur.withdrawOk = c.withdrawOk
      cur.domDepositOk = c.domDepositOk; cur.domWithdrawOk = c.domWithdrawOk; cur.fxDepositOk = c.fxDepositOk; cur.fxWithdrawOk = c.fxWithdrawOk
      cur.blockedSec = Math.min(step, cur.blockedSec + c.blockedSec)
      cur.samples += c.samples
    } else {
      cur = { ...c, ts: b }
      out.push(cur)
    }
  }
  return out
}
