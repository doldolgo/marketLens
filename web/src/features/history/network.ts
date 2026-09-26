// 망 경로 표기 (스펙 024 §3.8). 순수 함수 — 차트 읽기 줄과 사건 로그가 같이 쓴다.
import type { Dir } from './types'

/** 망을 모를 때의 표기. */
export const NET_NONE = '–'

/**
 * 경로 방향대로 `{보내는 망} → {받는 망}` — 김프는 해외 → 국내, 역프는 국내 → 해외.
 * 한쪽만 있으면 있는 쪽만(`– → Ethereum`), 둘 다 없으면(배포 전 봉·사건, 모름) `–`.
 */
export function netPath(dir: Dir, netDom: string | null, netFx: string | null): string {
  if (netDom == null && netFx == null) return NET_NONE
  const dom = netDom ?? NET_NONE
  const fx = netFx ?? NET_NONE
  if (dir === 'kimp') return `${fx} → ${dom}`
  return `${dom} → ${fx}`
}
