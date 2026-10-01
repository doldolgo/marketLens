// KimpTrack 관리자 화면 v2 스크립트 (스펙 036). 빌드 없음 — oxlint·vite 대상이 아니다(landing.html 과 같다).
// 한 파일이다(§2 — 나누면 로드 순서·전역 이름이 계약이 된다). 순서: 공통 도구 → 요청·세션·주기 → 부분 상태·차트
// → 개요·수집 → 인프라 → 알림·접속 → 비용·그리기·시작.
// 보안(§3.8): 서버·방문자가 정한 글자는 textContent 로만 넣고 title 말고는 어떤 속성에도 쓰지 않는다 — 링크가 되지 않게.
// 즉시 갱신 토큰은 입력칸과 이 스크립트의 변수에만 있다(브라우저 저장소·쿠키 없음). 브라우저에 값을 쌓지 않는다(§3.3).
'use strict';

const FAST_MS = 10_000; // 빠른 묶음 — 029 의 네 경로, 보이는 동안만
const SLOW_MS = 60_000; // 느린 묶음 — 034·035 피드 넷, 보이는 동안만
const RELOAD_MARK = 'relogin'; // 로그인 만료로 새로고침했다는 표시 — URL 쿼리(연속 새로고침은 한 번까지)
const XHR = { 'X-Requested-With': 'XMLHttpRequest' };
const SVG_NS = 'http://www.w3.org/2000/svg';

const P = {
  collector: '/api/health',
  api: '/svc/api/health',
  status: '/svc/api/admin/status',
  collect: '/api/health/collect',
  aws: '/api/admin/aws',
  alerts: '/api/admin/alerts',
  access: '/svc/api/admin/access',
  clarity: '/svc/api/admin/clarity',
};
const FAST = [P.collector, P.api, P.status, P.collect];
const SLOW = [P.aws, P.alerts, P.access, P.clarity];
const HEALTH = new Set([P.collector, P.api]); // 025 — 503 도 상태 응답이다(본문을 그린다)

class Expired extends Error {}

const got = new Map(); // 경로 → { body } | { why } — 마지막 호출 결과 하나뿐(쌓지 않는다)
const okSince = new Set(); // 마지막 만료 신호 뒤 만료 신호 없이 끝난 경로
let reloading = false;
let alertFilter = 'all'; // 알림 필터 — JS 변수에만 (§3.4)

// --- 공통 도구 ---------------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
// 보이는 글자에서 양방향 제어문자를 뺀다(§3.8) — 방향을 뒤집어 다른 글처럼 보이게 하는 것을 막는다
const BIDI = /[‪-‮⁦-⁩]/g;
const clean = (v) => String(v ?? '').replace(BIDI, '');
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const list = (v) => (Array.isArray(v) ? v : []);
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
// 서버 값으로 표를 찾을 때 — 'constructor' 같은 이름이 Object 의 것을 집지 않게
const own = (map, key) => (typeof key === 'string' && Object.hasOwn(map, key) ? map[key] : undefined);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = clean(text);
  return node;
}

// 길면 자르고 전체는 title (§3.8)
function clip(node, text, limit) {
  const full = clean(text);
  node.textContent = full.length > limit ? `${full.slice(0, limit)}…` : full;
  if (full.length > limit) node.title = full;
  return node;
}

const NF = new Intl.NumberFormat('ko-KR');
const int = (v) => (num(v) === null ? '–' : NF.format(Math.round(v)));
const fixed = (v, d) => (num(v) === null ? '–' : v.toFixed(d));
const pad = (n) => String(n).padStart(2, '0');
const clock = (ms) => new Date(ms).toTimeString().slice(0, 8); // HH:MM:SS (브라우저 시간대)
const hm = (ms) => clock(ms).slice(0, 5);
const md = (ms) => `${pad(new Date(ms).getMonth() + 1)}-${pad(new Date(ms).getDate())}`;
// 브라우저 시계가 서버보다 빨라도 음수 없이 0초 (§3.9)
const since = (ms) => Math.max(0, Math.round((Date.now() - ms) / 1000));

