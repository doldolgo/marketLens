// 주소 상태(스펙 066 §3.1·§3.4) — 고른 기간·비교·거르기는 ?range·cmp·f, 열린 상세 판은 #detail. 목록 밖 값은 기본값으로(오류 없이).
// 주소를 만드는 곳은 urlOf() 하나 — 그 페이지의 고정 주소(page.home) + 검사한 값으로만(지금 경로를 쓰지 않는다 — 029).
import { own } from './common.js';

export const CMPS = Object.freeze(['none', 'prev', 'week']);
const TONE_WORD = Object.freeze({ bad: '문제', warn: '주의' });

export function cmpOpen(page, range, cmp) {
  return cmp === 'none' || Boolean(page.compare?.(range)?.[cmp]);
}
const splitDetail = (id) => {
  const at = id.indexOf(':');
  return at < 0 ? [id, null] : [id.slice(0, at), id.slice(at + 1)];
};
// 상세 판 id — 카드 id, 또는 '이름:값'(값을 받는 판 — 방문자 대역 'ip:203.0.113.0')
export function detailOK(page, id) {
  if (typeof id !== 'string' || !id) return false;
  const [name, arg] = splitDetail(id);
  const p = own(page.details ?? {}, name);
  if (!p) return false;
  return arg === null ? !p.accepts : Boolean(p.accepts?.(arg));
}
export function readView(page, search, hash) {
  const q = new URLSearchParams(search ?? '');
  const range = page.ranges.includes(q.get('range')) ? q.get('range') : page.range;
  const cmp = CMPS.includes(q.get('cmp')) && cmpOpen(page, range, q.get('cmp')) ? q.get('cmp') : 'none';
  const f = [];
  for (const raw of q.getAll('f')) {
    const at = raw.indexOf(':');
    if (at <= 0) continue;
    const key = raw.slice(0, at);
    const value = raw.slice(at + 1);
    const ok = own(page.filters ?? {}, key);
    if (ok?.(value) && !f.some(([k, v]) => k === key && v === value)) f.push([key, value]);
  }
  const d = new URLSearchParams(String(hash ?? '').replace(/^#/, '')).get('detail');
  return { range, cmp, f, detail: d !== null && detailOK(page, d) ? d : null };
}
// 보기 → 주소. 기본값은 싣지 않는다(처음 주소가 그대로 깨끗하게)
export function urlOf(page, view, extra = []) {
  const q = new URLSearchParams();
  if (view.range !== page.range) q.set('range', view.range);
  if (view.cmp !== 'none') q.set('cmp', view.cmp);
  for (const [k, v] of view.f) q.append('f', `${k}:${v}`);
  for (const [k, v] of extra) q.set(k, v);
  const s = q.toString();
  return `${page.home}${s ? `?${s}` : ''}${view.detail ? `#detail=${encodeURIComponent(view.detail)}` : ''}`;
}
