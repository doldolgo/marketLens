// 운영 상세 판(스펙 066 §3.2) — 개요·서버 카드의 ⤢ 가 여는 판: 상자 지표(CPU·메모리·디스크·네트워크·디스크 입출력·크레딧·상태 검사)·
// WS 접속·점검(canary)·수집 상태·경보·비용. 판 하나 = 피드 열쇠(needs) + 모델(model) — 그리기는 detail.js.
// 시계열 판의 기간은 피드가 주는 가장 긴 창(30일)까지, 비교는 063 + 068 offset(models.js). 서버 페이지의 상자 거르기를 따른다.
import { bytes, dec1, int, isObj, kstText, list, money, num, partOf, pct, plainOf, req, usable } from './common.js';
import { hasValue } from './calc.js';
import { boxMark } from './names.js';
import { SERIES_RANGES, alertHours, boxGet, hourlySuccess, outageMinutes, seriesCompare, seriesReqs, seriesWindow } from './models.js';
import { alertRows, durText, outageRows } from './ops.js';
import { BOX_LEVELS } from './rules.js';

const DAY = 86_400_000;
const pctFmt = (v) => pct(v);
const pctAxis = (v) => `${v}%`;
const rate = (v) => (num(v) === null ? '–' : `${bytes(v)}/s`);
const NOTE = '이 자료는 비교 없음 — 서버가 이전 기간을 아직 주지 않는다';
export const cmpNote = (w) => (w.cmpState === 'missing' ? NOTE : '');
// 기준선 — 상태 띠 규칙과 같은 문턱(주의·문제, 064 §3.2)
export const refsOf = (key) => {
  const [, , red, amber] = BOX_LEVELS.find((l) => l[0] === key);
  return [[amber, 'warn', `주의 ${amber}%`], [red, 'bad', `문제 ${red}%`]];
};
// 서버 페이지의 상자 거르기(범례 겸 거르기 — f=box:…) — 고른 상자만, 없으면 모두
export const boxesOf = (app, w) => {
  const pick = app.filters('box');
  return w.boxes.filter((b) => !pick.length || pick.includes(b.box));
};

function seriesDetail(title, unit, build, related) {
  return {
    title,
    unit,
    related,
    ranges: SERIES_RANGES,
    range: (r) => (SERIES_RANGES.includes(r) ? r : '24h'),
    compare: seriesCompare,
    needs: (app, pv) => seriesReqs(pv.range, pv.cmp),
    model: (app, pv) => {
      const w = seriesWindow(app, pv.range, pv.cmp);
      const base = { part: w.part, deps: [...w.deps, app.view.f.length], note: cmpNote(w) };
      if (!w.ok) return base;
      return { ...base, span: [w.startMs, w.endMs], time: { startMs: w.startMs, endMs: w.endMs, ...build(app, w) } };
    },
  };
}
export const boxLines = (key) => (app, w) => ({
  panes: [{ fmt: pctFmt, axisFmt: pctAxis, max: 100, refs: refsOf(key) }],
  series: boxesOf(app, w).map((b) => ({ name: b.box, mark: boxMark(b.box), ...w.line(boxGet(b.box, key)) })),
});
// 위 들어옴·읽기 / 아래 나감·쓰기(뒤집은 면적) — 상자마다 한 칸(상자끼리 수십 배 차이라 세로 눈금 따로)
export const mirrorPanes = (upKey, downKey, upName, downName) => (app, w) => {
  const boxes = boxesOf(app, w);
  return {
    panes: boxes.map((b) => ({ title: b.box, fmt: rate, axisFmt: bytes })),
    series: boxes.flatMap((b, i) => [
      { name: `${b.box} ${upName}`, mark: boxMark(b.box), pane: i, area: true, ...w.line(boxGet(b.box, upKey)) },
      { name: `${b.box} ${downName}`, mark: boxMark(b.box), pane: i, neg: true, area: true, ...w.line(boxGet(b.box, downKey)) },
    ]),
  };
};
export const credits = (app, w) => {
  const boxes = boxesOf(app, w).filter((b) => ['creditBalance', 'creditUsage', 'surplusCharged'].some((k) => hasValue(b.series[k])));
  return {
    panes: [{ title: '잔량', fmt: dec1 }, { title: '사용 · 초과 과금', fmt: dec1 }],
    series: boxes.flatMap((b) => [
      { name: `${b.box} 잔량`, mark: boxMark(b.box), pane: 0, ...w.line(boxGet(b.box, 'creditBalance')) },
      { name: `${b.box} 사용`, mark: boxMark(b.box), pane: 1, ...w.line(boxGet(b.box, 'creditUsage')) },
      { name: `${b.box} 초과 과금`, mark: 'bad', pane: 1, type: 'bar', stack: 'surplus', ...w.line(boxGet(b.box, 'surplusCharged')) },
    ]),
    empty: '크레딧이 있는 상자 없음',
  };
};
export const wsLine = (app, w) => ({ panes: [{ fmt: int }], series: [{ name: 'WS 접속', mark: 'acc', area: true, ...w.line((p) => p?.wsClients) }] });
export const canaryPanes = (app, w) => ({
  panes: [{ title: '걸린 시간', fmt: (v) => (num(v) === null ? '–' : `${int(v)}ms`) }, { title: '오류', fmt: int }],
  series: [
    { name: '걸린 시간', mark: 'acc', ...w.line((p) => p?.canary?.durationMs) },
    { name: '오류', mark: 'bad', pane: 1, type: 'bar', ...w.line((p) => p?.canary?.errors) },
  ],
});
export const checkLines = (app, w) => ({
  panes: [{ fmt: (v) => (num(v) === null ? '–' : v >= 1 ? '실패' : '정상'), axisFmt: (v) => (v >= 1 ? '실패' : '정상'), max: 1 }],
  series: boxesOf(app, w).map((b) => ({ name: b.box, mark: boxMark(b.box), step: 'end', ...w.line(boxGet(b.box, 'statusFailed')) })),
});

