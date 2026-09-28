// KimpTrack 관리자 화면 스크립트 (스펙 029 §3.3). 빌드 없음 — oxlint·vite 대상이 아니다(landing.html 과 같다).
// 서버 응답의 문자열(거래소 오류 message 에는 WAF HTML 이 흔하다)은 textContent 로만 넣는다 — HTML 로 해석하지 않는다.
// 즉시 갱신 토큰은 입력칸과 이 스크립트의 변수에만 있다 — 브라우저 저장소에 두지 않는다.
'use strict';

const POLL_MS = 10_000; // 보이는 동안만
const RELOAD_MARK = 'relogin'; // 로그인 만료로 새로고침했다는 표시 — URL 쿼리(연속 새로고침은 한 번까지)
const XHR = { 'X-Requested-With': 'XMLHttpRequest' };

class Expired extends Error {}

let busy = false;
let reloading = false;
let timer = 0;

const $ = (id) => document.getElementById(id);

function put(id, text, tone) {
  const el = $(id);
  el.textContent = text;
  el.className = tone ? `tone-${tone}` : '';
}

function notice(text) {
  const el = $('notice');
  el.textContent = text;
  el.hidden = !text;
}

const ago = (ms) => (ms == null ? '없음' : `${Math.max(0, Math.round((Date.now() - ms) / 1000))}초 전`);
const clock = (ms) => new Date(ms).toTimeString().slice(0, 8); // HH:MM:SS (브라우저 시간대)
const HEALTH_TONE = { ok: 'ok', starting: 'warn', stale: 'bad', redis_down: 'bad' };
const STATE_TONE = { ok: 'ok', stale: 'warn', down: 'bad' };
const UP_TONE = { ok: 'ok', down: 'bad' };

// 세션 판별 — (1) 401 + 앱 JSON(detail) = 토큰 오류, 그대로 돌려준다 (2) 401 인데 JSON 이 아니거나 fetch 자체가
// 실패(교차 출처 리다이렉트) = 로그인 만료 → 전체 새로고침 (3) 403 은 호출한 쪽이 "권한·설정 오류" 로 보인다.
async function call(path, init = {}) {
  let resp;
  try {
    resp = await fetch(path, { ...init, headers: { ...XHR, ...init.headers }, cache: 'no-store' });
  } catch {
    expired();
    throw new Expired();
  }
  const text = await resp.text();
  let body = null;
  try {
    body = JSON.parse(text);
  } catch {
    body = null;
  }
  const appJson = body !== null && typeof body === 'object' && 'detail' in body;
  if (resp.status === 401 && !appJson) {
    expired();
    throw new Expired();
  }
  alive();
  return { status: resp.status, body: body !== null && typeof body === 'object' ? body : null };
}

function expired() {
  if (reloading) return;
  if (new URLSearchParams(location.search).has(RELOAD_MARK)) {
    notice('로그인이 만료됐거나 연결이 끊겼다 — 새로고침해 다시 로그인한다');
    return;
  }
  reloading = true;
  location.replace(`${location.pathname}?${RELOAD_MARK}=1`);
}

function alive() {
  // 응답이 왔다 = 로그인이 살아 있다. 다음 만료 때 다시 한 번 새로고침할 수 있게 표시를 지운다
  if (new URLSearchParams(location.search).has(RELOAD_MARK)) {
    history.replaceState(null, '', location.pathname);
    notice('');
  }
}

// 경로 하나를 불러 그린다. 403 과 JSON 이 아닌 응답(502 등)은 칸에 사유를 적는다
async function load(path, render, fail) {
  try {
    const { status, body } = await call(path);
    if (status === 403) fail('권한·설정 오류 (403)');
    else if (body === null) fail(`응답 오류 (HTTP ${status})`);
    else render(body, status);
  } catch (err) {
    if (!(err instanceof Expired)) fail('불러오지 못함');
  }
}

