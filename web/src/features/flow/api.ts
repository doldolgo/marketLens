// ── /flow/netflow · /flow/recent 조회 (스펙 050 §3.6·§3.7·§3.8) ──
// 입출금 레이더 탭이 보이는 동안(탭 활성 && 브라우저 탭 보임) 두 경로를 FLOW_POLL_MS 마다 다시 부른다 — 013 과 같이 다른 탭·가려진
// 브라우저 탭에서는 멈춘다. 셸은 탭을 숨길 뿐 내리지 않으므로, 멈추지 않으면 스프레드 탭만 보는 접속자도 5초마다 두 경로를 부른다.
// 실패하면 직전 표를 유지하고 HTTP 상태만 들고 온다(바 1 우측 문구용). 진행 중 요청은 조건이 바뀌거나 탭이 숨으면 AbortController 로 취소한다.
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { API_BASE, FLOW_POLL_MS } from '../../shared/config'
import type { FlowDir, FlowWindow, NetflowResponse, RecentResponse } from './types'

/** 최근 전송 표 행 수 — §3.8 "100행". 서버 기본값과 같지만 화면이 기대는 수라 쿼리에 명시한다. */
const RECENT_LIMIT = 100

/** 비 2xx 는 status 를 들고 오류로. 네트워크 실패는 호출처가 status 0 으로 둔다(013 과 같은 표기). */
export type FlowResult<T> =
  | { kind: 'ok'; data: T }
  | { kind: 'error'; status: number }

export async function fetchNetflow(window: FlowWindow, signal: AbortSignal): Promise<FlowResult<NetflowResponse>> {
  const params = new URLSearchParams({ window })
  const res = await fetch(`${API_BASE}/flow/netflow?${params}`, { signal })
  if (!res.ok) return { kind: 'error', status: res.status }
  return { kind: 'ok', data: (await res.json()) as NetflowResponse }
}

/** `symbol` 은 빈 문자열이면 전체(쿼리에 싣지 않는다 — 서버는 대문자 심볼만 받는다, §3.7). */
export async function fetchRecent(dir: FlowDir, symbol: string, signal: AbortSignal): Promise<FlowResult<RecentResponse>> {
  const params = new URLSearchParams({ limit: String(RECENT_LIMIT), dir })
  if (symbol) params.set('symbol', symbol)
  const res = await fetch(`${API_BASE}/flow/recent?${params}`, { signal })
  if (!res.ok) return { kind: 'error', status: res.status }
  return { kind: 'ok', data: (await res.json()) as RecentResponse }
}

export interface FlowState<T> {
  /** 마지막으로 성공한 응답 — 실패해도 직전 것을 유지한다 (§3.8). 첫 응답 전 null. */
  data: T | null
  /** 마지막 회차의 실패 HTTP 상태(네트워크 실패는 0). 성공하면 null. */
  errorStatus: number | null
}

function subscribeVisibility(onChange: () => void): () => void {
  document.addEventListener('visibilitychange', onChange)
  return () => document.removeEventListener('visibilitychange', onChange)
}
const pageVisible = () => document.visibilityState === 'visible'

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

function applyResult<T>(prev: FlowState<T>, result: FlowResult<T>): FlowState<T> {
  if (result.kind === 'ok') return { data: result.data, errorStatus: null }
  return { data: prev.data, errorStatus: result.status }
}

/**
 * 켜져 있는 동안(`active` && 페이지 보임) `run` 을 곧바로 1회, 그 뒤 FLOW_POLL_MS 마다 다시 부른다. `run` 이 바뀌면(창·방향·코인) 그 자리에서
 * 다시 부르고 주기를 새로 센다. 꺼지면 진행 중 요청을 취소하고 멈춘다(받은 결과는 그대로) — 다시 켜지면 곧바로 1회 부른다.
 * 다음 회차는 요청을 낸 시점부터 세므로 응답이 주기보다 늦어도 요청이 쌓이지 않는다(늦은 요청은 취소된다).
 */
function usePolled<T>(active: boolean, run: (signal: AbortSignal) => Promise<FlowResult<T>>): FlowState<T> {
  const visible = useSyncExternalStore(subscribeVisibility, pageVisible)
  const on = active && visible
  const [state, setState] = useState<FlowState<T>>({ data: null, errorStatus: null })
  // 회차 카운터 — 오르면 아래 효과가 다시 돌아 한 번 더 부른다
  const [round, setRound] = useState(0)
  useEffect(() => {
    if (!on) return
    const ctl = new AbortController()
    run(ctl.signal)
      .then((result) => setState((prev) => applyResult(prev, result)))
      .catch((err: unknown) => {
        // 취소는 정상 경로 — 새 요청이 상태를 이어받는다
        if (isAbort(err)) return
        setState((prev) => ({ data: prev.data, errorStatus: 0 }))
      })
    const timer = setTimeout(() => setRound((r) => r + 1), FLOW_POLL_MS)
    return () => {
      clearTimeout(timer)
      ctl.abort()
    }
  }, [on, run, round])
  return state
}

/** `active` = 입출금 레이더 탭이 보이는 중. */
export function useNetflow(window: FlowWindow, active: boolean): FlowState<NetflowResponse> {
  const run = useCallback((signal: AbortSignal) => fetchNetflow(window, signal), [window])
  return usePolled(active, run)
}

/** `symbol` 은 적용된 검색값(대문자) — 빈 문자열이면 전체. */
export function useRecent(dir: FlowDir, symbol: string, active: boolean): FlowState<RecentResponse> {
  const run = useCallback((signal: AbortSignal) => fetchRecent(dir, symbol, signal), [dir, symbol])
  return usePolled(active, run)
}
