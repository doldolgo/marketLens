// 개요 페이지(스펙 066 §3.3) — "지금 문제 있나": 상태 띠(064 규칙 그대로, 칩 → 그 카드의 상세 판) · 큰 수 다섯(비교 배지) ·
// 거래소 줄(원천마다 24시간 상태 띠) · 상자 표(문턱 눈금 막대 — 게이지 아홉 대신) · 트래픽(위아래 세 칸, 십자선 공유).
// 기간 1시간·6시간·24시간(1시간은 6시간 시계열을 잘라 쓴다), 비교 이전 기간·지난주 같은 때.
import { $, dec1, el, int, kstDay, list, money, n0, num, once, partOf, pct, plainOf, put, req, stateWord, tag, usable } from './common.js';
import { align, crop, deltaText, hourPoints, sumHours, valueAt } from './calc.js';
import { blank, draw, lanesOption, sparkBarsOption, sparkOption, timeOption } from './charts.js';
import { BOXES, BOX_ROLE, boxMark, cmpPhrase } from './names.js';
import { ACCESS_RANGES, RANGE_MS, accessCompare, accessReqs, accessWindow, alertHours, boxGet, hourlySuccess, seriesCompare, seriesReqs, seriesWindow, todayVisitors } from './models.js';
import { collectLanes } from './ops.js';
import { OPS_DETAILS } from './opsdetail.js';
import { BOX_LEVELS, chip, recentAvg, worstMonthly } from './rules.js';
import { bigNumber, meter, table } from './ui.js';
import { start } from './app.js';

const HOUR = 3_600_000;
const DAY = 86_400_000;
const GROUP = 'overview';
const ACCESS24 = req('access', { window: '24h' });
const ACCESS7 = req('access', { window: '7d' });

// 트래픽 창 — 접속 요약 24시간의 시간 칸을 고른 기간만큼(1·6시간은 겹치는 칸). 비교: 1·6시간의 이전 기간은 같은 응답 안에서,
// 24시간의 이전 기간은 7일 응답에서 잘라 지금 축으로 민다. 지난주 같은 때는 7일 창 밖이라 없다('이 자료는 비교 없음')
export function trafficWindow(app) {
  const v = app.view;
  const main = app.entry(ACCESS24);
  const part = partOf(main);
  const other = v.cmp === 'prev' ? app.entry(ACCESS7) : null;
  const out = { part, ok: usable(part), deps: [main, other] };
  if (!out.ok) return out;
  const L = RANGE_MS[v.range] ?? DAY;
  const end = n0(part.endTs) * 1000;
  out.endMs = Math.floor(end / HOUR) * HOUR + HOUR;
  out.startMs = Math.floor((end - L) / HOUR) * HOUR;
  const op = other ? partOf(other) : undefined;
  let src = null;
  let by = 0;
  if (v.cmp === 'prev' && v.range !== '24h') [src, by] = [part, L];
  else if (v.cmp === 'prev' && usable(op)) [src, by] = [op, L];
  out.cmpState = v.cmp === 'none' ? 'none' : src ? 'ok' : v.cmp === 'prev' && op === undefined ? 'wait' : 'missing';
  out.line = (key) => ({ data: crop(hourPoints(part.hourly, key), out.startMs, out.endMs), cmp: src ? align(hourPoints(src.hourly, key), out.startMs, out.endMs, by) : null });
  return out;
}

// 오늘 방문자 배지 — 오늘(KST) 지금까지의 사람 모양 페이지를 비교 날의 같은 시각까지와(이전 기간 = 어제, 지난주 = 7일 전)
export function visitDelta(part, src, cmp) {
  if (!src || cmp === 'none') return '';
  const end = n0(part.endTs);
  const from = kstDay(end);
  const back = cmp === 'week' ? 7 * 86_400 : 86_400;
  const phrase = cmp === 'week' ? '지난주 같은 때' : '어제 같은 때';
  return deltaText(sumHours(part.hourly, 'humanPages', from, end), sumHours(src.hourly, 'humanPages', from - back, end - back), phrase);
}

// --- 카드 ----------------------------------------------------------------------------------------

