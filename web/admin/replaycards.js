// 방문자 페이지의 화면 기록 카드 둘(스펙 074 §3.1)과 IP 상세 판의 '화면 기록' 절(§3.2) — 073 피드 live(지금 묶음 5초)·sessions(느린 60초)·
// sessions?ip=(IP 판이 열려 있을 때). 행 → 재생 판(replaysheet.js), 지금 보는 중의 행은 따라 보기로. 글자는 textContent 로만.
import { $, el, int, num, once, partOf, put, span, stateWord, tag, usable } from './common.js';
import { matches, sortRows } from './calc.js';
import { liveReq, liveRows, sessionRows, sessionsReq } from './replaymodel.js';
import { openReplay } from './replaysheet.js';
import { table } from './ui.js';

// 화면 상태(주소 밖) — 화면 기록 찾기 글자(IP 가 주소·접속 기록에 남지 않게)·정렬
const ui = { q: '', sort: { key: 'first', dir: 'desc' } };
export const replayNeeds = () => [liveReq(), sessionsReq()];
const secs = (v) => (num(v) === null ? '–' : span(v));
// 동의 칸 — 채운 점 = 화면 분석에 동의, 빈 점 = 미동의(거부함·하루 동안 안 보기·답 안 함·GPC)
const consentCell = (r) => {
  const box = el('span', 'verdict');
  box.append(el('i', `shape ${r.agreed ? 'ok' : 'none'}`), el('span', null, r.consent));
  return box;
};
const CONSENT_COLUMN = { key: 'consent', label: '동의', cell: consentCell };

const LIVE_COLUMNS = [
  { key: 'ip', label: 'IP', cls: 'mono' },
  CONSENT_COLUMN,
  { key: 'now', label: '지금 화면' },
  { key: 'dev', label: '기기' },
  { key: 'stay', label: '머문 시간', num: true, fmt: secs },
  { key: 'ago', label: '마지막 움직임', num: true },
];

// 지금 보는 중(§3.1) — 60초 안에 묶음이 들어온 조각, 최근 순. 행을 누르면 따라 보기
export function drawLive(app) {
  const entry = app.entry(liveReq());
  const part = partOf(entry);
  tag('t-rplive', part);
  once('tbl-rplive', [entry], () => {
    const node = $('tbl-rplive');
    if (!usable(part)) return node.replaceChildren(el('caption', 'empty', stateWord(part)));
    const rows = liveRows(part.rows, num(part.now) ?? app.now());
    if (!rows.length) return node.replaceChildren(el('caption', 'empty', '지금 화면을 움직이는 방문 없음'));
    table(node, LIVE_COLUMNS, rows, { onRow: (r) => r.open && openReplay(app, r.sid, true) });
    return rows;
  });
}

const SESSION_COLUMNS = [
  { key: 'first', label: '시작(KST)', cls: 'mono', fmt: (v, r) => r.start },
  { key: 'ip', label: 'IP', cls: 'mono' },
  CONSENT_COLUMN,
  { key: 'pages', label: '화면' },
  { key: 'dev', label: '기기' },
  { key: 'len', label: '길이', num: true, fmt: secs },
  { key: 'clicks', label: '누름', num: true, fmt: int },
];

// 화면 기록(§3.1) — 최근 조각 표(머리 눌러 정렬·IP·화면 찾기), 행 → 재생 판
export function drawSessions(app) {
  const entry = app.entry(sessionsReq());
  const part = partOf(entry);
  tag('t-replay', part);
  once('tbl-replay', [entry, ui.q, ui.sort.key, ui.sort.dir], () => {
    const node = $('tbl-replay');
    if (!usable(part)) {
      put('replay-count', '');
      return node.replaceChildren(el('caption', 'empty', stateWord(part)));
    }
    const all = sessionRows(part.rows);
    if (!all.length) {
      put('replay-count', '');
      return node.replaceChildren(el('caption', 'empty', '화면 기록 없음 — 모든 방문을 기록하고 서비스 종료까지 보관한다'));
    }
    const rows = sortRows(all.filter((r) => matches(r, ['ip', 'pages', 'consent'], ui.q)), ui.sort.key, ui.sort.dir);
    table(node, SESSION_COLUMNS, rows, {
      sort: ui.sort,
      onSort: (key) => {
        ui.sort = { key, dir: ui.sort.key === key && ui.sort.dir === 'desc' ? 'asc' : 'desc' };
        app.paint();
      },
      onRow: (r) => r.open && openReplay(app, r.sid, false),
    });
    put('replay-count', `${int(rows.length)} / ${int(all.length)}개 조각`);
    return rows;
  });
}

export function wireReplayCards(app) {
  $('rp-q').addEventListener('input', (ev) => {
    ui.q = String(ev.target?.value ?? '').slice(0, 64);
    app.paint();
  });
}

// --- IP 상세 판의 '화면 기록' 절(§3.2) — 가린 대역(전체 저장 전)에는 두지 않는다 -------------------------------

const masked = (ip) => typeof ip !== 'string' || ip.includes('/');
export const ipReplayNeeds = (ip) => (masked(ip) ? [] : [sessionsReq(ip)]);
export const ipReplayEntry = (app, ip) => (masked(ip) ? null : app.entry(sessionsReq(ip)));

const IP_COLUMNS = [
  { key: 'first', label: '시작(KST)', cls: 'mono', fmt: (v, r) => r.start },
  CONSENT_COLUMN,
  { key: 'pages', label: '화면' },
  { key: 'dev', label: '기기' },
  { key: 'len', label: '길이', num: true, fmt: secs },
  { key: 'clicks', label: '누름', num: true, fmt: int },
  { key: 'go', label: '', sort: false, cell: (r) => el('span', 'rp-go', r.open ? '재생 ▶' : '–') },
];

// 판 맨 위 칸(판정 근거·어디) 다음 — 그 IP 의 조각 표, 행 → 재생 판. 가린 대역이면 null
export function ipReplaySection(app, ip) {
  if (masked(ip)) return null;
  const part = partOf(app.entry(sessionsReq(ip)));
  const box = el('section', 'panel addr-top rp-ip');
  box.id = 'addr-replay';
  const head = el('div', 'panel-head');
  head.append(el('span', null, '화면 기록'));
  box.append(head);
  if (!usable(part)) {
    box.append(el('p', 'empty', part?.why?.includes('bad_ip') ? '이 IP 는 화면 기록을 찾을 수 없다(bad_ip)' : stateWord(part)));
    return box;
  }
  const rows = sessionRows(part.rows);
  if (!rows.length) {
    box.append(el('p', 'empty', '이 IP 의 화면 기록 없음'));
    return box;
  }
  const agreed = rows.filter((r) => r.agreed).length;
  head.append(el('span', 'unit', `${int(rows.length)}개 조각 · 동의 ${int(agreed)} · 미동의 ${int(rows.length - agreed)} · 서비스 종료까지 보관`));
  const scroll = el('div', 'scroll');
  const tbl = el('table', 'tbl');
  scroll.append(tbl);
  box.append(scroll);
  table(tbl, IP_COLUMNS, rows, { onRow: (r) => r.open && openReplay(app, r.sid, false) });
  return box;
}