function ago(ms) {
  if (num(ms) === null) return '없음';
  const s = since(ms);
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 172_800) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86_400)}일 전`;
}

// 상태는 색 + 모양 + 글자 (§3.7)
const GLYPH = { ok: '●', warn: '▲', bad: '✕', dim: '○', wait: '…', unknown: '?' };

function marked(tone, text) {
  const span = el('span', `t-${tone}`);
  span.append(el('span', 'mk', GLYPH[tone]), clean(text));
  return span;
}

function badge(tone, text) {
  const tag = el('span', `tag t-${tone}`);
  tag.append(el('span', 'mk', GLYPH[tone]), clean(text));
  return tag;
}

// --- 요청·세션·주기 ------------------------------------------------------------------------------

// 세션 판별(029) — (1) 401 + 앱 JSON(detail) = 토큰 오류, 그대로 돌려준다 (2) 401 인데 JSON 이 아니거나 요청 자체가
// 실패(교차 출처 리다이렉트) = 로그인 만료 신호 — Expired (3) 403 은 부른 쪽이 "권한·설정 오류" 로 보인다.
async function call(path, init = {}) {
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
  const appJson = isObj(body) && 'detail' in body;
  if (resp.status === 401 && !appJson) throw new Expired();
  return { status: resp.status, body: isObj(body) ? body : null };
}

// 경로 하나를 부르고 결과를 got 에 둔다. 403·JSON 아님·예상 밖 상태(피드 경로가 없는 404 등)는 사유만 남긴다 —
// 그 경로가 채우는 칸은 비우고 사유를 적는다(직전 값이 정상으로 읽히지 않게). 돌려주는 값 = 만료 신호였는가
async function load(path) {
  try {
    const { status, body } = await call(path);
    const fine = status === 200 || (status === 503 && HEALTH.has(path));
    if (status === 403) got.set(path, { why: '권한·설정 오류 (403)' });
    else if (body === null || !fine) got.set(path, { why: `응답 오류 (HTTP ${status})` });
    else got.set(path, { body });
    okSince.add(path);
    return false;
  } catch (err) {
    const gone = err instanceof Expired;
    got.set(path, { why: gone ? '로그인 만료·연결 끊김' : '불러오지 못함' });
    if (!gone) okSince.add(path);
    return gone;
  }
}

function notice(text) {
  const node = $('notice');
  node.textContent = text;
  node.hidden = !text;
}

// 로그인 만료 신호 — 표시가 없으면 한 번 새로고침하고, 표시가 있으면 알림만 보인다(연속 새로고침은 한 번까지).
// 주소는 화면 주소 `/` 로 고정한다: nginx 는 `//다른호스트/..%2F/` 같은 경로도 `/` 로 정규화해 이 화면을 주는데,
// 그 경로를 그대로 쓰면 프로토콜 상대 URL 이 되어 다른 출처(가짜 로그인 화면)로 간다.
function expired() {
  if (reloading) return;
  if (new URLSearchParams(location.search).has(RELOAD_MARK)) {
    notice('로그인이 만료됐거나 연결이 끊겼다 — 새로고침해 다시 로그인한다');
    return;
  }
  reloading = true;
  location.replace(`/?${RELOAD_MARK}=1`);
}

// 표시 지우기(§3.8) — 마지막 만료 신호 뒤 여덟 경로가 모두 만료 신호 없이 끝났을 때만. 묶음이 둘이라 한 묶음만 보고
// 지우면, 다른 묶음에만 있는 만료가 60초마다 새로고침을 되풀이한다. 즉시 갱신 버튼은 지우지 않는다(029).
function alive() {
  if (new URLSearchParams(location.search).has(RELOAD_MARK)) {
    history.replaceState(null, '', '/');
    notice('');
  }
}

// 두 묶음은 따로 돈다 — 느린 쪽이 늦어도 빠른 쪽 주기를 막지 않는다. 같은 묶음은 앞선 호출이 끝나기 전에 다시 부르지
// 않고, 다음 호출은 앞선 호출을 시작한 때부터 잰다(응답 시간만큼 밀리지 않게).
const fast = { paths: FAST, every: FAST_MS, busy: false, timer: 0, started: 0 };
const slow = { paths: SLOW, every: SLOW_MS, busy: false, timer: 0, started: 0 };

async function run(loop) {
  clearTimeout(loop.timer);
  if (loop.busy || reloading || document.visibilityState !== 'visible') return;
  loop.busy = true;
  loop.started = Date.now();
  try {
    const signals = await Promise.all(loop.paths.map(load));
    if (signals.some(Boolean)) {
      okSince.clear();
      expired();
    } else if (okSince.size === FAST.length + SLOW.length) {
      alive();
    }
    $('updated').textContent = clock(Date.now());
    paint();
  } finally {
    loop.busy = false;
    schedule(loop);
  }
}

