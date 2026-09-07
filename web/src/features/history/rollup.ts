// 1분봉 → 상위 봉 접기 (순수 함수). 화면에서 봉 종류를 바꿀 때 서버를 다시 부르지 않고 이미 받은 1분봉을 접는다 —
// 하루 창이면 1분봉 1,440개라 접는 비용이 ms 단위이고, 저장은 1분 해상도 하나만 두겠다는 결정(스펙 014 예정)과도 맞는다.
import type { Candle1m } from './types'

export type Interval = '1m' | '3m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d'
export const INTERVALS: Interval[] = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']
export const INTERVAL_SEC: Record<Interval, number> = {
  '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14_400, '1d': 86_400,
}
export const INTERVAL_LABEL: Record<Interval, string> = {
  '1m': '1분', '3m': '3분', '5m': '5분', '15m': '15분', '30m': '30분', '1h': '1시간', '4h': '4시간', '1d': '1일',
}

/** 로컬(KST) 자정·정시에 맞춰 버킷을 자른다 — 1일봉이 09:00 에 끊기지 않게. 분 단위 오프셋이 없는 시간대 가정. */
const TZ_OFF = -new Date().getTimezoneOffset() * 60
function bucketStart(ts: number, step: number): number {
  return Math.floor((ts + TZ_OFF) / step) * step - TZ_OFF
}

/**
 * 오름차순 1분봉을 step 초 버킷으로 접는다. 김프 % 는 시/고/저/종 규칙, 가격·환율·입출금 상태는 버킷 마지막 분의 값,
 * blockedSec 은 버킷 안 합계(상한 = step). 빈 버킷은 만들지 않는다(1분봉이 없으면 그 구간은 그냥 비어 있다).
 */
export function rollup(candles: Candle1m[], step: number): Candle1m[] {
  if (step <= 60) return candles
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
      cur.blockedSec = Math.min(step, cur.blockedSec + c.blockedSec)
    } else {
      cur = { ...c, ts: b }
      out.push(cur)
    }
  }
  return out
}
