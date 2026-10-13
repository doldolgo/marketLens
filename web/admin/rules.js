// 상태 띠 규칙(064 §3.2 1 그대로 — 066 §3.3 개요 띠·§3.1 내비 모양이 같은 함수). 칩 = { tone, name, value, to },
// 빨강(■ 문제) 먼저, 같은 색은 규칙 순서. 칩을 누르면 그 페이지의 그 카드 상세 판(066 §3.3) — 주소는 고정 표 JUMP 뿐이다.
import { ago, clean, dec1, el, isObj, list, n0, num, own, pct, span } from './common.js';
import { exName } from './names.js';

// 바로 가기 — 칩이 가리키는 곳은 이 표뿐이고, 링크 주소를 쓰는 곳은 jump() 하나다(피드 글자가 주소가 되지 않게)
const JUMP = Object.freeze({
  collect: '/server.html#detail=collect',
  alarms: '/server.html#detail=alarms',
  canary: '/server.html#detail=canary',
  cpu: '/server.html#detail=cpu',
  mem: '/server.html#detail=mem',
  disk: '/server.html#detail=disk',
  credit: '/server.html#detail=credit',
  checks: '/server.html#detail=checks',
  cost: '/server.html#detail=cost',
  problems: '/traffic.html#detail=status',
});
export function jump(node, to) {
  const href = own(JUMP, to);
  if (href !== undefined) node.setAttribute('href', href);
  return node;
}
// 칩이 가리키는 페이지 — 내비 항목 옆 모양을 그 페이지의 칩으로 센다
const PAGE_OF = Object.freeze({ problems: 'traffic' });
export const pageOf = (to) => own(PAGE_OF, to) ?? 'server';

