// /ws/spreads 구독 — 폴링 대신 snapshot + patch 푸시 (스펙 017 §3.4). WebSocket 이 막히면 백오프 재연결만 반복한다(폴링 fallback 없음).
// 거래소 id→표시명 변환은 shared/format 의 exName (011 이 같이 쓴다). 구독은 이 기능 폴더 안에 살고,
// 셸이 공유 피드를 만든 직후 시작한다.
import { useEffect, useReducer } from 'react'
import {
  API_BASE, SPREAD_WS_BACKOFF_EMPTY_MAX_MS, SPREAD_WS_BACKOFF_MAX_MS, SPREAD_WS_BACKOFF_MIN_MS, SPREAD_WS_SILENCE_MS,
} from '../../shared/config'
import { exName } from '../../shared/format'
import type { Feed, SpreadRow } from '../../shared/types'
import type { SpreadPatch, SpreadsMessage, SpreadsResponse } from './types'

/**
 * ws(s)://<host>/api/ws/spreads?v=2 — API_BASE 가 상대경로라 페이지 origin 으로 절대화한 뒤 스킴만 바꾼다 (§3.4).
 * `v=2` 는 새 표마다 바뀐 행을 통째로 싣는 delta 대신 바뀐 필드만 싣는 patch 를 받겠다는 표시다(§3.3) — 쿼리가 없는 옛 탭은
 * delta 를 그대로 받는다. 쿼리가 어디서 빠져도 delta 를 그대로 병합하므로 표는 맞다.
 */
