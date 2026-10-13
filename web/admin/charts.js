// ECharts 5.5.1(vendor 사본 — 전역 echarts)를 감싼다(스펙 066 §3.1·조사 §6). 칸 id 마다 인스턴스 하나, 테마 'kt' 는 admin.css 의
// CSS 변수에서 읽어 한 번 등록한다(CSS 가 하나의 원본). 툴팁 HTML 은 모두 tip() 하나를 지난다 — 피드 글자는 echarts.format.encodeHTML
// (064 §3.1 보안). 축·범례·이름표 글자는 캔버스에 그려져 HTML 이 되지 않는다. 라이브러리를 못 불러오면 그림 칸에 한 줄.
import { clean, el, hm, int, list, md, num, own, short } from './common.js';
import { change, hasValue, signed } from './calc.js';

// 토큰 — admin.css :root 와 같은 값(조사 §5). 문서의 CSS 변수를 읽지 못하면(시험·CSS 못 받음) 이 값을 쓴다
export const TOKENS = Object.freeze({
  bg: '#121420',
  surface: '#1c1f2b',
  raised: '#252938',
  line: '#2c3040',
  'line-strong': '#3a3f52',
  grid: '#262a37',
  'text-1': '#ececf1',
  'text-2': '#a7aab9',
  'text-3': '#8e92a4',
  accent: '#9184d9',
  ok: '#5fbf8f',
  warn: '#e0a458',
  bad: '#e0697d',
  none: '#8e92a4',
  c1: '#3987e5',
  c2: '#d95926',
  c3: '#199e70',
  c4: '#c98500',
  c5: '#d55181',
  c6: '#008300',
  c7: '#9085e9',
  c8: '#e66767',
  'seq-1': '#184f95',
  'seq-2': '#256abf',
  'seq-3': '#3987e5',
  'seq-4': '#6da7ec',
  'seq-5': '#b7d3f6',
});
const T = { ...TOKENS };
const FONT = 'system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif';
const lib = () => globalThis.echarts;

