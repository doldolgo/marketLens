// 화면 페이지의 상세 판(스펙 065 §3.6 — 066 상세 판의 목록 꼴): 판마다 표 전체 — 정렬·찾기·CSV(kimptrack-screens-<화면>-<기기>-<range>.csv,
// 066 CSV 규칙), 그래프 없음. 요소 표는 열쇠 원문 칸을 더한다. DOM 없음 — 표 칸은 글자·수만.
import { int } from './common.js';
import { DEVICE_NAME, METRIC_NAME, PERIOD_NAME, current, pageReq, screenName } from './screensmodel.js';
import { areaName, elName } from './screensnames.js';

const NO_CMP = () => ({ prev: false, week: false });
const STATE = Object.freeze({ ok: '', gone: '지금 화면에 없음', other: '열쇠 300 넘음', none: '기록 없음' });
const sec = (v) => (v === null || v === undefined ? '–' : v.toFixed(1));
const d1 = (v) => (v === null || v === undefined ? '–' : v.toFixed(1));
const rankText = (v) => (v ? `#${v}` : '–');
const fileOf = (c) => `kimptrack-screens-${c.page}-${c.device}-${c.range}.csv`;
const unitOf = (c, tail) => `${screenName(c.page)} · ${DEVICE_NAME[c.device]} · ${PERIOD_NAME[c.range]} · ${tail}`;

// 판 하나 — 고른 화면의 응답만 부르고(페이지와 같은 열쇠), 값이 있을 때만 표
function sheet(title, build) {
  return {
    title,
    compare: NO_CMP,
    needs: (app) => {
      const c = current(app);
      return c.page ? [pageReq(c.range, c.page)] : [];
    },
    model: (app) => {
      const c = current(app);
      const base = { part: c.part, deps: [c.entry, c.page, c.device, c.metric, c.ready] };
      return c.ok ? { ...base, ...build(c) } : base;
    },
  };
}

export const SCREEN_DETAILS = Object.freeze({
  elements: sheet('요소', (c) => ({
    unit: unitOf(c, `${METRIC_NAME[c.metric]} 큰 순`),
    note: c.elements.capped ? '그 밖은 피드 상한 300 — 피드는 (클릭, 보인 시간) 큰 순 300개까지 준다' : '',
    table: {
      title: '요소 전체',
      columns: [
        { key: 'rank', label: '순위', num: true, fmt: rankText },
        { key: 'name', label: '이름' },
        { key: 'key', label: '열쇠', cls: 'mono' },
        { key: 'areaName', label: '영역' },
        { key: 'level', label: '단계', num: true, fmt: (v) => (v === null ? '–' : String(v)) },
        { key: 'c', label: '클릭', num: true, fmt: int },
        { key: 'cRate', label: '100뷰당 클릭', num: true, fmt: d1 },
        { key: 'h', label: '평균 마우스 올림(초)', num: true, fmt: sec },
        { key: 'v', label: '평균 보인 시간(초)', num: true, fmt: sec },
        { key: 'stateName', label: '상태' },
      ],
      rows: c.elements.rows.map((e) => ({ ...e, name: elName(c.page, e.key), areaName: areaName(c.page, e.area), stateName: STATE[e.state] })),
      sort: null,
      search: ['name', 'key', 'areaName'],
      file: fileOf(c),
    },
  })),
  areas: sheet('영역', (c) => {
    const v = c.areas;
    const rows = [...v.ranked, ...v.blank, ...v.away].map((a) => ({
      ...a,
      name: areaName(c.page, a.id),
      lowName: a.low && a.state !== 'none' ? '거의 안 닿음' : '',
      stateName: STATE[a.state],
    }));
    return {
      unit: unitOf(c, '평균 보인 시간 큰 순'),
      table: {
        title: '영역 전체',
        columns: [
          { key: 'rank', label: '순위', num: true, fmt: rankText },
          { key: 'name', label: '영역' },
          { key: 'id', label: '영역 id', cls: 'mono' },
          { key: 't', label: '평균 보인 시간(초)', num: true, fmt: d1 },
          { key: 'r', label: '도달률(%)', num: true, fmt: int },
          { key: 'c', label: '100뷰당 클릭', num: true, fmt: d1 },
          { key: 'level', label: '단계', num: true, fmt: (l) => String(l) },
          { key: 'lowName', label: '거의 안 닿음' },
          { key: 'stateName', label: '상태' },
        ],
        rows,
        sort: null,
        search: ['name', 'id'],
        file: fileOf(c),
      },
    };
  }),
  exits: sheet('떠난 곳·깊이', (c) => {
    const x = c.exits;
    const d = c.depth;
    const depthRows = d?.reach
      ? d.reach.map((r, j) => ({ kind: '깊이', name: j === 10 ? '끝' : `${j * 10}% 지점`, id: '', n: d.cells[j], share: (d.cells[j] / d.total) * 100, reach: r }))
      : [];
    return {
      unit: unitOf(c, 'pagehide 로 끝난 페이지뷰'),
      note: `떠난 곳: pagehide 로 끝난 pv ${int(x.total)}${x.share === null ? '' : ` — 전체 pv 의 ${x.share.toFixed(0)}%`} · 깊이: ${d === null ? '탭 화면은 깊이 없음' : d.total ? `${int(d.total)} pv 기준(pv = 가장 아래가 그 칸에서 멈춤, 도달 = 그 지점까지 내려옴)` : '깊이 기록 없음'}`,
      table: {
        title: '떠난 곳·깊이 전체',
        columns: [
          { key: 'kind', label: '구분' },
          { key: 'name', label: '항목' },
          { key: 'id', label: '영역 id', cls: 'mono' },
          { key: 'n', label: 'pv', num: true, fmt: int },
          { key: 'share', label: '몫(%)', num: true, fmt: d1 },
          { key: 'reach', label: '도달(%)', num: true, fmt: d1 },
        ],
        rows: [...x.rows.map((r) => ({ kind: '떠난 곳', name: areaName(c.page, r.id), id: r.id, n: r.n, share: r.pct, reach: null })), ...depthRows],
        sort: null,
        search: ['kind', 'name', 'id'],
        file: fileOf(c),
      },
    };
  }),
});