function socketUrl(): string {
  const url = new URL(`${API_BASE}/ws/spreads?v=2`, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/**
 * gzip 바이너리 프레임 → JSON 문자열 (§3.3). 서버가 표 1장당 1회만 압축하려고 permessage-deflate(접속마다 따로 압축,
 * api CPU 가 접속자 수에 비례) 대신 미리 gzip 한 같은 바이트를 전원에게 보낸다. 브라우저 내장 DecompressionStream 으로 푼다.
 */
async function inflate(data: ArrayBuffer): Promise<string> {
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('gzip'))
  return new Response(stream).text()
}

/** 서버 키 `sym|dom|fx` — 서버 id 기준이라 표시명으로 바꾸기 전에 만든다. */
const rowKey = (r: Pick<SpreadRow, 'sym' | 'dom' | 'fx'>) => `${r.sym}|${r.dom}|${r.fx}`

/** 서버 키 → 피드 행. snapshot 은 통째 교체, delta·patch 는 키로 병합·삭제한다 (§3.4). */
export interface RowTable {
  snapshot(rows: SpreadRow[]): void
  delta(rows: SpreadRow[], removed: string[]): void
  /** `rows` 는 행 통째(새 키), `patches` 는 있던 행의 바뀐 필드. 모르는 키의 patch 가 있으면 false — 표가 어긋났다. */
  patch(rows: SpreadRow[], patches: SpreadPatch[], removed: string[]): boolean
  /** 피드에 걸 행 배열 — 부를 때마다 새 배열(행 객체는 그대로). */
  rows(): SpreadRow[]
}

export function createRowTable(): RowTable {
  const rows = new Map<string, SpreadRow>() // 서버 키 → 피드 행 객체
  const serverAge = new Map<string, number>() // 서버 키 → 서버가 마지막에 준 age

  // 행은 JSON.parse 가 막 만든 객체라 아무도 참조하지 않는다 — 복사하지 않고 그 자리에서 표시명으로 바꾸고, 키는 한 번만 만든다.
  // 매초 수백 행이 오는 자리라 행마다 19키 객체를 복사하면 그만큼 가비지가 생긴다
  function put(r: SpreadRow): void {
    const k = rowKey(r)
    serverAge.set(k, r.age)
    r.dom = exName(r.dom)
    r.fx = exName(r.fx)
    rows.set(k, r)
  }

  // patch 항목의 필드를 있던 행에 그 자리에서 덮는다 — 행 키 셋은 서버 id 라 건너뛴다(행은 표시명을 들고 있다).
  // spark 는 새 배열로 바꾼다(끝값 바꾸기·앞 하나 버리고 붙이기) — 화면은 spark 를 읽지 않지만 배열을 고쳐 쓰지는 않는다
  function merge(r: SpreadRow, p: SpreadPatch): void {
    const dst = r as unknown as Record<string, unknown>
    const src = p as unknown as Record<string, unknown>
    for (const f in src) {
      switch (f) {
        case 'sym':
        case 'dom':
        case 'fx':
          break
        case 'sparkEnd':
          r.spark = [...r.spark.slice(0, -1), p.sparkEnd as number]
          break
        case 'sparkPush':
          r.spark = [...r.spark.slice(1), p.sparkPush as number]
          break
        default:
          dst[f] = src[f]
      }
    }
  }

  function settle(removed: string[]): void {
    for (const k of removed) {
      rows.delete(k)
      serverAge.delete(k)
    }
    // 안 실린 행 = 서버 판정 그대로 → tick 이 키운 age 를 서버 값으로 되돌린다 (§3.4)
    for (const [k, r] of rows) r.age = serverAge.get(k) ?? r.age
  }

  return {
    snapshot(list) {
      rows.clear()
      serverAge.clear()
      for (const r of list) put(r)
    },
    delta(list, removed) {
      for (const r of list) put(r)
      settle(removed)
    },
    patch(list, patches, removed) {
      for (const r of list) put(r)
      let known = true
      for (const p of patches) {
        const k = rowKey(p)
        const r = rows.get(k)
        if (r === undefined) {
          known = false
          continue
        }
        merge(r, p)
        serverAge.set(k, p.age)
      }
      settle(removed)
      return known
    },
    rows: () => [...rows.values()],
  }
}

/** 재연결 대기(ms) = 백오프 × 0.5~1.5 — api 가 재기동하면 접속자 전원이 같은 순간 끊기므로, 같은 박자로 다시 붙지 않게 흩는다 (§3.4). */
export function reconnectDelay(backoff: number, r: number = Math.random()): number {
  return backoff * (0.5 + r)
}

/**
 * 연결 하나를 열고 snapshot 은 통째 교체, patch(옛 경로면 delta)는 키로 병합·삭제한다. 안 실린 행의 age 는 서버가 마지막에 준
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
    const table = createRowTable()
    let silenceTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null

    // 숨은 브라우저 탭은 표를 병합만 하고 그리지 않는다 — 연결·병합은 그대로(delta 는 바뀐 행만 오므로 하나라도 빠지면 표가
    // 틀어진다), 셸·표 다시 그리기만 다시 보이는 순간 한 번으로 모은다. feed.replace 는 미루지 않는다 — 미루면 셸 tick 이 옛 배열의
    // age 를 키우고 새 delta 로 바뀐 행 객체는 키우지 않아, 숨은 동안 delta 가 끊겼을 때 다시 보인 직후 stale 판정이 틀어진다
    let unpainted = false
    function apply(rate: number): void {
      feed.replace(table.rows(), rate)
      if (document.hidden) {
        unpainted = true
        return
      }
      bump()
    }
    const onVisible = () => {
      if (document.hidden || !unpainted) return
      unpainted = false
      bump()
    }
    document.addEventListener('visibilitychange', onVisible)

    function applyTable(res: SpreadsResponse): void {
      hasTable = true
      table.snapshot(res.rows)
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
        case 'delta': // 쿼리가 빠진 경로·배포 중 옛 api — 행 통째라 그대로 병합한다
          table.delta(msg.rows, msg.removed)
          apply(msg.rate)
          break
        case 'patch':
          if (!table.patch(msg.rows, msg.patch, msg.removed)) ws?.close() // 모르는 키 — 다시 붙어 snapshot 부터
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
      }, reconnectDelay(backoff))
      backoff = Math.min(backoff * 2, hasTable ? SPREAD_WS_BACKOFF_MAX_MS : SPREAD_WS_BACKOFF_EMPTY_MAX_MS)
    }

    function connect(): void {
      if (!alive) return
      const socket = new WebSocket(socketUrl())
      socket.binaryType = 'arraybuffer' // 프레임은 gzip 바이너리 — 기본 Blob 이면 풀기 전에 한 번 더 읽어야 한다
      ws = socket
      armSilence()
      // 해제가 비동기라 도착 순서대로 처리하도록 직렬화한다 — delta 순서가 바뀌면 병합·삭제가 틀어진다
      let chain: Promise<void> = Promise.resolve()
      socket.onmessage = (ev) => {
        if (!alive || ws !== socket) return
        backoff = SPREAD_WS_BACKOFF_MIN_MS // 서버 메시지를 받았으면 살아 있는 연결
        armSilence()
        chain = chain
          .then(() => inflate(ev.data as ArrayBuffer))
          .then((text) => {
            if (!alive || ws !== socket) return // 푸는 사이 닫혔거나 새 연결로 바뀐 경우
            handle(JSON.parse(text) as SpreadsMessage)
          })
          .catch(() => {
            // 알 수 없는 프레임은 무시
          })
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
      document.removeEventListener('visibilitychange', onVisible)
      if (silenceTimer !== null) clearTimeout(silenceTimer)
      if (reconnectTimer !== null) clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [feed])
}