const BAND_WORD = { ok: '정상', warn: '주의', bad: '문제', wait: '확인 중' };
let chipKey = null;
function drawBand(app) {
  const chips = app.ready() ? app.chips() : [];
  const tone = !app.ready() ? 'wait' : chips.some((c) => c.tone === 'bad') ? 'bad' : chips.length ? 'warn' : 'ok';
  $('band').className = `band ${tone} s12`;
  $('band-shape').className = tone === 'wait' ? 'shape' : `shape ${tone}`;
  // 정상은 조용히 한 줄 — '● 정상 · 14:03 확인'
  put('band-word', tone === 'ok' ? `정상 · ${new Date(app.now()).toTimeString().slice(0, 5)} 확인` : BAND_WORD[tone]);
  const key = JSON.stringify(chips);
  if (key !== chipKey) $('chips').replaceChildren(...chips.map(chip));
  chipKey = key;
}

function drawWs(app) {
  const status = app.entry(req('status'));
  const w = seriesWindow(app, app.view.range, app.view.cmp);
  tag('t-ws', plainOf(status));
  const now = num(status?.body?.wsConnections);
  bigNumber('v-ws', int(now));
  const line = w.ok ? w.line((p) => p?.wsClients) : null;
  put('d-ws', line?.cmp ? deltaText(now, valueAt(line.cmp, w.endMs), cmpPhrase(app.view.range, app.view.cmp)) : '');
  once('c-ws', [...w.deps, app.view.range, app.view.cmp], () =>
    line ? draw('c-ws', sparkOption(line.data, 'acc', (v) => `${int(v)} 접속`, line.cmp)) : blank('c-ws', stateWord(w.part)),
  );
}

function drawVisit(app) {
  const t = trafficWindow(app);
  const visitors = usable(t.part) ? partOf({ body: t.part }, 'visitors') : t.part;
  tag('t-visit', visitors);
  const today = usable(t.part) ? todayVisitors(t.part) : null;
  bigNumber('v-visit', today ? int(today[0]) : '–', today ? `~ ${int(today[1])}` : '');
  const seven = app.view.cmp === 'prev' ? partOf(app.entry(ACCESS7)) : null;
  put('d-visit', t.ok ? visitDelta(t.part, usable(seven) ? seven : null, app.view.cmp) : '');
  $('d-visit').title = '오늘 지금까지의 사람 모양 페이지를 비교 날의 같은 시각까지와 견준다';
  once('c-visit', [...t.deps, app.view.range, app.view.cmp], () => {
    if (!t.ok) return blank('c-visit', stateWord(t.part));
    const { data, cmp } = t.line('humanPages');
    return draw('c-visit', sparkOption(data, 'acc', (v) => `사람 모양 페이지 ${int(v)}`, cmp));
  });
}

function drawRate(app) {
  const entry = app.entry(req('collect'));
  tag('t-rate', plainOf(entry));
  const rate = num(entry?.body?.successRate1h);
  bigNumber('v-rate', pct(rate), '', rate !== null && rate < 99 ? 'warn' : null);
  once('c-rate', [entry, app.view.range, Math.floor(Date.now() / 60_000)], () => {
    if (!entry?.body) return blank('c-rate', stateWord(plainOf(entry)));
    const now = Date.now();
    return draw('c-rate', sparkOption(crop(hourlySuccess(entry.body, now), now - (RANGE_MS[app.view.range] ?? DAY), now), 'acc', (v) => pct(v, 2)));
  });
}

function drawAlarm(app) {
  const aws = app.entry(req('aws'));
  const part = partOf(aws, 'alarms');
  tag('t-alarm', part);
  if (usable(part)) {
    const items = list(part.items);
    const firing = Math.max(items.filter((a) => a?.state === 'ALARM').length, n0(part.counts?.alarm));
    bigNumber('v-alarm', firing ? `ALARM ${int(firing)}` : 'OK', `/ ${int(items.length)}`, firing ? 'bad' : null);
  } else bigNumber('v-alarm', '–');
  const alerts = app.entry(req('alerts'));
  once('c-alarm', [alerts, app.view.range, Math.floor(Date.now() / 60_000)], () => {
    if (!alerts?.body) return blank('c-alarm', stateWord(plainOf(alerts)));
    const now = Date.now();
    return draw('c-alarm', sparkBarsOption(crop(alertHours(alerts.body.items, now), now - (RANGE_MS[app.view.range] ?? DAY), now), 'acc', (v) => `알림 ${int(v)}`));
  });
}

