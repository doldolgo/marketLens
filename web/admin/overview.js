// 개요 페이지(스펙 064 §3.2) — "지금 문제 있나": 상태 띠 · 큰 수 다섯 · 수집 원천 알약 · 상자 셋 · 24시간 트래픽.
// 느린 묶음: AWS 요약·AWS 시계열(24시간)·알림 기록·접속 요약(24시간). 그리기는 카드마다 따로(common.start).
import {
  $,
  BOX_LEVELS,
  ago,
  chip,
  clean,
  el,
  exName,
  int,
  isObj,
  levelOf,
  list,
  money,
  n0,
  num,
  once,
  own,
  partOf,
  pct,
  plainOf,
  put,
  recentAvg,
  start,
  stateWord,
  tag,
  usable,
  worstMonthly,
} from './common.js';
import { COLOR, axisTip, base, blank, budgetGauge, draw, gauges, legend, ms, spark, sparkBars, timeAxis, tip, valueAxis, when } from './charts.js';

const BOXES = ['collect', 'data', 'serve'];
const HOUR = 3_600_000;
const KST = 32_400;
const kstDay = (sec) => Math.floor((sec + KST) / 86_400) * 86_400 - KST;

// --- 계산(§4 node 시험 대상) -----------------------------------------------------------------------

// 오늘(KST — 응답 endTs 가 든 날) 방문자 [확인, 브라우저 모양]. visitors 가 ok 가 아니면 null
export function todayVisitors(a) {
  const v = a?.visitors;
  if (!isObj(v) || v.state !== 'ok') return null;
  const day = kstDay(n0(a.endTs));
  const d = list(v.days).find((x) => isObj(x) && x.ts === day);
  return d ? [n0(d.confirmed), n0(d.shaped)] : [0, 0];
}

// 24시간 시간 칸마다 수집 성공률 — 011 의 1시간 식(1 − 실패 구간과 겹친 초 ÷ 칸 길이)을 원천 전체로 평균. [[ts초, %]]
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
    out.push([from / 1000, Math.max(0, 100 * (1 - down / (n * (to - from))))]);
  }
  return out;
}

// 24시간 시간 칸마다 알림 수 — 보낸 Slack 알림과 ALARM 으로 바뀐 경보. [[ts초, 수]]
export function alertHours(items, now) {
  const top = Math.floor(now / HOUR) * HOUR;
  const counts = new Map();
  for (let i = 23; i >= 0; i -= 1) counts.set(top - i * HOUR, 0);
  for (const it of list(items).filter(isObj)) {
    if (!(it.source === 'slack' || (it.source === 'alarm' && it.toState === 'ALARM')) || num(it.at) === null) continue;
    const hour = Math.floor(it.at / HOUR) * HOUR;
    if (counts.has(hour)) counts.set(hour, counts.get(hour) + 1);
  }
  return [...counts].map(([hour, count]) => [hour / 1000, count]);
}

// --- 카드 ----------------------------------------------------------------------------------------

const BAND_WORD = { ok: '정상', warn: '주의', bad: '문제', wait: '확인 중' };
let chipKey = null;
function drawBand(app) {
  const ready = app.entry('collector') !== undefined && app.entry('api') !== undefined;
  const chips = ready ? app.chips() : [];
  const tone = !ready ? 'wait' : chips.some((c) => c.tone === 'bad') ? 'bad' : chips.length ? 'warn' : 'ok';
  $('band').className = `band ${tone}`;
  put('band-word', BAND_WORD[tone]);
  const key = JSON.stringify(chips);
  if (key !== chipKey) $('chips').replaceChildren(...chips.map(chip));
  chipKey = key;
}

function drawWs(app) {
  const entry = app.entry('status');
  tag('t-ws', plainOf(entry));
  put('v-ws', entry?.body ? int(entry.body.wsConnections) : '–');
  const series = app.current('series');
  once('c-ws', series, () => {
    const part = partOf(series);
    if (!usable(part)) return blank('c-ws', stateWord(part));
    return spark('c-ws', part.wsClients, COLOR.accent, (v) => `${int(v)} 접속`);
  });
}

