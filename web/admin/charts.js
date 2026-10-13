// KimpTrack 관리자 v3 차트 모듈 (스펙 064 §3.1) — vendor/echarts-5.5.1.min.js 가 만든 전역 echarts 를 감싼다.
// 그림 칸 하나 = 인스턴스 하나(칸 id 로 기억). 라이브러리를 못 불러오면 그 칸에 '차트를 불러오지 못함' 한 줄(§3.5).
// 툴팁 서식은 모두 tip() 하나를 지난다 — 피드 글자는 echarts.format.encodeHTML 을 거친다(§3.1 보안). 축·범례·라벨
// 글자는 캔버스에 그려져 HTML 이 되지 않는다.
import { clean, el, hm, md, num, n0, list, own } from './common.js';

export const COLOR = Object.freeze({
  bg: '#121420',
  card: '#1c1f2b',
  line: '#2c3040',
  text: '#ececf1',
  muted: '#9a9cab',
  accent: '#9184d9',
  ok: '#5fbf8f',
  warn: '#e0a458',
  bad: '#e0697d',
  sub: '#6f9bee',
});
// 차트 색 순서(§3.1) — 강조·보조·정상·주의·문제 + 셋. 색은 순서대로만 쓰고 돌려쓰지 않는다
export const SERIES = Object.freeze([COLOR.accent, COLOR.sub, COLOR.ok, COLOR.warn, COLOR.bad, '#c792ea', '#7fdbca', '#f78c6c']);
export const TONE = Object.freeze({ ok: COLOR.ok, warn: COLOR.warn, bad: COLOR.bad, dim: COLOR.muted, wait: COLOR.muted });
// 툴팁 줄 머리 표시 — admin.css 의 클래스 이름만 받는다(색 글자를 HTML 에 싣지 않는다)
const MARKS = new Set(['c0', 'c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7', 'ok', 'warn', 'bad', 'dim']);
export const markOf = (i) => `c${Math.min(7, Math.max(0, i))}`;
const FONT = 'system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif';

const lib = () => globalThis.echarts;
const live = new Map(); // 칸 id → { chart, shape }

// 그림 칸에 한 줄(§3.5 — '자료 없음'·'차트를 불러오지 못함'·상태 글). 떠 있던 인스턴스는 버린다
export function blank(id, text) {
  const box = document.getElementById(id);
  if (!box) return;
  const held = live.get(id);
  if (held) {
    held.chart.dispose();
    live.delete(id);
  }
  box.replaceChildren(el('p', 'empty', text));
}

// 칸에 그린다. 모양(그리드·시리즈 수)이 같으면 시리즈만 갈아 확대·이동 상태를 지키고, 다르면 새로 그린다.
// group 이 있으면 같은 이름의 칸끼리 툴팁·확대가 함께 움직인다(echarts.connect — 서버 페이지의 시간 차트)
export function draw(id, option, group) {
  const ec = lib();
  if (!ec) return blank(id, '차트를 불러오지 못함');
  const box = document.getElementById(id);
  if (!box) return undefined;
  let held = live.get(id);
  if (!held) {
    box.replaceChildren();
    held = { chart: ec.init(box, null, { renderer: 'canvas' }), shape: '' };
    if (group) {
      held.chart.group = group;
      ec.connect(group);
    }
    live.set(id, held);
  }
  const shape = JSON.stringify([list(option.grid).length, list(option.series).map((s) => s.type)]);
  held.chart.setOption(option, shape === held.shape ? { replaceMerge: ['series'] } : { notMerge: true });
  held.shape = shape;
  return held.chart;
}

// 같은 무리의 확대를 처음으로(서버 페이지 '확대 풀기'). 시간 차트의 확대 부품은 모두 id 'zoom' — connect 가 넘기는
// dataZoomId 가 다른 칸에서도 맞게
export function unzoom(group) {
  for (const { chart } of live.values()) {
    if (chart.group === group) chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100 });
  }
}

// 창 크기가 바뀌면 모든 칸을 다시 맞춘다(§3.1) — 100ms 묶어서
let resizing = 0;
window.addEventListener('resize', () => {
  clearTimeout(resizing);
  resizing = setTimeout(() => live.forEach(({ chart }) => chart.resize()), 100);
});
// 숨겨 두었던 칸을 보일 때
export const refit = (id) => live.get(id)?.chart.resize();

// --- 툴팁 (§3.1 보안 — 글자는 모두 encodeHTML) ----------------------------------------------------

