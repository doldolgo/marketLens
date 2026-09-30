// URL 쿼리에 실리는 화면 상태 — 새로고침·링크 공유 시 같은 화면이 나오게 (스펙 002 §3.5).
// useState 와 같은 모양이되 초기값을 URL 에서 읽고, 바뀌면 자기 키만 replaceState 로 갱신한다.
// 기본값이면 키를 지워 URL 을 짧게 유지하고, pushState 가 아니라 뒤로가기 히스토리는 쌓이지 않는다.
// 탭은 한 번 마운트되면 언마운트되지 않으므로(002 §3.5) 각 탭이 자기 키만 읽고 쓰면 충돌이 없다 — 키는 탭 접두어로 구분.
// 기록 탭만 처음 볼 때 마운트된다 — 그 전에는 셸이 `h.` 키를 URL 에서 대신 빼고, 값은 마운트될 때 읽게 메모리에 둔다(dropParams).
import { createContext, useContext, useEffect, useState, type Dispatch, type SetStateAction } from 'react'

/** 값 ↔ URL 문자열. parse 가 undefined 를 돌려주면 잘못된 값이라 기본값으로 간다. */
export interface Codec<T> {
  parse: (s: string) => T | undefined
  format: (v: T) => string
}

export const str: Codec<string> = { parse: (s) => s, format: (v) => v }
export const num: Codec<number> = {
  parse: (s) => { const n = Number(s); return s.trim() !== '' && Number.isFinite(n) ? n : undefined },
  format: (v) => String(v),
}
export const bool: Codec<boolean> = { parse: (s) => (s === '1' ? true : s === '0' ? false : undefined), format: (v) => (v ? '1' : '0') }

/** 허용 값 집합 — URL 토큰이 값 그 자체. */
export const oneOf = <T extends string>(values: readonly T[]): Codec<T> => ({
  parse: (s) => (values as readonly string[]).includes(s) ? (s as T) : undefined,
  format: (v) => v,
})

/** 허용 값 집합인데 URL 토큰을 따로 둔다 — 한글 값('업비트')을 퍼센트 인코딩 없이 짧은 토큰(upbit)으로. */
export const alias = <T>(pairs: [token: string, value: T][]): Codec<T> => ({
  parse: (s) => pairs.find(([t]) => t === s)?.[1],
  format: (v) => pairs.find(([, x]) => x === v)?.[0] ?? '',
})

/** 원소 codec 을 쉼표 목록으로. 잘못된 원소는 버리고 나머지만 남긴다. */
export const list = <T>(item: Codec<T>): Codec<T[]> => ({
  parse: (s) => s.split(',').filter(Boolean).map(item.parse).filter((v): v is T => v !== undefined),
  format: (v) => v.map(item.format).join(','),
})

/** 표 정렬 {col, asc} — `col:asc` / `col:desc`. */
export const sortOf = <C extends string>(cols: readonly C[]): Codec<{ col: C; asc: boolean }> => ({
  parse: (s) => {
    const [c, d] = s.split(':')
    if (!(cols as readonly string[]).includes(c) || (d !== 'asc' && d !== 'desc')) return undefined
    return { col: c as C, asc: d === 'asc' }
  },
  format: (v) => `${v.col}:${v.asc ? 'asc' : 'desc'}`,
})

// dropParams 가 URL 에서 뺀 값 — 아직 마운트하지 않은 탭이 나중에 마운트될 때 초기값으로 읽는다
const parked = new Map<string, string>()

function readParam(key: string): string | null {
  return new URLSearchParams(window.location.search).get(key) ?? parked.get(key) ?? null
}

function writeParam(key: string, value: string | null) {
  const url = new URL(window.location.href)
  if (value === null) url.searchParams.delete(key)
  else url.searchParams.set(key, value)
  if (url.href !== window.location.href) window.history.replaceState(window.history.state, '', url)
}

/** 접두어로 시작하는 키를 URL 에서 모두 뺀다 — 아직 마운트하지 않은 탭은 자기 키를 지울 수 없어서, 셸이 대신 지운다
 *  (마운트돼 있었다면 비활성이라 지웠을 키다 — URL 은 보이는 화면만 담는다). 값은 메모리에 남겨 그 탭이 처음 마운트될 때 초기값으로 쓴다
 *  — 마운트돼 있던 비활성 탭이 URL 에서 키를 빼도 값은 들고 있는 것과 같게. */
export function dropParams(prefix: string): void {
  const url = new URL(window.location.href)
  // 지우면서 훑으면 목록이 당겨져 건너뛰는 키가 생기므로 지울 키를 먼저 모은다
  const drop = Array.from(url.searchParams.keys()).filter((key) => key.startsWith(prefix))
  for (const key of drop) {
    parked.set(key, url.searchParams.get(key) ?? '')
    url.searchParams.delete(key)
  }
  if (url.href !== window.location.href) window.history.replaceState(window.history.state, '', url)
}

/** URL 은 지금 보이는 화면만 담는다 — 셸이 탭마다 활성 여부를 내려 주고, 비활성 탭의 키는 URL 에서 빠진다(값은 메모리에 남는다). */
export const UrlActive = createContext(true)

/** useState 대체 — URL `?key=` 에서 초기값을 읽고, 값이 바뀔 때 URL 을 따라 갱신한다. 기본값이면 키를 지운다.
 *  `active` 를 주면 UrlActive 대신 그 값을 쓴다(셸 자신의 키처럼 특정 탭에만 속하는 것). */
export function useUrlState<T>(key: string, def: T, codec: Codec<T>, active?: boolean): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    const s = readParam(key)
    if (s === null) return def
    const parsed = codec.parse(s)
    return parsed === undefined ? def : parsed
  })
  const ctxActive = useContext(UrlActive)
  const on = active ?? ctxActive
  useEffect(() => {
    const s = codec.format(value)
    writeParam(key, !on || s === codec.format(def) ? null : s)
  }, [key, value, on]) // def·codec 은 호출처에서 상수라 의존성에서 뺀다
  return [value, setValue]
}