function drawVisit(app) {
  const access = app.current('access');
  const part = partOf(access);
  // 접속 요약이 ok 면 방문자 하위 부분의 상태를 표로
  tag('t-visit', usable(part) ? partOf({ body: part }, 'visitors') : part);
  const today = usable(part) ? todayVisitors(part) : null;
  if (today) $('v-visit').replaceChildren(int(today[0]), el('small', null, ` ~ ${int(today[1])}`));
  else put('v-visit', '–');
  once('c-visit', access, () => {
    if (!usable(part)) return blank('c-visit', stateWord(part));
    const hours = list(part.hourly).filter(isObj).map((h) => [h.ts, num(h.humanPages)]);
    return spark('c-visit', hours, COLOR.accent, (v) => `사람 모양 페이지 ${int(v)}`);
  });
}

function drawRate(app) {
  const entry = app.entry('collect');
  tag('t-rate', plainOf(entry));
  const rate = num(entry?.body?.successRate1h);
  put('v-rate', pct(rate), rate !== null && rate < 99 ? 'big warn' : 'big');
  once('c-rate', [entry, Math.floor(Date.now() / 60_000)], () => {
    if (!entry?.body) return blank('c-rate', stateWord(plainOf(entry)));
    return spark('c-rate', hourlySuccess(entry.body, Date.now()), COLOR.accent, (v) => pct(v, 2));
  });
}

function drawAlarm(app) {
  const part = partOf(app.entry('aws'), 'alarms');
  tag('t-alarm', part);
  if (usable(part)) {
    const items = list(part.items).filter(isObj);
    const firing = Math.max(items.filter((a) => a.state === 'ALARM').length, n0(part.counts?.alarm));
    $('v-alarm').replaceChildren(firing ? `ALARM ${int(firing)}` : 'OK', el('small', null, ` / ${int(items.length)}`));
    $('v-alarm').className = `big ${firing ? 'bad' : 'ok'}`;
  } else put('v-alarm', '–', 'big');
  const alerts = app.entry('alerts');
  once('c-alarm', [alerts, Math.floor(Date.now() / 60_000)], () => {
    if (!alerts?.body) return blank('c-alarm', stateWord(plainOf(alerts)));
    return sparkBars('c-alarm', alertHours(alerts.body.items, Date.now()), COLOR.sub, (v) => `알림 ${int(v)}`);
  });
}

function drawCost(app) {
  const part = partOf(app.entry('aws'), 'budget');
  tag('t-cost', part);
  const worst = usable(part) ? worstMonthly(part) : null;
  if (!worst) {
    put('v-cost', usable(part) ? '예산 없음' : '–', 'big');
    return once('c-cost', [part], () => blank('c-cost', usable(part) ? '월 예산 없음' : stateWord(part)));
  }
  const unit = worst.b.unit;
  $('v-cost').replaceChildren(money(worst.b.actual, unit), el('small', null, ` / ${money(worst.b.limit, unit)}`));
  $('v-cost').className = worst.tone === 'ok' ? 'big' : `big ${worst.tone}`;
  return once('c-cost', [part], () => budgetGauge('c-cost', worst.b, (v) => money(v, unit)));
}

// 수집 원천 알약 — 초록 수집 중 · 주황 지연 · 빨강 끊김(열린 실패 구간 포함), 글자는 마지막 성공
const PILL = { ok: 'ok', stale: 'warn', down: 'bad' };
function drawPills(app) {
  const entry = app.entry('collect');
  tag('t-ex', plainOf(entry));
  const exchanges = list(entry?.body?.exchanges).filter(isObj);
  if (entry?.body && !exchanges.length) return $('pills').replaceChildren(el('p', 'empty', '자료 없음'));
  return $('pills').replaceChildren(
    ...exchanges.map((ex) => {
      const pill = el('span', `pill ${isObj(ex.openOutage) ? 'bad' : (own(PILL, ex.state) ?? 'dim')}`, exName(ex.exchange));
      pill.append(el('small', null, ago(ex.lastSuccessAt)));
      pill.title = `${clean(ex.exchange)} · 1시간 성공률 ${pct(ex.successRate1h)}`;
      return pill;
    }),
  );
}