const esc = (v) => lib().format.encodeHTML(clean(v));
// 머리 한 줄 + [표시 클래스, 이름, 값] 줄 — 값이 굵게(값을 찾으러 온다)
export function tip(head, rows = []) {
  const out = head ? [`<div class="tt-h">${esc(head)}</div>`] : [];
  for (const [mark, name, value] of rows) {
    const key = MARKS.has(mark) ? `<i class="tt-k ${mark}"></i>` : '';
    out.push(`<div class="tt-r">${key}<span class="tt-n">${esc(name)}</span><b>${esc(value)}</b></div>`);
  }
  return out.join('');
}
export const when = (ms) => (num(ms) === null ? '–' : `${md(ms)} ${hm(ms)}`);

const TOOLTIP = Object.freeze({
  confine: true,
  className: 'tt',
  backgroundColor: '#262a3a',
  borderColor: COLOR.line,
  borderWidth: 1,
  padding: [8, 10],
  textStyle: { color: COLOR.text, fontSize: 13, fontFamily: FONT },
});
export const axisTip = (formatter) => ({ ...TOOLTIP, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: COLOR.muted, width: 1 } }, formatter });
export const itemTip = (formatter) => ({ ...TOOLTIP, trigger: 'item', formatter });

// --- 바탕·축 ----------------------------------------------------------------------------------

export function base(extra) {
  return { backgroundColor: 'transparent', color: SERIES, textStyle: { fontFamily: FONT, color: COLOR.muted }, animationDuration: 300, ...extra };
}
const LABEL = Object.freeze({ color: COLOR.muted, fontSize: 12 });
// 시간 축 글자 — 단계마다 한국 꼴(영어 달 이름을 쓰지 않는다)
const TIME_LABEL = Object.freeze({ year: '{yyyy}', month: '{M}월', day: '{M}/{d}', hour: '{HH}:{mm}', minute: '{HH}:{mm}', second: '{HH}:{mm}:{ss}', millisecond: '{HH}:{mm}:{ss}', none: '{HH}:{mm}' });
export const timeAxis = (startMs, endMs, extra) => ({
  type: 'time',
  min: startMs,
  max: endMs,
  axisLine: { lineStyle: { color: COLOR.line } },
  axisTick: { show: false },
  splitLine: { show: false },
  axisLabel: { ...LABEL, hideOverlap: true, formatter: TIME_LABEL },
  ...extra,
});
export const valueAxis = (extra) => ({
  type: 'value',
  axisLine: { show: false },
  axisTick: { show: false },
  splitLine: { lineStyle: { color: COLOR.line } },
  axisLabel: { ...LABEL },
  ...extra,
});
export const categoryAxis = (data, extra) => ({
  type: 'category',
  data,
  axisLine: { lineStyle: { color: COLOR.line } },
  axisTick: { show: false },
  axisLabel: { ...LABEL },
  ...extra,
});
export const legend = (extra) => ({ top: 0, right: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 4, textStyle: { color: COLOR.muted, fontSize: 12 }, ...extra });

// 점 [[ts초, 값|null], …] → [[ms, 값|null], …] — 모양이 틀린 점은 뺀다, null 은 선을 끊는다(보간 없음)
export const ms = (points) => list(points).filter((p) => Array.isArray(p) && num(p[0]) !== null).map((p) => [p[0] * 1000, num(p[1])]);
export const hasValue = (points) => list(points).some((p) => num(p[1]) !== null);

// --- 자주 쓰는 그림 ------------------------------------------------------------------------------

// 작은 선(큰 수 곁) — 축 없음, 마지막 값에 점. fmt 는 툴팁 값 글
export function spark(id, points, color, fmt) {
  const data = ms(points);
  if (!hasValue(data)) return blank(id, '자료 없음');
  return draw(id, base({
    grid: { left: 2, right: 6, top: 6, bottom: 2 },
    xAxis: { type: 'time', show: false },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: axisTip((ps) => tip(when(ps[0]?.value?.[0]), [['', '', fmt(ps[0]?.value?.[1])]])),
    series: [{ type: 'line', data, showSymbol: false, smooth: false, lineStyle: { width: 2, color }, itemStyle: { color }, areaStyle: { color, opacity: 0.1 } }],
  }));
}

