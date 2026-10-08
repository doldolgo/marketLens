// KimpTrack 관리자 화면 v2 스크립트 (스펙 036 — 접속 절의 서버 기록은 042, 화면 이용 절은 053). 빌드 없음 — oxlint·vite 대상이 아니다(landing.html 과 같다).
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
  attention: '/svc/api/admin/attention', // 053 — 화면 이용 절이 보이는 동안만(두 묶음 밖)
};
const FAST = [P.collector, P.api, P.status, P.collect];
const SLOW = [P.aws, P.alerts, P.access, P.clarity];
const HEALTH = new Set([P.collector, P.api]); // 025 — 503 도 상태 응답이다(본문을 그린다)
// 서버 기록 창(042 §3.2) — 버튼의 data-window 글자. 고른 창은 이 변수에만 둔다(주소·브라우저 저장소 없음 — 새로고침하면 24시간)
const WINDOW_NAME = { '24h': '24시간', '7d': '7일', '30d': '30일' };
const WINDOW_HOURS = { '24h': 24, '7d': 168, '30d': 720 };

class Expired extends Error {}

const got = new Map(); // 경로 → { body } | { why } — 마지막 호출 결과 하나뿐(쌓지 않는다)
const okSince = new Set(); // 마지막 만료 신호 뒤 만료 신호 없이 끝난 경로
let reloading = false;
let alertFilter = 'all'; // 알림 필터 — JS 변수에만 (§3.4)
let picked = '24h'; // 고른 서버 기록 창 (042 §3.2)
// 떠 있는 호출 하나와 '끝나면 한 번 더' — 접속(042 창)·화면 이용(053 기간)이 고른 값으로 부르는 두 경로
const acc = { path: P.access, flight: null, again: false, after: () => follow() };
const att = { path: P.attention, flight: null, again: false, after: null, started: 0, timer: 0 };

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

// 직전 결과와 같으면 직전 객체를 그대로 둔다 — 그 값으로 그린 본문을 다시 그리지 않게(아래 redraw).
// 접속 결과는 요청한 창(asked)이 같을 때만 같은 것이다
function keep(path, next) {
  const prev = got.get(path);
  const same = prev && prev.asked === next.asked && (next.why ? prev.why === next.why : prev.text === next.text);
  if (!same) got.set(path, next);
}

