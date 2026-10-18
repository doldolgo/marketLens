// Clarity 페이지(스펙 066 §3.3 — 새): 040 요약(최근 24시간) + 068 날별 기록·차원. 큰 수 여섯(+ 날별 작은 선·전날 대비·스크롤·시간 한 줄) ·
// 불만 신호 여섯(쉬운 풀이·좋음/주의/나쁨·방문 100칸·전날) · 맞대기(Clarity 세션 vs 우리 방문자 — 038 visitors.days) · 페이지 × 기기 줄
// (040 pages.groups — 방문 막대·페이지 모양 스크롤·머문/움직인 시간 띠·가장 잦은 신호) · 나라 · 기기·브라우저·OS 100% 띠 · 유입(Channel·Source) ·
// 녹화 목록(077 — insightrec.js). 카드마다 데이터로 만든 한 문장(그림은 insightviz.js). 머리에 '마지막 받음 n시간 전 · 다음 HH:MM'.
// Clarity 는 IP·개인 단위 자료를 주지 않는다(방문자 페이지로 안내).
// 이 파일 이름에 'clarity' 를 쓰지 않는다 — 관리자 파일이 Clarity 태그를 싣지 않는다는 033 의 글자 검사(src=…clarity)와 겹치지 않게.
import { $, ago, el, hm, int, num, once, partOf, put, stateWord, tag, usable } from './common.js';
import { deltaText } from './calc.js';
import { blank, draw, sparkOption, timeOption } from './charts.js';
import { HISTORY_RANGES, historyCompare, historyReqs, historyWindow } from './models.js';
import {
  ATTN_TODAY,
  CLARITY,
  INSIGHT_DETAILS,
  KPIS,
  diagOf,
  dimItems,
  dimsOf,
  dimsSay,
  kpiSay,
  lastTwo,
  matchModel,
  matchNeeds,
  matchSay,
  okBase,
  pagesModel,
  pagesSay,
  signalModel,
  signalSay,
  state,
  topSay,
} from './insightdetail.js';
import { REC_DETAILS, drawRecs, recReq } from './insightrec.js';
import { placeList, say, sayLines, signalList } from './insightviz.js';
import { bar100s, bigNumber, tabs, topList } from './ui.js';
import { start } from './app.js';

const baseOf = (app) => partOf(app.entry(CLARITY));

function drawHead(app) {
  const part = baseOf(app);
  put('clarity-when', okBase(part) ? `마지막 받음 ${ago(part.fetchedAt)}${num(part.nextAt) !== null ? ` · 다음 ${hm(part.nextAt)}` : ''}` : '');
}

// 진단 줄(071 §3.6) — Clarity 가 0 일 때 왜 비었는지 한 줄
function drawDiag(app) {
  once('clarity-diag', [app.entry(CLARITY), app.entry(ATTN_TODAY)], () => {
    const box = $('clarity-diag');
    if (!box) return;
    const d = diagOf(baseOf(app), partOf(app.entry(ATTN_TODAY)));
    box.hidden = !d;
    box.replaceChildren(...(d ? [...(d.tone ? [el('i', `shape ${d.tone}`)] : []), el('span', null, d.text)] : []));
  });
}

// 큰 수 여섯 — 값은 최근 24시간 요약, 선은 날별 기록(점선 = 같은 길이의 앞 구간), 배지는 전날 대비
function drawKpis(app) {
  const part = baseOf(app);
  const h = historyWindow(app, app.view.range, app.view.cmp, app.now());
  for (const [id, , pick, fmt] of KPIS) {
    tag(`t-${id}`, part);
    const now = okBase(part) ? num(pick(part)) : null;
    const total = id === 'active' && okBase(part) ? num(part.summary?.totalSec) : null;
    bigNumber(`v-${id}`, fmt(now), total === null ? '' : `/ ${int(total)}초`);
    const [last, before] = h.ok ? lastTwo(h.rows, pick) : [null, null];
    put(`d-${id}`, deltaText(last, before, '전날'));
    say(`say-${id}`, kpiSay(id, part));
    once(`c-${id}`, [...h.deps, app.view.range, app.view.cmp], () => {
      if (!h.ok) return blank(`c-${id}`, stateWord(h.part));
      const line = h.line(pick);
      return line.data.length ? draw(`c-${id}`, sparkOption(line.data, 'acc', fmt, line.cmp)) : blank(`c-${id}`, '기록 쌓는 중');
    });
  }
}

