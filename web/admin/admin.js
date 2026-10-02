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
// 비율 % 소수 1자리 — 1시간 성공률(서버가 소수 1자리로 준다)·박스 지표
const pctFmt = (v) => (num(v) === null ? '–' : `${v.toFixed(1)}%`);
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

// 본문 안 경과 글자 — 본문은 값이 바뀔 때만 다시 그려(§3.3) 그 안의 "n분 전" 이 그린 때에 멈춘다. 시각(ms)을
// data-at 에 두고 그리기 끝(retick)에 글자만 고친다 — 본문 DOM·스크롤·초점·펼침은 그대로, 개요 칸과 같은 경과.
// 글자가 바뀔 때만 쓴다 — 같은 글자라도 textContent 를 쓰면 텍스트 노드가 새로 생겨 그 안의 글자 선택이 풀린다
function agoSpan(ms) {
  const span = el('span', null, ago(ms));
  if (num(ms) !== null) span.dataset.at = String(ms);
  return span;
}

function retick() {
  for (const span of document.querySelectorAll('span[data-at]')) {
    const text = ago(Number(span.dataset.at));
    if (span.textContent !== text) span.textContent = text;
  }
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
  return { status: resp.status, body: isObj(body) ? body : null, text };
}

// 직전 결과와 같으면 직전 객체를 그대로 둔다 — 그 값으로 그린 본문을 다시 그리지 않게(아래 redraw)
function keep(path, next) {
  const prev = got.get(path);
  const same = prev && (next.why ? prev.why === next.why : prev.text === next.text);
  if (!same) got.set(path, next);
}

// 경로 하나를 부르고 결과를 got 에 둔다. 403·JSON 아님·예상 밖 상태(피드 경로가 없는 404 등)는 사유만 남긴다 —
// 그 경로가 채우는 칸은 비우고 사유를 적는다(직전 값이 정상으로 읽히지 않게). 돌려주는 값 = 만료 신호였는가
async function load(path) {
  try {
    const { status, body, text } = await call(path);
    const fine = status === 200 || (status === 503 && HEALTH.has(path));
    if (status === 403) keep(path, { why: '권한·설정 오류 (403)' });
    else if (body === null || !fine) keep(path, { why: `응답 오류 (HTTP ${status})` });
    else keep(path, { body, text });
    okSince.add(path);
    return false;
  } catch (err) {
    const gone = err instanceof Expired;
    keep(path, { why: gone ? '로그인 만료·연결 끊김' : '불러오지 못함' });
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
  aws: 'AWS 자격 없음',
  slack: '이 수집기에 Slack 웹훅 없음',
  access: '로그 파일 없음',
  clarity: '토큰 없음 또는 033 전',
};
const DENIED = 'IAM 정책 또는 조직 SCP — 콘솔에서 본다';

const BAD_SHAPE = Object.freeze({ why: '응답 모양 오류' });

// 경로 응답에서 부분 하나 — undefined(첫 호출 전)·{ why }(호출 실패)·부분 객체 { state, code, fetchedAt, refreshSec, … }.
// 같은 호출 결과면 같은 객체다(다시 그리기 판단이 객체로 비교한다)
function partOf(path, key) {
  const entry = got.get(path);
  if (!entry) return undefined;
  if (entry.why) return entry;
  const part = key ? entry.body[key] : entry.body;
  return isObj(part) && typeof part.state === 'string' ? part : BAD_SHAPE;
}

// 본문은 그 본문이 기대는 값이 바뀌었을 때만 다시 그린다 — 느린 피드가 채우는 목록·표의 스크롤·초점·글자 선택·
// SVG 툴팁이 빠른 묶음(10초)마다 날아가지 않게. 머리의 경과 글자와 절 요약은 매번 다시 쓴다.
const drawn = new Map(); // 본문 이름 → 직전에 그린 입력들
function redraw(name, deps, draw) {
  const last = drawn.get(name);
  if (last && last.length === deps.length && deps.every((d, i) => d === last[i])) return;
  draw();
  drawn.set(name, deps); // 그리다 예외가 나면 남기지 않는다 — 다음 묶음이 다시 그린다
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

// 칸 하나 = 머리(m-이름, 매번) + 본문(b-이름, 부분이 바뀌었을 때만). 값을 그릴 수 있으면 true.
// boxed — 카드가 없는 칸(인프라 박스 영역)은 상태 글을 카드에 넣어 옆 칸들과 모양을 맞춘다
function region(name, part, cause, fill, valueKey, boxed = false) {
  $(`m-${name}`).replaceChildren(...head(part, valueKey));
  const body = $(`b-${name}`);
  const ok = usable(part, valueKey);
  redraw(`b-${name}`, [part], () => {
    if (ok) return fill(body, part);
    const msg = stateMsg(part, cause);
    if (!boxed) return body.replaceChildren(msg);
    const card = el('div', 'card span-all');
    card.append(msg);
    body.replaceChildren(card);
  });
  return ok;
}

// 부분 머리의 짧은 상태 — 값을 못 그릴 때(§3.5 — error 는 장애색 "불러오지 못함")
function stateWord(part) {
  if (part === undefined) return ['wait', '불러오는 중'];
  if (part.why) return ['bad', '호출 실패'];
  if (part.state === 'pending') return ['wait', '첫 조회 중'];
  if (part.state === 'unconfigured') return ['dim', '연결 안 됨'];
  if (part.state === 'denied') return ['dim', '권한 없음'];
  return ['bad', '불러오지 못함'];
}

// 개요 칸·절 요약의 짧은 상태 — ✕(장애색)는 §3.4 판정에서 장애인 것에만 쓴다. 판정 밖 호출 실패·error 는 ▲
function softWord(part) {
  const [tone, word] = stateWord(part);
  return [tone === 'bad' ? 'warn' : tone, word];
}

// --- 차트 (§3.6) — SVG 를 DOM 으로. 모양은 기하 속성, 색·굵기는 클래스 ----------------------------------

// SVG 에 쓰는 속성은 이 목록뿐 — 기하·이름표만. href·style·on… 같은 이름은 여기서 막는다(§3.6·§3.8)
const SVG_ATTRS = new Set(['viewBox', 'preserveAspectRatio', 'role', 'aria-label', 'x', 'y', 'width', 'height', 'x1', 'x2', 'y1', 'y2', 'd', 'points']);

function svg(tag, attrs, cls) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (!SVG_ATTRS.has(k)) throw new Error(`SVG 속성 ${k} 은 쓰지 않는다`);
    node.setAttribute(k, String(v));
  }
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
  if (s.count < 2) return el('p', 'empty', s.count ? '값 1개뿐' : '값 없음');
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
  if (o.noAxis) return root; // 박스 카드는 축 글자를 카드 맨 아래 한 번만 단다
  const out = document.createDocumentFragment();
  out.append(root, axis('24시간 전', '지금'));
  return out;
}

