// Clarity 녹화(스펙 077 §3.3) — 녹화 목록 카드와 상세 판 둘(목록 `recs` · 녹화 하나 `rec:<행 번호>`). DOM 은 카드 표·판 위 링크 줄만.
// 재생은 Clarity 링크로(clarityLink — Clarity 출처·허용 글자만). Clarity 는 IP 를 주지 않아 방문자 페이지와 잇지 않는다.
// 기간 탭(1·3·7·30일, 기본 3일)은 주소에 싣지 않는다 — 판 주소의 행 번호는 그때 고른 기간의 목록 순서다.
// 이 파일 이름에 'clarity' 를 쓰지 않는다(insight.js 머리와 같은 까닭).
import { $, clarityLink, el, int, isObj, kstText, list, num, once, own, partOf, req, span, stateWord, tag, usable } from './common.js';
import { PAGE_NAME, tabName } from './names.js';
import { table, tabs } from './ui.js';

export const REC_DAYS = Object.freeze([['1', '1일'], ['3', '3일'], ['7', '7일'], ['30', '30일']]);
export const recState = { days: '3' };
export const recReq = () => req('recordings', { days: recState.days });
const CARD_ROWS = 10;

// 지나간 페이지 이름 — 우리 사이트면 페이지 이름표(names.js — 랜딩·대시보드 탭·정적 페이지), 그 밖은 서버가 줄인 주소 그대로
const OUR_HOSTS = new Set(['kimptrack.com', 'www.kimptrack.com']);
const STATIC_PAGE = Object.freeze({ '/': 'landing', '/privacy': 'privacy', '/kimp-chart': 'kimp-chart', '/kimp-history': 'kimp-history' });
export function recPage(url) {
  if (typeof url !== 'string' || !url) return '–';
  let u;
  try {
    u = new URL(url);
  } catch {
    return url;
  }
  if (!OUR_HOSTS.has(u.hostname)) return url;
  const path = u.pathname.replace(/\.html$/, '').replace(/\/(index)?$/, '') || '/';
  if (path === '/app') {
    const tab = u.searchParams.get('tab') ?? 'spread';
    return own(PAGE_NAME, `app-${tab}`) ?? `대시보드 ${tabName(tab)}`;
  }
  const key = own(STATIC_PAGE, path);
  return key ? PAGE_NAME[key] : path;
}

const EVENT_NAME = Object.freeze({ Click: '클릭', Scroll: '스크롤', Input: '입력', Resize: '창 크기', Navigate: '이동' });
const eventName = (type) => (typeof type === 'string' ? (own(EVENT_NAME, type) ?? type) : '–');
const mmss = (sec) => (num(sec) === null ? '–' : `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`);
const secText = (v) => (num(v) === null ? '–' : span(v));
const msText = (v) => (num(v) === null ? '' : v < 1000 ? `${int(v)}ms` : span(v / 1000));

// 목록 행 — 표가 쓰는 값으로. 행 번호(at)는 응답 순서(시작 최근 순) 그대로
export function recRows(part) {
  if (!usable(part, 'rows')) return [];
  return list(part.rows)
    .filter(isObj)
    .map((r, at) => ({
      at,
      startedAt: num(r.startedAt),
      activeSec: num(r.activeSec),
      totalSec: num(r.totalSec),
      pages: num(r.pages),
      clicks: num(r.clicks),
      link: r.link,
      path: list(r.timeline).filter(isObj).map((p) => recPage(p.url)),
      timeline: list(r.timeline).filter(isObj),
    }));
}
const lengthText = (r) => `${secText(r.activeSec)} / ${secText(r.totalSec)}`;
const pathText = (r) => (r.path.length ? r.path.join(' → ') : '–');

// 녹화 하나의 시간선 — 페이지 열림 줄 다음에 그 페이지의 사건 줄(시각은 녹화 시작부터)
export function timelineRows(rec) {
  return list(rec?.timeline)
    .filter(isObj)
    .flatMap((p) => [
    {
      atSec: num(p.atSec),
      kind: '페이지 열림',
      page: recPage(p.url),
      text: p.title ?? '',
      note: [num(p.stayMs) !== null ? `머묾 ${msText(p.stayMs)}` : '', num(p.loadMs) !== null ? `로딩 ${msText(p.loadMs)}` : '', p.referrer ? `들어온 곳 ${p.referrer}` : '']
        .filter(Boolean)
        .join(' · '),
    },
    ...list(p.events)
      .filter(isObj)
      .map((e) => ({ atSec: num(e.atSec), kind: eventName(e.type), page: recPage(p.url), text: e.text ?? '', note: '' })),
  ]);
}

