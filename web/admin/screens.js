// 화면 페이지(스펙 065) — 실제 우리 페이지를 틀로 크게 띄워(공개 사이트 + kt-overlay=1) postMessage 로 값을 넘기면, 그 페이지의
// attention.js 가 영역·요소·스크롤 깊이를 덮어 그린다(053·065 §3.5). 옆 판: 요소 순위·영역·떠난 곳과 깊이(판마다 ⤢ → 066 상세 판).
// 고르기: 기간(머리 — 주소 range)·화면·기기·보기·지표(주소 page·device·mode·metric — 목록 밖은 기본값). 부르기: 화면·기간을 바꾸면
// 곧바로 days=&page= 하나, 화면 칩의 pv 는 page 없는 같은 기간 응답 하나, 그 밖에는 보이는 동안 5분마다(066 공통 규칙).
import { $, button, el, frameVars, int, isObj, list, n0, once, partOf, pressed, put, tag, usable, widthOf } from './common.js';
import {
  DEVICE_NAME, FEED_MAX, PARAMS, PERIOD_NAME, SCREEN_PAGES, SMALL_PV,
  current, isDashboard, listReq, overlayMessage, pageReq, rowOf, screenName, screenOf, ui, validArea, validKey,
} from './screensmodel.js';
import { SCREEN_DETAILS } from './screensdetail.js';
import { drawPanel } from './screenspanel.js';
import { start } from './app.js';

// 덮어 보기로 띄우는 공개 사이트 — 틀 주소·postMessage 대상·받는 메시지 출처가 모두 이 값이다(이 파일에 한 번).
// 로컬 확인은 커밋하지 않는 시험 사본에서 바꾼다(053 §3.4 — attention.js 의 ADMIN_ORIGIN 과 짝)
const SITE_ORIGIN = 'https://kimptrack.com';
const STATIC_PATH = Object.freeze({ landing: '/', privacy: '/privacy', 'kimp-chart': '/kimp-chart', 'kimp-history': '/kimp-history' });
const FRAME_WIDTH = Object.freeze({ pc: 1280, mobile: 390 });
const FRAME_WAIT_MS = 5_000; // 틀이 이 안에 ready 를 보내지 않으면 '페이지가 응답하지 않음'
const SCREENS_MS = 300_000; // 보이는 동안 5분마다
const MIN_STAGE = 480; // 틀 칸 높이 하한(px)

// 틀 주소 — 공개 사이트의 그 화면에 덮어 보기 표시. 대시보드는 탭을 쿼리로
const frameUrl = (page) => `${SITE_ORIGIN}${isDashboard(page) ? `/app/?tab=${page.slice(4)}&` : `${STATIC_PATH[page]}?`}kt-overlay=1`;

// 틀로 보내기 — 대상 출처는 늘 공개 사이트 하나
function post(message) {
  const win = $('frame')?.contentWindow;
  if (win) win.postMessage(message, SITE_ORIGIN);
}

// 틀 칸 맞추기 — 폭은 칸 폭 ÷ 틀 폭(늘리지 않음), 높이는 창 높이에서 칸 위쪽(머리·고르기 줄)을 뺀 만큼(최소 480px)
function fit(device) {
  const wrap = $('stage-wrap');
  const stage = $('stage');
  if (!wrap || !stage) return;
  const width = wrap.clientWidth || FRAME_WIDTH[device];
  const top = wrap.getBoundingClientRect().top + window.scrollY;
  frameVars(stage, Math.min(1, width / FRAME_WIDTH[device]), Math.max(MIN_STAGE, window.innerHeight - top - 24));
}