// 가로 비율 막대 하나(0~1) — 막대 전체에 title(숫자만)
function ratio(frac, tone, label) {
  const root = frame(100, 6, 'bar6', label);
  const f = Math.min(1, Math.max(0, num(frac) ?? 0));
  root.append(svg('rect', { x: 0, y: 0, width: 100, height: 6 }, 'track'));
  root.append(svg('rect', { x: 0, y: 0, width: (f * 100).toFixed(2), height: 6 }, tone ? `fill t-${tone}` : 'fill'));
  return tip(root, `${(f * 100).toFixed(1)}%`);
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
  // 판정은 바꾸지 않지만 재료를 못 읽은 것은 사유에 적는다 — "이상 없음" 이 모르는 것까지 단정하지 않게
  const blind = [];
  if (got.get(P.collect)?.why) blind.push('거래소 상태 모름');
  if (got.get(P.status)?.why) blind.push('Redis·Influx 상태 모름');
  if (col.body.status !== 'ok') bad.push(`수집기 ${col.body.status}`);
  if (api.body.status !== 'ok') bad.push(`api ${api.body.status}`);
  const st = bodyOf(P.status);
  if (st?.redis === 'down') bad.push('Redis down');
  if (st?.influx === 'down') bad.push('Influx down');
  const alarms = partOf(P.aws, 'alarms');
  if (alarms?.state === 'ok') {
    const names = list(alarms.items).filter((a) => isObj(a) && a.state === 'ALARM').map((a) => alarmName(a.name));
    const count = Math.max(names.length, num(alarms.counts?.alarm) ?? 0);
    // 둘까지는 이름을 댄다(서버 글 — 글자로만 들어간다), 셋 이상이면 수
    if (count > 0) bad.push(count <= 2 && names.length === count ? `경보 ALARM ${names.join(', ')}` : `경보 ${count}개 ALARM`);
  } else if (alarms?.state === 'error') warn.push('경보 읽기 오류');
  for (const ex of list(bodyOf(P.collect)?.exchanges).filter(isObj)) {
    if (ex.state === 'down') bad.push(`${ex.exchange} 끊김`);
    else if (ex.state === 'stale') warn.push(`${ex.exchange} 지연`);
  }
  const canary = partOf(P.aws, 'canary');
  if (canary?.state === 'ok' && canary.ok === false) warn.push('canary 실패');
  else if (canary?.state === 'error') warn.push('canary 읽기 오류');
  if (bad.length) return { tone: 'bad', word: '장애', why: [...bad, ...warn, ...blind] };
  if (warn.length) return { tone: 'warn', word: '주의', why: [...warn, ...blind] };
  const judged = alarms?.state === 'ok' && canary?.state === 'ok';
  const what = got.get(P.collect)?.why ? '수집기·api' : '수집·api';
  return { tone: 'ok', word: '정상', why: [...blind, judged ? `${what}·경보·canary 이상 없음` : `${what} 이상 없음 — 경보·canary 는 판정 밖(연결 안 됨·첫 조회)`] };
}

