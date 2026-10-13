// 공유 피드 — 셸이 만들어 모든 탭에 내려주는 객체 하나 (스펙 002 §3.4).
import { useEffect, useRef, useState } from 'react'
import { MOCK_TICK_MS, STALE_SEC } from './config'
import { buildMarkets, tickMarkets } from './mock'
import type { Feed } from './types'

export function createFeed(): Feed {
  const feed: Feed = {
    spreads: [], // 이 스펙에서는 항상 빈 배열 — 003(017 구독)이 replace 로 채운다
    rate: 0,
    markets: buildMarkets(),
    health: null, // 011 이 setHealth 로 채운다
    // 행을 복사하거나 파생 맵을 만들지 않는다 — 매초 불리는 자리라 받은 배열을 그대로 건다 (002 §3.4)
    replace(rows, rate) {
      feed.spreads = rows
      feed.rate = rate
    },
    setHealth(data) {
      feed.health = data
    },
  }
  return feed
}

/**
 * 1.5초 tick — 푸시가 멈추면 stale 로 드러나도록 모든 행의 age 를 키운다 (017 은 안 바뀐 행을 안 보낸다).
 * 이 tick 에서 stale 판정이 바뀐 행이 하나라도 있으면 true 를 돌려준다. 스프레드 표가 age 를 읽는 곳은 stale 판정뿐이라
 * false 인 tick 은 표의 글자·색을 하나도 바꾸지 못한다 — 셸이 그 tick 에 표를 다시 그리지 않아도 되는 근거다.
 * 판정식은 집계(coins.ts)가 쓰는 `age >= STALE_SEC` 를 더하기 앞뒤로 평가해 견준다. 같은 식이라 NaN·null 도 집계와 같게 다뤄지고,
 * '넘었나' 한 방향만 보지 않으므로 계약 밖 값(문자열 age 의 이어 붙이기 등)이 판정을 되돌려도 놓치지 않는다.
 */
export function tickFeed(feed: Feed): boolean {
  let changed = false
  for (const row of feed.spreads) {
    const was = row.age >= STALE_SEC
    row.age += 1.5
    if ((row.age >= STALE_SEC) !== was) changed = true
  }
  tickMarkets(feed.markets)
  return changed
}

/**
 * 셸의 심장 박동 — 피드 하나를 만들고 MOCK_TICK_MS 마다 tick + 리렌더.
 * - `staleTick` 은 tick 이 행의 stale 판정을 바꿨을 때만 오른다. 스프레드 표는 행 배열과 이 값이 그대로면 다시 그리지 않는다
 *   (delta 사이 tick 대부분은 서버 age 가 1초 안쪽이라 문턱에 닿지 않는다).
 * - 브라우저 탭이 숨은 동안에는 tick(age 증가)만 하고 다시 그리지 않는다. 숨은 화면은 아무도 보지 않는데, 크롬이 숨은 탭을
 *   낮은 우선순위로 돌려 같은 렌더에 CPU 를 더 쓴다. 다시 보이는 순간 시계를 맞춰 한 번 그리고, 숨은 동안 판정이 바뀐 행이
 *   있었으면 staleTick 도 그때 올린다. age 는 숨은 동안에도 키워야 다시 보인 직후의 stale 판정이 맞다.
 */
export function useFeed(): { feed: Feed; now: number; staleTick: number } {
  const ref = useRef<Feed | null>(null)
  ref.current ??= createFeed()
  const [now, setNow] = useState(() => Date.now())
  const [staleTick, setStaleTick] = useState(0)
  useEffect(() => {
    let staleWhileHidden = false // 숨은 동안 stale 판정이 바뀐 tick 이 있었나 — 다시 보일 때 표를 한 번 다시 그려야 하는가
    const bumpStale = () => setStaleTick((n) => n + 1)
    const id = setInterval(() => {
      const changed = ref.current !== null && tickFeed(ref.current)
      if (document.hidden) {
        if (changed) staleWhileHidden = true
        return
      }
      // 같은 콜백 안의 두 갱신은 한 번에 묶여 그려진다
      if (changed) bumpStale()
      setNow(Date.now())
    }, MOCK_TICK_MS)
    const onVisible = () => {
      if (document.hidden) return
      if (staleWhileHidden) {
        staleWhileHidden = false
        bumpStale()
      }
      setNow(Date.now())
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])
  return { feed: ref.current, now, staleTick }
}
