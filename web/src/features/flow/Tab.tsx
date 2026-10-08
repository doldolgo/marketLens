// 입출금 레이더 탭 — 업비트 이더리움 ERC-20 입출금 실데이터 (스펙 050 §3.8). 위 = 코인별 순유입 표, 아래 = 최근 전송 표.
// 두 경로를 탭이 보이는 동안만 5초마다 부른다(api.ts). 실패하면 직전 표를 두고 바 1 우측에 HTTP 상태를 적는다.
import { useState, type CSSProperties } from 'react'
import { fmtAgo, fmtKrw, fmtQty, fmtTime } from '../../shared/format'
import { Empty, Pill, Seg, bar, card, gridHead, gridRow, headCell, kicker, searchInput, segOpt, type PillTone } from '../../shared/ui'
import { oneOf, useUrlState } from '../../shared/urlState'
import { useNetflow, useRecent } from './api'
import type { FlowDir, FlowFeed, FlowWindow, NetflowRow, RecentRow } from './types'

const WINDOWS: [FlowWindow, string][] = [['1h', '1시간'], ['6h', '6시간'], ['24h', '24시간']]
const DIRS: [FlowDir, string][] = [['all', '전체'], ['in', '입금'], ['out', '출금']]

/** 원화 강조 기준 — 1억 이상은 accent 굵게 (§3.8). */
const KRW_BIG = 100_000_000

/** 블록 번호·집합 크기 콤마 표기 — 로캘을 주는 toLocaleString 은 부를 때마다 Intl 객체를 만들므로 한 번만 만든다. */
const NF_INT = new Intl.NumberFormat('ko-KR')

const NET_COLS = '100px 1fr 1fr 150px 150px 90px'
const NET_MIN = 860
const RECENT_COLS = '92px 60px 72px 1fr 140px 130px 130px 170px'
const RECENT_MIN = 1020

const cell: CSSProperties = { padding: '0 8px', fontSize: 12, fontVariantNumeric: 'tabular-nums' }
const right: CSSProperties = { ...cell, textAlign: 'right' }
const mono: CSSProperties = { ...cell, fontFamily: 'ui-monospace, monospace', fontSize: 11.5, color: 'var(--color-neutral-300)' }
const dim: CSSProperties = { color: 'var(--color-neutral-600)' }
/** 표 안의 글자 버튼(코인 클릭 → 검색값). */
const textBtn: CSSProperties = {
  justifySelf: 'start', appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 12.5, fontWeight: 500,
  padding: '0 8px', cursor: 'pointer', color: 'var(--color-text)',
}

/** 주소·tx 해시 축약 `앞6…뒤4` — 전체는 title 로 (§3.8). */
function shortHex(s: string): string {
  return `${s.slice(0, 6)}…${s.slice(-4)}`
}

/** 순유입 색 — 양수 accent, 음수 회색, 0 은 본문색 (§3.8). */
function netColor(v: number): string {
  if (v > 0) return 'var(--color-accent-300)'
  if (v < 0) return 'var(--color-neutral-400)'
  return 'var(--color-text)'
}

/** 부호 붙은 수량 — 양수만 `+`, 음수는 포맷이 `-` 를 단다. */
function signedQty(v: number): string {
  if (v > 0) return `+${fmtQty(v)}`
  return fmtQty(v)
}

/** 입금·출금 칸 `건수 · 수량` — 0건이면 `–` (창 안에 그 방향 전송이 없다는 뜻을 숫자 0 보다 또렷하게). */
function countCell(n: number, amount: number): string {
  if (n === 0) return '–'
  return `${n}건 · ${fmtQty(amount)}`
}

function dirLabel(d: 'in' | 'out'): string {
  if (d === 'in') return '입금'
  return '출금'
}

/** 방향 칩 — 입금 accent, 출금 회색 (§3.8). */
function dirTone(d: 'in' | 'out'): PillTone {
  if (d === 'in') return 'accent'
  return 'neutral'
}

/** 원화 칸 — null 은 `–` 흐리게, 1억 이상 accent 굵게 (§3.8). */
function krwStyle(krw: number | null): CSSProperties {
  if (krw === null) return { ...right, ...dim }
  if (krw >= KRW_BIG) return { ...right, color: 'var(--color-accent-300)', fontWeight: 600 }
  return right
}

function krwText(krw: number | null): string {
  if (krw === null) return '–'
  return fmtKrw(krw)
}

/** 순유입 원화 — 부호 붙여 순유입 수량과 같은 읽기. */
function netKrwText(krw: number | null): string {
  if (krw === null) return '–'
  if (krw > 0) return `+${fmtKrw(krw)}`
  return fmtKrw(krw)
}

/** 순유입 원화 칸 스타일 — null 만 흐리게 (§3.8). */
function netKrwStyle(krw: number | null): CSSProperties {
  if (krw === null) return { ...right, ...dim }
  return right
}

