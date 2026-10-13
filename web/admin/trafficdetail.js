// 트래픽 그림 option 과 상세 판(스펙 066 §3.2·§3.3) — 큰 추이(고른 지표)·흐름·목록 넷(전체 — 찾기·정렬·CSV)·누가·요일×시간·응답 상태·
// WS 연결 시간. 카드의 탭·지표는 state 하나에 두고(traffic.js 가 바꾼다) 판이 같은 값을 읽는다.
import { clean, int, isObj, kstText, list, n0, num, pct } from './common.js';
import { colsOption, heatOption, sankeyOption, tip } from './charts.js';
import { channelName, pageName } from './names.js';
import { ACCESS_RANGES, accessCompare, accessReqs, accessWindow, seriesReqs, seriesWindow } from './models.js';
import { LISTS, WEEKDAY, WS_BUCKETS, denomOf, flowGraph, heatGrid, whoGroups } from './trafficmodel.js';

export const state = { metric: 'visitors', tabs: { src: 'channels', geo: 'countries', ends: 'entries', what: 'paths' } };
export const tabOf = (name) => LISTS[name].find((t) => t[0] === state.tabs[name]) ?? LISTS[name][0];
export const METRIC_NAME = Object.freeze({ visitors: '방문자', pages: '사람 모양 페이지', now: 'WS 접속', bots: '요청 · 사람 모양 페이지', errors: '5xx' });
const NO_CMP = () => ({ prev: false, week: false });

// 큰 추이 — 고른 지표의 시간 그림(24시간은 시간별, 7·30일의 방문자는 날별). 점선 = 비교 기간(지금 축으로 민 것)
export function trendModel(app, range, cmp, which) {
  const a = accessWindow(app, range, cmp);
  const out = { a, part: a.part, deps: a.deps, ok: a.ok, cmpState: a.cmpState };
  if (!a.ok) return out;
  const hourly = (name, key, mark, type) => ({ name, mark, type, ...a.hours(key) });
  if (which === 'visitors' && range !== '24h') {
    out.series = [{ name: '브라우저 모양 방문자', mark: 'acc', type: 'bar', ...a.days('shaped') }, { name: '확인된 방문자', mark: 'c1', ...a.days('confirmed'), cmp: null }, { name: '다시 온', mark: 'c3', ...a.days('returning'), cmp: null }];
  } else if (which === 'visitors' || which === 'pages') {
    out.series = [hourly('사람 모양 페이지', 'humanPages', 'acc', 'bar'), { ...hourly('스크립트가 돈 페이지', 'jsViews', 'c1'), cmp: null }].slice(0, which === 'pages' ? 1 : 2);
  } else if (which === 'bots') {
    out.series = [hourly('요청', 'requests', 'c1'), hourly('사람 모양 페이지', 'humanPages', 'acc')];
  } else if (which === 'errors') {
    out.series = [hourly('5xx', 'errors', 'bad', 'bar'), { ...hourly('WS 재접속 실패', 'wsErrors', 'c8', 'bar'), cmp: null }];
  } else {
    const w = seriesWindow(app, range, cmp);
    out.deps = [...a.deps, ...w.deps];
    out.series = [{ name: 'WS 접속', mark: 'acc', area: true, ...(w.ok ? w.line((p) => p?.wsClients) : { data: [], cmp: null }) }];
  }
  return out;
}
export const trendNeeds = (range, cmp, which) => [...accessReqs(range, cmp), ...(which === 'now' ? seriesReqs(range, cmp) : [])];

