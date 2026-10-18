// Clarity 모델과 상세 판(스펙 066 §3.2·§3.3) — 040 요약 + 068 날별 기록·차원. 큰 수 여섯의 값 고르기, 맞대기(우리 방문자 038), 페이지 × 기기
// 칸 진하기, 차원 목록, 판(날별 선 + 같은 길이의 앞 구간 · 신호 여섯 · 표들). DOM 없음.
import { dec1, int, isObj, kstDay, kstText, list, n0, num, own, partOf, pct, req, usable } from './common.js';
import { SIGNALS, SIGNAL_PLAIN, clarityDevice, clarityPage } from './names.js';
import { HISTORY_RANGES, accessReqs, accessWindow, historyCompare, historyReqs, historyWindow } from './models.js';

export const CLARITY = req('clarity');
export const state = { refTab: 'channel' };
// 우리 방문자는 30일까지(038) — 90일 창의 맞대기도 30일 응답
export const accessRange = (range) => (range === '7d' ? '7d' : '30d');
const NO_CMP = () => ({ prev: false, week: false });

// 큰 수 여섯 — [id, 이름, 기록 행·요약에서 값 고르기, 글자]
export const KPIS = Object.freeze([
  ['sessions', '세션', (r) => r?.traffic?.sessions, int],
  ['botSessions', '봇 세션', (r) => r?.traffic?.botSessions, int],
  ['users', '사용자', (r) => r?.traffic?.users, int],
  ['pps', '페이지/세션', (r) => r?.traffic?.pagesPerSession, dec1],
  ['scroll', '평균 스크롤 깊이', (r) => r?.summary?.scrollDepth, (v) => pct(v, 0)],
  ['active', '활성 시간', (r) => r?.summary?.activeSec, (v) => (num(v) === null ? '–' : `${int(v)}초`)],
]);
// 마지막 두 기록(날)의 값 — '전날 대비'
export function lastTwo(rows, pick) {
  const vals = list(rows).map((r) => num(pick(r)));
  return [vals.at(-1) ?? null, vals.at(-2) ?? null];
}
// 요약의 값은 마지막 성공(040 — error 여도 값이 있으면 그린다)
export const okBase = (part) => usable(part, 'numOfDays');

// 안내 줄(071 §3.6) — Clarity 최근 24시간 세션이 0 일 때만. Clarity 요약은 받은 시각(fetchedAt) 앞 24시간의 한 장면이고(6시간마다,
// 이 페이지를 열 때만 다시 받는다), 화면 이용 통계(052, days=1 = 오늘 KST)는 시각 없는 날짜별 합이라 둘을 바로 견주지 못한다 —
// 어긋났다고 단정하지 않고 받은 시각과 두 수를 그대로 알린다. 어느 피드든 ok 가 아니면 줄을 숨긴다(null)
export const ATTN_TODAY = req('attention', { days: 1 });
export function diagOf(clarity, attn) {
  if (clarity?.state !== 'ok' || clarity.why || attn?.state !== 'ok' || attn.why) return null;
  const sessions = num(clarity.traffic?.sessions);
  if (sessions === null || sessions > 0) return null;
  const pv = list(attn.rows).reduce((sum, r) => sum + n0(r?.pv), 0);
  const when = kstText(clarity.fetchedAt);
  if (pv > 0) {
    return {
      tone: null,
      text: `Clarity 가 ${when} 에 받은 최근 24시간 세션은 0 이다 — 그 뒤의 방문은 다음 받기(6시간마다, 이 페이지를 열 때)에 들어간다. 같은 동의로 도는 화면 이용 통계는 오늘 페이지 보기 ${int(pv)}회(시각 없는 날짜별 합이라 Clarity 의 24시간과 바로 견주지 못한다). 지난 기록은 Clarity 열기에서 본다.`,
    };
  }
  return { tone: null, text: `Clarity 가 ${when} 에 받은 최근 24시간 세션 0 · 오늘 화면 이용 통계 0 — 동의한 방문이 없어 비는 게 정상이다. 지난 기록은 Clarity 열기에서 본다.` };
}

