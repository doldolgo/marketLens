// Clarity 모델과 상세 판(스펙 066 §3.2·§3.3) — 040 요약 + 068 날별 기록·차원. 큰 수 여섯의 값 고르기, 맞대기(우리 방문자 038), 페이지 × 기기
// 칸 진하기, 차원 목록, 판(날별 선 + 같은 길이의 앞 구간 · 신호 여섯 · 표들). DOM 없음.
import { dec1, int, isObj, kstDay, kstText, list, n0, num, partOf, pct, req, usable } from './common.js';
import { SIGNALS, clarityDevice, clarityPage } from './names.js';
import { HISTORY_RANGES, accessReqs, accessWindow, historyCompare, historyReqs, historyWindow } from './models.js';

export const CLARITY = req('clarity');
export const state = { refTab: 'channel' };
// 우리 방문자는 30일까지(038) — 90일 창의 맞대기도 30일 응답
export const accessRange = (range) => (range === '7d' ? '7d' : '30d');
const NO_CMP = () => ({ prev: false, week: false });

// 큰 수 여섯 — [id, 이름, 기록 행·요약에서 값 고르기, 글자]
export const KPIS = Object.freeze([
  ['sessions', '세션', (r) => r?.traffic?.sessions, int],
  ['botSessions', '봇 세션', (r) => r?.traffic?.botSessions, int],
  ['users', '사용자', (r) => r?.traffic?.users, int],
  ['pps', '페이지/세션', (r) => r?.traffic?.pagesPerSession, dec1],
  ['scroll', '평균 스크롤 깊이', (r) => r?.summary?.scrollDepth, (v) => pct(v, 0)],
  ['active', '활성 시간', (r) => r?.summary?.activeSec, (v) => (num(v) === null ? '–' : `${int(v)}초`)],
]);
// 마지막 두 기록(날)의 값 — '전날 대비'
export function lastTwo(rows, pick) {
  const vals = list(rows).map((r) => num(pick(r)));
  return [vals.at(-1) ?? null, vals.at(-2) ?? null];
}
// 요약의 값은 마지막 성공(040 — error 여도 값이 있으면 그린다)
export const okBase = (part) => usable(part, 'numOfDays');

// 맞대기 — Clarity 세션(동의한 방문자만) vs 우리 방문자(서버 기록, 날마다 셈). 둘의 비 = 동의 비율의 어림
export function matchModel(app, range) {
  const h = historyWindow(app, range, 'none', app.now());
  const a = accessWindow(app, accessRange(range), 'none');
  const out = { part: h.part, deps: [...h.deps, ...a.deps], ok: h.ok };
  if (!h.ok) return out;
  out.startMs = h.startMs;
  out.endMs = h.endMs;
  // 우리 방문자 날 점을 Clarity 날 점(그날 00:00 KST)과 같은 x 에 — 한 툴팁에 같은 날 둘이 나란히
  const clip = (pts) => pts.map((p) => [kstDay(Math.floor(p[0] / 1000)) * 1000, p[1]]).filter((p) => p[0] >= h.startMs && p[0] < h.endMs);
  out.series = [
    { name: 'Clarity 세션', mark: 'acc', data: h.line((r) => r?.traffic?.sessions).data },
    { name: '확인된 방문자', mark: 'c1', data: a.ok ? clip(a.days('confirmed').data) : [] },
    { name: '브라우저 모양 방문자', mark: 'c3', data: a.ok ? clip(a.days('shaped').data) : [] },
  ];
  return out;
}
export const matchNeeds = (range) => [...historyReqs(range, 'none'), ...accessReqs(accessRange(range), 'none')];

// 페이지 × 기기(040 pages.groups) — 열: 세션 · 스크롤 · 활성 초 · 신호 여섯. 칸 진하기 = 그 열 최댓값 대비 다섯 단계
export const PAGE_COLS = Object.freeze([
  ['sessions', '세션', int],
  ['scrollDepth', '스크롤', (v) => pct(v, 0)],
  ['activeSec', '활성 초', int],
  ...SIGNALS.map(([key, name]) => [`${key}Pct`, name, (v) => pct(v, 1)]),
]);
export const shade = (v, max) => (num(v) === null || !(max > 0) || v <= 0 ? '' : `q${Math.min(5, Math.max(1, Math.ceil((v / max) * 5)))}`);
// 세션 3 미만 칸은 흐리게(표본 적음)
export const groupRows = (part) =>
  list(part?.groups)
    .filter(isObj)
    .map((g) => ({ ...g, page: clarityPage(g.page), device: clarityDevice(g.device), thin: n0(g.sessions) < 3 }));
// 차원 목록(068 dims · 040 countries) [[이름, 세션]] — 색은 순서대로(이름이 Clarity 의 글자라 표가 없다)
export const dimItems = (rows) =>
  list(rows)
    .filter((r) => Array.isArray(r) && typeof r[0] === 'string')
    .map(([name, n], i) => ({ key: name, name, value: n0(n), mark: `k${Math.min(8, i + 1)}` }));
export const dimsOf = (part) => (okBase(part) ? partOf({ body: part }, 'dims') : part);
const shareRows = (items) => {
  const total = items.reduce((s, it) => s + it.value, 0);
  return items.map((it) => ({ ...it, share: total ? (it.value / total) * 100 : null }));
};
const LIST_COLS = (label) => [{ key: 'name', label }, { key: 'value', label: '세션', num: true, fmt: int }, { key: 'share', label: '비율', num: true, fmt: (v) => pct(v, 1) }];

