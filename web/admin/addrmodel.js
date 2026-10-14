// IP 모델(스펙 070 — 069 피드 /admin/access/addrs·addr): IP 글자 검사, 쪽 요청, 큰 수 넷, 표 행(판정·점수·위치), 판정 근거·어디 칸의
// 글과 수. DOM 없음. 전체 저장(069) 전 기록은 가린 행(masked — ip 가 대역 글자)으로 온다. IP 는 주소 쿼리(f=)에 싣지 않는다.
import { clean, isObj, kstDay, kstText, list, n0, num, req } from './common.js';
import { countryName, flagOf, netName } from './names.js';
import { PAGE_ROWS, recentRows, toMs, validBand } from './ipmodel.js';

// 판정 다섯 — 069 §3.4 순서. 상태색은 066 규칙(문제 ■ · 주의 ▲ · 정상 ● · 자료 없음 ○), 운영자는 강조색 ◆
export const VERDICTS = Object.freeze(['bot', 'suspect', 'human', 'unknown', 'operator']);
export const VERDICT_NAME = Object.freeze({ bot: '봇', suspect: '의심', human: '사람', unknown: '모름', operator: '운영자' });
export const VERDICT_TONE = Object.freeze({ bot: 'bad', suspect: 'warn', human: 'ok', unknown: 'none', operator: 'op' });
// 점수 줄의 문턱 글(069 §3.4 — 60 이상 봇·30~59 의심·30 미만 사람·요청 3 미만 모름·운영자 흔적은 점수를 세지 않음)
const VERDICT_RULE = Object.freeze({ bot: '60 이상', suspect: '30~59', human: '30 미만', unknown: '요청 3 미만', operator: '운영자 흔적 — 점수 안 셈' });
export const verdictOf = (v) => (VERDICTS.includes(v) ? v : 'unknown');
// 서버가 정렬하는 열(069 sort) — 그 밖 열은 불러온 행 안에서
export const ADDR_SORT = Object.freeze(['requests', 'pages', 'last', 'score']);
// 근거 막대 폭의 기준 — 가장 큰 가중치(ua_bot 70)
export const REASON_SCALE = 70;

// 상세를 부를 수 있는 IP — IPv4 점 넷(0~255), IPv6(소문자 16진·콜론, IPv4 꼬리 허용), 또는 067 대역 글자. 모음 행 '(없음)'·'(기타)' 는 아니다.
// 정규 꼴 검사는 서버(069 — 아니면 400 bad_ip)가 한다
const OCTET = '(25[0-5]|2[0-4]\\d|1?\\d?\\d)';
const V4 = new RegExp(`^${OCTET}(\\.${OCTET}){3}$`);
const V6 = /^(?=.*:)[0-9a-f:.]{2,45}$/;
export const validAddr = (ip) => typeof ip === 'string' && (V4.test(ip) || V6.test(ip) || validBand(ip));

// 쪽 요청 — 거르기(망 종류·나라·판정 여럿·가린 기록 빼기)와 찾기, 정렬(서버 열만), 200행씩
export function addrsReqs(view, o) {
  const all = (k) => view.f.filter(([key]) => key === k).map(([, v]) => v);
  const verdict = VERDICTS.filter((v) => all('verdict').includes(v)).join(',');
  const sort = ADDR_SORT.includes(o.sort) ? o.sort : 'requests';
  const q = clean(o.q).trim();
  return Array.from({ length: Math.max(1, o.pages) }, (_, i) =>
    req('addrs', {
      window: view.range,
      net: all('net')[0],
      country: all('country')[0],
      verdict: verdict || undefined,
      q: q || undefined,
      masked: all('masked')[0],
      sort,
      limit: PAGE_ROWS,
      offset: i * PAGE_ROWS,
    }),
  );
}