export function flowOption(flows) {
  const graph = flowGraph(flows);
  if (!graph.links.length) return null;
  return sankeyOption({
    heads: ['들어온 길', '첫 페이지', '마지막 페이지'],
    nodes: graph.nodes,
    links: graph.links,
    tipNode: (label, v) => tip(label, [['acc', '브라우저 모양', int(v)]]),
    tipLink: (from, to, d) => tip(`${from} → ${to}`, [['acc', '브라우저 모양', int(d?.value)], ['c1', '확인', int(d?.c)]]),
  });
}
export function heatModel(a) {
  const grid = heatGrid(a.hourly);
  const option = heatOption({
    xs: [...Array(24).keys()].map(String),
    ys: WEEKDAY,
    cells: grid.cells.flatMap((row, d) => row.map((v, h) => [h, d, v])),
    head: (x, y) => `${WEEKDAY[y] ?? ''}요일 ${x}시`,
    name: '스크립트가 돈 페이지',
    fmt: int,
  });
  return { grid, option };
}
export function durationsOption(a) {
  const d = isObj(a?.ws?.durations) ? a.ws.durations : {};
  if (!WS_BUCKETS.some(([k]) => n0(d[k]) > 0)) return null;
  return colsOption({ cats: WS_BUCKETS.map(([, l]) => l), values: WS_BUCKETS.map(([k]) => n0(d[k])), name: '끝난 연결', fmt: int });
}
export const FIVES = Object.freeze([
  { key: 'ts', label: '시각(KST)', fmt: (v) => (num(v) === null ? '–' : kstText(v * 1000)), csv: (r) => (num(r.ts) === null ? '' : kstText(r.ts * 1000, true)) },
  { key: 'path', label: '경로', cls: 'mono' },
  { key: 'status', label: '상태', num: true },
]);
export const fiveRows = (a, n) => list(a?.recent5xx).filter(isObj).slice(0, n).map((r) => ({ ts: num(r.ts), path: clean(r.path).slice(0, 100), status: num(r.status) }));

// --- 상세 판 -------------------------------------------------------------------------------------

function accessDetail(title, unit, build, compare = NO_CMP) {
  return {
    title,
    unit,
    ranges: ACCESS_RANGES,
    range: () => '24h',
    compare,
    needs: (app, pv) => accessReqs(pv.range, pv.cmp),
    model: (app, pv) => {
      const a = accessWindow(app, pv.range, pv.cmp);
      const base = { part: a.part, deps: a.deps };
      return a.ok ? { ...base, span: [a.startMs, a.endMs], ...build(app, pv, a) } : base;
    },
  };
}
const listDetail = (name, title) =>
  accessDetail(title, '전체 목록 · 찾기 · 정렬 · CSV', (app, pv, a) => {
    const cur = tabOf(name);
    const items = cur[3](a.part);
    const denom = denomOf(a.part, cur[4]);
    const withC = items.some((it) => it.c !== undefined);
    const columns = [{ key: 'name', label: cur[1] }];
    if (withC) columns.push({ key: 'c', label: '확인', num: true, fmt: int });
    columns.push({ key: 'value', label: cur[4] === '방문자' ? '브라우저 모양' : cur[4], num: true, fmt: int }, { key: 'share', label: '비율', num: true, fmt: (v) => pct(v, 1) });
    const rows = items.map((it) => ({ ...it, share: num(denom) > 0 ? (it.value / denom) * 100 : null }));
    return { title: `${title} · ${cur[1]}`, deps: [...a.deps, cur[0]], table: { title: cur[1], columns, rows, sort: { key: 'value', dir: 'desc' }, search: ['name'] } };
  });

