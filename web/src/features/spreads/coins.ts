// 스프레드 탭 코인 집계·정렬 (스펙 003 §3.5) — 순수 함수. React·DOM 이 없어 node 로 바로 돌려 볼 수 있다.
// 렌더마다(매초 delta + 1.5초 tick) 1,500행 안팎을 코인 400개로 묶으므로, 중간 배열 없이 한 번 훑고 정렬 키는 코인마다 한 번만 뽑는다.
import { STALE_SEC } from '../../shared/config'
import type { IoState, SpreadRow } from '../../shared/types'

export type View = 'kimp' | 'rev'
export type DomFilter = 'all' | '업비트' | '빗썸'
export type SortCol = 'sym' | 'chg' | 'price' | 'usd' | 'fxEx' | 'domEx' | 'val' | 'io' | 'net'

/** 코인 1개의 집계 행. */
export interface CoinRow {
  sym: string
  allFail: boolean
  allStale: boolean
  /** 보기 기준(김프/역프) 최대 행의 값. 전부 fail 이면 null. */
  val: number | null
  /** 최대 행의 해외·국내 거래소(표시명) — 열 2개로 보인다. */
  fxEx: string | null
  domEx: string | null
  /** 최대 행의 KST 00시 대비 국내 변동 %. 기준가 없으면 null (`–`). */
  chg: number | null
  /** 최대 행의 해외 마지막 체결가(USDT). fail 이거나 0 이면 null. */
  usd: number | null
  /** 출금(출발 거래소)·입금(도착 거래소) 상태. */
  wd: IoState
  dep: IoState
  net: string
  /** 맞춘 해외 망 이름 — null 이면 모름 또는 다름. "네트워크 같음" 필터는 이 값이 있는 행만 통과. */
  netFx: string | null
  /** 네트워크 다름 — 국내 망은 있는데 못 맞췄고 해외 입출금이 둘 다 false(006 absent). 모름(null)과 구분한다. */
  netDiff: boolean
  /** 국내가 KRW — 보기 기준(김프/역프) 최대 행의 `krw` 그대로. fail 이거나 0 이면 null. */
  price: number | null
  /** 그 방향에서 서버가 차감한 폭(%p, 양수). 소수 2자리로 반올림해 0 이면 배지를 숨긴다(`slipText`). */
  slip: number
}

/** 코인별 누산기 — 행을 한 번 지나가며 live 수·김프/역프 최대 행·stale 여부를 바로 모은다. */
interface Acc {
  sym: string
  live: number
  fwdBest: SpreadRow | null
  revBest: SpreadRow | null
  allStale: boolean
}

export function aggregateCoins(
  spreads: SpreadRow[], domFilter: DomFilter, fxOff: Record<string, boolean>, view: View,
): CoinRow[] {
  // 응답의 fwd·rev 가 이미 순값이라 FE 는 슬리피지를 계산하지 않는다 — 그 값이 그대로
  // 최대 행 선택·강조·임계 필터·정렬·표시에 쓰인다 (§3.5).
  const byCoin = new Map<string, Acc>()
  const order: Acc[] = [] // 코인이 처음 나온 순서 — 정렬 전 순서도 그대로 둔다
  for (const r of spreads) {
    if (domFilter !== 'all' && r.dom !== domFilter) continue
    if (fxOff[r.fx]) continue
    let a = byCoin.get(r.sym)
    if (!a) {
      a = { sym: r.sym, live: 0, fwdBest: null, revBest: null, allStale: true }
      byCoin.set(r.sym, a)
      order.push(a)
    }
    if (r.status === 'fail') continue
    a.live++
    if (!a.fwdBest || r.fwd > a.fwdBest.fwd) a.fwdBest = r
    if (!a.revBest || r.rev > a.revBest.rev) a.revBest = r
    if (!(r.age >= STALE_SEC)) a.allStale = false // every(age ≥ 5) 의 부정형 — NaN 도 같은 결과
  }
  const kimp = view === 'kimp'
  // 한 줄의 값은 전부 보기 기준 최대 행 하나에서 온다 — 역프 보기에서 국내가만 김프 최대 행에서 가져오면 국내거래소 태그와 다른 거래소의 가격이 보인다 (003 §3.5)
  return order.map((a) => {
    const best = kimp ? a.fwdBest : a.revBest
    return {
      sym: a.sym,
      allFail: a.live === 0,
      allStale: a.live > 0 && a.allStale,
      val: best ? (kimp ? best.fwd : best.rev) : null,
      fxEx: best ? best.fx : null,
      domEx: best ? best.dom : null,
      chg: best ? best.dayChg : null,
      usd: best && best.usd !== null && best.usd > 0 ? best.usd : null,
      // 김프 = 해외 → 국내, 역프 = 국내 → 해외. 출금 거래소는 출발, 입금 거래소는 도착.
      wd: best ? (kimp ? best.wdFx : best.wdDom) : null,
      dep: best ? (kimp ? best.depDom : best.depFx) : null,
      net: best ? (best.netDom ?? '–') : '–',
      netFx: best ? best.netFx : null,
      // null 은 다름이 아니다 — 해외 입출금이 둘 다 false 로 못 박힌 경우만 다름 (003 §3.2)
      netDiff: best !== null && best.netDom !== null && best.netFx === null && best.depFx === false && best.wdFx === false,
      // 서버가 그 행 국내 거래소의 최우선 매수호가를 그대로 준다 — 환산도 보정도 하지 않는다
      price: best && best.krw > 0 ? best.krw : null,
      slip: best ? (kimp ? best.slipFwd : best.slipRev) : 0,
    }
  })
}

