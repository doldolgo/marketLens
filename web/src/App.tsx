// 셸 레이아웃 — 헤더 + KPI 스트립 + 탭 6개 + 푸터 (스펙 002 §3.5, 구조는 docs/design/reference/App.tsx).
// 탭은 한 번 마운트되면 언마운트하지 않고 숨긴다: 검색어·필터·드릴다운 상태가 전환 후에도 유지되어야 하기 때문.
// 기록 탭만 처음 볼 때 마운트한다 — 그 코드(차트 라이브러리 포함)는 첫 화면 번들에서 빠져 처음 볼 때 받는다.
import { Component, Suspense, lazy, memo, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import FlowTab from './features/flow/Tab'
import GapTab from './features/gap/Tab'
import { useHealthPolling } from './features/health/api'
import HealthTab from './features/health/Tab'
import PpTab from './features/pp/Tab'
import { useSpreadSocket } from './features/spreads/api'
import SpreadsTab from './features/spreads/Tab'
import { CLARITY_READY, clarityEvent, clarityTag } from './shared/clarity'
import { useFeed } from './shared/feed'
import { exName, fmtPct, pctColor } from './shared/format'
import { Empty, kicker, vDivider } from './shared/ui'
import { UrlActive, discardParams, dropParams, oneOf, symbol, useUrlState } from './shared/urlState'

const HistoryTab = lazy(() => import('./features/history/Tab'))

// 033 — 검색어(s.q·g.q·p.q)는 URL 에 싣지 않는다. 옛 링크에 남은 키는 첫 렌더 전(이 모듈이 실행될 때)에 지우고 값은 버린다 —
// Clarity 는 전송마다 그때의 주소를 통째로 싣는다(입력칸을 가려도 주소의 검색어는 간다)
discardParams(['s.q', 'g.q', 'p.q'])

type TabId = 'spread' | 'history' | 'gap' | 'pp' | 'health' | 'flow'

/** 탭 id·라벨·순서 고정 (§3.5). */
const TABS: [TabId, string][] = [
  ['spread', '실시간 스프레드'], ['history', '기록/통계'], ['gap', '선물–현물 갭'],
  ['pp', '선선갭'], ['health', '수집 상태'], ['flow', '입출금 레이더'],
]

// 시계·환율 표기 — 로캘·옵션을 주는 toLocale*String 은 부를 때마다 Intl 객체를 새로 만든다. 셸은 초마다 다시 그리므로 한 번만 만든다(출력 동일).
const KST_CLOCK = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
const RATE_1 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

/**
 * 탭 자리 하나. 숨김 탭은 레이아웃에 참여하지 않는다 — display:none, 보이는 탭은 contents 로 셸의 세로 flex 에 직접 참여.
 * 계속 숨어 있는 탭은 셸의 리렌더(1.5초 tick·매초 delta)에서 다시 그리지 않는다 — 보이게 되는 순간 한 번 그려 최신이 되고,
 * 숨은 동안에도 탭 자기 상태(폴링 결과 등)가 바뀌면 그 탭만 스스로 다시 그린다. UrlActive: 보이는 탭의 상태 키만 URL 에 남긴다 (shared/urlState)
 */
const Pane = memo(function Pane({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <div style={{ display: active ? 'contents' : 'none' }}>
      <UrlActive.Provider value={active}>{children}</UrlActive.Provider>
    </div>
  )
}, (prev, next) => !prev.active && !next.active)

/** 기록 탭 코드를 받지 못하면(배포로 옛 청크가 사라진 뒤 처음 여는 경우 등) 그 탭 자리만 안내하고 셸·다른 탭은 그대로 둔다. */
class TabLoadGuard extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }
  render() {
    if (!this.state.failed) return this.props.children
    return <div style={{ flex: 1 }}><Empty>기록 탭을 불러오지 못했습니다 — 새로고침해 주세요</Empty></div>
  }
}

