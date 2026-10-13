// 트래픽 페이지(스펙 066 §3.3) — "누가 어디서 와서 어디로": 큰 수(누르면 아래 큰 추이의 지표가 바뀐다 — Plausible) · 큰 추이(점선 =
// 비교 기간) · 흐름(062 sankey) · 목록 넷(카드 안 탭, 행 → 상세 판 — 접속 피드는 거르기를 받지 않는다) · 100% 띠(기기·OS·브라우저·
// 요청 종류) · 요일×시간(한 색 진하기) · 응답 상태 띠 + 최근 5xx · WS 연결 시간. 기간 24시간·7일·30일, 비교는 더 긴 창에서 잘라 쓴다.
import { $, el, forget, int, list, n0, once, partOf, pct, plainOf, pressed, put, req, stateWord, tag, tagText, usable } from './common.js';
import { deltaText } from './calc.js';
import { blank, draw, timeOption } from './charts.js';
import { cmpPhrase } from './names.js';
import { ACCESS_RANGES, accessCompare, accessWindow } from './models.js';
import { FIVES, METRIC_NAME, TRAFFIC_DETAILS, durationsOption, fiveRows, flowOption, heatModel, state, tabOf, trendModel, trendNeeds } from './trafficdetail.js';
import { LISTS, botShare, denomOf, statusItems, whoGroups } from './trafficmodel.js';
import { bar100s, bigNumber, table, tabs, topList } from './ui.js';
import { start } from './app.js';

const GROUP = 'traffic';
const accessReq = (app) => req('access', { window: app.view.range });
const sum = (pts) => list(pts).reduce((s, p) => s + n0(p[1]), 0);
// 하위 부분(visitors·geo)의 상태 — 바깥이 ok 가 아니면 바깥 상태
const subOf = (a, key) => (usable(a) ? partOf({ body: a }, key) : a);

// --- 큰 수 · 큰 추이 ---------------------------------------------------------------------------

function drawKpis(app) {
  const a = accessWindow(app, app.view.range, app.view.cmp);
  const part = a.part;
  const visitors = subOf(part, 'visitors');
  const phrase = cmpPhrase(app.view.range, app.view.cmp);
  const delta = (key) => {
    if (!a.ok) return '';
    const h = a.hours(key);
    return h.cmp ? deltaText(sum(h.data), sum(h.cmp), phrase) : '';
  };
  tag('t-visitors', visitors);
  if (usable(visitors)) bigNumber('v-visitors', int(visitors.confirmed), `~ ${int(visitors.shaped)}`);
  else bigNumber('v-visitors', '–');
  const days = a.ok && app.view.range !== '24h' ? a.days('confirmed') : null;
  put('d-visitors', days?.cmp ? deltaText(sum(days.data), sum(days.cmp), phrase) : '');
  tag('t-pages', part);
  bigNumber('v-pages', a.ok ? int(part.totals?.humanPages) : '–');
  put('d-pages', delta('humanPages'));
  const status = app.entry(req('status'));
  tag('t-now', plainOf(status));
  bigNumber('v-now', status?.body ? int(status.body.wsConnections) : '–');
  tag('t-bots', part);
  const share = a.ok ? botShare(part) : null;
  bigNumber('v-bots', share === null ? '–' : pct(share, 0));
  tag('t-errors', part);
  const fives = a.ok ? n0(part.status?.['5xx']) : null;
  bigNumber('v-errors', fives === null ? '–' : int(fives), '', fives ? 'bad' : null);
  put('d-errors', delta('errors'));
  for (const b of document.querySelectorAll('[data-metric]')) pressed(b, b.dataset.metric === state.metric);
}

function drawTrend(app) {
  const t = trendModel(app, app.view.range, app.view.cmp, state.metric);
  put('trend-title', METRIC_NAME[state.metric]);
  tag('t-trend', t.part);
  once('c-trend', [...t.deps, state.metric, app.view.range, app.view.cmp], () => {
    if (!t.ok) {
      put('f-trend', '');
      return blank('c-trend', stateWord(t.part));
    }
    const first = t.series[0];
    put('f-trend', first.cmp ? deltaText(sum(first.data), sum(first.cmp), `${first.name} 합 — ${cmpPhrase(app.view.range, app.view.cmp)}`) : t.cmpState === 'missing' ? '이 자료는 비교 없음' : '');
    if (!t.series.some((s) => list(s.data).some((p) => n0(p[1]) > 0))) return blank('c-trend', '자료 없음');
    return draw('c-trend', timeOption({ startMs: t.a.startMs, endMs: t.a.endMs, daily: t.daily, zoom: 'card', panes: [{ fmt: int }], series: t.series }), GROUP);
  });
}

// --- 흐름(062) ---------------------------------------------------------------------------------

