// ── /history/events 조회 (스펙 013 §3.5) ──
// 방향·기간·거래소가 바뀔 때 1회 + 60초마다 같은 조건으로 재조회.
// 연속 변경은 400ms 디바운스로 마지막 것만 보내고 진행 중 요청은 AbortController 로 취소한다 —
// 늦게 온 옛 응답이 새 결과를 덮지 않게 (005 §3.6 방식). 심볼은 클라이언트에서 거르므로 쿼리에 없다.
import { useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE, HISTORY_CANDLES_POLL_MS, HISTORY_EVENTS_POLL_MS } from '../../shared/config'
import { RES_OF_INTERVAL, chunkKey, neededChunks, resLimitSec, type ChunkRef } from './candles'
import { INTERVAL_SEC, type Interval } from './rollup'
import type { Candle1m, CandlesResponse, Dir, Dom, EventsResponse } from './types'

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

// ── /history/candles (스펙 014 §3.7) ──
// 선택된 (국내 × 해외) 쌍마다 계층의 청크(= 요청당 상한 길이, KST 자정 정렬)를 부른다. 청크는 (쌍, 심볼, 방향, 계층, 청크 시작)
// 키로 메모리에 들고 있어 심볼·방향·쌍·봉을 바꿔도 없는 청크만 새로 부르고, 최신 청크만 60초마다 다시 부른다(창이 닫힐 때마다 새 봉).
// 청크가 상한 길이라 400 은 나올 수 없다. 요청은 위와 같은 400ms 디바운스·직전 요청 취소.

class HttpError extends Error {
  constructor(readonly status: number) { super(`HTTP ${status}`) }
}

export async function fetchCandles(ref: ChunkRef, signal: AbortSignal): Promise<CandlesResponse> {
  const params = new URLSearchParams({
    base: ref.base, res: ref.res, dom: ref.dom, fx: ref.fx, dir: ref.dir,
    start: String(ref.start), end: String(ref.start + resLimitSec(ref.res)),
  })
  const res = await fetch(`${API_BASE}/history/candles?${params}`, { signal })
  if (!res.ok) throw new HttpError(res.status)
  return (await res.json()) as CandlesResponse
}

export interface CandlesQuery {
  base: string
  dir: Dir
  doms: Dom[]
  fxs: string[]
  interval: Interval
  /** 사용자가 왼쪽으로 끌어 더 붙인 청크 수. */
  older: number
}

/** (국내, 해외) 쌍 하나의 봉 — 계층 그대로(접기 전), ts 오름차순. */
export interface PairCandles { dom: Dom; fx: string; candles: Candle1m[] }

export interface CandlesState {
  pairs: PairCandles[]
  /** 청크를 받는 중 — 직전 봉은 유지된다. */
  loading: boolean
  /** 마지막 회차의 실패 HTTP 상태(네트워크 실패는 0). 성공하면 null. */
  errorStatus: number | null
  /** 이 계층의 보관 기간까지 다 받았다 — 과거 로드를 멈춘다. */
  oldestReached: boolean
}

export function useCandles(q: CandlesQuery): CandlesState {
  const cache = useRef(new Map<string, Candle1m[]>())
  const [version, setVersion] = useState(0)
  const [loading, setLoading] = useState(false)
  const [errorStatus, setErrorStatus] = useState<number | null>(null)
  const [round, setRound] = useState(0)
  const seenRound = useRef(0)
  useEffect(() => {
    const id = setInterval(() => setRound((r) => r + 1), HISTORY_CANDLES_POLL_MS)
    return () => clearInterval(id)
  }, [])
  const { base, dir, interval, older } = q
  const res = RES_OF_INTERVAL[interval]
  const domsKey = q.doms.join('+'), fxsKey = q.fxs.join('+')
  useEffect(() => {
    // 60초 회차로 깨어난 경우만 최신 청크를 다시 부른다 — 봉 종류·쌍을 바꿀 때는 없는 청크만
    const refreshLatest = round !== seenRound.current
    seenRound.current = round
    const ctl = new AbortController()
    const timer = setTimeout(() => {
      const now = Math.floor(Date.now() / 1000)
      const { starts } = neededChunks(now, res, INTERVAL_SEC[interval], older)
      const latest = starts[starts.length - 1]
      const refs: ChunkRef[] = []
      for (const dom of domsKey.split('+') as Dom[]) {
        for (const fx of fxsKey.split('+')) {
          for (const start of starts) {
            const ref: ChunkRef = { dom, fx, base, dir, res, start }
            if ((refreshLatest && start === latest) || !cache.current.has(chunkKey(ref))) refs.push(ref)
          }
        }
      }
      setLoading(refs.length > 0) // 직전 회차가 취소돼 켜진 채 남은 표시도 여기서 끈다
      if (refs.length === 0) return
      Promise.all(refs.map((ref) => fetchCandles(ref, ctl.signal).then((body) => { cache.current.set(chunkKey(ref), body.candles) })))
        .then(() => setErrorStatus(null))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === 'AbortError') return
          setErrorStatus(err instanceof HttpError ? err.status : 0)
        })
        .finally(() => {
          if (ctl.signal.aborted) return // 취소는 정상 경로 — 새 요청이 상태를 이어받는다
          setLoading(false)
          setVersion((v) => v + 1)
        })
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      ctl.abort()
    }
  }, [base, dir, domsKey, fxsKey, res, interval, older, round])

  // 캐시에서 쌍별로 이어 붙인다 — version(청크 도착)과 선택이 바뀔 때만. 셸의 매초 리렌더에는 같은 참조를 돌려줘 차트가 다시 그리지 않는다
  const pairs = useMemo<PairCandles[]>(() => {
    const now = Math.floor(Date.now() / 1000)
    const { starts } = neededChunks(now, res, INTERVAL_SEC[interval], older)
    const out: PairCandles[] = []
    for (const dom of domsKey.split('+') as Dom[]) {
      for (const fx of fxsKey.split('+')) {
        const candles: Candle1m[] = []
        for (const start of starts) candles.push(...(cache.current.get(chunkKey({ dom, fx, base, dir, res, start })) ?? []))
        out.push({ dom, fx, candles })
      }
    }
    return out
  }, [version, base, dir, domsKey, fxsKey, res, interval, older])
  const oldestReached = useMemo(
    () => neededChunks(Math.floor(Date.now() / 1000), res, INTERVAL_SEC[interval], older).oldestReached,
    [res, interval, older],
  )
  return { pairs, loading, errorStatus, oldestReached }
}
