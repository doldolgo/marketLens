// GET /health/collect 5초 폴링 (스펙 011 §3.6) — 003 패턴: 즉시 1회·재진입 방지·실패 시 직전 유지.
import { useEffect, useReducer } from 'react'
import { API_BASE, HEALTH_POLL_MS } from '../../shared/config'
import type { Feed, HealthData } from '../../shared/types'

async function fetchHealth(): Promise<HealthData> {
  const res = await fetch(`${API_BASE}/health/collect`)
  if (!res.ok) throw new Error(`GET /health/collect → ${res.status}`)
  return (await res.json()) as HealthData
}

/** 성공 시 공유 피드에 적용하고 리렌더, 실패(네트워크·비 2xx)는 무시하고 직전 응답 유지. */
export function useHealthPolling(feed: Feed): void {
  const [, bump] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    let alive = true
    let busy = false // 응답이 폴링 주기보다 늦을 때 요청이 쌓이지 않게
    async function poll(): Promise<void> {
      if (busy) return
      busy = true
      try {
        const data = await fetchHealth()
        if (!alive) return
        feed.setHealth(data)
        bump()
      } catch {
        // 직전 응답 유지 — 서버 시각은 절대값이라 경과 표기가 저절로 자란다
      } finally {
        busy = false
      }
    }
    void poll()
    const id = setInterval(() => void poll(), HEALTH_POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [feed])
}
