// 수집 상태 탭 (mock, 일 단위 시드) — 스펙 002 §3.9.
import { fmtAgo, fmtHm, fmtHms, fmtTime } from '../../shared/format'
import type { Feed, HealthEx, HealthState } from '../../shared/types'
import { Chip, DIM_TEXT } from '../../shared/ui'

const DAY_MIN = 24 * 60

function stateColor(s: HealthState): string {
  return s === 'down' ? 'var(--color-up)' : s === 'stale' ? 'var(--color-warn)' : 'var(--color-ok)'
}

function stateLine(s: HealthState): { mark: string; text: string } {
  if (s === 'ok') return { mark: '●', text: 'WebSocket 연결됨' }
  if (s === 'stale') return { mark: '◌', text: '재연결 중' }
  return { mark: '✕', text: '끊김' }
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

function ExCard({ ex }: { ex: HealthEx }) {
  const line = stateLine(ex.state)
  const mkts = [ex.spot > 0 ? `현물 ${ex.spot}` : null, ex.perp > 0 ? `선물 ${ex.perp}` : null]
    .filter((v) => v !== null)
    .join(' · ')
  return (
    <div style={{ ...cardStyle, borderTop: `2px solid ${stateColor(ex.state)}` }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>{ex.name}</span>
        <span style={{ fontSize: 11, color: stateColor(ex.state), whiteSpace: 'nowrap' }}>
          {line.mark} {line.text}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 10px', marginTop: 8, fontSize: 12 }}>
        <span style={{ color: DIM_TEXT }}>마지막 수신</span>
        <span style={{ textAlign: 'right', color: ex.state !== 'ok' ? 'var(--color-warn)' : undefined }}>
          {fmtAgo(ex.lastRecvSec)}
        </span>
        <span style={{ color: DIM_TEXT }}>구독 마켓</span>
        <span style={{ textAlign: 'right' }}>{mkts}</span>
        <span style={{ color: DIM_TEXT }}>최근 실패율</span>
        <span
          style={{
            textAlign: 'right',
            color: ex.state === 'down' ? DIM_TEXT : ex.failRate > 2 ? 'var(--color-warn)' : undefined,
          }}
        >
          {ex.state === 'down' ? '–' : `${ex.failRate.toFixed(2)}%`}
        </span>
      </div>
    </div>
  )
}

export default function HealthTab({ feed, now }: { feed: Feed; now: number }) {
  const cards = feed.health
  const downs = cards.filter((c) => c.state === 'down')
  const stales = cards.filter((c) => c.state === 'stale')
  const circle = downs.length ? 'var(--color-up)' : stales.length ? 'var(--color-warn)' : 'var(--color-ok)'
  const phrase = downs.length
    ? `장애 — ${downs.map((d) => d.name).join(', ')} 끊김`
    : stales.length
      ? `일부 결측 — ${stales.length}곳 지연`
      : '정상'
  const totalMkts = cards.reduce((s, c) => s + c.spot + c.perp, 0)
  // 최근 1시간 수집 성공률 = 100 − 평균(down 은 12, 아니면 실패율/8).
  const success = 100 - cards.reduce((s, c) => s + (c.state === 'down' ? 12 : c.failRate / 8), 0) / cards.length

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
            <div style={kicker}>총 구독 마켓</div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{totalMkts.toLocaleString('ko-KR')}</div>
          </div>
          <div>
            <div style={kicker}>최근 1시간 수집 성공률</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: success > 99 ? undefined : 'var(--color-warn)' }}>
              {success.toFixed(2)}%
            </div>
          </div>
          <span style={{ marginLeft: 'auto', fontSize: 11, color: DIM_TEXT }}>{fmtHms(now)} 기준</span>
        </div>

        {/* 2. 거래소 카드 8장 (4열) */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          {cards.map((ex) => (
            <ExCard key={ex.name} ex={ex} />
          ))}
        </div>

        {/* 3. 결측 타임라인 */}
        <div style={cardStyle}>
          <div style={{ ...kicker, marginBottom: 10 }}>결측 구간 · 최근 24시간</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {cards.map((ex) => (
              <div key={ex.name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ width: 84, fontSize: 11, color: DIM_TEXT, flex: 'none' }}>{ex.name}</span>
                <div
                  style={{
                    position: 'relative',
                    flex: 1,
                    height: 10,
                    borderRadius: 5,
                    background: 'color-mix(in srgb, var(--color-text) 6%, transparent)',
                    overflow: 'hidden',
                  }}
                >
                  {ex.gaps.map((g, i) => {
                    const start = now - g.startAgoMin * 60_000
                    const end = now - (g.startAgoMin - g.durMin) * 60_000
                    return (
                      <span
                        key={i}
                        title={`${fmtHm(start)} – ${fmtHm(end)} 결측`}
                        style={{
                          position: 'absolute',
                          top: 0,
                          bottom: 0,
                          left: `${((DAY_MIN - g.startAgoMin) / DAY_MIN) * 100}%`,
                          width: `${Math.max((g.durMin / DAY_MIN) * 100, 0.3)}%`,
                          background: 'var(--color-warn)',
                          borderRadius: 5,
                        }}
                      />
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
          {/* 축 5눈금: HH:00 ×4 + 지금 */}
          <div style={{ position: 'relative', height: 16, marginTop: 6, marginLeft: 94, fontSize: 10, color: DIM_TEXT }}>
            {[1, 2, 3, 4].map((i) => {
              const t = new Date(now - (1 - i / 5) * 86_400_000)
              return (
                <span
                  key={i}
                  style={{ position: 'absolute', left: `${(i / 5) * 100}%`, transform: 'translateX(-50%)' }}
                >
                  {String(t.getHours()).padStart(2, '0')}:00
                </span>
              )
            })}
            <span style={{ position: 'absolute', right: 0 }}>지금</span>
          </div>
        </div>

        {/* 4. 이벤트 로그 12행 (최신순) */}
        <div style={cardStyle}>
          <div style={{ ...kicker, marginBottom: 10 }}>이벤트 로그</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {feed.healthEvents.map((ev, i) => (
              <div
                key={i}
                className="hv-row4"
                style={{
                  display: 'grid',
                  gridTemplateColumns: '80px 100px 90px 1fr',
                  gap: 10,
                  alignItems: 'center',
                  fontSize: 12,
                  padding: '3px 6px',
                  borderRadius: 'var(--radius-sm)',
                }}
              >
                <span style={{ color: DIM_TEXT }}>{fmtTime(now - ev.ageMin * 60_000)}</span>
                <span>{ev.ex}</span>
                <Chip tone={ev.kind === '재연결' ? 'neutral' : 'warn'}>{ev.kind}</Chip>
                <span style={{ color: 'color-mix(in srgb, var(--color-text) 80%, transparent)' }}>{ev.msg}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