function drawFlows(app) {
  const entry = app.entry(accessReq(app));
  const visitors = subOf(partOf(entry), 'visitors');
  // 062 배포 전이면 visitors 에 flows 가 없다 — 부분 상태 규칙대로 '연결 안 됨'
  const missing = usable(visitors) && !Array.isArray(visitors.flows);
  if (missing) tagText('t-flows', ['dim', '연결 안 됨', '접속 흐름 피드(062) 없음']);
  else tag('t-flows', visitors);
  once('c-flows', [entry], () => {
    if (!usable(visitors) || missing) return blank('c-flows', missing ? '연결 안 됨' : stateWord(visitors));
    const option = flowOption(visitors.flows);
    return option ? draw('c-flows', option) : blank('c-flows', '자료 없음');
  });
}

// --- 목록 넷(카드 안 탭) ---------------------------------------------------------------------------

function listCard(name) {
  return (app) => {
    const entry = app.entry(accessReq(app));
    const a = partOf(entry);
    const cur = tabOf(name);
    tabs(`tabs-${name}`, LISTS[name].map((t) => [t[0], t[1]]), cur[0], (k) => {
      state.tabs[name] = k;
      forget(`l-${name}`);
      app.paint();
    });
    const picked = usable(a) ? cur[2](a) : undefined;
    const sub = !usable(a) ? a : picked === a ? a : partOf({ body: { x: picked } }, 'x');
    // 062 배포 전 — 방문자는 있는데 첫·마지막 페이지가 없다
    const missing = name === 'ends' && usable(sub) && !Array.isArray(sub.entries);
    if (missing) tagText(`t-${name}`, ['dim', '연결 안 됨', '접속 흐름 피드(062) 없음']);
    else tag(`t-${name}`, sub);
    once(`l-${name}`, [entry, cur[0]], () =>
      topList(`l-${name}`, usable(sub) && !missing ? cur[3](a) : [], {
        n: 6,
        total: usable(a) ? denomOf(a, cur[4]) : null,
        fmt: int,
        onPick: () => app.openDetail(name),
        more: () => app.openDetail(name),
        empty: missing ? '연결 안 됨' : usable(sub) ? '자료 없음' : stateWord(sub),
      }),
    );
  };
}

// --- 누가 · 언제 · 문제 ----------------------------------------------------------------------------

function drawWho(app) {
  const entry = app.entry(accessReq(app));
  const a = partOf(entry);
  tag('t-who', a);
  once('b-who', [entry], () => {
    if (!usable(a)) return $('b-who').replaceChildren(el('p', 'empty', stateWord(a)));
    return bar100s('b-who', whoGroups(a).map((g) => ({ ...g, fmt: int, onPick: () => app.openDetail('who') })));
  });
}

function drawHeat(app) {
  const entry = app.entry(accessReq(app));
  const a = partOf(entry);
  tag('t-heat', a);
  once('c-heat', [entry], () => {
    if (!usable(a)) return blank('c-heat', stateWord(a));
    const { grid, option } = heatModel(a);
    return grid.max ? draw('c-heat', option) : blank('c-heat', '자료 없음');
  });
}

function drawStatus(app) {
  const entry = app.entry(accessReq(app));
  const a = partOf(entry);
  tag('t-status', a);
  once('b-status', [entry], () => {
    if (!usable(a)) {
      $('tbl-5xx').replaceChildren();
      return $('b-status').replaceChildren(el('p', 'empty', stateWord(a)));
    }
    bar100s('b-status', [{ name: '응답 상태', note: '모든 요청', items: statusItems(a), fmt: int, onPick: () => app.openDetail('status') }]);
    const rows = fiveRows(a, 8);
    return rows.length ? table($('tbl-5xx'), FIVES, rows) : $('tbl-5xx').replaceChildren(el('caption', 'dim', '최근 5xx 없음'));
  });
}

function drawDurations(app) {
  const entry = app.entry(accessReq(app));
  const a = partOf(entry);
  tag('t-durations', a);
  once('c-durations', [entry], () => {
    if (!usable(a)) return blank('c-durations', stateWord(a));
    const option = durationsOption(a);
    return option ? draw('c-durations', option) : blank('c-durations', '자료 없음');
  });
}

start({
  id: 'traffic',
  home: '/traffic.html',
  ranges: ACCESS_RANGES,
  range: '24h',
  compare: accessCompare,
  needs: (app) => trendNeeds(app.view.range, app.view.cmp, state.metric),
  rules: (app) => ({ access: app.entry(accessReq(app)) }),
  cards: [
    ['kpis', drawKpis],
    ['trend', drawTrend],
    ['flows', drawFlows],
    ['src', listCard('src')],
    ['geo', listCard('geo')],
    ['ends', listCard('ends')],
    ['what', listCard('what')],
    ['who', drawWho],
    ['heat', drawHeat],
    ['status', drawStatus],
    ['durations', drawDurations],
  ],
  details: TRAFFIC_DETAILS,
  wire: (app) => {
    for (const b of document.querySelectorAll('[data-metric]')) {
      b.addEventListener('click', () => {
        if (state.metric === b.dataset.metric) return;
        state.metric = b.dataset.metric;
        app.refresh();
      });
    }
  },
});
