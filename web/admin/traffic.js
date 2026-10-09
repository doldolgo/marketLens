// 트래픽 페이지(스펙 064 §3.4) — 누가·어디서·언제·무엇을. 접속 요약(038·039·062)·Clarity(040)·status(029).
// 창 단추(24시간·7일·30일)는 JS 변수 — 바꾸면 /svc/api/admin/access?window= 하나만 곧바로(042 규칙 — 떠 있으면 끝난 뒤
// 한 번, 서버가 다른 창으로 답하면 그 창으로 되돌림). 고른 창으로 요청한 응답만 그린다.
import {
  $,
  clean,
  el,
  int,
  isObj,
  list,
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
  tagText,
  md,
  usable,
} from './common.js';
import { COLOR, SERIES, axisTip, bars, base, blank, categoryAxis, donut, draw, itemTip, legend, refit, timeAxis, tip, valueAxis, when } from './charts.js';

// --- 이름표 (038·039·052·062 의 키 → 화면 이름 — 표에 없는 값은 원래 글자) --------------------------------

const CHANNEL_NAME = { direct: '직접', search: '검색', inapp: '앱 안 브라우저', social: '소셜·커뮤니티', ai: 'AI 답변', referral: '다른 사이트 링크', campaign: '캠페인(utm)', internal: '사이트 안 이동', unknown: '첫 페이지 기록 없음' };
const PAGE_NAME = { landing: '랜딩', 'app-spread': '대시보드 스프레드', 'app-history': '대시보드 기록', 'app-gap': '대시보드 갭', 'app-pp': '대시보드 선선갭', 'app-health': '대시보드 수집 상태', 'app-flow': '대시보드 입출금 레이더', privacy: '처리방침', 'kimp-chart': '김프 차트', 'kimp-history': '김프 기록' };
const NET_NAME = { telecom_kr: '국내 통신사', telecom: '해외 통신사', cloud: '클라우드', other: '기타', unknown: '모름' };
const CLASS_NAME = { browser: '사람 브라우저 모양', search: '검색엔진', ai: 'AI 수집기', preview: '링크 미리보기', tool: '자동화 도구', scanner: '스캐너', operator: '운영자 흔적', unknown: '이름 없음' };
const DEVICE_NAME = { mobile: '휴대폰', tablet: '태블릿', desktop: '데스크톱' };
const OS_NAME = { ios: 'iOS', android: 'Android', windows: 'Windows', macos: 'macOS', linux: 'Linux', chromeos: 'ChromeOS', other: '그 밖' };
const BROWSER_NAME = { chrome: 'Chrome', safari: 'Safari', samsung: '삼성 인터넷', whale: '웨일', edge: 'Edge', firefox: 'Firefox', opera: 'Opera', inapp: '앱 안 브라우저', other: '그 밖' };
const TAB_NAME = { spread: '실시간 스프레드', history: '기록/통계', gap: '선물–현물 갭', pp: '선선갭', health: '수집 상태', flow: '입출금 레이더' };
const nameIn = (table) => (key) => own(table, key) ?? clean(key);
export const channelName = nameIn(CHANNEL_NAME);
export const pageName = nameIn(PAGE_NAME);

// 색은 키를 따른다(순위가 아니라) — 차트 색 순서를 키 표의 순서대로, 표 밖·(기타)는 흐린 회색
const keyColor = (table) => (key) => {
  const at = Object.keys(table).indexOf(key);
  return at >= 0 && at < SERIES.length ? SERIES[at] : COLOR.muted;
};

// [[이름, 확인, 모양]]·[[이름, 수]] — 모양이 틀린 행은 버리고, 피드 상한을 믿지 않고 앞에서 자른다(036)
const rows3 = (v, max = 21) => list(v).filter((r) => Array.isArray(r) && typeof r[0] === 'string').slice(0, max).map((r) => [r[0], n0(r[1]), n0(r[2])]);
const rows2 = (v, max = 21) => list(v).filter((r) => Array.isArray(r) && typeof r[0] === 'string').slice(0, max).map((r) => [r[0], n0(r[1])]);
const pair = (c, s) => [['c0', '확인', int(c)], ['c1', '브라우저 모양', int(s)]];