function schedule(loop) {
  clearTimeout(loop.timer);
  if (reloading || document.visibilityState !== 'visible') return;
  loop.timer = setTimeout(() => run(loop), Math.max(0, loop.every - (Date.now() - loop.started)));
}

// 숨었다가 보이면 빠른 묶음은 곧바로, 느린 묶음은 마지막 호출에서 60초가 지났을 때만 곧바로(아니면 남은 만큼 뒤)
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') {
    clearTimeout(fast.timer);
    clearTimeout(slow.timer);
    return;
  }
  run(fast);
  if (!slow.busy) schedule(slow);
});

// --- 부분 상태 (§3.5) ----------------------------------------------------------------------------

// 연결 안 됨의 한 줄 원인
const CAUSE = {
  aws: 'AWS 자격 없음 또는 계정 종료',
  slack: '이 수집기에 Slack 웹훅 없음',
  access: '로그 파일 없음',
  clarity: '토큰 없음 또는 033 전',
};
const DENIED = 'IAM 정책 또는 조직 SCP — 콘솔에서 본다';

// 경로 응답에서 부분 하나 — undefined(첫 호출 전)·{ why }(호출 실패)·부분 객체 { state, code, fetchedAt, refreshSec, … }
function partOf(path, key) {
  const entry = got.get(path);
  if (!entry) return undefined;
  if (entry.why) return { why: entry.why };
  const part = key ? entry.body[key] : entry.body;
  return isObj(part) && typeof part.state === 'string' ? part : { why: '응답 모양 오류' };
}

// 값을 그릴 수 있는가 — ok, 또는 error 인데 값이 있다(Clarity 의 마지막 성공 값)
function usable(part, valueKey) {
  if (!part || part.why) return false;
  if (part.state === 'ok') return true;
  return part.state === 'error' && valueKey !== undefined && part[valueKey] != null;
}

// 머리의 경과 "4분 전 값" — 그 부분의 refreshSec × 3 을 넘으면 주의(refreshSec 0 은 보지 않는다, §3.3)
function age(part) {
  if (num(part.fetchedAt) === null) return el('span', 'muted', '값 시각 없음');
  const stale = num(part.refreshSec) > 0 && Date.now() - part.fetchedAt > part.refreshSec * 3000;
  return stale ? marked('warn', `${ago(part.fetchedAt)} 값 · 오래됨`) : el('span', 'muted', `${ago(part.fetchedAt)} 값`);
}

function head(part, valueKey) {
  if (part === undefined) return [badge('wait', '불러오는 중')];
  if (part.why) return [badge('bad', part.why)];
  const out = [];
  if (part.state === 'ok') out.push(age(part));
  else if (part.state === 'pending') out.push(badge('wait', '첫 조회 중'));
  else if (part.state === 'unconfigured') out.push(badge('dim', '연결 안 됨'));
  else if (part.state === 'denied') out.push(badge('dim', '권한 없음'));
  else if (usable(part, valueKey)) out.push(badge('bad', `불러오지 못함 · 마지막 성공 ${ago(part.fetchedAt)}`));
  else out.push(badge('bad', '불러오지 못함'));
  if (part.code) out.push(el('span', 'code', part.code));
  return out;
}

// 값을 못 그릴 때 본문 — error 에 값이 없으면 빈칸(배지가 말한다)
function stateMsg(part, cause) {
  if (part === undefined) return el('p', 'state-msg span-all', '… 불러오는 중');
  if (part.why) return el('p', 'state-msg t-bad span-all', `${part.why} — 이 칸의 값은 비웠다`);
  if (part.state === 'pending') return el('p', 'state-msg span-all', '… 첫 조회 중 — 다음 갱신에 찬다');
  if (part.state === 'unconfigured') return el('p', 'state-msg span-all', `연결 안 됨 — ${cause}`);
  if (part.state === 'denied') return el('p', 'state-msg span-all', `권한 없음 — ${DENIED}`);
  return el('p', 'state-msg span-all');
}

