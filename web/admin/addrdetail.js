// IP 상세 판(스펙 070 §3.3 — 069 /admin/access/addr): 머리 줄(IP·복사·판정 배지·점수) · 판정 근거(가중치 큰 순, 봇 쪽 빨강·사람 쪽
// 청록 막대, 점수 줄, 확인된/사칭 검색 로봇) · 어디(나라·지역·도시 '대략'·망 사업자·AS·망 종류·역방향 DNS·위도·경도, DB-IP 표시) — 판 맨 위,
// 그 아래 067 대역 판과 같은 것(시간별 요청·종류·상태 띠·브라우저 정보·경로 상위·짝의 첫/마지막 페이지·최근 요청 100줄 + 신호 칩).
// 대역 판(visitors.js)과 같이 쓰는 조각(시간별 그래프·쓰임 칸)도 여기 둔다.
import { $, button, clean, el, int, kstText, list, num, partOf, req, usable, widthOf } from './common.js';
import { cellPoints } from './calc.js';
import { RANGE_MS } from './models.js';
import { IP_RANGES, kindItems, pairList, statusItems } from './ipmodel.js';
import { FLAG_NAME, FLAG_TONE, VERDICT_NAME, VERDICT_TONE, badIp, crawlerLine, placeText, reasonRows, recentAddrRows, scoreLine, validAddr, verdictOf, whereRows } from './addrmodel.js';
import { bar100s, table, topList } from './ui.js';

// 판정 배지 — 모양 + 글(■ 봇 · ▲ 의심 · ● 사람 · ○ 모름 · ◆ 운영자)
export function verdictBadge(verdict) {
  const v = verdictOf(verdict);
  const box = el('span', 'verdict');
  box.append(el('i', `shape ${VERDICT_TONE[v]}`), el('span', null, VERDICT_NAME[v]));
  return box;
}

// 시간별 요청 그래프(067 hourly — 요청 막대 · 사람 모양 페이지·JS·오류) — 대역·IP 판이 같이 쓴다
export function hourlyTime(app, pv, part) {
  const endMs = app.now();
  const startMs = endMs - (RANGE_MS[pv.range] ?? RANGE_MS['24h']);
  const hourly = list(part.hourly).filter((h) => num(h?.ts) !== null);
  const pts = (key) => cellPoints(hourly, key, 3_600_000, startMs, endMs);
  return {
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
  };
}

// 쓰임 칸 — 종류·상태 띠, 브라우저 정보·경로 상위, 짝의 첫/마지막 페이지(50). 붙인 뒤 fillUsage 로 채운다(id 로 찾는 그리기라서)
export function usageHolder() {
  const holder = el('div', 'grid2');
  const col = (title, node) => {
    const c = el('div');
    c.append(el('div', 'panel-head', title), node);
    return c;
  };
  const bars = el('div', 'b100s');
  bars.id = 'ip-bars';
  const agents = el('div', 'list');
  agents.id = 'ip-agents';
  const paths = el('div', 'list');
  paths.id = 'ip-paths';
  const pairs = el('table', 'tbl');
  pairs.id = 'ip-pairs';
  holder.append(col('요청 종류 · 응답 상태', bars), col('브라우저 정보 상위', agents), col('경로 상위', paths), col('짝의 첫 · 마지막 페이지(50)', pairs));
  return holder;
}
export function fillUsage(part) {
  bar100s('ip-bars', [{ name: '요청 종류', items: kindItems(part.kinds), fmt: int }, { name: '응답 상태', items: statusItems(part.status), fmt: int }]);
  topList('ip-agents', pairList(part.agents), { n: 8, fmt: int });
  topList('ip-paths', pairList(part.paths), { n: 8, fmt: int });
  const rows = list(part.pairs).filter((p) => p && typeof p === 'object').slice(0, 50).map((p) => ({ ...p, npages: num(p.npages) }));
  table($('ip-pairs'), [
    { key: 'entry', label: '첫 페이지' },
    { key: 'exit', label: '마지막 페이지' },
    { key: 'npages', label: '페이지 수', num: true, fmt: int },
    { key: 'channel', label: '들어온 길' },
    { key: 'device', label: '기기' },
    { key: 'browser', label: '브라우저' },
  ], rows);
}

// 최근 요청 표의 칸(067 대역 판과 같음) + 신호 칩
export const RECENT_COLUMNS = Object.freeze([
  { key: 'ts', label: '시각(KST)', fmt: (v) => kstText(v, true) },
  { key: 'path', label: '경로', cls: 'mono' },
  { key: 'status', label: '상태', num: true },
  { key: 'kind', label: '종류' },
  { key: 'browser', label: '브라우저' },
]);
export function recentMore(r) {
  const dl = el('dl', 'kv');
  for (const [k, v] of [['OS', r.os], ['기기', r.device], ['들어온 출처', r.referer || '–']]) dl.append(el('dt', null, k), el('dd', null, v || '–'));
  return dl;
}
const flagChips = (r) => {
  const box = el('span', 'flags');
  box.append(...r.flags.map((f) => el('span', `flag${FLAG_TONE[f] ? ` ${FLAG_TONE[f]}` : ''}`, FLAG_NAME[f])));
  return box;
};