// --- 계산 (§4 node 시험 대상) ---------------------------------------------------------------------

// 들어온 길 → 첫 페이지 → 마지막 페이지(062 flows) → sankey 마디·고리. 마디 이름에 층 머리를 붙여 같은 페이지가
// 들어온 곳이자 나간 곳이어도 두 마디다. 고리 굵기 = 브라우저 모양 짝 수(value), 확인 수는 c. (기타) 줄도 세 층의 마디로
export function flowGraph(flows) {
  const nodes = new Map();
  const links = new Map();
  const node = (depth, key, label) => {
    const name = `${depth}:${key}`;
    if (!nodes.has(name)) nodes.set(name, { name, depth, label });
    return name;
  };
  const link = (source, target, s, c) => {
    const id = `${source}>${target}`;
    const held = links.get(id) ?? { source, target, value: 0, c: 0 };
    held.value += s;
    held.c += c;
    links.set(id, held);
  };
  for (const row of list(flows).slice(0, 41)) {
    if (!Array.isArray(row) || !row.slice(0, 3).every((v) => typeof v === 'string') || !(n0(row[4]) > 0)) continue;
    const [channel, entry, exit] = row;
    const [c, s] = [n0(row[3]), n0(row[4])];
    const a = node(0, channel, channelName(channel));
    const b = node(1, entry, pageName(entry));
    link(a, b, s, c);
    link(b, node(2, exit, pageName(exit)), s, c);
  }
  return { nodes: [...nodes.values()], links: [...links.values()] };
}

// 요일×시간 — hourly.jsViews 를 이 브라우저 시간대의 (월~일, 0~23시) 칸에 더한다(042 그대로)
export const WEEKDAY = ['월', '화', '수', '목', '금', '토', '일'];
export function heatGrid(hourly) {
  const cells = WEEKDAY.map(() => new Array(24).fill(0));
  for (const h of list(hourly).filter((x) => isObj(x) && num(x.ts) !== null)) {
    const d = new Date(h.ts * 1000);
    cells[(d.getDay() + 6) % 7][d.getHours()] += n0(h.jsViews);
  }
  return { cells, max: Math.max(0, ...cells.flat()) };
}

// 봇 비율 — (스캐너 + 자동화 도구 요청) ÷ 전체 요청 × 100. 요청이 없으면 null
export function botShare(a) {
  const total = n0(a?.totals?.requests);
  if (!total) return null;
  const req = (k) => n0(a.classes?.[k]?.requests);
  return ((req('scanner') + req('tool')) / total) * 100;
}

// 나라 — 국기 그림 문자 + 브라우저의 한국어 지역 이름. 못 푸는 값(ZZ·(기타))은 글자 그대로
const REGION = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['ko'], { type: 'region' }) : null;
export function countryLabel(code) {
  if (typeof code !== 'string' || !/^[A-Z]{2}$/.test(code) || code === 'ZZ') return clean(code);
  let name = code;
  try {
    name = REGION?.of(code) ?? code;
  } catch {
    name = code;
  }
  return `${String.fromCodePoint(...[...code].map((ch) => 0x1f1e6 + ch.charCodeAt(0) - 65))} ${name}`;
}

// --- 창·큰 수 -----------------------------------------------------------------------------------

const KST = 32_400_000;
const kstMd = (ms) => {
  const d = new Date(ms + KST);
  return `${d.getUTCMonth() + 1}-${String(d.getUTCDate()).padStart(2, '0')}`;
};
const WINDOWS = ['24h', '7d', '30d'];
const accessPart = (app) => partOf(app.current('access'));
// 접속 요약이 ok 일 때 하위 부분(visitors·geo)의 상태 — 그 밖은 바깥 부분의 상태
const subPart = (part, key) => (usable(part) ? partOf({ body: part }, key) : part);