function winLabel(win: FlowWindow): string {
  const found = WINDOWS.find(([id]) => id === win)
  if (found === undefined) return win
  return found[1]
}

function countText(n: number | undefined): string {
  if (n === undefined) return '–'
  return NF_INT.format(n)
}

/** 검색값이 있으면 그 코인만 — 순유입 표는 서버가 전 코인을 주므로 화면에서 거른다(최근 전송 표는 서버 `symbol` 로 거른다). */
function filterNet(rows: NetflowRow[], sym: string): NetflowRow[] {
  if (!sym) return rows
  return rows.filter((r) => r.symbol === sym)
}

/** 바 1 우측 피드 상태 — `블록 26,147,699 · 3초 전`, 끊기면 warn 색 `피드 끊김` (§3.8). 첫 응답 전엔 비운다. */
function FeedStatus({ feed }: { feed: FlowFeed | undefined }) {
  if (feed === undefined) return null
  if (!feed.connected || feed.lastBlock === null || feed.lagSec === null) {
    return <span style={{ fontSize: 12, color: 'var(--color-warn)' }}>피드 끊김</span>
  }
  return (
    <span style={{ fontSize: 12, color: 'var(--color-neutral-400)', fontVariantNumeric: 'tabular-nums' }}>
      블록 {NF_INT.format(feed.lastBlock)} · {fmtAgo(feed.lagSec)}
    </span>
  )
}

/** 표 카드 안의 소제목 줄. */
function TableTitle({ children }: { children: string }) {
  return <div style={{ ...kicker, padding: 'var(--space-4) var(--space-6) var(--space-2)' }}>{children}</div>
}

function NetRow({ r, asOf, onCoin }: { r: NetflowRow; asOf: number; onCoin: (sym: string) => void }) {
  return (
    <div className="hv-row4" style={gridRow(NET_COLS, { height: 36, rule: 6 })}>
      <button className="hv-txt" onClick={() => onCoin(r.symbol)} style={textBtn}>{r.symbol}</button>
      <span style={{ ...cell, color: 'var(--color-neutral-300)' }}>{countCell(r.inCount, r.inAmount)}</span>
      <span style={{ ...cell, color: 'var(--color-neutral-300)' }}>{countCell(r.outCount, r.outAmount)}</span>
      <span style={{ ...right, fontSize: 12.5, color: netColor(r.netAmount) }}>{signedQty(r.netAmount)}</span>
      <span style={netKrwStyle(r.netKrw)}>{netKrwText(r.netKrw)}</span>
      <span style={{ ...right, color: 'var(--color-neutral-500)' }}>{fmtAgo(Math.max(0, asOf - r.lastTs))}</span>
    </div>
  )
}

function RecentRowView({ r }: { r: RecentRow }) {
  return (
    <div className="hv-row4" style={gridRow(RECENT_COLS, { height: 36, rule: 6 })}>
      <span style={{ ...cell, padding: '0 8px 0 0', fontSize: 11.5, color: 'var(--color-neutral-500)' }}>{fmtTime(r.ts * 1000)}</span>
      <Pill tone={dirTone(r.dir)} style={{ margin: '0 8px' }}>{dirLabel(r.dir)}</Pill>
      <span style={{ ...cell, fontSize: 12.5, fontWeight: 500 }}>{r.symbol}</span>
      <span style={{ ...right, color: 'var(--color-neutral-300)' }}>{fmtQty(r.amount)}</span>
      <span style={krwStyle(r.krw)}>{krwText(r.krw)}</span>
      <span style={mono} title={r.addr}>{shortHex(r.addr)}</span>
      <span style={mono} title={r.counterparty}>{shortHex(r.counterparty)}</span>
      <span style={{ ...cell, display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        {/* 새 창 — 열어 둔 대시보드의 WebSocket 을 끊지 않게 */}
        <a href={`https://etherscan.io/tx/${r.txHash}`} target="_blank" rel="noopener" className="hv-txt" title={r.txHash}
          style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11.5, color: 'var(--color-neutral-400)', textDecoration: 'none' }}>
          {shortHex(r.txHash)}
        </a>
        {!r.confirmed && <Pill tone="neutral">확정 전</Pill>}
      </span>
    </div>
  )
}

