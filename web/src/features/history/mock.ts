// 1분봉 mock — 스펙 014 서버 계약이 생기기 전 화면 시안용. 서버가 붙으면 이 파일은 지운다.
// 같은 (dom, base, dir, 창 시작) 은 항상 같은 모양이어야 리로드해도 시안 비교가 되므로 시드 난수를 쓴다 (002 §3.4 방식).
import { rng, uniform } from '../../shared/rand'
import type { Candle1m, Dir, Dom } from './types'

/** 차트에서 고를 수 있는 해외 거래소 id·표시명 — 서버는 아직 binance 만 있어 나머지는 mock 시안용. */
export const FX_CHOICES: { id: string; label: string }[] = [
  { id: 'binance', label: 'Binance' }, { id: 'bybit', label: 'Bybit' }, { id: 'bitget', label: 'Bitget' },
  { id: 'mexc', label: 'MEXC' }, { id: 'gateio', label: 'Gate.io' }, { id: 'hyperliquid', label: 'Hyperliquid' },
]

/** 심볼 문자열 → 그럴듯한 USDT 기준가. BTC·ETH 는 실제 근처, 나머지는 해시로 0.05~50 사이. */
function basePriceUsdt(sym: string): number {
  if (sym === 'BTC') return 112_000
  if (sym === 'ETH') return 4_300
  if (sym === 'SOL') return 210
  if (sym === 'XRP') return 2.9
  const r = rng(`price:${sym}`)
  return Math.exp(uniform(r, Math.log(0.05), Math.log(50)))
}

/**
 * [startSec, startSec + count분) 의 1분봉.
 * 김프 % 는 평상시 0.3~0.8 근처를 떠돌고 하루 2~3번 1.5%p 안팎 솟구치는 구간(= 사건)이 있다.
 * 입출금은 대부분 열림, 하루 1~2번 5~40분 막힘.
 */
export function makeMockCandles(dom: Dom, fx: string, base: string, dir: Dir, startSec: number, count = 1440): Candle1m[] {
  const r = rng(`candle:${dom}:${fx}:${base}:${dir}:${startSec}`)
  const level = dir === 'kimp' ? uniform(r, 0.3, 0.8) : uniform(r, 0.2, 0.6)

  // 솟구침 구간: 시작 분·길이(20~90분)·높이(+1.0~2.0)
  const bursts = Array.from({ length: 2 + Math.floor(r() * 2) }, () => ({
    at: Math.floor(uniform(r, 0, count - 100)),
    len: Math.floor(uniform(r, 20, 90)),
    amp: uniform(r, 1.0, 2.0),
  }))
  // 입출금 막힘 구간: 시작 분·길이(5~40분)·입금/출금
  const blocks = Array.from({ length: 1 + Math.floor(r() * 2) }, () => ({
    at: Math.floor(uniform(r, 0, count - 50)),
    len: Math.floor(uniform(r, 5, 40)),
    kind: r() < 0.5 ? 'deposit' : 'withdraw',
  }))

  const p0 = basePriceUsdt(base)
  let usdt = p0
  let fxRate = uniform(r, 1_370, 1_400)
  let prem = level
  const out: Candle1m[] = []
  for (let i = 0; i < count; i++) {
    // 이 분의 목표 수준 = 기본 + 솟구침 (구간 안이면 종 모양으로 올라갔다 내려옴)
    let target = level
    for (const b of bursts) {
      const t = (i - b.at) / b.len
      if (t >= 0 && t < 1) target += b.amp * Math.sin(Math.PI * t)
    }
    // 60초 랜덤워크 + 평균회귀 → OHLC
    const open = prem
    let high = open, low = open, cur = open
    for (let s = 0; s < 60; s++) {
      cur += (target - cur) * 0.08 + uniform(r, -0.025, 0.025)
      if (cur > high) high = cur
      if (cur < low) low = cur
    }
    prem = cur
    // 가격: USDT 가 0.05%/분 랜덤워크, 환율은 아주 느리게, 원화가는 김프를 반영해 역산
    usdt *= 1 + uniform(r, -0.0005, 0.0005)
    fxRate += uniform(r, -0.15, 0.15)
    const signed = dir === 'kimp' ? prem : -prem
    const krw = usdt * fxRate * (1 + signed / 100)

    let depositOk = true, withdrawOk = true, blockedSec = 0
    for (const b of blocks) {
      if (i >= b.at && i < b.at + b.len) {
        if (b.kind === 'deposit') depositOk = false
        else withdrawOk = false
        blockedSec = 60
      }
    }
    // 막힘 구간 첫 분·마지막 분은 부분 막힘 (분 경계에 딱 맞춰 막히지 않으니까)
    for (const b of blocks) {
      if (i === b.at) blockedSec = Math.floor(uniform(r, 10, 59))
      if (i === b.at + b.len) { blockedSec = Math.floor(uniform(r, 1, 50)) }
    }

    out.push({
      ts: startSec + i * 60,
      open: round4(open), high: round4(high), low: round4(low), close: round4(prem),
      krw, usdt, fxRate, depositOk, withdrawOk, blockedSec,
    })
  }
  return out
}

function round4(v: number): number {
  return Math.round(v * 10_000) / 10_000
}