export const TRAFFIC_DETAILS = Object.freeze({
  trend: {
    title: '큰 추이',
    unit: '고른 지표 · 점선은 비교 기간',
    ranges: ACCESS_RANGES,
    range: () => '24h',
    compare: accessCompare,
    related: ['status'],
    needs: (app, pv) => trendNeeds(pv.range, pv.cmp, state.metric),
    model: (app, pv) => {
      const t = trendModel(app, pv.range, pv.cmp, state.metric);
      const base = { part: t.part, deps: [...t.deps, state.metric], title: METRIC_NAME[state.metric], note: t.cmpState === 'missing' ? '이 자료는 비교 없음' : '' };
      return t.ok ? { ...base, span: [t.a.startMs, t.a.endMs], time: { startMs: t.a.startMs, endMs: t.a.endMs, panes: [{ fmt: int }], series: t.series } } : base;
    },
  },
  flows: accessDetail('흐름', '들어온 길 → 첫 페이지 → 마지막 페이지', (app, pv, a) => {
    const v = isObj(a.part.visitors) && a.part.visitors.state === 'ok' ? a.part.visitors : null;
    const flows = list(v?.flows).filter((r) => Array.isArray(r) && r.slice(0, 3).every((x) => typeof x === 'string'));
    return {
      chart: v ? flowOption(flows) : null,
      table: {
        title: '흐름 줄',
        sort: { key: 's', dir: 'desc' },
        search: ['channel', 'entry', 'exit'],
        rows: flows.map((r) => ({ channel: channelName(r[0]), entry: pageName(r[1]), exit: pageName(r[2]), c: n0(r[3]), s: n0(r[4]) })),
        columns: [
          { key: 'channel', label: '들어온 길' },
          { key: 'entry', label: '첫 페이지' },
          { key: 'exit', label: '마지막 페이지' },
          { key: 'c', label: '확인', num: true, fmt: int },
          { key: 's', label: '브라우저 모양', num: true, fmt: int },
        ],
      },
    };
  }),
  src: listDetail('src', '들어온 길'),
  geo: listDetail('geo', '어디서'),
  ends: listDetail('ends', '첫 · 마지막 페이지'),
  what: listDetail('what', '무엇을'),
  who: accessDetail('누가', '기기 · OS · 브라우저 · 요청 종류', (app, pv, a) => {
    const rows = whoGroups(a.part).flatMap((g) => {
      const total = g.items.reduce((s, it) => s + n0(it.value), 0);
      return g.items.filter((it) => it.value > 0).map((it) => ({ group: g.name, name: it.name, value: it.value, share: total ? (it.value / total) * 100 : null }));
    });
    const columns = [{ key: 'group', label: '묶음' }, { key: 'name', label: '이름' }, { key: 'value', label: '수', num: true, fmt: int }, { key: 'share', label: '묶음 안 비율', num: true, fmt: (v) => pct(v, 1) }];
    return { table: { title: '전체', columns, rows, sort: { key: 'value', dir: 'desc' }, search: ['group', 'name'] } };
  }),
  heat: accessDetail('요일 × 시간', '스크립트가 돈 페이지 · 이 브라우저 시간대', (app, pv, a) => {
    const { grid, option } = heatModel(a.part);
    const rows = grid.cells.flatMap((row, d) => row.map((v, h) => ({ day: `${WEEKDAY[d]}요일`, order: d * 24 + h, hour: h, value: v })));
    const columns = [{ key: 'day', label: '요일' }, { key: 'hour', label: '시', num: true }, { key: 'value', label: '스크립트가 돈 페이지', num: true, fmt: int }];
    return { chart: grid.max ? option : null, table: { title: '칸마다', columns, rows, sort: { key: 'value', dir: 'desc' } } };
  }),
  status: accessDetail('응답 상태', '시간별 5xx · 최근 5xx', (app, pv, a) => ({
    time: {
      startMs: a.startMs,
      endMs: a.endMs,
      panes: [{ fmt: int }],
      series: [{ name: '5xx', mark: 'bad', type: 'bar', ...a.hours('errors') }, { name: 'WS 재접속 실패', mark: 'c8', type: 'bar', ...a.hours('wsErrors') }],
    },
    table: { title: '최근 5xx(20)', columns: FIVES, rows: fiveRows(a.part, 20), sort: { key: 'ts', dir: 'desc' }, search: ['path'] },
  }), accessCompare),
  durations: accessDetail('WS 연결 시간', '끝난 대시보드 연결', (app, pv, a) => {
    const d = isObj(a.part.ws?.durations) ? a.part.ws.durations : {};
    const rows = WS_BUCKETS.map(([k, label], i) => ({ order: i, name: label, value: n0(d[k]) }));
    return { chart: durationsOption(a.part), table: { title: '구간', columns: [{ key: 'name', label: '열어 둔 시간' }, { key: 'value', label: '연결', num: true, fmt: int }], rows, sort: { key: 'order', dir: 'asc' } } };
  }),
});
