// 피드 → 그림 모델(스펙 066 §3.2·§3.3) — 카드와 상세 판이 같은 함수로 같은 값을 그린다. 기간·비교 원천 고르기(063 시계열 +
// 068 offset=prev, 038 의 더 긴 창에서 잘라 쓰기, 068 날별 기록)와 수집·경보·방문 계산. DOM·echarts 없음(node 시험 대상).
import { isObj, kstDay, list, n0, num, partOf, req, usable } from './common.js';
import { align, cellPoints, crop, dailyLine, ms } from './calc.js';

const HOUR = 3_600_000;
const DAY = 86_400_000;
export const RANGE_MS = Object.freeze({ '1h': HOUR, '6h': 6 * HOUR, '24h': DAY, '7d': 7 * DAY, '30d': 30 * DAY, '90d': 90 * DAY });

// --- AWS 시계열(063) — 비교: 이전 기간 = 068 의 offset=prev(1시간은 6시간 응답 안에서), 지난주 같은 때 = 7일 창의 이전 기간
// (range=7d&offset=prev — 지금 7일 창은 [지금 − 7일, 지금] 이라 지난주 같은 때를 담지 않는다)에서 잘라(7일 이하) ---

export const SERIES_RANGES = Object.freeze(['1h', '6h', '24h', '7d', '30d']);
const SERIES_FEED = Object.freeze({ '1h': '6h', '6h': '6h', '24h': '24h', '7d': '7d', '30d': '30d' });
// 068 배포 전 서버는 offset 을 모르고 지금 창을 다시 준다 — 한 번 보면 이 문서에서 offset 을 쓰는 비교를 끈다('이 자료는 비교 없음')
const seen = { prevMissing: false };
export const seriesCompare = (range) => {
  const known = Object.hasOwn(SERIES_FEED, range);
  return { prev: known && (range === '1h' || !seen.prevMissing), week: known && range !== '30d' && !seen.prevMissing };
};
export function seriesReqs(range, cmp) {
  const base = SERIES_FEED[range] ?? '24h';
  const out = [req('series', { range: base })];
  if (cmp === 'prev' && range !== '1h') out.push(req('series', { range: base, offset: 'prev' }));
  if (cmp === 'week' && range !== '30d') out.push(req('series', { range: '7d', offset: 'prev' }));
  return out;
}
// 고른 창의 시계열 — { ok, part, startMs, endMs, periodSec, boxes, cmpState(none·wait·ok·missing), deps, line(get) → { data, cmp } }.
// get(응답) 은 [[ts초, v]] 를 돌려준다. offset 을 모르는 서버(068 배포 전)는 지금 창을 다시 주므로 offset 키가 없으면 비교 없음
export function seriesWindow(app, range, cmp) {
  const [main, other] = seriesReqs(range, cmp);
  const entry = app.entry(main);
  const part = partOf(entry);
  const out = { entry, part, ok: usable(part), cmpState: cmp === 'none' ? 'none' : 'wait', deps: [entry, other ? app.entry(other) : null] };
  if (!out.ok) return out;
  const L = RANGE_MS[range] ?? DAY;
  out.endMs = n0(part.endTs) * 1000;
  out.startMs = SERIES_FEED[range] === range ? n0(part.startTs) * 1000 : out.endMs - L;
  out.periodSec = part.periodSec;
  let src = null;
  let by = 0;
  if (cmp === 'prev' && range === '1h') {
    src = part;
    by = L;
  } else if (other) {
    const o = partOf(app.entry(other));
    const shifted = !other.url.includes('offset=prev') || o?.offset === 'prev';
    if (usable(o) && shifted) {
      src = o;
      by = cmp === 'week' ? 7 * DAY : L;
    } else if (o !== undefined) {
      out.cmpState = 'missing';
      if (usable(o) && !shifted) seen.prevMissing = true;
    }
  }
  if (src) out.cmpState = 'ok';
  out.line = (get) => ({ data: crop(ms(get(part)), out.startMs, out.endMs), cmp: src ? align(ms(get(src)), out.startMs, out.endMs, by) : null });
  out.boxes = list(part.boxes).filter((b) => isObj(b) && isObj(b.series));
  return out;
}
export const boxGet = (name, key) => (p) => list(p?.boxes).find((b) => isObj(b) && b.box === name)?.series?.[key];