// 025 /health — 503 도 상태 응답이라 본문을 그린다(오류가 아니다)
function health(prefix) {
  return [
    (body) => {
      put(`${prefix}-status`, String(body.status), HEALTH_TONE[body.status] || 'bad');
      put(`${prefix}-tick`, ago(body.lastTickAt));
      if ($(`${prefix}-version`)) put(`${prefix}-version`, String(body.version));
    },
    (why) => put(`${prefix}-status`, why, 'bad'),
  ];
}

function renderStatus(body) {
  put('api-ws', String(body.wsConnections));
  put('api-redis', String(body.redis), UP_TONE[body.redis] || 'bad');
  put('api-influx', String(body.influx), UP_TONE[body.influx] || 'bad');
  put('api-version', String(body.version));
}

function cell(row, text, tone, title) {
  const td = document.createElement('td');
  td.textContent = text;
  if (tone) td.className = `tone-${tone}`;
  if (title) td.title = title;
  row.append(td);
}

function renderCollect(body) {
  const rows = (Array.isArray(body.exchanges) ? body.exchanges : []).map((ex) => {
    const tr = document.createElement('tr');
    const outage = ex.openOutage;
    const error = ex.lastError;
    cell(tr, String(ex.exchange));
    cell(tr, String(ex.state), STATE_TONE[ex.state] || 'bad');
    cell(tr, typeof ex.successRate1h === 'number' ? `${ex.successRate1h.toFixed(1)}%` : '-');
    cell(tr, outage ? `${outage.kind} · ${outage.count}회 · ${ago(outage.startedAt)} 시작` : '-', outage ? 'bad' : '');
    const message = error ? String(error.message) : '';
    const errorText = error ? `${clock(error.at)} ${error.kind} ${error.statusCode ?? ''} ${message.slice(0, 300)}` : '-';
    cell(tr, errorText, '', message);
    return tr;
  });
  $('exchanges').replaceChildren(...rows);
  put('collect-meta', `전체 1시간 ${Number(body.successRate1h).toFixed(1)}%`);
}

async function refreshAll() {
  if (busy || reloading) return;
  busy = true;
  try {
    await Promise.all([
      load('/api/health', ...health('collector')),
      load('/svc/api/health', ...health('api')),
      load('/svc/api/admin/status', renderStatus, (why) => put('api-ws', why, 'bad')),
      load('/api/health/collect', renderCollect, (why) => put('collect-meta', why, 'bad')),
    ]);
    put('updated', `마지막 갱신 ${clock(Date.now())}`);
  } finally {
    busy = false;
  }
}

async function tick() {
  if (document.visibilityState === 'visible') await refreshAll();
  clearTimeout(timer);
  if (document.visibilityState === 'visible') timer = setTimeout(tick, POLL_MS);
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') tick();
  else clearTimeout(timer);
});

// 003 POST /refresh — 토큰이 틀리면 401 {"detail"} (새로고침하지 않는다)
$('refresh').addEventListener('click', async () => {
  const button = $('refresh');
  const token = $('token').value;
  button.disabled = true;
  try {
    const { status, body } = await call('/api/refresh', {
      method: 'POST',
      headers: token ? { 'X-Refresh-Token': token } : {},
    });
    $('refresh-result').hidden = false;
    const label = status === 401 ? ' 토큰 오류' : status === 403 ? ' 권한·설정 오류' : '';
    put('refresh-status', `${status}${label}`, status === 200 ? 'ok' : 'bad');
    const ok = status === 200 && body !== null;
    put('refresh-saved', ok ? String(body.totalSaved) : '-');
    const failures = ok && Array.isArray(body.failures) ? body.failures : [];
    put('refresh-failures', failures.map((f) => `${f.exchange} · ${f.errorCode}`).join(', ') || '없음');
    const warnings = ok && Array.isArray(body.warnings) ? body.warnings : [];
    put('refresh-warnings', warnings.map(String).join(' / ') || '없음');
  } catch (err) {
    if (!(err instanceof Expired)) put('refresh-status', '보내지 못함', 'bad');
  } finally {
    button.disabled = false;
  }
});

tick();
