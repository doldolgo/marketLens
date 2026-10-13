// KimpTrack 관리자 v4 공통 도구 (스펙 066 — 064 §3.1 의 요청·보안 규칙을 이어받는다). 모든 페이지 모듈이 가져온다.
// 빌드 없음: 브라우저가 ES 모듈로 바로 읽는다(CSP script-src 'self' — oxlint·vite 대상이 아니다, 029·036).
// 차례: 경로·요청 → 글자·수·시각 도구 → DOM 도구 → 부분 상태 표 → 다시 그리기 판단.
// 보안: 서버·방문자가 정한 글자는 textContent·title 로만 넣는다(innerHTML 없음). 주소를 쓰는 곳은 app.js(그 페이지의 고정
// 주소)·rules.js 의 jump()(고정 표)·이 파일의 download()(브라우저가 만든 Blob 주소)뿐이다.

export const FAST_MS = 10_000; // 빠른 묶음 — 029 의 네 경로, 보이는 동안만
export const SLOW_MS = 60_000; // 느린 묶음 — 그 페이지가 쓰는 나머지, 보이는 동안만
const XHR = Object.freeze({ 'X-Requested-With': 'XMLHttpRequest' });

// 관리자 nginx 분기로 가는 경로(029·034·035·063 — 067 의 ips·ip 와 068 의 history 는 그 스펙이 정확 일치 location 을 더한다).
// 화면이 부르는 곳은 이 표와 즉시 갱신(POST /api/refresh)뿐이다
export const PATHS = Object.freeze({
  collector: '/api/health',
  api: '/svc/api/health',
  status: '/svc/api/admin/status',
  collect: '/api/health/collect',
  aws: '/api/admin/aws',
  series: '/api/admin/aws/series',
  alerts: '/api/admin/alerts',
  access: '/svc/api/admin/access',
  ips: '/svc/api/admin/access/ips',
  ip: '/svc/api/admin/access/ip',
  clarity: '/svc/api/admin/clarity',
  history: '/svc/api/admin/clarity/history',
  attention: '/svc/api/admin/attention',
});
export const FAST = Object.freeze(['collector', 'api', 'status', 'collect']);
// 025 — 헬스의 503 도 상태 응답이다(본문을 읽는다)
export const HEALTH = Object.freeze(['collector', 'api']);

// 요청 하나 = 경로 이름 + 쿼리. 열쇠는 주소 그대로 — 같은 주소는 한 번만 부르고 결과 하나를 여러 카드가 나눠 쓴다
export function req(name, query = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
  const s = q.toString();
  const url = s ? `${PATHS[name]}?${s}` : PATHS[name];
  return Object.freeze({ name, url, key: url, fast: FAST.includes(name) });
}

class Expired extends Error {}

// 요청은 모두 이 함수 하나를 지난다(헤더·만료 판정). 029 의 세션 판별 — (1) 401 + 앱 JSON(detail) = 토큰 오류,
// 그대로 돌려준다 (2) 401 인데 JSON 이 아니거나 요청 자체가 실패(교차 출처 리다이렉트) = 로그인 만료 신호
export async function call(path, init = {}) {
  let resp;
  try {
    resp = await fetch(path, { ...init, headers: { ...XHR, ...init.headers }, cache: 'no-store' });
  } catch {
    throw new Expired();
  }
  const text = await resp.text();
  let body = null;
  try {
    body = JSON.parse(text);
  } catch {
    body = null;
  }
  if (resp.status === 401 && !(isObj(body) && 'detail' in body)) throw new Expired();
  return { status: resp.status, body: isObj(body) ? body : null, text };
}
export const isExpired = (err) => err instanceof Expired;

// --- 글자·수·시각 도구 -------------------------------------------------------------------------

