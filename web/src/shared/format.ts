// 숫자 포맷 규칙 — 전부 순수 함수, 스펙 002 §3.3 예시와 정확히 일치해야 한다.

// 로캘·옵션을 주는 toLocaleString 은 부를 때마다 Intl.NumberFormat 을 새로 만든다 — 스프레드 표 한 번 그리는 데
// 수백 번이라 모듈에서 한 번만 만들어 재사용한다. 같은 로캘·옵션이라 출력 문자열은 toLocaleString 과 같다 (002 §3.3).
const NF_INT = new Intl.NumberFormat('ko-KR')
const NF_FIX2 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const NF_MAX4 = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 4 })

/** KRW: ≥100 반올림 정수 콤마 / ≥1 소수 2 / 그 외 최대 4자리. */
export function fmtKrw(v: number): string {
  const a = Math.abs(v)
  if (a >= 100) return NF_INT.format(Math.round(v))
  if (a >= 1) return NF_FIX2.format(v)
  return NF_MAX4.format(v)
}

/** USDT: ≥1000 콤마+소수 2 / ≥1 소수 3 / ≥0.001 소수 4 / 그 외 소수 8. */
export function fmtUsdt(v: number): string {
  const a = Math.abs(v)
  if (a >= 1000) return NF_FIX2.format(v)
  if (a >= 1) return v.toFixed(3)
  if (a >= 0.001) return v.toFixed(4)
  return v.toFixed(8)
}

/**
 * 부호 붙은 고정 소수 — 양수는 `+` 접두. 표시 자릿수로 반올림한 값이 0 이면 부호 없이 0 이다 —
 * `toFixed` 는 −0.005 < v < 0 에서 `-0.00` 을 내는데, 글자·색이 반올림한 값(0)과 어긋나지 않게 (002 §3.3).
 */
function signedFixed(v: number, digits: number): string {
  const t = v.toFixed(digits)
  if (nearZero(v, digits) && Number(t) === 0) return (0).toFixed(digits)
  return v > 0 ? `+${t}` : t
}

/**
 * 반올림해 0 이 될 수 있는 값인가 — |v| 가 10^−digits 이상이면 반올림 결과가 0 일 수 없으므로 문자열을 숫자로 되읽지 않는다.
 * 스프레드 표는 렌더마다 수백 셀이라, 되읽기를 0 근처 값에만 해 반올림 판정 비용을 없앤다(결과는 늘 되읽는 것과 같다).
 */
function nearZero(v: number, digits: number): boolean {
  return Math.abs(v) < 10 ** -digits
}

/** 퍼센트: 양수 + 접두, 소수 2자리, %. 반올림해 0 이면 `0.00%`. */
export function fmtPct(v: number): string {
  return `${signedFixed(v, 2)}%`
}

/**
 * 경과: <60s N초 전 / <60m N분 전 / 그 이상 N시간 전 (반올림). 먼저 표시 단위로 반올림하고, 반올림한 값이 다음 단위 경계(60)에
 * 닿으면 다음 단위로 올린다 — 59.6초 → `1분 전`(`60초 전` 아님), 3,599초 → `1시간 전`(`60분 전` 아님).
 */
export function fmtAgo(sec: number): string {
  const s = Math.round(sec)
  if (s < 60) return `${s}초 전`
  const min = sec / 60
  const m = Math.round(min)
  if (m < 60) return `${m}분 전`
  return `${Math.round(min / 60)}시간 전`
}

/** 수량: ≥1000 정수 콤마 / ≥1 소수 2 / 그 외 소수 4. */
export function fmtQty(v: number): string {
  const a = Math.abs(v)
  if (a >= 1000) return NF_INT.format(Math.round(v))
  if (a >= 1) return v.toFixed(2)
  return v.toFixed(4)
}

/** USD: null → – / 아니면 $ + 반올림 정수 콤마. */
export function fmtUsd(v: number | null): string {
  if (v === null) return '–'
  return `$${NF_INT.format(Math.round(v))}`
}

/** 펀딩(갭 탭): 소수 3자리 %. 반올림해 0 이면 `0.000%`. */
export function fmtFunding3(v: number): string {
  return `${signedFixed(v, 3)}%`
}

/** 펀딩(선선갭): 시간당 정규화, 소수 4자리 %/h. 반올림해 0 이면 `0.0000%/h`. */
export function fmtFundingHr(v: number): string {
  return `${signedFixed(v, 4)}%/h`
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

/**
 * 한국식 색 규약 — >0 빨강(상승), <0 파랑(하락), 0 중립 (§3.3). 판정은 표시 자릿수(`digits`, 퍼센트 2·펀딩 3·4)로 반올림한 값으로 —
 * 글자가 `0.00%` 인데 색만 파랗게 나오지 않게.
 */
export function pctColor(v: number, digits = 2): string {
  const r = nearZero(v, digits) ? Number(v.toFixed(digits)) : v
  if (r > 0) return 'var(--color-up)'
  if (r < 0) return 'var(--color-down)'
  return 'var(--color-text)'
}

/** 거래소 id → 표시명 (003 §3.4). 모르는 id 는 그대로. spreads·health 가 같이 쓴다. perp 원천은 046 §3.8(Hyperliquid 는 047). */
const EX_NAMES: Record<string, string> = {
  upbit: '업비트',
  bithumb: '빗썸',
  binance: 'Binance',
  bybit: 'Bybit',
  bitget: 'Bitget',
  okx: 'OKX',
  binance_perp: 'Binance perp',
  bybit_perp: 'Bybit perp',
  bitget_perp: 'Bitget perp',
  hyperliquid_perp: 'Hyperliquid',
}

export function exName(id: string): string {
  return EX_NAMES[id] ?? id
}
