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

// --- 인프라 (§3.4) — metrics·alarms·canary ----------------------------------------------------------

const BOX_ROLE = { collect: '수집기', data: 'Influx·Redis', serve: 'caddy·web·api' };
// 027 크레딧 잔고 경보 임계(최대 적립의 30%) — t4g 박스만
const CREDIT_FLOOR = { data: 173, serve: 86 };
const pctFmt = (v) => (num(v) === null ? '–' : `${v.toFixed(1)}%`);

// 지표 한 줄 — 이름·지금 값·24시간 최저 또는 최고(값 색은 임계 — 모양도 함께)·선
function metricRow(box, name, points, o) {
  const s = stats(points);
  const fmt = o.format || ((v) => int(v));
  const toned = (v) => {
    const tone = v === null || !o.tone ? null : o.tone(v);
    return tone ? marked(tone, fmt(v)) : el('span', null, fmt(v));
  };
  const row = el('div', 'metric');
  const val = el('span', 'metric-val');
  val.append(toned(s.now));
  const ext = el('span', 'metric-ext', `24시간 ${o.extreme === 'min' ? '최저' : '최고'} `);
  ext.append(toned(o.extreme === 'min' ? s.min : s.max));
  if (o.ref !== undefined) ext.append(` · 경보 기준 ${fmt(o.ref)}`);
  // 박스 이름은 아는 셋만 aria-label 에(서버 글은 title 밖의 속성에 쓰지 않는다, §3.8)
  const who = own(BOX_ROLE, box) || box === 'canary' ? box : '박스';
  row.append(el('span', 'metric-name', name), val, ext, line(points, { ...o, format: fmt, label: `${who} ${name}` }));
  return row;
}

function boxCard(box, where) {
  const card = el('div', 'card');
  const title = el('h3', null, `${box.box} ${own(BOX_ROLE, box.box) ?? ''}`.trim());
  if (box.instanceId) title.title = clean(box.instanceId);
  const top = el('div', 'card-head');
  top.append(title);
  card.append(top);
  const pctRow = (name, key, extra) => metricRow(box.box, name, box[key], { ...where, fixed100: true, format: pctFmt, ...extra });
  card.append(
    pctRow('메모리 가용률', 'mem', { ref: 10, extreme: 'min', tone: (v) => (v < 10 ? 'bad' : v < 20 ? 'warn' : null) }),
    pctRow('디스크 사용률', 'disk', { ref: 80, extreme: 'max', tone: (v) => (v > 80 ? 'bad' : v > 70 ? 'warn' : null) }),
    pctRow('CPU 사용률', 'cpu', { extreme: 'max' }),
  );
  const floor = own(CREDIT_FLOOR, box.box);
  if (floor !== undefined) {
    card.append(metricRow(box.box, 'CPU 크레딧 잔고', box.credit, { ...where, ref: floor, extreme: 'min', tone: (v) => (v < floor ? 'bad' : null) }));
  }
  if (box.swap != null) card.append(pctRow('스왑 사용률', 'swap', { extreme: 'max' }));
  else card.append(el('p', 'muted small', '스왑 지표 없음'));
  return card;
}

function fillBoxes(node, part) {
  const where = { startMs: part.startTs * 1000, endMs: part.endTs * 1000 };
  const boxes = list(part.boxes).filter(isObj);
  if (!boxes.length) return node.replaceChildren(el('p', 'empty span-all', '메모리 경보가 있는 박스 없음'));
  node.replaceChildren(...boxes.map((b) => boxCard(b, where)));
}

const ALARM_RANK = { ALARM: 0, INSUFFICIENT_DATA: 1, OK: 2 };
const ALARM_STATE = { ALARM: ['bad', 'ALARM'], INSUFFICIENT_DATA: ['dim', '데이터 부족'], OK: ['ok', 'OK'] };
const alarmName = (name) => clean(name).replace(/^marketlens-/, '');