// 맞대기 — Clarity 세션(동의한 방문자만) vs 우리 방문자(서버 기록, 날마다 셈). 둘의 비 = 동의 비율의 어림
export function matchModel(app, range) {
  const h = historyWindow(app, range, 'none', app.now());
  const a = accessWindow(app, accessRange(range), 'none');
  const out = { part: h.part, deps: [...h.deps, ...a.deps], ok: h.ok };
  if (!h.ok) return out;
  out.startMs = h.startMs;
  out.endMs = h.endMs;
  // 우리 방문자 날 점을 Clarity 날 점(그날 00:00 KST)과 같은 x 에 — 한 툴팁에 같은 날 둘이 나란히
  const clip = (pts) => pts.map((p) => [kstDay(Math.floor(p[0] / 1000)) * 1000, p[1]]).filter((p) => p[0] >= h.startMs && p[0] < h.endMs);
  out.series = [
    { name: 'Clarity 세션', mark: 'acc', data: h.line((r) => r?.traffic?.sessions).data },
    { name: '확인된 방문자', mark: 'c1', data: a.ok ? clip(a.days('confirmed').data) : [] },
    { name: '브라우저 모양 방문자', mark: 'c3', data: a.ok ? clip(a.days('shaped').data) : [] },
  ];
  return out;
}
export const matchNeeds = (range) => [...historyReqs(range, 'none'), ...accessReqs(accessRange(range), 'none')];

// 페이지 × 기기(040 pages.groups) — 열: 세션 · 스크롤 · 활성 초 · 신호 여섯. 칸 진하기 = 그 열 최댓값 대비 다섯 단계
export const PAGE_COLS = Object.freeze([
  ['sessions', '세션', int],
  ['scrollDepth', '스크롤', (v) => pct(v, 0)],
  ['activeSec', '활성 초', int],
  ...SIGNALS.map(([key, name]) => [`${key}Pct`, name, (v) => pct(v, 1)]),
]);
export const shade = (v, max) => (num(v) === null || !(max > 0) || v <= 0 ? '' : `q${Math.min(5, Math.max(1, Math.ceil((v / max) * 5)))}`);
// 세션 3 미만 칸은 흐리게(표본 적음)
export const groupRows = (part) =>
  list(part?.groups)
    .filter(isObj)
    .map((g) => ({ ...g, page: clarityPage(g.page), device: clarityDevice(g.device), thin: n0(g.sessions) < 3 }));
// 차원 목록(068 dims · 040 countries) [[이름, 세션]] — 색은 순서대로(이름이 Clarity 의 글자라 표가 없다)
export const dimItems = (rows) =>
  list(rows)
    .filter((r) => Array.isArray(r) && typeof r[0] === 'string')
    .map(([name, n], i) => ({ key: name, name, value: n0(n), mark: `k${Math.min(8, i + 1)}` }));
export const dimsOf = (part) => (okBase(part) ? partOf({ body: part }, 'dims') : part);

// --- 쉽게 읽기(066 §3.3) — 불만 신호 100칸·페이지 × 기기 줄·카드마다 데이터로 만든 한 문장 ----------------------------
// 불만 신호 문턱 [주의, 나쁨] — 방문 100번 중 몇 번(신호가 있던 세션 %)부터인가. Clarity 가 기준을 주지 않아 어림으로 정했다
export const SIGNAL_LEVELS = Object.freeze({ deadClick: [10, 20], rageClick: [2, 5], excessiveScroll: [5, 10], quickback: [10, 20], scriptError: [2, 5], errorClick: [1, 3] });
export const LEVEL_WORD = Object.freeze({ ok: '좋음', warn: '주의', bad: '나쁨' });
const RANK = Object.freeze({ bad: 2, warn: 1, ok: 0 });
export function levelOf(key, v) {
  const at = own(SIGNAL_LEVELS, key);
  if (!at || num(v) === null) return null;
  return v >= at[1] ? 'bad' : v >= at[0] ? 'warn' : 'ok';
}
// 더 나쁜 신호가 앞 — 등급, 같으면 주의 문턱에 견준 크기
const worse = (a, b) => RANK[b.level] - RANK[a.level] || b.value / own(SIGNAL_LEVELS, b.key)[0] - a.value / own(SIGNAL_LEVELS, a.key)[0];
// 방문 100번 중 몇 번 — 반올림, 0 보다 크고 0.5 아래는 '1번 미만'
export const per100 = (v) => (num(v) === null ? '–' : v <= 0 ? '0번' : v < 0.5 ? '1번 미만' : `${Math.round(Math.min(100, v))}번`);
// 머문 시간 — 45초 · 3분 32초 · 1시간 5분
export function dur(sec) {
  if (num(sec) === null) return '–';
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}초`;
  if (s < 3600) return s % 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s / 60}분`;
  return `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분`;
}
// 스크롤 깊이(%)를 말로 — 평균으로 어디서 멈추는가
export const depthWord = (v) =>
  num(v) === null ? '' : v < 25 ? '첫 화면 근처에서 멈춘다' : v < 45 ? '위쪽 절반 안에서 멈춘다' : v < 65 ? '페이지 가운데쯤에서 멈춘다' : v < 90 ? '아래쪽까지 대부분 본다' : '끝까지 본다';
