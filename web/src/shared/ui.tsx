// 공용 UI 조각 — docs/design/reference/ui.tsx 이식 + 탭마다 중복돼 있던
// kicker·card·세로선·grid 표·거래소/상태 배지를 여기 한 번만 둔다 (스펙 002 §3.2, §3.6).
// 스타일은 인라인 style 객체 + CSS 변수만. 흐린 글자는 neutral-500/600 램프 — color-mix 재해석 금지.
// 폰 조각(필터 접기·정렬 줄·카드 행)과 좁은 화면 표 틀은 072 — 구간은 shared/viewport 가 정한다.
import { useId, type CSSProperties, type ReactNode } from 'react'
import { TAB_BAR_H, useViewport } from './viewport'

/** 브랜드 마크 — 김치 항아리 속 스프레드(public/favicon.svg 의 32 격자에서 바탕 타일을 뺀 모양). 색은 currentColor,
 *  항아리 안의 선·점은 마스크로 뚫어 어떤 바탕에서도 보인다. 마스크 id 는 넓은·좁은 헤더가 함께 그려도 겹치지 않게 useId. */
export function BrandMark({ size = 18 }: { size?: number }) {
  const id = useId()
  return (
    <svg width={size} height={size} viewBox="5 5 22 22" aria-hidden="true" focusable="false" style={{ flex: 'none', color: 'var(--color-accent)' }}>
      <mask id={id}>
        <rect x="5" y="5" width="22" height="22" fill="#fff" />
        <g stroke="#000" fill="none" strokeLinecap="round">
          <line x1="14.6" y1="21.2" x2="24" y2="21.2" strokeWidth="1.3" strokeDasharray="1.5 1.7" />
          <line x1="11.2" y1="21.2" x2="20.6" y2="14.2" strokeWidth="2.1" />
        </g>
        <circle cx="11.2" cy="21.2" r="2.3" />
        <circle cx="20.6" cy="14.2" r="2.3" />
      </mask>
      <g fill="currentColor" mask={`url(#${id})`}>
        <rect x="10.6" y="5.6" width="10.8" height="2.8" rx="1.4" />
        <path d="M12.2 9.6 L 19.8 9.6 C 25.6 11 27 16.4 25.4 21.4 C 24.2 25.2 20.6 26.8 16 26.8 C 11.4 26.8 7.8 25.2 6.6 21.4 C 5 16.4 6.4 11 12.2 9.6 Z" />
      </g>
    </svg>
  )
}

/** 10px 대문자 소제목 (KPI 라벨·카드 제목). */
export const kicker: CSSProperties = {
  fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--color-neutral-600)',
}

/** 카드 = surface + radius-md + shadow-sm. */
export const card: CSSProperties = {
  background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-sm)',
}

/** 탭 상단 컨트롤 바 — 고정 높이, 줄바꿈 허용. */
export const bar: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 'var(--space-4)', padding: 'var(--space-3) var(--space-6)',
  borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap',
}

/** 바 안의 컨트롤 라벨(12px 보조). */
export const label: CSSProperties = { fontSize: 12, color: 'var(--color-neutral-400)' }
/** 바 안의 안내 문구(11px 캡션). */
export const hint: CSSProperties = { fontSize: 11, color: 'var(--color-neutral-600)' }
/** 바 우측 건수 `N / M 표시`. */
export const count: CSSProperties = {
  marginLeft: 'auto', fontSize: 12, color: 'var(--color-neutral-600)', fontVariantNumeric: 'tabular-nums',
}
/** 검색 입력 — 전역 .input 클래스 위에 크기만 덮는다. */
export const searchInput: CSSProperties = { width: 150, fontSize: 12, padding: '5px 10px' }
/** 폰이 아닌 배치의 검색 칸을 터치 기기(가로 폰·태블릿)에서 — 16px 아래 글자는 iOS 가 누를 때 화면을 확대한다 (072 §3.9). */
export const touchSearch: CSSProperties = { ...searchInput, fontSize: 16, padding: '3px 10px' }
/** 심볼 입력칸 공통 속성 — 휴대폰 자판이 첫 글자를 대문자로 바꾸거나 자동 수정으로 심볼을 사전 낱말로 바꾸지 않게 (072 §3.9). */
export const SYMBOL_INPUT = { autoCapitalize: 'characters', autoCorrect: 'off', autoComplete: 'off', spellCheck: false } as const