function alarmRow(a) {
  const tr = el('tr');
  const name = cell(tr, alarmName(a.name), 'nowrap');
  name.title = clean(a.name);
  cell(tr, badge(...(own(ALARM_STATE, a.state) || ['dim', String(a.state)])));
  cell(tr, ago(a.changedAt), 'num small');
  clip(cell(tr, '', 'small muted'), a.reason ?? '', 160);
  return tr;
}

function fillAlarms(node, part) {
  const items = list(part.items).filter(isObj);
  items.sort((a, b) => (own(ALARM_RANK, a.state) ?? 1) - (own(ALARM_RANK, b.state) ?? 1) || clean(a.name).localeCompare(clean(b.name)));
  const hot = items.filter((a) => a.state !== 'OK');
  const calm = items.filter((a) => a.state === 'OK');
  // OK 행은 접힌 묶음 — 펼침 상태는 HTML 의 details 가 들고 있어 다시 그려도 그대로다
  $('alarms-ok-rows').replaceChildren(...calm.map(alarmRow));
  $('alarms-ok-sum').textContent = `정상 ${calm.length}개`;
  $('alarms-ok').hidden = !calm.length;
  if (!items.length) return node.replaceChildren(el('p', 'empty', '경보 없음'));
  if (!hot.length) return node.replaceChildren(el('p', 'empty', `ALARM·데이터 부족 경보 없음 — ${items.length}개 모두 정상`));
  const wrap = el('div', 'scroll');
  const table = el('table', 'table');
  const tbody = el('tbody');
  tbody.append(...hot.map(alarmRow));
  table.append(tbody);
  wrap.append(table);
  node.replaceChildren(wrap);
}

const sum = (points) => list(points).reduce((acc, p) => acc + (Array.isArray(p) ? num(p[1]) ?? 0 : 0), 0);

function fillCanary(node, part, metrics) {
  const out = [];
  const last = el('p', 'summary-row');
  if (num(part.lastRunAt) === null) last.append(marked('dim', '11분 안에 끝난 실행 없음'));
  else {
    last.append(marked(part.ok ? 'ok' : 'warn', part.ok ? '최근 실행 통과' : '최근 실행 실패'));
    last.append(el('span', null, `${ago(part.lastRunAt)} · ${int(part.durationMs)}ms`));
  }
  out.push(last);
  if (usable(metrics)) {
    const c = isObj(metrics.canary) ? metrics.canary : {};
    const runs = el('p', 'summary-row');
    const errors = sum(c.errors);
    runs.append(el('span', null, `24시간 실행 ${int(sum(c.runs))}`), errors ? marked('warn', `오류 ${int(errors)}`) : el('span', null, '오류 0'));
    const where = { startMs: metrics.startTs * 1000, endMs: metrics.endTs * 1000 };
    out.push(runs, metricRow('canary', '실행 시간 (5분 최댓값)', c.durationMs, { ...where, extreme: 'max', format: (v) => `${int(v)}ms` }));
  } else {
    const [tone, word] = stateWord(metrics);
    out.push(marked(tone, `24시간 지표 ${word}`));
  }
  const lines = list(part.lines).slice(0, 10);
  if (lines.length) {
    const log = el('ol', 'mono log');
    log.append(...lines.map((ln) => clip(el('li'), ln, 300)));
    out.push(log);
  }
  node.replaceChildren(...out);
}

