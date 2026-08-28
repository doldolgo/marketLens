// 셸 레이아웃 — 헤더 + KPI 스트립 + 탭 6개 + 푸터 (스펙 002 §3.5).
// 탭은 언마운트하지 않고 숨긴다: 검색어·필터·드릴다운 상태가 전환 후에도 유지되어야 하기 때문.
import { useState } from 'react'
import type { ReactNode } from 'react'
import FlowTab from './features/flow/Tab'
import GapTab from './features/gap/Tab'
import HealthTab from './features/health/Tab'
import HistoryTab from './features/history/Tab'
import PpTab from './features/pp/Tab'
import { useSpreadPolling } from './features/spreads/api'
import SpreadsTab from './features/spreads/Tab'
import { useFeed } from './shared/feed'
import { fmtPct, pctColor } from './shared/format'
import type { Feed } from './shared/types'
import { DIM_TEXT } from './shared/ui'

type TabId = 'spread' | 'history' | 'gap' | 'pp' | 'health' | 'flow'

/** 탭 id·라벨·순서 고정 (§3.5). */
const TABS: ReadonlyArray<{ id: TabId; label: string }> = [
  { id: 'spread', label: '실시간 스프레드' },
  { id: 'history', label: '기록/통계' },
  { id: 'gap', label: '선물–현물 갭' },
  { id: 'pp', label: '선선갭' },
  { id: 'health', label: '수집 상태' },
  { id: 'flow', label: '입출금 레이더' },
]

function Header({ tab, onTab, now }: { tab: TabId; onTab: (t: TabId) => void; now: number }) {
  const clock = new Date(now).toLocaleTimeString('ko-KR', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 20,
        padding: '0 16px',
        borderBottom: '1px solid var(--color-divider)',
        flex: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: 'var(--font-heading)', fontSize: 17, fontWeight: 600 }}>트레이딩룸</span>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: 'var(--color-ok)',
            animation: 'tr-pulse 1.6s ease-in-out infinite',
            flex: 'none',
          }}
        />
        <span style={{ fontSize: 12, color: 'var(--color-ok)', whiteSpace: 'nowrap' }}>실시간 수집 중</span>
      </div>
      <nav style={{ display: 'flex', margin: '0 auto' }}>
        {TABS.map((t) => {
          const active = t.id === tab
          return (
            <button
              key={t.id}
              type="button"
              className={active ? undefined : 'hv-txt'}
              onClick={() => onTab(t.id)}
              style={{
                font: 'inherit',
                fontSize: 13,
                cursor: 'pointer',
                background: 'transparent',
                border: 'none',
                padding: '12px 13px 10px',
                whiteSpace: 'nowrap',
                color: active ? 'var(--color-text)' : 'color-mix(in srgb, var(--color-text) 50%, transparent)',
                borderBottom: active ? '2px solid var(--color-accent)' : '2px solid transparent',
              }}
            >
              {t.label}
            </button>
          )
        })}
      </nav>
      <span style={{ fontSize: 13, whiteSpace: 'nowrap', color: 'color-mix(in srgb, var(--color-text) 75%, transparent)' }}>
        {clock} KST
      </span>
    </header>
  )
}

function Kpi({ label, value, color, sub }: { label: string; value: string; color?: string; sub?: string }) {
  return (
    <div style={{ flex: 'none' }}>
      <div style={{ fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--color-accent)' }}>
        {label}
      </div>
      <div style={{ fontSize: 17, fontWeight: 600, lineHeight: 1.35, color: color ?? 'var(--color-text)' }}>{value}</div>
      {sub !== undefined && <div style={{ fontSize: 11, color: DIM_TEXT }}>{sub}</div>}
    </div>
  )
}

function KpiDivider() {
  return <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--color-divider)', flex: 'none' }} />
}