// 머문 시간 중 움직인 몫(%) — 둘 다 있고 머문 시간 > 0 일 때만
const activeShare = (active, total) => (num(active) !== null && num(total) > 0 ? Math.min(100, (active / total) * 100) : null);
const THIN_SESSIONS = 20; // 최근 24시간 세션이 이보다 적으면 신호 비율은 한두 번에 크게 흔들린다

// 불만 신호 여섯 — 값 = 신호가 있던 세션 %(최근 24시간 요약), 칸 = 100칸 중 색칠할 수, 전날 = 기록의 마지막 두 날.
// 최근 24시간 세션이 0 이면 0% 는 '좋음' 이 아니라 셀 것이 없는 것 — 등급을 비운다
export function signalModel(part, rows) {
  const signals = isObj(part?.summary?.signals) ? part.summary.signals : {};
  const sessions = num(part?.traffic?.sessions);
  const items = SIGNALS.map(([key, name]) => {
    const value = num(signals[key]?.sessionPct);
    const [last, before] = lastTwo(rows, (r) => r?.summary?.signals?.[key]?.sessionPct);
    const diff = last === null || before === null ? null : last - before;
    const trend = diff === null ? null : Math.abs(diff) < 0.5 ? 'same' : diff > 0 ? 'up' : 'down';
    const cells = value === null ? 0 : Math.round(Math.min(100, Math.max(0, value)));
    const level = sessions === 0 ? null : levelOf(key, value);
    return { key, name, plain: SIGNAL_PLAIN[key], value, level, at: SIGNAL_LEVELS[key], cells, part: cells === 0 && value > 0, before, trend };
  });
  return { items, sessions, thin: sessions !== null && sessions < THIN_SESSIONS };
}
const TREND_WORD = Object.freeze({ up: '전날보다 늘었다', down: '전날보다 줄었다' });
const TREND_MARK = Object.freeze({ up: '▲ 늘었다', down: '▼ 줄었다', same: '– 비슷' });
export const trendText = (it) => [it.trend === null ? '' : `전날 ${per100(it.before)} · ${TREND_MARK[it.trend]}`, `${it.at[0]}번부터 주의 · ${it.at[1]}번부터 나쁨`].filter(Boolean).join(' · ');
export function signalSay(m) {
  if (m.sessions === 0) return '최근 24시간에 동의한 방문이 없어 셀 신호가 없다.';
  const seen = m.items.filter((it) => it.level !== null);
  if (!seen.length) return '';
  const top = [...seen].sort(worse)[0];
  const thin = m.thin ? ` 다만 최근 24시간 방문이 ${int(m.sessions)}번뿐이라 한두 번에도 크게 흔들린다.` : '';
  if (top.level === 'ok') {
    const most = [...seen].sort((x, y) => y.value - x.value)[0];
    return `모두 좋음 — 가장 잦은 ${most.name}도 방문 100번 중 ${per100(most.value)}이다.${thin}`;
  }
  const trend = top.trend === 'up' || top.trend === 'down' ? `, ${TREND_WORD[top.trend]}` : '';
  return `가장 눈에 띄는 신호는 ${top.name}(${LEVEL_WORD[top.level]}) — 방문 100번 중 ${per100(top.value)}${trend}.${thin}`;
}

