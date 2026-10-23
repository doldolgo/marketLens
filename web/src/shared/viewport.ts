// 화면 구간 판정 — 좁은 화면 배치(스펙 072 §3.1)를 고르는 유일한 곳. 경계값도 여기에만 둔다.
// 스타일은 인라인 style 객체만 쓰므로(002 §3.1) 미디어 쿼리 대신 이 훅이 구간을 알려 주고 호출부가 style 을 고른다.
// matchMedia 의 change 로만 다시 그린다 — 창 크기를 끄는 동안 매 픽셀 다시 그리지 않는다.
import { useSyncExternalStore } from 'react'

/** 이 폭 이하 = 폰 배치(카드 행·필터 접기·KPI 격자·짧은 탭 이름). 정적 페이지들의 폰 경계와 같다. 가로로 눕힌 폰(터치 + 높이 ≤ 560)도 폰 배치다. */
export const PHONE_MAX = 719
/** 이 폭 이하 = 두 줄 헤더 + 메뉴. 한 줄 헤더는 1080px 아래에서 넘친다. */
export const COMPACT_MAX = 1079
/** 이 높이 이하 = 문서 스크롤(가로로 눕힌 폰·낮은 창). 터치 기기면 폰 배치까지. */
export const SHORT_MAX_H = 560
/** 문서 스크롤 때 화면 위에 붙는 탭 줄 높이 — 그 아래 붙는 정렬 줄의 top. */
export const TAB_BAR_H = 40

export interface Viewport {
  /** 폭 ≤ 719, 또는 터치 기기이면서 높이 ≤ 560(가로로 눕힌 폰 — 넓은 표·필터 줄이 화면 높이를 다 먹고 표 머리가 붙지 않았다, 072 §3.1) */
  phone: boolean
  /** 폭 ≤ 1079 — 두 줄 헤더 */
  compact: boolean
  /** 폰 또는 높이 ≤ 560 — 셸이 화면 높이에 묶이지 않고 문서가 스크롤된다 */
  docScroll: boolean
  /** 손가락이 주 입력인 기기(휴대폰·태블릿) — 폭과 무관. 입력칸 글자를 16px 로 둔다(iOS 는 더 작으면 누를 때 확대한다, 072 §3.9) */
  touch: boolean
}

const QUERIES = [`(max-width: ${PHONE_MAX}px)`, `(max-width: ${COMPACT_MAX}px)`, `(max-height: ${SHORT_MAX_H}px)`, '(hover: none) and (pointer: coarse)'] as const

// 조합 16가지를 미리 만들어 둔다 — useSyncExternalStore 는 같은 상태면 같은 객체를 돌려받아야 다시 그리지 않는다
const STATES: Viewport[] = Array.from({ length: 16 }, (_, bits) => {
  const phone = (bits & 1) !== 0 || ((bits & 4) !== 0 && (bits & 8) !== 0)
  return { phone, compact: phone || (bits & 2) !== 0, docScroll: phone || (bits & 4) !== 0, touch: (bits & 8) !== 0 }
})
const WIDE: Viewport = STATES[0]

const lists = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
  ? QUERIES.map((q) => window.matchMedia(q))
  : null

function snapshot(): Viewport {
  if (!lists) return WIDE
  return STATES[(lists[0].matches ? 1 : 0) | (lists[1].matches ? 2 : 0) | (lists[2].matches ? 4 : 0) | (lists[3].matches ? 8 : 0)]
}

function subscribe(onChange: () => void): () => void {
  if (!lists) return () => {}
  for (const l of lists) l.addEventListener('change', onChange)
  return () => { for (const l of lists) l.removeEventListener('change', onChange) }
}

/** 지금 화면 구간. 구간이 바뀔 때만 다시 그린다. */
export function useViewport(): Viewport {
  return useSyncExternalStore(subscribe, snapshot, () => WIDE)
}

/** 셸 높이 — 지원하면 주소창을 뺀 높이(dvh), 아니면 vh. 한 번만 판정한다. */
export const SCREEN_H = typeof CSS !== 'undefined' && typeof CSS.supports === 'function' && CSS.supports('height', '100dvh') ? '100dvh' : '100vh'
