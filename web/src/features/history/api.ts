// /history/streaks 1회 조회 (스펙 005 §3.6). 폴링이 아니라 선택 심볼·필터가 바뀔 때만 다시 부른다.
// 한 조회가 서버에서 수 초 걸리므로(1초 기록 위 7일 ≈ 8초) 연속 변경은 400ms 디바운스로 마지막 것만 보내고,
// 진행 중 요청은 AbortController 로 취소한다 — 늦게 온 옛 응답이 새 결과를 덮지 않게.
import { useEffect, useState } from 'react'
import { API_BASE } from '../../shared/config'
import type { StreaksResponse } from './types'

const DEBOUNCE_MS = 400

export interface StreaksQuery {
  base: string
  dom: 'upbit' | 'bithumb'
  threshold: number
  /** 조회 기간(초). start = 지금 − periodSec, end = 지금 — 항상 둘 다 붙인다 (§3.6). */
  periodSec: number
}

/** 404 = 기간 내 기록 없음(빈 상태와 같다), 그 외 비 2xx 는 status 를 들고 오류로. */
export type StreaksResult =
  | { kind: 'ok'; data: StreaksResponse }
  | { kind: 'empty' }
  | { kind: 'error'; status: number }

export async function fetchStreaks(q: StreaksQuery, signal: AbortSignal): Promise<StreaksResult> {
  const nowSec = Math.floor(Date.now() / 1000)
  const params = new URLSearchParams({
    base: q.base,
    dom: q.dom,
    threshold: String(q.threshold),
    start: String(nowSec - q.periodSec),
    end: String(nowSec),
  })
  const res = await fetch(`${API_BASE}/history/streaks?${params}`, { signal })
  if (res.status === 404) return { kind: 'empty' }
  if (!res.ok) return { kind: 'error', status: res.status }
  return { kind: 'ok', data: (await res.json()) as StreaksResponse }
}

export interface StreaksState {
  /** 마지막으로 성공한 결과 — 조회 중에도 직전 결과를 유지한다 (§3.6). */
  result: StreaksResult | null
  loading: boolean
}

export function useStreaks(q: StreaksQuery): StreaksState {
  const [state, setState] = useState<StreaksState>({ result: null, loading: false })
  // 객체 q 를 통째 의존성에 넣으면 렌더마다 재조회되므로 원시값으로 푼다
  const { base, dom, threshold, periodSec } = q
  useEffect(() => {
    if (!base) return
    const ctl = new AbortController()
    setState((s) => ({ ...s, loading: true }))
    const timer = setTimeout(() => {
      fetchStreaks({ base, dom, threshold, periodSec }, ctl.signal)
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
  }, [base, dom, threshold, periodSec])
  return state
}