/** KPI 스트립 블록 사이 세로 그라디언트 선. */
export const vDivider = (
  <div style={{ width: 1, alignSelf: 'stretch', background: 'linear-gradient(to bottom, transparent, var(--color-divider), transparent)', flex: 'none' }} />
)

// ── 조작 조각 3종 (§3.6) ─────────────────────────────────────────────────
// el — 061 화면 이용 통계의 요소 열쇠(data-el). 모양·동작과 무관하고, 주지 않으면 속성이 없다
export interface SegOpt { label: string; onClick: () => void; bg: string; color: string; el?: string }

/** 선택 상태 스타일을 만들어주는 헬퍼 — 활성은 neutral-900 배경 + accent-300 글자. */
export const segOpt = (label: string, active: boolean, onClick: () => void, el?: string): SegOpt => ({
  label, onClick,
  bg: active ? 'var(--color-neutral-900)' : 'transparent',
  color: active ? 'var(--color-accent-300)' : 'var(--color-neutral-500)',
  el,
})

/** 테두리 안에 버튼이 나란히 붙는 분절 버튼 그룹. 글자는 꺾지 않는다 — 자리가 모자라면 묶음 안에서 가로로 스크롤된다 (072 §3.4). */
export function Seg({ opts, pad = '5px 12px' }: { opts: SegOpt[]; pad?: string }) {
  return (
    <div style={{ display: 'flex', maxWidth: '100%', border: '1px solid var(--color-neutral-800)', borderRadius: 'var(--radius-sm)', overflowX: 'auto', overflowY: 'hidden', scrollbarWidth: 'none' }}>
      {opts.map((o) => (
        <button key={o.label} onClick={o.onClick} className="hv-txt" data-el={o.el}
          style={{ appearance: 'none', border: 'none', font: 'inherit', fontSize: 12, padding: pad, cursor: 'pointer', background: o.bg, color: o.color, flex: 'none', whiteSpace: 'nowrap' }}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** 숫자 입력 — 라벨 + 입력 + 단위(기본 %). 비숫자 입력은 0. 폰·터치 기기는 16px — 그보다 작으면 iOS 가 누를 때 화면을 확대한다 (072 §3.9). */
export function NumField({ label, value, step, onChange, unit = '%', el }: {
  label: string; value: number; step: number; onChange: (v: number) => void; unit?: string; el?: string
}) {
  const { phone, touch } = useViewport()
  return (
    <label data-el={el} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', fontSize: 12, color: 'var(--color-neutral-400)' }}>
      {label}
      <input className="input" type="number" step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        style={{ width: phone || touch ? 72 : 62, fontSize: phone || touch ? 16 : 12, padding: phone || touch ? '4px 8px' : '5px 8px', fontVariantNumeric: 'tabular-nums', textAlign: 'right' }} />
      <span>{unit}</span>
    </label>
  )
}

/** "임계 초과만" 류의 토글 버튼 — on 은 accent 테두리 + 옅은 accent 배경. */
export function ToggleBtn({ on, label, onClick, el }: { on: boolean; label: string; onClick: () => void; el?: string }) {
  return (
    <button onClick={onClick} className="hv-bd" data-el={el}
      style={{
        appearance: 'none', font: 'inherit', fontSize: 12, padding: '5px 12px', cursor: 'pointer',
        borderRadius: 'var(--radius-sm)',
        border: `1px solid ${on ? 'var(--color-accent)' : 'var(--color-neutral-800)'}`,
        background: on ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)' : 'transparent',
        color: on ? 'var(--color-accent-300)' : 'var(--color-neutral-400)',
      }}>
      {label}
    </button>
  )
}

// ── 배지 ─────────────────────────────────────────────────────────────────
/** 거래소 칩 — 기본은 중립 테두리, accent 는 선물/상대편 거래소 강조. small 은 2줄 셀용 소형(선선갭). */
export function exTag(accent = false, small = false): CSSProperties {
  return {
    ...(small ? { fontSize: 9.5, padding: '2px 6px' } : { fontSize: 10, letterSpacing: '0.04em', padding: '2px 7px' }),
    borderRadius: 'var(--radius-sm)',
    border: `1px solid ${accent ? 'var(--color-accent-800)' : 'var(--color-neutral-800)'}`,
    color: accent ? 'var(--color-accent-300)' : 'var(--color-neutral-400)',
    background: 'var(--color-surface)', whiteSpace: 'nowrap',
  }
}

export type PillTone = 'accent' | 'neutral' | 'warn'
const PILL: Record<PillTone, { border: string; color: string }> = {
  accent: { border: 'var(--color-accent-800)', color: 'var(--color-accent-300)' },
  neutral: { border: 'var(--color-neutral-800)', color: 'var(--color-neutral-400)' },
  warn: { border: '#8a5a42', color: 'var(--color-warn)' },
}

/** 상태 배지(방향·유형·상태) — 10px 테두리 칩. */
export function Pill({ tone, children, style }: { tone: PillTone; children: ReactNode; style?: CSSProperties }) {
  return (
    <span style={{
      justifySelf: 'start', fontSize: 10, padding: '2px 7px', borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap',
      border: `1px solid ${PILL[tone].border}`, color: PILL[tone].color, ...style,
    }}>
      {children}
    </span>
  )
}

// ── grid 표 (§3.2) ───────────────────────────────────────────────────────
/**
 * 표 바깥 프레임 — 가운데 정렬, 최대 1080, 좌우 선, 자체 스크롤.
 * 화면이 minWidth 보다 좁으면 표 상자 안에서 가로로 스크롤되고 왼쪽부터 보인다(072 §3.6 — minWidth 를 상자 자신에 걸면
 * 가운데 정렬 때문에 왼쪽 열이 잘려 닿을 수 없었다). 폰은 열 없는 카드 행이라 틀 없이 문서 흐름에 놓인다(§3.5).
 * 문서 스크롤 구간에서는 세로 스크롤을 문서에 맡기고 가로만 상자 안에서 한다.
 */
export function TableFrame({ minWidth, area, children }: { minWidth: number; area?: string; children: ReactNode }) {
  const { phone, docScroll } = useViewport()
  // area — 052 화면 영역 이용 통계의 영역 id(data-area). 표 상자 자체에 단다 — 어느 폭에서나 같은 상자 하나
  const box = (
    <div data-area={area} style={phone ? undefined : { maxWidth: 1080, margin: '0 auto', flex: 1, minWidth: 0, overflowX: 'auto', overflowY: docScroll ? 'hidden' : 'auto', borderLeft: '1px solid var(--color-divider)', borderRight: '1px solid var(--color-divider)' }}>
      {phone ? children : <div style={{ minWidth }}>{children}</div>}
    </div>
  )
  if (phone) return box
  return (
    <div style={docScroll ? { padding: '0 var(--space-6)' } : { flex: 1, minHeight: 0, display: 'flex', justifyContent: 'center', padding: '0 var(--space-6)' }}>
      {box}
    </div>
  )
}

/** 탭 본문의 스크롤 상자 — 넓은 화면은 남은 높이를 채우고 자체 스크롤, 문서 스크롤 구간은 문서에 맡긴다 (072 §3.1). */
export function bodyScroll(docScroll: boolean): CSSProperties {
  return docScroll ? {} : { flex: 1, overflow: 'auto', minHeight: 0 }
}

/** 정렬 불가 헤더 행(로그·레이더 표) — sticky 없이 10.5px uppercase. */
export function gridHead(cols: string): CSSProperties {
  return {
    display: 'grid', gridTemplateColumns: cols, padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)',
    fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--color-neutral-600)',
  }
}
/** 헤더 셀 여백. */
export const headCell: CSSProperties = { padding: '6px 8px' }

/** el — 061 요소 열쇠(정렬 머리 `sort:<열>`). */
export type Header = [key: string, label: string, align: 'left' | 'right', el?: string]

/** 정렬 가능한 sticky 헤더 — 활성 열은 accent-300 + ▾/▴. */
export function GridHeader({ cols, headers, sortKey, sortDir, onSort }: {
  cols: string; headers: Header[]; sortKey: string; sortDir: number; onSort: (k: string) => void
}) {
  return (
    <div style={{ ...gridHead(cols), position: 'sticky', top: 0, zIndex: 2, background: 'var(--color-bg)' }}>
      {headers.map(([k, text, align, el]) => (
        <button key={k} onClick={() => onSort(k)} className="hv-txt" data-el={el}
          style={{
            appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 10.5,
            letterSpacing: '0.07em', textTransform: 'uppercase', padding: '8px 8px', cursor: 'pointer',
            textAlign: align, whiteSpace: 'nowrap',
            color: k === sortKey ? 'var(--color-accent-300)' : 'var(--color-neutral-600)',
          }}>
          {text}{k === sortKey ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}
        </button>
      ))}
    </div>
  )
}

/** 표 행 — hot 은 accent 8% 배경, stale 은 opacity 0.45. rule 은 행 구분선 농도(%). */
export function gridRow(cols: string, o: { hot?: boolean; stale?: boolean; height?: number; rule?: number } = {}): CSSProperties {
  return {
    display: 'grid', gridTemplateColumns: cols, alignItems: 'center', padding: '0 var(--space-6)', height: o.height ?? 40,
    borderBottom: `1px solid color-mix(in srgb, #e9e9ed ${o.rule ?? 7}%, transparent)`,
    background: o.hot ? 'color-mix(in srgb, var(--color-accent) 8%, transparent)' : 'transparent',
    opacity: o.stale ? 0.45 : 1,
  }
}

// 심볼 셀 스타일 [hot ? 1 : 0] — 스프레드·현선갭·선선갭 표의 행마다 매초 수백 번 그려지는 칸이라 두 가지 객체를 모듈에서 한 번만 만든다.
// React 는 같은 style 객체면 키별 비교를 건너뛴다. 값·키 순서는 렌더마다 만들던 인라인 객체와 같아 DOM 의 style 글자도 같다
const symCellStyle = (hot: boolean): CSSProperties => (
  { padding: '0 8px', display: 'flex', alignItems: 'center', gap: 6, fontWeight: 500, fontSize: 13.5, color: hot ? 'var(--color-accent-300)' : 'var(--color-text)' }
)
const symDotStyle = (hot: boolean): CSSProperties => (
  { width: 4, height: 4, borderRadius: '50%', background: hot ? 'var(--color-accent)' : 'transparent', flex: 'none' }
)
const SYM_CELL = [symCellStyle(false), symCellStyle(true)] as const
const SYM_DOT = [symDotStyle(false), symDotStyle(true)] as const

/** 심볼 셀 — 앞 점은 hot 일 때만 accent 로 보인다. */
export function SymCell({ sym, hot }: { sym: string; hot: boolean }) {
  return (
    <div style={SYM_CELL[hot ? 1 : 0]}>
      <span style={SYM_DOT[hot ? 1 : 0]} />{sym}
    </div>
  )
}

/** 빈 상태 문구 — 표·카드 안 가운데 정렬. */
export function Empty({ children, size = 13 }: { children: ReactNode; size?: number }) {
  return (
    <div style={{ padding: 'var(--space-8) var(--space-6)', textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: size }}>
      {children}
    </div>
  )
}

export function Chip(props: {
  children: ReactNode
  tone?: 'accent' | 'neutral' | 'outline' | 'warn'
  onClick?: () => void
  active?: boolean
  style?: CSSProperties
}) {
  const tone = props.tone ?? 'neutral'
  const toneStyle: CSSProperties =
    tone === 'accent'
      ? { background: 'var(--color-accent-800)', color: 'var(--color-accent-100)' }
      : tone === 'outline'
        ? { border: '1px solid var(--color-accent)', color: 'var(--color-accent)' }
        : tone === 'warn'
          ? { background: 'color-mix(in srgb, var(--color-warn) 20%, transparent)', color: 'var(--color-warn)' }
          : { background: 'var(--color-neutral-800)', color: 'var(--color-neutral-100)' }
  const base: CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, letterSpacing: '0.02em', padding: '2px 8px',
    borderRadius: 'calc(var(--radius-md) * 0.75)', whiteSpace: 'nowrap', ...toneStyle,
    ...(props.active ? { boxShadow: 'inset 0 0 0 1px var(--color-accent)', color: 'var(--color-accent)' } : null),
    ...props.style,
  }
  if (props.onClick) {
    return (
      <button type="button" className="hv-bd-txt" onClick={props.onClick}
        style={{ ...base, cursor: 'pointer', font: 'inherit', fontSize: 11, border: base.border ?? '1px solid transparent' }}>
        {props.children}
      </button>
    )
  }
  return <span style={base}>{props.children}</span>
}

// ── 폰 조각 (072 §3.4·§3.5) ─────────────────────────────────────────────
/** 폰 컨트롤 바 — 넓은 바와 같은 선·줄바꿈, 여백만 좁게. 접힌 필터는 이 바 안에서 다음 줄(전체 폭)로 펼쳐진다. */
export const phoneBar: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-3) var(--space-4)',
  borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap',
}

