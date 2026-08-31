// 숫자 포맷 규칙 — 전부 순수 함수, 스펙 002 §3.3 예시와 정확히 일치해야 한다.

/** KRW: ≥100 반올림 정수 콤마 / ≥1 소수 2 / 그 외 최대 4자리. */
export function fmtKrw(v: number): string {
  const a = Math.abs(v)
  if (a >= 100) return Math.round(v).toLocaleString('ko-KR')
  if (a >= 1) return v.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return v.toLocaleString('ko-KR', { maximumFractionDigits: 4 })
}

/** USDT: ≥1000 콤마+소수 2 / ≥1 소수 3 / ≥0.001 소수 4 / 그 외 소수 8. */
export function fmtUsdt(v: number): string {
  const a = Math.abs(v)
  if (a >= 1000) return v.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (a >= 1) return v.toFixed(3)
  if (a >= 0.001) return v.toFixed(4)
  return v.toFixed(8)
}

/** 퍼센트: 양수 + 접두, 소수 2자리, %. */
export function fmtPct(v: number): string {
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

/** 경과: <60s N초 전 / <60m N분 전 / 그 이상 N시간 전 (반올림). */
export function fmtAgo(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}초 전`
  const min = sec / 60
  if (min < 60) return `${Math.round(min)}분 전`
  return `${Math.round(min / 60)}시간 전`
}

/** 수량: ≥1000 정수 콤마 / ≥1 소수 2 / 그 외 소수 4. */
export function fmtQty(v: number): string {
  const a = Math.abs(v)
  if (a >= 1000) return Math.round(v).toLocaleString('ko-KR')
  if (a >= 1) return v.toFixed(2)
  return v.toFixed(4)
}

/** USD: null → – / 아니면 $ + 반올림 정수 콤마. */
export function fmtUsd(v: number | null): string {
  if (v === null) return '–'
  return `$${Math.round(v).toLocaleString('ko-KR')}`
}

/** 펀딩(갭 탭): 소수 3자리 %. */
export function fmtFunding3(v: number): string {
  return `${v > 0 ? '+' : ''}${v.toFixed(3)}%`
}

/** 펀딩(선선갭): 시간당 정규화, 소수 4자리 %/h. */
export function fmtFundingHr(v: number): string {
  return `${v > 0 ? '+' : ''}${v.toFixed(4)}%/h`
}

/** 시각: M/D HH:mm (로컬, 월 1-base). */
export function fmtTime(ms: number): string {
  const d = new Date(ms)
  return `${d.getMonth() + 1}/${d.getDate()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** HH:mm (결측 타임라인 툴팁·축 라벨용). */
export function fmtHm(ms: number): string {
  const d = new Date(ms)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** HH:mm:ss (요약 카드 기준 시각용). */
export function fmtHms(ms: number): string {
  const d = new Date(ms)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** 한국식 색 규약 — >0 빨강(상승), <0 파랑(하락), 0 중립 (§3.3). */
export function pctColor(v: number): string {
  if (v > 0) return 'var(--color-up)'
  if (v < 0) return 'var(--color-down)'
  return 'var(--color-text)'
}
