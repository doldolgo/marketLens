// 방문자 페이지(스펙 066 §3.3 — 새, 067 IP 대역): 큰 수 넷 · 거르기(망 종류·나라·봇 뺌 — 주소 f=…, 찾기는 주소에 싣지 않음) ·
// 대역 표(머리 눌러 정렬 — 요청·사람 모양 페이지·마지막은 서버가, 그 밖은 불러온 행 안에서 · 200행씩) · 행 → 대역 상세 판
// (시간별 요청 · 종류·상태 띠 · 브라우저 정보·경로 상위 · 첫/마지막 페이지 · 최근 요청 100줄 펼치기 · ◀ 이전/다음 ▶).
import { $, button, el, int, kstText, list, num, once, partOf, pct, pressed, put, req, stateWord, tag, usable, widthOf } from './common.js';
import { cellPoints, sortRows } from './calc.js';
import { countryLabel, netName } from './names.js';
import { RANGE_MS } from './models.js';
import { IP_RANGES, NETS, SERVER_SORT, bandKpis, bandLabel, bandRows, ipsReqs, kindItems, loadedBands, pairList, recentRows, statusItems, validBand } from './ipmodel.js';
import { bar100s, bigNumber, sparkSvg, table, topList } from './ui.js';
import { start } from './app.js';

// 화면 상태(주소 밖) — 찾기 글자(대역이 주소·접속 기록에 남지 않게), 불러온 쪽 수, 정렬
const ui = { q: '', pages: 1, sort: { key: 'requests', dir: 'desc' } };
let order = []; // 지금 표 순서의 대역 — 상세 판 ◀ ▶
const reqsOf = (app) => ipsReqs(app.view, { q: ui.q, pages: ui.pages, sort: ui.sort.key });
const reset = (app) => {
  ui.pages = 1;
  app.refresh();
};

function drawKpis(app) {
  const got = loadedBands(app, reqsOf(app));
  for (const id of ['count', 'dc', 'top', 'new']) tag(`t-${id}`, got.part);
  if (!usable(got.part)) {
    for (const id of ['count', 'dc', 'top', 'new']) bigNumber(`v-${id}`, '–');
    return;
  }
  const k = bandKpis(got.bands, got.total, app.now());
  const approx = k.partial ? '≈ ' : '';
  bigNumber('v-count', int(k.count));
  bigNumber('v-dc', k.dc === null ? '–' : `${approx}${pct(k.dc, 0)}`);
  bigNumber('v-top', k.top === null ? '–' : `${approx}${pct(k.top, 0)}`);
  bigNumber('v-new', int(k.fresh));
  put('d-dc', k.partial ? `불러온 ${int(got.bands.length)}개 대역 기준` : '');
  put('d-top', k.partial ? `불러온 ${int(got.bands.length)}개 대역 기준` : '');
}