// --- 접속 요약(038) — 비교: 24시간의 이전 기간은 7일 응답의 hourly 에서, 7일은 30일 응답에서 잘라 쓴다(7일의 지난주 같은 때 = 이전 기간).
// 24시간의 지난주 같은 때는 7일 창 밖이라 없다. 30일은 비교 없음 ---

export const ACCESS_RANGES = Object.freeze(['24h', '7d', '30d']);
const ACCESS_CMP = Object.freeze({ '24h': '7d', '7d': '30d' });
export const accessCompare = (range) => ({ prev: Object.hasOwn(ACCESS_CMP, range), week: range === '7d' });
export function accessReqs(window, cmp) {
  const out = [req('access', { window })];
  if (cmp !== 'none' && Object.hasOwn(ACCESS_CMP, window)) out.push(req('access', { window: ACCESS_CMP[window] }));
  return out;
}
// { ok, part, startMs, endMs, cmpState, deps, hours(key) → { data, cmp }, days(key) → { data, cmp } } — 이전 기간 점은 지금 축으로 민 것.
// 시·날 칸 점은 칸이 창과 겹친 구간의 가운데(cellPoints — 지금 칸이 창 끝을 넘지 않는다)
export function accessWindow(app, window, cmp) {
  const [main, other] = accessReqs(window, cmp);
  const entry = app.entry(main);
  const part = partOf(entry);
  const out = { entry, part, ok: usable(part), cmpState: cmp === 'none' ? 'none' : 'wait', deps: [entry, other ? app.entry(other) : null] };
  if (!out.ok) return out;
  out.startMs = n0(part.startTs) * 1000;
  out.endMs = n0(part.endTs) * 1000;
  const by = cmp === 'week' ? 7 * DAY : (RANGE_MS[window] ?? DAY);
  const o = other ? partOf(app.entry(other)) : undefined;
  const src = usable(o) ? o : null;
  if (src) out.cmpState = 'ok';
  else if (other && o !== undefined) out.cmpState = 'missing';
  const cells = (rows, key, width, shift = 0) => cellPoints(rows, key, width, out.startMs, out.endMs, shift);
  out.hours = (key) => ({ data: cells(part.hourly, key, HOUR), cmp: src ? cells(src.hourly, key, HOUR, by) : null });
  const visits = (p) => (isObj(p?.visitors) && p.visitors.state === 'ok' ? p.visitors.days : null);
  out.days = (key) => ({ data: cells(visits(part), key, DAY), cmp: src ? cells(visits(src), key, DAY, by) : null });
  return out;
}

// 오늘(KST — 응답 endTs 가 든 날) 방문자 [확인, 브라우저로 온]. visitors 가 ok 가 아니면 null(064)
export function todayVisitors(a) {
  const v = a?.visitors;
  if (!isObj(v) || v.state !== 'ok') return null;
  const day = kstDay(n0(a.endTs));
  const d = list(v.days).find((x) => isObj(x) && x.ts === day);
  return d ? [n0(d.confirmed), n0(d.shaped)] : [0, 0];
}

// --- Clarity 날별 기록(068 history) — 비교: 같은 길이의 앞 구간(7일은 30일 기록에서, 30일은 90일 기록에서). 90일은 비교 없음 ---

