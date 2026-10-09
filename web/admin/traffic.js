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
  return (100 * (req('scanner') + req('tool'))) / total;
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

const accessPart = (app) => partOf(app.current('access'));
// 접속 요약이 ok 일 때 하위 부분(visitors·geo)의 상태 — 그 밖은 바깥 부분의 상태
const subPart = (part, key) => (usable(part) ? partOf({ body: part }, key) : part);

// 창 단추 — 셋 다 늘 열려 있고, 응답의 windows 에 없는 창만 끈다(응답이 없으면 끄지 않는다 — 사람 결정 2026-10-09)
function drawWindow(app) {
  const body = app.entry('access')?.body;
  const open = Array.isArray(body?.windows) ? body.windows : null;
  for (const button of document.querySelectorAll('[data-window]')) {
    button.disabled = open !== null && !open.includes(button.dataset.window);
    button.setAttribute('aria-pressed', String(button.dataset.window === app.picked.access));
  }
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

// --- 어디서 — 들어온 길·출처·나라·망 종류·utm ---------------------------------------------------------

const pairItems = (rows, name, color) => rows.map(([key, c, s]) => ({ name: name(key), value: s, color: color(key), tip: pair(c, s) }));
const lineItems = (rows, name, denom) =>
  rows.map(([key, v]) => ({ name: name(key), value: v, tip: [['c0', '사람 모양 페이지', int(v)], ['dim', '비율', denom > 0 ? pct((v / denom) * 100, 0) : '–']] }));
// 그림 하나 — 값을 못 그리면 칸에 상태 글. part 는 그 그림이 기대는 부분(바깥 또는 하위)
function chartCard(app, id, part, make) {
  once(id, [app.current('access'), part], () => (usable(part) ? make(part) : blank(id, stateWord(part))));
}

function drawWhere(app) {
  const part = accessPart(app);
  const visitors = subPart(part, 'visitors');
  const geo = subPart(part, 'geo');
  tag('t-channels', visitors);
  tag('t-countries', geo);
  tag('t-networks', geo);
  chartCard(app, 'c-channels', visitors, (v) => donut('c-channels', pairItems(rows3(v.channels), channelName, keyColor(CHANNEL_NAME))));
  chartCard(app, 'c-referrers', part, (a) => bars('c-referrers', lineItems(rows2(a.referrers, 10), clean, n0(a.totals?.humanPages)), { fmt: int }));
  chartCard(app, 'c-countries', geo, (g) => bars('c-countries', pairItems(rows3(g.countries), countryLabel, () => COLOR.accent), { fmt: int, labelWidth: 150 }));
  chartCard(app, 'c-networks', geo, (g) => {
    const rows = rows3(g.networks);
    // 국내 통신사 행이 없는 창의 telecom 은 국내·해외를 합친 값일 수 있다(039 — KR 이 (기타)로 묶임)
    const hasKr = rows.some(([key]) => key === 'telecom_kr');
    const name = (key) => (key === 'telecom' && !hasKr ? '통신사' : nameIn(NET_NAME)(key));
    return donut('c-networks', pairItems(rows, name, keyColor(NET_NAME)));
  });
  const utm = usable(part) ? rows2(part.utmSources, 10) : [];
  const card = $('utm-card');
  const hidden = !utm.length;
  if (card.hidden !== hidden) {
    card.hidden = hidden;
    if (!hidden) refit('c-utm');
  }
  if (!hidden) chartCard(app, 'c-utm', part, (a) => bars('c-utm', lineItems(utm, clean, n0(a.totals?.humanPages)), { fmt: int }));
}

// --- 들어오고 나간 곳 (062 — entries·exits·depthPages) ----------------------------------------------------

const DEPTH = [['1', '1쪽'], ['2', '2쪽'], ['3-5', '3~5쪽'], ['6+', '6쪽 이상']];
function endsOption(v) {
  const entries = rows3(v.entries, 12);
  const exits = rows3(v.exits, 12);
  const keys = [...new Set([...entries, ...exits].map(([key]) => key))];
  if (!keys.length) return null;
  const at = (rows) => new Map(rows.map(([key, c, s]) => [key, [c, s]]));
  const [inMap, outMap] = [at(entries), at(exits)];
  const order = [...keys].reverse(); // 범주 축은 아래에서 위로 — 받은 순서(큰 순)가 위에 오게
  const bar = (name, map, color) => ({ type: 'bar', name, data: order.map((k) => map.get(k)?.[1] ?? 0), barMaxWidth: 12, itemStyle: { color, borderRadius: [0, 4, 4, 0] } });
  return base({
    grid: { left: 8, right: 24, top: 28, bottom: 4, containLabel: true },
    legend: legend({ data: ['첫 페이지', '마지막 페이지'] }),
    xAxis: valueAxis({ minInterval: 1 }),
    yAxis: categoryAxis(order.map(pageName), { axisLine: { show: false }, axisLabel: { color: COLOR.text, fontSize: 12, width: 130, overflow: 'truncate' } }),
    tooltip: axisTip((ps) => {
      const key = order[ps[0]?.dataIndex];
      const [ic, is] = inMap.get(key) ?? [0, 0];
      const [oc, os] = outMap.get(key) ?? [0, 0];
      return tip(pageName(key), [['c0', '첫 페이지(확인~모양)', `${int(ic)}~${int(is)}`], ['c1', '마지막 페이지(확인~모양)', `${int(oc)}~${int(os)}`]]);
    }),
    series: [bar('첫 페이지', inMap, COLOR.accent), bar('마지막 페이지', outMap, COLOR.sub)],
  });
}

function drawEnds(app) {
  const visitors = subPart(accessPart(app), 'visitors');
  const missing = usable(visitors) && !Array.isArray(visitors.entries);
  for (const id of ['t-ends', 't-depth']) {
    if (missing) tagText(id, ['dim', '연결 안 됨', '접속 흐름 피드(062) 없음']);
    else tag(id, visitors);
  }
  chartCard(app, 'c-ends', visitors, (v) => {
    const option = missing ? null : endsOption(v);
    return option ? draw('c-ends', option) : blank('c-ends', missing ? '연결 안 됨' : '자료 없음');
  });
  chartCard(app, 'c-depth', visitors, (v) => {
    const depth = isObj(v.depthPages) ? v.depthPages : null;
    if (!depth) return blank('c-depth', missing ? '연결 안 됨' : '자료 없음');
    const cells = DEPTH.map(([key, label]) => [label, n0(depth[key]?.[0]), n0(depth[key]?.[1])]);
    if (!cells.some((x) => x[2] > 0)) return blank('c-depth', '자료 없음');
    return draw('c-depth', base({
      grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
      xAxis: categoryAxis(cells.map((x) => x[0])),
      yAxis: valueAxis({ minInterval: 1 }),
      tooltip: itemTip((p) => tip(cells[p.dataIndex]?.[0], pair(cells[p.dataIndex]?.[1], cells[p.dataIndex]?.[2]))),
      series: [{ type: 'bar', data: cells.map((x) => x[2]), barMaxWidth: 36, itemStyle: { color: COLOR.accent, borderRadius: [4, 4, 0, 0] } }],
    }));
  });
}

// --- 누가 — 요청 종류 여덟(100% 띠)·기기·OS·브라우저 ----------------------------------------------------

function classesOption(a) {
  const classes = isObj(a.classes) ? a.classes : {};
  const keys = Object.keys(CLASS_NAME);
  const totals = ['requests', 'pages'].map((k) => keys.reduce((sum, key) => sum + n0(classes[key]?.[k]), 0));
  if (!totals[0]) return null;
  const share = (key, i) => (totals[i] ? (n0(classes[key]?.[['requests', 'pages'][i]]) / totals[i]) * 100 : 0);
  return base({
    grid: { left: 8, right: 8, top: 4, bottom: 44, containLabel: true },
    legend: { bottom: 0, left: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 8, textStyle: { color: COLOR.muted, fontSize: 12 } },
    xAxis: valueAxis({ show: false, max: 100 }),
    yAxis: categoryAxis(['요청', '페이지'], { inverse: true, axisLine: { show: false }, axisLabel: { color: COLOR.text, fontSize: 12 } }),
    tooltip: itemTip((p) => {
      const key = keys[p.seriesIndex];
      return tip(CLASS_NAME[key], [['c0', '요청', int(classes[key]?.requests)], ['c1', '페이지', int(classes[key]?.pages)], ['dim', '비율', pct(p.value, 1)]]);
    }),
    series: keys.map((key, i) => ({
      type: 'bar',
      name: CLASS_NAME[key],
      stack: 'all',
      barWidth: 18,
      data: [share(key, 0), share(key, 1)],
      itemStyle: { color: SERIES[i], borderColor: COLOR.card, borderWidth: 1 },
      label: { show: true, position: 'inside', color: '#121420', fontSize: 11, formatter: (p) => (p.value >= 6 ? `${Math.round(p.value)}%` : '') },
    })),
  });
}

// 기기·OS·브라우저 — 방문자(브라우저 모양)로, 방문자 값이 없으면 기기·브라우저는 사람 모양 페이지 줄로(OS 는 없다)
const WHO = [['devices', 'c-devices', 't-devices', DEVICE_NAME], ['os', 'c-os', 't-os', OS_NAME], ['browsers', 'c-browsers', 't-browsers', BROWSER_NAME]];
function drawWho(app) {
  const part = accessPart(app);
  chartCard(app, 'c-classes', part, (a) => {
    const option = classesOption(a);
    return option ? draw('c-classes', option) : blank('c-classes', '자료 없음');
  });
  const visitors = subPart(part, 'visitors');
  for (const [key, id, tagId, table] of WHO) {
    const lines = usable(part) && !usable(visitors) && key !== 'os' ? rows2(part[key]) : [];
    tag(tagId, lines.length ? null : visitors);
    if (lines.length) {
      once(id, [app.current('access'), 'lines'], () => donut(id, lines.map(([k, v]) => ({ name: nameIn(table)(k), value: v, color: keyColor(table)(k), tip: [['c0', '사람 모양 페이지', int(v)]] }))));
    } else chartCard(app, id, visitors, (v) => donut(id, pairItems(rows3(v[key]), nameIn(table), keyColor(table))));
  }
}

// --- 무엇을 — 경로·대시보드 탭 ---------------------------------------------------------------------------

function drawWhat(app) {
  const part = accessPart(app);
  chartCard(app, 'c-paths', part, (a) => bars('c-paths', lineItems(rows2(a.paths, 10), clean, n0(a.totals?.humanPages)), { fmt: int, labelWidth: 180 }));
  chartCard(app, 'c-tabs', part, (a) => {
    const rows = rows2(a.tabs);
    return bars('c-tabs', lineItems(rows, nameIn(TAB_NAME), rows.reduce((sum, r) => sum + r[1], 0)), { fmt: int });
  });
}

// --- 문제 — 응답 상태·WS 연결 시간·최근 5xx ------------------------------------------------------------------

const STATUS = [['2xx', '2xx', COLOR.ok], ['3xx', '3xx', COLOR.sub], ['4xx', '4xx', COLOR.warn], ['5xx', '5xx', COLOR.bad], ['ws5xx', 'WS 재접속 실패', SERIES[7]]];
const WS_BUCKETS = [['lt10s', '10초 미만'], ['lt1m', '1분 미만'], ['lt10m', '10분 미만'], ['lt1h', '1시간 미만'], ['ge1h', '1시간 이상']];
function drawProblems(app) {
  const part = accessPart(app);
  chartCard(app, 'c-status', part, (a) => {
    const total = STATUS.reduce((sum, [key]) => sum + n0(a.status?.[key]), 0);
    return donut('c-status', STATUS.map(([key, name, color]) => ({ name, value: n0(a.status?.[key]), color, tip: [['dim', '요청', int(a.status?.[key])], ['dim', '비율', total ? pct((n0(a.status?.[key]) / total) * 100, 1) : '–']] })));
  });
  chartCard(app, 'c-durations', part, (a) => {
    const durations = isObj(a.ws?.durations) ? a.ws.durations : {};
    if (!WS_BUCKETS.some(([key]) => n0(durations[key]) > 0)) return blank('c-durations', '자료 없음');
    return draw('c-durations', base({
      grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
      xAxis: categoryAxis(WS_BUCKETS.map(([, label]) => label)),
      yAxis: valueAxis({ minInterval: 1 }),
      tooltip: itemTip((p) => tip(WS_BUCKETS[p.dataIndex]?.[1], [['c0', '끝난 연결', int(p.value)]])),
      series: [{ type: 'bar', data: WS_BUCKETS.map(([key]) => n0(durations[key])), barMaxWidth: 36, itemStyle: { color: COLOR.accent, borderRadius: [4, 4, 0, 0] } }],
    }));
  });
  once('r5xx', [app.current('access')], () => {
    const recent = usable(part) ? list(part.recent5xx).filter(isObj).slice(0, 10) : [];
    const rows = recent.map((r) => {
      const tr = el('tr');
      const path = clean(r.path);
      tr.append(el('td', null, num(r.ts) === null ? '–' : when(r.ts * 1000)), el('td', 'mono', path.length > 80 ? `${path.slice(0, 80)}…` : path), el('td', 'num num-bad', r.status));
      return tr;
    });
    $('r5xx').replaceChildren(...(rows.length ? rows : [el('tr', null, usable(part) ? '없음' : stateWord(part))]));
  });
}

// --- Clarity (040 — 세션·평균 스크롤 깊이·활성 시간 + 불만 신호 여섯) ----------------------------------------------

const SIGNALS = [['deadClick', '죽은 클릭'], ['rageClick', '분노 클릭'], ['excessiveScroll', '과한 스크롤'], ['quickback', '빠른 뒤로'], ['scriptError', '스크립트 오류'], ['errorClick', '오류 클릭']];
function drawClarity(app) {
  const entry = app.entry('clarity');
  const part = partOf(entry);
  tag('t-clarity', part);
  const ok = usable(part, 'numOfDays');
  const summary = ok && isObj(part.summary) ? part.summary : {};
  put('v-sessions', ok ? int(part.traffic?.sessions) : '–');
  put('v-scroll', ok ? pct(num(summary.scrollDepth), 0) : '–');
  put('v-active', ok && num(summary.activeSec) !== null ? `${int(summary.activeSec)}초` : '–');
  once('c-signals', [entry], () => {
    if (!ok) return blank('c-signals', stateWord(part));
    const signals = isObj(summary.signals) ? summary.signals : {};
    const items = SIGNALS.map(([key, name]) => {
      const s = isObj(signals[key]) ? signals[key] : {};
      return { name, value: num(s.sessionPct), tip: [['c0', '세션 비율', pct(num(s.sessionPct))], ['dim', '세션', int(s.sessions)], ['dim', '횟수', int(s.count)]] };
    });
    return bars('c-signals', items, { fmt: (v) => pct(v), color: COLOR.warn });
  });
}

const app = start({
  home: '/traffic.html',
  slow: ['access', 'clarity'],
  picks: { access: '24h' },
  cards: [
    ['access', drawWindow],
    ['kpis', drawKpis],
    ['flows', drawFlows],
    ['trend', drawTrend],
    ['heat', drawHeat],
    ['where', drawWhere],
    ['ends', drawEnds],
    ['who', drawWho],
    ['what', drawWhat],
    ['problems', drawProblems],
    ['clarity', drawClarity],
  ],
});
for (const button of document.querySelectorAll('[data-window]')) button.addEventListener('click', () => app.pick('access', button.dataset.window));