// 칸 하나 = 머리(m-이름) + 본문(b-이름). 값을 그렸으면 true
function region(name, part, cause, fill, valueKey) {
  $(`m-${name}`).replaceChildren(...head(part, valueKey));
  const body = $(`b-${name}`);
  if (usable(part, valueKey)) {
    fill(body, part);
    return true;
  }
  body.replaceChildren(stateMsg(part, cause));
  return false;
}

// 개요 칸·절 요약에 쓰는 짧은 상태 — 값을 못 그릴 때
function stateWord(part) {
  if (part === undefined) return ['wait', '불러오는 중'];
  if (part.why) return ['bad', '호출 실패'];
  if (part.state === 'pending') return ['wait', '첫 조회 중'];
  if (part.state === 'unconfigured') return ['dim', '연결 안 됨'];
  if (part.state === 'denied') return ['dim', '권한 없음'];
  return ['bad', '불러오지 못함'];
}

// --- 차트 (§3.6) — SVG 를 DOM 으로. 모양은 기하 속성, 색·굵기는 클래스 ----------------------------------

function svg(tag, attrs, cls) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
  if (cls) node.setAttribute('class', cls);
  return node;
}

function tip(node, text) {
  const title = svg('title');
  title.textContent = clean(text);
  node.prepend(title);
  return node;
}

function frame(w, h, cls, label) {
  const attrs = { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'none', role: 'img', 'aria-label': clean(label) };
  return svg('svg', attrs, `chart${cls ? ` ${cls}` : ''}`);
}

function axis(left, right) {
  const row = el('div', 'axis');
  row.append(el('span', null, left), el('span', null, right));
  return row;
}

// 점 [[ts초, 값|null], …] → 지금(마지막 값)·최저·최고·값 수
function stats(points) {
  const vals = list(points).map((p) => (Array.isArray(p) ? num(p[1]) : null)).filter((v) => v !== null);
  return { now: vals.length ? vals[vals.length - 1] : null, min: vals.length ? Math.min(...vals) : null, max: vals.length ? Math.max(...vals) : null, count: vals.length };
}

// 24시간 선 — 가로는 ts×1000 을 창에, null 에서 끊고(보간 없음) 비율은 0~100 고정·그 밖은 0~최댓값, 기준선은 점선
function line(points, o) {
  const s = stats(points);
  if (s.count < 2) return el('p', 'empty', '데이터 부족');
  const W = 288;
  const H = 48;
  const top = o.fixed100 ? 100 : Math.max(s.max, o.ref ?? 0) * 1.1 || 1;
  const x = (ts) => ((ts * 1000 - o.startMs) / (o.endMs - o.startMs)) * W;
  const y = (v) => H - (Math.min(Math.max(v, 0), top) / top) * H;
  const segs = [];
  let cur = null;
  for (const p of list(points)) {
    const v = Array.isArray(p) ? num(p[1]) : null;
    if (v === null || num(p[0]) === null) {
      cur = null;
      continue;
    }
    if (!cur) segs.push((cur = []));
    cur.push(`${x(p[0]).toFixed(1)} ${y(v).toFixed(1)}`);
  }
  // 앞뒤가 빈 점 하나는 길이 0 선 — 둥근 끝이 점으로 보인다
  const d = segs.map((seg) => `M${seg.join(' L')}${seg.length === 1 ? ' h0.1' : ''}`).join(' ');
  const fmt = o.format || ((v) => int(v));
  const label = `${o.label} 24시간 — ${o.extreme === 'min' ? `최저 ${fmt(s.min)}` : `최고 ${fmt(s.max)}`}, 지금 ${fmt(s.now)}`;
  const root = frame(W, H, '', label);
  root.append(svg('line', { x1: 0, x2: W, y1: H, y2: H }, 'floor'));
  if (o.ref !== undefined) {
    root.append(tip(svg('line', { x1: 0, x2: W, y1: y(o.ref), y2: y(o.ref) }, 'ref t-bad'), `경보 기준 ${fmt(o.ref)}`));
  }
  root.append(svg('path', { d }, 'line'));
  const out = document.createDocumentFragment();
  out.append(root, axis('24시간 전', '지금'));
  return out;
}

// 가로 비율 막대 하나(0~1)
function ratio(frac, tone, label) {
  const root = frame(100, 6, 'bar6', label);
  const w = Math.min(1, Math.max(0, num(frac) ?? 0)) * 100;
  root.append(svg('rect', { x: 0, y: 0, width: 100, height: 6 }, 'track'));
  root.append(svg('rect', { x: 0, y: 0, width: w.toFixed(2), height: 6 }, tone ? `fill t-${tone}` : 'fill'));
  return root;
}

