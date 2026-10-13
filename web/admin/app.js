// 관리자 페이지 하나를 띄운다(스펙 066 §3.1) — 주소 상태(?range·cmp·f · #detail)·피드 부르기(빠른 10초·느린 60초, 보이는 동안만 —
// 064 규칙 그대로)·머리(기간·비교·'10초마다 · 시각')·거르기 칩·내비 모양·단축키(X 비교 켜고 끄기, Esc 닫기)·상세 판 열고 닫기.
// 카드 그리기는 페이지 모듈이 준 함수가 하고, 한 카드의 예외는 그 카드의 '표시 오류' 배지 하나로 끝난다.
// 주소를 쓰는 곳은 urlOf() 하나 — 그 페이지의 고정 주소(page.home) + 검사한 값으로만 만든다(지금 경로를 쓰지 않는다 — 029).
import { FAST, FAST_MS, SLOW_MS, $, button, clean, clock, el, notice, own, put, req, tagText } from './common.js';
import { marks, problems } from './rules.js';
import { mountDetail } from './detail.js';
import { createStore } from './store.js';
import { CMPS, cmpOpen, detailOK, readView, urlOf } from './view.js';

const RELOAD_MARK = 'relogin'; // 로그인 만료로 새로고침했다는 표시(연속 새로고침은 한 번까지 — 029)
const TONE_WORD = Object.freeze({ bad: '문제', warn: '주의' });

// --- 페이지 하나 ----------------------------------------------------------------------------