const LIST_COLUMNS = Object.freeze([
  { key: 'startedAt', label: '시작 (KST)', fmt: (v) => kstText(v, true) },
  { key: 'activeSec', label: '길이 (활성 / 전체)', num: true, fmt: (v, r) => lengthText(r), csv: (r) => lengthText(r) },
  { key: 'pages', label: '페이지', num: true, fmt: (v) => (num(v) === null ? '–' : int(v)) },
  { key: 'clicks', label: '클릭', num: true, fmt: (v) => (num(v) === null ? '–' : int(v)) },
  { key: 'pathText', label: '지나간 페이지' },
  { key: 'link', label: '재생', sort: false, cell: (r) => clarityLink('재생 ↗', r.link) ?? el('span', 'dim', '–') },
]);
const withPath = (rows) => rows.map((r) => ({ ...r, pathText: pathText(r) }));
const EMPTY = '이 기간 녹화 없음 — 동의한 방문만 녹화된다';

export function drawRecs(app) {
  const entry = app.entry(recReq());
  const part = partOf(entry);
  tag('t-recs', part);
  tabs('tabs-recs', REC_DAYS, recState.days, (k) => {
    recState.days = k;
    app.refresh();
  });
  once('tbl-recs', [entry, recState.days], () => {
    const node = $('tbl-recs');
    if (!usable(part, 'rows')) return node.replaceChildren(el('caption', 'empty', stateWord(part)));
    const rows = withPath(recRows(part));
    if (!rows.length) return node.replaceChildren(el('caption', 'empty', EMPTY));
    return table(node, LIST_COLUMNS, rows.slice(0, CARD_ROWS), { onRow: (r) => app.openDetail(`rec:${r.at}`), rowTitle: (r) => `${kstText(r.startedAt, true)} · ${pathText(r)}` });
  });
}

// --- 판 ------------------------------------------------------------------------------------------

const validAt = (arg) => /^(0|[1-9]\d{0,2})$/.test(String(arg ?? ''));
export const REC_DETAILS = Object.freeze({
  recs: {
    title: '녹화',
    unit: 'Clarity · 최근 순 100',
    needs: () => [recReq()],
    model: (app) => {
      const entry = app.entry(recReq());
      const part = partOf(entry);
      const days = own(Object.fromEntries(REC_DAYS), recState.days) ?? '';
      const base = { part, deps: [entry, recState.days], note: `최근 ${days} · 기간은 카드의 탭에서 바꾼다` };
      if (!usable(part, 'rows')) return base;
      const rows = withPath(recRows(part));
      return {
        ...base,
        table: { title: rows.length ? '녹화마다' : EMPTY, columns: LIST_COLUMNS, rows, sort: { key: 'startedAt', dir: 'desc' }, search: ['pathText'], onRow: (r) => app.swapDetail(`rec:${r.at}`) },
      };
    },
  },
  rec: {
    title: 'Clarity 녹화',
    unit: '시작 KST · 길이',
    accepts: validAt,
    needs: () => [recReq()],
    model: (app, pv, arg) => {
      const entry = app.entry(recReq());
      const part = partOf(entry);
      const base = { part, deps: [entry, recState.days, arg], related: ['recs'] };
      if (!usable(part, 'rows')) return base;
      const rows = recRows(part);
      const at = Number(arg);
      const rec = rows[at];
      const nav = { prev: at > 0 ? `rec:${at - 1}` : null, next: at < rows.length - 1 ? `rec:${at + 1}` : null };
      if (!rec) return { ...base, nav, note: '이 녹화는 지금 목록에 없다 — 기간을 바꿨거나 목록이 새로 받아졌다' };
      const columns = [
        { key: 'atSec', label: '시각', num: true, fmt: mmss },
        { key: 'kind', label: '종류' },
        { key: 'page', label: '페이지' },
        { key: 'text', label: '글자 (클릭한 곳)' },
        { key: 'note', label: '머문 시간 · 로딩' },
      ];
      return {
        ...base,
        nav,
        unit: `${kstText(rec.startedAt, true)} · ${lengthText(rec)} · 페이지 ${rec.pages ?? '–'} · 클릭 ${rec.clicks ?? '–'}`,
        note: '시각은 녹화 시작부터 · 마우스 움직임은 Clarity 재생에서 본다',
        custom: (box) => {
          const link = clarityLink('Clarity 에서 재생 ↗', rec.link);
          const line = el('p', 'note');
          if (link) line.append(link, el('span', 'dim', ' · Clarity 로그인이 필요하다'));
          else line.textContent = '이 녹화는 재생 링크가 없다';
          box.replaceChildren(line);
        },
        table: { title: '시간선', columns, rows: timelineRows(rec), search: ['kind', 'page', 'text'], file: `kimptrack-clarity-recording-${at}.csv` },
      };
    },
  },
});