// 세로 막대 — bars [{ value, over, tip }], over 는 같은 눈금으로 겹쳐 그린다(5xx)
function columns(bars, label, cls) {
  const W = bars.length * 10;
  const H = 60;
  const top = Math.max(1, ...bars.map((b) => b.value));
  const root = frame(W, H, cls, label);
  root.append(svg('line', { x1: 0, x2: W, y1: H, y2: H }, 'floor'));
  bars.forEach((b, i) => {
    const g = svg('g');
    const h = (b.value / top) * H;
    if (b.value > 0) g.append(svg('rect', { x: i * 10 + 1, y: H - h, width: 8, height: h }, 'fill'));
    if (b.over > 0) {
      const ho = Math.max(1.5, (b.over / top) * H);
      g.append(svg('rect', { x: i * 10 + 1, y: H - ho, width: 8, height: ho }, 'err'));
    }
    g.append(svg('rect', { x: i * 10, y: 0, width: 10, height: H }, 'hit'));
    root.append(tip(g, b.tip));
  });
  return root;
}

// --- 개요 (§3.4) -------------------------------------------------------------------------------

const bodyOf = (path) => {
  const entry = got.get(path);
  return entry && !entry.why ? entry.body : null;
};
const HEALTH_TONE = { ok: 'ok', starting: 'warn' }; // 그 밖(stale·redis_down)은 장애

// 종합 판정 — 위에서부터 먼저 맞는 것. unconfigured·denied·pending 과 판정 밖 부분의 error 는 넣지 않는다
function verdict() {
  const col = got.get(P.collector);
  const api = got.get(P.api);
  if (!col || !api) return { tone: 'wait', word: '확인 중', why: ['첫 조회를 기다린다'] };
  if (col.why || api.why) {
    const why = [col.why && `수집기 헬스 ${col.why}`, api.why && `api 헬스 ${api.why}`];
    return { tone: 'unknown', word: '알 수 없음', why: why.filter(Boolean) };
  }
  const bad = [];
  const warn = [];
  if (col.body.status !== 'ok') bad.push(`수집기 ${col.body.status}`);
  if (api.body.status !== 'ok') bad.push(`api ${api.body.status}`);
  const st = bodyOf(P.status);
  if (st?.redis === 'down') bad.push('Redis down');
  if (st?.influx === 'down') bad.push('Influx down');
  const alarms = partOf(P.aws, 'alarms');
  if (alarms?.state === 'ok') {
    const n = list(alarms.items).filter((a) => isObj(a) && a.state === 'ALARM').length;
    const count = Math.max(n, num(alarms.counts?.alarm) ?? 0);
    if (count > 0) bad.push(`경보 ${count}개 ALARM`);
  } else if (alarms?.state === 'error') warn.push('경보 읽기 오류');
  for (const ex of list(bodyOf(P.collect)?.exchanges).filter(isObj)) {
    if (ex.state === 'down') bad.push(`${ex.exchange} 끊김`);
    else if (ex.state === 'stale') warn.push(`${ex.exchange} 지연`);
  }
  const canary = partOf(P.aws, 'canary');
  if (canary?.state === 'ok' && canary.ok === false) warn.push('canary 실패');
  else if (canary?.state === 'error') warn.push('canary 읽기 오류');
  if (bad.length) return { tone: 'bad', word: '장애', why: [...bad, ...warn] };
  if (warn.length) return { tone: 'warn', word: '주의', why: warn };
  const judged = alarms?.state === 'ok' && canary?.state === 'ok';
  return { tone: 'ok', word: '정상', why: [judged ? '수집·api·경보·canary 이상 없음' : '수집·api 이상 없음 — 경보·canary 는 판정 밖(연결 안 됨·첫 조회)'] };
}