function readTokens() {
  try {
    const css = globalThis.getComputedStyle?.(document.documentElement);
    for (const name of Object.keys(TOKENS)) {
      const v = css?.getPropertyValue(`--${name}`)?.trim();
      if (/^#[0-9a-f]{6}$/i.test(v ?? '')) T[name] = v;
    }
  } catch {
    // CSS 를 못 읽으면 기본값 그대로
  }
}
export const tok = (name) => T[name];
export function alpha(hex, a) {
  const n = Number.parseInt(String(hex).slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

// 색 이름 → 색. c1~c8·k1~k8 = 차트 8색(누구), acc = 강조(계열 하나짜리), ok·warn·bad·none = 상태(뜻), cmp = 이전 기간, x = 기타
const NAMED = Object.freeze({ acc: 'accent', kacc: 'accent', ok: 'ok', kok: 'ok', warn: 'warn', kwarn: 'warn', bad: 'bad', kbad: 'bad', none: 'none', cmp: 'text-3', x: 'line-strong', kx: 'line-strong' });
export function color(mark) {
  const m = /^[kc]([1-8])$/.exec(mark ?? '');
  return m ? T[`c${m[1]}`] : T[own(NAMED, mark) ?? 'text-3'];
}
// 툴팁 줄 머리 — admin.css 의 .tt-k 클래스 이름만(색 글자를 HTML 에 싣지 않는다)
const TIP_MARK = Object.freeze({ acc: 'acc', kacc: 'acc', ok: 'ok', kok: 'ok', warn: 'warn', kwarn: 'warn', bad: 'bad', kbad: 'bad', cmp: 'cmp' });
function tipMark(mark) {
  const m = /^[kc]([1-8])$/.exec(mark ?? '');
  return m ? `c${m[1]}` : (own(TIP_MARK, mark) ?? '');
}

function theme() {
  const axis = {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: T['text-3'], fontSize: 11, fontFamily: FONT },
    splitLine: { lineStyle: { color: T.grid, width: 1 } },
    splitArea: { show: false },
    nameTextStyle: { color: T['text-2'], fontSize: 12 },
  };
  return {
    color: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => T[`c${i}`]),
    backgroundColor: 'transparent',
    textStyle: { fontFamily: FONT, color: T['text-2'] },
    title: { textStyle: { color: T['text-2'], fontSize: 12, fontWeight: 600 } },
    legend: { textStyle: { color: T['text-2'], fontSize: 12 }, inactiveColor: T['line-strong'] },
    tooltip: {
      backgroundColor: T.raised,
      borderColor: T['line-strong'],
      borderWidth: 1,
      textStyle: { color: T['text-1'], fontSize: 12, fontFamily: FONT },
      extraCssText: 'box-shadow: 0 8px 24px rgb(0 0 0 / .45); border-radius: 6px;',
    },
    categoryAxis: axis,
    valueAxis: axis,
    timeAxis: axis,
    logAxis: axis,
    line: { symbol: 'circle', symbolSize: 7, showSymbol: false, lineStyle: { width: 2 } },
    bar: { barMaxWidth: 24 },
    animation: false,
  };
}
let themed = false;
function ensureTheme(ec) {
  if (themed) return;
  readTokens();
  if (typeof ec.registerTheme === 'function') ec.registerTheme('kt', theme());
  themed = true;
}

const live = new Map(); // 칸 id → { chart, box, shape, ro, layout }

// 시간 그림의 칸 배치(px) — 칸 높이를 알아야 범례·칸 이름·눈금 글자가 겹치지 않게 놓을 수 있어 그릴 때와 크기가 바뀔 때 셈한다
// (퍼센트로 두면 낮은 카드에서 칸 이름이 범례·앞 칸 맨 아래 눈금 글자와 같은 줄에 앉았다). containLabel 은 세로축 글자를 가로로만
// 비켜 주므로 위아래 자리는 여기서 남긴다: 위 = 범례 줄 + 첫 칸 이름(없으면 맨 위 눈금 글자 반), 칸 사이 = 앞 칸 맨 아래 눈금 글자 반
// + 다음 칸 이름, 아래 = 미니맵(판). 가로축 글자는 마지막 칸 안(containLabel)
// 마지막 칸은 가로축 글자(containLabel 이 그 칸 안에서 뺀다)만큼 더 높게 — 칸마다 그림 높이가 같다. 눈금 수는 그림 높이에 맞춘다
// (낮은 칸에 눈금 글자 넷이 들어가 서로 겹치지 않게)
const LAYOUT = new WeakMap(); // option → { titles:[bool…], legend, detail, right, labels:[이름…]|null, ends:[계열 순번…] }
export const PX = Object.freeze({ legend: 28, title: 26, half: 9, edge: 6, gapTitled: 36, gap: 22, foot: 6, slider: 40, xLabels: 20, endPad: 16, endShare: 0.3 });
// 눈금·선 끝 글자(11px) 폭 어림 — 한글 1em · 숫자·영문 0.6em · 띄어쓰기 0.3em · 점·쌍점 0.4em
export const textWidth = (text, size = 11) =>
  [...String(text ?? '')].reduce((w, ch) => w + size * (/[\uac00-\ud7a3]/.test(ch) ? 1 : /\s/.test(ch) ? 0.3 : /[·:,/.]/.test(ch) ? 0.4 : 0.6), 0);
// 선 끝 이름표 자리 — 가장 긴 이름 + 여백을 오른쪽에 남긴다. 그림 폭의 30% 를 넘으면(좁은 카드) 이름표를 끄고 범례만 둔다
export function endSpace(spec, width) {
  if (!spec.labels?.length) return { right: spec.right, ends: null };
  const need = Math.ceil(Math.max(...spec.labels.map((name) => textWidth(name)))) + PX.endPad;
  return width > 0 && need <= width * PX.endShare ? { right: need, ends: true } : { right: 14, ends: false };
}
export function placeGrids(spec, height, width = 0) {
  const n = spec.titles.length;
  const { right, ends } = endSpace(spec, width);
  const head = (spec.legend ? PX.legend : PX.edge) + (spec.titles[0] ? PX.title : PX.half);
  const gap = spec.titles.slice(1).some(Boolean) ? PX.gapTitled : PX.gap;
  const foot = PX.foot + (spec.detail ? PX.slider : 0);
  const h = Math.max(24, (height - head - foot - gap * (n - 1) - PX.xLabels) / n);
  return {
    grid: spec.titles.map((_, i) => ({ left: 8, right, top: Math.round(head + i * (h + gap)), height: Math.floor(h + (i === n - 1 ? PX.xLabels : 0)), containLabel: true })),
    splits: spec.titles.map(() => (h >= 100 ? 3 : h >= 56 ? 2 : 1)),
    ends,
  };
}
// 지금 칸 높이로 낸 배치 — 높이를 모르면(숨은 칸·시험의 가짜 차트) null 이고 option 의 퍼센트 배치를 그대로 쓴다
function layoutOf(held) {
  const height = typeof held.chart.getHeight === 'function' ? held.chart.getHeight() : 0;
  const width = typeof held.chart.getWidth === 'function' ? held.chart.getWidth() : 0;
  return held.layout && height > 0 ? placeGrids(held.layout, height, width) : null;
}
// 선 끝 이름표가 있는 계열만 켜고 끈다(나머지 계열은 그대로 — 순번으로 합쳐진다)
const endToggles = (held, on) => {
  const ends = new Set(held.layout.ends);
  return Array.from({ length: held.layout.count }, (_, i) => (ends.has(i) ? { endLabel: { show: on } } : {}));
};
function relayout(held) {
  const placed = layoutOf(held);
  if (!placed) return;
  const next = { grid: placed.grid, yAxis: placed.splits.map((splitNumber) => ({ splitNumber })) };
  if (placed.ends !== null) next.series = endToggles(held, placed.ends);
  held.chart.setOption(next);
}

export function dispose(id) {
  const held = live.get(id);
  if (!held) return;
  held.ro?.disconnect();
  held.chart.dispose();
  live.delete(id);
}
// 그림 칸에 한 줄('자료 없음'·'연결 안 됨'·'차트를 불러오지 못함'). 떠 있던 인스턴스는 버린다
export function blank(id, text) {
  const box = document.getElementById(id);
  dispose(id);
  if (box) box.replaceChildren(el('p', 'empty', text));
}
export const chartOf = (id) => live.get(id)?.chart;

// 칸에 그린다. 모양(칸·계열 종류·이름·확대 부품)이 같으면 계열만 갈아 확대·이동을 지키고, 다르면 새로 그린다.
// group 이 있으면 같은 이름의 칸끼리 십자선·확대가 함께 움직인다(echarts.connect — 상세 판 그림은 넣지 않는다)
export function draw(id, option, group) {
  const ec = lib();
  if (!ec) return blank(id, '차트를 불러오지 못함');
  const box = document.getElementById(id);
  if (!box) return undefined;
  ensureTheme(ec);
  let held = live.get(id);
  if (held && held.box !== box) {
    dispose(id);
    held = undefined;
  }
  if (!held) {
    box.replaceChildren();
    const chart = ec.init(box, 'kt', { renderer: 'canvas' });
    held = { chart, box, shape: '' };
    if (group) {
      chart.group = group;
      ec.connect(group);
    }
    if (typeof ResizeObserver === 'function') {
      const slot = held;
      slot.ro = new ResizeObserver(() => {
        chart.resize();
        relayout(slot);
      });
      slot.ro.observe(box);
    }
    live.set(id, held);
  }
  held.layout = LAYOUT.get(option) ?? null;
  const placed = layoutOf(held);
  if (placed) {
    option.grid = placed.grid;
    list(option.yAxis).forEach((axis, i) => {
      axis.splitNumber = placed.splits[i];
    });
    if (placed.ends === false) for (const i of held.layout.ends) if (option.series[i]?.endLabel) option.series[i].endLabel.show = false;
  }
  const grids = Array.isArray(option.grid) ? option.grid.length : option.grid ? 1 : 0;
  const shape = JSON.stringify([grids, list(option.series).map((s) => `${s.type}:${s.name ?? ''}`), list(option.dataZoom).length]);
  held.chart.setOption(option, shape === held.shape ? { replaceMerge: ['series'] } : { notMerge: true });
  held.shape = shape;
  return held.chart;
}

// 같은 무리의 확대를 처음으로
export function unzoom(group) {
  for (const { chart } of live.values()) if (chart.group === group) chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100 });
}
// ResizeObserver 가 없는 곳 — 창 크기가 바뀌면 모든 칸을 다시 맞춘다
if (typeof ResizeObserver !== 'function' && typeof window !== 'undefined') {
  let resizing = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizing);
    resizing = setTimeout(() => live.forEach((held) => {
      held.chart.resize();
      relayout(held);
    }), 100);
  });
}

