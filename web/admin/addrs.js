// 방문자 'IP 별' 보기(스펙 070 §3.2 — 069 /admin/access/addrs): 큰 수 넷 · 'IP 전체는 … 부터' 줄 · 거르기(판정 여럿·망 종류·나라·
// 가린 기록 빼기 — 주소 f=…, 찾기는 주소에 싣지 않음) · IP 표(판정 배지·점수 막대·위치·요청 막대·브라우저로 연 페이지·마지막·작은 선,
// 행 title 에 근거 상위 셋, 머리 눌러 정렬 — 요청·페이지·마지막·점수는 서버가, 그 밖은 불러온 행 안에서 · 200행씩) · 행 → IP 상세 판.
import { $, el, int, kstText, num, once, pct, pressed, put, stateWord, tag, usable, widthOf } from './common.js';
import { sortRows } from './calc.js';
import { countryLabel } from './names.js';
import { loadedPages } from './ipmodel.js';
import { ADDR_SORT, VERDICTS, VERDICT_TONE, addrKpis, addrRows, addrsReqs, sinceText } from './addrmodel.js';
import { verdictBadge } from './addrdetail.js';
import { bigNumber, sparkSvg, table } from './ui.js';

// 화면 상태(주소 밖) — 찾기 글자(IP 가 주소·접속 기록에 남지 않게), 불러온 쪽 수, 정렬
const ui = { q: '', pages: 1, sort: { key: 'requests', dir: 'desc' } };
let order = []; // 지금 표 차례의 IP — 상세 판 ◀ ▶
export const addrOrder = () => order;
export const addrsOf = (app) => addrsReqs(app.view, { q: ui.q, pages: ui.pages, sort: ui.sort.key });
const loaded = (app) => loadedPages(app, addrsOf(app), 'rows', 'ip');
const KPIS = ['acount', 'abot', 'adc', 'anew'];

export function drawAddrKpis(app) {
  const got = loaded(app);
  for (const id of KPIS) tag(`t-${id}`, got.part);
  if (!usable(got.part)) {
    for (const id of KPIS) bigNumber(`v-${id}`, '–');
    for (const id of ['abot', 'adc']) put(`d-${id}`, '');
    return;
  }
  const k = addrKpis(got.rows, got.total, app.now());
  const approx = k.partial ? '≈ ' : '';
  bigNumber('v-acount', int(k.count));
  bigNumber('v-abot', k.bot === null ? '–' : `${approx}${pct(k.bot, 0)}`);
  bigNumber('v-adc', k.dc === null ? '–' : `${approx}${pct(k.dc, 0)}`);
  bigNumber('v-anew', int(k.fresh));
  for (const id of ['abot', 'adc']) put(`d-${id}`, k.partial ? `불러온 ${int(got.rows.length)}개 기준` : '');
}

// 'IP 전체는 MM-DD HH:MM 부터 — …'(069 since, 없으면 숨김)
export function drawSince(app, on) {
  const got = loaded(app);
  const text = on && usable(got.part) ? sinceText(got.part.since) : '';
  put('since', text);
  $('since').hidden = !text;
}

// 거르기 — 판정(여럿)·가린 기록 빼기·나라(불러온 행의 나라 + 고른 나라). 망 종류 단추는 두 보기가 같이 쓴다(visitors.js)
export function drawAddrFilters(app) {
  for (const b of document.querySelectorAll('[data-verdict]')) pressed(b, app.hasFilter('verdict', b.dataset.verdict));
  pressed($('a-masked'), app.hasFilter('masked', '0'));
  const got = loaded(app);
  const picked = app.filters('country')[0] ?? '';
  const codes = [...new Set([...got.rows.map((r) => r.country).filter((c) => typeof c === 'string' && /^[A-Z]{2}$/.test(c)), ...(picked ? [picked] : [])])].sort();
  once('a-country', [codes.join(), picked], () => {
    const select = $('a-country');
    const all = el('option', null, '모든 나라');
    all.value = '';
    select.replaceChildren(
      all,
      ...codes.map((code) => {
        const opt = el('option', null, countryLabel(code));
        opt.value = code;
        return opt;
      }),
    );
    select.value = picked;
  });
}