// 틀 주소(§3.7) — 화면·기기를 바꿀 때만 다시 띄운다. 이 문서가 숨은 동안은 about:blank(대시보드는 실시간 연결을 연다).
// 주소는 frameUrl 이 SITE_ORIGIN 으로만 만든다
function aim(app, page) {
  const want = page && document.visibilityState === 'visible' ? frameUrl(page) : 'about:blank';
  const key = want === 'about:blank' ? want : `${want}|${app.view.p.device}`;
  if (key === ui.key) return;
  Object.assign(ui, { key, page: want === 'about:blank' ? null : page, ready: null, silent: false, sent: null });
  clearTimeout(ui.wait);
  $('frame').src = want;
  if (want !== 'about:blank') {
    ui.wait = setTimeout(() => {
      ui.silent = ui.ready === null;
      app.paint();
    }, FRAME_WAIT_MS);
  }
}

// 틀의 ready(053·065 §3.5) — 출처가 공개 사이트이고 창이 이 틀일 때만, 지금 띄운 화면의 것만. 받을 때마다 값을 다시 보낸다
function onFrameMessage(app, event) {
  const win = $('frame')?.contentWindow;
  if (event.origin !== SITE_ORIGIN || !win || event.source !== win) return;
  const d = event.data;
  if (!isObj(d) || d.type !== 'kt-attention-ready' || d.v !== 1 || !ui.page || d.page !== ui.page) return;
  clearTimeout(ui.wait);
  const areas = [...new Set(list(d.areas).filter(validArea))].slice(0, 40);
  // elements 가 없으면(옛 attention.js·요소 없음) '지금 화면에 없음' 을 판정하지 않는다
  const elements = Array.isArray(d.elements)
    ? new Set(d.elements.filter((x) => Array.isArray(x) && validArea(x[0]) && validKey(x[1])).slice(0, FEED_MAX).map(([a, k]) => `${a}|${k}`))
    : null;
  Object.assign(ui, { ready: { page: d.page, areas, elements }, silent: false, sent: null });
  app.paint();
}

// 보기·지표를 지금 화면·기기에 맞춘다(주소에 있으면 고쳐 쓴다 — 다시 부르지 않는다): 대시보드의 깊이 → 요소, 휴대폰의 마우스 올림 → 클릭
function settle(app, page) {
  const fix = {};
  if (page && isDashboard(page) && app.view.p.mode === 'depth') fix.mode = 'element';
  if (app.view.p.device === 'mobile' && app.view.p.metric === 'hover') fix.metric = 'clicks';
  if (Object.keys(fix).length) app.setParams(fix, true);
}

const pickScreen = (app, page) => {
  Object.assign(ui, { focus: null, focusArea: null, area: '' });
  app.setParams({ page });
};

// 고르기 줄 — 화면 칩 열(이름 + 고른 기간·기기 pv + pv/최대 폭 막대, pv 0 은 흐리게) · 기기 · 보기 · 지표(보기가 요소일 때만)
function drawPick(app) {
  settle(app, screenOf(app));
  const c = current(app);
  const listPart = partOf(c.listEntry);
  const known = usable(listPart);
  once('pick-page', [c.listEntry, c.device, c.page], () => {
    const pvs = SCREEN_PAGES.map((p) => (known ? n0(rowOf(listPart.rows, p, c.device)?.pv) : 0));
    const max = Math.max(0, ...pvs);
    $('pick-page').replaceChildren(
      ...SCREEN_PAGES.map((p, i) => {
        const b = button(pvs[i] ? 'scr-chip' : 'scr-chip zero');
        pressed(b, p === c.page);
        const bar = el('i', 'bg');
        widthOf(bar, max ? pvs[i] / max : 0);
        b.append(bar, el('span', null, screenName(p)), el('b', null, known ? int(pvs[i]) : '–'));
        b.title = `${screenName(p)} · ${DEVICE_NAME[c.device]} · ${PERIOD_NAME[c.range]} 페이지뷰 ${known ? int(pvs[i]) : '–'}`;
        b.addEventListener('click', () => pickScreen(app, p));
        return b;
      }),
    );
  });
  for (const b of document.querySelectorAll('[data-device]')) pressed(b, b.dataset.device === c.device);
  for (const b of document.querySelectorAll('[data-mode]')) {
    const off = b.dataset.mode === 'depth' && Boolean(c.page) && isDashboard(c.page);
    pressed(b, b.dataset.mode === c.mode);
    b.disabled = off;
    b.title = off ? '탭 화면은 깊이 없음' : '';
  }
  const seg = $('metric-seg');
  if (seg) seg.hidden = c.mode !== 'element';
  for (const b of document.querySelectorAll('[data-metric]')) {
    const off = b.dataset.metric === 'hover' && c.device === 'mobile';
    pressed(b, b.dataset.metric === c.metric);
    b.disabled = off;
    b.title = off ? '휴대폰은 마우스 올림 없음' : '';
  }
}