// --- 판 하나(page.details 의 'addr') ---------------------------------------------------------------

const addrReq = (ip, range) => req('addr', { ip, window: range });
// orderOf() — 지금 IP 표 차례(◀ ▶)
export function addrDetail(orderOf) {
  return {
    title: 'IP',
    accepts: validAddr,
    ranges: IP_RANGES,
    range: (r) => (IP_RANGES.includes(r) ? r : '24h'),
    needs: (app, pv, ip) => [addrReq(ip, pv.range)],
    model: (app, pv, ip) => addrModel(app, pv, ip, orderOf()),
  };
}

function addrModel(app, pv, ip, order) {
  const entry = app.entry(addrReq(ip, pv.range));
  const part = partOf(entry);
  const at = order.indexOf(ip);
  const base = {
    part,
    deps: [entry],
    title: clean(ip),
    unit: usable(part) ? placeText(part) : '',
    nav: { prev: at > 0 ? `addr:${order[at - 1]}` : null, next: at >= 0 && at < order.length - 1 ? `addr:${order[at + 1]}` : null },
    badge: usable(part) && part.partial ? ['warn', '일부만 읽음', '읽기 상한(압축 풀린 100MiB)에 닿아 최근 파일부터 그만큼만 읽었다'] : null,
    note: badIp(part) ? '이 IP 는 상세를 볼 수 없다 — IP 꼴이 아니다(bad_ip)' : '',
  };
  if (!usable(part)) return base;
  return {
    ...base,
    ...hourlyTime(app, pv, part),
    custom: (box) => addrParts(box, ip, part),
    table: {
      title: '최근 요청(100)',
      sort: { key: 'ts', dir: 'desc' },
      search: ['path', 'kind', 'browser', 'flagText'],
      rows: recentAddrRows(part.recent),
      columns: [...RECENT_COLUMNS, { key: 'flagText', label: '신호', sort: false, cell: flagChips, csv: (r) => r.flagText }],
      expand: recentMore,
    },
  };
}

let credit = null;
// 판 맨 위 두 칸(판정 근거·어디) + 그래프 아래 쓰임 칸. 사용자 칸 상자를 풀어(flat) 칸마다 격자 차례를 준다
function addrParts(box, ip, part) {
  box.className = 'panel flat';
  const verdict = el('section', 'panel addr-top');
  verdict.id = 'addr-verdict';
  const head = el('div', 'addr-head');
  const copy = button('btn', '복사');
  copy.title = 'IP 를 클립보드로';
  copy.addEventListener('click', () => {
    const clip = globalThis.navigator?.clipboard;
    if (!clip) {
      copy.textContent = '복사 안 됨';
      return;
    }
    clip.writeText(String(ip)).then(
      () => {
        copy.textContent = '복사됨';
      },
      () => {
        copy.textContent = '복사 안 됨';
      },
    );
  });
  head.append(el('span', 'mono addr-ip', ip), copy, verdictBadge(part.verdict), el('span', 'dim', `점수 ${num(part.score) === null ? '–' : Math.round(part.score)}`));
  const reasons = el('div', 'reasons');
  reasons.id = 'addr-reasons';
  const rows = reasonRows(part.reasons);
  if (!rows.length) reasons.append(el('p', 'empty', '근거 없음'));
  for (const r of rows) {
    const row = el('div', `reason ${r.side}`);
    const bar = el('span', 'rbar');
    const fill = el('i', `fill ${r.weight > 0 ? 'pos' : 'neg'}`);
    widthOf(fill, r.ratio);
    bar.append(fill);
    row.append(el('span', 'w', r.sign), el('span', 't', r.text), bar);
    row.title = r.line;
    reasons.append(row);
  }
  verdict.append(head, el('div', 'panel-head', '판정 근거'), reasons, el('p', 'score-line', scoreLine(part.score, part.verdict)));
  const crawler = crawlerLine(part);
  if (crawler) verdict.append(el('p', `crawler ${crawler.ok ? 'ok' : 'bad'}`, crawler.text));

  const where = el('section', 'panel addr-top');
  where.id = 'addr-where';
  const dl = el('dl', 'kv');
  for (const [k, v, small] of whereRows(part)) {
    const dd = el('dd', null, v);
    if (small) dd.append(el('small', null, small));
    dl.append(el('dt', null, k), dd);
  }
  where.append(el('div', 'panel-head', '어디'), dl);
  credit ??= $('dbip-sheet'); // 페이지의 정적 표시 링크를 옮겨 붙인다(스크립트는 주소를 쓰지 않는다) — 한 번 잡아 판마다 같은 노드
  if (credit) where.append(credit);

  const usage = el('section', 'panel');
  usage.append(usageHolder());
  box.replaceChildren(verdict, where, usage);
  fillUsage(part);
}