function drawInfra() {
  const metrics = partOf(P.aws, 'metrics');
  region('metrics', metrics, CAUSE.aws, fillBoxes);
  const alarms = partOf(P.aws, 'alarms');
  if (!region('alarms', alarms, CAUSE.aws, fillAlarms)) $('alarms-ok').hidden = true;
  const canary = partOf(P.aws, 'canary');
  region('canary', canary, CAUSE.aws, (node, part) => fillCanary(node, part, metrics));
  const bits = [];
  let tone = 'ok';
  if (usable(alarms)) {
    const items = list(alarms.items).filter(isObj);
    const n = items.filter((a) => a.state === 'ALARM').length;
    if (n) tone = 'bad';
    bits.push(n ? `경보 ${n}개 ALARM / ${items.length}` : `경보 ${items.length}개 ALARM 없음`);
  } else {
    const [t, w] = stateWord(alarms);
    tone = t === 'bad' ? 'warn' : t;
    bits.push(`경보 ${w}`);
  }
  if (usable(canary) && num(canary.lastRunAt) !== null) {
    if (!canary.ok && tone === 'ok') tone = 'warn';
    bits.push(`canary ${canary.ok ? '통과' : '실패'} ${ago(canary.lastRunAt)}`);
  } else if (!usable(canary)) bits.push(`canary ${stateWord(canary)[1]}`);
  $('s-infra').replaceChildren(marked(tone, bits.join(' · ')));
}

// --- 알림 (§3.4) — 보낸 Slack 알림 + 경보 상태 변경 ------------------------------------------------------

const TO_TONE = { ALARM: 'bad', OK: 'ok', INSUFFICIENT_DATA: 'dim' };
const MAX_ALERTS = 200;

function when(ms) {
  if (num(ms) === null) return '–';
  const today = new Date().toDateString() === new Date(ms).toDateString();
  return today ? clock(ms) : `${md(ms)} ${hm(ms)}`;
}

// Slack 글의 머리 그림 → 색 (025 문구 규칙)
function slackTone(text) {
  if (text.startsWith('🔴')) return 'bad';
  if (text.startsWith('🟢')) return 'ok';
  if (text.startsWith('⚠')) return 'warn';
  return null;
}

function alertRow(item) {
  const li = el('li');
  li.append(el('span', 'when', when(item.at)));
  const what = el('span', 'what');
  if (item.source === 'alarm') {
    li.append(el('span', 'tag', '경보'));
    const name = el('span', null, `${alarmName(item.alarm)} ${clean(item.fromState ?? '?')} → `);
    name.title = clean(item.alarm);
    const to = String(item.toState ?? '?');
    what.append(name, marked(own(TO_TONE, to) || 'dim', to));
    if (item.text) what.append(clip(el('span', 'why'), item.text, 160));
  } else {
    li.append(el('span', 'tag', item.role ?? 'slack'));
    const text = clean(item.text);
    const tone = slackTone(text);
    what.append(clip(el('span', tone ? `t-${tone}` : null), text, 300));
    if (item.delivered === false) what.append(' ', badge('warn', '전송 실패'));
    if (item.key) li.title = clean(item.key);
  }
  li.append(what);
  return li;
}

function sourceState(label, part, cause) {
  const span = el('span', null, `${label} `);
  if (part && !part.why && part.state === 'ok') span.append(el('span', 'muted', 'ok'));
  else {
    const [tone, word] = stateWord(part);
    const tag = badge(tone, word);
    if (part?.state === 'unconfigured') tag.title = cause;
    if (part?.state === 'denied') tag.title = DENIED;
    span.append(tag);
    if (part?.code) span.append(el('span', 'code', ` ${clean(part.code)}`));
  }
  return span;
}

function drawAlerts() {
  const entry = got.get(P.alerts);
  if (!entry || entry.why) {
    const part = entry ? { why: entry.why } : undefined;
    $('m-alerts').replaceChildren(...head(part));
    $('b-alerts').replaceChildren(stateMsg(part));
    $('alerts-count').textContent = '';
    $('s-alerts').replaceChildren(entry ? marked('bad', entry.why) : marked('wait', '불러오는 중'));
    return;
  }
  const b = entry.body;
  const slackPart = isObj(b.slack) ? b.slack : { why: '응답 모양 오류' };
  const alarmPart = isObj(b.alarms) ? b.alarms : { why: '응답 모양 오류' };
  $('m-alerts').replaceChildren(sourceState('Slack', slackPart, CAUSE.slack), sourceState('경보 이력', alarmPart, CAUSE.aws));
  const items = list(b.items).filter(isObj).slice(0, MAX_ALERTS);
  const shown = alertFilter === 'all' ? items : items.filter((i) => i.source === alertFilter);
  $('alerts-count').textContent = `${items.length}건 · 보낸 Slack 알림(전송 실패 포함)과 경보 상태 변경 — 억제된 알림은 없다`;
  if (!shown.length) $('b-alerts').replaceChildren(el('p', 'empty', '지난 7일 기록 없음'));
  else {
    const feed = el('ul', 'feed');
    feed.append(...shown.map(alertRow));
    $('b-alerts').replaceChildren(feed);
  }
  const failed = items.filter((i) => i.source === 'slack' && i.delivered === false).length;
  const changes = items.filter((i) => i.source === 'alarm').length;
  const text = `7일 ${items.length}건 · 경보 상태 변경 ${changes}${failed ? ` · 전송 실패 ${failed}` : ''}`;
  $('s-alerts').replaceChildren(marked(failed ? 'warn' : 'ok', text));
}