// --- 툴팁 (글자는 모두 encodeHTML) ---------------------------------------------------------------

const esc = (v) => lib().format.encodeHTML(clean(v));
// 머리 한 줄 + [색 이름, 이름, 값] 줄 — 값이 굵게(값을 찾으러 온다)
export function tip(head, rows = []) {
  const out = head ? [`<div class="tt-h">${esc(head)}</div>`] : [];
  for (const [mark, name, value] of rows) {
    const cls = tipMark(mark);
    const key = cls ? `<i class="tt-k ${cls}"></i>` : '';
    out.push(`<div class="tt-r">${key}<span class="tt-n">${esc(name)}</span><b>${esc(value)}</b></div>`);
  }
  return out.join('');
}
export const when = (ms) => (num(ms) === null ? '–' : `${md(ms)} ${hm(ms)}`);

const TIP = Object.freeze({ confine: true, className: 'tt', transitionDuration: 0 });
export const axisTip = (formatter) => ({ ...TIP, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: T['line-strong'], width: 1 } }, formatter });
export const itemTip = (formatter) => ({ ...TIP, trigger: 'item', formatter });

// --- 축 ----------------------------------------------------------------------------------------

// 시간 축 글자 — 단계마다 짧은 한국 꼴('14:00'·'10/14' — 영어 달 이름을 쓰지 않는다)
const TIME_LABEL = Object.freeze({ year: '{yyyy}', month: '{M}월', day: '{M}/{d}', hour: '{HH}:{mm}', minute: '{HH}:{mm}', second: '{HH}:{mm}:{ss}', millisecond: '{HH}:{mm}:{ss}', none: '{HH}:{mm}' });
const LABEL = () => ({ color: T['text-3'], fontSize: 11 });
export const timeAxis = (startMs, endMs, extra = {}) => ({
  type: 'time',
  min: startMs,
  max: endMs,
  axisLine: { show: false },
  axisTick: { show: false },
  splitLine: { show: false },
  ...extra,
  axisLabel: { ...LABEL(), hideOverlap: true, formatter: TIME_LABEL, ...extra.axisLabel },
});
export const valueAxis = (extra = {}) => ({
  type: 'value',
  splitNumber: 3,
  axisLine: { show: false },
  axisTick: { show: false },
  splitLine: { lineStyle: { color: T.grid } },
  ...extra,
  axisLabel: { ...LABEL(), formatter: short, ...extra.axisLabel },
});
export const catAxis = (data, extra = {}) => ({
  type: 'category',
  data,
  axisLine: { show: false },
  axisTick: { show: false },
  ...extra,
  axisLabel: { ...LABEL(), ...extra.axisLabel },
});