// 거르기 — 망 종류(하나)·나라(하나)·봇만 있는 대역 빼기·찾기
function drawFilters(app) {
  const net = app.filters('net')[0] ?? '';
  for (const b of document.querySelectorAll('[data-net]')) pressed(b, b.dataset.net === net);
  pressed($('bots'), app.hasFilter('bots', '0'));
  const got = loadedBands(app, reqsOf(app));
  const picked = app.filters('country')[0] ?? '';
  const codes = [...new Set([...got.bands.map((b) => b.country).filter((c) => typeof c === 'string' && /^[A-Z]{2}$/.test(c)), ...(picked ? [picked] : [])])].sort();
  once('country', [codes.join(), picked], () => {
    const select = $('country');
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

const COLUMNS = [
  { key: 'label', label: '대역', cls: 'mono' },
  { key: 'where', label: '나라 · 망' },
  {
    key: 'requests',
    label: '요청',
    num: true,
    cls: 'cellbar',
    // 글 뒤 막대 — 값 ÷ 불러온 대역의 최댓값(오른쪽에서 왼쪽으로)
    cell: (r) => {
      const box = el('span');
      const bar = el('i', 'bg');
      widthOf(bar, r.ratio);
      box.append(bar, el('span', null, int(r.requests)));
      return box;
    },
  },
  { key: 'pages', label: '사람 모양 페이지', num: true, fmt: int },
  { key: 'pairs', label: '짝 수', num: true, fmt: int },
  { key: 'botPct', label: '봇 %', num: true, fmt: (v) => pct(v, 0) },
  { key: 'fives', label: '5xx', num: true, fmt: int },
  { key: 'last', label: '처음 ~ 마지막', fmt: (v, r) => r.seen },
  { key: 'spark', label: '추이', sort: false, cell: (r) => sparkSvg(r.spark) },
];

function drawTable(app) {
  const reqs = reqsOf(app);
  const got = loadedBands(app, reqs);
  tag('t-bands', got.part);
  once('tbl-bands', [...got.deps, ui.sort.key, ui.sort.dir], () => {
    const node = $('tbl-bands');
    const more = $('bands-more');
    if (!usable(got.part)) {
      more.hidden = true;
      put('bands-count', '');
      order = [];
      return node.replaceChildren(el('caption', 'empty', stateWord(got.part)));
    }
    const rows = SERVER_SORT.includes(ui.sort.key) ? bandRows(got.bands) : sortRows(bandRows(got.bands), ui.sort.key, ui.sort.dir);
    order = rows.filter((r) => r.open).map((r) => r.band);
    table(node, COLUMNS, rows, {
      sort: ui.sort,
      onSort: (key) => {
        if (SERVER_SORT.includes(key)) {
          ui.sort = { key, dir: 'desc' };
          reset(app);
          return;
        }
        ui.sort = { key, dir: ui.sort.key === key && ui.sort.dir === 'desc' ? 'asc' : 'desc' };
        app.paint();
      },
      onRow: (r) => r.open && app.openDetail(`ip:${r.band}`),
    });
    put('bands-count', `${int(rows.length)} / ${int(got.total ?? rows.length)}개 대역`);
    more.hidden = !(num(got.total) > rows.length) || got.deps.some((e) => e === undefined);
    return rows;
  });
}

// --- 대역 하나의 상세 판(067 /admin/access/ip) ------------------------------------------------------

const ipReq = (band, range) => req('ip', { band, window: range });
function ipModel(app, pv, band) {
  const entry = app.entry(ipReq(band, pv.range));
  const part = partOf(entry);
  const at = order.indexOf(band);
  const base = {
    part,
    deps: [entry],
    title: bandLabel(band),
    unit: usable(part) ? [countryLabel(part.country ?? '–'), part.net ? netName(part.net) : '–'].join(' · ') : '',
    nav: { prev: at > 0 ? `ip:${order[at - 1]}` : null, next: at >= 0 && at < order.length - 1 ? `ip:${order[at + 1]}` : null },
    badge: usable(part) && part.partial ? ['warn', '일부만 읽음', '읽기 상한(압축 풀린 100MiB)에 닿아 최근 파일부터 그만큼만 읽었다'] : null,
    note: part?.why?.includes('bad_band') ? '이 대역은 상세를 볼 수 없다 — 대역 꼴이 아니다(bad_band)' : '',
  };
  if (!usable(part)) return base;
  const endMs = app.now();
  const startMs = endMs - (RANGE_MS[pv.range] ?? RANGE_MS['24h']);
  const hourly = list(part.hourly).filter((h) => num(h?.ts) !== null);
  const pts = (key) => cellPoints(hourly, key, 3_600_000, startMs, endMs);
  return {
    ...base,
    span: [startMs, endMs],
    time: {
      startMs,
      endMs,
      panes: [{ title: '요청', fmt: int }, { title: '사람 모양 페이지 · JS · 오류', fmt: int }],
      series: [
        { name: '요청', mark: 'acc', type: 'bar', data: pts('requests') },
        { name: '사람 모양 페이지', mark: 'c1', pane: 1, data: pts('pages') },
        { name: 'JS 신호', mark: 'c3', pane: 1, data: pts('js') },
        { name: '오류', mark: 'bad', pane: 1, type: 'bar', data: pts('errors') },
      ],
    },
    custom: (box) => bandParts(app, box, band, part),
    table: {
      title: '최근 요청(100)',
      sort: { key: 'ts', dir: 'desc' },
      search: ['path', 'kind', 'browser'],
      rows: recentRows(part.recent),
      columns: [
        { key: 'ts', label: '시각(KST)', fmt: (v) => kstText(v, true) },
        { key: 'path', label: '경로', cls: 'mono' },
        { key: 'status', label: '상태', num: true },
        { key: 'kind', label: '종류' },
        { key: 'browser', label: '브라우저' },
      ],
      expand: (r) => {
        const dl = el('dl', 'kv');
        for (const [k, v] of [['OS', r.os], ['기기', r.device], ['들어온 출처', r.referer || '–']]) dl.append(el('dt', null, k), el('dd', null, v || '–'));
        return dl;
      },
    },
  };
}
// 판 가운데 — 종류·상태 띠, 브라우저 정보·경로 상위, 짝의 첫/마지막 페이지, [이 대역만 보기]
function bandParts(app, box, band, part) {
  const holder = el('div', 'grid2');
  const left = el('div', 'b100s');
  left.id = 'ip-bars';
  const agents = el('div', 'list');
  agents.id = 'ip-agents';
  const paths = el('div', 'list');
  paths.id = 'ip-paths';
  const pairs = el('table', 'tbl');
  const only = button('btn', '이 대역만 보기');
  only.title = '표와 큰 수를 이 대역으로 거른다(찾기 칸)';
  only.addEventListener('click', () => {
    ui.q = band;
    $('q').value = band;
    app.closeDetail();
    reset(app);
  });
  const head = (text) => el('div', 'panel-head', text);
  const col = (...kids) => {
    const c = el('div');
    c.append(...kids);
    return c;
  };
  holder.append(col(head('요청 종류 · 응답 상태'), left), col(head('브라우저 정보 상위'), agents), col(head('경로 상위'), paths), col(head('짝의 첫 · 마지막 페이지(50)'), pairs));
  box.replaceChildren(holder, only);
  bar100s('ip-bars', [{ name: '요청 종류', items: kindItems(part.kinds), fmt: int }, { name: '응답 상태', items: statusItems(part.status), fmt: int }]);
  topList('ip-agents', pairList(part.agents), { n: 8, fmt: int });
  topList('ip-paths', pairList(part.paths), { n: 8, fmt: int });
  const pairRows = list(part.pairs).filter((p) => p && typeof p === 'object').slice(0, 50).map((p) => ({ ...p, npages: num(p.npages) }));
  table(pairs, [
    { key: 'entry', label: '첫 페이지' },
    { key: 'exit', label: '마지막 페이지' },
    { key: 'npages', label: '페이지 수', num: true, fmt: int },
    { key: 'channel', label: '들어온 길' },
    { key: 'device', label: '기기' },
    { key: 'browser', label: '브라우저' },
  ], pairRows);
}

function wire(app) {
  for (const b of document.querySelectorAll('[data-net]')) {
    b.addEventListener('click', () => {
      if (b.dataset.net) app.pickFilter('net', b.dataset.net);
      else app.removeFilter('net');
      ui.pages = 1;
    });
  }
  $('country').addEventListener('change', (ev) => {
    const v = String(ev.target?.value ?? '');
    ui.pages = 1;
    if (v) app.pickFilter('country', v);
    else app.removeFilter('country');
  });
  $('bots').addEventListener('click', () => {
    ui.pages = 1;
    if (app.hasFilter('bots', '0')) app.removeFilter('bots', '0');
    else app.addFilter('bots', '0');
  });
  // 찾기 — 글자마다 부르되 같은 경로는 한 줄로(떠 있는 호출이 끝난 뒤 마지막 글자만 — store)
  $('q').addEventListener('input', (ev) => {
    ui.q = String(ev.target?.value ?? '').slice(0, 64);
    reset(app);
  });
  $('bands-more').addEventListener('click', () => {
    ui.pages += 1;
    app.refresh();
  });
}

start({
  id: 'visitors',
  home: '/visitors.html',
  ranges: IP_RANGES,
  range: '24h',
  filters: { net: (v) => NETS.includes(v), country: (v) => /^[A-Z]{2}$/.test(v), bots: (v) => v === '0' },
  filterName: (k, v) => (k === 'net' ? ['망 종류', netName(v)] : k === 'country' ? ['나라', countryLabel(v)] : ['봇', '봇만 있는 대역 뺌']),
  needs: (app) => reqsOf(app),
  cards: [
    ['count', drawKpis],
    ['filters', drawFilters],
    ['bands', drawTable],
  ],
  details: {
    ip: {
      title: 'IP 대역',
      accepts: validBand,
      ranges: IP_RANGES,
      range: (r) => (IP_RANGES.includes(r) ? r : '24h'),
      needs: (app, pv, band) => [ipReq(band, pv.range)],
      model: ipModel,
    },
  },
  wire,
});
