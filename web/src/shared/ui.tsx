// 공용 UI 조각 — docs/design/reference/ui.tsx 이식 + 탭마다 중복돼 있던
// kicker·card·세로선·grid 표·거래소/상태 배지를 여기 한 번만 둔다 (스펙 002 §3.2, §3.6).
// 스타일은 인라인 style 객체 + CSS 변수만. 흐린 글자는 neutral-500/600 램프 — color-mix 재해석 금지.
import type { CSSProperties, ReactNode } from 'react'

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

/** KPI 스트립 블록 사이 세로 그라디언트 선. */
export const vDivider = (
  <div style={{ width: 1, alignSelf: 'stretch', background: 'linear-gradient(to bottom, transparent, var(--color-divider), transparent)', flex: 'none' }} />
)

// ── 조작 조각 3종 (§3.6) ─────────────────────────────────────────────────
export interface SegOpt { label: string; onClick: () => void; bg: string; color: string }

/** 선택 상태 스타일을 만들어주는 헬퍼 — 활성은 neutral-900 배경 + accent-300 글자. */
export const segOpt = (label: string, active: boolean, onClick: () => void): SegOpt => ({
  label, onClick,
  bg: active ? 'var(--color-neutral-900)' : 'transparent',
  color: active ? 'var(--color-accent-300)' : 'var(--color-neutral-500)',
})

/** 테두리 안에 버튼이 나란히 붙는 분절 버튼 그룹. */
export function Seg({ opts, pad = '5px 12px' }: { opts: SegOpt[]; pad?: string }) {
  return (
    <div style={{ display: 'flex', border: '1px solid var(--color-neutral-800)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
      {opts.map((o) => (
        <button key={o.label} onClick={o.onClick} className="hv-txt"
          style={{ appearance: 'none', border: 'none', font: 'inherit', fontSize: 12, padding: pad, cursor: 'pointer', background: o.bg, color: o.color }}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** 숫자 입력 — 라벨 + 입력 + 단위(기본 %). 비숫자 입력은 0. */
export function NumField({ label, value, step, onChange, unit = '%' }: {
  label: string; value: number; step: number; onChange: (v: number) => void; unit?: string
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', fontSize: 12, color: 'var(--color-neutral-400)' }}>
      {label}
      <input className="input" type="number" step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        style={{ width: 62, fontSize: 12, padding: '5px 8px', fontVariantNumeric: 'tabular-nums', textAlign: 'right' }} />
      <span>{unit}</span>
    </label>
  )
}

/** "임계 초과만" 류의 토글 버튼 — on 은 accent 테두리 + 옅은 accent 배경. */
export function ToggleBtn({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="hv-bd"
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
/** 표 바깥 프레임 — 가운데 정렬, 최대 1080, 좌우 선, 자체 스크롤. */
export function TableFrame({ minWidth, children }: { minWidth: number; children: ReactNode }) {
  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', justifyContent: 'center', padding: '0 var(--space-6)' }}>
      <div style={{ minWidth, maxWidth: 1080, flex: 1, overflow: 'auto', borderLeft: '1px solid var(--color-divider)', borderRight: '1px solid var(--color-divider)' }}>
        {children}
      </div>
    </div>
  )
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

export type Header = [key: string, label: string, align: 'left' | 'right']

/** 정렬 가능한 sticky 헤더 — 활성 열은 accent-300 + ▾/▴. */
export function GridHeader({ cols, headers, sortKey, sortDir, onSort }: {
  cols: string; headers: Header[]; sortKey: string; sortDir: number; onSort: (k: string) => void
}) {
  return (
    <div style={{ ...gridHead(cols), position: 'sticky', top: 0, zIndex: 2, background: 'var(--color-bg)' }}>
      {headers.map(([k, text, align]) => (
        <button key={k} onClick={() => onSort(k)} className="hv-txt"
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

/** 심볼 셀 — 앞 점은 hot 일 때만 accent 로 보인다. */
export function SymCell({ sym, hot }: { sym: string; hot: boolean }) {
  return (
    <div style={{ padding: '0 8px', display: 'flex', alignItems: 'center', gap: 6, fontWeight: 500, fontSize: 13.5, color: hot ? 'var(--color-accent-300)' : 'var(--color-text)' }}>
      <span style={{ width: 4, height: 4, borderRadius: '50%', background: hot ? 'var(--color-accent)' : 'transparent', flex: 'none' }} />{sym}
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

// ── 레거시 — 아직 이식 전인 탭이 쓴다. 마지막 탭(flow) 이식 커밋에서 제거한다. ──
export const DIM_TEXT = 'color-mix(in srgb, var(--color-text) 55%, transparent)'

export function Toggle(props: { label: string; on: boolean; onChange: (on: boolean) => void }) {
  return <ToggleBtn on={props.on} label={props.label} onClick={() => props.onChange(!props.on)} />
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