// --- 그림 option (066 §3.1 차트 규칙 — 선 2px·점은 올릴 때만·가로 격자 3~4개·눈금 11px·애니메이션 끔·lttb) -----------------

const flip = (data, neg) => (neg ? list(data).map((p) => [p[0], num(p[1]) === null ? null : -p[1]]) : data);
const SLIDER = () => ({
  type: 'slider',
  id: 'mini',
  height: 22,
  bottom: 8,
  borderColor: T.line,
  backgroundColor: 'transparent',
  fillerColor: alpha(T.accent, 0.14),
  dataBackground: { lineStyle: { color: T['text-3'], width: 1 }, areaStyle: { color: T.grid } },
  selectedDataBackground: { lineStyle: { color: T.accent, width: 1 }, areaStyle: { color: alpha(T.accent, 0.12) } },
  handleStyle: { color: T['text-2'], borderColor: T['line-strong'] },
  moveHandleStyle: { color: T['line-strong'] },
  textStyle: { color: T['text-3'], fontSize: 11 },
  labelFormatter: (v) => when(v),
});

// 작은 선(큰 수 곁) — 축 없음. 이전 기간이 있으면 점선으로 아래에
export function sparkOption(data, mark, fmt, cmp) {
  const c = color(mark);
  const series = [];
  if (hasValue(cmp)) series.push({ type: 'line', data: cmp, showSymbol: false, silent: true, z: 1, lineStyle: { width: 1, type: 'dashed', color: T['text-3'] } });
  series.push({ type: 'line', data, showSymbol: false, z: 2, lineStyle: { width: 1.5, color: c }, itemStyle: { color: c }, areaStyle: { color: c, opacity: 0.1 } });
  const main = series.length - 1;
  return {
    animation: false,
    grid: { left: 1, right: 1, top: 3, bottom: 1 },
    xAxis: { type: 'time', show: false },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: axisTip((ps) => {
      const p = list([ps].flat()).find((q) => q.seriesIndex === main);
      return p ? tip(when(p.value?.[0]), [[mark, '', fmt(p.value?.[1])]]) : '';
    }),
    series,
  };
}
// 작은 막대(시간마다 센 수)
export function sparkBarsOption(data, mark, fmt) {
  return {
    animation: false,
    grid: { left: 1, right: 1, top: 3, bottom: 1 },
    xAxis: { type: 'time', show: false },
    yAxis: { type: 'value', show: false, min: 0, minInterval: 1 },
    tooltip: axisTip((ps) => {
      const p = list([ps].flat())[0];
      return p ? tip(when(p.value?.[0]), [[mark, '', fmt(p.value?.[1])]]) : '';
    }),
    series: [{ type: 'bar', data, barMaxWidth: 6, itemStyle: { color: color(mark), borderRadius: [2, 2, 0, 0] } }],
  };
}

