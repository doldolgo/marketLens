// 수집 상태 탭 — /health/collect 실데이터 (스펙 011 §3.8). 카드 4개: 요약·거래소 3장·24시간 타임라인·로그.
import { exName, fmtAgo, fmtHm, fmtHms } from '../../shared/format'
import type { Feed, HealthData, HealthExchange, HealthOutage, HealthState, OutageKind } from '../../shared/types'
import { Chip, DIM_TEXT } from '../../shared/ui'
import { KIND_LABELS, STATE_LINES } from './types'

const DAY_MS = 24 * 3_600_000
const LOG_MAX = 50
const MSG_MAX = 120

function stateColor(s: HealthState): string {
  return s === 'down' ? 'var(--color-up)' : s === 'stale' ? 'var(--color-warn)' : 'var(--color-ok)'
}

/** 차단·rate limit 은 빨강, 그 외 주황 (§3.8). */
function kindColor(k: OutageKind): string {
  return k === 'banned' || k === 'rate_limit' ? 'var(--color-up)' : 'var(--color-warn)'
}

function httpText(code: number | null): string {
  return code === null ? '' : ` · HTTP ${code}`
}

const cardStyle = {
  background: 'var(--color-surface)',
  borderRadius: 'var(--radius-md)',
  padding: 'var(--space-4)',
} as const

const kicker = {
  fontSize: 10,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'var(--color-accent)',
} as const

function ExCard({ ex, now }: { ex: HealthExchange; now: number }) {
  const line = STATE_LINES[ex.state]
  const err = ex.lastError
  return (
    <div style={{ ...cardStyle, borderTop: `2px solid ${stateColor(ex.state)}` }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>{exName(ex.exchange)}</span>
        <span style={{ fontSize: 11, color: stateColor(ex.state), whiteSpace: 'nowrap' }}>
          {line.mark} {line.text}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 10px', marginTop: 8, fontSize: 12 }}>
        <span style={{ color: DIM_TEXT }}>마지막 수신</span>
        <span style={{ textAlign: 'right', color: ex.state !== 'ok' ? 'var(--color-warn)' : undefined }}>
          {ex.lastSuccessAt === null ? '–' : fmtAgo(Math.max(0, now - ex.lastSuccessAt) / 1000)}
        </span>
        <span style={{ color: DIM_TEXT }}>수집 마켓</span>
        <span style={{ textAlign: 'right' }}>{ex.markets}</span>
        <span style={{ color: DIM_TEXT }}>성공률 1h</span>
        <span style={{ textAlign: 'right', color: ex.successRate1h <= 99 ? 'var(--color-warn)' : undefined }}>
          {ex.successRate1h.toFixed(1)}%
        </span>
        <span style={{ color: DIM_TEXT }}>최근 에러</span>
        <span style={{ textAlign: 'right' }}>
          {err ? `${fmtHms(err.at)} · ${KIND_LABELS[err.kind]}${httpText(err.statusCode)}` : '–'}
        </span>
      </div>
      {ex.openOutage && (
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-up)' }}>진행 중 · ×{ex.openOutage.count}회</div>
      )}
    </div>
  )
}

function Timeline({ data, now }: { data: HealthData; now: number }) {
  const winStart = now - DAY_MS
  const pct = (t: number) => `${(Math.min(Math.max(t, winStart), now) - winStart) / DAY_MS * 100}%`
  return (
    <div style={cardStyle}>
      <div style={{ ...kicker, marginBottom: 10 }}>실패 구간 · 최근 24시간</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {data.exchanges.map((ex) => (
          <div key={ex.exchange} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ width: 84, fontSize: 11, color: DIM_TEXT, flex: 'none' }}>{exName(ex.exchange)}</span>
            <div
              style={{
                position: 'relative',
                flex: 1,
                height: 10,
                borderRadius: 5,
                background: 'color-mix(in srgb, var(--color-text) 6%, transparent)',
              }}
            >
              {data.outages
                .filter((o) => o.exchange === ex.exchange)
                .map((o) => {
                  const end = o.endedAt ?? now
                  const start = Math.max(o.startedAt, winStart)
                  return (
                    <span
                      key={o.startedAt}
                      title={`${fmtHm(o.startedAt)} – ${fmtHm(end)} · ${KIND_LABELS[o.kind]}${httpText(o.statusCode)} · ×${o.count}회`}
                      style={{
                        position: 'absolute',
                        top: 0,
                        bottom: 0,
                        left: pct(start),
                        width: `${((Math.min(end, now) - start) / DAY_MS) * 100}%`,
                        minWidth: 2, // 1분 미만 구간도 보이게
                        background: kindColor(o.kind),
                        borderRadius: 5,
                      }}
                    />
                  )
                })}
              {data.serverStartedAt >= winStart && (
                <span
                  title={`${fmtHm(data.serverStartedAt)} 서버 시작`}
                  style={{
                    position: 'absolute',
                    top: -2,
                    bottom: -2,
                    left: pct(data.serverStartedAt),
                    borderLeft: `1px dashed ${DIM_TEXT}`,
                  }}
                />
              )}
            </div>
          </div>
        ))}
      </div>
      {/* 축 5눈금: HH:00 ×4 + 지금 (002 §3.9 와 같다) */}
      <div style={{ position: 'relative', height: 16, marginTop: 6, marginLeft: 94, fontSize: 10, color: DIM_TEXT }}>
        {[1, 2, 3, 4].map((i) => {
          const t = new Date(now - (1 - i / 5) * DAY_MS)
          return (
            <span key={i} style={{ position: 'absolute', left: `${(i / 5) * 100}%`, transform: 'translateX(-50%)' }}>
              {String(t.getHours()).padStart(2, '0')}:00
            </span>
          )
        })}
        <span style={{ position: 'absolute', right: 0 }}>지금</span>
      </div>
    </div>
  )
}