// 이번 달 비용 — 큰 수 '실제 / 한도', 아래 막대(채움 = 실제 ÷ 한도, 눈금 = 예측). 게이지는 서버 페이지 비용 카드 하나만(066 §3.1)
function drawCost(app) {
  const aws = app.entry(req('aws'));
  const part = partOf(aws, 'budget');
  tag('t-cost', part);
  const worst = usable(part) ? worstMonthly(part) : null;
  if (!worst) {
    bigNumber('v-cost', usable(part) ? '예산 없음' : '–');
    put('d-cost', '');
    return $('m-cost').replaceChildren();
  }
  const b = worst.b;
  bigNumber('v-cost', money(b.actual, b.unit), `/ ${money(b.limit, b.unit)}`, worst.tone === 'ok' ? null : worst.tone);
  put('d-cost', num(b.forecast) === null ? '' : `예측 ${money(b.forecast, b.unit)} · ${pct((b.forecast / b.limit) * 100, 0)}`);
  const forecast = num(b.forecast) === null ? 100 : Math.min(100, (b.forecast / b.limit) * 100);
  return $('m-cost').replaceChildren(meter(Math.min(100, (b.actual / b.limit) * 100), 100, forecast));
}

function drawExchanges(app) {
  const entry = app.entry(req('collect'));
  tag('t-ex', plainOf(entry));
  once('c-ex', [entry, Math.floor(Date.now() / 60_000)], () => {
    if (!entry?.body) return blank('c-ex', stateWord(plainOf(entry)));
    const lanes = collectLanes(entry.body, Date.now());
    return lanes.rows.length ? draw('c-ex', lanesOption(lanes)) : blank('c-ex', '자료 없음');
  });
}

// 상자 표 — 상자 | CPU·메모리·디스크 문턱 눈금 막대(최근 15분 평균 — 띠 규칙과 같은 값·문턱) | 크레딧 | 고른 기간 CPU 선
function drawBoxes(app) {
  const w = seriesWindow(app, app.view.range, 'none');
  tag('t-boxes', w.part);
  once('tbl-boxes', [...w.deps, app.view.range], () => {
    const node = $('tbl-boxes');
    if (!w.ok) return node.replaceChildren(el('caption', 'empty', stateWord(w.part)));
    const rows = BOXES.map((box) => {
      const found = w.boxes.find((b) => b.box === box);
      const s = found?.series ?? {};
      const avg = (key) => recentAvg(s[key], w.part.endTs, w.part.periodSec);
      return { box, found, avg, line: found ? w.line(boxGet(box, 'cpu')).data : [] };
    });
    const levels = Object.fromEntries(BOX_LEVELS.map((l) => [l[0], l]));
    const gauge = (key) => (r) => (r.found ? meter(r.avg(key), levels[key][2], levels[key][3]) : el('span', 'dim', '–'));
    table(node, [
      {
        key: 'box',
        label: '상자',
        cell: (r) => {
          const who = el('span', 'who');
          who.append(el('i', `key sq ${boxMark(r.box)}`), el('b', null, r.box), el('span', 'dim', BOX_ROLE[r.box]));
          return who;
        },
      },
      { key: 'cpu', label: 'CPU', cell: gauge('cpu') },
      { key: 'mem', label: '메모리', cell: gauge('mem') },
      { key: 'disk', label: '디스크', cell: gauge('disk') },
      { key: 'credit', label: '크레딧', num: true, cell: (r) => el('span', null, dec1(r.avg('creditBalance'))) },
      {
        key: 'line',
        label: 'CPU 선',
        cell: (r) => {
          const box = el('div', 'chart spark');
          box.id = `c-box-${r.box}`;
          return box;
        },
      },
    ], rows, { onRow: () => app.openDetail('cpu') });
    // 표가 막 만든 칸(문서에 붙은 뒤)에 작은 선 — 칸 id 마다 인스턴스 하나
    for (const r of rows) {
      if (r.line.length) draw(`c-box-${r.box}`, sparkOption(r.line, boxMark(r.box), (v) => `CPU ${pct(v)}`));
      else blank(`c-box-${r.box}`, '–');
    }
  });
}