// 시간 그림 — 칸(panes) 여럿을 같은 시간 축 위아래로(둘째 y축 대신), 십자선 공유(axisPointer.link). 이전 기간은 점선·--text-3·낮은 z.
// o = { startMs, endMs, panes:[{ title?, fmt, axisFmt?, min?, max?, int?(센 수 — 눈금도 정수), refs?:[[y, 색 이름, 이름표]] }],
//       series:[{ name, mark, data, cmp?, type?:'line'|'bar', pane?, neg?(아래로 뒤집음), area?, step?, stack? }],
//       areas?:[[from, to, 이름]](옅은 빨강 구간), zoom?:'card'|'detail', legend?, labels?(선 끝 이름표), daily?(날 칸 — 툴팁 머리는 날짜만) }
export function timeOption(o) {
  const panes = list(o.panes).length ? o.panes : [{ fmt: o.fmt ?? short }];
  const n = panes.length;
  const shown = list(o.series).filter((s) => hasValue(s.data) || hasValue(s.cmp));
  const detail = o.zoom === 'detail';
  const legendOn = o.legend ?? (!detail && shown.length > 1);
  const labels = (o.labels ?? !detail) && shown.length > 1 && shown.length <= 4 && n === 1;
  const paneOf = (s) => Math.min(n - 1, Math.max(0, s.pane ?? 0));
  const neg = (i) => shown.some((s) => paneOf(s) === i && s.neg);
  // 칸 배치 — 그릴 때 px 로 다시 셈한다(placeGrids). 높이를 모를 때의 퍼센트 배치: 위(범례·첫 칸 이름) · 칸 사이 틈 · 아래(미니맵)
  const head = legendOn || panes[0].title ? 12 : 5;
  const foot = detail ? 16 : 10;
  const gap = n > 1 ? 10 : 0;
  const h = (100 - head - foot - gap * (n - 1)) / n;
  const axes = [...Array(n).keys()];
  const meta = [];
  const series = [];
  for (const s of shown) {
    if (!hasValue(s.cmp)) continue;
    const i = paneOf(s);
    meta.push({ s, cmp: true });
    series.push({ type: 'line', name: `${s.name} · 이전`, xAxisIndex: i, yAxisIndex: i, data: flip(s.cmp, s.neg), showSymbol: false, silent: true, z: 1, step: s.step, lineStyle: { width: 1.5, type: 'dashed', color: T['text-3'] }, itemStyle: { color: T['text-3'] }, emphasis: { disabled: true } });
  }
  const seen = new Set();
  for (const s of shown) {
    const i = paneOf(s);
    const c = color(s.mark);
    const first = !seen.has(i);
    seen.add(i);
    const base = { name: s.name, xAxisIndex: i, yAxisIndex: i, data: flip(s.data, s.neg), z: 3 };
    const item =
      s.type === 'bar'
        ? { ...base, type: 'bar', stack: s.stack, barMaxWidth: detail ? 18 : 12, itemStyle: { color: c, borderRadius: s.neg ? [0, 0, 3, 3] : [3, 3, 0, 0] } }
        : {
            ...base,
            type: 'line',
            showSymbol: false,
            symbolSize: 7,
            step: s.step,
            sampling: 'lttb',
            lineStyle: { width: s.width ?? 2, color: c },
            itemStyle: { color: c },
            areaStyle: s.area ? { color: c, opacity: 0.1 } : undefined,
            endLabel: labels ? { show: true, formatter: '{a}', color: T['text-2'], fontSize: 11 } : undefined,
            labelLayout: labels ? { moveOverlap: 'shiftY' } : undefined,
          };
    if (first && list(panes[i].refs).length) {
      item.markLine = {
        symbol: 'none',
        silent: true,
        label: { position: 'insideEndTop', color: T['text-3'], fontSize: 11, formatter: '{b}' },
        data: panes[i].refs.map(([y, tone, label]) => ({ yAxis: y, name: label ?? String(y), lineStyle: { color: color(tone), type: 'dashed', width: 1, opacity: 0.8 } })),
      };
    }
    if (first && i === 0 && list(o.areas).length) {
      item.markArea = { silent: true, itemStyle: { color: alpha(T.bad, 0.1) }, label: { show: false }, data: o.areas.map(([a, b, name]) => [{ xAxis: a, name: clean(name) }, { xAxis: b }]) };
    }
    meta.push({ s, cmp: false });
    series.push(item);
  }
  const fmtOf = (s) => panes[paneOf(s)].fmt;
  const valueOf = (p, s) => {
    const raw = num(p?.value?.[1]);
    return raw === null ? null : s.neg ? -raw : raw;
  };
  const option = {
    animation: false,
    grid: panes.map((_, i) => ({ left: 8, right: labels ? 64 : 14, top: `${head + i * (h + gap)}%`, height: `${h}%`, containLabel: true })),
    legend: legendOn ? { top: 0, right: 0, type: 'scroll', icon: 'roundRect', itemWidth: 12, itemHeight: 3, textStyle: { color: T['text-2'], fontSize: 12 }, data: shown.map((s) => s.name) } : undefined,
    xAxis: panes.map((_, i) => timeAxis(o.startMs, o.endMs, { gridIndex: i, axisLabel: { show: i === n - 1 } })),
    yAxis: panes.map((p, i) =>
      valueAxis({
        gridIndex: i,
        min: p.min ?? (neg(i) ? undefined : 0),
        max: p.max,
        splitNumber: n > 1 ? 2 : 3,
        // 센 수(fmt 가 int 이거나 int 표시) — 눈금을 정수로(0.5 칸이 '1, 1, 0' 처럼 같은 글자로 겹치지 않게)
        minInterval: p.int || p.fmt === int ? 1 : undefined,
        name: p.title ? clean(p.title) : undefined,
        nameLocation: 'end',
        nameGap: 8,
        nameTextStyle: { align: 'left', color: T['text-2'], fontSize: 12, fontWeight: 600 },
        axisLabel: { formatter: (v) => (p.axisFmt ?? p.fmt)(neg(i) ? Math.abs(v) : v) },
      }),
    ),
    axisPointer: n > 1 ? { link: [{ xAxisIndex: 'all' }] } : undefined,
    dataZoom:
      o.zoom === 'card'
        ? [{ type: 'inside', id: 'zoom', xAxisIndex: axes, zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }]
        : detail
          ? [{ type: 'inside', id: 'zoom', xAxisIndex: axes }, { ...SLIDER(), xAxisIndex: axes }]
          : undefined,
    toolbox: detail ? { show: false, feature: { dataZoom: { xAxisIndex: axes, yAxisIndex: 'none' } } } : undefined,
    tooltip: axisTip((ps) => {
      const arr = list([ps].flat());
      const t = num(arr[0]?.value?.[0]) ?? num(arr[0]?.axisValue);
      const rows = [];
      for (const p of arr) {
        const m = meta[p.seriesIndex];
        if (!m || m.cmp) continue;
        const v = valueOf(p, m.s);
        rows.push([m.s.mark, m.s.name, fmtOf(m.s)(v)]);
        const q = arr.find((x) => meta[x.seriesIndex]?.cmp && meta[x.seriesIndex].s === m.s);
        if (q) {
          const cv = valueOf(q, m.s);
          const d = change(v, cv);
          rows.push(['cmp', '이전', d.pct === null ? fmtOf(m.s)(cv) : `${fmtOf(m.s)(cv)} · ${signed(d.pct)}%`]);
        }
      }
      return tip(t === null ? '' : o.daily ? md(t) : when(t), rows);
    }),
    series,
  };
  const ends = series.flatMap((x, i) => (x.endLabel ? [i] : []));
  LAYOUT.set(option, { titles: panes.map((p) => Boolean(p.title)), legend: Boolean(legendOn), detail, right: labels ? 64 : 14, labels: labels ? ends.map((i) => series[i].name) : null, ends, count: series.length });
  return option;
}

