import { useState, type ReactNode } from 'react';
import { useFeed } from './data/useFeed';
import { buildHealth } from './data/mockFeed';
import { fmtPct } from './lib/format';
import { pctColor } from './config';
import { kicker } from './components/ui';
import SpreadTab from './tabs/SpreadTab';
import HistoryTab from './tabs/HistoryTab';
import GapTab from './tabs/GapTab';
import PPTab from './tabs/PPTab';
import HealthTab from './tabs/HealthTab';
import FlowTab from './tabs/FlowTab';

export type TabId = 'spread' | 'history' | 'gap' | 'pp' | 'health' | 'flow';

const TABS: [TabId, string][] = [
  ['spread', '실시간 스프레드'], ['history', '기록/통계'], ['gap', '선물–현물 갭'],
  ['pp', '선선갭'], ['health', '수집 상태'], ['flow', '입출금 레이더'],
];

// 세로 구분선 (KPI 스트립)
const vDivider = (
  <div style={{ width: 1, alignSelf: 'stretch', background: 'linear-gradient(to bottom, transparent, var(--color-divider), transparent)', flex: 'none' }} />
);

export default function App() {
  const { feed, now } = useFeed();
  const [tab, setTab] = useState<TabId>('spread');
  const [hSym, setHSym] = useState('BTC'); // 기록/통계 탭 선택 티커 — 스프레드 행 클릭으로도 바뀜

  // 수집 상태 요약 — KPI 스트립은 전 탭 공통이라 여기서 계산
  const { cards } = buildHealth(now);
  const downNames = cards.filter(c => c.st === 'down').map(c => c.name);
  const staleN = cards.filter(c => c.st === 'stale').length;

  // BTC 김프 KPI — fail 제외 전 페어 중 최고값
  const btcLive = feed.spreads.filter(r => r.sym === 'BTC' && r.status !== 'fail');
  const btcFwd = btcLive.length ? Math.max(...btcLive.map(r => r.fwd)) : 0;
  const btcRev = btcLive.length ? Math.max(...btcLive.map(r => r.rev)) : 0;
  // rate 0 = 백엔드 첫 폴링 전. 숫자를 지어내지 않고 '–' 로 둔다.
  const hasRate = feed.rate > 0;
  const coinCount = new Set(feed.spreads.map(r => r.sym)).size;

  // 탭 전환 시 각 탭의 필터 상태를 유지하려고 언마운트 대신 display로 숨긴다
  const wrap = (id: TabId, node: ReactNode) => (
    <div key={id} style={{ display: tab === id ? 'contents' : 'none' }}>{node}</div>
  );

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--color-bg)', color: 'var(--color-text)', fontFamily: 'var(--font-body)', fontSize: 13 }}>

      {/* 헤더: 타이틀 + LIVE + 탭 + 시계 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)', padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-divider)', height: 52, flex: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-3)' }}>
          <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 16, letterSpacing: '-0.01em' }}>트레이딩룸</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--color-neutral-500)' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#5fbf8f', animation: 'tr-pulse 1.6s ease-in-out infinite' }} />실시간 수집 중
          </span>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', alignSelf: 'stretch', alignItems: 'stretch' }}>
          {TABS.map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} className="hv-txt"
              style={{
                appearance: 'none', background: 'none', border: 'none',
                borderBottom: `2px solid ${tab === id ? 'var(--color-accent)' : 'transparent'}`,
                color: tab === id ? 'var(--color-text)' : 'var(--color-neutral-500)',
                font: 'inherit', fontSize: 13, padding: '0 var(--space-4)', cursor: 'pointer',
              }}>
              {label}
            </button>
          ))}
        </div>
        <div style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', fontSize: 12 }}>
          {new Date(now).toLocaleTimeString('ko-KR', { hour12: false })} KST
        </div>
      </div>

      {/* KPI 스트립 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)', padding: 'var(--space-4) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', overflowX: 'auto' }}>
        <div style={{ flex: 'none' }}>
          <div style={{ ...kicker, marginBottom: 2 }}>USDT/KRW 암묵환율</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-3)' }}>
            <span style={{ fontSize: 18, fontVariantNumeric: 'tabular-nums' }}>
              {hasRate ? '₩' + feed.rate.toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : '–'}
            </span>
          </div>
        </div>
        {vDivider}
        <div style={{ flex: 'none' }}>
          <div style={{ ...kicker, marginBottom: 2 }}>BTC 김프 · 순방향</div>
          <div style={{ fontSize: 18, fontVariantNumeric: 'tabular-nums', color: pctColor(btcFwd) }}>{fmtPct(btcFwd)}</div>
        </div>
        <div style={{ flex: 'none' }}>
          <div style={{ ...kicker, marginBottom: 2 }}>BTC 김프 · 역방향</div>
          <div style={{ fontSize: 18, fontVariantNumeric: 'tabular-nums', color: pctColor(btcRev) }}>{fmtPct(btcRev)}</div>
        </div>
        {vDivider}
        <div style={{ flex: 'none' }}>
          <div style={{ ...kicker, marginBottom: 2 }}>수집 상태</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-3)', fontSize: 14 }}>
            <span>{cards.length}곳 중 {cards.length - downNames.length}곳 정상</span>
            <span style={{ fontSize: 11, color: 'var(--color-neutral-500)' }}>
              {downNames.length ? `${downNames.join(', ')} 끊김 · ${staleN}곳 지연` : staleN ? `${staleN}곳 지연` : '전체 정상'}
            </span>
          </div>
        </div>
        <div style={{ flex: 'none', marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ ...kicker, marginBottom: 2 }}>추적 페어</div>
          <div style={{ fontSize: 14, fontVariantNumeric: 'tabular-nums' }}>{coinCount}개 코인 · {feed.spreads.length} 페어</div>
        </div>
      </div>

      {wrap('spread', <SpreadTab feed={feed} onPivot={sym => { setHSym(sym); setTab('history'); }} />)}
      {wrap('history', <HistoryTab feed={feed} now={now} hSym={hSym} onSelect={setHSym} />)}
      {wrap('gap', <GapTab feed={feed} now={now} />)}
      {wrap('pp', <PPTab feed={feed} />)}
      {wrap('health', <HealthTab now={now} />)}
      {wrap('flow', <FlowTab feed={feed} />)}

      {/* 푸터 — 입출금 레이더 탭은 FlowTab이 자체 푸터를 그림 */}
      {tab !== 'flow' && (
        <div style={{ flex: 'none', display: 'flex', gap: 'var(--space-6)', padding: 'var(--space-2) var(--space-6)', borderTop: '1px solid var(--color-divider)', fontSize: 11, color: 'var(--color-neutral-600)' }}>
          <span>암묵환율 = 국내 거래소 USDT/KRW 체결가 기준</span>
          <span>순방향 = 해외 매수 → 국내 매도 · 역방향 = 국내 매수 → 해외 매도</span>
          <span style={{ marginLeft: 'auto' }}>수집 실패 값은 보간 없이 –로 표시</span>
        </div>
      )}
    </div>
  );
}