// 트래픽 — 위아래 세 칸(사람 모양 페이지 · 5xx · 대시보드 동시 접속), 십자선 공유(둘째 y축 대신 — 066 §3.1). 점선은 비교 기간
function drawTraffic(app) {
  const t = trafficWindow(app);
  const w = seriesWindow(app, app.view.range, app.view.cmp);
  tag('t-traffic', t.part);
  once('c-traffic', [...t.deps, ...w.deps, app.view.range, app.view.cmp], () => {
    if (!t.ok) {
      put('f-traffic', '');
      return blank('c-traffic', stateWord(t.part));
    }
    const ws = w.ok ? w.line((p) => p?.wsClients) : { data: [], cmp: null };
    const pages = t.line('humanPages');
    const phrase = cmpPhrase(app.view.range, app.view.cmp);
    const sum = (pts) => list(pts).reduce((s, p) => s + n0(p[1]), 0);
    put('f-traffic', pages.cmp ? deltaText(sum(pages.data), sum(pages.cmp), `사람 모양 페이지 합 — ${phrase}`) : t.cmpState === 'missing' ? '이 자료는 비교 없음' : '');
    return draw('c-traffic', timeOption({
      startMs: t.startMs,
      endMs: t.endMs,
      zoom: 'card',
      panes: [{ title: '사람 모양 페이지', fmt: int }, { title: '5xx', fmt: int }, { title: '대시보드 동시 접속', fmt: int }],
      series: [
        { name: '사람 모양 페이지', mark: 'acc', type: 'bar', pane: 0, ...pages },
        { name: '5xx', mark: 'bad', type: 'bar', pane: 1, ...t.line('errors') },
        { name: '대시보드 동시 접속', mark: 'c1', pane: 2, data: crop(ws.data, t.startMs, t.endMs), cmp: ws.cmp ? crop(ws.cmp, t.startMs, t.endMs) : null },
      ],
    }), GROUP);
  });
}

// --- 개요만의 상세 판(나머지는 서버와 같은 OPS_DETAILS) ---------------------------------------------------

function accessDetail(title, unit, build, related) {
  return {
    title,
    unit,
    related,
    ranges: ACCESS_RANGES,
    range: () => '24h',
    compare: accessCompare,
    needs: (app, pv) => [...accessReqs(pv.range, pv.cmp), ...seriesReqs(pv.range, pv.cmp)],
    model: (app, pv) => {
      const a = accessWindow(app, pv.range, pv.cmp);
      const base = { part: a.part, deps: a.deps, note: a.cmpState === 'missing' ? '이 자료는 비교 없음' : '' };
      if (!a.ok) return base;
      return { ...base, span: [a.startMs, a.endMs], time: { startMs: a.startMs, endMs: a.endMs, ...build(app, pv, a) } };
    },
  };
}
const DETAILS = {
  ...OPS_DETAILS,
  visit: accessDetail('방문 추이', '시간별 · 사람 모양 페이지 · 스크립트가 돈 페이지', (app, pv, a) => ({
    panes: [{ fmt: int }],
    series: [{ name: '사람 모양 페이지', mark: 'acc', type: 'bar', ...a.hours('humanPages') }, { name: '스크립트가 돈 페이지', mark: 'c1', ...a.hours('jsViews') }],
  }), ['traffic']),
  traffic: accessDetail('트래픽', '시간별 · 사람 모양 페이지 · 5xx · 대시보드 동시 접속', (app, pv, a) => {
    const w = seriesWindow(app, pv.range, pv.cmp);
    const ws = w.ok ? w.line((p) => p?.wsClients) : { data: [], cmp: null };
    return {
      panes: [{ title: '사람 모양 페이지', fmt: int }, { title: '5xx', fmt: int }, { title: '대시보드 동시 접속', fmt: int }],
      series: [
        { name: '사람 모양 페이지', mark: 'acc', type: 'bar', pane: 0, ...a.hours('humanPages') },
        { name: '5xx', mark: 'bad', type: 'bar', pane: 1, ...a.hours('errors') },
        { name: '대시보드 동시 접속', mark: 'c1', pane: 2, ...ws },
      ],
    };
  }, ['visit', 'ws']),
};

start({
  id: 'overview',
  home: '/',
  ranges: ['1h', '6h', '24h'],
  range: '24h',
  compare: seriesCompare,
  needs: (app) => [req('aws'), req('alerts'), ...seriesReqs(app.view.range, app.view.cmp), ACCESS24, ...(app.view.cmp === 'prev' ? [ACCESS7] : [])],
  rules: (app) => ({
    aws: app.entry(req('aws')),
    series: app.entry(seriesReqs(app.view.range, 'none')[0]),
    alerts: app.entry(req('alerts')),
    access: app.entry(ACCESS24),
  }),
  cards: [
    ['band', drawBand],
    ['ws', drawWs],
    ['visit', drawVisit],
    ['rate', drawRate],
    ['alarm', drawAlarm],
    ['cost', drawCost],
    ['ex', drawExchanges],
    ['boxes', drawBoxes],
    ['traffic', drawTraffic],
  ],
  details: DETAILS,
});