/** 로그 한 행의 내용 문자열 (§3.8 4번). */
function logContent(o: HealthOutage): string {
  const dur = o.endedAt === null ? '진행 중' : fmtAgo((o.endedAt - o.startedAt) / 1000).replace(' 전', '')
  const http = o.statusCode === null ? '' : `HTTP ${o.statusCode} · `
  const retry = o.retryAfterSec === null ? '' : ` · Retry-After ${o.retryAfterSec}s`
  return `${http}${o.message.slice(0, MSG_MAX)} · ×${o.count}회 · ${dur}${retry}`
}

const logRowStyle = {
  display: 'grid',
  gridTemplateColumns: '80px 100px 90px 1fr',
  gap: 10,
  alignItems: 'center',
  fontSize: 12,
  padding: '3px 6px',
  borderRadius: 'var(--radius-sm)',
} as const

function Log({ data }: { data: HealthData }) {
  const rows = data.outages.slice(0, LOG_MAX) // 이미 startedAt 내림차순
  return (
    <div style={cardStyle}>
      <div style={{ ...kicker, marginBottom: 10 }}>최근 실패 구간</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {rows.length === 0 && <div style={{ fontSize: 12, color: DIM_TEXT, padding: '3px 6px' }}>최근 24시간 실패 없음</div>}
        {rows.map((o) => (
          <div key={`${o.exchange}|${o.startedAt}`} className="hv-row4" style={logRowStyle}>
            <span style={{ color: DIM_TEXT }}>{fmtHms(o.startedAt)}</span>
            <span>{exName(o.exchange)}</span>
            <Chip style={{ background: 'transparent', color: kindColor(o.kind), border: `1px solid ${kindColor(o.kind)}` }}>
              {KIND_LABELS[o.kind]}
            </Chip>
            <span style={{ color: 'color-mix(in srgb, var(--color-text) 80%, transparent)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {logContent(o)}
            </span>
          </div>
        ))}
        {/* 맨 끝(가장 오래된 쪽) 서버 시작 행 */}
        <div className="hv-row4" style={logRowStyle}>
          <span style={{ color: DIM_TEXT }}>{fmtHms(data.serverStartedAt)}</span>
          <span style={{ color: DIM_TEXT }}>서버</span>
          <Chip tone="neutral">서버 시작</Chip>
          <span style={{ color: DIM_TEXT }}>이력 복원 후 수집 시작</span>
        </div>
      </div>
    </div>
  )
}

export default function HealthTab({ feed, now }: { feed: Feed; now: number }) {
  const data = feed.health
  if (data === null) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, color: DIM_TEXT }}>
        수집 상태 조회 전
      </div>
    )
  }
  const downs = data.exchanges.filter((e) => e.state === 'down')
  const stales = data.exchanges.filter((e) => e.state === 'stale')
  const circle = downs.length ? 'var(--color-up)' : stales.length ? 'var(--color-warn)' : 'var(--color-ok)'
  const phrase = downs.length
    ? `장애 — ${downs.map((d) => exName(d.exchange)).join(', ')} 끊김`
    : stales.length
      ? `일부 지연 — ${stales.length}곳`
      : '정상'
  const totalMkts = data.exchanges.reduce((s, e) => s + e.markets, 0)

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 16 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 1200, margin: '0 auto' }}>
        {/* 1. 요약 */}
        <div style={{ ...cardStyle, display: 'flex', alignItems: 'center', gap: 28, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ width: 12, height: 12, borderRadius: '50%', background: circle, flex: 'none' }} />
            <span style={{ fontSize: 15, fontWeight: 600 }}>{phrase}</span>
          </div>
          <div>
            <div style={kicker}>총 수집 마켓</div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{totalMkts.toLocaleString('ko-KR')}</div>
          </div>
          <div>
            <div style={kicker}>최근 1시간 수집 성공률</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: data.successRate1h > 99 ? undefined : 'var(--color-warn)' }}>
              {data.successRate1h.toFixed(1)}%
            </div>
          </div>
          <span style={{ marginLeft: 'auto', fontSize: 11, color: DIM_TEXT }}>
            {fmtHms(data.fetchedAt)} 기준
            <span style={{ marginLeft: 12, opacity: 0.7 }}>{fmtHm(data.serverStartedAt)} 서버 시작</span>
          </span>
        </div>

        {/* 2. 거래소 카드 3장 (3열) */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
          {data.exchanges.map((ex) => (
            <ExCard key={ex.exchange} ex={ex} now={now} />
          ))}
        </div>

        {/* 3. 타임라인 */}
        <Timeline data={data} now={now} />

        {/* 4. 로그 */}
        <Log data={data} />
      </div>
    </div>
  )
}