export default function FlowTab({ active }: { active: boolean }) {
  // 창·방향은 URL 쿼리(f.*)에 실려 새로고침해도 같은 화면 (002 §3.5). 검색은 URL 에 싣지 않는다(033 — Clarity 가 주소를 통째로 싣는다)
  const [win, setWin] = useUrlState<FlowWindow>('f.win', '1h', oneOf(['1h', '6h', '24h']))
  const [dir, setDir] = useUrlState<FlowDir>('f.dir', 'all', oneOf(['all', 'in', 'out']))
  // q = 입력 중 글자, sym = Enter 로 적용된 코인(대문자) — 두 표 모두 그 코인만
  const [q, setQ] = useState('')
  const [sym, setSym] = useState('')
  const netflow = useNetflow(win, active)
  const recent = useRecent(dir, sym, active)

  const feed = netflow.data?.feed
  // 둘 중 하나라도 실패하면 그 상태를 적는다 — 0 도 실패(네트워크)라 ?? 로 고른다
  const errorStatus = netflow.errorStatus ?? recent.errorStatus
  const netRows = filterNet(netflow.data?.rows ?? [], sym)
  // "최근" 칸의 기준 시각 — 서버 응답 시각. 첫 응답 전엔 행이 없어 쓰이지 않는다
  const asOf = netflow.data?.asOf ?? 0
  const recentRows = recent.data?.rows ?? []

  function applySearch(s: string) {
    const upper = s.trim().toUpperCase()
    setQ(upper)
    setSym(upper)
  }

  return (
    <>
      {/* 바 1: 창 · 방향 · 코인 검색 · 우측 오류 문구 + 피드 상태 */}
      <div data-area="filters" style={bar}>
        <Seg opts={WINDOWS.map(([id, l]) => segOpt(l, win === id, () => setWin(id)))} />
        <Seg opts={DIRS.map(([id, l]) => segOpt(l, dir === id, () => setDir(id)))} />
        <input className="input" placeholder="코인 심볼 입력 후 Enter" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') applySearch(q) }}
          style={{ ...searchInput, width: 200 }} />
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 'var(--space-4)' }}>
          {errorStatus !== null && <span style={{ fontSize: 12, color: 'var(--color-warn)' }}>불러오지 못했습니다 (HTTP {errorStatus})</span>}
          <FeedStatus feed={feed} />
        </span>
      </div>

      {/* 본문 — 표 둘이라 TableFrame 대신 자체 스크롤 영역에 카드 둘 */}
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        <div style={{ maxWidth: 1240, margin: '0 auto', padding: 'var(--space-4) var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>

          {/* 순유입 표 — data-area 는 052 화면 영역 이용 통계의 영역 */}
          <div data-area="table" style={{ ...card, padding: '0 0 var(--space-2)' }}>
            <TableTitle>{`코인별 순유입 · 최근 ${winLabel(win)} · ${netRows.length}종`}</TableTitle>
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: NET_MIN }}>
                <div style={gridHead(NET_COLS)}>
                  <span style={headCell}>코인</span>
                  <span style={headCell}>입금</span>
                  <span style={headCell}>출금</span>
                  <span style={{ ...headCell, textAlign: 'right' }}>순유입</span>
                  <span style={{ ...headCell, textAlign: 'right' }}>원화</span>
                  <span style={{ ...headCell, textAlign: 'right' }}>최근</span>
                </div>
                {netRows.map((r) => <NetRow key={r.symbol} r={r} asOf={asOf} onCoin={applySearch} />)}
              </div>
            </div>
            {netflow.data === null && <Empty size={12}>조회 중…</Empty>}
            {netflow.data !== null && netRows.length === 0 && <Empty size={12}>이 창에 업비트 ERC-20 입출금 없음</Empty>}
          </div>

          {/* 최근 전송 표 */}
          <div data-area="table-2" style={{ ...card, padding: '0 0 var(--space-2)' }}>
            <TableTitle>최근 전송 · 24시간 안 최신순 · 최대 100행</TableTitle>
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: RECENT_MIN }}>
                <div style={gridHead(RECENT_COLS)}>
                  <span style={{ padding: '6px 8px 6px 0' }}>시각</span>
                  <span style={headCell}>방향</span>
                  <span style={headCell}>코인</span>
                  <span style={{ ...headCell, textAlign: 'right' }}>수량</span>
                  <span style={{ ...headCell, textAlign: 'right' }}>원화</span>
                  <span style={headCell}>주소</span>
                  <span style={headCell}>상대</span>
                  <span style={headCell}>tx</span>
                </div>
                {recentRows.map((r, i) => <RecentRowView key={i} r={r} />)}
              </div>
            </div>
            {recent.data === null && <Empty size={12}>조회 중…</Empty>}
            {recent.data !== null && recentRows.length === 0 && <Empty size={12}>해당 조건의 전송 없음</Empty>}
          </div>

        </div>
      </div>

      {/* 전용 푸터 — flow 탭에서는 셸 푸터를 대체한다 (§3.8) */}
      <footer style={{ flex: 'none', display: 'flex', gap: 'var(--space-6)', padding: 'var(--space-2) var(--space-6)', borderTop: '1px solid var(--color-divider)', fontSize: 11, color: 'var(--color-neutral-600)', flexWrap: 'wrap' }}>
        <span>업비트 · 이더리움 네트워크 ERC-20 {countText(feed?.contracts)}종 · 블록 2개 확정 전은 '확정 전'</span>
        <span style={{ marginLeft: 'auto', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
          입금주소 {countText(feed?.depositAddrs)} · 핫월렛 {countText(feed?.hotWallets)} · 자동 확장
        </span>
      </footer>
    </>
  )
}