const ipCell = (r) => {
  const box = el('span');
  box.append(el('span', 'mono', r.label));
  if (r.masked) box.append(el('span', 'vtag', '대역'));
  return box;
};
const scoreCell = (r) => {
  const box = el('span', 'score');
  const bar = el('span', 'sbar');
  const fill = el('i', `fill ${VERDICT_TONE[r.verdict]}`);
  widthOf(fill, (r.score ?? 0) / 100);
  bar.append(fill);
  box.append(bar, el('span', null, r.score === null ? '–' : String(Math.round(r.score))));
  return box;
};
const COLUMNS = [
  { key: 'label', label: 'IP', cell: ipCell },
  { key: 'verdictName', label: '판정', cell: (r) => verdictBadge(r.verdict) },
  { key: 'score', label: '점수', num: true, cell: scoreCell },
  { key: 'place', label: '위치', cls: 'place', cell: (r) => Object.assign(el('span', null, r.place), { title: r.place }) },
  {
    key: 'requests',
    label: '요청',
    num: true,
    cls: 'cellbar',
    cell: (r) => {
      const box = el('span');
      const bar = el('i', 'bg');
      widthOf(bar, r.ratio);
      box.append(bar, el('span', null, int(r.requests)));
      return box;
    },
  },
  { key: 'pages', label: '브라우저로 연 페이지', num: true, fmt: int },
  { key: 'last', label: '마지막', fmt: (v) => (num(v) === null ? '–' : kstText(v).slice(5)) },
  { key: 'spark', label: '추이', sort: false, cell: (r) => sparkSvg(r.spark) },
];

export function drawAddrTable(app) {
  const got = loaded(app);
  tag('t-addrs', got.part);
  once('tbl-addrs', [...got.deps, ui.sort.key, ui.sort.dir], () => {
    const node = $('tbl-addrs');
    const more = $('addrs-more');
    if (!usable(got.part) || !got.rows.length) {
      more.hidden = true;
      put('addrs-count', '');
      order = [];
      return node.replaceChildren(el('caption', 'empty', usable(got.part) ? '자료 없음' : stateWord(got.part)));
    }
    const rows = ADDR_SORT.includes(ui.sort.key) ? addrRows(got.rows) : sortRows(addrRows(got.rows), ui.sort.key, ui.sort.dir);
    order = rows.filter((r) => r.open).map((r) => r.ip);
    table(node, COLUMNS, rows, {
      sort: ui.sort,
      onSort: (key) => {
        if (ADDR_SORT.includes(key)) {
          ui.sort = { key, dir: 'desc' };
          ui.pages = 1;
          app.refresh();
          return;
        }
        ui.sort = { key, dir: ui.sort.key === key && ui.sort.dir === 'desc' ? 'asc' : 'desc' };
        app.paint();
      },
      onRow: (r) => r.open && app.openDetail(`addr:${r.ip}`),
      rowTitle: (r) => r.reasons,
    });
    put('addrs-count', `${int(rows.length)} / ${int(got.total ?? rows.length)}개`);
    more.hidden = !(num(got.total) > rows.length) || got.deps.some((e) => e === undefined);
    return rows;
  });
}

export function wireAddrs(app) {
  for (const b of document.querySelectorAll('[data-net]')) b.addEventListener('click', () => (ui.pages = 1));
  for (const b of document.querySelectorAll('[data-verdict]')) {
    b.addEventListener('click', () => {
      ui.pages = 1;
      if (app.hasFilter('verdict', b.dataset.verdict)) app.removeFilter('verdict', b.dataset.verdict);
      else app.addFilter('verdict', b.dataset.verdict);
    });
  }
  $('a-country').addEventListener('change', (ev) => {
    const v = String(ev.target?.value ?? '');
    ui.pages = 1;
    if (v) app.pickFilter('country', v);
    else app.removeFilter('country');
  });
  $('a-masked').addEventListener('click', () => {
    ui.pages = 1;
    if (app.hasFilter('masked', '0')) app.removeFilter('masked', '0');
    else app.addFilter('masked', '0');
  });
  // 찾기 — 글자마다 부르되 같은 경로는 한 줄로(떠 있는 호출이 끝난 뒤 마지막 글자만 — store)
  $('a-q').addEventListener('input', (ev) => {
    ui.q = String(ev.target?.value ?? '').slice(0, 64);
    ui.pages = 1;
    app.refresh();
  });
  $('addrs-more').addEventListener('click', () => {
    ui.pages += 1;
    app.refresh();
  });
}

export const verdictOK = (v) => VERDICTS.includes(v);
