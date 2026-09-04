// 표시 규칙 상수 — 값이 진실이며 이 파일 한 곳에서만 관리한다 (스펙 002 §3.1).

/** 공통 하이라이트의 기본 기준 (%) — 003(spreads)이 사용, 셸은 값만 들고 있다. */
export const HIGHLIGHT_PCT = 1.5
/** stale 기준(초) — 마지막 수신 후 이 시간이 지나면 행을 흐리게 그린다. */
export const STALE_SEC = 5
/** mock tick(ms) — 셸 전체의 심장 박동. 시계·age·mock 흔들림이 전부 이 주기를 따른다. */
export const MOCK_TICK_MS = 1500
/** 스프레드 폴링(ms) — 003 이 사용, 셸은 값만 들고 있다. */
export const SPREAD_POLL_MS = 1000
/** 수집 상태 폴링(ms) — 011 이 사용, 셸은 값만 들고 있다. */
export const HEALTH_POLL_MS = 5000
/** 색 규약 — 한국식: 빨강=상승, 파랑=하락 (§3.3). */
export const COLOR_CONVENTION = '한국식'

/** API base — VITE_API_BASE 미설정 시 /api (dev 는 vite proxy 가 /api 접두사를 떼고 8000 으로). */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api'