/** 폰 검색 칸 — 늘 보이는 줄의 남는 폭을 다 쓴다. 16px 아래 글자는 iOS 가 누를 때 화면을 확대하므로 16px. */
export const phoneSearch: CSSProperties = { flex: '1 1 120px', minWidth: 0, width: 'auto', fontSize: 16, padding: '6px 10px', minHeight: 34 }

/** 펼친 필터 묶음 — 바의 다음 줄 전체. 안의 조작은 줄바꿈하며 놓인다. */
export const foldPanel: CSSProperties = {
  flexBasis: '100%', minWidth: 0, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px var(--space-4)',
  padding: 'var(--space-2) 0 var(--space-1)',
}

/** `필터` 단추 — 기본값과 다른 필터 수를 작은 숫자로 붙인다(0 이면 없음). 펼침 상태는 호출부 메모리. el — 061 요소 열쇠(호출부가 준다) */
export function FoldButton({ open, active, onClick, el }: { open: boolean; active: number; onClick: () => void; el?: string }) {
  return (
    <button type="button" onClick={onClick} className="hv-bd" data-el={el} aria-expanded={open}
      style={{
        appearance: 'none', font: 'inherit', fontSize: 12, padding: '0 10px', minHeight: 34, cursor: 'pointer', flex: 'none',
        display: 'inline-flex', alignItems: 'center', gap: 6, borderRadius: 'var(--radius-sm)',
        border: `1px solid ${open || active ? 'var(--color-accent)' : 'var(--color-neutral-800)'}`,
        background: open ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)' : 'transparent',
        color: open || active ? 'var(--color-accent-300)' : 'var(--color-neutral-400)',
      }}>
      필터
      {active > 0 && (
        <span style={{ minWidth: 16, height: 16, padding: '0 4px', borderRadius: 8, background: 'var(--color-accent-700)', color: 'var(--color-accent-100)', fontSize: 10, lineHeight: '16px', textAlign: 'center' }}>
          {active}
        </span>
      )}
      <span aria-hidden style={{ fontSize: 9 }}>{open ? '▴' : '▾'}</span>
    </button>
  )
}