// 띠 오른쪽의 판정 재료 일곱 — 어느 칸이 판정에 들었고 어떤 상태인지 (판정 밖은 흐림)
function checks() {
  const health = (path) => {
    const e = got.get(path);
    return !e ? 'wait' : e.why ? 'unknown' : own(HEALTH_TONE, e.body.status) === 'ok' ? 'ok' : 'bad';
  };
  const st = got.get(P.status);
  const store = (key) => (!st ? 'wait' : st.why ? 'dim' : st.body[key] === 'down' ? 'bad' : 'ok');
  const exs = list(bodyOf(P.collect)?.exchanges).filter(isObj);
  const exTone = !got.get(P.collect) ? 'wait' : !exs.length ? 'dim' : exs.some((e) => e.state === 'down') ? 'bad' : exs.some((e) => e.state === 'stale') ? 'warn' : 'ok';
  const judged = (part, test) => (part === undefined ? 'wait' : part.why ? 'dim' : part.state === 'ok' ? test(part) : part.state === 'error' ? 'warn' : part.state === 'pending' ? 'wait' : 'dim');
  const alarms = judged(partOf(P.aws, 'alarms'), (a) => (list(a.items).some((i) => isObj(i) && i.state === 'ALARM') || num(a.counts?.alarm) > 0 ? 'bad' : 'ok'));
  const canary = judged(partOf(P.aws, 'canary'), (c) => (c.ok === false ? 'warn' : 'ok'));
  return [
    ['수집기', health(P.collector)],
    ['api', health(P.api)],
    ['Redis', store('redis')],
    ['Influx', store('influx')],
    ['거래소', exTone],
    ['경보', alarms],
    ['canary', canary],
  ];
}

const reasons = (why) => why.slice(0, 3).join(' · ') + (why.length > 3 ? ` 외 ${why.length - 3}` : '');

// 개요 칸 — word 면 값 자리에 상태 글(작게), 아니면 값(크게)
function vital(id, tone, value, sub, word = false) {
  const cls = [tone === 'ok' ? '' : `t-${tone}`, word ? 'state' : ''].join(' ').trim();
  $(`v-${id}`).replaceChildren(el('span', `mk t-${tone}`, GLYPH[tone]), el('span', cls || null, value));
  $(`v-${id}-s`).textContent = clean(sub);
}

function healthVital(id, entry, sub) {
  if (!entry) return vital(id, 'wait', '불러오는 중', '', true);
  if (entry.why) return vital(id, 'bad', '호출 실패', entry.why, true);
  const status = String(entry.body.status);
  vital(id, own(HEALTH_TONE, status) || 'bad', status === 'ok' ? '정상' : status, sub(entry.body));
}

function drawOverview() {
  const v = verdict();
  $('band').className = `band t-${v.tone}`;
  $('band-mark').textContent = GLYPH[v.tone];
  $('band-word').textContent = v.word;
  $('band-why').textContent = clean(reasons(v.why));
  $('band-checks').replaceChildren(...checks().map(([label, tone]) => marked(tone, label)));
  const hdr = $('hdr-verdict');
  hdr.className = `tag t-${v.tone}`;
  hdr.replaceChildren(el('span', 'mk', GLYPH[v.tone]), v.word);
  hdr.title = clean(reasons(v.why));

  healthVital('collector', got.get(P.collector), (b) => `마지막 틱 ${ago(b.lastTickAt)}`);
  const st = got.get(P.status);
  healthVital('api', got.get(P.api), () => (st?.body ? `Redis ${st.body.redis} · Influx ${st.body.influx}` : st?.why || ''));
  const apiTile = got.get(P.api);
  if (apiTile?.body?.status === 'ok' && st?.body && (st.body.redis !== 'ok' || st.body.influx !== 'ok')) {
    vital('api', 'bad', '저장소 끊김', `Redis ${st.body.redis} · Influx ${st.body.influx}`, true);
  }
  if (!st) vital('ws', 'wait', '불러오는 중', '', true);
  else if (st.why) vital('ws', 'bad', '호출 실패', st.why, true);
  else vital('ws', 'ok', int(st.body.wsConnections), '열린 대시보드 수');

  const alarms = partOf(P.aws, 'alarms');
  if (usable(alarms)) {
    const items = list(alarms.items).filter(isObj);
    const n = items.filter((a) => a.state === 'ALARM').length;
    const nodata = items.filter((a) => a.state === 'INSUFFICIENT_DATA').length;
    vital('alarms', n ? 'bad' : 'ok', `${n} / ${items.length}`, `ALARM / 전체${nodata ? ` · 데이터 부족 ${nodata}` : ''}`);
  } else vital('alarms', ...stateWord(alarms), alarms?.why || alarms?.code || '', true);

  const canary = partOf(P.aws, 'canary');
  if (usable(canary)) {
    if (num(canary.lastRunAt) === null) vital('canary', 'dim', '실행 없음', '11분 안에 끝난 실행 없음', true);
    else vital('canary', canary.ok ? 'ok' : 'warn', canary.ok ? '통과' : '실패', `${ago(canary.lastRunAt)} · ${int(canary.durationMs)}ms`);
  } else vital('canary', ...stateWord(canary), canary?.why || canary?.code || '', true);

  const budget = partOf(P.aws, 'budget');
  if (usable(budget)) {
    const worst = worstMonthly(budget);
    if (!worst) vital('cost', 'dim', '예산 없음', '월 단위 비용 예산이 없다', true);
    else vital('cost', worst.tone, money(worst.b.actual, worst.b.unit), `한도 ${money(worst.b.limit, worst.b.unit)} 중 ${fixed(worst.r * 100, 0)}%`);
  } else vital('cost', ...stateWord(budget), budget?.why || budget?.code || '', true);
}

