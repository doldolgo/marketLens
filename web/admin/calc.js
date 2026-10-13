// 계산(스펙 066 §3.2·§3.3·§4) — 상세 판 통계·비교선·CSV·상위 목록·100% 띠·정렬. DOM·echarts 없이 node 시험이 바로 부른다.
import { clean, isObj, kstStamp, kstText, list, num, utcText } from './common.js';

const HOUR = 3_600_000;

// 점 [[ts초, 값|null], …] → [[ms, 값|null], …] — 모양이 틀린 점은 뺀다, null 은 선을 끊는다(보간 없음)
export const ms = (points) => list(points).filter((p) => Array.isArray(p) && num(p[0]) !== null).map((p) => [p[0] * 1000, num(p[1])]);
export const hasValue = (points) => list(points).some((p) => Array.isArray(p) && num(p[1]) !== null);

// 구간 [from, to] 안의 점
export const crop = (points, from, to) => list(points).filter((p) => Array.isArray(p) && num(p[0]) !== null && p[0] >= from && p[0] <= to);
// 이전 기간 점을 지금 축으로(066 §3.2 — 시각 + 기간 길이): [from − by, to − by] 안의 점을 by 만큼 민다
export const align = (points, from, to, by) => crop(points, from - by, to - by).map((p) => [p[0] + by, num(p[1])]);
// 시각 t 의 값 — t 와 같거나 앞선 가장 늦은 점(null 은 건너뜀). 이전 기간 선에서 '같은 때' 값을 읽는다
export function valueAt(points, t) {
  let best = null;
  for (const p of list(points)) {
    if (!Array.isArray(p) || num(p[0]) === null || num(p[1]) === null || p[0] > t) continue;
    if (!best || p[0] >= best[0]) best = p;
  }
  return best ? best[1] : null;
}

// 통계(066 §3.2 — 보이는 구간 기준): 구간 안 null 아닌 값의 최소·평균·최대·p95(가장 가까운 순위 — 올림)·마지막(가장 늦은 점)
export function stats(points, from = -Infinity, to = Infinity) {
  const vals = [];
  let last = null;
  let lastT = -Infinity;
  for (const p of list(points)) {
    if (!Array.isArray(p)) continue;
    const t = num(p[0]);
    const v = num(p[1]);
    if (t === null || v === null || t < from || t > to) continue;
    vals.push(v);
    if (t >= lastT) {
      lastT = t;
      last = v;
    }
  }
  if (!vals.length) return { n: 0, min: null, avg: null, max: null, p95: null, last: null };
  const sorted = [...vals].sort((a, b) => a - b);
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  return { n: vals.length, min: sorted[0], avg, max: sorted.at(-1), p95: sorted[Math.max(0, Math.ceil(0.95 * sorted.length) - 1)], last };
}