/**
 * 폰 정렬 줄 — 열 머리 줄 대신(§3.5). 고르기 = 그 열 머리를 누른 것, 방향 단추 = 지금 열 머리를 다시 누른 것.
 * 문서 스크롤에서 탭 줄 바로 아래에 붙는다. right 는 오른쪽 끝(표시 건수). els — 061 요소 열쇠 [고르기, 방향 단추](호출부가 준다)
 */
export function SortBar({ headers, sortKey, sortDir, onSort, right, els }: {
  headers: Header[]; sortKey: string; sortDir: number; onSort: (k: string) => void; right?: ReactNode; els?: readonly [string, string]
}) {
  // 탭 줄 바로 아래 붙는 것은 문서 스크롤 구간만 — 탭 본문이 스스로 스크롤되는 배치(좁은 칸의 기록 순위 카드, 072 §3.9)에서는 붙이면 위에 빈 줄이 생긴다
  const { docScroll } = useViewport()
  return (
    <div style={{
      ...(docScroll ? { position: 'sticky', top: TAB_BAR_H, zIndex: 2 } : null), display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
      padding: 'var(--space-2) var(--space-4)', background: 'var(--color-bg)', borderBottom: '1px solid var(--color-neutral-800)',
      fontSize: 12, color: 'var(--color-neutral-500)',
    }}>
      {/* 열 이름이 길면(갭 탭 `진입 갭 · 현물 → 선물`) 고르기 칸이 줄어든다 — 줄이 꺾이지 않게. 16px 는 iOS 확대 막기 */}
      <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minWidth: 0, flex: '0 1 auto', whiteSpace: 'nowrap' }}>
        정렬
        <select value={headers.some(([k]) => k === sortKey) ? sortKey : ''} onChange={(e) => onSort(e.target.value)} data-el={els?.[0]}
          style={{ font: 'inherit', fontSize: 16, color: 'var(--color-accent-300)', background: 'var(--color-surface)', border: '1px solid var(--color-neutral-800)', borderRadius: 'var(--radius-sm)', padding: '4px 6px', minHeight: 30, minWidth: 0, maxWidth: '100%', textOverflow: 'ellipsis' }}>
          {!headers.some(([k]) => k === sortKey) && <option value="" disabled>–</option>}
          {headers.map(([k, text]) => <option key={k} value={k}>{text}</option>)}
        </select>
      </label>
      <button type="button" onClick={() => onSort(sortKey)} className="hv-bd-txt" data-el={els?.[1]}
        style={{ appearance: 'none', font: 'inherit', fontSize: 12, padding: '0 10px', minHeight: 30, cursor: 'pointer', borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-neutral-800)', background: 'transparent', color: 'var(--color-neutral-300)', flex: 'none', whiteSpace: 'nowrap' }}>
        {sortDir < 0 ? '내림차순 ▾' : '오름차순 ▴'}
      </button>
      {right && <span style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)', whiteSpace: 'nowrap', flex: 'none' }}>{right}</span>}
    </div>
  )
}