// 상자 셋 — 게이지는 최근 15분 평균(띠 규칙과 같은 값·같은 문턱), 아래 선은 24시간 CPU
function drawBoxes(app) {
  const entry = app.current('series');
  const part = partOf(entry);
  for (const box of BOXES) {
    tag(`t-box-${box}`, part);
    once(`box-${box}`, entry, () => {
      const found = usable(part) ? list(part.boxes).find((b) => isObj(b) && b.box === box) : undefined;
      if (!found) {
        blank(`g-${box}`, usable(part) ? '자료 없음' : stateWord(part));
        return blank(`l-${box}`, '');
      }
      const s = isObj(found.series) ? found.series : {};
      gauges(`g-${box}`, BOX_LEVELS.map(([key, label, red, amber]) => {
        const value = recentAvg(s[key], part.endTs, part.periodSec);
        return { label, value, tone: levelOf(value, red, amber) };
      }));
      return spark(`l-${box}`, s.cpu, COLOR.accent, (v) => `CPU ${pct(v)}`);
    });
  }
}

// 24시간 트래픽 — 시간별 사람 모양 페이지·5xx 막대(시의 가운데), WS 접속 선(오른쪽 축 — §3.2 5)
const TRAFFIC = [['사람 모양 페이지', 'c0'], ['5xx', 'bad'], ['WS 접속', 'c1']];
function drawTraffic(app) {
  const access = app.current('access');
  const series = app.current('series');
  const part = partOf(access);
  tag('t-traffic', part);
  once('traffic', [access, series], () => {
    if (!usable(part)) return blank('c-traffic', stateWord(part));
    const hours = list(part.hourly).filter((h) => isObj(h) && num(h.ts) !== null);
    if (!hours.length) return blank('c-traffic', '자료 없음');
    const mid = (h) => h.ts * 1000 + HOUR / 2;
    const ws = partOf(series);
    const bar = (key, color) => ({ type: 'bar', data: hours.map((h) => [mid(h), num(h[key])]), barMaxWidth: 14, itemStyle: { color, borderRadius: [4, 4, 0, 0] } });
    return draw('c-traffic', base({
      grid: { left: 8, right: 8, top: 34, bottom: 4, containLabel: true },
      legend: legend({ data: TRAFFIC.map(([name]) => name) }),
      tooltip: axisTip((ps) => tip(when(ps[0]?.value?.[0]), ps.map((p) => [TRAFFIC[p.seriesIndex]?.[1], p.seriesName, int(p.value?.[1])]))),
      xAxis: timeAxis(n0(part.startTs) * 1000, n0(part.endTs) * 1000),
      yAxis: [valueAxis({ minInterval: 1 }), valueAxis({ minInterval: 1, splitLine: { show: false } })],
      series: [
        { ...bar('humanPages', COLOR.accent), name: TRAFFIC[0][0] },
        { ...bar('errors', COLOR.bad), name: TRAFFIC[1][0] },
        { type: 'line', name: TRAFFIC[2][0], yAxisIndex: 1, data: usable(ws) ? ms(ws.wsClients) : [], showSymbol: false, lineStyle: { width: 2, color: COLOR.sub }, itemStyle: { color: COLOR.sub } },
      ],
    }));
  });
}

start({
  home: '/',
  slow: ['aws', 'series', 'alerts', 'access'],
  picks: { series: '24h', access: '24h' },
  cards: [
    ['band', drawBand],
    ['ws', drawWs],
    ['visit', drawVisit],
    ['rate', drawRate],
    ['alarm', drawAlarm],
    ['cost', drawCost],
    ['ex', drawPills],
    ['box', drawBoxes],
    ['traffic', drawTraffic],
  ],
});