export const $ = (id) => document.getElementById(id);
// 보이는 글자에서 양방향 제어문자를 뺀다 — 방향을 뒤집어 다른 글처럼 보이게 하는 것을 막는다(036)
const BIDI = /[‪-‮⁦-⁩]/g;
export const clean = (v) => String(v ?? '').replace(BIDI, '');
export const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
export const list = (v) => (Array.isArray(v) ? v : []);
export const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
export const n0 = (v) => num(v) ?? 0;
// 서버 값으로 표를 찾을 때 — 'constructor' 같은 이름이 Object 의 것을 집지 않게
export const own = (map, key) => (typeof key === 'string' && Object.hasOwn(map, key) ? map[key] : undefined);

const NF = new Intl.NumberFormat('ko-KR');
const D1 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const SHORT = new Intl.NumberFormat('ko-KR', { notation: 'compact', maximumFractionDigits: 1 });
// 0 은 그대로, null 은 '–', 큰 값은 천 단위 쉼표(064 §3.5)
export const int = (v) => (num(v) === null ? '–' : NF.format(Math.round(v)));
export const dec1 = (v) => (num(v) === null ? '–' : D1.format(v));
export const pct = (v, digits = 1) => (num(v) === null ? '–' : `${v.toFixed(digits)}%`);
// 축 눈금 — 1,200 · 1.2만 처럼 짧게
export const short = (v) => (num(v) === null ? '–' : Math.abs(v) < 10_000 ? NF.format(Math.round(v * 10) / 10) : SHORT.format(v));
export const pad = (n) => String(n).padStart(2, '0');
export const clock = (ms) => new Date(ms).toTimeString().slice(0, 8); // HH:MM:SS (브라우저 시간대)
export const hm = (ms) => clock(ms).slice(0, 5);
export const md = (ms) => `${pad(new Date(ms).getMonth() + 1)}-${pad(new Date(ms).getDate())}`;
export const when = (ms, now = Date.now()) =>
  num(ms) === null ? '–' : new Date(ms).toDateString() === new Date(now).toDateString() ? clock(ms) : `${md(ms)} ${hm(ms)}`;