for (const chip of document.querySelectorAll('.chip')) {
  chip.addEventListener('click', () => {
    alertFilter = chip.dataset.filter;
    for (const other of document.querySelectorAll('.chip')) other.setAttribute('aria-pressed', String(other === chip));
    drawAlerts();
  });
}

// --- 접속 (§3.4) — 실시간·서버 기록 24시간·Clarity ---------------------------------------------------

const TOP_TABLES = [
  ['paths', '경로'],
  ['tabs', '탭'],
  ['referrers', '외부 출처'],
  ['utmSources', 'utm_source'],
  ['devices', '기기'],
  ['browsers', '브라우저'],
];
const WS_BUCKETS = [
  ['lt10s', '10초 미만'],
  ['lt1m', '1분 미만'],
  ['lt10m', '10분 미만'],
  ['lt1h', '1시간 미만'],
  ['ge1h', '1시간 이상'],
];
const share = (n, total) => (total > 0 ? n / total : 0);

function stat(label, value, tone) {
  const box = el('div', 'stat');
  box.append(el('span', 'stat-label', label), tone ? marked(tone, value) : el('span', 'stat-val', value));
  if (tone) box.lastChild.classList.add('stat-val');
  return box;
}

// 상위 목록 표 — 이름(방문자가 정한 글자 — 글자로만)·수·페이지 대비 비율 막대, 상위 10
function topTable(rows, label, pages) {
  const card = el('div', 'card');
  card.append(el('span', 'card-kicker', label));
  const top = list(rows).filter((r) => Array.isArray(r)).slice(0, 10);
  if (!top.length) {
    card.append(el('p', 'empty', '지난 24시간 기록 없음'));
    return card;
  }
  const table = el('table', 'table top-table');
  const tbody = el('tbody');
  for (const [name, n] of top) {
    const tr = el('tr');
    clip(cell(tr, ''), name, 60);
    cell(tr, int(n), 'num');
    const r = share(num(n) ?? 0, pages);
    const td = cell(tr, `${(r * 100).toFixed(1)}%`, 'num share');
    td.append(ratio(r, null, `페이지의 ${(r * 100).toFixed(1)}%`));
    tbody.append(tr);
  }
  table.append(tbody);
  const wrap = el('div', 'scroll');
  wrap.append(table);
  card.append(wrap);
  return card;
}

