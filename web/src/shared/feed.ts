// 공유 피드 — 셸이 만들어 모든 탭에 내려주는 객체 하나 (스펙 002 §3.4).
import { useEffect, useRef, useState } from 'react'
import { MOCK_TICK_MS } from './config'
import { buildFlow, buildMarkets, makeEvents, tickMarkets } from './mock'
import type { Feed, IoEntry, SpreadRow } from './types'

/** 교체 시 io 재구성 — 행마다 국내·해외 각각 한 항목, net 은 netDom ?? '–'. */
function buildIo(rows: SpreadRow[]): Record<string, IoEntry> {
  const io: Record<string, IoEntry> = {}
  for (const row of rows) {
    const net = row.netDom ?? '–'
    io[`${row.sym}|${row.dom}`] = { dep: row.depDom, wd: row.wdDom, net }
    io[`${row.sym}|${row.fx}`] = { dep: row.depFx, wd: row.wdFx, net }
  }
  return io
}

export function createFeed(): Feed {
  const flow = buildFlow()
  const feed: Feed = {
    spreads: [], // 이 스펙에서는 항상 빈 배열 — 003 이 replace 로 채운다
    rate: 0,
    io: {},
    markets: buildMarkets(),
    health: null, // 011 이 setHealth 로 채운다
    flowAddrs: flow.addrs,
    flowRows: flow.rows,
    replace(rows, rate) {
      feed.spreads = rows
      feed.rate = rate
      feed.io = buildIo(rows)
    },
    setHealth(data) {
      feed.health = data
    },
    events: makeEvents,
  }
  return feed
}

/** 1.5초 tick — 폴링이 멈추면 stale 로 드러나도록 모든 행의 age 를 키운다. */
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