// 상태 띠 한 칸 — 정상은 얇고 옅게, 주의·끊김은 칸 높이 가득, 자료 없음은 점선 테두리 + 빗금(45°)
const LANE_H = Object.freeze({ bad: 0.64, warn: 0.5, none: 0.5, ok: 0.2, track: 0.12 });
function hatch(r, step = 6) {
  const out = [];
  for (let k = step; k < r.width + r.height; k += step) {
    let x1 = r.x + k - r.height;
    let y1 = r.y + r.height;
    let x2 = r.x + k;
    let y2 = r.y;
    if (x1 < r.x) {
      y1 -= r.x - x1;
      x1 = r.x;
    }
    if (x2 > r.x + r.width) {
      y2 += x2 - (r.x + r.width);
      x2 = r.x + r.width;
    }
    if (x2 > x1) out.push({ type: 'line', silent: true, shape: { x1, y1, x2, y2 }, style: { stroke: T.none, lineWidth: 1, opacity: 0.55 } });
  }
  return out;
}
function laneItem(segs) {
  return (params, api) => {
    const seg = segs[params.dataIndex];
    const a = api.coord([api.value(1), api.value(0)]);
    const b = api.coord([api.value(2), api.value(0)]);
    const height = api.size([0, 1])[1] * (own(LANE_H, seg.tone) ?? 0.2);
    const box = params.coordSys;
    const r = lib().graphic.clipRectByRect({ x: a[0], y: a[1] - height / 2, width: Math.max(2, b[0] - a[0]), height }, { x: box.x, y: box.y, width: box.width, height: box.height });
    if (!r) return null;
    if (seg.tone === 'none') {
      return { type: 'group', children: [{ type: 'rect', shape: r, style: { fill: 'transparent', stroke: T.none, lineWidth: 1, lineDash: [2, 2] } }, ...hatch(r)] };
    }
    const fill = seg.tone === 'track' ? T.grid : color(seg.tone);
    return { type: 'rect', transition: [], shape: { ...r, r: 2 }, style: { fill, opacity: seg.tone === 'ok' ? 0.45 : 1 } };
  };
}
// 상태 띠 — rows = 왼쪽 이름, right = 오른쪽 글(마지막 성공·성공률), segs = [{ row, from, to, tone, head, tip }]
export function lanesOption(o) {
  const right = list(o.right).length ? [catAxis(o.right, { inverse: true, position: 'right', axisLabel: { color: T['text-3'], fontSize: 11 } })] : [];
  return {
    animation: false,
    grid: { left: 8, right: right.length ? 8 : 16, top: 6, bottom: 4, containLabel: true },
    xAxis: timeAxis(o.startMs, o.endMs),
    yAxis: [catAxis(o.rows, { inverse: true, axisLabel: { color: T['text-1'], fontSize: 12 } }), ...right],
    dataZoom: o.zoom ? [{ type: 'inside', id: 'zoom', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }] : undefined,
    tooltip: itemTip((p) => tip(o.segs[p.dataIndex]?.head, o.segs[p.dataIndex]?.tip ?? [])),
    series: [{ type: 'custom', encode: { x: [1, 2], y: 0 }, data: o.segs.map((s) => [s.row, s.from, s.to]), renderItem: laneItem(o.segs) }],
  };
}