function drawWindow(app) {
  const body = app.entry('access')?.body;
  const open = Array.isArray(body?.windows) ? body.windows : ['24h'];
  for (const button of document.querySelectorAll('[data-window]')) {
    button.disabled = !open.includes(button.dataset.window);
    button.setAttribute('aria-pressed', String(button.dataset.window === app.picked.access));
  }
  const gate = num(body?.gateAt);
  put('window-note', gate !== null && !open.includes('7d') ? `7·30일은 ${kstMd(gate)} 부터` : '');
  tag('t-access', accessPart(app));
}

function drawKpis(app) {
  const part = accessPart(app);
  const ok = usable(part);
  const visitors = subPart(part, 'visitors');
  tag('t-visitors', visitors);
  if (ok && usable(visitors)) $('v-visitors').replaceChildren(int(visitors.confirmed), el('small', null, ` ~ ${int(visitors.shaped)}`));
  else put('v-visitors', '–');
  put('v-pages', ok ? int(part.totals?.humanPages) : '–');
  const share = ok ? botShare(part) : null;
  put('v-bots', share === null ? '–' : pct(share, 0));
  const fives = ok ? n0(part.status?.['5xx']) : null;
  put('v-5xx', fives === null ? '–' : int(fives), fives ? 'big bad' : 'big');
  const status = app.entry('status');
  tag('t-now', plainOf(status));
  put('v-now', status?.body ? int(status.body.wsConnections) : '–');
}

// --- 흐름 (062 — sankey) ----------------------------------------------------------------------------

function drawFlows(app) {
  const access = app.current('access');
  const part = partOf(access);
  const visitors = subPart(part, 'visitors');
  // 062 배포 전이면 visitors 에 flows 가 없다 — 부분 상태 규칙대로 '연결 안 됨'
  const missing = usable(visitors) && !Array.isArray(visitors.flows);
  if (missing) tagText('t-flows', ['dim', '연결 안 됨', '접속 흐름 피드(062) 없음']);
  else tag('t-flows', visitors);
  once('c-flows', access, () => {
    if (!usable(visitors)) return blank('c-flows', stateWord(visitors));
    if (missing) return blank('c-flows', '연결 안 됨');
    const graph = flowGraph(visitors.flows);
    if (!graph.links.length) return blank('c-flows', '자료 없음');
    const label = new Map(graph.nodes.map((n) => [n.name, n.label]));
    const head = (text, left) => ({ text, left, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: COLOR.muted } });
    return draw('c-flows', base({
      title: [head('들어온 길', 8), head('첫 페이지', 'center'), head('마지막 페이지', '74%')],
      tooltip: itemTip((p) =>
        p.dataType === 'edge'
          ? tip(`${label.get(p.data?.source) ?? ''} → ${label.get(p.data?.target) ?? ''}`, pair(p.data?.c, p.data?.value))
          : tip(label.get(p.name) ?? '', [['c0', '브라우저 모양', int(p.value)]]),
      ),
      series: [{
        type: 'sankey',
        left: 8,
        right: 150,
        top: 28,
        bottom: 8,
        nodeWidth: 14,
        nodeGap: 12,
        nodeAlign: 'justify',
        layoutIterations: 64,
        draggable: false,
        emphasis: { focus: 'adjacency' },
        data: graph.nodes.map((n) => ({ name: n.name, depth: n.depth, itemStyle: { color: SERIES[n.depth], borderWidth: 0 } })),
        links: graph.links,
        lineStyle: { color: 'gradient', opacity: 0.3, curveness: 0.5 },
        label: { color: COLOR.text, fontSize: 12, formatter: (p) => label.get(p.name) ?? '' },
      }],
    }));
  });
}

// --- 언제 — 방문 추이·요일×시간 ---------------------------------------------------------------------