// 지금 − 이전과 그 %(이전이 0·없으면 % 는 null)
export function change(cur, prev) {
  if (num(cur) === null || num(prev) === null) return { diff: null, pct: null };
  return { diff: cur - prev, pct: prev === 0 ? null : ((cur - prev) / Math.abs(prev)) * 100 };
}
// 비교 한 줄 — '어제 같은 때보다 ▲ 12%'. 증감에 상태색을 붙이지 않는다(5xx 처럼 오르면 나쁜 지표가 섞여 있다 — 조사 §2-3)
export function deltaText(cur, prev, phrase) {
  const { pct } = change(cur, prev);
  if (pct === null) return '';
  const r = Math.round(pct);
  return `${phrase}보다 ${r > 0 ? '▲' : r < 0 ? '▼' : '–'} ${Math.abs(r)}%`;
}
export const signed = (v, digits = 0) => (num(v) === null ? '–' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`);

// --- 시간 칸(038 hourly · 068 날) ------------------------------------------------------------------

// 칸 [{ts초, …}](너비 width ms) → [[칸이 창 [startMs, endMs] 와 겹친 구간의 가운데 ms, key 값]]. 지금 칸은 지금까지의 가운데라
// 창 끝(= 지금)을 넘지 않는다(칸 가운데에 두면 앞 절반 동안 마지막 막대가 축 밖으로 나간다). 이전 기간 칸은 by 만큼 민 뒤 같은 규칙
export const cellPoints = (cells, key, width, startMs, endMs, by = 0) =>
  list(cells)
    .filter((c) => isObj(c) && num(c.ts) !== null)
    .map((c) => [c.ts * 1000 + by, num(c[key]) ?? 0])
    .filter(([t]) => t + width > startMs && t < endMs)
    .map(([t, v]) => [(Math.max(t, startMs) + Math.min(t + width, endMs)) / 2, v]);
// 시간 칸 [{ts초, …}] → [[그 시의 가운데 ms, key 값]] — 축을 시 단위로 넓힌 그림(개요 24시간 트래픽)만
export const hourPoints = (hourly, key) =>
  list(hourly).filter((h) => isObj(h) && num(h.ts) !== null).map((h) => [h.ts * 1000 + HOUR / 2, num(h[key]) ?? 0]);
// [from, to] ms 와 겹치는 시간 칸만
export const hoursIn = (hourly, from, to) => list(hourly).filter((h) => isObj(h) && num(h.ts) !== null && h.ts * 1000 + HOUR > from && h.ts * 1000 <= to);
// [fromSec, toSec) 의 시간 칸 key 합 — '같은 때까지' 비교
export const sumHours = (hourly, key, fromSec, toSec) =>
  list(hourly)
    .filter((h) => isObj(h) && num(h.ts) !== null && h.ts >= fromSec && h.ts < toSec)
    .reduce((sum, h) => sum + (num(h[key]) ?? 0), 0);

// 068 날별 기록의 날 — 'YYYY-MM-DD'·'YYYYMMDD' 둘 다(그 KST 날 00:00 ms), 못 읽으면 null
export function dayMs(date) {
  const m = /^(\d{4})-?(\d{2})-?(\d{2})$/.exec(String(date ?? ''));
  return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) - 9 * HOUR : null;
}
// 날별 선 — rows 를 날 순서로, pick(row) 이 수가 아니면 null(선이 끊긴다)
export function dailyLine(rows, pick) {
  return list(rows)
    .filter(isObj)
    .map((r) => [dayMs(r.date), num(pick(r))])
    .filter((p) => p[0] !== null)
    .sort((a, b) => a[0] - b[0]);
}

// --- CSV (066 §3.2 — 보이는 구간·보이는 계열 그대로, 시각은 UTC·KST 두 칸, 브라우저에서 만든 Blob) ---------------------

// 칸 하나 — 수는 그대로, 글자는 엑셀이 수식으로 읽지 않게(= + - @ 탭 CR 로 시작하면 ' 를 앞에) 한 뒤 쉼표·따옴표·줄바꿈이 있으면 따옴표
export function csvCell(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '';
  let s = clean(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
// 머리 한 줄 + 행, CRLF, 맨 앞 BOM(엑셀이 한국어를 UTF-8 로 읽게)
export const toCsv = (head, rows) => `﻿${[head, ...rows].map((r) => r.map(csvCell).join(',')).join('\r\n')}\r\n`;
// kimptrack-<카드 id>-<시작>-<끝>.csv — 시각은 KST, id 의 파일 이름에 못 쓰는 글자는 '-'
export const csvName = (id, from, to) => `kimptrack-${String(id).replace(/[^\w.-]+/g, '-')}-${kstStamp(from)}-${kstStamp(to)}.csv`;

// 시계열 → 원자료 행 — 보이는 계열의 시각 합집합(구간 안). 계열마다 값(+ 이전 기간 값 — 그 계열에 비교선이 있을 때)
export function seriesRows(series, from, to) {
  const times = new Set();
  const maps = series.map((s) => {
    const cur = new Map(crop(s.data, from, to).map((p) => [p[0], num(p[1])]));
    const cmp = s.cmp ? new Map(crop(s.cmp, from, to).map((p) => [p[0], num(p[1])])) : null;
    for (const t of cur.keys()) times.add(t);
    if (cmp) for (const t of cmp.keys()) times.add(t);
    return [cur, cmp];
  });
  return [...times]
    .sort((a, b) => a - b)
    .map((t) => [t, ...maps.flatMap(([cur, cmp]) => (cmp ? [cur.get(t) ?? null, cmp.get(t) ?? null] : [cur.get(t) ?? null]))]);
}
export const seriesHead = (series) => series.flatMap((s) => (s.cmp ? [s.name, `${s.name} (이전)`] : [s.name]));
export function seriesCsv(series, from, to) {
  const rows = seriesRows(series, from, to).map(([t, ...vals]) => [utcText(t), kstText(t, true), ...vals]);
  return toCsv(['시각(UTC)', '시각(KST)', ...seriesHead(series)], rows);
}

// --- 상위 목록·100% 띠·정렬 ---------------------------------------------------------------------

// 상위 목록 행 — items [{ key, name, value, … }] 중 값 > 0 을 큰 순(같으면 받은 순서)으로 n 행. ratio = 값 ÷ 최댓값(글 뒤 막대 폭),
// share = 값 ÷ 분모(total 이 없으면 행 합)
export function topRows(items, n, total) {
  const ok = list(items).filter((it) => isObj(it) && num(it.value) !== null && it.value > 0);
  ok.sort((a, b) => b.value - a.value);
  const max = ok.length ? ok[0].value : 0;
  const denom = num(total) !== null && total > 0 ? total : ok.reduce((s, it) => s + it.value, 0);
  return ok.slice(0, n ?? ok.length).map((it) => ({ ...it, ratio: max > 0 ? it.value / max : 0, share: denom > 0 ? it.value / denom : null }));
}

// 100% 띠 — 값 큰 순 다섯 조각 + 기타(나머지 합, 이름이 기타·그 밖인 칸 포함). share = 값 ÷ 합
const OTHER = new Set(['(기타)', '기타', 'other']);
export function split100(items, k = 5) {
  const ok = list(items).filter((it) => isObj(it) && num(it.value) !== null && it.value > 0);
  const total = ok.reduce((s, it) => s + it.value, 0);
  if (!total) return [];
  const head = ok.filter((it) => !OTHER.has(it.key)).sort((a, b) => b.value - a.value).slice(0, k);
  const rest = ok.filter((it) => !head.includes(it)).reduce((s, it) => s + it.value, 0);
  const out = head.map((it) => ({ ...it, share: it.value / total }));
  if (rest > 0) out.push({ key: '(기타)', name: '기타', mark: 'kx', value: rest, share: rest / total, other: true });
  return out;
}

// 표 정렬 — 수는 수로, 글자는 한국어 순서로. 값 없음(null·'')은 방향과 무관하게 늘 뒤. 같으면 원래 순서(안정 정렬)
export function sortRows(rows, key, dir = 'desc') {
  const sign = dir === 'asc' ? 1 : -1;
  const val = (r) => (typeof key === 'function' ? key(r) : r?.[key]);
  return list(rows)
    .map((r, i) => [r, i])
    .sort(([a, i], [b, j]) => {
      const x = val(a);
      const y = val(b);
      const xNone = x === null || x === undefined || x === '' || (typeof x === 'number' && !Number.isFinite(x));
      const yNone = y === null || y === undefined || y === '' || (typeof y === 'number' && !Number.isFinite(y));
      if (xNone || yNone) return xNone === yNone ? i - j : xNone ? 1 : -1;
      const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'ko');
      return c === 0 ? i - j : sign * c;
    })
    .map(([r]) => r);
}

// 찾기 — 글자 칸들 가운데 하나라도 찾는 말을 품으면(대소문자 무관)
export function matches(row, keys, q) {
  const needle = clean(q).trim().toLowerCase();
  return !needle || keys.some((k) => clean(row?.[k]).toLowerCase().includes(needle));
}
