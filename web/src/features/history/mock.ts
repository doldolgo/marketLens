// 015 UI 시안용 mock — 서버가 수집하는 해외 거래소는 binance 뿐이라, Bybit·MEXC 카드는 binance 실봉을 변형해 그린다.
// 실 API 경로(청크·과거 로드·60초 갱신)는 그대로 타고, 이 파일은 받은 봉을 바꾸기만 한다. 수집 스펙이 생기면 이 파일과
// `FX_CHOICES` 의 mock 항목을 지운다. 변형은 시드 난수라 같은 (거래소, 시각) 은 언제 봐도 같은 값이다.
import type { Candle1m, Dir, PremiumEvent } from './types'

/** 거래소별 김프 기본 오프셋(%p) — Bybit 는 조금 높게, MEXC 는 조금 낮게 보이게. */
const BASE_OFFSET: Record<string, number> = { bybit: 0.12, mexc: -0.18 }
/** 사건 시각을 미는 초 — binance 사건을 복사해 살짝 어긋나게. */
const EVENT_SHIFT: Record<string, number> = { bybit: 300, mexc: -600 }

/** [0, 1) 시드 난수 — 문자열 해시(FNV-1a). */
function rand(key: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < key.length; i++) { h ^= key.charCodeAt(i); h = Math.imul(h, 0x01000193) }
  return ((h >>> 0) % 100_000) / 100_000
}

/** 30분 단위로 천천히 바뀌는 편차(±0.3%p) + 봉마다 작은 흔들림(±0.05%p). */
function kimpDelta(fx: string, ts: number): number {
  const slow = (rand(`${fx}:k:${Math.floor(ts / 1800)}`) - 0.5) * 0.6
  const fast = (rand(`${fx}:j:${ts}`) - 0.5) * 0.1
  return (BASE_OFFSET[fx] ?? 0) + slow + fast
}

/** 시간 단위로 막힘·모름 구간을 섞는다 — 출금은 15%, 입금은 8%, 모름은 3% 확률. */
function state(fx: string, kind: string, ts: number, blockedP: number): boolean | null {
  const r = rand(`${fx}:${kind}:${Math.floor(ts / 3600)}`)
  if (r < 0.03) return null
  return r >= 0.03 + blockedP
}

/**
 * binance 봉 → mock 해외 거래소 봉. 김프를 delta 만큼 옮기고 해외 USDT 가격은 그만큼 반대로 움직여 두 값이 어긋나지 않게 한다
 * (김프 ≈ 국내 USDT 환산 ÷ 해외 USDT − 1). 입출금은 4상태를 채운다 — 국내 쪽은 binance 봉의 경로 상태를 그대로,
 * 해외 쪽은 시드 난수. 경로 2상태(`depositOk`·`withdrawOk`·`blockedSec`)도 방향에 맞게 다시 채운다.
 */
export function mockCandles(fx: string, base: Candle1m[], dir: Dir, windowSec: number): Candle1m[] {
  return base.map((c) => {
    const d = kimpDelta(fx, c.ts)
    const fxDepositOk = state(fx, 'dep', c.ts, 0.08)
    const fxWithdrawOk = state(fx, 'wd', c.ts, 0.15)
    // 국내 쪽: binance 봉에서 경로에 든 쪽은 실값, 나머지 한쪽은 대체로 열림
    const domDepositOk = dir === 'kimp' ? c.depositOk : state(fx, 'ddep', c.ts, 0.02)
    const domWithdrawOk = dir === 'reverse' ? c.withdrawOk : state(fx, 'dwd', c.ts, 0.02)
    const depositOk = dir === 'kimp' ? domDepositOk : fxDepositOk
    const withdrawOk = dir === 'kimp' ? fxWithdrawOk : domWithdrawOk
    const blocked = depositOk === false || withdrawOk === false
    return {
      ...c,
      open: c.open + d, high: c.high + d, low: c.low + d, close: c.close + d,
      usdt: c.usdt * (1 - d / 100),
      depositOk, withdrawOk, blockedSec: blocked ? windowSec : 0,
      domDepositOk, domWithdrawOk, fxDepositOk, fxWithdrawOk,
    }
  })
}

/** binance 사건 → mock 거래소 사건: 시각만 밀고 fx 를 바꾼다. 진행 중 사건은 진행 중 그대로. */
export function mockEvents(fx: string, base: PremiumEvent[]): PremiumEvent[] {
  const shift = EVENT_SHIFT[fx] ?? 0
  return base.map((e) => ({ ...e, fx, startTs: e.startTs + shift, endTs: e.endTs == null ? null : e.endTs + shift, maxTs: e.maxTs + shift }))
}