function fillAccess(node, a) {
  const totals = isObj(a.totals) ? a.totals : {};
  const sub = [`폴링·canary 제외, IP 없음 · 읽지 못한 줄 ${int(totals.skipped)}`];
  if (num(a.firstTs) !== null && num(a.startTs) !== null && a.firstTs > a.startTs) sub.push(`기록 시작 ${hm(a.firstTs * 1000)}`);
  $('access-sub').textContent = sub.join(' · ');
  const status = isObj(a.status) ? a.status : {};
  const stats = el('div', 'stats');
  stats.append(stat('총 요청', int(totals.requests)), stat('페이지', int(totals.pages)), stat('5xx', int(status['5xx']), num(status['5xx']) ? 'bad' : null));
  const hourly = list(a.hourly).filter(isObj);
  const bars = hourly.map((h) => ({
    value: num(h.requests) ?? 0,
    over: num(h.errors) ?? 0,
    tip: `${hm(h.ts * 1000)} · 요청 ${int(h.requests)} · 페이지 ${int(h.pages)} · 5xx ${int(h.errors)}`,
  }));
  const peak = Math.max(0, ...bars.map((b) => b.value));
  const chart = hourly.length ? columns(bars, `시간대별 요청 24시간 — 최고 ${int(peak)}, 5xx 는 겹쳐 표시`, 'tall') : el('p', 'empty', '데이터 부족');
  const codes = el('table', 'table');
  const tbody = el('tbody');
  for (const key of ['2xx', '3xx', '4xx', '5xx']) {
    const tr = el('tr');
    const n = num(status[key]) ?? 0;
    cell(tr, key === '5xx' && n ? marked('bad', key) : key);
    cell(tr, int(n), 'num');
    const r = share(n, num(totals.requests) ?? 0);
    cell(tr, `${(r * 100).toFixed(1)}%`, 'num share').append(ratio(r, key === '5xx' ? 'bad' : null, `${key} 요청의 ${(r * 100).toFixed(1)}%`));
    tbody.append(tr);
  }
  codes.append(tbody);
  const ws = isObj(a.ws) ? a.ws : {};
  const durations = isObj(ws.durations) ? ws.durations : {};
  const wsBars = WS_BUCKETS.map(([k, label]) => ({ value: num(durations[k]) ?? 0, over: 0, tip: `${label} · ${int(durations[k])}` }));
  const labels = el('div', 'cols5');
  labels.append(...WS_BUCKETS.map(([, label]) => el('span', null, label)));
  const wsHead = el('p', 'summary-row');
  wsHead.append(el('span', null, `WebSocket 연결 ${int(ws.count)} · 지속 시간`));
  node.replaceChildren(stats, chart, axis(hourly.length ? hm(hourly[0].ts * 1000) : '', '지금'), codes, wsHead, columns(wsBars, `WebSocket 지속 시간 구간 — 연결 ${int(ws.count)}`), labels);
  const pages = num(totals.pages) ?? 0;
  $('access-tables').replaceChildren(...TOP_TABLES.map(([k, label]) => topTable(a[k], label, pages)));
  const recent = list(a.recent5xx).filter(isObj).slice(0, 20);
  $('r5xx').hidden = false;
  $('r5xx-sum').textContent = recent.length ? `최근 5xx ${recent.length}건` : '최근 5xx 없음';
  $('r5xx-rows').replaceChildren(
    ...recent.map((r) => {
      const tr = el('tr');
      cell(tr, num(r.ts) === null ? '–' : when(r.ts * 1000), 'nowrap');
      clip(cell(tr, ''), r.path, 120);
      cell(tr, marked('bad', String(r.status)), 'num');
      return tr;
    }),
  );
}

// Clarity 행 — 키·값을 글자 한 줄로(받은 이름·키 그대로 — 035 가 정규화하지 않았다)
function clarityText(row) {
  const show = (v) => (isObj(v) || Array.isArray(v) ? JSON.stringify(v) : String(v));
  return isObj(row) ? Object.entries(row).map(([k, v]) => `${k}:${show(v)}`).join(' · ') : show(row);
}

