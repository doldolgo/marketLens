// /ws/gap 구독 — 탭이 보이는 동안만 연결 (스펙 048 §3.6). 연결 규칙(gzip 해제·직렬 처리·10초 무응답·백오프·지터·
// 첫 snapshot 전 3초 상한·폴링 fallback 없음)은 017 §3.4 와 같다. 017 의 훅은 spreads 기능 폴더에 살고 기능 간 import 가
// 금지라 여기에 같은 규칙을 다시 쓴다(049 pp 도 같은 모양이면 shared 로 옮길 후보 — status.md 빚).
import { useEffect, useReducer, useRef } from 'react'
import {
  API_BASE, SPREAD_WS_BACKOFF_EMPTY_MAX_MS, SPREAD_WS_BACKOFF_MAX_MS, SPREAD_WS_BACKOFF_MIN_MS, SPREAD_WS_SILENCE_MS,
} from '../../shared/config'
import type { GapMessage, GapRow } from './types'

/** ws(s)://<host>/api/ws/gap — API_BASE 가 상대경로라 페이지 origin 으로 절대화한 뒤 스킴만 바꾼다. */
function socketUrl(): string {
  const url = new URL(`${API_BASE}/ws/gap`, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/** gzip 바이너리 프레임 → JSON 문자열 — 서버가 표 1장당 1회만 압축한 같은 바이트를 전원에게 보낸다 (017 §3.3). */
async function inflate(data: ArrayBuffer): Promise<string> {
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('gzip'))
  return new Response(stream).text()
}

/** 서버 키 `sym|spot|perp` — 서버 id 기준이라 표시명으로 바꾸기 전에 만든다 (§3.6). */
const rowKey = (r: GapRow) => `${r.sym}|${r.spot}|${r.perp}`

/** 표 1장 — 행은 서버 id 그대로 들고, 표시명 변환은 집계가 한다. */
export interface GapTable {
  /** 서버 키 → 행. snapshot 은 통째 교체, delta 는 키로 병합·삭제. */
  rows: Map<string, GapRow>
  /** 마지막 snapshot·delta 를 적용한 시각(ms). 화면은 `age + (now − receivedAt)` 으로 stale 을 판정한다 —
   *  delta 가 끊기면(수집 정지·탭 숨김) 5초 뒤 전 행이 stale 로 가고, delta 가 오면 안 실린 행도 서버 age 로 되돌아간다 (017 §3.4 와 같은 결과). */
  receivedAt: number
}

export function createGapTable(): GapTable {
  return { rows: new Map(), receivedAt: 0 }
}

/** snapshot — 통째 교체. */
export function applySnapshot(table: GapTable, rows: GapRow[], now: number): void {
  table.rows.clear()
  for (const r of rows) table.rows.set(rowKey(r), r)
  table.receivedAt = now
}

/** delta — 키로 병합·삭제. 행은 JSON.parse 가 막 만든 객체라 복사하지 않는다. */
export function applyDelta(table: GapTable, rows: GapRow[], removed: string[], now: number): void {
  for (const r of rows) table.rows.set(rowKey(r), r)
  for (const k of removed) table.rows.delete(k)
  table.receivedAt = now
}

/** 재연결 대기(ms) = 백오프 × 0.5~1.5 — api 재기동 때 전원이 같은 박자로 다시 붙지 않게 (017 §3.4). */
function reconnectDelay(backoff: number): number {
  return backoff * (0.5 + Math.random())
}

/**
 * `active` 인 동안만 /ws/gap 연결 하나를 연다. 숨으면(active=false) 닫되 표는 메모리에 둔다 — 다시 보이면 새로 연결해
 * snapshot 으로 통째 교체한다. 돌려주는 표 객체는 고정이고 프레임마다 리렌더를 깨운다.
 */
export function useGapSocket(active: boolean): GapTable {
  const tableRef = useRef<GapTable | null>(null)
  tableRef.current ??= createGapTable()
  const table = tableRef.current
  const [, bump] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    if (!active) return
    let alive = true
    let ws: WebSocket | null = null
    let backoff = SPREAD_WS_BACKOFF_MIN_MS
    let hasTable = false // snapshot 을 한 번이라도 받았는가 — 못 받은 동안은 재연결 상한을 짧게
    let silenceTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null

    function armSilence(): void {
      if (silenceTimer !== null) clearTimeout(silenceTimer)
      silenceTimer = setTimeout(() => ws?.close(), SPREAD_WS_SILENCE_MS)
    }

    function handle(msg: GapMessage): void {
      switch (msg.type) {
        case 'snapshot':
          hasTable = true
          applySnapshot(table, msg.rows, Date.now())
          bump()
          break
        case 'delta':
          applyDelta(table, msg.rows, msg.removed, Date.now())
          bump()
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
      }, reconnectDelay(backoff))
      backoff = Math.min(backoff * 2, hasTable ? SPREAD_WS_BACKOFF_MAX_MS : SPREAD_WS_BACKOFF_EMPTY_MAX_MS)
    }

    function connect(): void {
      if (!alive) return
      const socket = new WebSocket(socketUrl())
      socket.binaryType = 'arraybuffer'
      ws = socket
      armSilence()
      // 해제가 비동기라 도착 순서대로 처리한다 — delta 순서가 바뀌면 병합·삭제가 틀어진다
      let chain: Promise<void> = Promise.resolve()
      socket.onmessage = (ev) => {
        if (!alive || ws !== socket) return
        backoff = SPREAD_WS_BACKOFF_MIN_MS
        armSilence()
        chain = chain
          .then(() => inflate(ev.data as ArrayBuffer))
          .then((text) => {
            if (!alive || ws !== socket) return
            handle(JSON.parse(text) as GapMessage)
          })
          .catch(() => {
            // 알 수 없는 프레임은 무시
          })
      }
      socket.onclose = () => {
        if (ws === socket) ws = null
        scheduleReconnect()
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
  }, [active, table])
  return table
}
