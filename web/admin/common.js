// KimpTrack 관리자 v3 공통 모듈 (스펙 064 §3.1) — 개요·서버·트래픽 페이지가 같이 쓴다.
// 빌드 없음: 브라우저가 ES 모듈로 바로 읽는다(CSP script-src 'self' — oxlint·vite 대상이 아니다, 029·036).
// 차례: 글자·수 도구 → 요청·세션·주기(029·036 규칙 그대로) → 부분 상태 표 → 상태 띠 규칙(§3.2) → 머리 줄.
// 보안(§3.1): 서버·방문자가 정한 글자는 textContent·title 로만 넣는다. 링크는 jump() 하나가 고정 표의 주소만 쓴다.
// 갱신 토큰은 입력칸과 변수에만 있다(브라우저 저장소·쿠키 없음).

export const FAST_MS = 10_000; // 빠른 묶음 — 029 의 네 경로, 보이는 동안만
export const SLOW_MS = 60_000; // 느린 묶음 — 그 페이지가 쓰는 나머지, 보이는 동안만
const RELOAD_MARK = 'relogin'; // 로그인 만료로 새로고침했다는 표시 — URL 쿼리(연속 새로고침은 한 번까지)
const XHR = { 'X-Requested-With': 'XMLHttpRequest' };

// 관리자 nginx 분기(029·034·035·063)로 가는 경로 — 화면이 부르는 곳은 이 표와 즉시 갱신(POST /api/refresh)뿐
export const PATHS = Object.freeze({
  collector: '/api/health',
  api: '/svc/api/health',
  status: '/svc/api/admin/status',
  collect: '/api/health/collect',
  aws: '/api/admin/aws',
  series: '/api/admin/aws/series',
  alerts: '/api/admin/alerts',
  access: '/svc/api/admin/access',
  clarity: '/svc/api/admin/clarity',
});
export const FAST = Object.freeze(['collector', 'api', 'status', 'collect']);
const HEALTH = new Set(['collector', 'api']); // 025 — 503 도 상태 응답이다(본문을 읽는다)
// 고른 값을 쿼리로 붙이는 피드 — [쿼리 이름, 고를 수 있는 값]. 응답은 같은 이름의 키로 답한 값을 말한다(063·038)
export const PICKED = Object.freeze({
  series: Object.freeze(['range', Object.freeze(['6h', '24h', '7d', '30d'])]),
  access: Object.freeze(['window', Object.freeze(['24h', '7d', '30d'])]),
});

// --- 글자·수 도구 --------------------------------------------------------------------------------

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

export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = clean(text);
  return node;
}

// 글자만 바꾼다 — 같으면 쓰지 않는다(텍스트 노드가 바뀌면 그 안의 글자 선택이 풀린다)
export function put(id, text, cls) {
  const node = $(id);
  const next = clean(text);
  if (node.textContent !== next) node.textContent = next;
  if (cls !== undefined) node.className = cls;
  return node;
}

const NF = new Intl.NumberFormat('ko-KR');
const D1 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
// §3.5 — 0 은 그대로, null 은 '–', 큰 값은 천 단위 쉼표
export const int = (v) => (num(v) === null ? '–' : NF.format(Math.round(v)));
export const dec1 = (v) => (num(v) === null ? '–' : D1.format(v));
export const pct = (v, digits = 1) => (num(v) === null ? '–' : `${v.toFixed(digits)}%`);
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

// 바이트 — 단위 자동(1024 단위 B·KB·MB·GB·TB, §3.5). 뒤집은 면적의 아래(음수)는 크기만
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

// --- 요청·세션·주기 (029·036 규칙) ---------------------------------------------------------------

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

export function notice(text) {
  const node = $('notice');
  node.textContent = clean(text);
  node.hidden = !text;
}

