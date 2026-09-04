// 시안에서 반복되는 작은 UI 조각들
import type { CSSProperties } from 'react';

// 10px 대문자 소제목 (KPI 라벨 등) — 시안 전역에서 반복되는 스타일
export const kicker: CSSProperties = {
  fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase',
  color: 'var(--color-neutral-600)',
};

export interface SegOpt { label: string; onClick: () => void; bg: string; color: string }

// 선택 상태 스타일을 만들어주는 헬퍼 — 시안의 bg/color 계산 패턴 그대로
export const segOpt = (label: string, active: boolean, onClick: () => void): SegOpt => ({
  label, onClick,
  bg: active ? 'var(--color-neutral-900)' : 'transparent',
  color: active ? 'var(--color-accent-300)' : 'var(--color-neutral-500)',
});

// 테두리 안에 버튼이 나란히 붙는 세그먼트 컨트롤
export function Seg({ opts, pad = '5px 12px' }: { opts: SegOpt[]; pad?: string }) {
  return (
    <div style={{ display: 'flex', border: '1px solid var(--color-neutral-800)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
      {opts.map(o => (
        <button key={o.label} onClick={o.onClick} className="hv-txt"
          style={{ appearance: 'none', border: 'none', font: 'inherit', fontSize: 12, padding: pad, cursor: 'pointer', background: o.bg, color: o.color }}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// 임계값 등 라벨 + 숫자 입력 조합
export function NumField({ label, value, step, onChange, unit = '%' }: {
  label: string; value: number; step: number; onChange: (v: number) => void; unit?: string;
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', fontSize: 12, color: 'var(--color-neutral-400)' }}>
      {label}
      <input className="input" type="number" step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value) || 0)}
        style={{ width: 62, fontSize: 12, padding: '5px 8px', fontVariantNumeric: 'tabular-nums', textAlign: 'right' }} />
      <span>{unit}</span>
    </label>
  );
}

// "임계 초과만" 류의 토글 버튼
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
  );
}