// 시간 선 여럿 — 시리즈 = { name, points(ms), mark(c0…) }. 둘 이상이면 범례 + 끝 이름표(색만으로 가르지 않게)
export function lines(id, o) {
  const shown = o.series.filter((s) => hasValue(s.points));
  if (!shown.length) return blank(id, '자료 없음');
  const color = (s) => own(TONE, s.mark) ?? SERIES[Number(s.mark.slice(1))];
  return draw(id, base({
    grid: { left: 8, right: shown.length > 1 ? 64 : 12, top: shown.length > 1 ? 28 : 12, bottom: 4, containLabel: true },
    legend: shown.length > 1 ? legend({ data: shown.map((s) => s.name) }) : undefined,
    xAxis: timeAxis(o.startMs, o.endMs),
    yAxis: valueAxis({ min: 0, max: o.max, axisLabel: { ...LABEL, formatter: o.axisFmt ?? o.fmt } }),
    dataZoom: o.group ? [{ type: 'inside', id: 'zoom', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }] : undefined,
    tooltip: axisTip((ps) => tip(when(ps[0]?.value?.[0]), ps.map((p) => [shown[p.seriesIndex]?.mark ?? '', p.seriesName, o.fmt(p.value?.[1])]))),
    series: shown.map((s, i) => ({
      type: 'line',
      name: s.name,
      data: s.points,
      showSymbol: false,
      lineStyle: { width: 2, color: color(s) },
      itemStyle: { color: color(s) },
      areaStyle: o.area ? { color: color(s), opacity: 0.1 } : undefined,
      endLabel: shown.length > 1 ? { show: true, formatter: '{a}', color: COLOR.muted, fontSize: 12 } : undefined,
      markLine: i === 0 && o.refs ? { symbol: 'none', silent: true, label: { color: COLOR.muted, formatter: '{c}' }, data: o.refs.map(([y, tone]) => ({ yAxis: y, lineStyle: { color: TONE[tone], type: 'solid', width: 1, opacity: 0.7 } })) } : undefined,
    })),
  }), o.group);
}