export const HISTORY_RANGES = Object.freeze(['7d', '30d', '90d']);
const HISTORY_DAYS = Object.freeze({ '7d': 7, '30d': 30, '90d': 90 });
const HISTORY_CMP = Object.freeze({ '7d': 30, '30d': 90 });
export const historyCompare = (range) => ({ prev: Object.hasOwn(HISTORY_CMP, range), week: false });
export const historyReqs = (range, cmp) => [req('history', { days: cmp === 'prev' && Object.hasOwn(HISTORY_CMP, range) ? HISTORY_CMP[range] : (HISTORY_DAYS[range] ?? 30) })];
// { ok, part, startMs, endMs, rows(창 안), firstMs(가장 이른 기록), deps, line(pick) → { data, cmp } }
export function historyWindow(app, range, cmp, now) {
  const [r] = historyReqs(range, cmp);
  const entry = app.entry(r);
  const part = partOf(entry);
  const out = { entry, part, ok: usable(part), deps: [entry], cmpState: cmp === 'none' ? 'none' : 'wait' };
  if (!out.ok) return out;
  const n = HISTORY_DAYS[range] ?? 30;
  out.endMs = kstDay(Math.floor(now / 1000)) * 1000 + DAY;
  out.startMs = out.endMs - n * DAY;
  const all = list(part.rows).filter(isObj);
  const first = dailyLine(all, () => 0)[0];
  out.firstMs = first ? first[0] : null;
  const cmpOn = cmp === 'prev' && Object.hasOwn(HISTORY_CMP, range);
  out.cmpState = cmpOn ? 'ok' : cmp === 'none' ? 'none' : 'missing';
  // 날 칸은 [시작, 끝) — 끝(내일 00:00)의 칸은 창 밖
  out.line = (pick) => {
    const points = dailyLine(all, pick);
    return { data: crop(points, out.startMs, out.endMs - 1), cmp: cmpOn ? align(points, out.startMs, out.endMs - 1, n * DAY) : null };
  };
  out.rows = all.filter((row) => {
    const t = dailyLine([row], () => 0)[0]?.[0];
    return t !== undefined && t >= out.startMs && t < out.endMs;
  });
  return out;
}

// --- 수집(011)·알림(034) ------------------------------------------------------------------

// 시간 칸마다 수집 성공률 — 011 의 1시간 식(1 − 실패 구간과 겹친 초 ÷ 칸 길이)을 원천 전체로 평균. [[칸 가운데 ms, %]] (064).
// 지금 칸의 점은 지금까지의 가운데(창 끝 = 지금을 넘지 않게 — 아래 둘도 같다)
export function hourlySuccess(body, now) {
  const n = list(body?.exchanges).filter(isObj).length;
  if (!n) return [];
  const outages = list(body.outages).filter((o) => isObj(o) && num(o.startedAt) !== null);
  const top = Math.floor(now / HOUR) * HOUR;
  const out = [];
  for (let i = 23; i >= 0; i -= 1) {
    const from = top - i * HOUR;
    const to = Math.min(from + HOUR, now);
    if (to <= from) continue;
    const down = outages.reduce((sum, o) => sum + Math.max(0, Math.min(num(o.endedAt) ?? now, to) - Math.max(o.startedAt, from)), 0);
    out.push([(from + to) / 2, Math.max(0, 100 * (1 - down / (n * (to - from))))]);
  }
  return out;
}
// 시간 칸마다 끊긴 분(원천 합) — [[칸 가운데 ms, 분]]
export function outageMinutes(body, now) {
  const outages = list(body?.outages).filter((o) => isObj(o) && num(o.startedAt) !== null);
  const top = Math.floor(now / HOUR) * HOUR;
  const out = [];
  for (let i = 23; i >= 0; i -= 1) {
    const from = top - i * HOUR;
    const to = Math.min(from + HOUR, now);
    const down = outages.reduce((sum, o) => sum + Math.max(0, Math.min(num(o.endedAt) ?? now, to) - Math.max(o.startedAt, from)), 0);
    out.push([(from + to) / 2, Math.round(down / 6000) / 10]);
  }
  return out;
}
// 시간 칸마다 알림 수 — 보낸 Slack 알림과 ALARM 으로 바뀐 경보(only 가 'slack'·'alarm' 이면 그것만). hours 칸, [[칸 가운데 ms, 수]] (064)
export function alertHours(items, now, hours = 24, only = null) {
  const top = Math.floor(now / HOUR) * HOUR;
  const counts = new Map();
  for (let i = hours - 1; i >= 0; i -= 1) counts.set(top - i * HOUR, 0);
  for (const it of list(items).filter(isObj)) {
    if (!(it.source === 'slack' || (it.source === 'alarm' && it.toState === 'ALARM')) || num(it.at) === null) continue;
    if (only && it.source !== only) continue;
    const hour = Math.floor(it.at / HOUR) * HOUR;
    if (counts.has(hour)) counts.set(hour, counts.get(hour) + 1);
  }
  return [...counts].map(([hour, count]) => [(hour + Math.min(hour + HOUR, now)) / 2, count]);
}