// 띠 오른쪽의 판정 재료 일곱 — 어느 칸이 판정에 들었고 어떤 상태인지 (판정 밖은 흐림)
function checks() {
  const health = (path) => {
    const e = got.get(path);
    return !e ? 'wait' : e.why ? 'unknown' : e.body.status === 'ok' ? 'ok' : 'bad';
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

// 개요 칸 — word 면 값 자리에 상태 글(작게), 아니면 값(크게). tone null 은 상태가 없는 값(모양 없이 수만)
function vital(id, tone, value, sub, word = false) {
  const cls = [tone && tone !== 'ok' ? `t-${tone}` : '', word ? 'state' : ''].join(' ').trim();
  const mark = tone ? [el('span', `mk t-${tone}`, GLYPH[tone])] : [];
  $(`v-${id}`).replaceChildren(...mark, el('span', cls || null, value));
  $(`v-${id}-s`).textContent = clean(sub);
}

// 헬스 칸 — 색·모양은 판정과 같다: ok 밖은 모두 장애(§3.4), 호출 실패는 판정처럼 알 수 없음
function healthVital(id, entry, sub) {
  if (!entry) return vital(id, 'wait', '불러오는 중', '', true);
  if (entry.why) return vital(id, 'unknown', '호출 실패', entry.why, true);
  const status = String(entry.body.status);
  vital(id, status === 'ok' ? 'ok' : 'bad', status === 'ok' ? '정상' : status, sub(entry.body));
}

// 개요 비용 칸의 아래 글 — 색의 이유가 보이게
function costSub(w) {
  const limit = money(w.b.limit, w.b.unit);
  const pct = `${fixed(w.r * 100, 0)}%`;
  if (w.r >= 1) return `한도 ${limit} 넘음 (${pct})`;
  if ((num(w.b.forecast) ?? 0) > w.b.limit) return `예측 ${money(w.b.forecast, w.b.unit)} · 한도 ${limit} 넘음`;
  if (w.r >= 0.85) return `한도 ${limit} 중 ${pct} — 85% 넘음`;
  return `한도 ${limit} 중 ${pct}`;
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
  // 지금 접속은 상태가 없는 수 — 모양 없이, 호출 실패(판정 밖)만 ▲
  if (!st) vital('ws', 'wait', '불러오는 중', '', true);
  else if (st.why) vital('ws', 'warn', '호출 실패', st.why, true);
  else vital('ws', null, int(st.body.wsConnections), '열린 대시보드 수');

  const alarms = partOf(P.aws, 'alarms');
  if (usable(alarms)) {
    const items = list(alarms.items).filter(isObj);
    const n = items.filter((a) => a.state === 'ALARM').length;
    const nodata = items.filter((a) => a.state === 'INSUFFICIENT_DATA').length;
    vital('alarms', n ? 'bad' : 'ok', `${n} / ${items.length}`, `ALARM / 전체${nodata ? ` · 데이터 부족 ${nodata}` : ''}`);
  } else vital('alarms', ...softWord(alarms), alarms?.why || alarms?.code || '', true);

  const canary = partOf(P.aws, 'canary');
  if (usable(canary)) {
    if (num(canary.lastRunAt) === null) vital('canary', 'dim', '실행 없음', '11분 안에 끝난 실행 없음', true);
    else vital('canary', canary.ok ? 'ok' : 'warn', canary.ok ? '통과' : '실패', `${ago(canary.lastRunAt)} · ${int(canary.durationMs)}ms`);
  } else vital('canary', ...softWord(canary), canary?.why || canary?.code || '', true);

  const budget = partOf(P.aws, 'budget');
  if (usable(budget)) {
    const worst = worstMonthly(budget);
    if (!worst) vital('cost', 'dim', '예산 없음', '월 단위 비용 예산이 없다', true);
    else vital('cost', worst.tone, money(worst.b.actual, worst.b.unit), costSub(worst));
  } else vital('cost', ...softWord(budget), budget?.why || budget?.code || '', true);
}

// --- 수집 (§3.4) -------------------------------------------------------------------------------

const EXCHANGES = ['upbit', 'bithumb', 'binance', 'bybit', 'bitget'];
const EX_STATE = { ok: ['ok', '수집 중'], stale: ['warn', '지연'], down: ['bad', '끊김'] };
const kindTone = (kind) => (kind === 'banned' || kind === 'rate_limit' ? 'bad' : 'warn');
// 실패 종류 이름표 — 공개 수집 상태 탭(011)의 유형 칩 라벨과 같다(041 §3.4). 표에 없는 값은 원래 글자 그대로
const KIND_NAME = {
  timeout: '타임아웃',
  network: '연결 실패',
  rate_limit: 'rate limit',
  banned: '차단',
  unavailable: '거래소 오류',
  bad_request: '요청 오류',
  bad_response: '응답 오류',
  stale_stream: '스트림 정체',
};
const kindName = (kind) => own(KIND_NAME, kind) ?? clean(kind);

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

// 거래소 표 — 좁은 화면에서도 첫 화면에 들도록 상태 바로 옆에 열린 실패 구간
function exchangeRow(ex) {
  const tr = el('tr');
  const [tone, word] = own(EX_STATE, ex.state) || ['bad', String(ex.state)];
  cell(tr, ex.exchange, 'nowrap');
  cell(tr, badge(tone, word));
  const o = ex.openOutage;
  // 실패 종류는 이름표로, 원래 id 는 title 로(041 §3.4)
  let open = '–';
  if (isObj(o)) {
    open = marked(kindTone(o.kind), `${kindName(o.kind)} · ${int(o.count)}회 · ${lasting(o.startedAt)}`);
    open.title = clean(o.kind);
  }
  cell(tr, open, 'nowrap');
  cell(tr, ago(ex.lastSuccessAt), 'num');
  cell(tr, pctFmt(ex.successRate1h), 'num');
  cell(tr, int(ex.markets), 'num');
  const e = ex.lastError;
  // 지난 오류는 흐리게 한 줄 — 열린 구간이 있는 거래소만 본문색
  const last = cell(tr, isObj(e) ? '' : '–', `small last-error${isObj(o) ? ' hot' : ''}`);
  if (isObj(e)) {
    const http = num(e.statusCode) === null ? '' : ` · HTTP ${e.statusCode}`;
    const head = `${when(e.at)} · ${kindName(e.kind)}${http} · `;
    const text = clip(el('span', 'clamp'), `${head}${clean(e.message)}`, head.length + 300);
    text.title = `${clean(e.kind)} — ${clean(e.message)}`;
    last.append(text);
  }
  return tr;
}

// 축 눈금 — 창을 5등분한 1/5~4/5 지점의 시각과 오른쪽 끝 "지금"(공개 수집 상태 탭과 같은 모양)
function ticks(start, end) {
  const row = el('div', 'axis ticks');
  for (const i of [1, 2, 3, 4]) row.append(el('span', null, hm(start + ((end - start) * i) / 5)));
  row.append(el('span', null, '지금'));
  return row;
}

// 실패 구간 타임라인 — 거래소 다섯 줄, 진행 중은 지금까지, 짧은 구간은 최소 폭(1000 중 4 ≈ 6분). 차단·rate limit 은 꽉 찬 높이·장애색,
// 그 밖은 낮은 막대·주의색(색만으로 가르지 않는다). 아래에 최신 다섯 구간을 글자로(휴대폰은 title 을 못 본다)
function timeline(outages) {
  if (!outages.length) return el('p', 'empty', '최근 24시간 실패 없음');
  const now = Date.now();
  const start = now - 86_400_000;
  const W = 1000;
  const lanes = el('div', 'lanes');
  const label = (o) => `${hm(o.startedAt)}–${num(o.endedAt) === null ? '진행 중' : hm(o.endedAt)} · ${kindName(o.kind)} · ×${int(o.count)}`;
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
      const tone = kindTone(o.kind);
      const [y, h] = tone === 'bad' ? [1, 12] : [4, 6];
      root.append(tip(svg('rect', { x: x.toFixed(1), y, width: w.toFixed(1), height: h }, `seg t-${tone}`), label(o)));
    }
    lanes.append(el('span', 'lane-name', ex), root);
  }
  lanes.append(ticks(start, now));
  const recent = outages.filter((o) => num(o.startedAt) !== null).sort((a, b) => b.startedAt - a.startedAt).slice(0, 5);
  const log = el('ol', 'outage-log');
  log.append(
    ...recent.map((o) => {
      const li = el('li');
      // 시작 시각은 첫 칸(날짜 포함 꼴)에 있으니 글자는 끝·실패 종류·횟수만 — 원래 id 는 title
      const end = num(o.endedAt) === null ? '진행 중' : `~${hm(o.endedAt)}`;
      const what = marked(kindTone(o.kind), `${end} · ${kindName(o.kind)} · ×${int(o.count)}`);
      what.title = clean(o.kind);
      li.append(el('span', 'muted', when(o.startedAt)), el('span', null, o.exchange), what);
      return li;
    }),
  );
  const out = document.createDocumentFragment();
  out.append(lanes, log);
  return out;
}