function KpiStrip({ feed }: { feed: Feed }) {
  const btc = feed.spreads.filter((r) => r.sym === 'BTC' && r.status !== 'fail')
  const fwd = btc.length ? Math.max(...btc.map((r) => r.fwd)) : 0
  const rev = btc.length ? Math.max(...btc.map((r) => r.rev)) : 0
  const okN = feed.health.filter((c) => c.state === 'ok').length
  const downNames = feed.health.filter((c) => c.state === 'down').map((c) => c.name)
  const staleN = feed.health.filter((c) => c.state === 'stale').length
  const healthSub = downNames.length
    ? `${downNames.join(', ')} 끊김${staleN ? ` · ${staleN}곳 지연` : ''}`
    : staleN
      ? `${staleN}곳 지연`
      : '전체 정상'
  const symN = new Set(feed.spreads.map((r) => r.sym)).size
  const rateText =
    feed.rate > 0
      ? `₩${feed.rate.toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`
      : '–'
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 24,
        padding: '8px 16px',
        overflowX: 'auto',
        borderBottom: '1px solid var(--color-divider)',
        flex: 'none',
      }}
    >
      <Kpi label="USDT/KRW 암묵환율" value={rateText} />
      <KpiDivider />
      <Kpi label="BTC 김프 · 순방향" value={fmtPct(fwd)} color={pctColor(fwd)} />
      <Kpi label="BTC 김프 · 역방향" value={fmtPct(rev)} color={pctColor(rev)} />
      <KpiDivider />
      <Kpi label="수집 상태" value={`8곳 중 ${okN}곳 정상`} sub={healthSub} />
      <div style={{ marginLeft: 'auto', flex: 'none' }}>
        <Kpi label="추적 페어" value={`${symN}개 코인 · ${feed.spreads.length} 페어`} />
      </div>
    </div>
  )
}

function AppFooter() {
  return (
    <footer
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        padding: '8px 16px',
        borderTop: '1px solid var(--color-divider)',
        fontSize: 11,
        color: DIM_TEXT,
        flex: 'none',
      }}
    >
      <span>암묵환율 = 국내 거래소 USDT/KRW 체결가 기준</span>
      <span>순방향 = 해외 매수 → 국내 매도 · 역방향 = 국내 매수 → 해외 매도</span>
      <span style={{ marginLeft: 'auto' }}>수집 실패 값은 보간 없이 –로 표시</span>
    </footer>
  )
}

/** 숨김 탭은 레이아웃에 참여하지 않는다 — display:none, 언마운트는 하지 않는다. */
function TabPane({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <div style={{ display: active ? 'flex' : 'none', flexDirection: 'column', flex: 1, minHeight: 0 }}>{children}</div>
  )
}

export default function App() {
  const { feed, now } = useFeed()
  // 셸이 공유 피드를 만든 직후 /spreads 1초 폴링 시작 (스펙 003 §3.4)
  useSpreadPolling(feed)
  const [tab, setTab] = useState<TabId>('spread')
  // 스프레드 행 클릭 → 기록 탭으로 피벗할 선택된 심볼 — 초기값 'BTC' (스펙 005 §2)
  const [selSym, setSelSym] = useState<string>('BTC')
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', fontVariantNumeric: 'tabular-nums' }}>
      <Header tab={tab} onTab={setTab} now={now} />
      <KpiStrip feed={feed} />
      <main style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <TabPane active={tab === 'spread'}>
          <SpreadsTab
            feed={feed}
            onPick={(sym) => {
              setSelSym(sym)
              setTab('history')
            }}
          />
        </TabPane>
        <TabPane active={tab === 'history'}>
          <HistoryTab feed={feed} now={now} selSym={selSym} onSelect={setSelSym} />
        </TabPane>
        <TabPane active={tab === 'gap'}>
          <GapTab feed={feed} now={now} />
        </TabPane>
        <TabPane active={tab === 'pp'}>
          <PpTab feed={feed} />
        </TabPane>
        <TabPane active={tab === 'health'}>
          <HealthTab feed={feed} now={now} />
        </TabPane>
        <TabPane active={tab === 'flow'}>
          <FlowTab feed={feed} now={now} />
        </TabPane>
      </main>
      {/* flow 탭에서는 탭 자체 푸터로 대체 (§3.5) */}
      {tab !== 'flow' && <AppFooter />}
    </div>
  )
}