// 불만 신호 여섯 — 줄마다 이름·등급·쉬운 풀이·방문 100칸(색칠 = 그 신호가 나온 방문)·전날, 위에 한 문장
function drawSignals(app) {
  const part = baseOf(app);
  const h = historyWindow(app, app.view.range, 'none', app.now());
  tag('t-signals', part);
  once('l-signals', [app.entry(CLARITY), ...h.deps], () => {
    const box = $('l-signals');
    if (!okBase(part)) {
      say('say-signals', '');
      return box.replaceChildren(el('p', 'empty', stateWord(part)));
    }
    const m = signalModel(part, h.ok ? h.rows : []);
    say('say-signals', signalSay(m));
    return signalList(box, m);
  });
}

function drawMatch(app) {
  const m = matchModel(app, app.view.range);
  tag('t-match', m.part);
  once('c-match', [...m.deps, app.view.range], () => {
    if (!m.ok) {
      say('say-match', '');
      return blank('c-match', stateWord(m.part));
    }
    say('say-match', matchSay(m.series));
    if (!m.series.some((s) => s.data.length)) return blank('c-match', '기록 쌓는 중');
    return draw('c-match', timeOption({ startMs: m.startMs, endMs: m.endMs, daily: true, panes: [{ fmt: int }], series: m.series }));
  });
}

// 페이지 × 기기 — 줄마다 방문 막대·페이지 모양 스크롤 깊이·머문/움직인 시간 띠·가장 잦은 신호(방문 3번 미만 줄은 흐리게), 위에 답 셋까지
function drawPages(app) {
  const base = baseOf(app);
  const pages = okBase(base) ? partOf({ body: base }, 'pages') : base;
  tag('t-pages', pages);
  once('l-pages', [app.entry(CLARITY)], () => {
    const box = $('l-pages');
    const rows = usable(pages, 'groups') ? pagesModel(pages) : [];
    sayLines('say-pages', pagesSay(rows));
    if (!usable(pages, 'groups')) return box.replaceChildren(el('p', 'empty', stateWord(pages)));
    if (!rows.length) return box.replaceChildren(el('p', 'empty', '자료 없음'));
    return placeList(box, rows, { n: 12, onPick: () => app.openDetail('pages'), more: () => app.openDetail('pages') });
  });
}

function drawCountries(app) {
  const part = baseOf(app);
  tag('t-countries', part);
  once('l-countries', [app.entry(CLARITY)], () => {
    const items = okBase(part) ? dimItems(part.countries) : [];
    say('say-countries', topSay(items, '가장 많은 나라는'));
    topList('l-countries', items, { n: 6, fmt: int, more: () => app.openDetail('countries'), empty: okBase(part) ? '자료 없음' : stateWord(part) });
  });
}
function drawDims(app) {
  const dims = dimsOf(baseOf(app));
  tag('t-dims', dims);
  once('b-dims', [app.entry(CLARITY)], () => {
    say('say-dims', usable(dims) ? dimsSay(dims) : '');
    if (!usable(dims)) return $('b-dims').replaceChildren(el('p', 'empty', stateWord(dims)));
    return bar100s('b-dims', [['device', '기기'], ['browser', '브라우저'], ['os', 'OS']].map(([k, name]) => ({ name, note: '세션', items: dimItems(dims[k]), fmt: int, onPick: () => app.openDetail('dims') })));
  });
}
function drawRefs(app) {
  const dims = dimsOf(baseOf(app));
  tag('t-refs', dims);
  tabs('tabs-refs', [['channel', 'Channel'], ['source', 'Source']], state.refTab, (k) => {
    state.refTab = k;
    app.paint();
  });
  once('l-refs', [app.entry(CLARITY), state.refTab], () => {
    const items = usable(dims) ? dimItems(dims[state.refTab]) : [];
    say('say-refs', topSay(items, state.refTab === 'source' ? '가장 많이 들어온 출처는' : '가장 많이 들어온 길은'));
    topList('l-refs', items, { n: 6, fmt: int, more: () => app.openDetail('refs'), empty: usable(dims) ? '자료 없음' : stateWord(dims) });
  });
}

start({
  id: 'clarity',
  home: '/clarity.html',
  ranges: HISTORY_RANGES,
  range: '30d',
  compare: historyCompare,
  needs: (app) => [CLARITY, ATTN_TODAY, ...historyReqs(app.view.range, app.view.cmp), ...matchNeeds(app.view.range), recReq()],
  cards: [
    ['head', drawHead],
    ['diag', drawDiag],
    ['kpis', drawKpis],
    ['recs', drawRecs],
    ['signals', drawSignals],
    ['match', drawMatch],
    ['pages', drawPages],
    ['countries', drawCountries],
    ['dims', drawDims],
    ['refs', drawRefs],
  ],
  details: { ...INSIGHT_DETAILS, ...REC_DETAILS },
});