// 페이지 하나를 띄운다. page = { home(이 페이지의 고정 주소), slow(느린 묶음 키), picks(고른 값의 처음),
// cards([이름, 그리기] — 카드마다 따로 그려 한 카드의 예상 밖 응답이 다른 카드를 막지 않게) }
export function start(page) {
  const keys = [...FAST, ...page.slow];
  const got = new Map(); // 키 → { body, text, asked } | { why, asked } | { off, asked } — 마지막 결과 하나(쌓지 않는다)
  const okSince = new Set(); // 마지막 만료 신호 뒤 만료 신호 없이 끝난 키
  const picked = { ...page.picks };
  const flights = new Map(); // 고른 값으로 부르는 키 → { flight, again }
  let reloading = false;

  // 직전 결과와 같으면 직전 객체를 그대로 둔다 — 그 값으로 그린 카드를 다시 그리지 않게(036)
  const keep = (key, next) => {
    const prev = got.get(key);
    const same = prev && prev.asked === next.asked && prev.why === next.why && prev.off === next.off && prev.text === next.text;
    if (!same) got.set(key, next);
  };
  const urlOf = (key) => (own(PICKED, key) ? `${PATHS[key]}?${PICKED[key][0]}=${picked[key]}` : PATHS[key]);

  // 경로 하나를 부르고 결과를 got 에 둔다. 403·JSON 아님·예상 밖 상태는 사유만(그 칸은 비운다). 앱이 모르는 경로(404
  // JSON — 062·063 배포 전의 새 피드)는 '연결 안 됨'. 돌려주는 값 = 만료 신호였는가
  async function load(key) {
    const asked = own(PICKED, key) ? picked[key] : undefined;
    try {
      const { status, body, text } = await call(urlOf(key));
      const fine = status === 200 || (status === 503 && HEALTH.has(key));
      if (status === 403) keep(key, { why: '권한·설정 오류 (403)', asked });
      else if (status === 404 && body !== null) keep(key, { off: true, asked });
      else if (body === null || !fine) keep(key, { why: `응답 오류 (HTTP ${status})`, asked });
      else keep(key, { body, text, asked });
      okSince.add(key);
      return false;
    } catch (err) {
      const gone = isExpired(err);
      keep(key, { why: gone ? '로그인 만료·연결 끊김' : '불러오지 못함', asked });
      if (!gone) okSince.add(key);
      return gone;
    }
  }

  // 고른 값으로 부르는 키(042 §3.2 규칙) — 떠 있으면 겹쳐 부르지 않고 끝난 뒤 한 번 더(그사이 여러 번 바꿔도 다음
  // 호출은 하나 — 그때 고른 값). 돌려주는 값 = 만료 신호였는가
  function latest(key) {
    if (!flights.has(key)) flights.set(key, { flight: null, again: false });
    const box = flights.get(key);
    if (box.flight) {
      box.again = true;
      return box.flight;
    }
    box.flight = (async () => {
      let gone = false;
      do {
        box.again = false;
        gone = (await load(key)) || gone;
        follow(key);
      } while (box.again && !reloading);
      box.flight = null;
      return gone;
    })();
    return box.flight;
  }

  // 응답이 답한 값이 요청한 값과 다르면(서버가 24시간으로 답했다) 고른 값을 응답 값으로 — 그사이 다른 값을 고르지 않았을 때만
  function follow(key) {
    const entry = got.get(key);
    const answered = entry?.body?.[PICKED[key][0]];
    if (entry?.asked !== picked[key] || answered === picked[key] || !PICKED[key][1].includes(answered)) return;
    picked[key] = answered;
    entry.asked = answered;
  }

  // 로그인 만료 신호 — 표시가 없으면 한 번 새로고침하고, 있으면 알림만(연속 새로고침은 한 번까지). 주소는 이 페이지의
  // 고정 주소(page.home): 지금 경로를 쓰면 `//다른호스트/..%2F` 꼴에서 프로토콜 상대 URL 이 되어 다른 출처로 간다(029)
  function expired() {
    if (reloading) return;
    if (new URLSearchParams(location.search).has(RELOAD_MARK)) {
      notice('로그인이 만료됐거나 연결이 끊겼다 — 새로고침해 다시 로그인한다');
      return;
    }
    reloading = true;
    location.replace(`${page.home}?${RELOAD_MARK}=1`);
  }

  // 표시 지우기 — 마지막 만료 신호 뒤 이 페이지의 키가 모두 만료 신호 없이 끝났을 때만(묶음 하나만 보고 지우면
  // 다른 묶음에만 있는 만료가 60초마다 새로고침을 되풀이한다)
  function settle(signals) {
    if (signals.some(Boolean)) {
      okSince.clear();
      expired();
    } else if (keys.every((key) => okSince.has(key)) && new URLSearchParams(location.search).has(RELOAD_MARK)) {
      history.replaceState(null, '', page.home);
      notice('');
    }
    put('updated', clock(Date.now()));
    paint();
  }

  // 두 묶음은 따로 돈다. 같은 묶음은 앞선 호출이 끝나기 전에 다시 부르지 않고, 다음 호출은 앞선 호출을 시작한
  // 때부터 잰다(응답 시간만큼 밀리지 않게). 숨은 탭은 부르지 않는다
  const fast = { keys: FAST, every: FAST_MS, busy: false, timer: 0, started: 0 };
  const slow = { keys: page.slow, every: SLOW_MS, busy: false, timer: 0, started: 0 };
  async function run(loop) {
    clearTimeout(loop.timer);
    if (loop.busy || reloading || document.visibilityState !== 'visible') return;
    loop.busy = true;
    loop.started = Date.now();
    try {
      settle(await Promise.all(loop.keys.map((key) => (own(PICKED, key) ? latest(key) : load(key)))));
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

  const app = {
    picked,
    entry: (key) => got.get(key),
    // 고른 값으로 부른 키는 지금 고른 값의 결과만(다른 값의 응답을 새 이름 아래 두지 않는다)
    current: (key) => {
      const entry = got.get(key);
      return entry && (!own(PICKED, key) || entry.asked === picked[key]) ? entry : undefined;
    },
    body: (key) => app.current(key)?.body ?? null,
    // 고르는 단추 — 바꾸는 순간 그 키 하나만 곧바로 부른다(느린 묶음의 다음 시각은 그대로)
    pick(key, value) {
      if (!own(PICKED, key) || !PICKED[key][1].includes(value) || value === picked[key] || reloading) return;
      picked[key] = value;
      paint();
      latest(key).then((gone) => settle([gone]));
    },
    expired,
    paint: () => paint(),
    // 이 페이지가 가진 피드로 센 상태 띠 칩(§3.2) — 머리의 점과 개요의 띠가 같이 쓴다
    chips: () => problems(Object.fromEntries(keys.map((key) => [key, app.current(key)])), Date.now()),
  };

  function paint() {
    for (const [name, draw] of page.cards) {
      try {
        draw(app);
      } catch {
        tagText(`t-${name}`, ['bad', '표시 오류', '응답 모양이 예상과 다르다']);
      }
    }
    header(app);
  }

  paint();
  run(fast);
  run(slow);
  return app;
}

// --- 부분 상태 표 (§3.1 — 카드 오른쪽 위 작은 표로만) ---------------------------------------------

const BAD_SHAPE = Object.freeze({ why: '응답 모양 오류' });
// 앱이 모르는 경로(404 JSON) — 그 피드가 이 서버에 아직 없다(062·063 배포 전, 부분 상태 규칙대로 '연결 안 됨')
const NO_FEED = Object.freeze({ state: 'unconfigured', code: null });
const PLAIN_OK = Object.freeze({ state: 'ok' });

// 결과에서 부분 하나 — undefined(첫 응답 전·고른 값을 부르는 중)·{ why }(호출 실패)·부분 객체({ state, code, … }).
// sub 가 없으면 응답 전체가 부분이다(series·access·clarity)
export function partOf(entry, sub) {
  if (!entry) return undefined;
  if (entry.why) return { why: entry.why };
  if (entry.off) return NO_FEED;
  const part = sub ? entry.body[sub] : entry.body;
  return isObj(part) && typeof part.state === 'string' ? part : BAD_SHAPE;
}
// 상태 응답(헬스·status·collect)은 부분 꼴이 아니다 — 호출 결과만 표로
export const plainOf = (entry) => (!entry ? undefined : entry.why ? { why: entry.why } : entry.off ? NO_FEED : PLAIN_OK);

// 값을 그릴 수 있는가 — ok, 또는 error 인데 값이 있다(Clarity 의 마지막 성공 값, 040)
export function usable(part, valueKey) {
  if (!part || part.why) return false;
  if (part.state === 'ok') return true;
  return part.state === 'error' && valueKey !== undefined && part[valueKey] != null;
}

// 표 = [색, 글, title] 또는 null(문제 없음 — 비운다). 오래됨 = fetchedAt 이 refreshSec 의 세 배를 넘음(036)
export function tagOf(part, now = Date.now()) {
  if (part === undefined) return ['wait', '불러오는 중', ''];
  if (part.why) return ['bad', '불러오지 못함', part.why];
  const code = clean(part.code ?? '');
  if (part.state === 'ok') {
    const old = num(part.refreshSec) > 0 && num(part.fetchedAt) !== null && now - part.fetchedAt > part.refreshSec * 3000;
    return old ? ['warn', '오래됨', `${ago(part.fetchedAt, now)} 값`] : null;
  }
  if (part.state === 'pending') return ['wait', '첫 조회 중', ''];
  if (part.state === 'unconfigured' && code === 'before_gate') return ['dim', '시행 전', '처리방침 개정 시행일부터 센다'];
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
export const tag = (id, part) => tagText(id, tagOf(part));

// --- 상태 띠 규칙 (§3.2 1 — 개요의 띠와 모든 페이지 머리의 점이 같은 함수) -----------------------------

// 수집 원천 이름 — /health/collect 순서(현물 6 → perp 4, 011·046·047). 표에 없는 값은 원래 글자 그대로
export const EX_NAME = Object.freeze({
  upbit: '업비트',
  bithumb: '빗썸',
  binance: '바이낸스',
  bybit: '바이빗',
  bitget: '비트겟',
  okx: 'OKX',
  binance_perp: '바이낸스 perp',
  bybit_perp: '바이빗 perp',
  bitget_perp: '비트겟 perp',
  hyperliquid_perp: 'Hyperliquid',
});
export const exName = (id) => own(EX_NAME, id) ?? clean(id);

// 바로 가기 — 칩이 가리키는 곳은 이 표뿐이고, 링크 주소를 쓰는 곳은 jump() 하나다(피드 글자가 주소가 되지 않게)
const JUMP = Object.freeze({
  collect: '/server.html#collect',
  alarms: '/server.html#alarms',
  canary: '/server.html#canary',
  cpu: '/server.html#cpu',
  mem: '/server.html#mem',
  disk: '/server.html#disk',
  credit: '/server.html#credit',
  checks: '/server.html#checks',
  cost: '/server.html#cost',
  problems: '/traffic.html#problems',
});
export function jump(node, to) {
  const href = own(JUMP, to);
  if (href !== undefined) node.setAttribute('href', href);
  return node;
}

// 시계열 끝 15분 평균 — 점 [[ts초, 값|null]] 가운데 끝(endTs) 앞 15분(주기가 더 길면 마지막 칸)의 값 평균, 없으면 null
export function recentAvg(points, endTs, periodSec) {
  if (num(endTs) === null) return null;
  const from = endTs - Math.max(900, n0(periodSec));
  const vals = list(points)
    .filter((p) => Array.isArray(p) && num(p[0]) !== null && p[0] >= from && p[0] < endTs)
    .map((p) => num(p[1]))
    .filter((v) => v !== null);
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

// 상자 지표 문턱(§3.2) — [시리즈 키, 이름, 빨강 이상, 주황 이상, 바로 가기]
export const BOX_LEVELS = Object.freeze([
  ['cpu', 'CPU', 90, 75, 'cpu'],
  ['mem', '메모리', 90, 80, 'mem'],
  ['disk', '디스크', 85, 75, 'disk'],
]);
export const levelOf = (v, red, amber) => (num(v) === null ? null : v >= red ? 'bad' : v >= amber ? 'warn' : 'ok');

// WS 재접속 실패 — 24시간 창은 status.ws5xx(§3.2), 7·30일 창은 마지막 24시간 칸의 wsErrors 합(머리 점이 창에 따라 바뀌지 않게)
export function wsFailures(a) {
  if (a.window === '24h') return n0(a.status?.ws5xx);
  const end = n0(a.endTs);
  return list(a.hourly).filter((h) => isObj(h) && n0(h.ts) > end - 86_400).reduce((sum, h) => sum + n0(h.wsErrors), 0);
}

const okPart = (body, key) => (isObj(body) && isObj(body[key]) && body[key].state === 'ok' ? body[key] : null);

// f = { 키: 마지막 결과 } — 없는 키는 판정하지 않는다. 칩 = { tone, name, value, to }, 빨강 먼저(같은 색은 규칙 순서)
export function problems(f, now = Date.now()) {
  const out = [];
  const add = (tone, name, value, to) => out.push({ tone, name: clean(name), value: clean(value), to });
  for (const [key, name] of [['collector', '수집 멈춤'], ['api', 'API 이상']]) {
    const entry = f[key];
    if (!entry) continue;
    const status = entry.body?.status;
    if (entry.why || entry.off) add('bad', name, '응답 없음', 'collect');
    else if (status !== 'ok') add('bad', name, typeof status === 'string' ? status : '?', 'collect');
  }
  const st = f.status?.body;
  if (st?.redis === 'down') add('bad', 'Redis 끊김', 'down', 'collect');
  if (st?.influx === 'down') add('bad', 'Influx 끊김', 'down', 'collect');
  for (const ex of list(f.collect?.body?.exchanges).filter(isObj)) {
    if (isObj(ex.openOutage)) add('bad', `${exName(ex.exchange)} 끊김`, span((now - n0(ex.openOutage.startedAt)) / 1000), 'collect');
    else if (num(ex.successRate1h) !== null && ex.successRate1h < 99) add('warn', `${exName(ex.exchange)} 성공률`, pct(ex.successRate1h), 'collect');
  }
  const aws = f.aws?.body;
  const alarms = okPart(aws, 'alarms');
  const firing = alarms ? Math.max(list(alarms.items).filter((a) => isObj(a) && a.state === 'ALARM').length, n0(alarms.counts?.alarm)) : 0;
  if (firing > 0) add('bad', '경보', `${firing}`, 'alarms');
  const canary = okPart(aws, 'canary');
  if (canary?.ok === false) add('bad', '점검 실패', ago(canary.lastRunAt, now), 'canary');
  const series = f.series?.body;
  if (isObj(series) && series.state === 'ok') {
    for (const b of list(series.boxes).filter(isObj)) {
      const s = isObj(b.series) ? b.series : {};
      const avg = (key) => recentAvg(s[key], series.endTs, series.periodSec);
      const box = clean(b.box);
      for (const [key, label, red, amber, to] of BOX_LEVELS) {
        const v = avg(key);
        const tone = levelOf(v, red, amber);
        if (tone === 'bad' || tone === 'warn') add(tone, `${box} ${label}`, pct(v), to);
      }
      const credit = avg('creditBalance');
      if (credit !== null && credit < 30) add(credit < 10 ? 'bad' : 'warn', `${box} 크레딧`, dec1(credit), 'credit');
      const surplus = avg('surplusCharged');
      if (surplus !== null && surplus > 0) add('warn', `${box} 초과 과금`, dec1(surplus), 'credit');
      const failed = avg('statusFailed');
      if (failed !== null && failed > 0) add('bad', `${box} 상태 검사`, '실패', 'checks');
    }
  }
  const a = f.access?.body;
  if (isObj(a) && a.state === 'ok') {
    const end = num(a.endTs);
    const fives = end === null ? 0 : list(a.recent5xx).filter((r) => isObj(r) && num(r.ts) !== null && r.ts >= end - 3600).length;
    if (fives >= 1) add(fives >= 10 ? 'bad' : 'warn', '5xx', `${fives}`, 'problems');
    const ws = wsFailures(a);
    if (ws >= 1) add('warn', 'WS 재접속 실패', `${ws}`, 'problems');
  }
  for (const b of list(okPart(aws, 'budget')?.items).filter(isObj)) {
    if (!(num(b.limit) > 0)) continue;
    if (num(b.actual) !== null && b.actual >= b.limit) add('bad', '예산 넘음', pct((b.actual / b.limit) * 100, 0), 'cost');
    else if (num(b.forecast) !== null && b.forecast >= b.limit) add('warn', '예산 예측 넘음', pct((b.forecast / b.limit) * 100, 0), 'cost');
  }
  const slack = list(f.alerts?.body?.items).filter((i) => isObj(i) && i.source === 'slack' && num(i.at) !== null && i.at >= now - 3_600_000);
  if (slack.length) add('warn', 'Slack 알림', `${slack.length}`, 'alarms');
  return out.filter((c) => c.tone === 'bad').concat(out.filter((c) => c.tone === 'warn'));
}

// 칩 하나 — 짧은 이름 + 값, 누르면 그 페이지의 해당 카드로(§3.2)
export function chip(c) {
  const node = jump(el('a', `chip ${c.tone}`), c.to);
  node.append(el('span', 'chip-dot'), el('span', 'chip-name', c.name));
  if (c.value) node.append(el('b', 'chip-val', c.value));
  return node;
}

// --- 머리 줄 (§3.1) — 전체 상태 점(그 페이지가 가진 피드로 센 띠 규칙), 개요 링크 -----------------------------

const WORD = Object.freeze({ ok: '정상', warn: '주의', bad: '문제', wait: '확인 중' });
function header(app) {
  const node = $('hdr-state');
  if (!node) return;
  const ready = app.entry('collector') !== undefined && app.entry('api') !== undefined;
  const chips = ready ? app.chips() : [];
  const tone = !ready ? 'wait' : chips.some((c) => c.tone === 'bad') ? 'bad' : chips.length ? 'warn' : 'ok';
  node.className = `state ${tone}`;
  put('hdr-word', chips.length ? `${WORD[tone]} ${chips.length}` : WORD[tone]);
  node.title = chips.map((c) => `${c.name} ${c.value}`).join(' · ');
}

// 그 카드가 기대는 값(결과 객체)이 바뀌었을 때만 다시 그린다 — 10초 묶음마다 차트·툴팁·확대가 날아가지 않게(036)
const drawn = new Map();
export function once(name, deps, draw) {
  const now = Array.isArray(deps) ? deps : [deps];
  const last = drawn.get(name);
  if (last && last.length === now.length && now.every((d, i) => d === last[i])) return;
  draw();
  drawn.set(name, now); // 그리다 예외가 나면 남기지 않는다 — 다음 묶음이 다시 그린다
}

// 그림 칸에 보일 상태 글 — 표가 없으면(ok) '자료 없음'
export const stateWord = (part) => tagOf(part)?.[1] ?? '자료 없음';

// 금액 — USD 는 $ 를 앞에, 그 밖 단위는 뒤에(서버 글자)
const USD = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const money = (v, unit) => (num(v) === null ? '–' : unit == null || unit === 'USD' ? `$${USD.format(v)}` : `${USD.format(v)} ${clean(unit)}`);

// 월 예산 가운데 한도 대비 실제가 가장 큰 것 — 색은 띠 규칙과 같다(실제 ≥ 100% 문제, 예측 ≥ 100% 주의)
export function worstMonthly(part) {
  let worst = null;
  for (const b of list(part.items).filter(isObj)) {
    if (b.timeUnit !== 'MONTHLY' || !(num(b.limit) > 0) || num(b.actual) === null) continue;
    const r = b.actual / b.limit;
    if (!worst || r > worst.r) worst = { b, r };
  }
  if (worst) worst.tone = worst.r >= 1 ? 'bad' : num(worst.b.forecast) !== null && worst.b.forecast >= worst.b.limit ? 'warn' : 'ok';
  return worst;
}