// 사건 띠(경보 7일 — 066 §3.3) — 줄마다 ALARM 구간(빨강 칸 가득)과 알림 점. dots = [{ row, at, tone, head, tip }]
export function eventsOption(o) {
  const base = lanesOption(o);
  base.series.push({
    type: 'scatter',
    data: o.dots.map((d) => ({ value: [d.at, d.row], itemStyle: { color: color(d.tone), borderColor: T.surface, borderWidth: 2 } })),
    symbolSize: 10,
    z: 4,
  });
  base.tooltip = itemTip((p) => {
    const it = p.seriesIndex === 0 ? o.segs[p.dataIndex] : o.dots[p.dataIndex];
    return it ? tip(it.head, it.tip ?? []) : '';
  });
  return base;
}

// 요일×시간 열지도 — 한 색 진하기(--seq-1 낮음 → --seq-5 높음, 0 은 격자색). cells = [[x, y, 값]]
export function heatOption(o) {
  const max = Math.max(0, ...o.cells.map((c) => num(c[2]) ?? 0));
  const step = max / 5;
  const pieces = [{ lte: 0, color: T.grid }, ...[1, 2, 3, 4].map((i) => ({ gt: (i - 1) * step, lte: i * step, color: T[`seq-${i}`] })), { gt: 4 * step, color: T['seq-5'] }];
  return {
    animation: false,
    grid: { left: 8, right: 8, top: 6, bottom: 4, containLabel: true },
    xAxis: catAxis(o.xs, { splitArea: { show: false }, axisLabel: { interval: o.interval ?? 2 } }),
    yAxis: catAxis(o.ys, { inverse: true, axisLabel: { color: T['text-2'] } }),
    visualMap: { show: false, type: 'piecewise', dimension: 2, seriesIndex: 0, pieces },
    tooltip: itemTip((p) => tip(o.head(p.value?.[0], p.value?.[1]), [['acc', o.name, o.fmt(p.value?.[2])]])),
    series: [{ type: 'heatmap', data: o.cells, itemStyle: { borderColor: T.surface, borderWidth: 2, borderRadius: 3 }, emphasis: { itemStyle: { borderColor: T['text-2'], borderWidth: 1 } } }],
  };
}

