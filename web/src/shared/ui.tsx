// 공용 조작 조각 3종 + 칩 — 분절 버튼 그룹·숫자 입력·토글 (스펙 002 §3.6).
// 스타일은 인라인 style 객체 + CSS 변수만. 활성·on 은 accent 로 강조.
import type { CSSProperties, ReactNode } from 'react'

export const DIM_TEXT = 'color-mix(in srgb, var(--color-text) 55%, transparent)'

/** 분절 버튼 그룹. */
export function Seg(props: {
  options: readonly { id: string; label: string }[]
  value: string
  onChange: (id: string) => void
}) {
  return (
    <div
      style={{
        display: 'inline-flex',
        border: '1px solid var(--color-divider)',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
        flex: 'none',
      }}
    >
      {props.options.map((o) => {
        const on = o.id === props.value
        return (
          <button
            key={o.id}
            type="button"
            className={on ? undefined : 'hv-txt'}
            onClick={() => props.onChange(o.id)}
            style={{
              font: 'inherit',
              fontSize: 12,
              padding: '5px 11px',
              cursor: 'pointer',
              border: 'none',
              background: on ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)' : 'transparent',
              color: on ? 'var(--color-accent)' : DIM_TEXT,
              boxShadow: on ? 'inset 0 0 0 1px var(--color-accent)' : 'none',
            }}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

/** 숫자 입력 — 라벨 + 입력 + 단위(기본 %). 비숫자 입력은 0. */
export function NumField(props: {
  label: string
  value: number
  onChange: (v: number) => void
  step?: number
  unit?: string
}) {
  return (
    <label
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12,
        flex: 'none',
        color: 'color-mix(in srgb, var(--color-text) 70%, transparent)',
      }}
    >
      {props.label}
      <input
        type="number"
        step={props.step ?? 0.1}
        value={props.value}
        onChange={(e) => {
          const n = e.target.valueAsNumber
          props.onChange(Number.isFinite(n) ? n : 0)
        }}
        style={{
          width: 64,
          minHeight: 28,
          padding: '2px 8px',
          font: 'inherit',
          fontSize: 12,
          color: 'var(--color-text)',
          caretColor: 'var(--color-accent)',
          background: 'var(--color-surface)',
          border: '1px solid var(--color-divider)',
          borderRadius: 'var(--radius-sm)',
        }}
      />
      {props.unit ?? '%'}
    </label>
  )
}

/** 토글 버튼. */
export function Toggle(props: { label: string; on: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      type="button"
      className={props.on ? undefined : 'hv-bd-txt'}
      onClick={() => props.onChange(!props.on)}
      style={{
        font: 'inherit',
        fontSize: 12,
        padding: '5px 11px',
        cursor: 'pointer',
        flex: 'none',
        border: `1px solid ${props.on ? 'var(--color-accent)' : 'var(--color-divider)'}`,
        borderRadius: 'var(--radius-md)',
        background: props.on ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)' : 'transparent',
        color: props.on ? 'var(--color-accent)' : DIM_TEXT,
      }}
    >
      {props.label}
    </button>
  )
}

/** 작은 칩 — 거래소·상태 표시용. */
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
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 11,
    letterSpacing: '0.02em',
    padding: '2px 8px',
    borderRadius: 'calc(var(--radius-md) * 0.75)',
    whiteSpace: 'nowrap',
    ...toneStyle,
    ...(props.active ? { boxShadow: 'inset 0 0 0 1px var(--color-accent)', color: 'var(--color-accent)' } : null),
    ...props.style,
  }
  if (props.onClick) {
    return (
      <button
        type="button"
        className="hv-bd-txt"
        onClick={props.onClick}
        style={{ ...base, cursor: 'pointer', font: 'inherit', fontSize: 11, border: base.border ?? '1px solid transparent' }}
      >
        {props.children}
      </button>
    )
  }
  return <span style={base}>{props.children}</span>
}
