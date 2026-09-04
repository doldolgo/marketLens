// /spreads 1초 폴링 (스펙 003 §3.4). 거래소 id→표시명 변환은 shared/format 의 exName (011 이 같이 쓴다).
// 폴링은 이 기능 폴더 안에 살고, 셸이 공유 피드를 만든 직후 시작한다.
import { useEffect, useReducer, useRef } from 'react'
import { API_BASE, SPREAD_POLL_MS } from '../../shared/config'
import { exName } from '../../shared/format'
import type { Feed, SpreadRow } from '../../shared/types'
import type { SpreadsResponse } from './types'

/** 체결 규모(USD) 선택지 — 첫 값이 기본이고 서버 기본값과 같다 (§3.5). */
export const NOTIONALS = [10000, 50000, 100000, 500000] as const

async function fetchSpreads(notional: number): Promise<SpreadsResponse> {
  const res = await fetch(`${API_BASE}/spreads?notional=${notional}`)
  if (!res.ok) throw new Error(`GET /spreads → ${res.status}`)
  return (await res.json()) as SpreadsResponse
}

/** 성공 시 표시명으로 바꿔 공유 피드에 통째 교체, 실패(네트워크·비 2xx)는 무시하고 직전 데이터 유지. */
export function useSpreadPolling(feed: Feed, notional: number): void {
  const [, bump] = useReducer((n: number) => n + 1, 0)
  // 규모는 ref 로 들고 간다 — 의존성에 넣으면 주기가 재시작해 즉시 재요청이 나가고,
  // 그 응답이 진행 중인 폴링과 겹쳐 순서가 뒤집힌다. 바뀐 값은 다음 폴링부터 나간다 (§3.4).
  const sizeRef = useRef(notional)
  sizeRef.current = notional
  useEffect(() => {
    let alive = true
    let busy = false // 응답이 폴링 주기보다 늦을 때 요청이 쌓이지 않게
    async function poll(): Promise<void> {
      if (busy) return
      busy = true
      try {
        const res = await fetchSpreads(sizeRef.current)
        if (!alive) return
        const rows: SpreadRow[] = res.rows.map((r) => ({ ...r, dom: exName(r.dom), fx: exName(r.fx) }))
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