/** 슬 배지 글자 `슬 −N.NN%p` — 소수 2자리로 반올림한 값이 0 이면 빈 문자열(배지 숨김, 열 폭은 그대로). 0 < slip < 0.005 에서 `슬 −0.00%p` 를 내지 않기 위해서다 (003 §3.5). */
export function slipText(slip: number): string {
  const t = slip.toFixed(2)
  return slip > 0 && Number(t) !== 0 ? `슬 −${t}%p` : ''
}

/** 입출금 정렬 순위: 가능(둘 다 true) > 모름 > 중단(하나라도 false). */
function ioRank(wd: IoState, dep: IoState): number {
  if (wd === false || dep === false) return 0
  if (wd === true && dep === true) return 2
  return 1
}

// 인자 없는 localeCompare 와 같은 규칙(기본 로캘·기본 옵션 Collator)이다. 비교마다 Collator 를 새로 만들지 않게 모듈에서 한 번만 만든다.
const compareText = new Intl.Collator().compare

function sortKey(c: CoinRow, col: SortCol): number | string | null {
  return col === 'val' ? c.val
    : col === 'chg' ? c.chg
    : col === 'price' ? c.price
    : col === 'usd' ? c.usd
    : col === 'fxEx' ? c.fxEx
    : col === 'domEx' ? c.domEx
    : col === 'io' ? ioRank(c.wd, c.dep)
    : col === 'net' ? (c.net === '–' ? null : c.net)
    : c.sym
}

/**
 * 정렬: 전부 fail 인 코인은 항상 맨 뒤, null 값은 뒤, 같으면 심볼 순 (§3.5). 키는 코인마다 한 번만 뽑아 두고 index 를 정렬한다 —
 * 비교 함수 안에서 키를 다시 뽑으면 비교 횟수(코인 400개면 약 3,500번)만큼 반복된다. Array.sort 는 안정 정렬이라 결과 순서가 같다.
 */
export function sortCoins(coins: CoinRow[], col: SortCol, asc: boolean): CoinRow[] {
  const dir = asc ? 1 : -1
  const keys = coins.map((c) => sortKey(c, col))
  const idx = coins.map((_, i) => i)
  idx.sort((i, j) => {
    const a = coins[i], b = coins[j]
    if (a.allFail !== b.allFail) return a.allFail ? 1 : -1
    const ka = keys[i], kb = keys[j]
    if (ka === null && kb === null) return compareText(a.sym, b.sym)
    if (ka === null) return 1
    if (kb === null) return -1
    const d = typeof ka === 'string' ? compareText(ka, kb as string) : ka - (kb as number)
    return d * dir || compareText(a.sym, b.sym)
  })
  return idx.map((i) => coins[i])
}
