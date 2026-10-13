// 상세 판(스펙 066 §3.2 — 조사 §3.3). 카드의 ⤢ 나 제목을 누르면 같은 페이지 위에 뜨는 거의 전체 화면 판(주소 #detail — app.js).
// 판 안(위 → 아래): 머리(제목·단위·상태 · 기간 · 비교 · CSV · ×) → 계열 칩(누르면 숨김/보임) → 큰 그래프(휠·끌기 확대·이동, 아래
// 미니맵, '구간 확대' 로 끌어서 고른 구간, '원래대로') → 통계 표(보이는 구간 — 최소·평균·최대·p95·마지막·이전 대비) → 원자료 표
// (머리 눌러 정렬, 200행씩) 또는 목록 표(찾기·정렬) → 관련 카드. 판의 기간·비교는 판만의 값이다(열 때 페이지 값을 받는다).
// 판 그림은 페이지의 connect 무리에 넣지 않고(뒤 카드가 따라 움직이지 않게), 닫으면 dispose 한다.
import { $, button, download, int, kstText, list, num, own, pressed, put, tag } from './common.js';
import { csvName, hasValue, matches, sortRows, toCsv } from './calc.js';
import { blank, chartOf, dispose, draw, timeOption } from './charts.js';
import { RANGE_NAME } from './names.js';
import { csvValue, rawTable, relatedButtons, seriesChips, statTable } from './tables.js';
import { table } from './ui.js';