// 가로 막대 순위 — items = [{ name, value, tip:[[mark, 이름, 값]…] }], 위에서부터 큰 순(받은 순서 그대로)
export function bars(id, items, o = {}) {
  const shown = items.filter((it) => num(it.value) !== null && it.value > 0);
  if (!shown.length) return blank(id, '자료 없음');
  const rows = [...shown].reverse(); // 범주 축은 아래에서 위로 쌓는다
  return draw(id, base({
    grid: { left: 8, right: 56, top: 4, bottom: 4, containLabel: true },
    xAxis: valueAxis({ show: false }),
    yAxis: categoryAxis(rows.map((it) => it.name), { axisLine: { show: false }, axisLabel: { ...LABEL, color: COLOR.text, width: o.labelWidth ?? 140, overflow: 'truncate' } }),
    tooltip: itemTip((p) => tip(rows[p.dataIndex]?.name, rows[p.dataIndex]?.tip ?? [])),
    series: [{
      type: 'bar',
      data: rows.map((it) => ({ value: it.value, itemStyle: { color: it.color ?? o.color ?? COLOR.accent } })),
      barMaxWidth: 18,
      itemStyle: { borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', color: COLOR.muted, formatter: (p) => o.fmt(p.value) },
    }],
  }));
}

// 도넛 — items = [{ name, value, color, tip }] (0 은 뺀다), 가운데 큰 글자 하나
export function donut(id, items, center) {
  const shown = items.filter((it) => num(it.value) !== null && it.value > 0);
  if (!shown.length) return blank(id, '자료 없음');
  return draw(id, base({
    tooltip: itemTip((p) => tip(shown[p.dataIndex]?.name, shown[p.dataIndex]?.tip ?? [])),
    legend: legend({ orient: 'vertical', top: 'middle', right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { color: COLOR.muted, fontSize: 12, width: 130, overflow: 'truncate' } }),
    title: center ? { text: center, left: '29%', top: 'middle', textAlign: 'center', textStyle: { color: COLOR.text, fontSize: 18, fontWeight: 600 } } : undefined,
    series: [{
      type: 'pie',
      radius: ['52%', '78%'],
      center: ['30%', '50%'],
      avoidLabelOverlap: true,
      label: { show: false },
      itemStyle: { borderColor: COLOR.card, borderWidth: 2 },
      data: shown.map((it) => ({ name: it.name, value: it.value, itemStyle: { color: it.color } })),
    }],
  }));
}

// 작은 막대(큰 수 곁 — 시간마다 센 수). points = [[ts초, 수]]
export function sparkBars(id, points, color, fmt) {
  const data = ms(points);
  if (!data.some((p) => num(p[1]) !== null)) return blank(id, '자료 없음');
  return draw(id, base({
    grid: { left: 2, right: 2, top: 4, bottom: 2 },
    xAxis: { type: 'time', show: false },
    yAxis: { type: 'value', show: false, min: 0, minInterval: 1 },
    tooltip: axisTip((ps) => tip(when(ps[0]?.value?.[0]), [['', '', fmt(ps[0]?.value?.[1])]])),
    series: [{ type: 'bar', data, barMaxWidth: 6, itemStyle: { color, borderRadius: [2, 2, 0, 0] } }],
  }));
}

// 반원 게이지 여럿(상자 카드 — CPU·메모리·디스크). items = [{ label, value(0~100|null), tone }], 값 글자는 상태일 때만 색
export function gauges(id, items) {
  const width = 100 / items.length;
  return draw(id, base({
    series: items.map((it, i) => ({
      type: 'gauge',
      center: [`${width * i + width / 2}%`, '62%'],
      radius: '86%',
      startAngle: 200,
      endAngle: -20,
      min: 0,
      max: 100,
      progress: { show: true, width: 9, roundCap: true, itemStyle: { color: TONE[it.tone] ?? COLOR.muted } },
      axisLine: { roundCap: true, lineStyle: { width: 9, color: [[1, COLOR.line]] } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      pointer: { show: false },
      anchor: { show: false },
      title: { offsetCenter: [0, '78%'], color: COLOR.muted, fontSize: 12 },
      detail: {
        offsetCenter: [0, '8%'],
        fontSize: 20,
        fontWeight: 700,
        color: it.tone === 'bad' || it.tone === 'warn' ? TONE[it.tone] : COLOR.text,
        formatter: () => (num(it.value) === null ? '–' : `${Math.round(it.value)}%`),
      },
      data: [{ value: num(it.value) ?? 0, name: it.label }],
    })),
  }));
}

// 예산 게이지(§3.2·§3.3) — 실제·예측 두 바늘(실제는 상태 색 + 같은 길이의 호, 예측은 흐린 짧은 바늘 — 있을 때만).
// 한도 100% 넘는 칸은 붉게, 끝은 120%
export function budgetGauge(id, b, money) {
  const r = (v) => Math.min(120, Math.max(0, (n0(v) / b.limit) * 100));
  const tone = b.actual >= b.limit ? 'bad' : num(b.forecast) !== null && b.forecast >= b.limit ? 'warn' : 'ok';
  const arc = {
    type: 'gauge',
    center: ['50%', '86%'],
    radius: '150%',
    startAngle: 180,
    endAngle: 0,
    min: 0,
    max: 120,
    splitNumber: 6,
    axisLine: { lineStyle: { width: 10, color: [[100 / 120, COLOR.line], [1, 'rgba(224, 105, 125, 0.35)']] } },
    axisTick: { show: false },
    splitLine: { show: false },
    axisLabel: { show: false },
    anchor: { show: true, size: 6, itemStyle: { color: COLOR.text } },
    title: { show: false },
    detail: { show: false },
  };
  const needle = (length, width, color) => ({ show: true, length, width, itemStyle: { color } });
  const series = [{ ...arc, progress: { show: true, width: 10, itemStyle: { color: TONE[tone] } }, pointer: needle('72%', 4, TONE[tone]), data: [{ value: r(b.actual), name: '실제' }] }];
  if (num(b.forecast) !== null) {
    series.push({ ...arc, axisLine: { show: false }, progress: { show: false }, pointer: needle('52%', 3, COLOR.muted), data: [{ value: r(b.forecast), name: '예측' }] });
  }
  return draw(id, base({
    tooltip: itemTip(() => tip('이번 달 예산', [[tone, '실제', money(b.actual)], ['dim', '예측', money(b.forecast)], ['dim', '한도', money(b.limit)]])),
    series,
  }));
}

// 가로 띠(시간 구간) — rows = [이름표], segs = [{ row, from(ms), to(ms), tone, head, tip }] 앞의 것부터 그린다(뒤가 위).
// 수집 상태(정상 초록·끊김 빨강)와 상태 검사(실패 빨강)가 쓴다. 높이: 문제 구간은 굵게, 바탕 띠는 얇게
// 바탕 띠(정상·흐림·빈 자리)는 얇게, 문제 구간은 굵게
const BASE_TONES = new Set(['ok', 'dim', 'track']);
export function lanes(id, o) {
  if (!o.rows.length) return blank(id, '자료 없음');
  const color = (tone) => TONE[tone] ?? COLOR.line;
  return draw(id, base({
    grid: { left: 8, right: 16, top: 4, bottom: 4, containLabel: true },
    xAxis: timeAxis(o.startMs, o.endMs),
    yAxis: categoryAxis(o.rows, { inverse: true, axisLine: { show: false }, axisLabel: { ...LABEL, color: COLOR.text } }),
    dataZoom: o.group ? [{ type: 'inside', id: 'zoom', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }] : undefined,
    tooltip: itemTip((p) => tip(o.segs[p.dataIndex]?.head, o.segs[p.dataIndex]?.tip ?? [])),
    series: [{
      type: 'custom',
      encode: { x: [1, 2], y: 0 },
      data: o.segs.map((s) => [s.row, s.from, s.to]),
      renderItem: (params, api) => {
        const seg = o.segs[params.dataIndex];
        const from = api.coord([api.value(1), api.value(0)]);
        const to = api.coord([api.value(2), api.value(0)]);
        const height = api.size([0, 1])[1] * (BASE_TONES.has(seg.tone) ? 0.28 : 0.6);
        const box = params.coordSys;
        const shape = lib().graphic.clipRectByRect(
          { x: from[0], y: from[1] - height / 2, width: Math.max(3, to[0] - from[0]), height },
          { x: box.x, y: box.y, width: box.width, height: box.height },
        );
        return shape && { type: 'rect', transition: [], shape: { ...shape, r: 2 }, style: { fill: color(seg.tone) } };
      },
    }],
  }), o.group);
}

// 위아래로 뒤집은 면적의 작은 그림 여럿(상자마다 한 칸 — 들어옴·읽기 위, 나감·쓰기 아래). panels = [{ title, up, down }]
// 점은 [[ms, 값]]. 칸마다 세로 눈금이 따로다(상자마다 크기가 수십 배 다르다)
export function mirror(id, o) {
  const panels = o.panels.filter((p) => hasValue(p.up) || hasValue(p.down));
  if (!panels.length) return blank(id, '자료 없음');
  const each = 100 / panels.length;
  const axes = panels.map((_, i) => i);
  const flip = (points) => points.map(([t, v]) => [t, v === null ? null : -v]);
  return draw(id, base({
    title: panels.map((p, i) => ({ text: p.title, left: 4, top: `${i * each}%`, textStyle: { fontSize: 12, fontWeight: 600, color: COLOR.muted } })),
    legend: legend({ data: o.names }),
    grid: panels.map((_, i) => ({ left: 8, right: 16, top: `${i * each + 5}%`, height: `${each - 13}%`, containLabel: true })),
    xAxis: panels.map((_, i) => timeAxis(o.startMs, o.endMs, { gridIndex: i, axisLabel: { show: i === panels.length - 1, ...LABEL, hideOverlap: true, formatter: TIME_LABEL } })),
    yAxis: panels.map((_, i) => valueAxis({ gridIndex: i, splitNumber: 2, axisLabel: { ...LABEL, formatter: o.fmt } })),
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: o.group ? [{ type: 'inside', id: 'zoom', xAxisIndex: axes, zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }] : undefined,
    tooltip: axisTip((ps) => tip(when(ps[0]?.value?.[0]), ps.map((p) => [p.seriesName === o.names[0] ? 'c0' : 'c1', `${panels[Math.floor(p.seriesIndex / 2)]?.title} ${p.seriesName}`, o.fmt(p.value?.[1])]))),
    series: panels.flatMap((p, i) => [
      { type: 'line', name: o.names[0], xAxisIndex: i, yAxisIndex: i, data: p.up, showSymbol: false, lineStyle: { width: 1.5, color: COLOR.accent }, itemStyle: { color: COLOR.accent }, areaStyle: { color: COLOR.accent, opacity: 0.18 } },
      { type: 'line', name: o.names[1], xAxisIndex: i, yAxisIndex: i, data: flip(p.down), showSymbol: false, lineStyle: { width: 1.5, color: COLOR.sub }, itemStyle: { color: COLOR.sub }, areaStyle: { color: COLOR.sub, opacity: 0.18 } },
    ]),
  }), o.group);
}