// 큰 수 타일 한 줄 — 스크롤 깊이·활성 시간을 말로
export function kpiSay(id, part) {
  if (!okBase(part)) return '';
  const s = part.summary ?? {};
  if (id === 'scroll') return num(s.scrollDepth) === null ? '' : `평균으로 보면 ${depthWord(s.scrollDepth)}`;
  if (id !== 'active') return '';
  const share = activeShare(s.activeSec, s.totalSec);
  return share === null ? '' : `머문 ${dur(s.totalSec)} 중 ${dur(s.activeSec)}(${pct(share, 0)}) 동안 스크롤·클릭·입력을 했다`;
}

// 페이지 × 기기 줄 — 방문 수 내림차순. 막대 몫: 방문 = 가장 많은 줄 대비, 시간 띠 = 가장 긴 머문 시간 대비, 그 안의 움직인 몫
const placeOf = (g) => (g.device === 'other' ? `${clarityPage(g.page)}(그 밖 기기)` : `${clarityDevice(g.device)} ${clarityPage(g.page)}`);
export function pagesModel(part) {
  const groups = list(part?.groups).filter(isObj);
  const rows = groupRows(part)
    .map((r, i) => {
      const worst = SIGNALS.map(([key, name]) => ({ key, name, value: num(r[`${key}Pct`]), level: levelOf(key, r[`${key}Pct`]) }))
        .filter((s) => s.value > 0)
        .sort(worse)[0];
      return { ...r, where: placeOf(groups[i]), at: i, worst: worst ?? null, share: activeShare(r.activeSec, r.totalSec) };
    })
    .sort((a, b) => n0(b.sessions) - n0(a.sessions) || a.at - b.at);
  const maxS = Math.max(0, ...rows.map((r) => n0(r.sessions)));
  const maxT = Math.max(0, ...rows.map((r) => Math.max(n0(r.totalSec), n0(r.activeSec))));
  for (const r of rows) {
    r.sessShare = maxS ? n0(r.sessions) / maxS : 0;
    r.timeShare = maxT ? Math.max(n0(r.totalSec), n0(r.activeSec)) / maxT : 0;
    r.lit = num(r.scrollDepth) === null ? 0 : Math.round(Math.min(100, Math.max(0, r.scrollDepth)) / 10);
  }
  return rows;
}
// 페이지 × 기기 답 — 많이 오는 곳의 스크롤, 눈에 띄는 신호(주의·나쁨), 가장 오래 쓰는 곳, 가장 덜 내려보는 곳 순으로 골라 셋까지
// (방문 3번 미만 줄은 빼고), 보이는 차례는 스크롤 → 덜 내려봄 → 시간 → 신호
export function pagesSay(rows) {
  const solid = rows.filter((r) => !r.thin);
  if (!solid.length) return rows.length ? ['모든 줄이 방문 3번 미만이라 아직 읽기 이르다 — 며칠 더 쌓인 뒤 본다.'] : [];
  const top = solid[0];
  const picks = [];
  if (num(top.scrollDepth) !== null) picks.push([0, `방문이 가장 많은 ${top.where}(${int(top.sessions)}번)은 평균 ${pct(top.scrollDepth, 0)} 까지 내려본다 — ${depthWord(top.scrollDepth)}.`]);
  const sore = solid.filter((r) => r.worst && r.worst.level !== 'ok').sort((a, b) => worse(a.worst, b.worst))[0];
  if (sore) picks.push([3, `불만 신호가 가장 눈에 띄는 곳은 ${sore.where}의 ${sore.worst.name} — 방문 100번 중 ${per100(sore.worst.value)}.`]);
  const busy = solid.filter((r) => num(r.activeSec) !== null).sort((a, b) => b.activeSec - a.activeSec)[0];
  if (busy) picks.push([2, `스크롤·클릭·입력을 가장 오래 한 곳은 ${busy.where} — ${busy.share === null ? `평균 ${dur(busy.activeSec)}` : `머문 ${dur(busy.totalSec)} 중 ${dur(busy.activeSec)}`}.`]);
  const shallow = solid.filter((r) => r !== top && num(r.scrollDepth) !== null && r.scrollDepth < 45).sort((a, b) => a.scrollDepth - b.scrollDepth)[0];
  if (shallow) picks.push([1, `가장 덜 내려보는 곳은 ${shallow.where}(평균 ${pct(shallow.scrollDepth, 0)}) — 그 아래 내용은 대개 보지 않는다.`]);
  return picks.slice(0, 3).sort((a, b) => a[0] - b[0]).map(([, text]) => text);
}