// page = { id, home(고정 주소), ranges, range(기본), compare(range) → { prev, week }, filters({ 키: 값 검사 }), filterName(k, v),
//   needs(app) → [req…](느린 묶음), rules(app) → { 이름: 결과 }(띠 규칙), cards [[이름, 그리기]], details({ id: 판 }), wire(app) }
export function start(page) {
  const view = readView(page, location.search, location.hash);
  const store = createStore({ wanted: (key) => needs().has(key), stopped: () => reloading });
  const errors = [];
  let reloading = false;
  let pushed = Boolean(history.state?.ktDetail); // 상세 판을 이 문서가 연 기록이면 ×·Esc 는 뒤로 가기
  let lastFast = 0;
  let detail = null;
  let menuOpen = false;

  const marked = () => (new URLSearchParams(location.search).has(RELOAD_MARK) ? [[RELOAD_MARK, '1']] : []);
  const needs = () => {
    const out = new Map(FAST.map((name) => [req(name).key, req(name)]));
    for (const r of page.needs?.(app) ?? []) out.set(r.key, r);
    for (const r of detail?.needs() ?? []) out.set(r.key, r);
    return out;
  };

  // 로그인 만료 신호 — 표시가 없으면 한 번 새로고침하고, 있으면 알림만
  function expired() {
    if (reloading) return;
    if (new URLSearchParams(location.search).has(RELOAD_MARK)) {
      notice('로그인이 만료됐거나 연결이 끊겼다 — 새로고침해 다시 로그인한다');
      return;
    }
    reloading = true;
    location.replace(urlOf(page, view, [[RELOAD_MARK, '1']]));
  }
  // 표시 지우기 — 마지막 만료 신호 뒤 지금 쓰는 열쇠가 모두 신호 없이 끝났을 때만
  function settle(signals) {
    if (signals.some(Boolean)) {
      store.okSince.clear();
      expired();
    } else if ([...needs().keys()].every((k) => store.okSince.has(k)) && marked().length) {
      history.replaceState(history.state, '', urlOf(page, view));
      notice('');
    }
    paint();
  }

  // 두 묶음은 따로 돈다. 같은 묶음은 앞선 호출이 끝나기 전에 다시 부르지 않고, 다음 호출은 앞선 호출을 시작한 때부터 잰다
  const fast = { fast: true, every: FAST_MS, busy: false, timer: 0, started: 0 };
  const slow = { fast: false, every: SLOW_MS, busy: false, timer: 0, started: 0 };
  async function run(loop) {
    clearTimeout(loop.timer);
    if (loop.busy || reloading || document.visibilityState !== 'visible') return;
    loop.busy = true;
    loop.started = Date.now();
    try {
      const signals = await Promise.all([...needs().values()].filter((r) => r.fast === loop.fast).map(store.load));
      if (loop.fast) lastFast = Date.now();
      settle(signals);
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
  // 보기가 바뀌면(기간·비교·거르기·상세 판) 아직 없는 느린 열쇠만 곧바로 부른다(묶음의 다음 시각은 그대로)
  function refresh() {
    const want = [...needs().values()].filter((r) => !r.fast && store.entry(r.key) === undefined && !store.busy(r.key));
    paint();
    if (want.length) Promise.all(want.map(store.load)).then(settle);
  }
  function replaceUrl() {
    history.replaceState(history.state, '', urlOf(page, view, marked()));
  }

  const app = {
    page,
    view,
    errors,
    entry: (r) => store.entry(typeof r === 'string' ? r : r.key),
    body: (r) => app.entry(r)?.body ?? null,
    now: () => lastFast || Date.now(),
    ready: () => store.entry(req('collector').key) !== undefined && store.entry(req('api').key) !== undefined,
    // 이 페이지가 가진 피드로 센 상태 띠 칩 — 개요 띠와 내비 모양이 같이 쓴다
    chips: () => problems({ ...Object.fromEntries(FAST.map((k) => [k, store.entry(req(k).key)])), ...(page.rules?.(app) ?? {}) }, Date.now()),
    setRange(v) {
      if (!page.ranges.includes(v) || v === view.range || reloading) return;
      view.range = v;
      if (!cmpOpen(page, v, view.cmp)) view.cmp = 'none';
      replaceUrl();
      refresh();
    },
    setCmp(v) {
      if (!CMPS.includes(v) || v === view.cmp || !cmpOpen(page, view.range, v) || reloading) return;
      view.cmp = v;
      replaceUrl();
      refresh();
    },
    filters: (k) => view.f.filter(([key]) => key === k).map(([, v]) => v),
    hasFilter: (k, v) => view.f.some(([key, value]) => key === k && value === v),
    addFilter(k, v) {
      if (!own(page.filters ?? {}, k)?.(v) || app.hasFilter(k, v)) return;
      view.f.push([k, v]);
      replaceUrl();
      refresh();
    },
    removeFilter(k, v) {
      const next = view.f.filter(([key, value]) => !(key === k && (v === undefined || value === v)));
      if (next.length === view.f.length) return;
      view.f.splice(0, view.f.length, ...next);
      replaceUrl();
      refresh();
    },
    // 값 하나만 고르는 거르기(망 종류·나라 등) — 같은 값을 다시 고르면 지운다
    pickFilter(k, v) {
      if (app.hasFilter(k, v)) return app.removeFilter(k, v);
      if (!own(page.filters ?? {}, k)?.(v)) return undefined;
      view.f.splice(0, view.f.length, ...view.f.filter(([key]) => key !== k), [k, v]);
      replaceUrl();
      return refresh();
    },
    openDetail(id) {
      if (!detailOK(page, id) || id === view.detail || reloading) return;
      view.detail = id;
      history.pushState({ ktDetail: id }, '', urlOf(page, view, marked()));
      pushed = true;
      detail.open(id);
      refresh();
    },
    // ◀ 이전 / 다음 ▶ — 기록을 쌓지 않고 지금 판을 바꾼다(뒤로 가기는 판을 닫는다)
    swapDetail(id) {
      if (!view.detail || !detailOK(page, id) || id === view.detail) return;
      view.detail = id;
      history.replaceState(pushed ? { ktDetail: id } : history.state, '', urlOf(page, view, marked()));
      detail.open(id);
      refresh();
    },
    closeDetail() {
      if (!view.detail) return;
      if (pushed) {
        history.back(); // popstate 가 닫는다 — 뒤로 가기와 같은 길
        return;
      }
      view.detail = null;
      replaceUrl();
      detail.close();
      paint();
    },
    paint: () => paint(),
    refresh: () => refresh(),
    expired,
  };

  function paint() {
    for (const [name, draw] of page.cards) {
      try {
        draw(app);
      } catch (err) {
        errors.push([name, String(err?.stack ?? err).slice(0, 300)]);
        tagText(`t-${name}`, ['bad', '표시 오류', '응답 모양이 예상과 다르다']);
      }
    }
    try {
      chrome();
    } catch (err) {
      errors.push(['chrome', String(err?.stack ?? err).slice(0, 300)]);
    }
    try {
      detail.paint();
    } catch (err) {
      errors.push(['detail', String(err?.stack ?? err).slice(0, 300)]);
      tagText('t-sheet', ['bad', '표시 오류', '응답 모양이 예상과 다르다']);
    }
    errors.splice(0, Math.max(0, errors.length - 20));
  }

  // 머리·내비·거르기 칩 — 고른 기간·비교, 비교가 없는 자료는 단추를 끄고 title, '10초마다 · 시각', 페이지마다 가장 나쁜 상태 모양
  let chipsKey = null;
  function chrome() {
    for (const b of document.querySelectorAll('[data-range]')) b.setAttribute('aria-pressed', String(b.dataset.range === view.range));
    for (const b of document.querySelectorAll('[data-cmp]')) {
      const open = cmpOpen(page, view.range, b.dataset.cmp);
      b.setAttribute('aria-pressed', String(b.dataset.cmp === view.cmp));
      b.disabled = !open;
      b.title = open ? '' : '이 자료는 비교 없음';
    }
    const stamp = lastFast ? clock(lastFast) : '--:--:--';
    put('clock', stamp);
    put('updated', stamp);
    const tones = app.ready() ? marks(app.chips()) : {};
    for (const id of ['overview', 'server', 'traffic']) {
      const node = $(`m-${id}`);
      if (!node) continue;
      const tone = tones[id];
      node.className = tone ? `mark shape ${tone}` : 'mark';
      node.title = tone ? TONE_WORD[tone] : '';
    }
    const key = JSON.stringify(view.f);
    const box = $('filters');
    if (!box || key === chipsKey) return;
    chipsKey = key;
    box.hidden = !view.f.length;
    box.replaceChildren(
      ...view.f.map(([k, v]) => {
        const [label, value] = page.filterName?.(k, v) ?? [k, v];
        const chipNode = el('span', 'fchip');
        const x = button(null, '×');
        x.setAttribute('aria-label', `${clean(label)} ${clean(value)} 거르기 지우기`);
        x.addEventListener('click', () => app.removeFilter(k, v));
        chipNode.append(el('span', null, label), el('b', null, value), x);
        return chipNode;
      }),
    );
  }

  function menu(open) {
    menuOpen = open;
    $('app')?.setAttribute('data-menu', open ? 'open' : 'closed');
    $('menu-btn')?.setAttribute('aria-expanded', String(open));
  }

  detail = mountDetail(app);
  for (const b of document.querySelectorAll('[data-range]')) b.addEventListener('click', () => app.setRange(b.dataset.range));
  for (const b of document.querySelectorAll('[data-cmp]')) b.addEventListener('click', () => app.setCmp(b.dataset.cmp));
  for (const b of document.querySelectorAll('[data-detail]')) b.addEventListener('click', () => app.openDetail(b.dataset.detail));
  $('menu-btn')?.addEventListener('click', () => menu(!menuOpen));
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      if (view.detail) app.closeDetail();
      else if (menuOpen) menu(false);
      return;
    }
    const tag = String(ev.target?.tagName ?? '');
    if (ev.altKey || ev.ctrlKey || ev.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || ev.target?.isContentEditable) return;
    // X — 비교 켜고 끄기(Plausible 과 같은 키). 상세 판이 열려 있으면 그 판의 비교
    if ((ev.key === 'x' || ev.key === 'X') && !detail.toggleCmp()) app.setCmp(view.cmp === 'none' ? 'prev' : 'none');
  });
  // 뒤로·앞으로 가기 — 상세 판만 따른다(기간·비교·거르기는 메모리 값 그대로 두고 주소를 맞춘다)
  window.addEventListener('popstate', () => {
    const next = readView(page, location.search, location.hash).detail;
    pushed = Boolean(history.state?.ktDetail);
    if (next === view.detail) return;
    view.detail = next;
    history.replaceState(history.state, '', urlOf(page, view, marked()));
    if (next) detail.open(next);
    else detail.close();
    refresh();
  });
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

  page.wire?.(app);
  if (view.detail) detail.open(view.detail);
  paint();
  run(fast);
  run(slow);
  return app;
}
