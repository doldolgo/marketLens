// 화면 페이지의 값 계산(스펙 065 §3.3) — 고른 화면 × 기기 행에서 요소·영역·떠난 곳·깊이, 덮어 보기로 넘길 값. DOM 없음(node 시험이 바로 부른다).
// 피드(052·061 /admin/attention): rows = [{ page, device, pv, areas:[{id, ms, clicks, seen}], exits:[[영역, n]], depth(11칸|null), elements? }].
import { isObj, list, n0, num, own, partOf, req, usable } from './common.js';
import { pageName } from './names.js';
import { OTHER_KEY, elName } from './screensnames.js';

// 화면 칩 차례(065 §3.1) — 정적 넷, 대시보드 탭 여섯. 기본 화면에서 pv 가 같으면 앞의 것
export const SCREEN_PAGES = Object.freeze(['landing', 'privacy', 'kimp-chart', 'kimp-history', 'app-spread', 'app-history', 'app-gap', 'app-pp', 'app-health', 'app-flow']);
export const DEVICES = Object.freeze(['pc', 'mobile']);
export const MODES = Object.freeze(['element', 'area', 'depth']);
export const METRICS = Object.freeze(['clicks', 'hover', 'vis']);
export const DAYS = Object.freeze({ '1d': 1, '7d': 7, '30d': 30, '90d': 90 });
export const PERIOD_NAME = Object.freeze({ '1d': '오늘', '7d': '최근 7일', '30d': '최근 30일', '90d': '최근 90일' });
export const DEVICE_NAME = Object.freeze({ pc: 'PC', mobile: '휴대폰' });
export const MODE_NAME = Object.freeze({ element: '요소', area: '영역', depth: '깊이' });
export const METRIC_NAME = Object.freeze({ clicks: '클릭', hover: '마우스 올림', vis: '보인 시간' });
const METRIC_FIELD = Object.freeze({ clicks: 'clicks', hover: 'hoverMs', vis: 'visMs' });
export const SMALL_PV = 5; // 표본 적음 — 페이지뷰가 이보다 적다
const LOW_REACH = 20; // 거의 안 닿음 — 도달률(%)이 이보다 낮다
export const FEED_MAX = 300; // 피드의 요소 상한(061)
const AREA_ID = /^[a-z][a-z0-9-]{0,31}$/; // 052 영역 id 꼴
const KEY_OK = /^[^|\u0000-\u001f\u007f-\u009f]{1,64}$/; // 061 열쇠 꼴
export const validArea = (id) => typeof id === 'string' && AREA_ID.test(id);
export const validKey = (key) => typeof key === 'string' && KEY_OK.test(key);
export const isDashboard = (page) => String(page).startsWith('app-');
export const screenName = (page) => pageName(page);
// 주소 인자(065 §3.2 — page·device·mode·metric) — [기본값, …허용 값]. 화면의 기본은 null(고르기 전 — pv 가 가장 큰 화면을 따른다)
export const PARAMS = Object.freeze({ page: [null, ...SCREEN_PAGES], device: DEVICES, mode: MODES, metric: METRICS });

// 화면 상태(주소 밖) — 오른쪽 판 탭·요소 표 영역 거르기·강조한 행, 틀(띄운 열쇠·화면·ready·5초 무응답·보낸 값), 기본 화면(직전)
export const ui = { panel: 'elements', area: '', focus: null, focusArea: null, key: '', page: null, ready: null, silent: false, wait: 0, sent: null, last: null };

const daysOf = (range) => own(DAYS, range) ?? 7;
export const listReq = (range) => req('attention', { days: daysOf(range) });
export const pageReq = (range, page) => req('attention', { days: daysOf(range), page });

export const rowOf = (rows, page, device) => list(rows).find((r) => isObj(r) && r.page === page && r.device === device) ?? null;

// 지금 화면 — 고른 화면, 아니면 page 없는 응답에서 pv 가 가장 큰 화면. 그 응답을 기다리는 동안은 직전 화면(없으면 null — 아직 모름)
export function screenOf(app) {
  if (app.view.p.page) return app.view.p.page;
  const part = partOf(app.entry(listReq(app.view.range)));
  if (part === undefined) return ui.last;
  ui.last = busiest(usable(part) ? part.rows : [], app.view.p.device);
  return ui.last;
}

// 기본 화면(065 §3.2) — 고른 기간·기기에서 pv 가 가장 큰 화면(같으면 칩 차례의 앞, 모두 0 이면 첫 화면)
export function busiest(rows, device) {
  let best = SCREEN_PAGES[0];
  let most = 0;
  for (const page of SCREEN_PAGES) {
    const pv = n0(rowOf(rows, page, device)?.pv);
    if (pv > most) [best, most] = [page, pv];
  }
  return best;
}

