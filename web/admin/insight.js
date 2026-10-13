// Clarity 페이지(스펙 066 §3.3 — 새): 040 요약(최근 24시간) + 068 날별 기록·차원. 큰 수 여섯(+ 날별 작은 선·전날 대비) · 불만 신호 여섯 ·
// 맞대기(Clarity 세션 vs 우리 방문자 — 038 visitors.days) · 페이지 × 기기 표(040 pages.groups, 칸 진하기) · 나라 · 기기·브라우저·OS 100% 띠 ·
// 유입(Channel·Source). 머리에 '마지막 받음 n시간 전 · 다음 HH:MM'. Clarity 는 IP·개인 단위 자료를 주지 않는다(방문자 페이지로 안내).
// 이 파일 이름에 'clarity' 를 쓰지 않는다 — 관리자 파일이 Clarity 태그를 싣지 않는다는 033 의 글자 검사(src=…clarity)와 겹치지 않게.
import { $, ago, el, hm, int, isObj, n0, num, once, partOf, pct, put, stateWord, tag, usable, widthOf } from './common.js';
import { change, deltaText } from './calc.js';
import { blank, draw, sparkOption, timeOption } from './charts.js';
import { SIGNALS } from './names.js';
import { HISTORY_RANGES, historyCompare, historyReqs, historyWindow } from './models.js';
import { CLARITY, INSIGHT_DETAILS, KPIS, PAGE_COLS, dimItems, dimsOf, groupRows, lastTwo, matchModel, matchNeeds, okBase, shade, state } from './insightdetail.js';
import { bar100s, bigNumber, table, tabs, topList } from './ui.js';
import { start } from './app.js';

const baseOf = (app) => partOf(app.entry(CLARITY));

function drawHead(app) {
  const part = baseOf(app);
  put('clarity-when', okBase(part) ? `마지막 받음 ${ago(part.fetchedAt)}${num(part.nextAt) !== null ? ` · 다음 ${hm(part.nextAt)}` : ''}` : '');
}

// 큰 수 여섯 — 값은 최근 24시간 요약, 선은 날별 기록(점선 = 같은 길이의 앞 구간), 배지는 전날 대비
function drawKpis(app) {
  const part = baseOf(app);
  const h = historyWindow(app, app.view.range, app.view.cmp, app.now());
  for (const [id, , pick, fmt] of KPIS) {
    tag(`t-${id}`, part);
    const now = okBase(part) ? num(pick(part)) : null;
    const total = id === 'active' && okBase(part) ? num(part.summary?.totalSec) : null;
    bigNumber(`v-${id}`, fmt(now), total === null ? '' : `/ ${int(total)}초`);
    const [last, before] = h.ok ? lastTwo(h.rows, pick) : [null, null];
    put(`d-${id}`, deltaText(last, before, '전날'));
    once(`c-${id}`, [...h.deps, app.view.range, app.view.cmp], () => {
      if (!h.ok) return blank(`c-${id}`, stateWord(h.part));
      const line = h.line(pick);
      return line.data.length ? draw(`c-${id}`, sparkOption(line.data, 'acc', fmt, line.cmp)) : blank(`c-${id}`, '기록 쌓는 중');
    });
  }
}

// 불만 신호 여섯 — 신호가 있던 세션 %(글 뒤 막대 = 가장 큰 신호 대비) · 전날 대비(%p)
export function signalRows(part, rows) {
  const signals = isObj(part?.summary?.signals) ? part.summary.signals : {};
  return SIGNALS.map(([key, name]) => {
    const [last, before] = lastTwo(rows, (r) => r?.summary?.signals?.[key]?.sessionPct);
    const d = change(last, before);
    return { key, name, value: num(signals[key]?.sessionPct), delta: d.diff === null ? '' : `${d.diff > 0 ? '▲' : d.diff < 0 ? '▼' : '–'} ${Math.abs(d.diff).toFixed(1)}%p` };
  });
}
function drawSignals(app) {
  const part = baseOf(app);
  const h = historyWindow(app, app.view.range, 'none', app.now());
  tag('t-signals', part);
  once('l-signals', [app.entry(CLARITY), ...h.deps], () => {
    const box = $('l-signals');
    if (!okBase(part)) return box.replaceChildren(el('p', 'empty', stateWord(part)));
    const rows = signalRows(part, h.ok ? h.rows : []);
    const max = Math.max(0, ...rows.map((r) => r.value ?? 0));
    return box.replaceChildren(
      ...rows.map((r) => {
        const row = el('div', 'lrow');
        const bar = el('i', 'bg');
        widthOf(bar, max ? (r.value ?? 0) / max : 0);
        row.append(bar, el('span', null, r.name), el('span', 'v', pct(r.value)), el('span', 'p', r.delta));
        row.title = `${r.name} · 신호가 있던 세션 ${pct(r.value)}${r.delta ? ` · 전날보다 ${r.delta}` : ''}`;
        return row;
      }),
    );
  });
}