// --- 판 -----------------------------------------------------------------------------------------

function historyDetail(title, unit, build, compare = historyCompare) {
  return {
    title,
    unit,
    ranges: HISTORY_RANGES,
    range: (r) => (HISTORY_RANGES.includes(r) ? r : '30d'),
    compare,
    needs: (app, pv) => historyReqs(pv.range, pv.cmp),
    model: (app, pv) => {
      const h = historyWindow(app, pv.range, pv.cmp, app.now());
      const base = { part: h.part, deps: h.deps, note: h.ok && h.firstMs !== null ? `기록은 ${kstText(h.firstMs).slice(5, 10)} 부터 모음 · 그날 마지막으로 받은 직전 24시간 값` : '' };
      return h.ok ? { ...base, span: [h.startMs, h.endMs], time: { startMs: h.startMs, endMs: h.endMs, daily: true, ...build(h) } } : base;
    },
  };
}
function baseDetail(title, unit, build) {
  return {
    title,
    unit,
    compare: NO_CMP,
    needs: () => [CLARITY],
    model: (app) => {
      const entry = app.entry(CLARITY);
      const part = partOf(entry);
      return { deps: [entry, state.refTab], ...build(part, app) };
    },
  };
}

export const INSIGHT_DETAILS = Object.freeze({
  ...Object.fromEntries(
    KPIS.map(([id, name, pick, fmt]) => [
      id,
      historyDetail(name, '날별 · 점선은 같은 길이의 앞 구간', (h) =>
        id === 'active'
          ? { panes: [{ fmt }], series: [{ name: '활성 시간', mark: 'acc', ...h.line(pick) }, { name: '전체 시간', mark: 'c1', ...h.line((r) => r?.summary?.totalSec) }] }
          : { panes: [{ fmt }], series: [{ name, mark: 'acc', ...h.line(pick) }] },
      ),
    ]),
  ),
  signals: historyDetail('불만 신호', '날별 · 신호가 있던 세션 %', (h) => ({
    panes: [{ fmt: (v) => pct(v, 1) }],
    series: SIGNALS.map(([key, name], i) => ({ name, mark: `c${i + 1}`, ...h.line((r) => r?.summary?.signals?.[key]?.sessionPct), cmp: null })),
  }), NO_CMP),
  match: {
    title: '맞대기',
    unit: 'Clarity 세션 vs 우리 방문자 · 날별',
    ranges: HISTORY_RANGES,
    range: (r) => (HISTORY_RANGES.includes(r) ? r : '30d'),
    compare: NO_CMP,
    needs: (app, pv) => matchNeeds(pv.range),
    model: (app, pv) => {
      const m = matchModel(app, pv.range);
      const base = { part: m.part, deps: m.deps, note: '우리 방문자는 최근 30일까지(서버 기록)' };
      return m.ok ? { ...base, span: [m.startMs, m.endMs], time: { startMs: m.startMs, endMs: m.endMs, daily: true, panes: [{ fmt: int }], series: m.series } } : base;
    },
  },
  pages: baseDetail('페이지 × 기기', '최근 72시간 · 세션 가중 평균', (part) => {
    const pages = okBase(part) ? partOf({ body: part }, 'pages') : part;
    if (!usable(pages, 'groups')) return { part: pages };
    const columns = [{ key: 'page', label: '페이지' }, { key: 'device', label: '기기' }, ...PAGE_COLS.map(([key, label, fmt]) => ({ key, label, num: true, fmt }))];
    return { part: pages, table: { title: '칸마다', columns, rows: groupRows(pages), sort: { key: 'sessions', dir: 'desc' }, search: ['page', 'device'] } };
  }),
  countries: baseDetail('나라', '세션 · 최근 24시간', (part) =>
    okBase(part) ? { part, table: { title: '나라', columns: LIST_COLS('나라'), rows: shareRows(dimItems(part.countries)), sort: { key: 'value', dir: 'desc' }, search: ['name'] } } : { part },
  ),
  dims: baseDetail('기기 · 브라우저 · OS', '세션 · 하루 한 번 받음', (part) => {
    const dims = dimsOf(part);
    if (!usable(dims)) return { part: dims };
    const rows = [['device', '기기'], ['browser', '브라우저'], ['os', 'OS']].flatMap(([k, group]) => shareRows(dimItems(dims[k])).map((r) => ({ ...r, group })));
    return { part: dims, table: { title: '차원', columns: [{ key: 'group', label: '차원' }, ...LIST_COLS('이름')], rows, sort: { key: 'value', dir: 'desc' }, search: ['group', 'name'] } };
  }),
  refs: baseDetail('유입', 'Channel · Source · 세션', (part) => {
    const dims = dimsOf(part);
    if (!usable(dims)) return { part: dims };
    const rows = [['channel', 'Channel'], ['source', 'Source']].flatMap(([k, group]) => shareRows(dimItems(dims[k])).map((r) => ({ ...r, group })));
    return { part: dims, table: { title: '유입', columns: [{ key: 'group', label: '차원' }, ...LIST_COLS('이름')], rows, sort: { key: 'value', dir: 'desc' }, search: ['group', 'name'] } };
  }),
});
