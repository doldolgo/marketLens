// /history/events 조회 (스펙 013 §3.5). 방향·기간·거래소가 바뀔 때 1회 + 60초마다 같은 조건으로 재조회.
// 연속 변경은 400ms 디바운스로 마지막 것만 보내고 진행 중 요청은 AbortController 로 취소한다 —
// 늦게 온 옛 응답이 새 결과를 덮지 않게 (005 §3.6 방식). 심볼은 클라이언트에서 거르므로 쿼리에 없다.
import { useEffect, useState } from 'react'
import { API_BASE, HISTORY_EVENTS_POLL_MS } from '../../shared/config'
import type { Dir, Dom, EventsResponse } from './types'

const DEBOUNCE_MS = 400

export interface EventsQuery {
  dir: Dir
  /** null = 전체(두 국내 거래소 다) → dom 생략. */
  dom: Dom | null
  /** 조회 기간(초). start = 지금 − periodSec, end = 지금 — 항상 둘 다 붙인다 (§3.5). */
  periodSec: number
}

/** 빈 events 는 정상 응답(200)이라 별도 상태가 없다. 비 2xx 는 status 를 들고 오류로. */
export type EventsResult =
  | { kind: 'ok'; data: EventsResponse }
  | { kind: 'error'; status: number }

export async function fetchEvents(q: EventsQuery, signal: AbortSignal): Promise<EventsResult> {
  const nowSec = Math.floor(Date.now() / 1000)
  const params = new URLSearchParams({
    start: String(nowSec - q.periodSec),
    end: String(nowSec),
    dir: q.dir,
  })
  if (q.dom) params.set('dom', q.dom)
  const res = await fetch(`${API_BASE}/history/events?${params}`, { signal })
  if (!res.ok) return { kind: 'error', status: res.status }
  return { kind: 'ok', data: (await res.json()) as EventsResponse }
}

export interface EventsState {
  /** 마지막으로 끝난 결과 — 조회 중에도 직전 결과를 유지한다 (§3.5). */
  result: EventsResult | null
  loading: boolean
}

export function useEvents(q: EventsQuery): EventsState {
  const [state, setState] = useState<EventsState>({ result: null, loading: false })
  // 60초 재조회 — 카운터를 올려 아래 효과를 다시 돌린다. 탭이 숨겨져도 계속 돈다(셸은 탭을 내리지 않는다, §3.5)
  const [round, setRound] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setRound((r) => r + 1), HISTORY_EVENTS_POLL_MS)
    return () => clearInterval(id)
  }, [])
  // 객체 q 를 통째 의존성에 넣으면 렌더마다 재조회되므로 원시값으로 푼다
  const { dir, dom, periodSec } = q
  useEffect(() => {
    const ctl = new AbortController()
    setState((s) => ({ ...s, loading: true }))
    const timer = setTimeout(() => {
      fetchEvents({ dir, dom, periodSec }, ctl.signal)
        .then((result) => setState({ result, loading: false }))
        .catch((err: unknown) => {
          // 취소는 정상 경로 — 새 요청이 상태를 이어받는다
          if (err instanceof DOMException && err.name === 'AbortError') return
          setState({ result: { kind: 'error', status: 0 }, loading: false })
        })
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      ctl.abort()
    }
  }, [dir, dom, periodSec, round])
  return state
}
