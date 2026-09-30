/**
 * KimpTrack canary — 5분마다 밖에서 도는 점검 (스펙 027 §3.5).
 *
 * AWS Lambda(nodejs22.x, EventBridge Scheduler 가 5분마다 부른다) 의 handler 이자 로컬 node 스크립트다.
 * 표준 fetch·WebSocket 만 쓰고, 실패하면 단계 번호가 든 메시지로 던진다 — Lambda 는 그 호출을 Errors 로 센다.
 * Node 22 가 필요하다 — 4단계가 내장 WebSocket 을 쓴다(Node 20 에는 없다).
 * 네 단계를 순서대로 돌고 하나라도 실패하면 그 실행은 실패다.
 *   1. GET /                      — 200, 본문에 KimpTrack (serve 박스·caddy·nginx)
 *   2. GET /api/health            — 200, status ok (수집기 틱 30초 이내)
 *   3. GET /api/history/candles   — 200, 최근 15분 1분 봉 count ≥ 1 (api·api→Influx 읽기·봉 쓰기)
 *   4. WS  /api/ws/spreads        — 15초 안에 snapshot(rows ≥ 1), 이어서 5초 안에 delta (수집 → Redis → api 허브)
 * 주소는 env CANARY_BASE_URL(기본 https://kimptrack.com), WebSocket 주소는 스킴만 바꾼다.
 * 모든 요청의 User-Agent 는 KimpTrack-Canary/1 — caddy 가 이 요청을 접속 로그에 남기지 않는다.
 *
 * 로컬: CANARY_BASE_URL=http://localhost:8080 node ops/canary/index.mjs
 */

import { pathToFileURL } from 'node:url'
import { gunzipSync } from 'node:zlib'

const UA = 'KimpTrack-Canary/1'
const REQUEST_TIMEOUT_MS = 8_000 // 1~3단계 요청마다, 재시도 없음
const SNAPSHOT_TIMEOUT_MS = 15_000
const DELTA_TIMEOUT_MS = 5_000
const CANDLE_WINDOW_SEC = 900 // 봉은 분이 닫힌 뒤 쓰인다 — 15분 동안 0개면 쓰기가 멈춘 것이다

function baseUrl() {
  return (process.env.CANARY_BASE_URL || 'https://kimptrack.com').replace(/\/+$/, '')
}

function fail(step, detail) {
  throw new Error(`${step}단계 실패: ${detail}`)
}

async function get(step, url) {
  let res
  let body
  try {
    res = await fetch(url, {
      headers: { 'User-Agent': UA },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    })
    // 8초 제한은 본문 읽기까지 덮는다 — 헤더 뒤 본문에서 멈추거나 끊겨도 단계 번호가 든 메시지로
    body = await res.text()
  } catch (err) {
    fail(step, `${url} ${res ? '본문 읽기' : '요청'} 오류 — ${err.name}: ${err.message}`)
  }
  if (res.status !== 200) fail(step, `${url} → ${res.status} ${body.slice(0, 200)}`)
  return body
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function json(step, url, body) {
  let data
  try {
    data = JSON.parse(body)
  } catch {
    fail(step, `${url} 응답이 JSON 이 아니다 — ${body.slice(0, 200)}`)
  }
  if (!isObject(data)) fail(step, `${url} 응답이 JSON 객체가 아니다 — ${body.slice(0, 200)}`)
  return data
}

async function landing(base) {
  const body = await get(1, `${base}/`)
  if (!body.includes('KimpTrack')) fail(1, '본문에 KimpTrack 이 없다')
}

async function health(base) {
  const url = `${base}/api/health`
  const data = json(2, url, await get(2, url))
  if (data.status !== 'ok') fail(2, `status ${data.status}`)
}

async function candles(base) {
  const end = Math.floor(Date.now() / 1000)
  const url = `${base}/api/history/candles?base=BTC&res=1m&start=${end - CANDLE_WINDOW_SEC}&end=${end}`
  const data = json(3, url, await get(3, url))
  if (!(data.count >= 1)) fail(3, `최근 15분 1분 봉 count ${data.count}`)
}

function spreads(base) {
  const url = base.replace(/^http/, 'ws') + '/api/ws/spreads'
  return new Promise((resolve, reject) => {
    // 표준 WebSocket 에는 헤더 인자가 없다 — Node 내장(undici) WebSocket 의 headers 옵션으로 UA 를 붙인다
    const ws = new WebSocket(url, { headers: { 'User-Agent': UA } })
    ws.binaryType = 'arraybuffer'
    let settled = false
    let snapshot = false
    let timer = setTimeout(() => finish('15초 안에 snapshot 이 없다'), SNAPSHOT_TIMEOUT_MS)

    function finish(problem) {
      if (settled) return
      settled = true
      clearTimeout(timer)
      ws.close()
      if (problem) reject(new Error(`4단계 실패: ${problem}`))
      else resolve()
    }

    ws.onmessage = (event) => {
      let msg
      try {
        // 프레임은 바이너리 1개 = gzip 으로 압축한 JSON 1개 (017)
        msg = JSON.parse(gunzipSync(Buffer.from(event.data)).toString('utf8'))
      } catch (err) {
        finish(`프레임을 못 풀었다 — ${err.message}`)
        return
      }
      if (!isObject(msg)) {
        finish('프레임이 JSON 객체가 아니다')
        return
      }
      if (!snapshot) {
        // waiting·heartbeat 는 첫 표를 기다린다
        if (msg.type !== 'snapshot') return
        if (!Array.isArray(msg.rows) || msg.rows.length < 1) {
          finish('snapshot 의 rows 가 비었다')
          return
        }
        snapshot = true
        clearTimeout(timer)
        timer = setTimeout(() => finish('snapshot 뒤 5초 안에 delta 가 없다(heartbeat 만 — 수집 정체)'), DELTA_TIMEOUT_MS)
        return
      }
      // delta 가 푸시 경로(수집기 → Redis → api 허브) 전체의 증거다
      if (msg.type === 'delta') finish()
    }
    ws.onerror = (event) => finish(`${url} 연결 오류 — ${event.message ?? event.error?.message ?? event.type}`)
    ws.onclose = (event) => finish(`${url} 이 먼저 닫혔다 (code ${event.code})`)
  })
}

export const handler = async () => {
  const base = baseUrl()
  const steps = [landing, health, candles, spreads]
  for (const [i, step] of steps.entries()) {
    const started = Date.now()
    await step(base)
    console.log(`${i + 1}단계 통과 (${Date.now() - started}ms)`)
  }
  return 'ok'
}

// 로컬 실행 — Lambda 는 handler 만 부른다
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  handler().then(
    () => console.log(`canary 통과 — ${baseUrl()}`),
    (err) => {
      console.error(err.message)
      process.exitCode = 1
    },
  )
}
