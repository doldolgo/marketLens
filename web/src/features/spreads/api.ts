// /spreads 1초 폴링 (스펙 003 §3.4). 거래소 id→표시명 변환은 shared/format 의 exName (011 이 같이 쓴다).
// 폴링은 이 기능 폴더 안에 살고, 셸이 공유 피드를 만든 직후 시작한다.
import { useEffect, useReducer } from 'react'
import { API_BASE, SPREAD_POLL_MS } from '../../shared/config'
import { exName } from '../../shared/format'
import type { Feed } from '../../shared/types'
import type { ApiSpreadRow, SpreadsResponse } from './types'

async function fetchSpreads(): Promise<SpreadsResponse> {
  const res = await fetch(`${API_BASE}/spreads`)
  if (!res.ok) throw new Error(`GET /spreads → ${res.status}`)
  return (await res.json()) as SpreadsResponse
}

/** 성공 시 표시명으로 바꿔 공유 피드에 통째 교체, 실패(네트워크·비 2xx)는 무시하고 직전 데이터 유지. */
export function useSpreadPolling(feed: Feed): void {
  const [, bump] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    let alive = true
    let busy = false // 응답이 폴링 주기보다 늦을 때 요청이 쌓이지 않게
    async function poll(): Promise<void> {
      if (busy) return
      busy = true
      try {
        const res = await fetchSpreads()
        if (!alive) return
        const rows: ApiSpreadRow[] = res.rows.map((r) => ({ ...r, dom: exName(r.dom), fx: exName(r.fx) }))
        feed.replace(rows, res.rate)
        bump()
      } catch {
        // 직전 데이터 유지 — age 는 셸의 mock tick 이 키워 저절로 stale 이 된다
      } finally {
        busy = false
      }
    }
    void poll()
    const id = setInterval(() => void poll(), SPREAD_POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [feed])
}
