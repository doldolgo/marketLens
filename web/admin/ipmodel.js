// IP 대역 모델(스펙 066 §3.3 '방문자' — 067 피드): 대역 글자 검사·표시, 쪽 요청, 큰 수 넷, 표 행, 대역 하나의 판 모델. DOM 없음.
// 대역은 접속 기록의 가린 IP 그대로(IPv4 /24 · IPv6 /48 — 원 IP·짝 열쇠·UA 원문은 피드에 없다). 대역 글자는 주소 쿼리에 싣지 않는다.
import { clean, isObj, kstDay, kstText, list, n0, num, own, partOf, req, usable } from './common.js';
import { CLASS_NAME, STATUS_KEYS, classMark, countryLabel, netName } from './names.js';

export const IP_RANGES = Object.freeze(['24h', '7d', '30d']);
export const NETS = Object.freeze(['telecom_kr', 'telecom', 'cloud', 'other', 'unknown']);
export const PAGE_ROWS = 200;
// 서버가 정렬하는 열(067 sort) — 그 밖 열은 불러온 행 안에서 정렬한다
export const SERVER_SORT = Object.freeze(['requests', 'pages', 'last']);

// 067 §3.3 — IPv4 /24 꼴(x.y.z.0) 또는 IPv6 /48 꼴(앞 세 마디 + ::)
const V4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)\.(25[0-5]|2[0-4]\d|1?\d?\d)\.(25[0-5]|2[0-4]\d|1?\d?\d)\.0$/;
const V6 = /^[0-9a-f]{1,4}(:[0-9a-f]{1,4}){0,2}::$/i;
export const validBand = (band) => typeof band === 'string' && (V4.test(band) || V6.test(band));
export const bandLabel = (band) => (validBand(band) ? `${band}${band.includes(':') ? '/48' : '/24'}` : clean(band));
// 처음·마지막 시각 — 초(그 밖 *Ts·ts 와 같은 단위)로 읽되 ms 로 오면 그대로
export const toMs = (t) => (num(t) === null ? null : t > 1e12 ? t : t * 1000);

// 쪽 요청 — 거르기(망 종류·나라·봇 뺌)와 찾기, 정렬(서버 열만), 200행씩
export function ipsReqs(view, o) {
  const f = (k) => view.f.find(([key]) => key === k)?.[1];
  const sort = SERVER_SORT.includes(o.sort) ? o.sort : 'requests';
  const q = clean(o.q).trim();
  return Array.from({ length: Math.max(1, o.pages) }, (_, i) =>
    req('ips', { window: view.range, net: f('net'), country: f('country'), q: q || undefined, bots: f('bots'), sort, limit: PAGE_ROWS, offset: i * PAGE_ROWS }),
  );
}
// 불러온 쪽들 → { part(첫 쪽), total, bands(대역 하나에 한 행 — 쪽이 겹치면 앞 쪽), deps }
export function loadedBands(app, reqs) {
  const entries = reqs.map((r) => app.entry(r));
  const part = partOf(entries[0]);
  const seen = new Set();
  const bands = [];
  for (const e of entries) {
    const p = partOf(e);
    if (!usable(p)) break;
    for (const b of list(p.bands).filter(isObj)) {
      if (seen.has(b.band)) continue;
      seen.add(b.band);
      bands.push(b);
    }
  }
  return { part, total: usable(part) ? num(part.total) : null, bands, deps: entries };
}

// 큰 수 넷 — 대역 수(거르기 뒤 total) · 데이터센터 대역의 요청 비중 · 상위 10 대역의 요청 비중 · 오늘(KST) 처음 본 대역.
// 비중의 분모는 불러온 대역의 요청 합 — 불러온 대역이 total 보다 적으면 partial(어림)
export function bandKpis(bands, total, now) {
  const reqs = bands.reduce((s, b) => s + n0(b.requests), 0);
  const dc = bands.filter((b) => b.net === 'cloud').reduce((s, b) => s + n0(b.requests), 0);
  const top = [...bands].sort((a, b) => n0(b.requests) - n0(a.requests)).slice(0, 10).reduce((s, b) => s + n0(b.requests), 0);
  const today = kstDay(Math.floor(now / 1000)) * 1000;
  return {
    count: num(total) ?? bands.length,
    dc: reqs ? (dc / reqs) * 100 : null,
    top: reqs ? (top / reqs) * 100 : null,
    fresh: bands.filter((b) => (toMs(b.first) ?? -Infinity) >= today).length,
    partial: num(total) !== null && total > bands.length,
  };
}

// 표 행 — 열 글자·수. 대역 꼴이 아닌 행((없음)·(기타))은 판을 열지 않는다
export function bandRows(bands) {
  const max = Math.max(0, ...bands.map((b) => n0(b.requests)));
  return bands.map((b) => ({
    band: b.band,
    label: bandLabel(b.band),
    where: [countryLabel(b.country ?? '–'), b.net ? netName(b.net) : '–'].join(' · '),
    requests: num(b.requests),
    ratio: max ? n0(b.requests) / max : 0,
    pages: num(b.pages),
    pairs: num(b.pairs),
    botPct: num(b.botPct),
    fives: num(b.status?.['5xx']),
    first: toMs(b.first),
    last: toMs(b.last),
    seen: `${kstText(toMs(b.first)).slice(5)} ~ ${kstText(toMs(b.last)).slice(5)}`,
    spark: list(b.spark),
    open: validBand(b.band),
  }));
}

// 대역 하나(067 /admin/access/ip) — 종류 여덟·상태 띠, 브라우저 정보·경로 상위, 짝의 첫/마지막 페이지, 최근 요청 100줄
export const kindItems = (kinds) => Object.keys(CLASS_NAME).map((k) => ({ key: k, name: CLASS_NAME[k], value: n0(kinds?.[k]), mark: classMark(k) }));
export const statusItems = (status) => STATUS_KEYS.filter(([k]) => k !== 'ws5xx').map(([key, name, mark]) => ({ key, name, value: n0(status?.[key]), mark }));
export const pairList = (rows) => list(rows).filter((r) => Array.isArray(r) && typeof r[0] === 'string').map(([name, n]) => ({ key: name, name: clean(name), value: n0(n) }));
export const recentRows = (recent) =>
  list(recent)
    .filter(isObj)
    .slice(0, 100)
    .map((r) => ({
      ts: toMs(r.ts),
      path: clean(r.path),
      status: num(r.status),
      kind: own(CLASS_NAME, r.kind) ?? clean(r.kind),
      browser: clean(r.browser),
      os: clean(r.os),
      device: clean(r.device),
      referer: clean(r.referer),
    }));