// --- 수집 (§3.4) -------------------------------------------------------------------------------

const EXCHANGES = ['upbit', 'bithumb', 'binance', 'bybit', 'bitget'];
const EX_STATE = { ok: ['ok', '수집 중'], stale: ['warn', '지연'], down: ['bad', '끊김'] };
const kindTone = (kind) => (kind === 'banned' || kind === 'rate_limit' ? 'bad' : 'warn');

function lasting(ms) {
  const s = since(ms);
  return s < 60 ? `${s}초째` : s < 3600 ? `${Math.floor(s / 60)}분째` : `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분째`;
}

function cell(tr, content, cls) {
  const td = el('td', cls);
  if (content instanceof Node) td.append(content);
  else td.textContent = clean(content);
  tr.append(td);
  return td;
}

function exchangeRow(ex) {
  const tr = el('tr');
  const [tone, word] = own(EX_STATE, ex.state) || ['bad', String(ex.state)];
  cell(tr, ex.exchange, 'nowrap');
  cell(tr, badge(tone, word));
  cell(tr, ago(ex.lastSuccessAt), 'num');
  cell(tr, int(ex.markets), 'num');
  cell(tr, num(ex.successRate1h) === null ? '–' : `${ex.successRate1h.toFixed(2)}%`, 'num');
  const o = ex.openOutage;
  cell(tr, isObj(o) ? marked(kindTone(o.kind), `${o.kind} · ${int(o.count)}회 · ${lasting(o.startedAt)}`) : '–', 'nowrap');
  const e = ex.lastError;
  const last = cell(tr, isObj(e) ? '' : '–', 'small last-error');
  if (isObj(e)) {
    const head = `${num(e.at) === null ? '' : `${md(e.at)} ${clock(e.at)}`} · ${e.kind} · HTTP ${e.statusCode ?? '–'} · `;
    const text = clip(el('span', 'clamp'), `${head}${clean(e.message)}`, head.length + 300);
    text.title = clean(e.message);
    last.append(text);
  }
  return tr;
}

// 실패 구간 타임라인 — 거래소 다섯 줄, 진행 중은 지금까지, 1분 미만도 최소 폭
function timeline(outages) {
  if (!outages.length) return el('p', 'empty', '최근 24시간 실패 없음');
  const now = Date.now();
  const start = now - 86_400_000;
  const W = 1000;
  const lanes = el('div', 'lanes');
  for (const ex of EXCHANGES) {
    const mine = outages.filter((o) => o.exchange === ex && num(o.startedAt) !== null);
    const root = frame(W, 14, 'lane', `${ex} 실패 구간 24시간 — ${mine.length}건`);
    root.append(svg('rect', { x: 0, y: 4, width: W, height: 6 }, 'track'));
    for (const o of mine) {
      const s = Math.max(start, o.startedAt);
      const e = Math.min(now, num(o.endedAt) ?? now);
      if (e < start) continue;
      const w = Math.max(4, ((e - s) / (now - start)) * W);
      const x = Math.min(W - w, ((s - start) / (now - start)) * W);
      const span = `${hm(o.startedAt)}–${num(o.endedAt) === null ? '진행 중' : hm(o.endedAt)} · ${o.kind} · ×${int(o.count)}`;
      root.append(tip(svg('rect', { x: x.toFixed(1), y: 1, width: w.toFixed(1), height: 12 }, `seg t-${kindTone(o.kind)}`), span));
    }
    lanes.append(el('span', 'lane-name', ex), root);
  }
  lanes.append(axis('24시간 전', '지금'));
  return lanes;
}