const STEP = 200; // 원자료·목록 표는 200행씩
// 판 하나(page.details 의 값) = { title, unit, ranges?(판 기간 단추), range?(페이지 기간 → 판 기간), compare?(range) → { prev, week },
//   needs(app, pv, arg) → [req…], model(app, pv, arg) → { part, deps, title?, unit?, note?, nav?, span?, related?,
//   time?: { startMs, endMs, panes, series, areas, empty }, chart?(시간 축 없는 option), table?: { title, columns, rows, sort, search, onRow },
//   custom?(상자, ctx) },
//   related?: [판 id…], accepts?(arg) }
export function mountDetail(app) {
  const page = app.page;
  const sheet = $('sheet');
  if (!sheet) return { needs: () => [], open() {}, close() {}, paint() {}, toggleCmp: () => false };
  let id = null;
  let pv = null; // 판 보기 { range, cmp, hidden, sort, rows, find, selecting, v }
  let opener = null;
  let last = null;
  let model = null;
  let shown = null; // 지금 표의 { tbl, rows } — CSV 가 쓴다
  const wired = new WeakSet();

  const providerOf = (full) => {
    const at = full.indexOf(':');
    return [own(page.details ?? {}, at < 0 ? full : full.slice(0, at)), at < 0 ? null : full.slice(at + 1)];
  };
  const cmpOK = (p, range, c) => c === 'none' || Boolean(p.compare?.(range)?.[c]);
  const visible = (t) => t.series.filter((s) => !pv.hidden.has(s.name) && (hasValue(s.data) || hasValue(s.cmp)));

  function open(full) {
    const [p] = providerOf(full);
    if (!p) return;
    const same = id !== null && providerOf(id)[0] === p;
    if (id === null) opener = document.activeElement ?? null;
    const range = same ? pv.range : p.ranges?.includes(app.view.range) ? app.view.range : (p.range?.(app.view.range) ?? p.ranges?.[0] ?? null);
    const cmp = same ? pv.cmp : cmpOK(p, range, app.view.cmp) ? app.view.cmp : 'none';
    pv = { range, cmp, hidden: new Set(), sort: null, rows: STEP, find: '', selecting: false, v: 0 };
    id = full;
    last = null;
    dispose('c-sheet');
    const find = $('sheet-find');
    if (find) find.value = '';
    sheet.hidden = false;
    $('app')?.setAttribute('inert', '');
    document.body?.setAttribute('data-sheet', 'open');
    paint();
    $('sheet-panel')?.focus?.();
  }
  function close() {
    if (id === null) return;
    id = null;
    pv = null;
    model = null;
    last = null;
    shown = null;
    dispose('c-sheet');
    sheet.hidden = true;
    $('app')?.removeAttribute('inert');
    document.body?.removeAttribute('data-sheet');
    const back = opener;
    opener = null;
    back?.focus?.();
  }

  // 그 판이 기대는 값(결과 객체)이나 판 보기가 바뀌었을 때만 몸을 다시 그린다 — 10초 묶음마다 확대·스크롤이 날아가지 않게
  function paint() {
    if (id === null) return;
    const [p, arg] = providerOf(id);
    model = p.model(app, pv, arg) ?? {};
    put('sheet-title', model.title ?? p.title);
    put('sheet-unit', model.unit ?? p.unit ?? '');
    tag('t-sheet', model.part);
    head(p, model);
    const deps = [...list(model.deps), pv.v, pv.range, pv.cmp, id];
    if (last && last.length === deps.length && deps.every((d, i) => d === last[i])) return;
    last = deps;
    body(p, model);
  }

  function head(p, m) {
    const box = $('sheet-ranges');
    const ranges = list(p.ranges);
    if (box) {
      box.hidden = ranges.length < 2;
      const key = `${ranges.join()}|${pv.range}`;
      if (box.dataset.key !== key) {
        box.dataset.key = key;
        box.replaceChildren(
          ...ranges.map((r) => {
            const b = button(null, own(RANGE_NAME, r) ?? r);
            pressed(b, r === pv.range);
            b.addEventListener('click', () => setRange(r));
            return b;
          }),
        );
      }
    }
    for (const b of document.querySelectorAll('[data-scmp]')) {
      const ok = cmpOK(p, pv.range, b.dataset.scmp);
      b.disabled = !ok;
      b.title = ok ? '' : '이 자료는 비교 없음';
      pressed(b, b.dataset.scmp === pv.cmp);
    }
    for (const [nid, target] of [['sheet-prev', m.nav?.prev], ['sheet-next', m.nav?.next]]) {
      const b = $(nid);
      if (!b) continue;
      b.hidden = !m.nav;
      b.disabled = !target;
    }
    put('sheet-note', m.note ?? '');
  }

  function body(p, m) {
    const t = m.time;
    $('sheet-chips')?.replaceChildren(...(t ? seriesChips(t.series, pv.hidden, toggle) : []));
    const graphBox = $('sheet-graph');
    if (graphBox) graphBox.hidden = !t && !m.chart;
    for (const bid of ['sheet-select', 'sheet-reset']) {
      const b = $(bid);
      if (b) b.hidden = !t;
    }
    if (t) graph(t);
    else if (m.chart) draw('c-sheet', m.chart); // 시간 축이 없는 그림(흐름·열지도·막대) — 확대·통계 없이
    else dispose('c-sheet');
    tables(m);
    const custom = $('sheet-custom');
    if (custom) {
      custom.hidden = !m.custom;
      if (m.custom) m.custom(custom, { app, pv });
      else custom.replaceChildren();
    }
    related(m.related ?? p.related);
  }

  function graph(t) {
    const series = visible(t);
    if (!series.length) {
      blank('c-sheet', t.empty ?? '자료 없음');
      return;
    }
    const chart = draw('c-sheet', timeOption({ ...t, series, zoom: 'detail', legend: false, labels: false }));
    if (!chart || typeof chart.on !== 'function') return;
    if (!wired.has(chart)) {
      wired.add(chart);
      chart.on('datazoom', () => model && tables(model));
    }
    if (pv.selecting) chart.dispatchAction({ type: 'takeGlobalCursor', key: 'dataZoomSelect', dataZoomSelectActive: true });
  }

  // 보이는 구간 — 그래프의 확대 상태(%)를 시각으로. 그래프가 없으면 모델의 창 전체
  function windowOf(t) {
    const dz = chartOf('c-sheet')?.getOption?.()?.dataZoom?.[0];
    const span = t.endMs - t.startMs;
    if (!dz || !(span > 0)) return [t.startMs, t.endMs];
    const lo = num(dz.start) !== null ? t.startMs + (span * dz.start) / 100 : (num(dz.startValue) ?? t.startMs);
    const hi = num(dz.end) !== null ? t.startMs + (span * dz.end) / 100 : (num(dz.endValue) ?? t.endMs);
    return [Math.max(t.startMs, lo), Math.min(t.endMs, hi)];
  }

  function tables(m) {
    const t = m.time;
    const stats$ = $('sheet-stats');
    if (stats$) stats$.hidden = !t;
    const [lo, hi] = t ? windowOf(t) : [null, null];
    if (t) {
      put('sheet-window', `${kstText(lo)} ~ ${kstText(hi)} · KST`);
      statTable($('sheet-stat-tbl'), t, visible(t), lo, hi);
    }
    const tbl = m.table ?? (t ? rawTable(id, t, visible(t), lo, hi) : null);
    const box = $('sheet-table');
    if (box) box.hidden = !tbl;
    if (tbl) listTable(tbl);
    else shown = null;
  }

  function listTable(tbl) {
    put('sheet-table-title', tbl.title ?? '원자료');
    const findBox = $('sheet-find-box');
    if (findBox) findBox.hidden = !tbl.search;
    const found = tbl.search && pv.find ? tbl.rows.filter((r) => matches(r, tbl.search, pv.find)) : tbl.rows;
    const sort = pv.sort ?? tbl.sort ?? null;
    const rows = sort ? sortRows(found, sort.key, sort.dir) : found;
    const part = rows.slice(0, pv.rows);
    table($('sheet-tbl'), tbl.columns, part, {
      sort,
      onSort: (key) => {
        pv.sort = { key, dir: sort?.key === key && sort.dir === 'desc' ? 'asc' : 'desc' };
        pv.rows = STEP;
        tables(model);
      },
      onRow: tbl.onRow,
    });
    put('sheet-count', `${int(part.length)} / ${int(rows.length)}행`);
    const more = $('sheet-more');
    if (more) more.hidden = rows.length <= part.length;
    shown = { tbl, rows };
  }

  function related(ids) {
    const box = $('sheet-related');
    const ok = list(ids).filter((rid) => own(page.details ?? {}, rid) && rid !== id);
    if (!box) return;
    box.hidden = !ok.length;
    box.replaceChildren(...relatedButtons(ok.map((rid) => [rid, own(page.details, rid).title]), (rid) => app.openDetail(rid)));
  }

  // CSV — 보이는 구간·보이는 계열(시계열은 UTC·KST 두 칸), 표는 찾고 정렬한 행 전부
  function csv() {
    if (!model || !shown) return;
    const { tbl, rows } = shown;
    if (tbl.csv) {
      const [name, text] = tbl.csv();
      download(name, text);
      return;
    }
    const [lo, hi] = model.span ?? [app.now(), app.now()];
    download(csvName(id, lo, hi), toCsv(tbl.columns.map((c) => c.label), rows.map((r) => tbl.columns.map((c) => csvValue(c, r)))));
  }

  function setRange(r) {
    const [p] = providerOf(id);
    if (r === pv.range) return;
    pv.range = r;
    if (!cmpOK(p, r, pv.cmp)) pv.cmp = 'none';
    pv.v += 1;
    app.refresh();
  }
  function setCmp(c) {
    const [p] = providerOf(id);
    if (c === pv.cmp || !cmpOK(p, pv.range, c)) return;
    pv.cmp = c;
    pv.v += 1;
    app.refresh();
  }
  function toggle(name) {
    if (pv.hidden.has(name)) pv.hidden.delete(name);
    else pv.hidden.add(name);
    pv.v += 1;
    paint();
  }

  $('sheet-close')?.addEventListener('click', () => app.closeDetail());
  sheet.addEventListener('click', (ev) => {
    if (ev.target === sheet) app.closeDetail();
  });
  $('sheet-csv')?.addEventListener('click', () => csv());
  for (const b of document.querySelectorAll('[data-scmp]')) b.addEventListener('click', () => id !== null && setCmp(b.dataset.scmp));
  $('sheet-select')?.addEventListener('click', () => {
    if (id === null) return;
    pv.selecting = !pv.selecting;
    pressed($('sheet-select'), pv.selecting);
    chartOf('c-sheet')?.dispatchAction({ type: 'takeGlobalCursor', key: 'dataZoomSelect', dataZoomSelectActive: pv.selecting });
  });
  $('sheet-reset')?.addEventListener('click', () => {
    chartOf('c-sheet')?.dispatchAction({ type: 'dataZoom', start: 0, end: 100 });
    if (model) tables(model);
  });
  $('sheet-find')?.addEventListener('input', (ev) => {
    if (id === null) return;
    pv.find = String(ev.target?.value ?? '');
    pv.rows = STEP;
    tables(model);
  });
  $('sheet-more')?.addEventListener('click', () => {
    if (id === null) return;
    pv.rows += STEP;
    tables(model);
  });
  $('sheet-prev')?.addEventListener('click', () => model?.nav?.prev && app.swapDetail(model.nav.prev));
  $('sheet-next')?.addEventListener('click', () => model?.nav?.next && app.swapDetail(model.nav.next));

  return {
    needs() {
      if (id === null) return [];
      const [p, arg] = providerOf(id);
      return p.needs?.(app, pv, arg) ?? [];
    },
    open,
    close,
    paint,
    // X — 열린 판의 비교 켜고 끄기(판이 닫혀 있으면 false — 페이지 비교)
    toggleCmp() {
      if (id === null) return false;
      setCmp(pv.cmp === 'none' ? 'prev' : 'none');
      return true;
    },
  };
}
