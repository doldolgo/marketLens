// 공유 피드 — 셸이 만들어 모든 탭에 내려주는 객체 하나 (스펙 002 §3.4).
import { useEffect, useRef, useState } from 'react'
import { MOCK_TICK_MS } from './config'
import { buildFlow, buildMarkets, tickMarkets } from './mock'
import type { Feed } from './types'

export function createFeed(): Feed {
  const flow = buildFlow()
  const feed: Feed = {
    spreads: [], // 이 스펙에서는 항상 빈 배열 — 003(017 구독)이 replace 로 채운다
    rate: 0,
    markets: buildMarkets(),
    health: null, // 011 이 setHealth 로 채운다
    flowAddrs: flow.addrs,
    flowRows: flow.rows,
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

/** 1.5초 tick — 푸시가 멈추면 stale 로 드러나도록 모든 행의 age 를 키운다 (017 은 안 바뀐 행을 안 보낸다). */
export function tickFeed(feed: Feed): void {
  for (const row of feed.spreads) row.age += 1.5
  for (const row of feed.flowRows) row.age += 1.5
  tickMarkets(feed.markets)
}

/** 셸의 심장 박동 — 피드 하나를 만들고 MOCK_TICK_MS 마다 tick + 리렌더. */
export function useFeed(): { feed: Feed; now: number } {
  const ref = useRef<Feed | null>(null)
  ref.current ??= createFeed()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => {
      if (ref.current) tickFeed(ref.current)
      setNow(Date.now())
    }, MOCK_TICK_MS)
    return () => clearInterval(id)
  }, [])
  return { feed: ref.current, now }
}