function fillClarity(node, c) {
  const t = isObj(c.traffic) ? c.traffic : {};
  const tiles = el('div', 'clarity-tiles');
  for (const [label, v] of [
    ['세션', int(t.sessions)],
    ['봇 세션', int(t.botSessions)],
    ['사용자', int(t.users)],
    ['세션당 페이지', fixed(t.pagesPerSession, 2)],
  ]) {
    tiles.append(stat(label, v));
  }
  const next = el('p', 'muted small', num(c.nextAt) === null ? '' : `다음 조회 ${when(c.nextAt)} 이후 — 하루 10회 한도라 3시간 간격`);
  node.replaceChildren(tiles, next);
  const metrics = list(c.metrics).filter(isObj);
  $('clarity-more').hidden = false;
  $('clarity-more-sum').textContent = metrics.length ? `받은 지표 ${metrics.length}개` : '받은 지표 없음';
  $('clarity-metrics').replaceChildren(
    ...metrics.map((m) => {
      const box = el('div');
      const rows = el('ol');
      rows.append(...list(m.rows).slice(0, 20).map((r) => clip(el('li'), clarityText(r), 200)));
      box.append(clip(el('h4'), m.name, 120), list(m.rows).length ? rows : el('p', 'empty', '행 없음'));
      return box;
    }),
  );
}

function drawTraffic() {
  const st = got.get(P.status);
  $('ws-now').textContent = !st ? '…' : st.why ? '–' : int(st.body.wsConnections);
  const metrics = partOf(P.aws, 'metrics');
  region('ws24', metrics, CAUSE.aws, (node, m) => {
    const where = { startMs: m.startTs * 1000, endMs: m.endTs * 1000 };
    node.replaceChildren(metricRow('serve', 'WebSocket 접속 수 (5분 최댓값)', m.wsClients, { ...where, extreme: 'max' }));
  });
  const access = partOf(P.access);
  if (!region('access', access, CAUSE.access, fillAccess)) {
    $('access-sub').textContent = '';
    $('access-tables').replaceChildren();
    $('r5xx').hidden = true;
  }
  const clarity = partOf(P.clarity);
  if (!region('clarity', clarity, CAUSE.clarity, fillClarity, 'numOfDays')) $('clarity-more').hidden = true;
  const bits = [`지금 ${st?.body ? int(st.body.wsConnections) : '–'} 접속`];
  let tone = 'ok';
  if (usable(access)) {
    const fives = num(access.status?.['5xx']) ?? 0;
    bits.push(`24시간 페이지 ${int(access.totals?.pages)}`);
    if (fives) {
      tone = 'warn';
      bits.push(`5xx ${int(fives)}`);
    }
  } else {
    const [t, w] = stateWord(access);
    tone = t;
    bits.push(`서버 기록 ${w}`);
  }
  $('s-traffic').replaceChildren(marked(st ? tone : 'wait', bits.join(' · ')));
}

// --- 비용 (§3.4) -------------------------------------------------------------------------------

function money(v, unit) {
  if (num(v) === null) return '–';
  return unit && unit !== 'USD' ? `${v.toFixed(2)} ${clean(unit)}` : `$${v.toFixed(2)}`;
}

// 월 예산 중 한도 대비 실제 비율이 가장 큰 것 — 개요 칸·절 요약
function worstMonthly(part) {
  let worst = null;
  for (const b of list(part.items).filter(isObj)) {
    if (b.timeUnit !== 'MONTHLY' || !(num(b.limit) > 0) || num(b.actual) === null) continue;
    const r = b.actual / b.limit;
    if (!worst || r > worst.r) worst = { b, r };
  }
  if (worst) worst.tone = worst.r >= 1 ? 'bad' : worst.r >= 0.85 || (num(worst.b.forecast) ?? 0) > worst.b.limit ? 'warn' : 'ok';
  return worst;
}

// 이번 달(UTC — AWS 예산의 달)이 지난 비율
function monthElapsed() {
  const now = new Date();
  const start = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1);
  const end = Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1);
  return (Date.now() - start) / (end - start);
}