// 24시간 = 시간별 페이지(확인 = 스크립트가 돈 페이지, 모양만 = 그 밖 사람 모양 페이지), 7·30일 = 날별 방문자 + 다시 온 선
function trendOption(part) {
  const span = { startMs: n0(part.startTs) * 1000, endMs: n0(part.endTs) * 1000 };
  const visitors = isObj(part.visitors) && part.visitors.state === 'ok' ? part.visitors : null;
  const daily = part.window !== '24h' && visitors;
  const rows = daily
    ? list(visitors.days).filter((d) => isObj(d) && num(d.ts) !== null).map((d) => [d.ts * 1000 + 43_200_000, n0(d.confirmed), n0(d.shaped), n0(d.returning)])
    : list(part.hourly).filter((h) => isObj(h) && num(h.ts) !== null).map((h) => [h.ts * 1000 + 1_800_000, n0(h.jsViews), Math.max(n0(h.humanPages), n0(h.jsViews)), null]);
  if (!rows.some((r) => r[2] > 0)) return null;
  const stack = (name, data, color, top) => ({ type: 'bar', name, stack: 'v', data, barMaxWidth: 18, itemStyle: { color, borderRadius: top ? [4, 4, 0, 0] : 0 } });
  const names = daily ? ['확인', '모양만', '다시 온'] : ['스크립트가 돈 페이지', '그 밖 사람 모양 페이지'];
  const series = [stack(names[0], rows.map((r) => [r[0], r[1]]), COLOR.accent, false), stack(names[1], rows.map((r) => [r[0], r[2] - r[1]]), COLOR.sub, true)];
  if (daily) series.push({ type: 'line', name: names[2], data: rows.map((r) => [r[0], r[3]]), symbolSize: 6, lineStyle: { width: 2, color: COLOR.ok }, itemStyle: { color: COLOR.ok } });
  return base({
    grid: { left: 8, right: 12, top: 32, bottom: 4, containLabel: true },
    legend: legend({ data: names }),
    xAxis: timeAxis(span.startMs, span.endMs),
    yAxis: valueAxis({ minInterval: 1 }),
    tooltip: axisTip((ps) => {
      const r = rows[ps[0]?.dataIndex];
      if (!r) return '';
      const head = daily ? md(r[0]) : when(r[0] - 1_800_000);
      return tip(head, daily ? [...pair(r[1], r[2]), ['c2', '다시 온', int(r[3])]] : [['c0', '사람 모양 페이지', int(r[2])], ['c1', '스크립트가 돈 페이지', int(r[1])]]);
    }),
    series,
  });
}

function drawTrend(app) {
  const access = app.current('access');
  const part = partOf(access);
  tag('t-trend', part);
  once('c-trend', access, () => {
    if (!usable(part)) return blank('c-trend', stateWord(part));
    const option = trendOption(part);
    return option ? draw('c-trend', option) : blank('c-trend', '자료 없음');
  });
}

function drawHeat(app) {
  const access = app.current('access');
  const part = partOf(access);
  tag('t-heat', part);
  once('c-heat', access, () => {
    if (!usable(part)) return blank('c-heat', stateWord(part));
    const grid = heatGrid(part.hourly);
    if (!grid.max) return blank('c-heat', '자료 없음');
    const data = grid.cells.flatMap((row, d) => row.map((v, h) => [h, d, v]));
    return draw('c-heat', base({
      grid: { left: 8, right: 8, top: 8, bottom: 4, containLabel: true },
      xAxis: categoryAxis([...Array(24).keys()].map((h) => `${h}`), { splitArea: { show: false }, axisLabel: { color: COLOR.muted, fontSize: 11, interval: 2 } }),
      yAxis: categoryAxis(WEEKDAY, { inverse: true, axisLine: { show: false } }),
      visualMap: { show: false, min: 0, max: grid.max, inRange: { color: ['#232636', COLOR.accent] } },
      tooltip: itemTip((p) => tip(`${WEEKDAY[p.value?.[1]] ?? ''}요일 ${p.value?.[0]}시`, [['c0', '스크립트가 돈 페이지', int(p.value?.[2])]])),
      series: [{ type: 'heatmap', data, itemStyle: { borderColor: COLOR.card, borderWidth: 2, borderRadius: 3 } }],
    }));
  });
}