// 큰 수 넷 — IP 수(거르기 뒤 total) · 봇 판정 비율(봇+의심 행의 요청 ÷ 불러온 행의 요청) · 데이터센터 요청 비율 · 오늘(KST) 처음 본 IP.
// 비율의 분모는 불러온 행 — total 이 더 많으면 partial(어림)
export function addrKpis(rows, total, now) {
  const reqs = rows.reduce((s, r) => s + n0(r.requests), 0);
  const sum = (pick) => rows.filter(pick).reduce((s, r) => s + n0(r.requests), 0);
  const today = kstDay(Math.floor(now / 1000)) * 1000;
  return {
    count: num(total) ?? rows.length,
    bot: reqs ? (sum((r) => r.verdict === 'bot' || r.verdict === 'suspect') / reqs) * 100 : null,
    dc: reqs ? (sum((r) => r.net === 'cloud') / reqs) * 100 : null,
    fresh: rows.filter((r) => (toMs(r.first) ?? -Infinity) >= today).length,
    partial: num(total) !== null && total > rows.length,
  };
}

// 'IP 전체는 MM-DD HH:MM 부터 — …'(KST). since 가 없으면 빈 글
export const sinceText = (since) => (num(since) === null ? '' : `IP 전체는 ${kstText(toMs(since)).slice(5)} 부터 — 그 전 기록은 대역(가림)으로 보인다`);

// 망 사업자 AS 번호 — 수면 'AS4766', 'AS…' 글자면 그대로
export const asnText = (asn) => (num(asn) !== null ? `AS${Math.round(asn)}` : typeof asn === 'string' && asn ? clean(/^\d+$/.test(asn) ? `AS${asn}` : asn) : '');
// 위치 글 — '🇰🇷 서울 · KT'(국기 · 도시, 없으면 지역, 없으면 나라 이름 · 망 사업자). 모두 없으면 '–'
export function placeText(r) {
  const spot = clean(r?.city || r?.region || (typeof r?.country === 'string' ? countryName(r.country) : ''));
  const head = [flagOf(r?.country), spot].filter(Boolean).join(' ');
  return [head, clean(r?.org ?? '')].filter(Boolean).join(' · ') || '–';
}

// 근거 — 가중치 큰 순(같으면 code 글자 순), '+70 …'·'−40 …' 꼴, 막대 폭 = |가중치| ÷ 70(1 에서 자름), 양수는 봇 쪽
export const signed = (w) => (w > 0 ? `+${w}` : w < 0 ? `−${Math.abs(w)}` : '0');
export function reasonRows(reasons) {
  return list(reasons)
    .filter((r) => isObj(r) && num(r.weight) !== null)
    .map((r) => ({ code: clean(r.code), text: clean(r.text), weight: r.weight }))
    .sort((a, b) => b.weight - a.weight || a.code.localeCompare(b.code))
    .map((r) => ({ ...r, sign: signed(r.weight), line: `${signed(r.weight)} ${r.text}`, ratio: Math.min(1, Math.abs(r.weight) / REASON_SCALE), side: r.weight > 0 ? 'bot' : 'human' }));
}
// 행 title — 근거 상위 셋(위와 같은 차례)
export const topReasons = (row, n = 3) => reasonRows(row?.reasons).slice(0, n).map((r) => r.line).join('\n');
// '점수 n → 봇(60 이상)' — 점수가 없으면(운영자) '점수 –'
export function scoreLine(score, verdict) {
  const v = verdictOf(verdict);
  return `점수 ${num(score) === null ? '–' : Math.round(score)} → ${VERDICT_NAME[v]}(${VERDICT_RULE[v]})`;
}