const r1 = (v) => Math.round(v * 10) / 10;
// 요소(065 §3.3) — c·cRate(100뷰당)·h(평균 마우스 올림 초 — 휴대폰은 null)·v(평균 보인 시간 초 — 행은 null), m = 고른 지표 값.
// 단계·순위는 지금 화면에 있는 요소끼리(present = ready 의 "영역|열쇠" — null 이면 판정하지 않음, 행은 늘 있는 것으로): 단계 =
// ⌈m / 최대 × 5⌉(m = 0 이고 보인 시간이 있으면 0, 그 밖의 m = 0 은 그리지 않음 — null), 순위 = m 이 0 보다 큰 것의 m 큰 순(같으면 피드 차례).
// 지금 화면에 없는 요소는 같은 최대 대비 단계(5 에서 멈춤)·순위 없이 끝에, (기타)는 그 뒤
export function elementValues(row, metric, present, device) {
  const pv = n0(row?.pv);
  const field = own(METRIC_FIELD, metric) ?? 'clicks';
  const all = list(row?.elements)
    .filter((e) => isObj(e) && typeof e.area === 'string' && AREA_ID.test(e.area) && typeof e.key === 'string' && KEY_OK.test(e.key))
    .map((e, order) => {
      const other = e.key === OTHER_KEY;
      const absent = !other && present !== null && !e.key.startsWith('row:') && !present.has(`${e.area}|${e.key}`);
      const isRow = e.key.startsWith('row:');
      return {
        area: e.area,
        key: e.key,
        order,
        clicks: n0(e.clicks),
        hoverMs: n0(e.hoverMs),
        visMs: n0(e.visMs),
        m: n0(e[field]),
        c: n0(e.clicks),
        cRate: pv ? r1((n0(e.clicks) / pv) * 100) : 0,
        h: device === 'mobile' || !pv ? null : r1(n0(e.hoverMs) / pv / 1000),
        v: isRow || !pv ? null : r1(n0(e.visMs) / pv / 1000),
        state: other ? 'other' : absent ? 'gone' : 'ok',
      };
    });
  const on = all.filter((e) => e.state === 'ok').sort((a, b) => b.m - a.m || a.order - b.order);
  const max = on.length ? on[0].m : 0;
  const level = (e) => (e.m > 0 && max > 0 ? Math.min(5, Math.ceil((e.m * 5) / max)) : e.m === 0 && e.visMs > 0 ? 0 : null);
  let rank = 0;
  for (const e of on) {
    e.level = level(e);
    e.rank = e.m > 0 ? ++rank : null;
  }
  const gone = all.filter((e) => e.state === 'gone').sort((a, b) => b.m - a.m || a.order - b.order);
  for (const e of gone) {
    e.level = level(e);
    e.rank = null;
  }
  const other = all.filter((e) => e.state === 'other');
  for (const e of other) {
    e.level = null;
    e.rank = null;
  }
  return { pv, small: pv < SMALL_PV, max, rows: [...on, ...gone, ...other], capped: list(row?.elements).length >= FEED_MAX };
}

// 영역(053 §3.3 그대로) — t = 평균 보인 시간(초), r = 도달률(%, 100 에서 멈춤), c = 100뷰당 클릭, 단계 = ⌈ms × 5 / 최대⌉, 순위 = ms 큰 순.
// present = ready 의 영역 id(null 이면 기록 있는 영역을 모두 페이지에 있는 것으로). ranked = 페이지에 있고 기록 있음, blank = 페이지에
// 있는데 기록 없음(단계 0), away = 기록은 있는데 지금 페이지에 없음(순위 없이 끝에)
export function areaValues(row, present) {
  const pv = n0(row?.pv);
  const data = list(row?.areas).filter((a) => isObj(a) && typeof a.id === 'string' && AREA_ID.test(a.id));
  const here = present ? new Set(present) : null;
  const value = (a) => ({
    id: a.id,
    ms: n0(a.ms),
    t: pv ? Math.round(n0(a.ms) / pv / 100) / 10 : 0,
    r: pv ? Math.min(100, Math.round((n0(a.seen) / pv) * 100)) : 0,
    c: pv ? Math.round((n0(a.clicks) / pv) * 1000) / 10 : 0,
  });
  const on = data.filter((a) => !here || here.has(a.id)).map(value).sort((x, y) => y.ms - x.ms);
  const top = on.length ? on[0].ms : 0;
  const level = (ms) => (ms > 0 && top > 0 ? Math.min(5, Math.ceil((ms * 5) / top)) : 0);
  const small = pv < SMALL_PV;
  const mark = (a, rank, state) => ({ ...a, level: level(a.ms), rank, low: a.r < LOW_REACH, small, state });
  const known = new Set(data.map((a) => a.id));
  return {
    pv,
    small,
    top,
    ranked: on.map((a, i) => mark(a, i + 1, 'ok')),
    blank: here ? [...here].filter((id) => !known.has(id)).map((id) => ({ id, ms: 0, t: 0, r: 0, c: 0, level: 0, rank: null, low: false, small, state: 'none' })) : [],
    away: here ? data.filter((a) => !here.has(a.id)).map((a) => mark(value(a), null, 'gone')) : [],
  };
}

