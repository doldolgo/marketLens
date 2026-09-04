// 셸 레이아웃 — 헤더 + KPI 스트립 + 탭 6개 + 푸터 (스펙 002 §3.5, 구조는 docs/design/reference/App.tsx).
// 탭은 언마운트하지 않고 숨긴다: 검색어·필터·드릴다운 상태가 전환 후에도 유지되어야 하기 때문.
import { useState, type ReactNode } from 'react'
import FlowTab from './features/flow/Tab'
import GapTab from './features/gap/Tab'
import { useHealthPolling } from './features/health/api'
import HealthTab from './features/health/Tab'
import HistoryTab from './features/history/Tab'
import PpTab from './features/pp/Tab'
import { NOTIONALS, useSpreadPolling } from './features/spreads/api'
import SpreadsTab from './features/spreads/Tab'
import { useFeed } from './shared/feed'
import { exName, fmtPct, pctColor } from './shared/format'
import { kicker, vDivider } from './shared/ui'

type TabId = 'spread' | 'history' | 'gap' | 'pp' | 'health' | 'flow'

/** 탭 id·라벨·순서 고정 (§3.5). */
const TABS: [TabId, string][] = [
  ['spread', '실시간 스프레드'], ['history', '기록/통계'], ['gap', '선물–현물 갭'],
  ['pp', '선선갭'], ['health', '수집 상태'], ['flow', '입출금 레이더'],
]

export default function App() {
  const { feed, now } = useFeed()
  // 체결 규모는 스프레드 탭이 고르고 폴링이 쿼리로 보낸다 — 둘이 같은 값을 봐야 해서 셸이 든다 (003 §3.4)
  const [notional, setNotional] = useState<number>(NOTIONALS[0])
  // 셸이 공유 피드를 만든 직후 /spreads 1초 폴링 시작 (스펙 003 §3.4), 그 옆에서 /health/collect 5초 폴링 (011 §3.6)
  useSpreadPolling(feed, notional)
  useHealthPolling(feed)
  const [tab, setTab] = useState<TabId>('spread')
  // 스프레드 행 클릭 → 기록 탭으로 피벗할 선택된 심볼 — 초기값 'BTC' (스펙 005 §2)
  const [selSym, setSelSym] = useState<string>('BTC')

  // 수집 상태 KPI — /health/collect 마지막 응답 기준, 첫 응답 전엔 – (011 §3.7)
  const exs = feed.health?.exchanges ?? []
  const okN = exs.filter((c) => c.state === 'ok').length
  const downNames = exs.filter((c) => c.state === 'down').map((c) => exName(c.exchange))
  const staleN = exs.filter((c) => c.state === 'stale').length
  const healthValue = feed.health ? `${exs.length}곳 중 ${okN}곳 정상` : '–'
  const healthSub = !feed.health
    ? '수집 상태 조회 전'
    : downNames.length
      ? `${downNames.join(', ')} 끊김${staleN ? ` · ${staleN}곳 지연` : ''}`
      : staleN
        ? `${staleN}곳 지연`
        : '전체 정상'

  // BTC 김프 KPI — fail 제외 전 페어 중 최고값
  const btcLive = feed.spreads.filter((r) => r.sym === 'BTC' && r.status !== 'fail')
  const btcFwd = btcLive.length ? Math.max(...btcLive.map((r) => r.fwd)) : 0
  const btcRev = btcLive.length ? Math.max(...btcLive.map((r) => r.rev)) : 0
  // rate 0 = 백엔드 첫 폴링 전. 숫자를 지어내지 않고 '–' 로 둔다.
  const hasRate = feed.rate > 0
  const coinCount = new Set(feed.spreads.map((r) => r.sym)).size
  const clock = new Date(now).toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })

  // 숨김 탭은 레이아웃에 참여하지 않는다 — display:none, 보이는 탭은 contents 로 셸의 세로 flex 에 직접 참여.
  const wrap = (id: TabId, node: ReactNode) => (
    <div key={id} style={{ display: tab === id ? 'contents' : 'none' }}>{node}</div>
  )

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--color-bg)', color: 'var(--color-text)', fontFamily: 'var(--font-body)', fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>

      {/* 헤더: 타이틀 + LIVE + 탭 + 시계 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)', padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-divider)', height: 52, flex: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-3)' }}>
          <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 16, letterSpacing: '-0.01em' }}>트레이딩룸</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--color-neutral-500)' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-ok)', animation: 'tr-pulse 1.6s ease-in-out infinite' }} />실시간 수집 중
          </span>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', alignSelf: 'stretch', alignItems: 'stretch' }}>
          {TABS.map(([id, text]) => (
            <button key={id} onClick={() => setTab(id)} className="hv-txt"
              style={{
                appearance: 'none', background: 'none', border: 'none',
                borderBottom: `2px solid ${tab === id ? 'var(--color-accent)' : 'transparent'}`,
                color: tab === id ? 'var(--color-text)' : 'var(--color-neutral-500)',
                font: 'inherit', fontSize: 13, padding: '0 var(--space-4)', cursor: 'pointer',
              }}>
              {text}
            </button>
          ))}
        </div>
        <div style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', fontSize: 12 }}>
          {clock} KST
        </div>
      </div>

      {/* KPI 스트립 — 카드가 아닌 flex 스트립, 블록 사이 세로 그라디언트 선 */}
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
            <span>{healthValue}</span>
            <span style={{ fontSize: 11, color: 'var(--color-neutral-500)' }}>{healthSub}</span>
          </div>
        </div>
        <div style={{ flex: 'none', marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ ...kicker, marginBottom: 2 }}>추적 페어</div>
          <div style={{ fontSize: 14, fontVariantNumeric: 'tabular-nums' }}>{coinCount}개 코인 · {feed.spreads.length} 페어</div>
        </div>
      </div>

      {wrap('spread', <SpreadsTab feed={feed} notional={notional} onNotional={setNotional} onPick={(sym) => { setSelSym(sym); setTab('history') }} />)}
      {wrap('history', <HistoryTab feed={feed} now={now} selSym={selSym} onSelect={setSelSym} />)}
      {wrap('gap', <GapTab feed={feed} now={now} />)}
      {wrap('pp', <PpTab feed={feed} />)}
      {wrap('health', <HealthTab feed={feed} now={now} />)}
      {wrap('flow', <FlowTab feed={feed} now={now} />)}

      {/* 푸터 — 입출금 레이더 탭은 FlowTab 이 자체 푸터를 그림 */}
      {tab !== 'flow' && (
        <div style={{ flex: 'none', display: 'flex', gap: 'var(--space-6)', padding: 'var(--space-2) var(--space-6)', borderTop: '1px solid var(--color-divider)', fontSize: 11, color: 'var(--color-neutral-600)' }}>
          <span>암묵환율 = 국내 거래소 USDT/KRW 체결가 기준</span>
          <span>순방향 = 해외 매수 → 국내 매도 · 역방향 = 국내 매수 → 해외 매도</span>
          <span style={{ marginLeft: 'auto' }}>수집 실패 값은 보간 없이 –로 표시</span>
        </div>
      )}
    </div>
  )
}