// 확인된/사칭 검색 로봇 줄(069 §3.4 verifiedCrawler — 상세만). 이름은 근거·브라우저 정보 글에서 찾는다(없으면 '검색')
const CRAWLERS = [
  [/googlebot/i, 'Google'],
  [/bingbot/i, 'Bing'],
  [/yeti/i, '네이버'],
  [/daum/i, 'Daum'],
];
export function crawlerLine(body) {
  if (typeof body?.verifiedCrawler !== 'boolean') return null;
  const words = [...list(body.reasons).map((r) => r?.text), ...list(body.agents).map((a) => (Array.isArray(a) ? a[0] : '')), ...list(body.topAgents).map((a) => (Array.isArray(a) ? a[0] : ''))].map(clean).join(' ');
  const name = CRAWLERS.find(([re]) => re.test(words))?.[1] ?? '검색';
  return body.verifiedCrawler ? { ok: true, text: `✓ 주소 확인된 ${name} 로봇` } : { ok: false, text: `✕ 이름만 ${name} — 주소 확인 실패` };
}

// 어디 칸 — [이름, 값, 흐린 덧글]. 값이 없으면 '–'. 도시는 '대략'(통신사 거점일 수 있다), 위도·경도는 지도 없이 수만
const fixed = (v) => (num(v) === null ? null : String(Math.round(v * 10_000) / 10_000));
export function whereRows(body) {
  const g = isObj(body?.geo) ? body.geo : {};
  const pick = (k) => g[k] ?? body?.[k] ?? null;
  const country = pick('country');
  const dash = (v) => (v === null || v === undefined || v === '' ? '–' : clean(v));
  const lat = fixed(g.lat);
  const lon = fixed(g.lon);
  return [
    ['나라', typeof country === 'string' && country ? [flagOf(country), countryName(country)].filter(Boolean).join(' ') : '–', ''],
    ['지역', dash(pick('region')), ''],
    ['도시', dash(pick('city')), pick('city') && g.approx !== false ? '대략' : ''],
    ['망 사업자', [clean(pick('org') ?? ''), asnText(pick('asn'))].filter(Boolean).join(' · ') || '–', ''],
    ['망 종류', pick('net') ? netName(pick('net')) : '–', ''],
    ['역방향 DNS', dash(g.ptr), ''],
    ['위도 · 경도', lat !== null && lon !== null ? `${lat}, ${lon}` : '–', ''],
  ];
}

// 표 행 — 열 글자·수. 가린 행은 '대역' 표시, 모음 행·꼴이 아닌 IP 는 판을 열지 않는다
export function addrRows(rows) {
  const max = Math.max(0, ...rows.map((r) => n0(r.requests)));
  return rows.map((r) => {
    const verdict = verdictOf(r.verdict);
    return {
      ip: r.ip,
      label: clean(r.ip),
      masked: r.masked === true,
      verdict,
      verdictName: VERDICT_NAME[verdict],
      score: num(r.score),
      place: placeText(r),
      requests: num(r.requests),
      ratio: max ? n0(r.requests) / max : 0,
      pages: num(r.pages),
      first: toMs(r.first),
      last: toMs(r.last),
      spark: list(r.spark),
      reasons: topReasons(r),
      open: validAddr(r.ip),
    };
  });
}

// 최근 요청 100줄(067 꼴) + 줄마다 판정 신호 칩(069 flags)
export const FLAG_NAME = Object.freeze({ probe: '탐색', 404: '404', asset: '자원', js: 'JS', ws: 'WS' });
export const FLAG_TONE = Object.freeze({ probe: 'bad', 404: 'warn' });
export function recentAddrRows(recent) {
  const raw = list(recent).filter(isObj).slice(0, 100);
  return recentRows(raw).map((r, i) => {
    const flags = list(raw[i].flags).filter((f) => typeof f === 'string' && Object.hasOwn(FLAG_NAME, f));
    return { ...r, flags, flagText: flags.map((f) => FLAG_NAME[f]).join(' ') };
  });
}

// 부분 하나가 그릴 수 있는 IP 상세인가 — 400 bad_ip 는 판 안 한 줄
export const badIp = (part) => Boolean(part?.why?.includes('bad_ip'));
