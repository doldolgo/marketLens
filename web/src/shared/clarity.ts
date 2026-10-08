// Microsoft Clarity 호출 두 개 — 태그 두기·이벤트 남기기 (스펙 033 §3.8).
// public/clarity.js 가 동의한 방문자에게만 window.clarity(태그가 오기 전 호출을 모아 두는 대기열)를 만든다 — 없으면 아무것도 안 한다.
// 키·이름은 고정 토큰만 싣는다 — 방문자가 입력한 글자는 싣지 않는다.

type ClarityCall = (...args: unknown[]) => void

/** clarity.js 가 문서 중간에(동의 창의 저장·다른 탭의 동의로) 대기열을 만든 직후 window 에 한 번 보내는 이벤트. */
export const CLARITY_READY = 'kt:clarity'

function clarity(): ClarityCall | null {
  const fn = (window as unknown as { clarity?: unknown }).clarity
  return typeof fn === 'function' ? (fn as ClarityCall) : null
}

/** 페이지 태그 — `tab`(탭 id)·`sym`(기록 탭의 선택 심볼). Clarity 태그는 페이지마다 새로 시작하므로 tab URL 을 쓴 다음에 둔다. */
export function clarityTag(key: 'tab' | 'sym', value: string): void {
  clarity()?.('set', key, value)
}

/** 사용자 이벤트 — `tab_<id>`(탭 단추)·`pivot_history`(스프레드 행 → 기록 탭). */
export function clarityEvent(name: string): void {
  clarity()?.('event', name)
}