// 시계열 끝 15분 평균 — 점 [[ts초, 값|null]] 가운데 끝(endTs) 앞 15분(주기가 더 길면 마지막 칸)의 값 평균, 없으면 null
export function recentAvg(points, endTs, periodSec) {
  if (num(endTs) === null) return null;
  const from = endTs - Math.max(900, n0(periodSec));
  const vals = list(points)
    .filter((p) => Array.isArray(p) && num(p[0]) !== null && p[0] >= from && p[0] < endTs)
    .map((p) => num(p[1]))
    .filter((v) => v !== null);
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

// 상자 지표 문턱(064 §3.2) — [시리즈 키, 이름, 빨강 이상, 주황 이상, 바로 가기]
export const BOX_LEVELS = Object.freeze([
  ['cpu', 'CPU', 90, 75, 'cpu'],
  ['mem', '메모리', 90, 80, 'mem'],
  ['disk', '디스크', 85, 75, 'disk'],
]);
export const levelOf = (v, red, amber) => (num(v) === null ? null : v >= red ? 'bad' : v >= amber ? 'warn' : 'ok');

// WS 재접속 실패 — 24시간 창은 status.ws5xx, 7·30일 창은 마지막 24시간 칸의 wsErrors 합(띠가 창에 따라 바뀌지 않게)
export function wsFailures(a) {
  if (a.window === '24h') return n0(a.status?.ws5xx);
  const end = n0(a.endTs);
  return list(a.hourly).filter((h) => isObj(h) && n0(h.ts) > end - 86_400).reduce((sum, h) => sum + n0(h.wsErrors), 0);
}

const okPart = (body, key) => (isObj(body) && isObj(body[key]) && body[key].state === 'ok' ? body[key] : null);

// f = { 키: 마지막 결과 } — 없는 키는 판정하지 않는다
export function problems(f, now = Date.now()) {
  const out = [];
  const add = (tone, name, value, to) => out.push({ tone, name: clean(name), value: clean(value), to });
  for (const [key, name] of [['collector', '수집 멈춤'], ['api', 'API 이상']]) {
    const entry = f[key];
    if (!entry) continue;
    const status = entry.body?.status;
    if (entry.why || entry.off) add('bad', name, '응답 없음', 'collect');
    else if (status !== 'ok') add('bad', name, typeof status === 'string' ? status : '?', 'collect');
  }
  const st = f.status?.body;
  if (st?.redis === 'down') add('bad', 'Redis 끊김', 'down', 'collect');
  if (st?.influx === 'down') add('bad', 'Influx 끊김', 'down', 'collect');
  for (const ex of list(f.collect?.body?.exchanges).filter(isObj)) {
    if (isObj(ex.openOutage)) add('bad', `${exName(ex.exchange)} 끊김`, span((now - n0(ex.openOutage.startedAt)) / 1000), 'collect');
    else if (num(ex.successRate1h) !== null && ex.successRate1h < 99) add('warn', `${exName(ex.exchange)} 성공률`, pct(ex.successRate1h), 'collect');
  }
  const aws = f.aws?.body;
  const alarms = okPart(aws, 'alarms');
  const firing = alarms ? Math.max(list(alarms.items).filter((a) => isObj(a) && a.state === 'ALARM').length, n0(alarms.counts?.alarm)) : 0;
  if (firing > 0) add('bad', '경보', `${firing}`, 'alarms');
  const canary = okPart(aws, 'canary');
  if (canary?.ok === false) add('bad', '점검 실패', ago(canary.lastRunAt, now), 'canary');
  const series = f.series?.body;
  if (isObj(series) && series.state === 'ok') {
    for (const b of list(series.boxes).filter(isObj)) {
      const s = isObj(b.series) ? b.series : {};
      const avg = (key) => recentAvg(s[key], series.endTs, series.periodSec);
      const box = clean(b.box);
      for (const [key, label, red, amber, to] of BOX_LEVELS) {
        const v = avg(key);
        const tone = levelOf(v, red, amber);
        if (tone === 'bad' || tone === 'warn') add(tone, `${box} ${label}`, pct(v), to);
      }
      const credit = avg('creditBalance');
      if (credit !== null && credit < 30) add(credit < 10 ? 'bad' : 'warn', `${box} 크레딧`, dec1(credit), 'credit');
      const surplus = avg('surplusCharged');
      if (surplus !== null && surplus > 0) add('warn', `${box} 초과 과금`, dec1(surplus), 'credit');
      const failed = avg('statusFailed');
      if (failed !== null && failed > 0) add('bad', `${box} 상태 검사`, '실패', 'checks');
    }
  }
  const a = f.access?.body;
  if (isObj(a) && a.state === 'ok') {
    const end = num(a.endTs);
    const fives = end === null ? 0 : list(a.recent5xx).filter((r) => isObj(r) && num(r.ts) !== null && r.ts >= end - 3600).length;
    if (fives >= 1) add(fives >= 10 ? 'bad' : 'warn', '5xx', `${fives}`, 'problems');
    const ws = wsFailures(a);
    if (ws >= 1) add('warn', 'WS 재접속 실패', `${ws}`, 'problems');
  }
  for (const b of list(okPart(aws, 'budget')?.items).filter(isObj)) {
    if (!(num(b.limit) > 0)) continue;
    if (num(b.actual) !== null && b.actual >= b.limit) add('bad', '예산 넘음', pct((b.actual / b.limit) * 100, 0), 'cost');
    else if (num(b.forecast) !== null && b.forecast >= b.limit) add('warn', '예산 예측 넘음', pct((b.forecast / b.limit) * 100, 0), 'cost');
  }
  const slack = list(f.alerts?.body?.items).filter((i) => isObj(i) && i.source === 'slack' && num(i.at) !== null && i.at >= now - 3_600_000);
  if (slack.length) add('warn', 'Slack 알림', `${slack.length}`, 'alarms');
  return out.filter((c) => c.tone === 'bad').concat(out.filter((c) => c.tone === 'warn'));
}

// 페이지마다 가장 나쁜 색 — 개요는 전체, 서버·트래픽은 그 페이지를 가리키는 칩(066 §3.1 내비 모양)
export function marks(chips) {
  const worst = (cs) => (cs.some((c) => c.tone === 'bad') ? 'bad' : cs.length ? 'warn' : null);
  return {
    overview: worst(chips),
    server: worst(chips.filter((c) => pageOf(c.to) === 'server')),
    traffic: worst(chips.filter((c) => pageOf(c.to) === 'traffic')),
  };
}

// 칩 하나 — 모양(■ 문제·▲ 주의) + 짧은 이름 + 값, 누르면 그 페이지의 그 카드 상세 판
export function chip(c) {
  const node = jump(el('a', `chip ${c.tone}`), c.to);
  node.append(el('i', `shape ${c.tone}`), el('span', null, c.name));
  if (c.value) node.append(el('b', null, c.value));
  return node;
}

// 월 예산 가운데 한도 대비 실제가 가장 큰 것 — 색은 띠 규칙과 같다(실제 ≥ 100% 문제, 예측 ≥ 100% 주의)
export function worstMonthly(part) {
  let worst = null;
  for (const b of list(part.items).filter(isObj)) {
    if (b.timeUnit !== 'MONTHLY' || !(num(b.limit) > 0) || num(b.actual) === null) continue;
    const r = b.actual / b.limit;
    if (!worst || r > worst.r) worst = { b, r };
  }
  if (worst) worst.tone = worst.r >= 1 ? 'bad' : num(worst.b.forecast) !== null && worst.b.forecast >= worst.b.limit ? 'warn' : 'ok';
  return worst;
}
