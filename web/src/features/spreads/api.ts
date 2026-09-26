// /ws/spreads 구독 — 폴링 대신 snapshot + delta 푸시 (스펙 017 §3.4). WebSocket 이 막히면 백오프 재연결만 반복한다(폴링 fallback 없음).
// 거래소 id→표시명 변환은 shared/format 의 exName (011 이 같이 쓴다). 구독은 이 기능 폴더 안에 살고,
// 셸이 공유 피드를 만든 직후 시작한다.
import { useEffect, useReducer } from 'react'
import {
  API_BASE, SPREAD_WS_BACKOFF_EMPTY_MAX_MS, SPREAD_WS_BACKOFF_MAX_MS, SPREAD_WS_BACKOFF_MIN_MS, SPREAD_WS_SILENCE_MS,
} from '../../shared/config'
import { exName } from '../../shared/format'
import type { Feed, SpreadRow } from '../../shared/types'
import type { SpreadsMessage, SpreadsResponse } from './types'

/** ws(s)://<host>/api/ws/spreads — API_BASE 가 상대경로라 페이지 origin 으로 절대화한 뒤 스킴만 바꾼다 (§3.4). */
function socketUrl(): string {
  const url = new URL(`${API_BASE}/ws/spreads`, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/** 서버 행(거래소 id) → 피드 행(표시명). 키는 서버 id 기준 `sym|dom|fx` 라 변환 전에 만든다. */
const rowKey = (r: SpreadRow) => `${r.sym}|${r.dom}|${r.fx}`
const display = (r: SpreadRow): SpreadRow => ({ ...r, dom: exName(r.dom), fx: exName(r.fx) })

/**
 * 연결 하나를 열고 snapshot 은 통째 교체, delta 는 키로 병합·삭제한다. delta 에 안 실린 행의 age 는 서버가 마지막에 준
 * 값으로 되돌린다 — 안 실렸다는 건 서버 판정이 안 바뀌었다는 뜻이다(조용한 코인도 현재값). 셸의 1.5초 tick 은 그대로
 * age 를 키우므로 delta 가 끊기면(수집 정지·반쯤 죽은 연결) 5초 뒤 전 행이 stale 로 간다 (§3.4).
 */
export function useSpreadSocket(feed: Feed): void {
  const [, bump] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    let alive = true
    let ws: WebSocket | null = null
    let backoff = SPREAD_WS_BACKOFF_MIN_MS
    let hasTable = false // snapshot 을 한 번이라도 받았는가 — 못 받은 동안은 재연결 상한을 짧게 (§3.4)
    const rows = new Map<string, SpreadRow>() // 서버 키 → 피드 행 객체
    const serverAge = new Map<string, number>() // 서버 키 → 서버가 마지막에 준 age
    let silenceTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null

    function apply(rate: number): void {
      feed.replace([...rows.values()], rate)
      bump()
    }

    function put(r: SpreadRow): void {
      rows.set(rowKey(r), display(r))
      serverAge.set(rowKey(r), r.age)
    }

    function applyTable(res: SpreadsResponse): void {
      hasTable = true
      rows.clear()
      serverAge.clear()
      for (const r of res.rows) put(r)
      apply(res.rate)
    }

    // --- 무응답 감지: 어떤 메시지든 10초 없으면 닫고 재연결 ---
    function armSilence(): void {
      if (silenceTimer !== null) clearTimeout(silenceTimer)
      silenceTimer = setTimeout(() => ws?.close(), SPREAD_WS_SILENCE_MS)
    }

    function handle(msg: SpreadsMessage): void {
      switch (msg.type) {
        case 'snapshot':
          applyTable(msg)
          break
        case 'delta':
          for (const r of msg.rows) put(r)
          for (const k of msg.removed) {
            rows.delete(k)
            serverAge.delete(k)
          }
          // 안 실린 행 = 서버 판정 그대로 → tick 이 키운 age 를 서버 값으로 되돌린다 (§3.4)
          for (const [k, r] of rows) r.age = serverAge.get(k) ?? r.age
          apply(msg.rate)
          break
        case 'waiting':
        case 'heartbeat':
          break
      }
    }

    function scheduleReconnect(): void {
      if (!alive || reconnectTimer !== null) return
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        connect()
      }, backoff)
      backoff = Math.min(backoff * 2, hasTable ? SPREAD_WS_BACKOFF_MAX_MS : SPREAD_WS_BACKOFF_EMPTY_MAX_MS)
    }

    function connect(): void {
      if (!alive) return
      const socket = new WebSocket(socketUrl())
      ws = socket
      armSilence()
      socket.onmessage = (ev) => {
        if (!alive || ws !== socket) return
        backoff = SPREAD_WS_BACKOFF_MIN_MS // 서버 메시지를 받았으면 살아 있는 연결
        armSilence()
        try {
          handle(JSON.parse(ev.data as string) as SpreadsMessage)
        } catch {
          // 알 수 없는 프레임은 무시
        }
      }
      socket.onclose = () => {
        if (ws === socket) ws = null
        scheduleReconnect() // 어느 코드든 재연결, 직전 표는 지우지 않는다 — 막힌 망이면 이 반복이 전부다
      }
      socket.onerror = () => {
        // onclose 가 뒤따라 온다
      }
    }

    connect()
    return () => {
      alive = false
      if (silenceTimer !== null) clearTimeout(silenceTimer)
      if (reconnectTimer !== null) clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [feed])
}
