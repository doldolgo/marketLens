// ── /history/events 조회 (스펙 013 §3.5) ──
// 방향·기간·거래소가 바뀔 때 1회 + 기록 탭이 보이는 동안 60초마다 같은 조건으로 재조회.
// 연속 변경은 400ms 디바운스로 마지막 것만 보내고 진행 중 요청은 AbortController 로 취소한다 —
// 늦게 온 옛 응답이 새 결과를 덮지 않게 (005 §3.6 방식). 심볼은 클라이언트에서 거르므로 쿼리에 없다.
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { API_BASE, HISTORY_CANDLES_POLL_MS, HISTORY_EVENTS_POLL_MS } from '../../shared/config'
import { RES_OF_INTERVAL, chunkKey, chunkSec, evictChunks, neededChunks, sameChunk, touchChunk, type ChunkRef } from './candles'
import { INTERVAL_SEC, type Interval } from './rollup'
import type { Candle1m, CandlesResponse, Dir, Dom, EventsResponse } from './types'

const DEBOUNCE_MS = 400

// ── 재조회 회차 — 기록 탭이 활성이고 브라우저 탭이 보일 때만 돈다 (013 §3.5·014 §3.7) ──
// 셸은 탭을 숨길 뿐 내리지 않으므로, 멈추지 않으면 스프레드 탭만 보는 접속자도 60초마다 7일 사건 전체와 봉 청크를 부른다.

function subscribeVisibility(onChange: () => void): () => void {
  document.addEventListener('visibilitychange', onChange)
  return () => document.removeEventListener('visibilitychange', onChange)
}
const pageVisible = () => document.visibilityState === 'visible'

/**
 * 재조회 회차 카운터. `on`(탭 활성 && 페이지 보임)인 동안 마지막 조회로부터 periodMs 마다 1씩 오른다 — 조회 효과는 이 값을 의존성에 넣는다.
 * 꺼지면 멈추고(받은 결과는 그대로), 다시 켜질 때 마지막 조회가 periodMs 보다 오래됐으면 곧바로 1회, 아니면 남은 시간 뒤에 1회.
 * `markFetched` 는 조회 효과가 요청을 낼 때 부른다 — "마지막 조회" 의 기준.
 */
function usePollRound(active: boolean, periodMs: number): { round: number; markFetched: () => void } {
  const visible = useSyncExternalStore(subscribeVisibility, pageVisible)
  const on = active && visible
  const [round, setRound] = useState(0)
  // null = 아직 조회 전 — 첫 조회는 조건 효과가 같은 커밋에서 낸다
  const lastRef = useRef<number | null>(null)
  useEffect(() => {
    if (!on) return
    const bump = () => setRound((r) => r + 1)
    const since = lastRef.current === null ? 0 : Date.now() - lastRef.current
    let id: ReturnType<typeof setInterval> | undefined
    const first = setTimeout(() => {
      bump()
      id = setInterval(bump, periodMs)
    }, Math.max(0, periodMs - since))
    return () => {
      clearTimeout(first)
      if (id !== undefined) clearInterval(id)
    }
  }, [on, periodMs])
  const markFetched = useCallback(() => { lastRef.current = Date.now() }, [])
  return { round, markFetched }
}

export interface EventsQuery {
  dir: Dir
  /** null = 전체(두 국내 거래소 다) → dom 생략. */
  dom: Dom | null
  /** 조회 기간(초). end = 지금을 60초 경계로 올린 값, start = end − periodSec — 항상 둘 다 붙인다 (§3.5, eventsWindow). */
  periodSec: number
  /** 있으면 periodSec 대신 이 절대 시각(epoch 초)이 start — 차트 음영용. 청크 경계에 맞춘 값이라 매초 바뀌지 않아 재조회가 돌지 않는다. */
  startSec?: number
  /** 심볼 하나만(서버 `base`) — 차트 음영용. 표는 전 코인을 받아 클라이언트에서 거른다. */
  base?: string
}

/** 빈 events 는 정상 응답(200)이라 별도 상태가 없다. 비 2xx 는 status 를 들고 오류로. */
export type EventsResult =
  | { kind: 'ok'; data: EventsResponse }
  | { kind: 'error'; status: number }

/**
 * 조회 창 끝 = 지금을 60초 경계로 올린 값, 시작 = 끝 − 기간(차트 음영은 청크 경계 startSec) — 같은 분에 같은 조건을 보는 접속자는
 * 같은 URL 을 부르게 되어 서버의 60초 공유 캐시가 맞는다(013 §3.4). 끝을 올리므로 지금까지의 사건은 빠지지 않는다.
 */
export function eventsWindow(q: Pick<EventsQuery, 'periodSec' | 'startSec'>, nowMs: number): { start: number; end: number } {
  const end = Math.ceil(nowMs / 60_000) * 60
  return { start: q.startSec ?? end - q.periodSec, end }
}

export async function fetchEvents(q: EventsQuery, signal: AbortSignal): Promise<EventsResult> {
  const { start, end } = eventsWindow(q, Date.now())
  const params = new URLSearchParams({
    start: String(start),
    end: String(end),
    dir: q.dir,
  })
  if (q.dom) params.set('dom', q.dom)
  if (q.base) params.set('base', q.base)
  const res = await fetch(`${API_BASE}/history/events?${params}`, { signal })
  if (!res.ok) return { kind: 'error', status: res.status }
  return { kind: 'ok', data: (await res.json()) as EventsResponse }
}

export interface EventsState {
  /** 마지막으로 끝난 결과 — 조회 중에도 직전 결과를 유지한다 (§3.5). */
  result: EventsResult | null
  loading: boolean
}