// 틀 칸 — 위 한 줄 '<화면> · <기기> · <기간> — pv n'(+ ▲ 표본 적음), 띄우기, 5초 무응답 한 줄, 값 메시지(ok 일 때만 — 같으면 다시 보내지 않음)
function drawFrame(app) {
  aim(app, screenOf(app)); // 먼저 — 틀을 다시 띄웠으면 새 ready 전에는 값을 보내지 않는다
  const c = current(app);
  tag('t-frame', c.part);
  const pv = n0(c.row?.pv);
  put('frame-line', c.page ? `${screenName(c.page)} · ${DEVICE_NAME[c.device]} · ${PERIOD_NAME[c.range]}${c.ok ? ` — pv ${int(pv)}` : ''}` : '');
  const small = $('frame-small');
  if (small) small.hidden = !(c.ok && pv > 0 && pv < SMALL_PV);
  const stage = $('stage');
  if (stage) stage.className = `scr-stage ${c.device}`;
  fit(c.device);
  const note = $('frame-note');
  if (note) {
    note.hidden = ui.key === 'about:blank' || ui.key === '' || ui.ready !== null;
    put('frame-note', ui.silent ? '페이지가 응답하지 않음 — 5초 안에 덮어 보기 준비 신호가 오지 않아 페이지만 보인다' : '페이지를 띄우는 중…');
  }
  if (!c.ok || !c.ready) return;
  const label = `${screenName(c.page)} · ${DEVICE_NAME[c.device]} · ${PERIOD_NAME[c.range]}`;
  const message = overlayMessage({ page: c.page, mode: c.mode, metric: c.metric, label, areas: c.areas, elements: c.elements, depth: c.depth });
  const sig = JSON.stringify(message);
  if (sig === ui.sent) return;
  ui.sent = sig;
  post(message);
}

// 고르기 단추·판 탭·틀 메시지·창 크기·숨김 — 기기·보기·지표는 주소 인자(목록 밖은 app 이 거른다), 판 탭은 이 문서에만
function wire(app) {
  for (const name of ['device', 'mode', 'metric']) {
    for (const b of document.querySelectorAll(`[data-${name}]`)) b.addEventListener('click', () => app.setParams({ [name]: b.dataset[name] }));
  }
  for (const b of document.querySelectorAll('[data-panel]')) {
    b.addEventListener('click', () => {
      ui.panel = b.dataset.panel;
      app.paint();
    });
  }
  window.addEventListener('message', (event) => onFrameMessage(app, event));
  window.addEventListener('resize', () => fit(app.view.p.device));
  document.addEventListener('visibilitychange', () => app.paint());
}

start({
  id: 'screens',
  home: '/screens.html',
  ranges: ['1d', '7d', '30d', '90d'],
  range: '7d',
  compare: () => ({ prev: false, week: false }),
  params: PARAMS,
  slowMs: SCREENS_MS,
  needs: (app) => {
    const page = screenOf(app);
    return page ? [listReq(app.view.range), pageReq(app.view.range, page)] : [listReq(app.view.range)];
  },
  cards: [
    ['pick', drawPick],
    ['frame', drawFrame],
    ['panel', (app) => drawPanel(app, post)],
  ],
  details: SCREEN_DETAILS,
  wire,
});