function budgetBar(b) {
  const top = Math.max(b.limit * 1.15, b.actual ?? 0, b.forecast ?? 0);
  const x = (v) => ((v / top) * 100).toFixed(2);
  const r = (b.actual ?? 0) / b.limit;
  const elapsed = monthElapsed();
  // 이름은 서버 글이라 aria-label 에 싣지 않는다(§3.8 — title 밖의 속성 금지). 같은 줄의 이름이 말한다
  const label = `예산 막대 — 실제 ${money(b.actual, b.unit)}, 한도의 ${(r * 100).toFixed(0)}%, 이번 달 ${(elapsed * 100).toFixed(0)}% 지남`;
  const root = frame(100, 14, 'bar12', label);
  root.append(svg('rect', { x: 0, y: 3, width: 100, height: 8 }, 'track'));
  root.append(tip(svg('rect', { x: 0, y: 3, width: x(b.actual ?? 0), height: 8 }, r >= 1 ? 'fill t-bad' : r >= 0.85 ? 'fill t-warn' : 'fill'), `실제 ${money(b.actual, b.unit)}`));
  const mark = (v, cls, text) => root.append(tip(svg('line', { x1: x(v), x2: x(v), y1: 0, y2: 14 }, cls), text));
  mark(b.limit * 0.85, 'mark', `한도 85% ${money(b.limit * 0.85, b.unit)} — 027 예산 알림`);
  mark(b.limit, 'mark t-bad', `한도 ${money(b.limit, b.unit)} — 027 예산 알림`);
  mark(top * elapsed, 'mark now', `이번 달 ${(elapsed * 100).toFixed(0)}% 지남`);
  return root;
}

function budgetRow(b) {
  const row = el('div', 'budget');
  const name = el('span', 'budget-name', b.name ?? '이름 없음');
  if (b.timeUnit && b.timeUnit !== 'MONTHLY') name.append(el('span', 'muted small', ` · ${clean(b.timeUnit)}`));
  const nums = el('span', 'budget-nums', `실제 ${money(b.actual, b.unit)} / 한도 ${money(b.limit, b.unit)}`);
  const monthly = b.timeUnit === 'MONTHLY';
  if (monthly) {
    const over = num(b.forecast) !== null && num(b.limit) !== null && b.forecast > b.limit;
    nums.append(' · ', over ? marked('warn', `예측 ${money(b.forecast, b.unit)} 한도 넘음`) : el('span', null, `예측 ${money(b.forecast, b.unit)}`));
  }
  row.append(name, nums);
  if (monthly && num(b.limit) > 0) {
    row.append(budgetBar(b), axis('0', `점선 = 이번 달 ${(monthElapsed() * 100).toFixed(0)}% 지남 · 세로선 = 한도 85%·100%`));
  }
  return row;
}

function drawCost() {
  const budget = partOf(P.aws, 'budget');
  region('budget', budget, CAUSE.aws, (node, part) => {
    const items = list(part.items).filter(isObj);
    node.replaceChildren(...(items.length ? items.map(budgetRow) : [el('p', 'empty', '예산 없음')]));
  });
  if (!usable(budget)) return $('s-cost').replaceChildren(marked(...stateWord(budget)));
  const worst = worstMonthly(budget);
  if (!worst) return $('s-cost').replaceChildren(marked('dim', '월 단위 예산 없음'));
  const fc = num(worst.b.forecast) === null ? '' : ` · 예측 ${money(worst.b.forecast, worst.b.unit)}`;
  $('s-cost').replaceChildren(marked(worst.tone, `이번 달 ${money(worst.b.actual, worst.b.unit)} / ${money(worst.b.limit, worst.b.unit)} (${(worst.r * 100).toFixed(0)}%)${fc}`));
}

// --- 그리기·시작 ---------------------------------------------------------------------------------

// 절마다 따로 — 한 절의 예상 밖 응답이 다른 절을 막지 않게
const SECTIONS = [
  ['overview', drawOverview],
  ['collect', drawCollect],
  ['infra', drawInfra],
  ['alerts', drawAlerts],
  ['traffic', drawTraffic],
  ['cost', drawCost],
];

function paint() {
  for (const [id, draw] of SECTIONS) {
    try {
      draw();
    } catch {
      const sumNode = $(`s-${id}`) || $('band-why');
      sumNode.replaceChildren(marked('bad', '표시 오류 — 응답 모양이 예상과 다르다'));
    }
  }
}

paint();
run(fast);
run(slow);