// 수집 상태(011) — 시간별 성공률·끊긴 분 + 끊김 목록(24시간 — 피드가 그만큼만 준다)
const collectDetail = {
  title: '수집 상태',
  unit: '24시간 · 원천 전체',
  related: ['alarms'],
  needs: () => [],
  model: (app) => {
    const entry = app.entry(req('collect'));
    const now = app.now();
    const base = { part: plainOf(entry), deps: [entry, Math.floor(now / 60_000)] };
    if (!entry?.body) return base;
    const body = entry.body;
    return {
      ...base,
      span: [now - DAY, now],
      time: {
        startMs: now - DAY,
        endMs: now,
        panes: [{ title: '성공률', fmt: (v) => pct(v, 2), max: 100 }, { title: '끊긴 분(원천 합)', fmt: (v) => (num(v) === null ? '–' : `${dec1(v)}분`) }],
        series: [{ name: '성공률', mark: 'acc', data: hourlySuccess(body, now) }, { name: '끊긴 분', mark: 'bad', pane: 1, type: 'bar', data: outageMinutes(body, now) }],
      },
      table: {
        title: '끊김 목록',
        sort: { key: 'start', dir: 'desc' },
        search: ['source', 'kind', 'message'],
        rows: outageRows(body, now),
        columns: [
          { key: 'start', label: '시작(KST)', fmt: (v) => kstText(v), csv: (r) => kstText(r.start, true) },
          { key: 'source', label: '원천' },
          { key: 'kind', label: '종류' },
          { key: 'dur', label: '길이', num: true, fmt: (v, r) => durText(v, r.open) },
          { key: 'count', label: '횟수', num: true, fmt: int },
          { key: 'http', label: 'HTTP', num: true, fmt: (v) => (num(v) === null ? '–' : String(v)) },
          { key: 'message', label: '내용', cls: 'mono' },
        ],
      },
    };
  },
};

