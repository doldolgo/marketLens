// 문자열 시드 기반 결정론적 난수 (xmur3 해시 + mulberry32).
// mock 데이터가 리로드해도 같은 모양이어야 하기 때문이다 (스펙 002 §3.4).

/** 문자열 시드 → [0,1) 난수 생성기. 같은 시드는 항상 같은 수열을 낸다. */
export function rng(seed: string): () => number {
  let h = 1779033703 ^ seed.length
  for (let i = 0; i < seed.length; i++) {
    h = Math.imul(h ^ seed.charCodeAt(i), 3432918353)
    h = (h << 13) | (h >>> 19)
  }
  let a = (h ^= h >>> 16) >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** min 이상 max 미만 균등 난수. */
export function uniform(r: () => number, min: number, max: number): number {
  return min + r() * (max - min)
}

/** 배열에서 하나 뽑기. */
export function pick<T>(r: () => number, arr: readonly T[]): T {
  return arr[Math.floor(r() * arr.length)]
}

/** Fisher–Yates 셔플한 사본. */
export function shuffled<T>(arr: readonly T[], r: () => number): T[] {
  const out = [...arr]
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(r() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}
