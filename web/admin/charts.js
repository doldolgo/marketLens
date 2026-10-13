// ECharts 5.5.1(vendor 사본 — 전역 echarts)를 감싼다(스펙 066 §3.1·조사 §6). 칸 id 마다 인스턴스 하나, 테마 'kt' 는 admin.css 의
// CSS 변수에서 읽어 한 번 등록한다(CSS 가 하나의 원본). 툴팁 HTML 은 모두 tip() 하나를 지난다 — 피드 글자는 echarts.format.encodeHTML
// (064 §3.1 보안). 축·범례·이름표 글자는 캔버스에 그려져 HTML 이 되지 않는다. 라이브러리를 못 불러오면 그림 칸에 한 줄.
import { clean, el, hm, list, md, num, own, short } from './common.js';
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

const live = new Map(); // 칸 id → { chart, box, shape, ro }

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
      held.ro = new ResizeObserver(() => chart.resize());
      held.ro.observe(box);
    }
    live.set(id, held);
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
    resizing = setTimeout(() => live.forEach(({ chart }) => chart.resize()), 100);
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