// 경보·알림(034) — 시간별 알림 수(Slack · ALARM 으로 바뀐 경보, 7일) + 알림 목록
const alarmsDetail = {
  title: '경보 · 알림',
  unit: '지난 7일',
  related: ['collect', 'canary'],
  needs: () => [req('aws'), req('alerts')],
  model: (app) => {
    const alerts = app.entry(req('alerts'));
    const now = app.now();
    const base = { part: plainOf(alerts), deps: [alerts, Math.floor(now / 60_000)] };
    if (!alerts?.body) return base;
    const items = list(alerts.body.items);
    return {
      ...base,
      span: [now - 7 * DAY, now],
      time: {
        startMs: now - 7 * DAY,
        endMs: now,
        panes: [{ fmt: int }],
        series: [
          { name: 'Slack 알림', mark: 'acc', type: 'bar', stack: 'n', data: alertHours(items, now, 168, 'slack') },
          { name: '경보 ALARM', mark: 'bad', type: 'bar', stack: 'n', data: alertHours(items, now, 168, 'alarm') },
        ],
      },
      table: {
        title: '알림 목록',
        sort: { key: 'at', dir: 'desc' },
        search: ['name', 'change', 'text'],
        rows: alertRows(items),
        columns: [
          { key: 'at', label: '시각(KST)', fmt: (v) => kstText(v), csv: (r) => kstText(r.at, true) },
          { key: 'source', label: '출처' },
          { key: 'name', label: '이름' },
          { key: 'change', label: '바뀜' },
          { key: 'text', label: '글', cls: 'mono' },
        ],
      },
    };
  },
};

// 비용(034 budget) — 예산마다 실제·예측·한도
const ratio = (a, b) => (num(a) !== null && num(b) > 0 ? (a / b) * 100 : null);
const costDetail = {
  title: '비용',
  unit: 'AWS 예산',
  needs: () => [req('aws')],
  model: (app) => {
    const entry = app.entry(req('aws'));
    const part = partOf(entry, 'budget');
    const base = { part, deps: [entry] };
    if (!usable(part)) return base;
    const rows = list(part.items)
      .filter(isObj)
      .map((b) => ({ name: b.name ?? '예산', period: b.timeUnit ?? '', unit: b.unit, actual: num(b.actual), forecast: num(b.forecast), limit: num(b.limit), used: ratio(b.actual, b.limit), fore: ratio(b.forecast, b.limit) }));
    const money$ = (v, r) => money(v, r.unit);
    return {
      ...base,
      span: [app.now(), app.now()],
      table: {
        title: '예산',
        sort: { key: 'used', dir: 'desc' },
        rows,
        columns: [
          { key: 'name', label: '이름' },
          { key: 'period', label: '주기' },
          { key: 'actual', label: '실제', num: true, fmt: money$ },
          { key: 'forecast', label: '예측', num: true, fmt: money$ },
          { key: 'limit', label: '한도', num: true, fmt: money$ },
          { key: 'used', label: '실제/한도', num: true, fmt: (v) => pct(v, 0) },
          { key: 'fore', label: '예측/한도', num: true, fmt: (v) => pct(v, 0) },
        ],
      },
    };
  },
};

export const OPS_DETAILS = Object.freeze({
  cpu: seriesDetail('CPU', '% · 상자별', boxLines('cpu'), ['credit', 'ws']),
  mem: seriesDetail('메모리', '% · 상자별', boxLines('mem'), ['cpu', 'disk']),
  disk: seriesDetail('디스크', '% · 상자별', boxLines('disk'), ['io', 'mem']),
  net: seriesDetail('네트워크', '초당 · 위 들어옴 · 아래 나감', mirrorPanes('netIn', 'netOut', '들어옴', '나감'), ['cpu', 'ws']),
  io: seriesDetail('디스크 입출력', '초당 · 위 읽기 · 아래 쓰기', mirrorPanes('ebsRead', 'ebsWrite', '읽기', '쓰기'), ['disk']),
  credit: seriesDetail('CPU 크레딧', '잔량 · 사용 · 초과 과금', credits, ['cpu']),
  ws: seriesDetail('WS 접속', '대시보드 연결 수', wsLine, ['net', 'cpu']),
  canary: seriesDetail('점검(canary)', '걸린 시간 · 오류', canaryPanes, ['alarms']),
  checks: seriesDetail('상태 검사', 'EC2 · 실패 = 1', checkLines, ['cpu']),
  collect: collectDetail,
  alarms: alarmsDetail,
  cost: costDetail,
});