function drawCollect() {
  const entry = got.get(P.collect);
  if (!entry || entry.why) {
    const part = entry ? { why: entry.why } : undefined;
    $('m-collect').replaceChildren(...head(part));
    $('exchanges').replaceChildren();
    $('collect-sum').replaceChildren(stateMsg(part));
    redraw('timeline', [entry], () => $('timeline').replaceChildren());
    // 판정 밖의 호출 실패는 ▲ (✕ 는 판정에서 장애인 것만)
    $('s-collect').replaceChildren(entry ? marked('warn', entry.why) : marked('wait', '불러오는 중'));
    return;
  }
  const b = entry.body;
  $('m-collect').replaceChildren(el('span', 'muted', `${ago(b.fetchedAt)} 값`));
  const exchanges = list(b.exchanges).filter(isObj);
  $('exchanges').replaceChildren(...exchanges.map(exchangeRow));
  const markets = exchanges.reduce((sum, ex) => sum + (num(ex.markets) ?? 0), 0);
  // 버전은 앱 상수라 배포마다 바뀌지 않아 적지 않는다 — 다시 뜬 때는 '수집기 시작' 이 말한다(041 §3.5)
  const parts = [
    ['전체 1시간', pctFmt(b.successRate1h)],
    ['마켓', int(markets)],
    ['수집기 시작', when(b.serverStartedAt)],
  ];
  $('collect-sum').replaceChildren(
    ...parts.map(([k, v]) => {
      const span = el('span', null, `${k} `);
      span.append(el('b', null, v));
      return span;
    }),
  );
  // 타임라인은 구간이 바뀌었거나 1분이 지났을 때만 다시 그린다 — 막대 툴팁이 10초마다 날아가지 않게
  const outages = list(b.outages).filter(isObj);
  redraw('timeline', [JSON.stringify(outages), Math.floor(Date.now() / 60_000)], () => $('timeline').replaceChildren(timeline(outages)));
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

// 지표 한 줄 — 이름·지금 값·24시간 최저 또는 최고(값 색은 임계 — 모양도 함께)·선. 지금 값의 상태는 o.worst 에 모은다
function metricRow(box, name, points, o) {
  const s = stats(points);
  const fmt = o.format || ((v) => int(v));
  const toned = (v) => {
    const tone = v === null || !o.tone ? null : o.tone(v);
    return tone ? marked(tone, fmt(v)) : el('span', null, fmt(v));
  };
  if (o.worst && s.now !== null && o.tone) {
    const tone = o.tone(s.now);
    if (tone) o.worst.push([tone, `${o.short ?? name} ${fmt(s.now)}`]);
  }
  const row = el('div', 'metric');
  const val = el('span', 'metric-val');
  let ext;
  if (o.peak) {
    // 같은 카드에 '지금' 값이 따로 있는 계열 — 굵은 값은 24시간 최고, 마지막 구간 값은 작게(두 '지금' 이 다투지 않게)
    val.append(el('span', 'metric-tag', '24시간 최고 '), toned(s.max));
    ext = el('span', 'metric-ext', `마지막 5분 구간 ${fmt(s.now)}`);
  } else {
    val.append(toned(s.now));
    ext = el('span', 'metric-ext', `24시간 ${o.extreme === 'min' ? '최저' : '최고'} `);
    ext.append(toned(o.extreme === 'min' ? s.min : s.max));
  }
  if (o.ref !== undefined) ext.append(` · 경보 기준 ${fmt(o.ref)}`);
  // 박스 이름은 아는 셋만 aria-label 에(서버 글은 title 밖의 속성에 쓰지 않는다, §3.8)
  const who = own(BOX_ROLE, box) || box === 'canary' ? box : '박스';
  row.append(el('span', 'metric-name', name), val, ext, line(points, { ...o, format: fmt, label: `${who} ${name}` }));
  return row;
}

// 박스 카드 — 머리에 지금 값 중 가장 나쁜 상태 배지, 지표 줄 사이에는 축 글자 없이 카드 맨 아래 한 번
function boxCard(box, where) {
  const card = el('div', 'card');
  const title = el('h3', null, `${box.box} ${own(BOX_ROLE, box.box) ?? ''}`.trim());
  if (box.instanceId) title.title = clean(box.instanceId);
  const top = el('div', 'card-head');
  top.append(title);
  card.append(top);
  const worst = [];
  const base = { ...where, worst, noAxis: true };
  const pctRow = (name, key, extra) => metricRow(box.box, name, box[key], { ...base, fixed100: true, format: pctFmt, ...extra });
  card.append(
    pctRow('메모리 가용률', 'mem', { ref: 10, extreme: 'min', short: '메모리', tone: (v) => (v < 10 ? 'bad' : v < 20 ? 'warn' : null) }),
    pctRow('디스크 사용률', 'disk', { ref: 80, extreme: 'max', short: '디스크', tone: (v) => (v > 80 ? 'bad' : v > 70 ? 'warn' : null) }),
    pctRow('CPU 사용률', 'cpu', { extreme: 'max' }),
  );
  const floor = own(CREDIT_FLOOR, box.box);
  if (floor !== undefined) {
    card.append(metricRow(box.box, 'CPU 크레딧 잔고', box.credit, { ...base, ref: floor, extreme: 'min', short: '크레딧', tone: (v) => (v < floor ? 'bad' : null) }));
  }
  // 스왑은 값이 있는 박스만. serve 는 스왑(027)이 있지만 에이전트가 모으지 않는다 — 그 사실을 한 줄로(§3.4)
  if (box.swap != null) card.append(pctRow('스왑 사용률', 'swap', { extreme: 'max' }));
  else if (box.box === 'serve') card.append(el('p', 'muted small', '스왑 지표 없음'));
  card.append(axis('24시간 전', '지금'));
  const pick = worst.find(([tone]) => tone === 'bad') || worst.find(([tone]) => tone === 'warn');
  if (pick) top.append(badge(...pick));
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
const alarmLabel = (state) => (own(ALARM_STATE, state) || ['dim', clean(state ?? '?')])[1];
// 경보 꼬리 이름표와 울리는 조건 — 027 경보 이름 `marketlens-<박스>-<꼬리>`(canary·5xx 는 박스 없이)의 꼬리(041 §3.4)
const ALARM_TAIL = {
  'status-instance': ['인스턴스 상태검사', '60초 3점 연속 실패면 AWS 가 재부팅'],
  'status-system': ['시스템 상태검사', '60초 2점 연속 실패면 AWS 가 복구(recover)'],
  'credit-balance': ['CPU 크레딧 잔고', '5분 3점 연속 최대 적립의 30%(data 173·serve 86) 미만'],
  'credit-surplus': ['잉여 크레딧 과금', '5분 1점 0 초과(unlimited 과금 시작)'],
  memory: ['메모리', '가용률 10% 미만 5분 연속(collect 는 5분 1점), 데이터 없음도 울린다'],
  disk: ['디스크', '사용률 80% 초과 5분 1점, 데이터 없음도 울린다'],
  canary: ['바깥 점검', 'Lambda 실패가 5분 2점 연속(10분), 데이터 없음도 울린다'],
  'http-5xx': ['사이트 5xx', '5분 합 10 이상(/api/ws/spreads 는 세지 않는다), 데이터 없음은 정상'],
};

// 접두를 뗀 이름이 꼬리와 같거나 `-<꼬리>` 로 끝나면 그 꼬리의 [이름표, 조건] — 없으면 undefined(원래 글자만)
function alarmTail(name) {
  const short = alarmName(name);
  const tail = Object.keys(ALARM_TAIL).find((t) => short === t || short.endsWith(`-${t}`));
  return tail === undefined ? undefined : ALARM_TAIL[tail];
}

// 경보 이름(접두 뗌) + 바로 뒤 흐린 꼬리 이름표 — 이름의 title 은 '전체 이름 — 조건'(경보 표·알림 행이 같이 쓴다)
function alarmNamed(name, cls) {
  const tail = alarmTail(name);
  const strong = el('span', cls, alarmName(name));
  strong.title = tail ? `${clean(name)} — ${tail[1]}` : clean(name);
  return tail ? [strong, ' ', el('span', 'alarm-tail', tail[0])] : [strong];
}

// 경보 한 행 — 이름·상태·바뀐 지, 사유는 이름 아래 둘째 줄(좁은 폭에서도 칸이 찌그러지지 않게)
function alarmRow(a) {
  const tr = el('tr');
  const name = cell(tr, '');
  name.append(...alarmNamed(a.name, 'nowrap'));
  if (a.reason) name.append(clip(el('span', 'alarm-why'), a.reason, 160));
  cell(tr, badge(...(own(ALARM_STATE, a.state) || ['dim', String(a.state)])));
  cell(tr, agoSpan(a.changedAt), 'num small');
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
    const ran = el('span');
    ran.append(agoSpan(part.lastRunAt), ` · ${int(part.durationMs)}ms`);
    last.append(ran);
  }
  out.push(last);
  if (usable(metrics)) {
    const c = isObj(metrics.canary) ? metrics.canary : {};
    const runs = el('p', 'summary-row');
    const errors = sum(c.errors);
    runs.append(el('span', null, `24시간 실행 ${int(sum(c.runs))}`), errors ? marked('warn', `오류 ${int(errors)}`) : el('span', null, '오류 0'));
    const where = { startMs: metrics.startTs * 1000, endMs: metrics.endTs * 1000 };
    out.push(runs, metricRow('canary', '실행 시간 · CloudWatch 5분 최댓값', c.durationMs, { ...where, extreme: 'max', peak: true, format: (v) => `${int(v)}ms` }));
  } else {
    const [tone, word] = softWord(metrics);
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

// 지표·경보·canary 세 부분이 같은 이유로 비어 있는가 — 연결 안 됨·권한 없음·같은 호출 실패(AWS 자격이 없을 때 매일 보는 화면)
function sameBlank(parts) {
  const key = (p) => (p === undefined ? null : p.why ? `why:${p.why}` : p.state === 'unconfigured' || p.state === 'denied' ? p.state : null);
  const first = key(parts[0]);
  return first !== null && parts.every((p) => key(p) === first);
}

function drawInfra() {
  const metrics = partOf(P.aws, 'metrics');
  const alarms = partOf(P.aws, 'alarms');
  const canary = partOf(P.aws, 'canary');
  const folded = sameBlank([metrics, alarms, canary]);
  $('infra-fold').hidden = !folded;
  $('infra-parts').hidden = folded;
  if (folded) region('infra-fold', alarms, CAUSE.aws, () => {});
  region('metrics', metrics, CAUSE.aws, fillBoxes, undefined, true);
  if (!region('alarms', alarms, CAUSE.aws, fillAlarms)) $('alarms-ok').hidden = true;
  region('canary', canary, CAUSE.aws, (node, part) => fillCanary(node, part, metrics));
  const bits = [];
  let tone = 'ok';
  if (usable(alarms)) {
    const items = list(alarms.items).filter(isObj);
    const n = items.filter((a) => a.state === 'ALARM').length;
    if (n) tone = 'bad';
    bits.push(n ? `경보 ${n}개 ALARM / ${items.length}` : `경보 ${items.length}개 ALARM 없음`);
  } else {
    const [t, w] = softWord(alarms);
    tone = t;
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
    // 이름·꼬리 이름표 뒤에 이전·새 상태 — 경보 표와 같은 이름으로(ALARM·데이터 부족·OK)
    const to = String(item.toState ?? '?');
    what.append(...alarmNamed(item.alarm), ` ${alarmLabel(item.fromState)} → `, marked(own(TO_TONE, to) || 'dim', alarmLabel(to)));
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

// 알림 머리의 출처 둘 — ok 면 경과("n분 전 값", refreshSec × 3 을 넘으면 주의 — slack 은 0 이라 보지 않는다, §3.3)
function sourceState(label, part, cause) {
  const span = el('span', null, `${label} `);
  if (part && !part.why && part.state === 'ok') span.append(age(part));
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

// 알림 목록은 HTML 의 고정 ul 이고 내용이 바뀔 때만 자식을 바꾼다 — 목록 안 스크롤·초점·툴팁이 남게.
// 필터를 바꾸면 새 목록이라 맨 위로
const feedDrawn = { filter: null, key: '' };
function fillFeed(shown) {
  const feed = $('alerts-feed');
  const key = JSON.stringify(shown);
  if (feedDrawn.filter === alertFilter && feedDrawn.key === key) return;
  const top = feedDrawn.filter === alertFilter ? feed.scrollTop : 0;
  feed.replaceChildren(...shown.map(alertRow));
  feed.scrollTop = top;
  Object.assign(feedDrawn, { filter: alertFilter, key });
}

function drawAlerts() {
  const entry = got.get(P.alerts);
  const feed = $('alerts-feed');
  if (!entry || entry.why) {
    const part = entry ? { why: entry.why } : undefined;
    $('m-alerts').replaceChildren(...head(part));
    $('b-alerts').replaceChildren(stateMsg(part));
    feed.hidden = true;
    $('alerts-count').textContent = '';
    $('s-alerts').replaceChildren(entry ? marked('warn', entry.why) : marked('wait', '불러오는 중'));
    return;
  }
  const b = entry.body;
  const slackPart = isObj(b.slack) ? b.slack : BAD_SHAPE;
  const alarmPart = isObj(b.alarms) ? b.alarms : BAD_SHAPE;
  $('m-alerts').replaceChildren(sourceState('Slack', slackPart, CAUSE.slack), sourceState('경보 이력', alarmPart, CAUSE.aws));
  const items = list(b.items).filter(isObj).slice(0, MAX_ALERTS);
  const shown = alertFilter === 'all' ? items : items.filter((i) => i.source === alertFilter);
  $('alerts-count').textContent = `${items.length}건 · 보낸 Slack 알림(전송 실패 포함)과 경보 상태 변경 — 10분 억제로 보내지 않은 알림은 기록에도 없다`;
  feed.hidden = !shown.length;
  if (shown.length) {
    $('b-alerts').replaceChildren();
    fillFeed(shown);
  } else $('b-alerts').replaceChildren(el('p', 'empty', '지난 7일 기록 없음'));
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

// 대시보드 탭 이름표 — 002 의 탭 id·이름(041 §3.4). `(기타)` 와 표에 없는 값은 원래 글자 그대로
const TAB_NAME = {
  spread: '실시간 스프레드',
  history: '기록/통계',
  gap: '선물–현물 갭',
  pp: '선선갭',
  health: '수집 상태',
  flow: '입출금 레이더',
};
const tabName = (id) => own(TAB_NAME, id) ?? clean(id);
// [응답 키, 표 이름, 이름표 함수(있으면 한국어 이름을 적고 원래 글자는 title)]
const TOP_TABLES = [
  ['paths', '경로'],
  ['tabs', '탭', tabName],
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

// 타일 — 이름·값, sub 는 값 아래 흐린 한 줄(무엇을 센 수인지 — 사람 수로 읽히거나 봇이 섞이는 타일, 041 §3.1)
function stat(label, value, tone, sub) {
  const box = el('div', 'stat');
  box.append(el('span', 'stat-label', label), tone ? marked(tone, value) : el('span', 'stat-val', value));
  if (tone) box.lastChild.classList.add('stat-val');
  if (sub) box.append(el('span', 'stat-sub', sub));
  return box;
}

// 상위 목록 표 — 이름(방문자가 정한 글자 — 글자로만)·수·페이지 대비 비율(막대와 % 를 한 줄에), 상위 10.
// named 가 있으면(탭) 이름표를 적고 원래 글자는 title
function topTable(rows, label, pages, named) {
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
    const first = clip(cell(tr, ''), named ? named(name) : name, 60);
    if (named) first.title = clean(name);
    cell(tr, int(n), 'num');
    const r = share(num(n) ?? 0, pages);
    cell(tr, ratio(r, null, `페이지의 ${(r * 100).toFixed(1)}%`), 'share-bar');
    cell(tr, `${(r * 100).toFixed(1)}%`, 'num');
    tbody.append(tr);
  }
  table.append(tbody);
  const wrap = el('div', 'scroll');
  wrap.append(table);
  card.append(wrap);
  return card;
}

// 5xx 는 수치·표·막대·절 요약이 같은 장애색(§3.4 — 시간대별 막대의 5xx 겹침이 장애색이다)
const fiveTone = (n) => (n ? 'bad' : null);

function fillAccess(node, a) {
  const totals = isObj(a.totals) ? a.totals : {};
  const sub = [`폴링·canary 제외, IP 없음 · 읽지 못한 줄 ${int(totals.skipped)}`];
  if (num(a.firstTs) !== null && num(a.startTs) !== null && a.firstTs > a.startTs) sub.push(`기록 시작 ${hm(a.firstTs * 1000)}`);
  $('access-sub').textContent = sub.join(' · ');
  const status = isObj(a.status) ? a.status : {};
  const fives = num(status['5xx']) ?? 0;
  const stats = el('div', 'stats');
  stats.append(
    stat('총 요청', int(totals.requests), null, '파일·봇·스캔까지 기록된 모든 줄'),
    stat('페이지', int(totals.pages), null, '화면 주소 요청 · 봇 섞임'),
    stat('5xx', int(fives), fiveTone(fives)),
  );
  const hourly = list(a.hourly).filter(isObj);
  const bars = hourly.map((h) => ({
    value: num(h.requests) ?? 0,
    over: num(h.errors) ?? 0,
    tip: `${hm(h.ts * 1000)} · 요청 ${int(h.requests)} · 페이지 ${int(h.pages)} · 5xx ${int(h.errors)}`,
  }));
  const peak = Math.max(0, ...bars.map((b) => b.value));
  const chart = hourly.length ? columns(bars, `시간대별 요청 24시간 — 최고 ${int(peak)}, 5xx 는 겹쳐 표시`, 'tall') : el('p', 'empty', '값 없음');
  // 막대 값은 title 에만 있으면 휴대폰에서 못 본다 — 최고 값과 5xx 가 난 시간을 글자로
  const hourHead = el('p', 'summary-row');
  hourHead.append(el('span', null, `시간대별 요청 · 최고 ${int(peak)}/시간`));
  const errHours = hourly.filter((h) => num(h.errors) > 0);
  if (errHours.length) {
    const list5 = errHours.slice(-6).map((h) => `${hm(h.ts * 1000).slice(0, 2)}시 ${int(h.errors)}`);
    hourHead.append(marked('bad', `5xx ${list5.join(' · ')}${errHours.length > 6 ? ` 외 ${errHours.length - 6}` : ''}`));
  }
  const codes = el('table', 'table');
  const tbody = el('tbody');
  for (const key of ['2xx', '3xx', '4xx', '5xx']) {
    const tr = el('tr');
    const n = num(status[key]) ?? 0;
    const tone = key === '5xx' ? fiveTone(n) : null;
    cell(tr, tone ? marked(tone, key) : key);
    cell(tr, int(n), 'num');
    const r = share(n, num(totals.requests) ?? 0);
    cell(tr, ratio(r, tone, `${key} 요청의 ${(r * 100).toFixed(1)}%`), 'share-bar');
    cell(tr, `${(r * 100).toFixed(1)}%`, 'num');
    tbody.append(tr);
  }
  codes.append(tbody);
  const ws = isObj(a.ws) ? a.ws : {};
  const durations = isObj(ws.durations) ? ws.durations : {};
  const wsBars = WS_BUCKETS.map(([k, label]) => ({ value: num(durations[k]) ?? 0, over: 0, tip: `${label} · ${int(durations[k])}` }));
  const labels = el('div', 'cols5');
  labels.append(
    ...WS_BUCKETS.map(([k, label]) => {
      const span = el('span', null, label);
      span.append(el('b', null, int(durations[k])));
      return span;
    }),
  );
  const wsHead = el('p', 'summary-row');
  wsHead.append(el('span', null, `WebSocket 연결 ${int(ws.count)} · 지속 시간`));
  // 눈금은 막대 24개가 덮는 구간(첫 칸 시작 ~ 마지막 칸 끝)을 5등분 — 마지막 칸이 지금 시각을 품는다
  const hourAxis = hourly.length ? ticks(hourly[0].ts * 1000, (hourly[0].ts + hourly.length * 3600) * 1000) : el('span');
  node.replaceChildren(stats, hourHead, chart, hourAxis, codes, wsHead, columns(wsBars, `WebSocket 지속 시간 구간 — 연결 ${int(ws.count)}`), labels);
  const pages = num(totals.pages) ?? 0;
  $('access-tables').replaceChildren(...TOP_TABLES.map(([k, label, named]) => topTable(a[k], label, pages, named)));
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

// 서버 주기(refreshSec) → "3시간" — 주기 상수를 화면에 복사해 두지 않는다(§3.3)
function interval(sec) {
  if (!(num(sec) > 0)) return null;
  if (sec % 3600 === 0) return `${sec / 3600}시간`;
  if (sec % 60 === 0) return `${sec / 60}분`;
  return `${sec}초`;
}

function fillClarity(node, c) {
  const t = isObj(c.traffic) ? c.traffic : {};
  const tiles = el('div', 'clarity-tiles');
  for (const [label, v, sub] of [
    ['세션', int(t.sessions), '동의한 방문자만'],
    ['봇 세션', int(t.botSessions), 'Clarity 가 봇으로 본 세션'],
    ['사용자', int(t.users), '동의한 방문자 · Clarity 기준'],
    ['세션당 페이지', fixed(t.pagesPerSession, 2), 'Clarity 값 그대로'],
  ]) {
    tiles.append(stat(label, v, null, sub));
  }
  const every = interval(c.refreshSec);
  const next = el('p', 'muted small', num(c.nextAt) === null ? '' : `다음 조회 ${when(c.nextAt)} 이후${every ? ` — ${every} 간격(Clarity 하루 호출 한도)` : ''}`);
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
    // 굵은 오른쪽 값은 '지금' 처럼 읽히니 24시간 최고로 — 지금 수는 위의 큰 숫자(빠른 묶음)다
    node.replaceChildren(metricRow('serve', 'WebSocket 접속 수 · CloudWatch 5분 최댓값', m.wsClients, { ...where, extreme: 'max', peak: true }));
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
  let fives = 0;
  if (usable(access)) {
    fives = num(access.status?.['5xx']) ?? 0;
    bits.push(`24시간 페이지 ${int(access.totals?.pages)}`);
  } else {
    const [t, w] = softWord(access);
    tone = t;
    bits.push(`서버 기록 ${w}`);
  }
  // 5xx 는 판정 밖이라 줄 전체를 칠하지 않고 그 조각만 같은 장애색으로(수치·표와 같게)
  const parts = [marked(st ? tone : 'wait', bits.join(' · '))];
  if (fives) parts.push(marked(fiveTone(fives), `5xx ${int(fives)}`));
  $('s-traffic').replaceChildren(...parts);
}

// --- 비용 (§3.4) -------------------------------------------------------------------------------

const USD = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const TIME_UNIT = { MONTHLY: '월', QUARTERLY: '분기', ANNUALLY: '연' };
const isUsd = (unit) => unit == null || unit === 'USD';

function money(v, unit) {
  if (num(v) === null) return '–';
  return isUsd(unit) ? `$${USD.format(v)}` : `${USD.format(v)} ${clean(unit)}`;
}

// aria-label 용 — 단위는 서버 글이라 싣지 않는다(§3.8 — title 밖의 속성 금지). USD 만 $ 를 붙인다
const plainMoney = (v, unit) => (num(v) === null ? '–' : `${isUsd(unit) ? '$' : ''}${USD.format(v)}`);

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

// 예산 막대 — 실제 사용액, 한도 85%·100% 세로선, 점선 = 한도 × 이번 달 지난 비율(한도 속도로 썼다면 지금 있을 자리 —
// 실제 막대가 점선을 넘으면 한도보다 빠르게 쓰고 있다)
function budgetBar(b) {
  const top = Math.max(b.limit * 1.15, b.actual ?? 0, b.forecast ?? 0);
  const x = (v) => ((v / top) * 100).toFixed(2);
  const r = (b.actual ?? 0) / b.limit;
  const elapsed = monthElapsed();
  // 이름·단위는 서버 글이라 aria-label 에 싣지 않는다(§3.8 — title 밖의 속성 금지). 같은 줄의 이름이 말한다
  const label = `예산 막대 — 실제 ${plainMoney(b.actual, b.unit)}, 한도의 ${(r * 100).toFixed(0)}%, 이번 달 ${(elapsed * 100).toFixed(0)}% 지남`;
  const root = frame(100, 14, 'bar12', label);
  root.append(svg('rect', { x: 0, y: 3, width: 100, height: 8 }, 'track'));
  root.append(tip(svg('rect', { x: 0, y: 3, width: x(b.actual ?? 0), height: 8 }, r >= 1 ? 'fill t-bad' : r >= 0.85 ? 'fill t-warn' : 'fill'), `실제 ${money(b.actual, b.unit)}`));
  const mark = (v, cls, text) => root.append(tip(svg('line', { x1: x(v), x2: x(v), y1: 0, y2: 14 }, cls), text));
  mark(b.limit * 0.85, 'mark', `한도 85% ${money(b.limit * 0.85, b.unit)} — 027 예산 알림`);
  mark(b.limit, 'mark t-bad', `한도 ${money(b.limit, b.unit)} — 027 예산 알림`);
  mark(b.limit * elapsed, 'mark now', `이번 달 ${(elapsed * 100).toFixed(0)}% 지남 — 한도 속도면 ${money(b.limit * elapsed, b.unit)}`);
  return root;
}

function budgetRow(b) {
  const row = el('div', 'budget');
  const name = el('span', 'budget-name', b.name ?? '이름 없음');
  if (b.timeUnit && b.timeUnit !== 'MONTHLY') name.append(el('span', 'muted small', ` · ${own(TIME_UNIT, b.timeUnit) ?? clean(b.timeUnit)}`));
  const nums = el('span', 'budget-nums', `실제 ${money(b.actual, b.unit)} / 한도 ${money(b.limit, b.unit)}`);
  const monthly = b.timeUnit === 'MONTHLY';
  if (monthly) {
    const over = num(b.forecast) !== null && num(b.limit) !== null && b.forecast > b.limit;
    nums.append(' · ', over ? marked('warn', `예측 ${money(b.forecast, b.unit)} 한도 넘음`) : el('span', null, `예측 ${money(b.forecast, b.unit)}`));
  }
  row.append(name, nums);
  if (monthly && num(b.limit) > 0) {
    const pace = `점선 = 한도 × 이번 달 ${(monthElapsed() * 100).toFixed(0)}% (한도 속도) · 세로선 = 한도 85%·100%`;
    row.append(budgetBar(b), axis('0', pace));
  }
  return row;
}

function drawCost() {
  const budget = partOf(P.aws, 'budget');
  region('budget', budget, CAUSE.aws, (node, part) => {
    const items = list(part.items).filter(isObj);
    node.replaceChildren(...(items.length ? items.map(budgetRow) : [el('p', 'empty', '예산 없음')]));
  });
  if (!usable(budget)) return $('s-cost').replaceChildren(marked(...softWord(budget)));
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
  retick();
}

paint();
run(fast);
run(slow);