// 흐름(062 sankey) — 마디 색은 들어온 길(차트 8색), 페이지 마디는 흐린 회색. 고리 색은 출발 마디, 올리면 앞뒤 고리 강조
export function sankeyOption(o) {
  const label = new Map(o.nodes.map((n) => [n.name, n.label]));
  const head = (text, left) => ({ text, left, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: T['text-3'] } });
  return {
    animation: false,
    title: [head(o.heads[0], 8), head(o.heads[1], 'center'), head(o.heads[2], '72%')],
    tooltip: itemTip((p) => (p.dataType === 'edge' ? o.tipLink(label.get(p.data?.source) ?? '', label.get(p.data?.target) ?? '', p.data) : o.tipNode(label.get(p.name) ?? '', p.value))),
    series: [
      {
        type: 'sankey',
        left: 8,
        right: 150,
        top: 26,
        bottom: 6,
        nodeWidth: 10,
        nodeGap: 10,
        nodeAlign: 'justify',
        layoutIterations: 64,
        draggable: false,
        emphasis: { focus: 'adjacency' },
        data: o.nodes.map((n) => ({ name: n.name, depth: n.depth, itemStyle: { color: color(n.mark), borderWidth: 0 } })),
        links: o.links,
        lineStyle: { color: 'source', opacity: 0.3, curveness: 0.5 },
        label: { color: T['text-1'], fontSize: 12, formatter: (p) => label.get(p.name) ?? '' },
      },
    ],
  };
}

// 세로 막대(범주) — 막대 위 값, 한 계열이라 강조색
export function colsOption(o) {
  return {
    animation: false,
    grid: { left: 8, right: 8, top: 18, bottom: 4, containLabel: true },
    xAxis: catAxis(o.cats, { axisLabel: { color: T['text-2'], interval: 0 } }),
    yAxis: valueAxis({ minInterval: 1 }),
    tooltip: itemTip((p) => tip(o.cats[p.dataIndex], [[o.mark ?? 'acc', o.name, o.fmt(p.value)]])),
    series: [
      {
        type: 'bar',
        data: o.values,
        barMaxWidth: 28,
        itemStyle: { color: color(o.mark ?? 'acc'), borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', color: T['text-2'], fontSize: 11, formatter: (p) => (p.value ? o.fmt(p.value) : '') },
      },
    ],
  };
}

// 예산 게이지 — 남기는 게이지 하나(066 §3.1). 실제(상태색 호 + 바늘)·예측(흐린 짧은 바늘), 한도 넘는 칸은 붉게, 끝은 120%
export function budgetOption(b, money) {
  const r = (v) => Math.min(120, Math.max(0, ((num(v) ?? 0) / b.limit) * 100));
  const tone = b.actual >= b.limit ? 'bad' : num(b.forecast) !== null && b.forecast >= b.limit ? 'warn' : 'ok';
  const arc = {
    type: 'gauge',
    center: ['50%', '82%'],
    radius: '130%',
    startAngle: 180,
    endAngle: 0,
    min: 0,
    max: 120,
    axisLine: { lineStyle: { width: 10, color: [[100 / 120, T.grid], [1, alpha(T.bad, 0.35)]] } },
    axisTick: { show: false },
    splitLine: { show: false },
    axisLabel: { show: false },
    anchor: { show: true, size: 6, itemStyle: { color: T['text-1'] } },
    title: { show: false },
    detail: { show: false },
  };
  const needle = (length, width, c) => ({ show: true, length, width, itemStyle: { color: c } });
  const series = [{ ...arc, progress: { show: true, width: 10, itemStyle: { color: color(tone) } }, pointer: needle('70%', 4, color(tone)), data: [{ value: r(b.actual), name: '실제' }] }];
  if (num(b.forecast) !== null) series.push({ ...arc, axisLine: { show: false }, progress: { show: false }, pointer: needle('50%', 3, T['text-3']), data: [{ value: r(b.forecast), name: '예측' }] });
  return {
    animation: false,
    tooltip: itemTip(() => tip('이번 달 예산', [[tone, '실제', money(b.actual)], ['cmp', '예측', money(b.forecast)], ['', '한도', money(b.limit)]])),
    series,
  };
}