function drawMatch(app) {
  const m = matchModel(app, app.view.range);
  tag('t-match', m.part);
  once('c-match', [...m.deps, app.view.range], () => {
    if (!m.ok) return blank('c-match', stateWord(m.part));
    if (!m.series.some((s) => s.data.length)) return blank('c-match', '기록 쌓는 중');
    return draw('c-match', timeOption({ startMs: m.startMs, endMs: m.endMs, panes: [{ fmt: int }], series: m.series }));
  });
}

// 페이지 × 기기 — 칸 진하기 = 그 열 최댓값 대비 다섯 단계, 세션 3 미만 행은 흐리게
function drawPages(app) {
  const base = baseOf(app);
  const pages = okBase(base) ? partOf({ body: base }, 'pages') : base;
  tag('t-pages', pages);
  once('tbl-pages', [app.entry(CLARITY)], () => {
    const node = $('tbl-pages');
    if (!usable(pages, 'groups')) return node.replaceChildren(el('caption', 'empty', stateWord(pages)));
    const rows = groupRows(pages);
    if (!rows.length) return node.replaceChildren(el('caption', 'empty', '자료 없음'));
    const max = Object.fromEntries(PAGE_COLS.map(([k]) => [k, Math.max(0, ...rows.map((r) => n0(r[k])))]));
    const cell = (k, fmt) => (r) => el('span', `${shade(r[k], max[k])}${r.thin ? ' thin' : ''}`.trim() || null, fmt(r[k]));
    return table(node, [{ key: 'page', label: '페이지' }, { key: 'device', label: '기기' }, ...PAGE_COLS.map(([k, label, fmt]) => ({ key: k, label, num: true, cell: cell(k, fmt) }))], rows, {
      onRow: () => app.openDetail('pages'),
    });
  });
}

function drawCountries(app) {
  const part = baseOf(app);
  tag('t-countries', part);
  once('l-countries', [app.entry(CLARITY)], () =>
    topList('l-countries', okBase(part) ? dimItems(part.countries) : [], { n: 6, fmt: int, more: () => app.openDetail('countries'), empty: okBase(part) ? '자료 없음' : stateWord(part) }),
  );
}
function drawDims(app) {
  const dims = dimsOf(baseOf(app));
  tag('t-dims', dims);
  once('b-dims', [app.entry(CLARITY)], () => {
    if (!usable(dims)) return $('b-dims').replaceChildren(el('p', 'empty', stateWord(dims)));
    return bar100s('b-dims', [['device', '기기'], ['browser', '브라우저'], ['os', 'OS']].map(([k, name]) => ({ name, note: '세션', items: dimItems(dims[k]), fmt: int, onPick: () => app.openDetail('dims') })));
  });
}
function drawRefs(app) {
  const dims = dimsOf(baseOf(app));
  tag('t-refs', dims);
  tabs('tabs-refs', [['channel', 'Channel'], ['source', 'Source']], state.refTab, (k) => {
    state.refTab = k;
    app.paint();
  });
  once('l-refs', [app.entry(CLARITY), state.refTab], () =>
    topList('l-refs', usable(dims) ? dimItems(dims[state.refTab]) : [], { n: 6, fmt: int, more: () => app.openDetail('refs'), empty: usable(dims) ? '자료 없음' : stateWord(dims) }),
  );
}

start({
  id: 'clarity',
  home: '/clarity.html',
  ranges: HISTORY_RANGES,
  range: '30d',
  compare: historyCompare,
  needs: (app) => [CLARITY, ...historyReqs(app.view.range, app.view.cmp), ...matchNeeds(app.view.range)],
  cards: [
    ['head', drawHead],
    ['kpis', drawKpis],
    ['signals', drawSignals],
    ['match', drawMatch],
    ['pages', drawPages],
    ['countries', drawCountries],
    ['dims', drawDims],
    ['refs', drawRefs],
  ],
  details: INSIGHT_DETAILS,
});