// 떠난 곳(065 §3.3) — 영역마다 n / Σn(%). share = Σn / pv(pagehide 로 끝난 pv 의 몫)
export function exitValues(row) {
  const pv = n0(row?.pv);
  const items = list(row?.exits).filter((x) => Array.isArray(x) && typeof x[0] === 'string' && num(x[1]) !== null && x[1] > 0);
  const total = items.reduce((s, [, n]) => s + n, 0);
  return { total, share: pv ? (total / pv) * 100 : null, rows: items.map(([id, n]) => ({ id, n, pct: total ? (n / total) * 100 : 0 })) };
}

// 깊이(065 §3.3) — 도달[j] = Σ depth[j..10] / Σ depth × 100(j = 0 은 100). half = 도달이 처음 50% 밑으로 가는 줄. 대시보드는 null
export function depthValues(row) {
  const cells = Array.isArray(row?.depth) && row.depth.length === 11 ? row.depth.map(n0) : null;
  if (!cells) return null;
  const total = cells.reduce((s, n) => s + n, 0);
  if (!total) return { total: 0, reach: null, half: null, cells };
  const reach = cells.map((_, j) => (j === 0 ? 100 : (cells.slice(j).reduce((s, n) => s + n, 0) / total) * 100));
  const half = reach.findIndex((r) => r < 50);
  return { total, reach, half: half < 0 ? null : half, cells };
}

const secText = (v) => (v === null ? '–' : `${v.toFixed(1)}초`);
// 꼬리표 글(065 §3.5 — 관리자가 만든다, 페이지가 80자에서 자른다) — 이름 · 고른 지표 값
export function elementTag(page, e, metric) {
  const name = elName(page, e.key);
  if (metric === 'hover') return `${name} · 마우스 올림 평균 ${secText(e.h)}`;
  if (metric === 'vis') return `${name} · 보인 시간 평균 ${secText(e.v)}`;
  return `${name} · 클릭 ${e.c} · 100뷰당 ${e.cRate.toFixed(1)}`;
}

// 덮어 보기 값 메시지(053 + 065 §3.5) — 영역(053 꼴, 지금 페이지에 있는 것), 요소(그릴 것 — 지금 화면에 있고 단계가 있는 것, 300개까지),
// 깊이(정적 페이지의 도달 11칸 또는 null)
export function overlayMessage(o) {
  const { page, mode, metric, label, areas, elements, depth } = o;
  return {
    type: 'kt-attention',
    v: 1,
    label,
    mode,
    areas: areas.ranked.concat(areas.blank).map(({ id, level, rank, t, r, c, low, small }) => ({ id, level, rank, t, r, c, low, small })),
    elements: elements.rows
      .filter((e) => e.state === 'ok' && e.level !== null)
      .slice(0, FEED_MAX)
      .map((e) => ({ area: e.area, key: e.key, level: e.level, rank: e.rank, tag: elementTag(page, e, metric), small: elements.small })),
    depth: !isDashboard(page) && depth?.reach ? depth.reach.map((r) => Math.round(r * 10) / 10) : null,
  };
}

// 052 피드의 before_gate 는 상태다 — 066 배지 규칙(before_gate 도 다른 '연결 안 됨' 과 같고 code 는 싣지 않음)대로 unconfigured·before_gate 로 읽는다
const asPart = (part) => (part?.state === 'before_gate' ? { ...part, state: 'unconfigured', code: 'before_gate' } : part);

// 지금 보기 한 장(065 §3.2·§3.3) — 화면·기기·보기·지표(대시보드의 깊이는 요소, 휴대폰의 마우스 올림은 클릭), 고른 화면 응답의 부분 상태와
// 그 화면 × 기기 행, 틀이 알린 ready(그 화면의 것만)로 센 요소·영역·떠난 곳·깊이
export function current(app) {
  const range = app.view.range;
  const { device } = app.view.p;
  const page = screenOf(app);
  const mode = page && isDashboard(page) && app.view.p.mode === 'depth' ? 'element' : app.view.p.mode;
  const metric = device === 'mobile' && app.view.p.metric === 'hover' ? 'clicks' : app.view.p.metric;
  const listEntry = app.entry(listReq(range));
  const entry = page ? app.entry(pageReq(range, page)) : listEntry;
  const part = asPart(partOf(entry));
  const ok = Boolean(page) && usable(part);
  const row = ok ? rowOf(part.rows, page, device) : null;
  const ready = ui.ready && ui.ready.page === page ? ui.ready : null;
  return {
    page,
    device,
    mode,
    metric,
    range,
    listEntry,
    entry,
    part,
    ok,
    row,
    ready,
    elements: elementValues(row, metric, ready ? ready.elements : null, device),
    areas: areaValues(row, ready ? ready.areas : null),
    exits: exitValues(row),
    depth: depthValues(row),
  };
}