export default function App() {
  const { feed, now } = useFeed()
  // 탭·선택 심볼은 URL 쿼리(?tab=&sym=)에 실려 새로고침해도 같은 화면 (002 §3.5).
  // URL 은 보이는 화면만 담는다 — sym 은 기록 탭이 활성일 때만 쓴다
  const [tab, setTab] = useUrlState<TabId>('tab', 'spread', oneOf(TABS.map(([id]) => id)))
  // 셸이 공유 피드를 만든 직후 /ws/spreads 구독 시작 (스펙 017 §3.4), 그 옆에서 /health/collect 5초 폴링 (011 §3.6)
  useSpreadSocket(feed)
  useHealthPolling(feed)
  // 스프레드 행 클릭 → 기록 탭으로 피벗할 선택된 심볼 — 초기값 'BTC' (스펙 005 §2). 영문 대문자·숫자 1~20자만 URL 에서 읽고 쓴다 (033)
  const [selSym, setSelSym] = useUrlState<string>('sym', 'BTC', symbol, tab === 'history')
  // 033 — 탭이 바뀐 뒤 남길 Clarity 이벤트(탭 단추 tab_<id>, 행 클릭 pivot_history). tab 태그를 둔 다음에 남긴다
  const clarityNext = useRef<string | null>(null)
  // 고정 참조 — 스프레드 행이 memo 라 이 함수가 렌더마다 바뀌면 행 전부가 다시 그려진다
  const onPick = useCallback((sym: string) => {
    setSelSym(sym)
    clarityNext.current = 'pivot_history'
    setTab('history')
  }, [setSelSym, setTab])
  const pickTab = (id: TabId) => {
    if (id === tab) return
    clarityNext.current = `tab_${id}`
    setTab(id)
  }
  // 기록 탭은 처음 볼 때 마운트하고(?tab=history 로 들어오면 곧바로) 그 뒤로는 내리지 않는다
  const [historySeen, setHistorySeen] = useState(tab === 'history')
  if (tab === 'history' && !historySeen) setHistorySeen(true)
  useEffect(() => {
    // 첫 화면이 기록 탭이 아니면 URL 의 h.* 는 보이지 않는 화면의 키다 — 마운트돼 있었다면 기록 탭이 스스로 뺐을 것이므로 셸이 뺀다.
    // 값은 메모리에 남아 기록 탭을 처음 열 때 초기값이 된다(다른 비활성 탭과 같다)
    if (tab !== 'history') dropParams('h.')
    // 첫 화면 기준 한 번만
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 033 — Clarity 태그·이벤트(동의하지 않은 방문자는 아무것도 안 한다). 이 effect 는 위 useUrlState('tab') 의 URL 쓰기 다음에 돈다 —
  // tab 을 쓰면 Clarity 가 새 페이지를 시작하므로 태그가 새 페이지에 실린다. 첫 화면에는 이벤트를 남기지 않는다
  const clarityTabRef = useRef<TabId | null>(null)
  useEffect(() => {
    if (clarityTabRef.current !== tab) {
      clarityTabRef.current = tab
      clarityTag('tab', tab)
      if (clarityNext.current) clarityEvent(clarityNext.current)
    }
    clarityNext.current = null
    if (tab === 'history') clarityTag('sym', selSym)
  }, [tab, selSym])
  // clarity.js 가 문서 중간에(동의 창의 저장·다른 탭의 동의로) 대기열을 만들면 지금 보이는 화면의 태그를 한 번 둔다
  useEffect(() => {
    const onReady = () => {
      clarityTag('tab', tab)
      if (tab === 'history') clarityTag('sym', selSym)
    }
    window.addEventListener(CLARITY_READY, onReady)
    return () => window.removeEventListener(CLARITY_READY, onReady)
  }, [tab, selSym])

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
  const clock = KST_CLOCK.format(now)

  const wrap = (id: TabId, node: ReactNode) => <Pane key={id} active={tab === id}>{node}</Pane>

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--color-bg)', color: 'var(--color-text)', fontFamily: 'var(--font-body)', fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>

      {/* 헤더: 타이틀 + LIVE + 탭 + 시계 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)', padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-divider)', height: 52, flex: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-3)' }}>
          <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 16, letterSpacing: '-0.01em' }}>Kimp<span style={{ color: 'var(--color-accent)' }}>Track</span></span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--color-neutral-500)' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-ok)', animation: 'tr-pulse 1.6s ease-in-out infinite' }} />실시간 수집 중
          </span>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', alignSelf: 'stretch', alignItems: 'stretch' }}>
          {TABS.map(([id, text]) => (
            <button key={id} onClick={() => pickTab(id)} className="hv-txt"
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
        {/* 032 — 방침은 새 탭으로. 열어 둔 대시보드의 WebSocket 을 끊지 않게. 푸터가 아닌 이유: 입출금 레이더 탭은 셸 푸터를 그리지 않는다 */}
        <a href="/privacy" target="_blank" rel="noopener" className="hv-txt"
          style={{ marginLeft: 'auto', color: 'var(--color-neutral-500)', fontSize: 12, textDecoration: 'none' }}>
          개인정보 처리방침
        </a>
        {/* 033 — 동의 창은 한 번 고르면 다시 뜨지 않으므로 바꾸러 가는 길을 늘 둔다(철회가 동의보다 어렵지 않게). 방침과 같은 모양·새 탭 */}
        <a href="/privacy#consent" target="_blank" rel="noopener" className="hv-txt"
          style={{ color: 'var(--color-neutral-500)', fontSize: 12, textDecoration: 'none' }}>
          화면 분석 설정
        </a>
        <div style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', fontSize: 12 }}>
          {clock} KST
        </div>
      </div>

      {/* KPI 스트립 — 카드가 아닌 flex 스트립, 블록 사이 세로 그라디언트 선 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)', padding: 'var(--space-4) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', overflowX: 'auto' }}>
        <div style={{ flex: 'none' }}>
          <div style={{ ...kicker, marginBottom: 2 }}>USDT/KRW 암묵환율</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-3)' }}>
            <span style={{ fontSize: 18, fontVariantNumeric: 'tabular-nums' }}>
              {hasRate ? '₩' + RATE_1.format(feed.rate) : '–'}
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

      {wrap('spread', <SpreadsTab feed={feed} onPick={onPick} />)}
      {historySeen && wrap('history', (
        <TabLoadGuard>
          <Suspense fallback={null}>
            <HistoryTab now={now} selSym={selSym} onSelect={setSelSym} spreads={feed.spreads} active={tab === 'history'} />
          </Suspense>
        </TabLoadGuard>
      ))}
      {wrap('gap', <GapTab feed={feed} now={now} />)}
      {wrap('pp', <PpTab feed={feed} />)}
      {wrap('health', <HealthTab feed={feed} now={now} />)}
      {wrap('flow', <FlowTab active={tab === 'flow'} />)}

      {/* 푸터 — 입출금 레이더 탭은 FlowTab 이 자체 푸터를 그림 */}
      {tab !== 'flow' && (
        <div style={{ flex: 'none', display: 'flex', gap: 'var(--space-6)', padding: 'var(--space-2) var(--space-6)', borderTop: '1px solid var(--color-divider)', fontSize: 11, color: 'var(--color-neutral-600)' }}>
          <span>암묵환율 = 국내 거래소 USDT/KRW 최우선 매도호가(ask) 기준</span>
          <span>순방향 = 해외 매수 → 국내 매도 · 역방향 = 국내 매수 → 해외 매도</span>
          <span style={{ marginLeft: 'auto' }}>수집 실패 값은 보간 없이 –로 표시</span>
        </div>
      )}
    </div>
  )
}