// 맞대기 답 — 둘 다 있는 날만 더해 견준다(세는 법이 달라 어림)
export function matchSay(series) {
  const seen = new Map(list(series?.[1]?.data).filter((p) => num(p[1]) !== null).map((p) => [p[0], p[1]]));
  let days = 0;
  let ours = 0;
  let theirs = 0;
  for (const [x, v] of list(series?.[0]?.data)) {
    if (num(v) === null || !seen.has(x)) continue;
    days += 1;
    theirs += v;
    ours += seen.get(x);
  }
  if (!days || !ours) return '';
  const head = `둘 다 있는 ${int(days)}일 동안 Clarity 세션 ${int(theirs)} · 확인된 방문자 ${int(ours)}`;
  if (theirs > ours) return `${head} — 세션이 더 많다. 한 사람이 여러 번 오면 세션은 여러 개로 센다.`;
  return `${head} — 방문자 100명 중 약 ${int((theirs / ours) * 100)}명꼴로 화면 분석에 동의한 셈이다(세는 법이 달라 어림).`;
}
// 목록 답 — 가장 큰 항목과 그 몫. 항목이 없으면 빈 글
export function topSay(items, head) {
  const total = items.reduce((s, it) => s + it.value, 0);
  const top = [...items].sort((a, b) => b.value - a.value)[0];
  if (!top || !total) return '';
  return `${head} ${top.name} — ${top.value === total ? '세션 전부' : `세션의 ${pct((top.value / total) * 100, 0)}`}.`;
}
export function dimsSay(dims) {
  const parts = [['device', '기기는'], ['browser', '브라우저는'], ['os', 'OS 는']]
    .map(([k, label]) => {
      const items = dimItems(dims?.[k]);
      const total = items.reduce((s, it) => s + it.value, 0);
      const top = [...items].sort((a, b) => b.value - a.value)[0];
      return top && total ? `${label} ${top.name}(${pct((top.value / total) * 100, 0)})` : null;
    })
    .filter(Boolean);
  return parts.length ? `가장 많은 ${parts.join(' · ')}.` : '';
}
const shareRows = (items) => {
  const total = items.reduce((s, it) => s + it.value, 0);
  return items.map((it) => ({ ...it, share: total ? (it.value / total) * 100 : null }));
};
const LIST_COLS = (label) => [{ key: 'name', label }, { key: 'value', label: '세션', num: true, fmt: int }, { key: 'share', label: '비율', num: true, fmt: (v) => pct(v, 1) }];

// --- 판 -----------------------------------------------------------------------------------------

function historyDetail(title, unit, build, compare = historyCompare) {
  return {
    title,
    unit,
    ranges: HISTORY_RANGES,
    range: (r) => (HISTORY_RANGES.includes(r) ? r : '30d'),
    compare,
    needs: (app, pv) => historyReqs(pv.range, pv.cmp),
    model: (app, pv) => {
      const h = historyWindow(app, pv.range, pv.cmp, app.now());
      const base = { part: h.part, deps: h.deps, note: h.ok && h.firstMs !== null ? `기록은 ${kstText(h.firstMs).slice(5, 10)} 부터 모음 · 그날 마지막으로 받은 직전 24시간 값` : '' };
      return h.ok ? { ...base, span: [h.startMs, h.endMs], time: { startMs: h.startMs, endMs: h.endMs, daily: true, ...build(h) } } : base;
    },
  };
}
function baseDetail(title, unit, build) {
  return {
    title,
    unit,
    compare: NO_CMP,
    needs: () => [CLARITY],
    model: (app) => {
      const entry = app.entry(CLARITY);
      const part = partOf(entry);
      return { deps: [entry, state.refTab], ...build(part, app) };
    },
  };
}