// 경로 하나를 부르고 결과를 got 에 둔다. 403·JSON 아님·예상 밖 상태(피드 경로가 없는 404 등)는 사유만 남긴다 —
// 그 경로가 채우는 칸은 비우고 사유를 적는다(직전 값이 정상으로 읽히지 않게). 돌려주는 값 = 만료 신호였는가
async function load(path) {
  // 접속 경로는 늘 고른 창(042 §3.2), 화면 이용 경로는 고른 기간(053 §3.2)을 붙인다 — 결과에 요청한 값을 남겨,
  // 고른 값으로 요청한 응답만 그린다
  const asked = path === P.access ? picked : path === P.attention ? scr.days : undefined;
  const url = path === P.access ? `${path}?window=${asked}` : path === P.attention ? `${path}?days=${asked}` : path;
  try {
    const { status, body, text } = await call(url);
    const fine = status === 200 || (status === 503 && HEALTH.has(path));
    if (status === 403) keep(path, { why: '권한·설정 오류 (403)', asked });
    else if (body === null || !fine) keep(path, { why: `응답 오류 (HTTP ${status})`, asked });
    else keep(path, { body, text, asked });
    okSince.add(path);
    return false;
  } catch (err) {
    const gone = err instanceof Expired;
    keep(path, { why: gone ? '로그인 만료·연결 끊김' : '불러오지 못함', asked });
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

// 호출 묶음 하나가 끝난 뒤 — 만료 신호면 새로고침 한 번, 여덟 경로가 모두 신호 없이 끝났으면 표시 지우기, 그리고 그리기
function settle(signals) {
  if (signals.some(Boolean)) {
    okSince.clear();
    expired();
  } else if (FAST.concat(SLOW).every((path) => okSince.has(path))) {
    alive();
  }
  $('updated').textContent = clock(Date.now());
  paint();
}

// 고른 값으로 부르는 경로 호출(042 §3.2·053 §3.2) — 접속은 느린 묶음과 창 버튼이, 화면 이용은 5분 주기와 기간 고르기가
// 함께 쓴다. 떠 있으면 겹쳐 부르지 않고 끝난 뒤 한 번 더 부른다(그사이 여러 번 바꿔도 다음 호출은 하나 — 그때 고른 값).
// 돌려주는 값 = 만료 신호였는가
function loadLatest(box) {
  if (box.flight) {
    box.again = true;
    return box.flight;
  }
  box.flight = (async () => {
    let gone = false;
    do {
      box.again = false;
      gone = (await load(box.path)) || gone;
      if (box.after) box.after();
    } while (box.again && !reloading);
    box.flight = null;
    return gone;
  })();
  return box.flight;
}

// 응답 창이 요청한 창과 다르면(서버가 24시간으로 답했다) 고른 창을 응답 창으로 되돌린다 — 그사이 다른 창을 고르지 않았을 때만
function follow() {
  const entry = got.get(P.access);
  const answered = entry?.body?.window;
  if (entry?.asked !== picked || answered === picked || !own(WINDOW_NAME, answered)) return;
  picked = answered;
  entry.asked = answered;
}

async function run(loop) {
  clearTimeout(loop.timer);
  if (loop.busy || reloading || document.visibilityState !== 'visible') return;
  loop.busy = true;
  loop.started = Date.now();
  try {
    settle(await Promise.all(loop.paths.map((path) => (path === P.access ? loadLatest(acc) : load(path)))));
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
// 화면 이용(053)은 절이 보일 때만 — 숨으면 주기를 멈추고 틀을 비운다(screensWake)
document.addEventListener('visibilitychange', () => {
  screensWake();
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

// 현물 6곳 뒤 perp 원천 3개 — /health/collect 의 exchanges 순서와 같다(046 §3.8)
const EXCHANGES = ['upbit', 'bithumb', 'binance', 'bybit', 'bitget', 'okx', 'binance_perp', 'bybit_perp', 'bitget_perp'];
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

for (const chip of document.querySelectorAll('.chip[data-filter]')) {
  chip.addEventListener('click', () => {
    alertFilter = chip.dataset.filter;
    for (const other of document.querySelectorAll('.chip[data-filter]')) other.setAttribute('aria-pressed', String(other === chip));
    drawAlerts();
  });
}

// --- 접속 (§3.4) — 실시간·서버 기록(042 — 창 줄과 질문 여섯 덩어리)·Clarity ------------------------------

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
const WS_BUCKETS = [
  ['lt10s', '10초 미만'],
  ['lt1m', '1분 미만'],
  ['lt10m', '10분 미만'],
  ['lt1h', '1시간 미만'],
  ['ge1h', '1시간 이상'],
];
const share = (n, total) => (total > 0 ? n / total : 0);
const n0 = (v) => num(v) ?? 0;
const QS = [1, 2, 3, 4, 5, 6];

// 타일 — 이름·값, sub 는 값 아래 흐린 한 줄(무엇을 센 수인지 — 사람 수로 읽히거나 봇이 섞이는 타일, 041 §3.1)
function stat(label, value, tone, sub) {
  const box = el('div', 'stat');
  box.append(el('span', 'stat-label', label), tone ? marked(tone, value) : el('span', 'stat-val', value));
  if (tone) box.lastChild.classList.add('stat-val');
  if (sub) box.append(el('span', 'stat-sub', sub));
  return box;
}

// 시행일·KST 날 — 화면의 시행일 글자는 늘 gateAt 의 KST 날짜다(§3.2). 날 막대도 KST 날로 자른다(§3.4 ④)
const KST_SEC = 32_400;
const kstDay = (sec) => Math.floor((sec + KST_SEC) / 86_400) * 86_400 - KST_SEC;
function kstMd(ms) {
  const d = new Date(ms + KST_SEC * 1000);
  return `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}
const hourStart = (sec) => sec - (sec % 3600);
// 응답이 답한 창 — 서버 값이라 own() 으로 찾고, 목록 밖이면 고른 창('constructor' 같은 이름이 Object 의 것을 집지 않게)
const winOf = (a) => (own(WINDOW_NAME, a.window) ? a.window : picked);
// 7일·30일 창의 시작이 시행일로 잘렸는가 — 자르기 전 시작 = endTs 가 든 시의 시작 − (N−1)시간
const naturalStart = (a) => hourStart(n0(a.endTs)) - (WINDOW_HOURS[winOf(a)] - 1) * 3600;
const isCut = (a) => winOf(a) !== '24h' && n0(a.startTs) > naturalStart(a);
const spanDays = (a) => Math.ceil((n0(a.endTs) - n0(a.startTs)) / 86_400);

// 창 버튼 — 응답 windows 에 든 창만 연다(없거나 호출 실패면 24시간만). 고른 버튼은 aria-pressed
function drawWindowBar(entry, part, current) {
  const body = entry && !entry.why ? entry.body : null;
  const open = Array.isArray(body?.windows) ? body.windows : ['24h'];
  for (const button of document.querySelectorAll('[data-window]')) {
    button.disabled = !open.includes(button.dataset.window);
    button.setAttribute('aria-pressed', String(button.dataset.window === picked));
  }
  const gate = num(body?.gateAt);
  $('window-note').textContent = gate !== null && !open.includes('7d') ? `7일·30일은 처리방침 개정 시행 ${kstMd(gate)} 부터` : '';
  const meta = $('m-window');
  if (!current) return meta.replaceChildren(badge('wait', `${WINDOW_NAME[picked]} 불러오는 중`));
  if (!usable(part)) return meta.replaceChildren(...head(part));
  const startMs = n0(part.startTs) * 1000;
  const range = isCut(part) ? `${kstMd(n0(part.gateAt))} 00:00 시행부터 센 ${spanDays(part)}일` : `${md(startMs)} ${hm(startMs)} ~ 지금`;
  const tail = [`읽지 못한 줄 ${int(part.totals?.skipped)}`];
  if (num(part.firstTs) !== null && part.firstTs > n0(part.startTs)) tail.push(`기록 시작 ${md(part.firstTs * 1000)} ${hm(part.firstTs * 1000)}`);
  // 한 줄 글(flex 칸으로 나누지 않는다) — '·' 앞은 줄 안 바꿈 공백이라 줄 머리에 오지 않는다
  const sep = '\u00a0· ';
  const line = el('span', null, `${range}${sep}${SLOW_MS / 1000}초마다${sep}`);
  line.append(age(part), sep + tail.join(sep));
  meta.replaceChildren(line);
}

// 창 버튼(042 §3.2) — 바꾸는 순간 덩어리를 '불러오는 중' 으로 비우고 접속 경로 하나만 곧바로 부른다.
// 같은 요청 함수(헤더·만료 판정·여덟 경로 셈)를 지나고, 느린 묶음의 다음 시각은 건드리지 않는다
for (const button of document.querySelectorAll('[data-window]')) {
  button.addEventListener('click', () => {
    const next = button.dataset.window;
    if (next === picked || !own(WINDOW_NAME, next) || reloading) return;
    picked = next;
    paint();
    loadLatest(acc).then((gone) => settle([gone]));
  });
}

// 이름표(042 §3.6) — 행에는 한국어 이름, 원래 키는 title 과 '이 칸 뜻' 에만. 표에 없는 키는 원래 글자 그대로
const CLASS_NAME = { browser: '사람 브라우저 모양', search: '검색엔진', ai: 'AI 수집기', preview: '링크 미리보기', tool: '자동화 도구', scanner: '스캐너', operator: '운영자 흔적', unknown: '이름 없음' };
const CHANNEL_NAME = { direct: '직접', search: '검색', inapp: '앱 안 브라우저', social: '소셜·커뮤니티', ai: 'AI 답변', referral: '다른 사이트 링크', campaign: '캠페인(utm)', internal: '사이트 안 이동', unknown: '첫 페이지 기록 없음' };
const NET_NAME = { telecom_kr: '국내 통신사', telecom: '해외 통신사', cloud: '데이터센터·클라우드', other: '기업·학교·기관', unknown: '자료에 없음' };
const APP_NAME = { kakaotalk: '카카오톡', naver: '네이버 앱', instagram: '인스타그램', facebook: '페이스북', line: '라인', daum: '다음 앱', band: '밴드', other: '그 밖 앱' };
const DEVICE_NAME = { mobile: '휴대폰', tablet: '태블릿', desktop: '데스크톱' };
const OS_NAME = { ios: 'iOS', android: 'Android', windows: 'Windows', macos: 'macOS', linux: 'Linux', chromeos: 'ChromeOS', other: '그 밖' };
const BROWSER_NAME = { chrome: 'Chrome', safari: 'Safari', samsung: '삼성 인터넷', whale: '웨일', edge: 'Edge', firefox: 'Firefox', opera: 'Opera', inapp: '앱 안 브라우저', other: '그 밖' };
const CLASSES = Object.keys(CLASS_NAME);
const NETS = Object.keys(NET_NAME);
const WEEKDAY = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일'];
const nameIn = (table) => (key) => own(table, key) ?? clean(key);
// 나라 — ISO 두 글자를 브라우저의 한국어 지역 이름으로. 못 푸는 값(ZZ·(기타)·Intl 없음)은 null(글자 그대로 쓴다)
const REGION = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['ko'], { type: 'region' }) : null;
function countryName(code) {
  if (!REGION || typeof code !== 'string' || !/^[A-Z]{2}$/.test(code) || code === 'ZZ') return null;
  try {
    const name = REGION.of(code);
    return name && name !== code ? name : null;
  } catch {
    return null;
  }
}

// [[이름, 확인, 모양]]·[[이름, 수]] — 모양이 아닌 행은 버리고 수는 숫자로. 피드 상한(상위 목록 20·나라 20 + (기타))을
// 믿지 않고 화면도 앞 21행에서 자른다(036 §3.9)
const LIST_MAX = 21;
const rowsOf = (v) => list(v).filter((r) => Array.isArray(r)).slice(0, LIST_MAX);
const rows3 = (v) => rowsOf(v).map((r) => [r[0], n0(r[1]), n0(r[2])]);
const rows2 = (v) => rowsOf(v).map((r) => [r[0], n0(r[1])]);
const total = (rows, i) => rows.reduce((acc, r) => acc + r[i], 0);
// 확인 내림차순(같으면 서버 순서), 확인 0 행 뺌 — 답 문장의 '상위'
const byConfirmed = (rows) => rows.filter((r) => r[1] > 0).sort((x, y) => y[1] - x[1]);
const pct = (n, d) => Math.round((n / d) * 100);
const AVG = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const visOf = (a) => (isObj(a.visitors) && a.visitors.state === 'ok' ? a.visitors : null);
// 하루 평균 — 합 ÷ ((endTs − sinceTs) ÷ 86,400), 소수 1자리. 날수가 0 이면 null
function perDay(a, v, value) {
  const days = (n0(a.endTs) - n0(v.sinceTs)) / 86_400;
  return days > 0 ? AVG.format(n0(value) / days) : null;
}

// 답 문장 틀(042 §3.5) — `{…}` 자리는 굵게. t`확인 ${a}` → ['확인 ', { b: 'a' }]
const t = (strs, ...vals) => strs.flatMap((s, i) => (i < vals.length ? [s, { b: String(vals[i]) }] : [s])).filter((x) => x !== '');
const joined = (rows) => rows.flatMap((row, i) => (i ? ['·', ...row] : row));

// 답 하나 = 문장 조각 목록. 못 만드는 조각(분모 0·빈 목록)은 빼고, 다 빠지면 '기록 없음'. null = 답 대신 본문 상태 글
function sentences(pieces) {
  if (pieces === null) return [];
  const kept = pieces.filter((p) => p && p.length);
  return kept.length ? kept.flatMap((p, i) => (i ? [' ', ...p] : p)) : ['기록 없음'];
}

// 답 문장에 쓰는 값 — 갈래(전·24h·기간)와 시행일·기간 글자
function facts(a) {
  const pre = !list(a.windows).includes('7d');
  const days = spanDays(a);
  return {
    a,
    pre,
    span: !pre && winOf(a) !== '24h',
    vis: visOf(a),
    geo: isObj(a.geo) ? a.geo : null,
    T: isObj(a.totals) ? a.totals : {},
    S: isObj(a.status) ? a.status : {},
    hourly: list(a.hourly).filter(isObj),
    D: num(a.gateAt) === null ? '–' : kstMd(a.gateAt),
    period: isCut(a) ? `개정 시행 뒤 센 ${days}일` : `최근 ${days}일`,
  };
}

function answer1(f) {
  const { T, vis: v } = f;
  if (f.pre) return [t`방문자는 처리방침 개정 시행 ${f.D} 00:00 부터 센다.`, t`지금은 줄 수만 — 사람 모양 페이지 ${int(n0(T.humanPages))}, 그중 스크립트가 돈 페이지 ${int(n0(T.jsViews))}.`];
  if (!v) return null;
  const [c, s, r] = [n0(v.confirmed), n0(v.shaped), n0(v.returning)];
  const out = [];
  if (!f.span) {
    out.push(t`확인 ${int(c)}, 브라우저 모양 ${int(s)}(날마다 센 방문자) — 오늘·어제(KST)를 따로 세어 더한 수라 사람 수가 아니다.`);
    if (n0(v.sinceTs) > n0(f.a.startTs)) out.push(t`시행 ${f.D} 00:00 부터 센 값이다.`);
  } else {
    const [ac, as] = [perDay(f.a, v, c), perDay(f.a, v, s)];
    if (ac !== null) out.push(t`${f.period} 동안 스크립트가 돈 방문자는 하루 평균 ${ac}명, 브라우저 모양까지 치면 ${as}명 — 실제 사람은 이 사이다.`);
    if (c > 0) out.push(t`확인의 ${pct(r, c)}%는 다시 온 사람이다.`);
  }
  if (s > 0 && c <= s * 0.1) out.push(['브라우저 모양의 대부분은 봇이다.']);
  return out;
}

function answer2(f) {
  const req = n0(f.T.requests);
  const classes = isObj(f.a.classes) ? f.a.classes : {};
  const requests = (k) => n0(classes[k]?.requests);
  const out = [];
  if (req > 0) {
    out.push(t`요청 ${int(req)}줄 가운데 사람 브라우저 모양은 ${pct(requests('browser'), req)}%다.`);
    const bots = CLASSES.filter((k) => k !== 'browser' && requests(k) > 0).sort((x, y) => requests(y) - requests(x)).slice(0, 2);
    if (bots.length) out.push(['자동 요청은 ', ...joined(bots.map((k) => t`${CLASS_NAME[k]}(${pct(requests(k), req)}%)`)), ' 순으로 많다.']);
  }
  const hp = n0(f.T.humanPages);
  if (hp > 0) out.push(t`사람 모양 페이지 가운데 스크립트가 돈 것은 ${pct(n0(f.T.jsViews), hp)}%다.`);
  return out;
}

function answer3(f) {
  if (f.pre) return [t`나라·망 종류와 들어온 길은 처리방침 개정 시행 ${f.D} 00:00 부터 센다.`, ['지금은 외부 출처·utm 만 페이지 줄로 보인다.']];
  const out = [];
  const g = f.geo;
  if (g?.state === 'ok') {
    const nets = rows3(g.networks);
    const kr = nets.find((r) => r[0] === 'telecom_kr');
    if (kr && total(nets, 1) > 0) out.push(t`확인의 ${pct(kr[1], total(nets, 1))}%는 국내 통신사 망에서 왔다.`);
    if (total(nets, 2) > 0) {
      const [, cc, cs] = nets.find((r) => r[0] === 'cloud') ?? ['cloud', 0, 0];
      const head = t`데이터센터·클라우드 망은 브라우저 모양의 ${pct(cs, total(nets, 2))}%이고 그중 확인은 ${int(cc)}`;
      out.push([...head, cs > 0 && cc <= cs * 0.1 ? '뿐 — 대부분 봇이다.' : '다.']);
    }
  } else if (g?.state === 'pending') out.push(['나라·망 종류는 자료를 받는 중이다.']);
  else if (g) out.push(t`나라·망 종류 자료를 받지 못했다(${clean(g.code ?? g.state)}).`);
  if (f.vis) {
    const channels = rows3(f.vis.channels);
    const sum = total(channels, 1);
    const top = byConfirmed(channels).slice(0, 3);
    if (sum > 0) out.push(['들어온 길은 ', ...joined(top.map(([k, c]) => [...t`${nameIn(CHANNEL_NAME)(k)} ${pct(c, sum)}`, '%'])), ' 순이다.']);
  }
  return out;
}

// 요일×시간 — hourly.jsViews 를 이 브라우저 시간대의 (요일 월~일, 시 0~23) 칸에 더한다
function heatGrid(hourly) {
  const cells = WEEKDAY.map(() => new Array(24).fill(0));
  for (const h of hourly) {
    const d = new Date(n0(h.ts) * 1000);
    cells[(d.getDay() + 6) % 7][d.getHours()] += n0(h.jsViews);
  }
  const hours = cells[0].map((_, i) => cells.reduce((acc, row) => acc + row[i], 0));
  return { cells, hours, days: cells.map((row) => row.reduce((x, y) => x + y, 0)), max: Math.max(0, ...cells.flat()) };
}
// 강도 — 0 이면 h0, 아니면 ⌈값 ÷ 최댓값 × 5⌉ (정수로 곱한 뒤 나눠 부동소수 끝자리가 단계를 넘기지 않게)
const heatLevel = (v, max) => (v > 0 && max > 0 ? `h${Math.min(5, Math.ceil((v * 5) / max))}` : 'h0');

function answer4(f) {
  const js = (h) => n0(h.jsViews);
  if (!f.span) {
    const J = f.hourly.reduce((acc, h) => acc + js(h), 0);
    const P = f.hourly.reduce((acc, h) => acc + n0(h.humanPages), 0);
    if (J === 0) return [['최근 24시간 동안 스크립트가 돈 페이지 보기가 없다.'], t`사람 모양 페이지 ${int(P)}회.`];
    const best = f.hourly.reduce((x, h) => (js(h) > js(x) ? h : x));
    return [t`최근 24시간 중 화면이 가장 많이 뜬 때는 ${new Date(n0(best.ts) * 1000).getHours()}시(${int(js(best))}회)다.`, t`스크립트가 돈 페이지 보기 ${int(J)}회, 사람 모양 페이지 ${int(P)}회.`];
  }
  const grid = heatGrid(f.hourly);
  const out = [];
  if (grid.hours.some((v) => v > 0)) {
    const three = (h) => grid.hours[h] + grid.hours[(h + 1) % 24] + grid.hours[(h + 2) % 24];
    const h1 = grid.hours.reduce((best, _, h) => (three(h) > three(best) ? h : best), 0);
    // {h2} = {h1}+3 — 24 를 넘을 때만(자정을 넘김) 24 를 뺀다: 21 → '21~24시', 22 → '22~1시'
    out.push(t`화면이 실제로 뜬 페이지 보기는 ${h1}~${h1 + 3 > 24 ? h1 - 21 : h1 + 3}시에 가장 몰린다.`);
  }
  if (grid.max > 0) {
    const d = grid.cells.findIndex((row) => row.includes(grid.max));
    out.push(t`가장 많은 칸은 ${WEEKDAY[d]} ${grid.cells[d].indexOf(grid.max)}시(${int(grid.max)}회).`);
  }
  const today = kstDay(n0(f.a.endTs));
  const days = f.vis ? list(f.vis.days).filter((d) => isObj(d) && d.ts !== today).map((d) => n0(d.confirmed)) : [];
  if (days.length) out.push(t`날마다 확인 방문자는 ${int(Math.min(...days))}~${int(Math.max(...days))}명이다.`);
  return out;
}

function answer5(f) {
  const hp = n0(f.T.humanPages);
  const paths = rows2(f.a.paths);
  const tabs = rows2(f.a.tabs);
  const tabSum = total(tabs, 1);
  const first = [];
  const t1 = tabSum > 0 && tabs.length ? tabs[0] : null;
  if (hp > 0 && paths.length) {
    // 방문자가 정한 경로 — 60자에서 자르고 전체는 그 b 의 title(§3.10)
    const label = clean(paths[0][0]);
    const u1 = label.length > 60 ? { b: `${label.slice(0, 60)}…`, title: label } : { b: label };
    first.push('사람 모양 페이지는 ', u1, ...t` 경로가 ${pct(paths[0][1], hp)}%로 가장 많`, t1 ? '고, ' : '다.');
  }
  if (t1) first.push(...t`대시보드는 ${tabName(t1[0])} 탭으로 들어온 경우가 ${pct(t1[1], tabSum)}%다.`);
  const out = [first];
  const device = nameIn(DEVICE_NAME);
  if (!f.pre && f.vis) {
    const devices = rows3(f.vis.devices);
    const [d1] = byConfirmed(devices);
    if (d1) out.push(t`기기는 ${device(d1[0])} ${pct(d1[1], total(devices, 1))}%(${'확인 방문자 기준'}).`);
  } else if (f.pre) {
    const [d1] = rows2(f.a.devices);
    if (d1 && hp > 0) out.push(t`기기는 ${device(d1[0])} ${pct(d1[1], hp)}%(${'페이지 줄 기준'}).`);
  }
  return out;
}

function answer6(f) {
  const [e, w, f4, pr] = [n0(f.S['5xx']), n0(f.S.ws5xx), n0(f.S['4xx']), n0(f.T.probes)];
  const first = e === 0 ? ['서버 오류(5xx)는 없었고, '] : t`서버 오류(5xx)는 ${int(e)}건, `;
  const ws = [...t`대시보드 재접속 실패는 ${int(w)}건`, ...(w > 0 ? ['(배포 때 몇 건은 정상)'] : []), '이다.'];
  return [[...first, ...ws], t`4xx 는 ${int(f4)}건, 탐색 경로 요청은 ${int(pr)}줄이다.`];
}

// 덩어리 여섯의 답 — 조각 목록([글자 | { b }])
const answers = (a) => [answer1, answer2, answer3, answer4, answer5, answer6].map((make) => sentences(make(facts(a))));
const plain = (segs) => segs.map((s) => (typeof s === 'string' ? s : s.b)).join('');

// 값 자리(b)는 바로 뒤 글자(공백 전까지 — '%다.'·'줄')와 한 덩어리로 줄을 바꾸지 않는다(좁은 폭에서 '41%' / '다.'·
// '10-' / '05' 로 갈리지 않게). 20자를 넘는 값(경로)은 그 안에서도 줄을 바꾸고, 자른 값의 전체는 그 b 의 title(§3.10)
function sentence(node, segs) {
  const kids = [];
  let glue = null; // 직전 값을 품은 줄 안 바꿈 span
  for (const s of segs) {
    if (typeof s !== 'string') {
      const b = el('b', null, s.b);
      if (s.title) b.title = clean(s.title);
      glue = s.b.length > 20 ? null : el('span', 'nb');
      if (glue) glue.append(b);
      kids.push(glue ?? b);
      continue;
    }
    const text = clean(s);
    const cut = glue ? text.search(/\s|$/) : 0;
    if (cut) glue.append(text.slice(0, cut));
    if (cut < text.length) kids.push(text.slice(cut));
    glue = null;
  }
  node.replaceChildren(...kids);
}

// --- 서버 기록 덩어리의 조각 (042 §3.4·§3.7·§3.9) ------------------------------------------------------

const opened = new Set(); // 연 접힘('n개 더'·덩어리 안 접힘) — 60초마다 다시 그려도 열린 채(036 '정상 n개' 와 같다)

function fold(key, summary, cls) {
  const box = el('details', cls);
  box.open = opened.has(key);
  box.addEventListener('toggle', () => (box.open ? opened.add(key) : opened.delete(key)));
  box.append(summary instanceof Node ? summary : clip(el('summary'), summary, 90));
  return box;
}

// 막대 행 — 이름·막대·값(같은 DOM 을 CSS 가 넓으면 한 줄, 480px 이하는 '이름 … 값' 아래 막대로). 이름은 줄을 바꾸고
// 자르지 않되 방문자 글자는 60자에서 자르고 전체는 title(§3.10). title 은 원래 키(이름표가 있으면)
function barRow(item, chart, value) {
  const row = el('div', `brow${item.zero ? ' zero' : ''}`);
  const name = clip(el('span', 'brow-name'), item.label, 60);
  if (item.iso) name.append(el('span', 'iso', item.iso));
  if (item.title) name.title = clean(item.title);
  const val = el('span', 'brow-val');
  val.append(...value);
  row.append(name, chart, val);
  return row;
}

// 겹친 두 막대 — 같은 눈금, 바깥 rect 브라우저 모양·안쪽 얇은 rect 확인. 값은 늘 'a / b'(a 굵게, §3.7)
function pairBar(c, s, top) {
  const x = (v) => ((Math.min(v, top) / top) * 100).toFixed(2);
  const root = frame(100, 10, 'pair', '확인과 브라우저 모양 막대');
  root.append(svg('rect', { x: 0, y: 0, width: 100, height: 10 }, 'track'), svg('rect', { x: 0, y: 0, width: x(s), height: 10 }, 'shaped'));
  root.append(svg('rect', { x: 0, y: 3, width: x(c), height: 4 }, 'confirmed'));
  return tip(root, `확인 ${int(c)} / 브라우저 모양 ${int(s)}`);
}
const pairValue = (c, s) => [el('b', null, int(c)), ` / ${int(s)}`];

function oneBar(frac, cls) {
  const f = Math.min(1, Math.max(0, frac));
  const root = frame(100, 10, 'pair', `비율 ${(f * 100).toFixed(1)}%`);
  root.append(svg('rect', { x: 0, y: 0, width: 100, height: 10 }, 'track'), svg('rect', { x: 0, y: 0, width: (f * 100).toFixed(2), height: 10 }, cls));
  return tip(root, `${(f * 100).toFixed(1)}%`);
}

// 긴 목록 — 위 5행만 펴고 나머지는 'n개 더 — 이름, 이름…' 접힘(§3.9)
function longList(key, items, make) {
  if (!items.length) return [el('p', 'empty', '기록 없음')];
  const out = items.slice(0, 5).map(make);
  const rest = items.slice(5);
  if (rest.length) {
    const more = fold(key, `${rest.length}개 더 — ${rest.map((it) => clean(it.label)).join(', ')}`, 'more');
    more.append(...rest.map(make));
    out.push(more);
  }
  return out;
}

// 이름 → { label, title, iso } — 이름표가 있으면 한국어 이름과 title 에 원래 키, 나라는 지역 이름 + ISO 작게
const labeled = (table) => (key) => (own(table, key) ? { label: table[key], title: key } : { label: clean(key) });
const country = (key) => {
  const name = countryName(key);
  return name ? { label: name, iso: key, title: key } : { label: clean(key) };
};

// 확인·모양 짝 목록([[이름, c, s]]) — 겹친 두 막대
function pairList(key, rows, name) {
  const top = Math.max(1, ...rows.map((r) => r[2]));
  const items = rows.map(([k, c, s]) => ({ ...name(k), c, s }));
  return longList(key, items, (it) => barRow(it, pairBar(it.c, it.s, top), pairValue(it.c, it.s)));
}

// 페이지 줄 목록([[이름, 수]]) — 한 막대 + %(분모 = 사람 모양 페이지 또는 그 목록의 합)
function shareList(key, rows, name, denom) {
  const items = rows.map(([k, v]) => ({ ...name(k), v }));
  return longList(key, items, (it) => {
    const r = share(it.v, denom);
    return barRow(it, oneBar(r, 'shaped'), [el('b', null, int(it.v)), ` · ${Math.round(r * 100)}%`]);
  });
}

function column(title, sub, content) {
  const col = el('div', 'col');
  const top = el('div', 'col-head');
  top.append(el('b', null, title), el('span', null, sub));
  col.append(top, ...content);
  return col;
}

function keys(pairs) {
  const row = el('div', 'keys');
  row.append(...pairs.map(([cls, text]) => el('span', `key${cls ? ` ${cls}` : ''}`, text)));
  return row;
}

// 짝 단위 칸이 비었을 때 — 시행 전이면 시행일 빈 상태(036 §3.5 의 before_gate 예외), 아니면 036 상태 글
function pairNote(f, part, tail = '') {
  if (f.pre || part?.code === 'before_gate') return el('p', 'state-msg', `처리방침 개정 시행(${f.D} 00:00) 뒤부터 센다${tail}`);
  return stateMsg(isObj(part) ? part : BAD_SHAPE, CAUSE.access);
}
const DBIP_TAIL = '. 그 전에는 DB-IP 자료를 받지도 않는다';

// geo 상태 한 줄(§3.8) — 받는 중·실패·지난달 판. ok 이고 이번 달 판이면 null
function geoNote(f) {
  const g = f.geo;
  if (!g || f.pre || g.code === 'before_gate') return pairNote(f, g, DBIP_TAIL);
  if (g.state === 'pending') return el('p', 'state-msg', '자료 받는 중 — 다음 갱신(60초)에 찬다');
  if (g.state !== 'ok') return marked('warn', `받지 못함(${clean(g.code ?? g.state)}) — 1시간 뒤 다시`);
  return oldMonth(f) ? marked('warn', `지난달 판 ${clean(g.month)} 으로 셈`) : null;
}
// 지난달 판 — month 가 응답 endTs 의 UTC 달보다 이르다(브라우저 시계를 쓰지 않는다)
const oldMonth = (f) => typeof f.geo?.month === 'string' && f.geo.month < new Date(n0(f.a.endTs) * 1000).toISOString().slice(0, 7);

// ① 몇 명
function q1(f) {
  const { T, vis: v } = f;
  const out = [];
  if (v?.capped) out.push(marked('warn', '짝 기록이 1,000에 닿은 날이 있다 — 방문자 수가 실제보다 적다'));
  const range = el('div', 'stat');
  range.append(el('span', 'stat-label', f.span ? '확인 ~ 브라우저 모양 · 하루 평균' : '확인 ~ 브라우저 모양'));
  const big = el('span', 'range-num');
  const [c, s, r] = v ? [n0(v.confirmed), n0(v.shaped), n0(v.returning)] : [0, 0, 0];
  const fmt = (x) => (f.span ? perDay(f.a, v, x) ?? '–' : int(x));
  if (v) big.append(el('b', null, fmt(c)), `\u00a0~ ${fmt(s)}`); // '~' 앞은 줄 안 바꿈 — 좁으면 '~' 뒤에서 나뉜다
  else big.textContent = '—';
  range.append(big, el('span', 'stat-sub', !v ? '방문자(날마다 셈)' : f.span ? '명/일' : '방문자(날마다 셈)'));
  const tiles = el('div', 'tiles');
  tiles.append(
    range,
    stat('다시 온', v ? int(r) : '—', null, v ? (c > 0 ? `확인의 ${pct(r, c)}%` : '확인 0') : f.pre ? '시행 뒤부터' : ''),
    stat('스크립트가 돈 페이지', int(T.jsViews), null, `사람 모양 페이지 ${int(n0(T.humanPages))} 중`),
    stat('사람 모양 페이지', int(T.humanPages), null, '위장 봇 섞임'),
  );
  out.push(tiles);
  if (!v) return [...out, pairNote(f, f.a.visitors)];
  const band = frame(100, 12, 'lohi', '하한–상한 띠 — 확인과 브라우저 모양');
  band.append(svg('rect', { x: 0, y: 0, width: 100, height: 12 }, s > 0 ? 'shaped' : 'track'));
  band.append(svg('rect', { x: 0, y: 0, width: (share(Math.min(c, s), s) * 100).toFixed(2), height: 12 }, 'confirmed'));
  const ends = el('div', 'ends');
  const low = el('span', null, '적어도 이만큼은 사람 — 확인 ');
  low.append(el('b', null, fmt(c)));
  ends.append(low, el('span', null, `많아야 이만큼 — 브라우저 모양 ${fmt(s)}`));
  out.push(tip(band, `확인 ${fmt(c)} / 브라우저 모양 ${fmt(s)}`), ends);
  return out;
}

function step(label, value, sub) {
  const box = el('div', 'step');
  box.append(el('span', 'step-label', label), el('span', 'step-val', value));
  if (sub) box.append(el('span', 'step-sub', sub));
  return box;
}

function clue(value, label, sub) {
  const row = el('div', 'clue');
  row.append(el('span', 'clue-val', value), el('span', null, label));
  if (sub instanceof Node) {
    sub.classList.add('clue-sub');
    row.append(sub);
  } else if (sub) row.append(el('span', 'clue-sub', sub));
  return row;
}

// ② 누가
function q2(f) {
  const { T, vis: v } = f;
  const [req, pages, hp, js] = [n0(T.requests), n0(T.pages), n0(T.humanPages), n0(T.jsViews)];
  const ofPrev = (x, prev) => (prev > 0 ? `앞 칸의 ${pct(x, prev)}%` : '');
  const funnel = el('div', 'funnel');
  const c = v ? n0(v.confirmed) : 0;
  funnel.append(
    step('모든 요청', int(req)),
    step('페이지', int(pages), ofPrev(pages, req)),
    step('사람 모양 페이지', int(hp), ofPrev(hp, pages)),
    step('스크립트가 돈 페이지', int(js), ofPrev(js, hp)),
    step('확인 방문자(날마다 셈)', v ? int(c) : '—', v ? (c > 0 ? `1명이 평균 ${AVG.format(js / c)}쪽` : '') : '시행 뒤부터'),
  );
  const classes = isObj(f.a.classes) ? f.a.classes : {};
  const kinds = CLASSES.map((k) => {
    const [r, p] = [n0(classes[k]?.requests), n0(classes[k]?.pages)];
    return barRow({ label: CLASS_NAME[k], title: k, zero: !r && !p }, oneBar(share(r, req), k === 'browser' ? 'confirmed' : 'bot'), [`${int(r)} · ${int(p)}`]);
  });
  const probes = n0(T.probes);
  kinds.push(el('p', 'muted small', req > 0 ? `탐색 경로 ${int(probes)}줄(요청의 ${pct(probes, req)}%)` : `탐색 경로 ${int(probes)}줄`));
  const g = f.geo;
  const cloud = g?.state === 'ok' ? rows3(g.networks).find((r) => r[0] === 'cloud') ?? ['cloud', 0, 0] : null;
  const gateSub = pairNote(f, f.a.visitors);
  // cloud 줄 — 확인 수에 geo 상태 글(지난달 판 ▲ 등)을 덧붙인다(§3.4 ②·§3.8)
  let cloudSub = geoNote(f);
  if (cloud) {
    const both = el('span', null, `그중 확인 ${int(cloud[1])}`);
    if (cloudSub) both.append(' · ', cloudSub);
    cloudSub = both;
  }
  const clues = [
    clue(v ? int(n0(v.shaped) - c) : '—', '스크립트가 한 번도 돌지 않은 브라우저 모양', v ? '날마다 셈' : gateSub),
    clue(cloud ? int(cloud[2]) : '—', '데이터센터·클라우드 망의 브라우저 모양', cloudSub),
    clue(int(n0(classes.operator?.requests)), '운영자 흔적 요청', '방문자에서 뺐다'),
  ];
  const cols = el('div', 'cols2');
  cols.append(column('종류 여덟', '요청 비율 · 요청 · 페이지', kinds), column('위장 봇 단서', '사람 수에서 덜어 볼 것', clues));
  return [funnel, cols];
}

// ③ 어디서
function q3(f) {
  const { vis: v, geo: g } = f;
  const geoOk = g?.state === 'ok' && !f.pre;
  // 상태 글은 칸마다 새로 만든다 — 노드 하나를 두 칸에 붙이면 나중 칸으로 옮겨 가 나라 칸이 빈다(§3.8 — 나라·망 칸에 같은 글)
  const geoCol = (key, rows, name) => {
    const note = geoNote(f);
    return [...(note ? [note] : []), ...(geoOk ? pairList(key, rows, name) : [])];
  };
  const nets = geoOk ? rows3(g.networks) : [];
  const hasKr = nets.some((r) => r[0] === 'telecom_kr');
  // 망은 다섯 고정 순서(모르는 키는 뒤), 국내 통신사 행이 없는 창의 telecom 은 '통신사'
  const ordered = [...NETS.flatMap((k) => nets.filter((r) => r[0] === k)), ...nets.filter((r) => !NETS.includes(r[0]))];
  const netName = (k) => (k === 'telecom' && !hasKr ? { label: '통신사', title: k } : labeled(NET_NAME)(k));
  const pairsOr = (key, rows, name) => (v ? pairList(key, rows, name) : [pairNote(f, f.a.visitors)]);
  const hp = n0(f.T.humanPages);
  const top = el('div', 'cols3');
  top.append(
    column('나라', geoOk ? `IPv6 ${int(g.ipv6)} 따로` : '', geoCol('q3-countries', rows3(g?.countries), country)),
    column('망 종류', '', geoCol('q3-nets', ordered, netName)),
    column('들어온 길', '그날 첫 페이지', pairsOr('q3-channels', rows3(v?.channels), labeled(CHANNEL_NAME))),
  );
  const second = el('div', 'cols3');
  second.append(
    column('앱 안 브라우저', '', pairsOr('q3-apps', rows3(v?.inApp), labeled(APP_NAME))),
    column('외부 출처', '페이지 줄 기준', shareList('q3-refs', rows2(f.a.referrers), (k) => ({ label: clean(k) }), hp)),
    column('utm', '페이지 줄 기준', shareList('q3-utm', rows2(f.a.utmSources), (k) => ({ label: clean(k) }), hp)),
  );
  return [keys([['', '확인(하한)'], ['shaped', '브라우저 모양(상한)']]), top, second];
}

// ③ 바닥의 geo 상태 배지 — DB-IP 링크는 index.html 에 고정(상태와 무관하게 늘 보인다)
function geoBadge(f) {
  const g = f.geo;
  if (!g || f.pre || g.code === 'before_gate') return [badge('dim', '나라·망 자료 — 시행 뒤부터')];
  if (g.state === 'pending') return [badge('wait', '자료 받는 중')];
  if (g.state !== 'ok') return [badge('warn', `받지 못함(${clean(g.code ?? g.state)})`)];
  const since = n0(g.sinceTs) * 1000;
  const tone = oldMonth(f) ? 'warn' : 'ok';
  const tag = el('span', `tag t-${tone}`);
  // 올린 때의 경과는 data-at 글자 — 본문을 다시 그리지 않아도 그리기 끝(retick)마다 고친다
  tag.append(el('span', 'mk', GLYPH[tone]), `${tone === 'warn' ? '지난달 판 · ' : ''}자료 ${clean(g.month)} 판 · `, agoSpan(g.loadedAt), ` 올림 · ${md(since)} ${hm(since)} 부터 셈 · IPv6 ${int(g.ipv6)}`);
  return [tag];
}

// 시 눈금 — 24칸 격자에 정시(0·6·12·18시) 글자만, 마지막 칸은 끝 글자(§3.4 ④ — 5등분 HH:mm 을 쓰지 않는다).
// 끝 글자 앞 두 칸의 정시는 비운다 — 좁은 폭에서 오른쪽 끝 '지금' 과 겹치지 않게
function hourAxis(hours, last) {
  const row = el('div', 'hours');
  const end = hours.length - 1;
  row.append(...hours.map((h, i) => el('span', null, i === end ? last : h % 6 === 0 && i < end - 2 ? `${h}시` : '')));
  return row;
}

// 24시간 시간 막대 — 회색 = 사람 모양 페이지, 그 안 진한 막대 = 스크립트가 돈 페이지, 빨강 = 5xx 겹침(최소 높이).
// 대시보드 재접속 실패는 title 에만
function hourBars(hourly) {
  const H = 60;
  const top = Math.max(1, ...hourly.map((h) => Math.max(n0(h.humanPages), n0(h.jsViews), n0(h.errors))));
  const root = frame(hourly.length * 10, H, 'tall', `시간 막대 24시간 — 한 시간 최고 ${int(top)}`);
  root.append(svg('line', { x1: 0, x2: hourly.length * 10, y1: H, y2: H }, 'floor'));
  hourly.forEach((h, i) => {
    const g = svg('g');
    const bar = (v, x, w, cls, min = 0) => v > 0 && g.append(svg('rect', { x: i * 10 + x, y: H - Math.max(min, (v / top) * H), width: w, height: Math.max(min, (v / top) * H) }, cls));
    bar(n0(h.humanPages), 1, 8, 'shaped');
    bar(n0(h.jsViews), 3, 4, 'confirmed');
    bar(n0(h.errors), 1, 8, 'err', 1.5);
    g.append(svg('rect', { x: i * 10, y: 0, width: 10, height: H }, 'hit'));
    const at = n0(h.ts) * 1000;
    root.append(tip(g, `${md(at)} ${hm(at)} · 사람 모양 페이지 ${int(h.humanPages)} · 스크립트가 돈 페이지 ${int(h.jsViews)} · 5xx ${int(h.errors)} · 대시보드 재접속 실패 ${int(h.wsErrors)}`));
  });
  return root;
}

// 7일·30일 날 막대 — 칸은 시행일로 자르기 전 창이 덮는 KST 날 전부, 시행 전 날들은 회색 덩어리 하나(§3.8)
function dayBars(f) {
  const v = f.vis;
  const win = winOf(f.a);
  const N = WINDOW_HOURS[win];
  const gateDay = kstDay(n0(f.a.gateAt) / 1000);
  const cols = [];
  for (let d = kstDay(naturalStart(f.a)); d <= kstDay(n0(f.a.endTs)); d += 86_400) cols.push(d);
  const before = cols.filter((d) => d < gateDay).length;
  const byDay = new Map(list(v.days).filter(isObj).map((d) => [d.ts, d]));
  const W = cols.length * 10;
  const H = 60;
  const top = Math.max(1, ...[...byDay.values()].map((d) => n0(d.confirmed)));
  const topS = Math.max(1, ...[...byDay.values()].map((d) => n0(d.shaped)));
  const days = frame(W, H, 'days', `날 막대 — 확인 방문자 최고 ${int(top)}`);
  const strip = frame(W, 16, 'strip', `브라우저 모양 얇은 줄 — 최고 ${int(topS)}`);
  days.append(svg('line', { x1: 0, x2: W, y1: H, y2: H }, 'floor'));
  if (before) {
    const label = `개정 전 ${before}일 — 세지 않음`;
    days.append(tip(svg('rect', { x: 0, y: 0, width: before * 10, height: H }, 'pre'), label));
    strip.append(tip(svg('rect', { x: 0, y: 0, width: before * 10, height: 16 }, 'pre'), label));
  }
  cols.forEach((ts, i) => {
    const d = byDay.get(ts);
    if (i < before || !d) return;
    const [c, r, s] = [n0(d.confirmed), n0(d.returning), n0(d.shaped)];
    const g = svg('g');
    if (c > 0) g.append(svg('rect', { x: i * 10 + 1, y: H - (c / top) * H, width: 8, height: (c / top) * H }, 'confirmed'));
    if (r > 0) g.append(svg('rect', { x: i * 10 + 1, y: H - (r / top) * H, width: 8, height: (r / top) * H }, 'returning'));
    g.append(svg('rect', { x: i * 10, y: 0, width: 10, height: H }, 'hit'));
    days.append(tip(g, `${kstMd(ts * 1000)} · 확인 ${int(c)} · 다시 온 ${int(r)} · 브라우저 모양 ${int(s)}`));
    if (s > 0) strip.append(tip(svg('rect', { x: i * 10 + 1, y: 16 - (s / topS) * 16, width: 8, height: (s / topS) * 16 }, 'shaped'), `${kstMd(ts * 1000)} · 브라우저 모양 ${int(s)}`));
  });
  const full = kstMd(n0(f.a.gateAt) + (N - 1) * 3_600_000);
  const out = [];
  if (before >= 8) out.push(el('p', 'pre-label', `개정 전 ${before}일 — 세지 않음 · ${full} 부터 ${WINDOW_NAME[win]}이 다 찬다`));
  const legend = [['', '확인'], ['returning', '다시 온(아랫부분)'], ['shaped', '브라우저 모양(아래 줄)']];
  if (before && before < 8) legend.push(['pre', `개정 전 ${before}일 — 세지 않음`]);
  out.push(days, axis(`${kstMd(cols[0] * 1000)} (KST)`, kstMd(cols[cols.length - 1] * 1000)), strip, el('p', 'muted small', `브라우저 모양 · 눈금 따로 · 최고 ${int(topS)}`), keys(legend));
  return out;
}

// 요일×시간 열지도 — 칸 7×24(rect + 강도 클래스), 오른쪽 요일 합(HTML), 아래 시간 합 막대와 단계 견본
function heatmap(f) {
  const grid = heatGrid(f.hourly);
  const map = frame(240, 126, 'heatmap', `요일×시간 — 스크립트가 돈 페이지 보기, 가장 많은 칸 ${int(grid.max)}`);
  grid.cells.forEach((row, d) => row.forEach((v, h) => map.append(tip(svg('rect', { x: h * 10 + 0.5, y: d * 18 + 1, width: 9, height: 16 }, heatLevel(v, grid.max)), `${WEEKDAY[d]} ${h}시 · ${int(v)}회`))));
  const topH = Math.max(1, ...grid.hours);
  const sums = frame(240, 24, 'hoursum', `시간 합 — 최고 ${int(Math.max(...grid.hours))}`);
  grid.hours.forEach((v, h) => v > 0 && sums.append(tip(svg('rect', { x: h * 10 + 1, y: 24 - (v / topH) * 24, width: 8, height: (v / topH) * 24 }, 'confirmed'), `${h}시 합 ${int(v)}`)));
  const names = el('div', 'heat-rows');
  names.append(...WEEKDAY.map((w) => el('span', null, w.slice(0, 1))));
  const rowSums = el('div', 'heat-rows sums');
  rowSums.append(...grid.days.map((v) => el('span', null, int(v))));
  const heat = el('div', 'heat');
  heat.append(names, map, rowSums, el('span'), sums, el('span'), el('span'), hourAxis([...Array(24).keys()], '24시'), el('span'));
  const scale = el('div', 'scale');
  scale.append('적음 ', ...[1, 2, 3, 4, 5].map((i) => el('span', `sw h${i}`)), ` 많음(최고 ${int(grid.max)})`);
  return [el('p', 'muted small', '요일×시간 — 스크립트가 돈 페이지 보기 · 이 브라우저 시간대 · 오른쪽 요일 합 · 아래 시간 합'), heat, scale];
}

// ④ 언제
function q4(f) {
  if (!f.span) {
    const out = [];
    if (f.hourly.length) out.push(hourBars(f.hourly), hourAxis(f.hourly.map((h) => new Date(n0(h.ts) * 1000).getHours()), '지금'));
    else out.push(el('p', 'empty', '기록 없음'));
    out.push(keys([['shaped', '사람 모양 페이지'], ['', '스크립트가 돈 페이지'], ['err', '5xx']]), el('p', 'muted small', '요일×시간 열지도와 날 막대는 7일·30일 창에서'));
    return out;
  }
  return [...(f.vis ? dayBars(f) : [pairNote(f, f.a.visitors)]), ...heatmap(f)];
}

// ⑤ 무엇을
function q5(f) {
  const { vis: v } = f;
  const hp = n0(f.T.humanPages);
  const tabs = rows2(f.a.tabs);
  const pairsOr = (key, rows, name) => (v ? pairList(key, rows, name) : [pairNote(f, f.a.visitors)]);
  const top = el('div', 'cols3');
  top.append(
    column('많이 연 경로', '사람 모양 페이지 줄', shareList('q5-paths', rows2(f.a.paths), (k) => ({ label: clean(k) }), hp)),
    column('대시보드 진입 탭', '탭 합 기준', shareList('q5-tabs', tabs, (k) => ({ label: tabName(k), title: k }), total(tabs, 1))),
    column('기기', '방문자 기준', pairsOr('q5-devices', rows3(v?.devices), labeled(DEVICE_NAME))),
  );
  const byVisitor = fold('q5-os', 'OS·브라우저 — 방문자 기준', 'more');
  const vcols = el('div', 'cols2');
  vcols.append(column('OS', '확인 / 모양', pairsOr('q5-os-rows', rows3(v?.os), labeled(OS_NAME))), column('브라우저', '확인 / 모양', pairsOr('q5-br-rows', rows3(v?.browsers), labeled(BROWSER_NAME))));
  byVisitor.append(vcols);
  const byLine = fold('q5-lines', '기기·브라우저 — 페이지 줄 기준', 'more');
  const lcols = el('div', 'cols2');
  lcols.append(
    column('기기', '사람 모양 페이지 대비', shareList('q5-dev-lines', rows2(f.a.devices), labeled(DEVICE_NAME), hp)),
    column('브라우저', '사람 모양 페이지 대비', shareList('q5-br-lines', rows2(f.a.browsers), labeled(BROWSER_NAME), hp)),
  );
  byLine.append(lcols);
  return [keys([['', '확인(하한)'], ['shaped', '브라우저 모양(상한)']]), top, byVisitor, byLine];
}

// ⑥ 문제
function q6(f) {
  const { S, T } = f;
  const req = n0(T.requests);
  const table = el('table', 'table');
  const tbody = el('tbody');
  for (const [key, label] of [['2xx', '2xx'], ['3xx', '3xx'], ['4xx', '4xx'], ['5xx', '5xx'], ['ws5xx', '대시보드 재접속 실패']]) {
    const tr = el('tr');
    const n = n0(S[key]);
    const tone = key === '5xx' && n ? 'bad' : null;
    const name = cell(tr, tone ? marked(tone, label) : label);
    name.title = key;
    cell(tr, ratio(share(n, req), tone, `${label} 요청 대비`), 'share-bar');
    cell(tr, int(n), 'num');
    cell(tr, `${(share(n, req) * 100).toFixed(1)}%`, 'num');
    tbody.append(tr);
  }
  table.append(tbody);
  const wrap = el('div', 'scroll');
  wrap.append(table);
  const ws = isObj(f.a.ws) ? f.a.ws : {};
  const tiles = el('div', 'tiles');
  tiles.append(
    stat('끝난 연결', int(ws.count), null, '연결 수 — 사람 수가 아니다'),
    stat('연결한 방문자', f.vis && num(ws.pairs) !== null ? int(ws.pairs) : '—', null, f.vis ? '날마다 셈' : f.pre ? `시행(${f.D} 00:00) 뒤부터` : ''),
    stat('재접속 실패', int(ws.errors), null, '대시보드 WebSocket 5xx'),
  );
  const durations = isObj(ws.durations) ? ws.durations : {};
  const lasting = fold('q6-durations', '지속 시간 — 머문 시간이 아니다', 'more');
  const labels = el('div', 'cols5');
  labels.append(
    ...WS_BUCKETS.map(([k, label]) => {
      const span = el('span', null, label);
      span.append(el('b', null, int(durations[k])));
      return span;
    }),
  );
  lasting.append(columns(WS_BUCKETS.map(([k, label]) => ({ value: n0(durations[k]), over: 0, tip: `${label} · ${int(durations[k])}` })), `대시보드 연결 지속 시간 구간 — 끝난 연결 ${int(ws.count)}`), labels);
  return [wrap, el('p', 'muted small', `탐색 경로 ${int(T.probes)}줄`), el('p', 'muted small', '대시보드 연결'), tiles, lasting];
}

// 최근 5xx(036 의 접힘을 ⑥ 으로 옮김) — 정적 details 라 펼침이 남는다
function recent5xx(a) {
  const recent = list(a.recent5xx).filter(isObj).slice(0, 20);
  $('r5xx').hidden = false;
  $('r5xx-sum').textContent = `${recent.length ? `최근 5xx ${recent.length}건` : '최근 5xx 없음'} — 대시보드 재접속 실패 제외`;
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

// 덩어리 머리 meta — 늘 창(시행 뒤 '날마다 센 방문자', 시행 전 '줄 수')
const qMeta = (a) => `${WINDOW_NAME[winOf(a)]} · ${list(a.windows).includes('7d') ? '날마다 센 방문자' : '줄 수'}`;
const BLOCKS = [q1, q2, q3, q4, q5, q6];

function fillTraffic(a) {
  const f = facts(a);
  const said = answers(a);
  for (const q of QS) {
    $(`m-q${q}`).textContent = qMeta(a);
    sentence($(`a-q${q}`), said[q - 1]);
    $(`b-q${q}`).replaceChildren(...BLOCKS[q - 1](f));
  }
  $('geo-state').replaceChildren(...geoBadge(f));
  recent5xx(a);
}

// 덩어리 여섯을 상태 글로 — 고른 창을 불러오는 중(part 없음)이거나 접속 부분을 못 그릴 때(036 §3.5)
function blankTraffic(part) {
  for (const q of QS) {
    $(`m-q${q}`).textContent = WINDOW_NAME[picked];
    $(`a-q${q}`).replaceChildren();
    const msg = part === undefined ? el('p', 'state-msg', `… ${WINDOW_NAME[picked]} 불러오는 중`) : part.state === 'error' ? el('p', 'state-msg', `불러오지 못함${part.code ? ` — ${clean(part.code)}` : ''}`) : stateMsg(part, CAUSE.access);
    $(`b-q${q}`).replaceChildren(msg);
  }
  $('geo-state').replaceChildren();
  $('r5xx').hidden = true;
}

// 절 요약 앞 조각(§3.3) — 7일·30일 '확인 방문자 하루 a명', 24시간(시행 뒤) '24시간 확인 a', 시행 전 '24시간 스크립트가 돈 페이지 j'
function trafficLead(a) {
  const v = visOf(a);
  if (!list(a.windows).includes('7d')) return `24시간 스크립트가 돈 페이지 ${int(a.totals?.jsViews)}`;
  if (winOf(a) === '24h') return `24시간 확인 ${v ? int(v.confirmed) : '–'}`;
  return `확인 방문자 하루 ${v ? perDay(a, v, v.confirmed) ?? '–' : '–'}명`;
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
  // 서버 기록(042) — 고른 창으로 요청한 응답만 그린다(다른 창의 값을 새 창 이름 아래 두지 않는다)
  const entry = got.get(P.access);
  const current = entry !== undefined && entry.asked === picked;
  const access = current ? partOf(P.access) : undefined;
  drawWindowBar(entry, access, current);
  const ok = usable(access);
  redraw('traffic', [access ?? picked], () => (ok ? fillTraffic(access) : blankTraffic(access)));
  const clarity = partOf(P.clarity);
  if (!region('clarity', clarity, CAUSE.clarity, fillClarity, 'numOfDays')) $('clarity-more').hidden = true;
  const now = `지금 ${st?.body ? int(st.body.wsConnections) : '–'} 접속`;
  if (!ok) {
    const [tone, word] = softWord(access);
    return $('s-traffic').replaceChildren(marked(st ? tone : 'wait', `${now} · 서버 기록 ${word}`));
  }
  // 5xx 는 판정 밖이라 줄 전체를 칠하지 않고 그 조각만 장애색으로(수치·표와 같게)
  const parts = [marked(st ? 'ok' : 'wait', `${trafficLead(access)} · ${now}`)];
  const fives = n0(access.status?.['5xx']);
  if (fives) parts.push(marked('bad', `5xx ${int(fives)}`));
  $('s-traffic').replaceChildren(...parts);
}

// --- 화면 이용 (053) — 값 계산·이름표 ------------------------------------------------------------

// 덮어 보기로 띄우는 공개 사이트 — 틀 주소·postMessage 대상·받는 메시지 출처가 모두 이 값이다(이 파일에 한 번).
// 로컬 확인은 커밋하지 않는 시험 사본에서 바꾼다(053 §3.4 — attention.js 의 ADMIN_ORIGIN 과 짝)
const SITE_ORIGIN = 'https://kimptrack.com';
const SCREENS_MS = 300_000; // 절이 보이는 동안 5분마다
const FRAME_WAIT_MS = 5_000; // 틀이 이 안에 준비 신호를 보내지 않으면 '페이지가 응답하지 않음'
const SMALL_PV = 5; // 표본 적음 — 페이지뷰가 이보다 적다
const LOW_REACH = 20; // 거의 안 닿음 — 도달률(%)이 이보다 낮다
const AREA_ID = /^[a-z][a-z0-9-]{0,31}$/; // 052 영역 id 꼴
const DAYS = [1, 7, 30, 90];
const PERIOD_NAME = { 1: '오늘', 7: '최근 7일', 30: '최근 30일', 90: '최근 90일' };
// 화면 이름(052 §3.1) → 고르기 이름. 이 순서가 고르기 순서이고, 기본 화면에서 pv 가 같으면 앞의 것
const PAGE_NAME = {
  landing: '랜딩',
  'app-spread': '대시보드 스프레드',
  'app-history': '대시보드 기록',
  'app-gap': '대시보드 갭',
  'app-pp': '대시보드 선선갭',
  'app-health': '대시보드 수집 상태',
  'app-flow': '대시보드 입출금 레이더',
  privacy: '처리방침',
  'kimp-chart': '김프 차트',
  'kimp-history': '김프 기록',
};
const SCREEN_PAGES = Object.keys(PAGE_NAME);
const STATIC_PATH = { landing: '/', privacy: '/privacy', 'kimp-chart': '/kimp-chart', 'kimp-history': '/kimp-history' };
const DEVICE_SCREEN = { pc: 'PC', mobile: '휴대폰' };
const FRAME_WIDTH = { pc: 1280, mobile: 390 }; // 틀 폭(높이는 admin.css — PC 800·휴대폰 844)
// 영역 이름표(§3.5) — 052 §7 '붙인 영역 목록' 의 화면별 id 전부, 화면에 적힌 낱말로 짧게. 대시보드 공통 셋은 "app"(탭마다
// 같다). 없는 id 는 id 그대로. JSON 꼴(큰따옴표)로 둔다 — server/tests/test_admin.py 가 읽어 052 의 영역을 모두 덮는지 본다
const AREA_NAMES = {
  "landing": { "top": "머리 막대", "hero": "첫 화면", "analyze": "히스토리 분석", "events": "지난 7일 사건", "kimp": "김프와 역프란", "method": "계산 방법", "faq": "자주 묻는 질문", "foot": "바닥" },
  "privacy": { "top": "머리 막대", "changes": "변경 안내", "glance": "한눈에 보기", "consent": "화면 분석 동의 관리", "s1": "1. 처리 목적", "s2": "2. 항목과 보유 기간", "s3": "3. 파기", "s4": "4. 제3자 제공", "s5": "5. 처리 위탁", "s6": "6. 국외 이전", "s7": "7. 자동 수집 장치", "s8": "8. 정보주체의 권리", "s9": "9. 안전성 확보 조치", "s10": "10. 보호책임자", "s11": "11. 권익침해 구제", "s12": "12. 방침의 변경", "foot": "바닥" },
  "kimp-chart": { "top": "머리 막대", "what": "볼 수 있는 것", "read": "읽는 법", "coins": "코인별 바로 가기", "faq": "자주 묻는 질문", "foot": "바닥" },
  "kimp-history": { "top": "머리 막대", "live": "지난 7일 사건", "what": "남는 것", "how": "보는 법", "faq": "자주 묻는 질문", "foot": "바닥" },
  "app": { "header": "로고·수집 상태", "tabs": "탭 단추", "kpi": "KPI 줄" },
  "app-spread": { "filters": "필터 바", "table": "김프 표" },
  "app-history": { "filters": "사건 필터", "table": "티커별 사건 표", "summary": "선택 심볼 요약", "list": "사건 로그", "filters-2": "차트 도구 줄", "chart": "봉 차트" },
  "app-gap": { "filters": "필터 바", "table": "갭 표" },
  "app-pp": { "filters": "필터 바", "table": "선선갭 표" },
  "app-health": { "summary": "요약", "cards": "거래소 카드", "chart": "실패 구간 타임라인", "list": "최근 실패 구간" },
  "app-flow": { "filters": "필터 바", "table": "코인별 순유입", "table-2": "최근 전송" }
};

function areaName(page, id) {
  const mine = own(AREA_NAMES, page) || {};
  const common = page.startsWith('app-') ? AREA_NAMES.app : {};
  return own(mine, id) ?? own(common, id) ?? clean(id);
}

const rowOf = (rows, page, device) => list(rows).find((r) => isObj(r) && r.page === page && r.device === device);

// 기본 화면(§3.2) — 고른 기간·기기에서 pv 가 가장 큰 화면(같으면 고르기 순서의 앞, 모두 0 이면 랜딩)
function busiest(rows, device) {
  let best = SCREEN_PAGES[0];
  let most = 0;
  for (const page of SCREEN_PAGES) {
    const pv = n0(rowOf(rows, page, device)?.pv);
    if (pv > most) [best, most] = [page, pv];
  }
  return best;
}

// 화면 하나 × 기기 하나의 값(§3.3). present = 페이지가 알린 영역 id 들(아직 모르면 null — 기록 있는 영역을 모두 페이지에
// 있는 것으로 본다). ranked = 페이지에 있고 기록 있는 영역(평균 보인 시간 큰 순 — 순위·단계는 이것끼리), blank = 페이지에
// 있는데 기록 없음(단계 0), away = 기록은 있는데 지금 페이지에 없음(목록 끝). 단계는 반올림 전 ms 로 정한다
function screenValues(row, present) {
  const pv = n0(row?.pv);
  const data = list(row?.areas).filter((a) => isObj(a) && typeof a.id === 'string' && AREA_ID.test(a.id));
  const here = present ? new Set(present) : null;
  const value = (a) => ({
    id: a.id,
    ms: n0(a.ms),
    t: pv ? Math.round(n0(a.ms) / pv / 100) / 10 : 0,
    r: pv ? Math.min(100, Math.round((n0(a.seen) / pv) * 100)) : 0,
    c: pv ? Math.round((n0(a.clicks) / pv) * 1000) / 10 : 0,
  });
  const on = data.filter((a) => !here || here.has(a.id)).map(value).sort((x, y) => y.ms - x.ms);
  const top = on.length ? on[0].ms : 0;
  const level = (ms) => (ms > 0 && top > 0 ? Math.min(5, Math.ceil((ms * 5) / top)) : 0);
  const small = pv < SMALL_PV;
  const mark = (a, rank, state) => ({ ...a, level: level(a.ms), rank, low: a.r < LOW_REACH, small, state });
  const known = new Set(data.map((a) => a.id));
  return {
    pv,
    small,
    ranked: on.map((a, i) => mark(a, i + 1, 'ok')),
    blank: here ? [...here].filter((id) => !known.has(id)).map((id) => ({ id, ms: 0, t: 0, r: 0, c: 0, level: 0, rank: null, low: false, small, state: 'none' })) : [],
    away: here ? data.filter((a) => !here.has(a.id)).map((a) => mark(value(a), null, 'gone')) : [],
  };
}

// 페이지로 넘기는 영역 값 — 지금 페이지에 있는 것만(기록 없음은 rank null·단계 0)
const overlayAreas = (v) => v.ranked.concat(v.blank).map(({ id, level, rank, t, r, c, low, small }) => ({ id, level, rank, t, r, c, low, small }));

// 답 문장(§3.5 — 042 의 답 줄 꼴, 값 자리는 굵게) — pv 0 · 보통 · 표본 적음(끝에 한 문장)
function screenAnswer(v, page, device, days) {
  if (!v.pv) return ['이 기간 이 화면·기기의 동의한 방문 기록이 없다.'];
  const out = t`${PERIOD_NAME[days]} ${PAGE_NAME[page]} ${DEVICE_SCREEN[device]} — 페이지뷰 ${int(v.pv)}.`;
  if (v.ranked.length) {
    const most = v.ranked[0];
    const least = v.ranked.reduce((lo, a) => (a.r <= lo.r ? a : lo));
    out.push(' ', ...t`가장 오래 본 곳은 ${areaName(page, most.id)}(평균 ${AVG.format(most.t)}초), 가장 덜 닿은 곳은 ${areaName(page, least.id)}(도달 ${least.r}%).`);
  } else out.push(' 영역 기록은 없다.');
  if (v.small) out.push(' ', ...t`표본 적음 — 페이지뷰가 ${SMALL_PV}보다 적어 단계·순위가 흔들린다.`);
  return out;
}

// 틀 주소 — 공개 사이트의 그 화면에 덮어 보기 표시. 대시보드는 탭을 쿼리로
const frameUrl = (page) => `${SITE_ORIGIN}${page.startsWith('app-') ? `/app/?tab=${page.slice(4)}&` : `${STATIC_PATH[page]}?`}kt-overlay=1`;

// --- 화면 이용 (053) — 고르기·틀·목록 -------------------------------------------------------------

// 고른 값은 이 변수에만(주소·브라우저 저장소 없음). page 는 고른 화면(고르기 전에는 기본 화면을 따른다 — chosen),
// inView = 절이 화면에 보인다, key = 지금 틀에 띄운 주소(+기기), ready = 그 틀이 알린 영역(없으면 null)
const scr = { days: 7, page: SCREEN_PAGES[0], chosen: false, device: 'pc', inView: false, key: '', ready: null, wait: 0, silent: false, lastOk: null };
const PAGE_OPTIONS = Array.from(document.querySelectorAll('#scr-page option'));
const DEVICE_BUTTONS = Array.from(document.querySelectorAll('[data-device]'));

// KST 날짜 YYYY-MM-DD (§3.6 — 시행일 D)
function kstDate(ms) {
  const d = new Date(ms + KST_SEC * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

// 틀로 보내기 — 대상 출처는 늘 공개 사이트 하나
function post(message) {
  const win = $('scr-frame').contentWindow;
  if (win) win.postMessage(message, SITE_ORIGIN);
}

// 틀 폭 맞추기 — 칸 폭 ÷ 틀 폭(휴대폰은 1 이하). 화면 스크립트가 쓰는 style 은 이 CSS 변수 하나뿐(036 §3.6 의 예외 — 053)
function fitFrame() {
  const stage = $('scr-stage');
  const fit = Math.min(1, stage.clientWidth / FRAME_WIDTH[scr.device]) || 1;
  stage.style.setProperty('--fit', fit.toFixed(4));
}

// 틀 주소(§3.4) — 값이 있고 이 절과 탭이 보이는 동안만 그 화면, 아니면 about:blank(대시보드는 실시간 연결을 연다).
// 주소나 기기가 바뀌면 다시 띄운다(페이지가 처음 폭으로 정하는 것이 있다). 주소는 frameUrl 이 SITE_ORIGIN 으로만 만든다
function aimFrame(url) {
  const stage = $('scr-stage');
  stage.hidden = !url;
  stage.className = `scr-stage ${scr.device}`;
  fitFrame();
  const want = url && scr.inView && document.visibilityState === 'visible' ? url : 'about:blank';
  const key = want === 'about:blank' ? want : `${want}|${scr.device}`;
  if (key !== scr.key) {
    scr.key = key;
    scr.ready = null;
    scr.silent = false;
    clearTimeout(scr.wait);
    $('scr-frame').src = want;
    if (want !== 'about:blank') {
      scr.wait = setTimeout(() => {
        scr.silent = scr.ready === null;
        noteFrame();
      }, FRAME_WAIT_MS);
    }
  }
  noteFrame();
}

function noteFrame() {
  const note = $('scr-note');
  note.hidden = scr.key === 'about:blank' || scr.ready !== null;
  note.textContent = scr.silent ? '페이지가 응답하지 않음 — 5초 안에 덮어 보기 준비 신호가 오지 않아 페이지만 보인다' : '페이지를 띄우는 중…';
}

// 틀의 준비 신호(§3.4) — 출처가 공개 사이트이고 창이 이 틀일 때만, 지금 고른 화면의 것만
function onFrameMessage(event) {
  const win = $('scr-frame').contentWindow;
  if (event.origin !== SITE_ORIGIN || !win || event.source !== win) return;
  const d = event.data;
  if (!isObj(d) || d.type !== 'kt-attention-ready' || d.v !== 1 || d.page !== scr.page || scr.key === 'about:blank') return;
  clearTimeout(scr.wait);
  scr.silent = false;
  const ids = list(d.areas).filter((id) => typeof id === 'string' && AREA_ID.test(id));
  scr.ready = { page: d.page, areas: [...new Set(ids)].slice(0, 40) };
  paint();
}

// 평균 보인 시간 막대 — 그 화면에서 가장 긴 영역 대비, 단계 색
function levelBar(frac, level, label) {
  const f = Math.min(1, Math.max(0, frac));
  const root = frame(100, 8, '', label);
  root.append(svg('rect', { x: 0, y: 0, width: 100, height: 8 }, 'track'), svg('rect', { x: 0, y: 0, width: (f * 100).toFixed(2), height: 8 }, `lv${level}`));
  return tip(root, label);
}

// 목록 한 행 — 순위 · 영역 이름 · 평균 보인 시간(막대) · 도달률 · 100뷰당 클릭. 지금 페이지에 있는 행을 누르면 그 영역으로
function screenRow(page, a, top) {
  const gone = a.state === 'gone';
  const tr = el('tr', gone ? 'gone' : null);
  cell(tr, a.rank === null ? '–' : `#${a.rank}`, 'rank');
  const name = el('button', 'area', areaName(page, a.id));
  name.type = 'button';
  name.title = a.id;
  const td = cell(tr, name);
  if (gone) {
    name.disabled = true;
    td.append(el('span', 'low-tag', '지금 화면에 없음'));
  } else tr.addEventListener('click', () => post({ type: 'kt-attention-focus', id: a.id }));
  const time = el('td', 'time');
  const words = a.state === 'none' ? '기록 없음' : `평균 ${AVG.format(a.t)}초`;
  time.append(levelBar(top ? a.ms / top : 0, a.level, `${words} · 단계 ${a.level}`), el('span', null, words));
  tr.append(time);
  const reach = cell(tr, a.state === 'none' ? '–' : `${a.r}%`, 'num');
  if (a.low && a.state !== 'none') reach.append(el('span', 'low-tag', '거의 안 닿음'));
  cell(tr, a.state === 'none' ? '–' : AVG.format(a.c), 'num');
  return tr;
}

function screenList(v, page) {
  if (!v.pv) return [el('p', 'empty', '기록 없음')];
  const out = [];
  if (v.small) out.push(marked('warn', `표본 적음 — 페이지뷰 ${int(v.pv)}(5 미만)이라 단계·순위가 흔들린다`));
  const table = el('table', 'table scr-table');
  const head = el('tr');
  for (const [name, cls] of [['순위'], ['영역'], ['평균 보인 시간'], ['도달률', 'num'], ['100뷰당 클릭', 'num']]) head.append(el('th', cls, name));
  const thead = el('thead');
  thead.append(head);
  const body = el('tbody');
  const top = v.ranked.length ? v.ranked[0].ms : 0;
  body.append(...v.ranked.concat(v.blank, v.away).map((a) => screenRow(page, a, top)));
  table.append(thead, body);
  const box = el('div', 'scroll');
  box.append(table);
  out.push(box);
  return out;
}

// 값이 없을 때 본문(§3.6) — 시행 전·첫 조회 전·불러오지 못함(036 부분 상태 칸)
function blankScreens(part) {
  $('a-screens').replaceChildren();
  let msg;
  if (part?.state === 'before_gate') msg = el('p', 'state-msg', `${kstDate(n0(part.gateAt))} 부터 모읍니다(처리방침 v3 시행일)`);
  else if (part === undefined && !att.flight) msg = el('p', 'state-msg', '이 절이 화면에 보이면 부른다');
  else if (part?.state === 'error') msg = el('p', 'state-msg t-bad', `불러오지 못함${part.code ? ` — ${clean(part.code)}` : ''}`);
  else msg = stateMsg(part, '');
  $('b-screens').replaceChildren(msg);
}

// 머리 — 직전 값을 그리는 실패면 '불러오지 못함 · 마지막 성공', 시행 전, 그 밖은 036 부분 상태
function screensHead(part, feed) {
  if (feed && feed !== part) {
    const out = [badge('bad', `불러오지 못함 · 마지막 성공 ${ago(feed.fetchedAt)}`)];
    if (!part.why && part.code) out.push(el('span', 'code', part.code));
    return out;
  }
  if (part?.state === 'before_gate') return [badge('dim', '시행 전')];
  if (part === undefined && !att.flight) return [el('span', 'muted', '보이면 부른다')];
  return head(part);
}

function drawScreens() {
  const entry = got.get(P.attention);
  const part = entry !== undefined && entry.asked === scr.days ? partOf(P.attention) : undefined;
  if (part?.state === 'ok') scr.lastOk = { days: scr.days, part };
  const failed = part !== undefined && (part.why !== undefined || part.state === 'error');
  const feed = part?.state === 'ok' ? part : failed && scr.lastOk?.days === scr.days ? scr.lastOk.part : null;
  const rows = feed ? list(feed.rows) : [];
  if (feed && !scr.chosen) scr.page = busiest(rows, scr.device);
  const page = scr.page;
  $('scr-days').value = String(scr.days);
  $('scr-page').value = page;
  for (const opt of PAGE_OPTIONS) {
    const name = own(PAGE_NAME, opt.value) ?? clean(opt.value);
    opt.textContent = feed ? `${name} (${int(n0(rowOf(rows, opt.value, scr.device)?.pv))})` : name;
  }
  for (const b of DEVICE_BUTTONS) b.setAttribute('aria-pressed', String(b.dataset.device === scr.device));
  $('m-screens').replaceChildren(...screensHead(part, feed));
  // 틀 — 값이 있을 때, 그리고 기간을 바꿔 새 값을 기다리는 동안은 띄운 페이지를 그대로(다시 띄우지 않는다)
  const waiting = part === undefined && scr.key !== '' && scr.key !== 'about:blank';
  aimFrame(feed || waiting ? frameUrl(page) : null);
  const ready = scr.ready && scr.ready.page === page ? scr.ready : null;
  redraw('screens', [feed, page, scr.device, ready, part ?? Boolean(att.flight)], () => {
    if (!feed) return blankScreens(part);
    const v = screenValues(rowOf(rows, page, scr.device), ready ? ready.areas : null);
    sentence($('a-screens'), screenAnswer(v, page, scr.device, scr.days));
    $('b-screens').replaceChildren(...screenList(v, page));
    const label = `${PERIOD_NAME[scr.days]} · ${PAGE_NAME[page]} · ${DEVICE_SCREEN[scr.device]}`;
    if (ready) post({ type: 'kt-attention', v: 1, label, areas: overlayAreas(v) });
  });
  if (feed) {
    const total = rows.reduce((acc, r) => acc + n0(r?.pv), 0);
    const stale = feed !== part ? ' · 불러오지 못함' : '';
    $('s-screens').replaceChildren(marked(stale ? 'warn' : 'ok', `${PERIOD_NAME[scr.days]} 동의한 페이지뷰 ${int(total)}${stale}`));
  } else if (part?.state === 'before_gate') $('s-screens').replaceChildren(marked('dim', `${kstDate(n0(part.gateAt))} 부터 모은다`));
  else if (part === undefined && !att.flight) $('s-screens').replaceChildren(marked('dim', '보이면 부른다'));
  else $('s-screens').replaceChildren(marked(...softWord(part)));
}

// 부르기(§3.2) — 절이 보이고 탭이 보이는 동안만 5분마다. 기간을 바꾸면 곧바로 하나(떠 있으면 끝난 뒤 한 번 — 마지막 값)
function runScreens() {
  clearTimeout(att.timer);
  if (reloading || !scr.inView || document.visibilityState !== 'visible') return;
  att.started = Date.now();
  loadLatest(att).then((gone) => {
    settle([gone]);
    scheduleScreens();
  });
}

function scheduleScreens() {
  clearTimeout(att.timer);
  if (reloading || !scr.inView || document.visibilityState !== 'visible') return;
  att.timer = setTimeout(runScreens, Math.max(0, SCREENS_MS - (Date.now() - att.started)));
}

// 절·탭이 보이거나 숨을 때 — 주기를 잇거나 멈추고, 틀 주소를 맞춘다(그리기가 aimFrame 을 부른다)
function screensWake() {
  clearTimeout(att.timer);
  if (scr.inView && document.visibilityState === 'visible' && !reloading) {
    if (Date.now() - att.started >= SCREENS_MS) runScreens();
    else scheduleScreens();
  }
  paint();
}

$('scr-days').addEventListener('change', () => {
  const next = Number($('scr-days').value);
  if (!DAYS.includes(next) || next === scr.days || reloading) return;
  scr.days = next;
  paint();
  loadLatest(att).then((gone) => settle([gone]));
});
$('scr-page').addEventListener('change', () => {
  const next = $('scr-page').value;
  if (!own(PAGE_NAME, next) || next === scr.page) return;
  scr.page = next;
  scr.chosen = true;
  paint();
});
for (const button of DEVICE_BUTTONS) {
  button.addEventListener('click', () => {
    const next = button.dataset.device;
    if (!own(DEVICE_SCREEN, next) || next === scr.device) return;
    scr.device = next;
    paint();
  });
}
window.addEventListener('message', onFrameMessage);
window.addEventListener('resize', fitFrame);
new IntersectionObserver((entries) => {
  scr.inView = entries[entries.length - 1].isIntersecting;
  screensWake();
}).observe($('screens'));

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
  ['screens', drawScreens],
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