// 경과 — 브라우저 시계가 서버보다 빨라도 음수 없이 0초(036)
export function ago(ms, now = Date.now()) {
  if (num(ms) === null) return '–';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 172_800) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86_400)}일 전`;
}

// 이어진 시간 — 45초 · 12분 · 3시간 5분
export function span(sec) {
  const s = Math.max(0, Math.round(n0(sec)));
  if (s < 60) return `${s}초`;
  if (s < 3600) return `${Math.floor(s / 60)}분`;
  return `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분`;
}

// 바이트 — 단위 자동(1024 단위 B·KB·MB·GB·TB). 뒤집은 면적의 아래(음수)는 크기만
const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];
export function bytes(v) {
  if (num(v) === null) return '–';
  let x = Math.abs(v);
  let i = 0;
  while (x >= 1023.5 && i < UNITS.length - 1) {
    x /= 1024;
    i += 1;
  }
  return `${i === 0 || x >= 9.95 ? NF.format(Math.round(x)) : D1.format(x)} ${UNITS[i]}`;
}

// 금액 — USD 는 $ 를 앞에, 그 밖 단위는 뒤에(서버 글자)
const USD = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const money = (v, unit) =>
  num(v) === null ? '–' : unit == null || unit === 'USD' ? `$${USD.format(v)}` : `${USD.format(v)} ${clean(unit)}`;

// KST 벽시계 — 한국은 일광 절약이 없어 +9시간 고정이다. 원자료 표·CSV 는 브라우저 시간대와 무관하게 이 글자를 쓴다
const KST_MS = 32_400_000;
function kstParts(ms) {
  const d = new Date(ms + KST_MS);
  return [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate(), d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()];
}
export function kstText(ms, withSec = false) {
  if (num(ms) === null) return '–';
  const [y, mo, d, h, mi, s] = kstParts(ms);
  return `${y}-${pad(mo)}-${pad(d)} ${pad(h)}:${pad(mi)}${withSec ? `:${pad(s)}` : ''}`;
}
// 파일 이름용 — 20261014T0930
export function kstStamp(ms) {
  const [y, mo, d, h, mi] = kstParts(ms);
  return `${y}${pad(mo)}${pad(d)}T${pad(h)}${pad(mi)}`;
}
export const utcText = (ms) => `${new Date(ms).toISOString().slice(0, 19)}Z`;
// 그 KST 날의 00:00(epoch 초)
export const kstDay = (sec) => Math.floor((sec + 32_400) / 86_400) * 86_400 - 32_400;

// --- DOM 도구 ---------------------------------------------------------------------------------

export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = clean(text);
  return node;
}

// 글자만 바꾼다 — 같으면 쓰지 않는다(텍스트 노드가 바뀌면 그 안의 글자 선택이 풀린다)
export function put(id, text, cls) {
  const node = $(id);
  if (!node) return node;
  const next = clean(text);
  if (node.textContent !== next) node.textContent = next;
  if (cls !== undefined) node.className = cls;
  return node;
}

export const button = (cls, text) => {
  const node = el('button', cls, text);
  node.setAttribute('type', 'button');
  return node;
};
export const pressed = (node, on) => node.setAttribute('aria-pressed', String(Boolean(on)));

// 작은 그림(표 칸의 작은 선) — SVG 요소. 속성 이름은 부르는 쪽의 고정 글자, 값은 수뿐이다
const SVG_NS = 'http://www.w3.org/2000/svg';
export function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

// 막대 폭·자리 — 0~1 비율 수만 받아 % 로 쓴다(피드 글자는 들어가지 않는다). 화면이 style 을 만지는 곳은 이 둘과 아래 틀 칸뿐
const ratioPct = (r) => `${Math.round(Math.min(1, Math.max(0, num(r) ?? 0)) * 1000) / 10}%`;
export function widthOf(node, ratio) {
  node.style.width = ratioPct(ratio);
}
export function leftOf(node, ratio) {
  node.style.left = ratioPct(ratio);
}
// 틀 칸(065 화면) — 실제 페이지를 줄이는 몫(0~1, 늘리지 않는다)과 칸 높이(px) 두 수만 CSS 변수로
export function frameVars(node, fit, height) {
  const s = node.style;
  s.setProperty('--fit', String(Math.round(Math.min(1, Math.max(0.05, num(fit) ?? 1)) * 10000) / 10000));
  s.setProperty('--stage-h', `${Math.round(Math.max(0, num(height) ?? 0))}px`);
}

// CSV 내려받기(066 §3.2) — 브라우저에서 만든 Blob 의 주소만 링크로 쓴다(서버 호출 없음)
export function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const link = el('a');
  link.setAttribute('href', url);
  link.setAttribute('download', name);
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function notice(text) {
  const node = $('notice');
  if (!node) return;
  node.textContent = clean(text);
  node.hidden = !text;
}

// --- 부분 상태 표 (064 §3.1 → 066 §3.1 — 카드 머리 오른쪽 작은 배지) -----------------------------------
// 결과 하나 = { body, text, okAt, why, off } — body 는 마지막 성공 값(실패해도 두고 흐리게 그린다), why 는 마지막 호출의 실패 사유,
// off 는 그 피드가 이 서버에 아직 없음(404 — 서버 배포 전)

const BAD_SHAPE = Object.freeze({ why: '응답 모양 오류' });
const NO_FEED = Object.freeze({ state: 'unconfigured', code: null });
const PLAIN_OK = Object.freeze({ state: 'ok' });

// 결과에서 부분 하나 — undefined(첫 응답 전)·{ why }(실패하고 값 없음)·부분 객체({ state, code, … }). 실패했지만 값이 있으면
// 그 값에 stale(사유)·staleAt(받은 때)을 붙인다. sub 가 없으면 응답 전체가 부분이다(series·access·clarity·ips…)
export function partOf(entry, sub) {
  if (!entry) return undefined;
  if (entry.off) return NO_FEED;
  if (entry.body === undefined || entry.body === null) return entry.why ? { why: entry.why } : undefined;
  const part = sub ? entry.body[sub] : entry.body;
  const found = isObj(part) && typeof part.state === 'string' ? part : sub && part === undefined ? NO_FEED : BAD_SHAPE;
  return entry.why && !found.why ? { ...found, stale: entry.why, staleAt: entry.okAt } : found;
}
// 상태 응답(헬스·status·collect)은 부분 꼴이 아니다 — 호출 결과만 표로
export function plainOf(entry) {
  if (!entry) return undefined;
  if (entry.off) return NO_FEED;
  if (entry.body === undefined || entry.body === null) return entry.why ? { why: entry.why } : undefined;
  return entry.why ? { ...PLAIN_OK, stale: entry.why, staleAt: entry.okAt } : PLAIN_OK;
}

// 값을 그릴 수 있는가 — ok(실패 뒤 마지막 값 포함), 또는 error 인데 값이 있다(Clarity 의 마지막 성공 값, 040)
export function usable(part, valueKey) {
  if (!part || part.why) return false;
  if (part.state === 'ok') return true;
  return part.state === 'error' && valueKey !== undefined && part[valueKey] != null;
}

// 표 = [색, 글, title] 또는 null(문제 없음 — 비운다). 오래됨 = fetchedAt 이 refreshSec 의 세 배를 넘음(036).
// 서버 기록 게이트 문구는 쓰지 않는다(사람 결정 2026-10-09 — 060): before_gate 도 다른 '연결 안 됨' 과 같고 code 는 싣지 않는다
const QUIET_CODES = new Set(['before_gate']);
export function tagOf(part, now = Date.now()) {
  if (part === undefined) return ['wait', '불러오는 중', ''];
  if (part.why) return ['bad', '불러오지 못함', part.why];
  if (part.stale) return ['bad', `불러오지 못함 · ${ago(part.staleAt, now)} 값`, part.stale];
  const code = QUIET_CODES.has(part.code) ? '' : clean(part.code ?? '');
  if (part.state === 'ok') {
    const old = num(part.refreshSec) > 0 && num(part.fetchedAt) !== null && now - part.fetchedAt > part.refreshSec * 3000;
    return old ? ['warn', '오래됨', `${ago(part.fetchedAt, now)} 값`] : null;
  }
  if (part.state === 'pending') return ['wait', '첫 조회 중', ''];
  if (part.state === 'unconfigured') return ['dim', '연결 안 됨', code];
  if (part.state === 'denied') return ['dim', '권한 없음', code];
  return ['bad', '불러오지 못함', code];
}

export function tagText(id, t) {
  const node = $(id);
  if (!node) return;
  node.className = t ? `tag ${t[0]}` : 'tag';
  put(id, t ? t[1] : '');
  node.title = t ? clean(t[2]) : '';
}
// 카드 하나의 상태 — 배지와, 실패 뒤 마지막 값이면 카드를 흐리게(066 §3.1)
export function tag(id, part) {
  tagText(id, tagOf(part));
  const card = $(id)?.closest?.('.card');
  if (card) card.classList.toggle('stale', Boolean(part?.stale));
}

// 그림 칸에 보일 상태 글 — 표가 없으면(ok) '자료 없음'
export const stateWord = (part) => tagOf(part)?.[1] ?? '자료 없음';

// 그 카드가 기대는 값(결과 객체·고른 값)이 바뀌었을 때만 다시 그린다 — 10초 묶음마다 차트·툴팁·확대가 날아가지 않게(036)
const drawn = new Map();
export function once(name, deps, draw) {
  const now = Array.isArray(deps) ? deps : [deps];
  const last = drawn.get(name);
  if (last && last.length === now.length && now.every((d, i) => d === last[i])) return;
  draw();
  drawn.set(name, now); // 그리다 예외가 나면 남기지 않는다 — 다음 묶음이 다시 그린다
}
export const forget = (name) => drawn.delete(name);