export const INSIGHT_DETAILS = Object.freeze({
  ...Object.fromEntries(
    KPIS.map(([id, name, pick, fmt]) => [
      id,
      historyDetail(name, '날별 · 점선은 같은 길이의 앞 구간', (h) =>
        id === 'active'
          ? { panes: [{ fmt }], series: [{ name: '활성 시간', mark: 'acc', ...h.line(pick) }, { name: '전체 시간', mark: 'c1', ...h.line((r) => r?.summary?.totalSec) }] }
          : { panes: [{ fmt }], series: [{ name, mark: 'acc', ...h.line(pick) }] },
      ),
    ]),
  ),
  signals: historyDetail('불만 신호', '날별 · 신호가 있던 세션 %', (h) => ({
    panes: [{ fmt: (v) => pct(v, 1) }],
    series: SIGNALS.map(([key, name], i) => ({ name, mark: `c${i + 1}`, ...h.line((r) => r?.summary?.signals?.[key]?.sessionPct), cmp: null })),
  }), NO_CMP),
  match: {
    title: '맞대기',
    unit: 'Clarity 세션 vs 우리 방문자 · 날별',
    ranges: HISTORY_RANGES,
    range: (r) => (HISTORY_RANGES.includes(r) ? r : '30d'),
    compare: NO_CMP,
    needs: (app, pv) => matchNeeds(pv.range),
    model: (app, pv) => {
      const m = matchModel(app, pv.range);
      const base = { part: m.part, deps: m.deps, note: '우리 방문자는 최근 30일까지(서버 기록)' };
      return m.ok ? { ...base, span: [m.startMs, m.endMs], time: { startMs: m.startMs, endMs: m.endMs, daily: true, panes: [{ fmt: int }], series: m.series } } : base;
    },
  },
  pages: baseDetail('페이지 × 기기', '최근 72시간 · 세션 가중 평균', (part) => {
    const pages = okBase(part) ? partOf({ body: part }, 'pages') : part;
    if (!usable(pages, 'groups')) return { part: pages };
    const columns = [{ key: 'page', label: '페이지' }, { key: 'device', label: '기기' }, ...PAGE_COLS.map(([key, label, fmt]) => ({ key, label, num: true, fmt }))];
    return { part: pages, table: { title: '칸마다', columns, rows: groupRows(pages), sort: { key: 'sessions', dir: 'desc' }, search: ['page', 'device'] } };
  }),
  countries: baseDetail('나라', '세션 · 최근 24시간', (part) =>
    okBase(part) ? { part, table: { title: '나라', columns: LIST_COLS('나라'), rows: shareRows(dimItems(part.countries)), sort: { key: 'value', dir: 'desc' }, search: ['name'] } } : { part },
  ),
  dims: baseDetail('기기 · 브라우저 · OS', '세션 · 하루 한 번 받음', (part) => {
    const dims = dimsOf(part);
    if (!usable(dims)) return { part: dims };
    const rows = [['device', '기기'], ['browser', '브라우저'], ['os', 'OS']].flatMap(([k, group]) => shareRows(dimItems(dims[k])).map((r) => ({ ...r, group })));
    return { part: dims, table: { title: '차원', columns: [{ key: 'group', label: '차원' }, ...LIST_COLS('이름')], rows, sort: { key: 'value', dir: 'desc' }, search: ['group', 'name'] } };
  }),
  refs: baseDetail('유입', 'Channel · Source · 세션', (part) => {
    const dims = dimsOf(part);
    if (!usable(dims)) return { part: dims };
    const rows = [['channel', 'Channel'], ['source', 'Source']].flatMap(([k, group]) => shareRows(dimItems(dims[k])).map((r) => ({ ...r, group })));
    return { part: dims, table: { title: '유입', columns: [{ key: 'group', label: '차원' }, ...LIST_COLS('이름')], rows, sort: { key: 'value', dir: 'desc' }, search: ['group', 'name'] } };
  }),
});
