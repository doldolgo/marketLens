// 트래픽 모델(스펙 066 §3.3 — 064 의 계산을 옮김): 흐름(062 sankey)·요일×시간·봇 비율·목록 원천(탭)·100% 띠 원천. DOM·echarts 없음.
import { clean, isObj, list, n0, num } from './common.js';
import {
  CLASS_NAME,
  STATUS_KEYS,
  browserMark,
  browserName,
  channelMark,
  channelName,
  className,
  classMark,
  countryLabel,
  deviceMark,
  deviceName,
  netMark,
  netName,
  osMark,
  osName,
  pageName,
  tabName,
} from './names.js';

// [[이름, 확인, 모양]]·[[이름, 수]] — 모양이 틀린 행은 버리고, 피드 상한을 믿지 않고 앞에서 자른다(036)
export const rows3 = (v, max = 41) => list(v).filter((r) => Array.isArray(r) && typeof r[0] === 'string').slice(0, max).map((r) => [r[0], n0(r[1]), n0(r[2])]);
export const rows2 = (v, max = 41) => list(v).filter((r) => Array.isArray(r) && typeof r[0] === 'string').slice(0, max).map((r) => [r[0], n0(r[1])]);
const okPart = (v) => (isObj(v) && v.state === 'ok' ? v : null);

// 들어온 길 → 첫 페이지 → 마지막 페이지(062 flows) → sankey 마디·고리. 마디 이름에 층 머리를 붙여 같은 페이지가 들어온 곳이자
// 나간 곳이어도 두 마디다. 고리 굵기 = 브라우저 모양 짝 수(value), 확인 수는 c. (기타) 줄도 세 층의 마디로. 들어온 길 마디만 색(064)
export function flowGraph(flows) {
  const nodes = new Map();
  const links = new Map();
  const node = (depth, key, label) => {
    const name = `${depth}:${key}`;
    if (!nodes.has(name)) nodes.set(name, { name, depth, label, mark: depth === 0 ? channelMark(key) : 'cmp' });
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

// 봇 비율 — (스캐너 + 자동화 도구 요청) ÷ 전체 요청 × 100. 요청이 없으면 null(064 — 탐색 줄은 대부분 이 둘에 들어 더하면 두 번 센다)
export function botShare(a) {
  const total = n0(a?.totals?.requests);
  if (!total) return null;
  const req = (k) => n0(a.classes?.[k]?.requests);
  return (100 * (req('scanner') + req('tool'))) / total;
}

// --- 목록 원천(카드의 탭) — items [{ key, name, value, c? }]. 방문자 기반은 값 = 브라우저 모양(s), 확인(c)은 표에 ---------------

const pairItems = (rows, name) => rows3(rows).map(([key, c, s]) => ({ key, name: name(key), value: s, c }));
const lineItems = (rows, name) => rows2(rows).map(([key, v]) => ({ key, name: name(key), value: v }));
const visitorsOf = (a) => okPart(a?.visitors);
const geoOf = (a) => okPart(a?.geo);

// 망 종류 — 서버가 묶지 않아(039, 2026-10-17) telecom 은 늘 해외 통신사, telecom_kr 은 국내 통신사다
function networks(g) {
  return rows3(g.networks).map(([key, c, s]) => ({ key, name: netName(key), value: s, c, mark: netMark(key) }));
}
const DEPTH = [['1', '1쪽'], ['2', '2쪽'], ['3-5', '3~5쪽'], ['6+', '6쪽 이상']];

// 탭 = [키, 이름, 기대는 부분(상태 배지), 행, 분모 이름]
export const LISTS = Object.freeze({
  src: [
    ['channels', '채널', (a) => a?.visitors, (a) => pairItems(visitorsOf(a)?.channels, channelName), '방문자'],
    ['referrers', '출처', (a) => a, (a) => lineItems(a?.referrers, clean), '사람 모양 페이지'],
    ['utm', 'utm', (a) => a, (a) => lineItems(a?.utmSources, clean), '사람 모양 페이지'],
  ],
  geo: [
    ['countries', '나라', (a) => a?.geo, (a) => pairItems(geoOf(a)?.countries, countryLabel), '방문자'],
    ['networks', '망 종류', (a) => a?.geo, (a) => (geoOf(a) ? networks(geoOf(a)) : []), '방문자'],
  ],
  ends: [
    ['entries', '첫 페이지', (a) => a?.visitors, (a) => pairItems(visitorsOf(a)?.entries, pageName), '방문자'],
    ['exits', '마지막 페이지', (a) => a?.visitors, (a) => pairItems(visitorsOf(a)?.exits, pageName), '방문자'],
    ['depth', '본 페이지 수', (a) => a?.visitors, (a) => DEPTH.map(([k, name]) => ({ key: k, name, value: n0(visitorsOf(a)?.depthPages?.[k]?.[1]), c: n0(visitorsOf(a)?.depthPages?.[k]?.[0]) })), '방문자'],
  ],
  what: [
    ['paths', '경로', (a) => a, (a) => lineItems(a?.paths, clean), '사람 모양 페이지'],
    ['tabs', '대시보드 탭', (a) => a, (a) => lineItems(a?.tabs, tabName), '사람 모양 페이지'],
  ],
});
// 분모 — 방문자 목록은 창 안 브라우저 모양 방문자, 줄 목록은 사람 모양 페이지(038 — % 분모 totals.humanPages)
export const denomOf = (a, kind) => (kind === '방문자' ? num(visitorsOf(a)?.shaped) : num(a?.totals?.humanPages));

// --- 100% 띠 원천 -------------------------------------------------------------------------------

// 기기·OS·브라우저 — 방문자(브라우저 모양)로, 방문자 값이 없으면 기기·브라우저는 사람 모양 페이지 줄로(OS 는 없다 — 064)
export function whoGroups(a) {
  const v = visitorsOf(a);
  const from = (key, name, mark, fallback) =>
    v ? rows3(v[key]).map(([k, , s]) => ({ key: k, name: name(k), value: s, mark: mark(k) })) : fallback ? rows2(a?.[key]).map(([k, n]) => ({ key: k, name: name(k), value: n, mark: mark(k) })) : [];
  const classes = isObj(a?.classes) ? a.classes : {};
  return [
    { id: 'devices', name: '기기', note: v ? '방문자' : '사람 모양 페이지', items: from('devices', deviceName, deviceMark, true) },
    { id: 'os', name: 'OS', note: '방문자', items: from('os', osName, osMark, false) },
    { id: 'browsers', name: '브라우저', note: v ? '방문자' : '사람 모양 페이지', items: from('browsers', browserName, browserMark, true) },
    { id: 'classes', name: '요청 종류', note: '모든 요청', items: Object.keys(CLASS_NAME).map((k) => ({ key: k, name: className(k), value: n0(classes[k]?.requests), mark: classMark(k) })) },
  ];
}
export const statusItems = (a) => STATUS_KEYS.map(([key, name, mark]) => ({ key, name, value: n0(a?.status?.[key]), mark }));
export const WS_BUCKETS = Object.freeze([['lt10s', '10초 미만'], ['lt1m', '1분 미만'], ['lt10m', '10분 미만'], ['lt1h', '1시간 미만'], ['ge1h', '1시간 이상']]);
