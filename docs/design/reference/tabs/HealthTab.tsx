// 수집 상태 탭 — 거래소별 수집 파이프라인 헬스체크 대시보드
import type { CSSProperties } from 'react';
import { buildHealth } from '../data/mockFeed';
import { fmtAge } from '../lib/format';
import { kicker } from '../components/ui';

const card: CSSProperties = { background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-sm)' };

// 상태별 색·아이콘·라벨
const ST = {
  ok: { color: '#5fbf8f', icon: '●', label: 'WebSocket 연결됨' },
  stale: { color: '#c8825f', icon: '◌', label: '재연결 중' },
  down: { color: '#e0697d', icon: '✕', label: '끊김' },
} as const;

export default function HealthTab({ now }: { now: number }) {
  const { cards, evLog } = buildHealth(now);
  const downNames = cards.filter(c => c.st === 'down').map(c => c.name);
  const staleN = cards.filter(c => c.st === 'stale').length;
  const ovLabel = downNames.length ? `장애 — ${downNames.join(', ')} 끊김` : staleN ? `일부 결측 — ${staleN}곳 지연` : '정상';
  const ovColor = downNames.length ? '#e0697d' : staleN ? '#c8825f' : '#5fbf8f';
  const totalMkts = cards.reduce((a, c) => a + c.spot + c.perp, 0);
  const okRate = 100 - cards.reduce((a, c) => a + (c.st === 'down' ? 12 : c.failPct / 8), 0) / cards.length;

  return (
    <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
      <div style={{ maxWidth: 1080, margin: '0 auto', padding: 'var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>

        {/* 전체 요약 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: ovColor }} />
            <span style={{ fontSize: 18, fontWeight: 500 }}>{ovLabel}</span>
          </div>
          <div>
            <div style={{ ...kicker, marginBottom: 2 }}>총 구독 마켓</div>
            <div style={{ fontSize: 16, fontVariantNumeric: 'tabular-nums' }}>{totalMkts.toLocaleString()}개</div>
          </div>
          <div>
            <div style={{ ...kicker, marginBottom: 2 }}>최근 1시간 수집 성공률</div>
            <div style={{ fontSize: 16, fontVariantNumeric: 'tabular-nums', color: okRate > 99 ? 'var(--color-text)' : '#c8825f' }}>{okRate.toFixed(2)}%</div>
          </div>
          <span style={{ marginLeft: 'auto', fontSize: 11, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>
            {new Date(now).toLocaleTimeString('ko-KR', { hour12: false })} 기준
          </span>
        </div>

        {/* 거래소 카드 */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 'var(--space-4)' }}>
          {cards.map(c => {
            const m = ST[c.st];
            return (
              <div key={c.name} style={{ ...card, padding: 'var(--space-4) var(--space-4)', borderTop: `2px solid ${m.color}` }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--space-3)' }}>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{c.name}</span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5, color: m.color }}>{m.icon} {m.label}</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px', fontSize: 11.5 }}>
                  <span style={{ color: 'var(--color-neutral-600)' }}>마지막 수신</span>
                  <span style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: c.st === 'ok' ? 'var(--color-neutral-300)' : '#c8825f' }}>{fmtAge(c.lastSec)}</span>
                  <span style={{ color: 'var(--color-neutral-600)' }}>구독 마켓</span>
                  <span style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {(c.spot ? '현물 ' + c.spot : '') + (c.spot && c.perp ? ' · ' : '') + (c.perp ? '선물 ' + c.perp : '')}
                  </span>
                  <span style={{ color: 'var(--color-neutral-600)' }}>최근 실패율</span>
                  <span style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: c.failPct > 2 ? '#c8825f' : 'var(--color-neutral-300)' }}>
                    {c.st === 'down' ? '–' : c.failPct.toFixed(1) + '%'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* 결측 구간 타임라인 */}
        <div style={{ ...card, padding: 'var(--space-4) var(--space-6)' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 'var(--space-4)' }}>
            <span style={kicker}>결측 구간 · 최근 24시간</span>
            <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>■ 수집 끊김 구간</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '96px 1fr', gap: '8px 12px', alignItems: 'center' }}>
            {cards.map(c => (
              <span key={c.name} style={{ display: 'contents' }}>
                <span style={{ fontSize: 11.5, color: 'var(--color-neutral-400)' }}>{c.name}</span>
                <div style={{ position: 'relative', height: 16, background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
                  {c.gaps.map((g, i) => {
                    const stT = new Date(now - (1 - g.start) * 864e5), enT = new Date(now - (1 - g.start - g.w) * 864e5);
                    const f = (t: Date) => String(t.getHours()).padStart(2, '0') + ':' + String(t.getMinutes()).padStart(2, '0');
                    return <span key={i} title={f(stT) + ' – ' + f(enT) + ' 결측'} style={{ position: 'absolute', top: 2, bottom: 2, left: (g.start * 100).toFixed(2) + '%', width: (g.w * 100).toFixed(2) + '%', minWidth: 2, background: '#c8825f', borderRadius: 2 }} />;
                  })}
                </div>
              </span>
            ))}
            <span />
            <div style={{ position: 'relative', height: 14 }}>
              {[0, 0.25, 0.5, 0.75, 1].map(f => {
                const t = new Date(now - (1 - f) * 864e5);
                return (
                  <span key={f} style={{ position: 'absolute', left: (f * 100).toFixed(0) + '%', transform: 'translateX(-50%)', fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>
                    {f === 1 ? '지금' : String(t.getHours()).padStart(2, '0') + ':00'}
                  </span>
                );
              })}
            </div>
          </div>
        </div>

        {/* 이벤트 로그 */}
        <div style={{ ...card, padding: 'var(--space-2) 0' }}>
          <div style={{ ...kicker, padding: 'var(--space-4) var(--space-6) var(--space-2)' }}>최근 이벤트 로그</div>
          <div style={{ display: 'grid', gridTemplateColumns: '130px 130px 90px 1fr', padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)', fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--color-neutral-600)' }}>
            <span style={{ padding: '6px 8px 6px 0' }}>시각</span>
            <span style={{ padding: '6px 8px' }}>거래소</span>
            <span style={{ padding: '6px 8px' }}>유형</span>
            <span style={{ padding: '6px 8px' }}>내용</span>
          </div>
          {evLog.map((r, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '130px 130px 90px 1fr', alignItems: 'center', height: 32, padding: '0 var(--space-6)', borderBottom: '1px solid color-mix(in srgb, #e9e9ed 6%, transparent)' }}>
              <span style={{ padding: '0 8px 0 0', fontVariantNumeric: 'tabular-nums', fontSize: 11.5, color: 'var(--color-neutral-500)' }}>{r.time}</span>
              <span style={{ padding: '0 8px', fontSize: 12 }}>{r.ex}</span>
              <span style={{
                justifySelf: 'start', margin: '0 8px', fontSize: 10, padding: '2px 7px', borderRadius: 'var(--radius-sm)',
                border: `1px solid ${r.tag === '재연결' ? 'var(--color-neutral-700)' : '#8a5a42'}`,
                color: r.tag === '재연결' ? 'var(--color-neutral-400)' : '#c8825f',
              }}>{r.tag}</span>
              <span style={{ padding: '0 8px', fontSize: 12, color: 'var(--color-neutral-300)' }}>{r.msg}</span>
            </div>
          ))}
        </div>

      </div>
    </div>
  );
}