/** 폰 카드 행 — 왼쪽 이름·보조값 / 오른쪽 큰 값, 줄마다 두 칸. hot·stale 은 넓은 표의 행과 같은 규칙. */
export function cardRow(o: { hot?: boolean; stale?: boolean; rule?: number } = {}): CSSProperties {
  return {
    display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', alignItems: 'center', columnGap: 'var(--space-4)', rowGap: 4,
    padding: 'var(--space-3) var(--space-4)',
    borderBottom: `1px solid color-mix(in srgb, #e9e9ed ${o.rule ?? 7}%, transparent)`,
    background: o.hot ? 'color-mix(in srgb, var(--color-accent) 8%, transparent)' : 'transparent',
    opacity: o.stale ? 0.45 : 1,
  }
}

/** 카드 행 왼쪽 칸 — 한 줄로 놓고 넘치면 말줄임. */
export const cardLeft: CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, overflow: 'hidden', whiteSpace: 'nowrap' }
/** 카드 행 오른쪽 칸 — 오른쪽 정렬. */
export const cardRight: CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 6, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }
/** 카드 행 보조 글자(11px 흐림). */
export const cardSub: CSSProperties = { fontSize: 11, color: 'var(--color-neutral-500)', fontVariantNumeric: 'tabular-nums', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }
