// 서버 페이지(스펙 064 §3.3) — AWS 시계열(063)·수집 상태·경보·비용·도구.
// 기간 단추(6시간·24시간·7일·30일, 기본 24시간)는 JS 변수 — 바꾸면 /api/admin/aws/series?range= 하나만 곧바로 부른다.
// 시간 차트는 한 무리('time')라 한 칸을 확대·이동하면 같은 페이지의 시간 차트가 함께 움직인다(echarts.connect).
import {
  $,
  ago,
  bytes,
  call,
  clean,
  dec1,
  el,
  exName,
  hm,
  int,
  isExpired,
  isObj,
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
  start,
  stateWord,
  tag,
  usable,
  worstMonthly,
} from './common.js';
import {
  COLOR,
  SERIES,
  TONE,
  axisTip,
  base,
  blank,
  budgetGauge,
  categoryAxis,
  draw,
  hasValue,
  itemTip,
  lanes,
  legend,
  lines,
  markOf,
  mirror,
  ms,
  timeAxis,
  tip,
  unzoom,
  valueAxis,
  when,
} from './charts.js';

const GROUP = 'time';
// 상자 색은 이름을 따른다(걸러도 남은 상자의 색이 바뀌지 않게) — 차트 색 순서의 앞 셋
const BOX_MARK = Object.freeze({ collect: 'c0', data: 'c1', serve: 'c2' });
const boxMark = (box, i) => own(BOX_MARK, box) ?? markOf(i + 3);
const colorOf = (mark) => own(TONE, mark) ?? SERIES[Number(mark.slice(1))];
const ZOOM = [{ type: 'inside', xAxisIndex: 'all', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }];

// 값이 1 이상인 칸이 이어진 구간 [[시작 ms, 끝 ms]] — 상태 검사 실패 띠
export function runs(points, periodSec) {
  const step = n0(periodSec) * 1000;
  const out = [];
  for (const [t, v] of ms(points)) {
    if (!(n0(v) >= 1)) continue;
    const last = out[out.length - 1];
    if (last && last[1] >= t) last[1] = t + step;
    else out.push([t, t + step]);
  }
  return out;
}

// --- AWS 시계열 카드 (063 — 값을 못 그리면 그 칸에 상태 글) --------------------------------------------

function timeCard(app, name, build) {
  const entry = app.current('series');
  const part = partOf(entry);
  tag(`t-${name}`, part);
  once(`c-${name}`, entry, () => {
    if (!usable(part)) return blank(`c-${name}`, stateWord(part));
    return build(part, { startMs: n0(part.startTs) * 1000, endMs: n0(part.endTs) * 1000 });
  });
}
const boxes = (part) => list(part.boxes).filter((b) => isObj(b) && isObj(b.series));
const perBox = (part, key) => boxes(part).map((b, i) => ({ name: clean(b.box), points: ms(b.series[key]), mark: boxMark(b.box, i) }));
const pctLines = (id, part, span, key, refs) =>
  lines(id, { ...span, series: perBox(part, key), max: 100, fmt: (v) => pct(v), axisFmt: (v) => `${v}%`, refs, group: GROUP });

const drawCpu = (app) => timeCard(app, 'cpu', (p, span) => pctLines('c-cpu', p, span, 'cpu'));
const drawMem = (app) => timeCard(app, 'mem', (p, span) => pctLines('c-mem', p, span, 'mem'));
const drawDisk = (app) => timeCard(app, 'disk', (p, span) => pctLines('c-disk', p, span, 'disk', [[75, 'warn'], [85, 'bad']]));
const drawWs = (app) =>
  timeCard(app, 'ws', (p, span) => lines('c-ws', { ...span, series: [{ name: 'WS 접속', points: ms(p.wsClients), mark: 'c0' }], fmt: (v) => int(v), area: true, group: GROUP }));
const flipped = (id, p, span, up, down, names) =>
  mirror(id, { ...span, names, fmt: (v) => bytes(v), group: GROUP, panels: boxes(p).map((b) => ({ title: clean(b.box), up: ms(b.series[up]), down: ms(b.series[down]) })) });
const drawNet = (app) => timeCard(app, 'net', (p, span) => flipped('c-net', p, span, 'netIn', 'netOut', ['들어옴', '나감']));
const drawIo = (app) => timeCard(app, 'io', (p, span) => flipped('c-io', p, span, 'ebsRead', 'ebsWrite', ['읽기', '쓰기']));

// CPU 크레딧 — 값 있는 상자만. 위 칸 잔량 선, 아래 칸 사용 막대 + 초과 과금 빨강 막대(상자끼리 쌓음)
function credits(p, span) {
  const shown = boxes(p).filter((b) => ['creditBalance', 'creditUsage', 'surplusCharged'].some((k) => hasValue(b.series[k])));
  if (!shown.length) return blank('c-credit', '크레딧이 있는 상자 없음');
  const color = (b, i) => colorOf(boxMark(b.box, i));
  const series = [
    ...shown.map((b, i) => ({ type: 'line', name: clean(b.box), data: ms(b.series.creditBalance), showSymbol: false, lineStyle: { width: 2, color: color(b, i) }, itemStyle: { color: color(b, i) } })),
    ...shown.map((b, i) => ({ type: 'bar', name: `${clean(b.box)} 사용`, xAxisIndex: 1, yAxisIndex: 1, data: ms(b.series.creditUsage), barMaxWidth: 6, itemStyle: { color: color(b, i) } })),
    ...shown.map((b) => ({ type: 'bar', name: `${clean(b.box)} 초과 과금`, xAxisIndex: 1, yAxisIndex: 1, stack: 'surplus', data: ms(b.series.surplusCharged), barMaxWidth: 6, itemStyle: { color: COLOR.bad } })),
  ];
  const marks = [...shown.map((b, i) => boxMark(b.box, i)), ...shown.map((b, i) => boxMark(b.box, i)), ...shown.map(() => 'bad')];
  return draw('c-credit', base({
    title: [
      { text: '잔량', left: 4, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: COLOR.muted } },
      { text: '사용 · 초과 과금', left: 4, top: '60%', textStyle: { fontSize: 12, fontWeight: 600, color: COLOR.muted } },
    ],
    legend: legend({ data: shown.map((b) => clean(b.box)) }),
    grid: [
      { left: 8, right: 16, top: 28, height: '42%', containLabel: true },
      { left: 8, right: 16, top: '68%', bottom: 4, containLabel: true },
    ],
    xAxis: [timeAxis(span.startMs, span.endMs, { axisLabel: { show: false } }), timeAxis(span.startMs, span.endMs, { gridIndex: 1 })],
    yAxis: [valueAxis({ min: 0, splitNumber: 3 }), valueAxis({ gridIndex: 1, min: 0, splitNumber: 2 })],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: ZOOM,
    tooltip: axisTip((ps) => tip(when(ps[0]?.value?.[0]), ps.map((q) => [marks[q.seriesIndex], q.seriesName, dec1(q.value?.[1])]))),
    series,
  }), GROUP);
}
const drawCredit = (app) => timeCard(app, 'credit', credits);

// 점검 — 걸린 시간 선과 오류 빨간 점(그 칸의 걸린 시간 위)
function canary(p, span) {
  const duration = ms(p.canary?.durationMs);
  const errors = ms(p.canary?.errors).filter(([, v]) => n0(v) > 0);
  if (!hasValue(duration) && !errors.length) return blank('c-canary', '자료 없음');
  const at = new Map(duration.map(([t, v]) => [t, v]));
  const count = new Map(errors);
  return draw('c-canary', base({
    grid: { left: 8, right: 16, top: 12, bottom: 4, containLabel: true },
    xAxis: timeAxis(span.startMs, span.endMs),
    yAxis: valueAxis({ min: 0, axisLabel: { color: COLOR.muted, fontSize: 12, formatter: (v) => `${int(v)}ms` } }),
    dataZoom: ZOOM,
    tooltip: axisTip((ps) =>
      tip(when(ps[0]?.value?.[0]), ps.map((q) => (q.seriesIndex === 0 ? ['c0', '걸린 시간', `${int(q.value?.[1])}ms`] : ['bad', '오류', int(count.get(q.value?.[0]))]))),
    ),
    series: [
      { type: 'line', name: '걸린 시간', data: duration, showSymbol: false, lineStyle: { width: 2, color: COLOR.accent }, itemStyle: { color: COLOR.accent }, areaStyle: { color: COLOR.accent, opacity: 0.1 } },
      { type: 'scatter', name: '오류', data: errors.map(([t]) => [t, at.get(t) ?? 0]), symbolSize: 10, itemStyle: { color: COLOR.bad, borderColor: COLOR.card, borderWidth: 2 } },
    ],
  }), GROUP);
}
const drawCanary = (app) => timeCard(app, 'canary', canary);

// 상태 검사 — 상자마다 띠, 실패(값 1)가 이어진 구간을 빨강으로
function checks(p, span) {
  const shown = boxes(p);
  const segs = [];
  shown.forEach((b, row) => {
    const failed = runs(b.series.statusFailed, p.periodSec);
    segs.push({ row, from: span.startMs, to: span.endMs, tone: 'track', head: clean(b.box), tip: [[failed.length ? 'bad' : 'ok', '실패 구간', `${failed.length}개`]] });
    for (const [from, to] of failed) segs.push({ row, from, to, tone: 'bad', head: `${clean(b.box)} 상태 검사 실패`, tip: [['bad', '구간', `${when(from)} ~ ${hm(to)}`]] });
  });
  return lanes('c-checks', { ...span, rows: shown.map((b) => clean(b.box)), segs, group: GROUP });
}
const drawChecks = (app) => timeCard(app, 'checks', checks);

// 기간 단추 — 고른 값만 눌림
function drawRange(app) {
  for (const button of document.querySelectorAll('[data-range]')) button.setAttribute('aria-pressed', String(button.dataset.range === app.picked.series));
}