function drawCollect() {
  const entry = got.get(P.collect);
  if (!entry || entry.why) {
    const part = entry ? { why: entry.why } : undefined;
    $('m-collect').replaceChildren(...head(part));
    $('exchanges').replaceChildren();
    $('collect-sum').replaceChildren(stateMsg(part));
    $('timeline').replaceChildren();
    $('s-collect').replaceChildren(entry ? marked('bad', entry.why) : marked('wait', '불러오는 중'));
    return;
  }
  const b = entry.body;
  $('m-collect').replaceChildren(el('span', 'muted', `${ago(b.fetchedAt)} 값`));
  const exchanges = list(b.exchanges).filter(isObj);
  $('exchanges').replaceChildren(...exchanges.map(exchangeRow));
  const markets = exchanges.reduce((sum, ex) => sum + (num(ex.markets) ?? 0), 0);
  const colVer = bodyOf(P.collector)?.version;
  const apiVer = bodyOf(P.status)?.version;
  const parts = [
    ['전체 1시간', num(b.successRate1h) === null ? '–' : `${b.successRate1h.toFixed(2)}%`],
    ['마켓', int(markets)],
    ['수집기 시작', num(b.serverStartedAt) === null ? '–' : `${md(b.serverStartedAt)} ${hm(b.serverStartedAt)}`],
    ['버전', `수집기 ${colVer ?? '–'} · api ${apiVer ?? '–'}`],
  ];
  $('collect-sum').replaceChildren(
    ...parts.map(([k, v]) => {
      const span = el('span', null, `${k} `);
      span.append(el('b', null, v));
      return span;
    }),
  );
  $('timeline').replaceChildren(timeline(list(b.outages).filter(isObj)));
  const down = exchanges.filter((ex) => ex.state === 'down').map((ex) => ex.exchange);
  const stale = exchanges.filter((ex) => ex.state === 'stale').map((ex) => ex.exchange);
  const tone = down.length ? 'bad' : stale.length ? 'warn' : 'ok';
  const text = down.length || stale.length
    ? [down.length && `끊김 ${down.join(', ')}`, stale.length && `지연 ${stale.join(', ')}`].filter(Boolean).join(' · ')
    : `${exchanges.length}곳 모두 수집 중`;
  $('s-collect').replaceChildren(marked(tone, `${text} · 1시간 ${parts[0][1]}`));
}

function put(id, text, tone) {
  const node = $(id);
  node.textContent = clean(text);
  node.className = tone ? `t-${tone}` : '';
}

// 003 POST /refresh — 토큰이 틀리면 401 {"detail"} (새로고침하지 않는다)
$('refresh').addEventListener('click', async () => {
  const button = $('refresh');
  const token = $('token').value;
  button.disabled = true;
  try {
    const { status, body: res } = await call('/api/refresh', {
      method: 'POST',
      headers: token ? { 'X-Refresh-Token': token } : {},
    });
    $('refresh-result').hidden = false;
    const label = status === 401 ? ' 토큰 오류' : status === 403 ? ' 권한·설정 오류' : '';
    put('refresh-status', `${status}${label}`, status === 200 ? 'ok' : 'bad');
    const ok = status === 200 && res !== null;
    put('refresh-saved', ok ? String(res.totalSaved) : '-');
    const failures = ok ? list(res.failures).filter(isObj) : [];
    put('refresh-failures', failures.map((f) => `${f.exchange} · ${f.errorCode}`).join(', ') || '없음');
    const warnings = ok ? list(res.warnings) : [];
    put('refresh-warnings', warnings.map(String).join(' / ') || '없음');
  } catch (err) {
    // 버튼은 만료 표시를 지우지 않는다(지우는 것은 폴링 묶음뿐) — 만료 신호면 새로고침 한 번 또는 알림
    if (err instanceof Expired) expired();
    $('refresh-result').hidden = false;
    put('refresh-status', err instanceof Expired ? '로그인 만료·연결 끊김' : '보내지 못함', 'bad');
    for (const id of ['refresh-saved', 'refresh-failures', 'refresh-warnings']) put(id, '-');
  } finally {
    button.disabled = false;
  }
});