/** `active` = 기록 탭이 보이는 중 — 아닐 때는 60초 재조회를 멈춘다(조건이 바뀌면 그때는 부른다). */
export function useEvents(q: EventsQuery, active: boolean): EventsState {
  const [state, setState] = useState<EventsState>({ result: null, loading: false })
  // 60초 재조회 — 회차 카운터가 오르면 아래 효과가 다시 돈다
  const { round, markFetched } = usePollRound(active, HISTORY_EVENTS_POLL_MS)
  // 객체 q 를 통째 의존성에 넣으면 렌더마다 재조회되므로 원시값으로 푼다
  const { dir, dom, periodSec, startSec, base } = q
  useEffect(() => {
    const ctl = new AbortController()
    markFetched()
    setState((s) => ({ ...s, loading: true }))
    const timer = setTimeout(() => {
      fetchEvents({ dir, dom, periodSec, startSec, base }, ctl.signal)
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
  }, [dir, dom, periodSec, startSec, base, round, markFetched])
  return state
}

// ── /history/candles (스펙 014 §3.7) ──
// 선택된 (국내 × 해외) 쌍마다 계층의 청크(360창, KST 기준 청크 길이 배수 정렬)를 부른다. 청크는 (쌍, 심볼, 방향, 계층, 청크 시작)
// 키로 메모리에 들고 있어 심볼·방향·쌍·봉을 바꿔도 없는 청크만 새로 부르고, 최신 청크만 기록 탭이 보이는 동안 60초마다 다시 부른다
// (창이 닫힐 때마다 새 봉). 캐시는 최근에 쓴 120청크까지(LRU, 지금 화면이 쓰는 청크는 버리지 않는다).
// 청크가 상한 길이라 400 은 나올 수 없다. 요청은 위와 같은 400ms 디바운스·직전 요청 취소.

class HttpError extends Error {
  constructor(readonly status: number) { super(`HTTP ${status}`) }
}

export async function fetchCandles(ref: ChunkRef, signal: AbortSignal): Promise<CandlesResponse> {
  const params = new URLSearchParams({
    base: ref.base, res: ref.res, dom: ref.dom, fx: ref.fx, dir: ref.dir,
    start: String(ref.start), end: String(ref.start + chunkSec(ref.res)),
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

/** `active` = 기록 탭이 보이는 중 — 아닐 때는 최신 청크 60초 재조회를 멈춘다. */
export function useCandles(q: CandlesQuery, active: boolean): CandlesState {
  const cache = useRef(new Map<string, Candle1m[]>())
  // 캐시 내용이 바뀐 횟수 — 이 값이 오를 때만 쌍별 봉 배열을 다시 만든다(차트가 다시 그린다)
  const [version, setVersion] = useState(0)
  const [loading, setLoading] = useState(false)
  const [errorStatus, setErrorStatus] = useState<number | null>(null)
  const { round, markFetched } = usePollRound(active, HISTORY_CANDLES_POLL_MS)
  const seenRound = useRef(0)
  const { base, dir, interval, older } = q
  const res = RES_OF_INTERVAL[interval]
  const domsKey = q.doms.join('+'), fxsKey = q.fxs.join('+')
  useEffect(() => {
    // 60초 회차로 깨어난 경우만 최신 청크를 다시 부른다 — 봉 종류·쌍을 바꿀 때는 없는 청크만
    const refreshLatest = round !== seenRound.current
    seenRound.current = round
    const ctl = new AbortController()
    // 부를 청크는 지금 정하고 요청만 디바운스한다 — 로딩 표시를 타이머 뒤로 미루면 코인·봉 종류를 바꾼 직후
    // 캐시가 비어 있는 동안 "기록 없음" 이 먼저 보인다
    const now = Math.floor(Date.now() / 1000)
    const { starts } = neededChunks(now, res, INTERVAL_SEC[interval], older)
    const latest = starts[starts.length - 1]
    const refs: ChunkRef[] = []
    const inUse = new Set<string>() // 지금 화면이 쓰는 청크 — 캐시 상한에서도 버리지 않는다
    for (const dom of domsKey.split('+') as Dom[]) {
      for (const fx of fxsKey.split('+')) {
        for (const start of starts) {
          const ref: ChunkRef = { dom, fx, base, dir, res, start }
          const key = chunkKey(ref)
          inUse.add(key)
          const cached = touchChunk(cache.current, key) // 가진 청크는 최근에 쓴 것으로 올린다
          if ((refreshLatest && start === latest) || cached === undefined) refs.push(ref)
        }
      }
    }
    setLoading(refs.length > 0) // 직전 회차가 취소돼 켜진 채 남은 표시도 여기서 끈다
    if (refs.length === 0) return
    markFetched()
    let changed = false
    const timer = setTimeout(() => {
      Promise.all(refs.map((ref) => fetchCandles(ref, ctl.signal).then((body) => {
        const key = chunkKey(ref)
        const prev = cache.current.get(key)
        // 다시 받은 청크가 들고 있던 것과 같으면(5분 이상 계층은 60초 회차 대부분이 그렇다) 배열을 바꾸지 않는다 — 차트가 다시 그리지 않게
        if (prev !== undefined && sameChunk(prev, body.candles)) return
        cache.current.set(key, body.candles)
        changed = true
      })))
        .then(() => setErrorStatus(null))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === 'AbortError') return
          setErrorStatus(err instanceof HttpError ? err.status : 0)
        })
        .finally(() => {
          if (ctl.signal.aborted) return // 취소는 정상 경로 — 새 요청이 상태를 이어받는다
          setLoading(false)
          evictChunks(cache.current, inUse)
          if (changed) setVersion((v) => v + 1)
        })
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      ctl.abort()
    }
  }, [base, dir, domsKey, fxsKey, res, interval, older, round, markFetched])

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
