// 화면 재생의 값 계산(스펙 074 §3.3·§3.5) — 073 피드의 묶음을 서버 시계에 맞춘 사건으로 펴고, 빈 시간 건너뛰기 시계·한 시각의 틀 상태
// (틀로 보낼 메시지)·시간 막대 표시·사건 목록·카드 행을 만든다. DOM 없음(node 시험이 바로 부른다).
// 사건(073 §3.2): [t,"m",x,y] · [t,"c",x,y,영역] · [t,"s",y] · [t,"e",영역,n,top] · [t,"r",w,h] · [t,"v",0|1] · [t,"p",화면] · [t,"a",영역].
import { isObj, list, num, own, req } from './common.js';
import { SCREEN_PAGES, isDashboard, validArea } from './screensmodel.js';

export const IDLE_GAP_MS = 5_000; // 빈 시간 건너뛰기 — 사건 사이 이보다 긴 틈을
export const IDLE_PLAY_MS = 1_000; // 이 길이로 재생한다
export const SPEEDS = Object.freeze([1, 2, 4, 8]);
export const SEEK_MS = 5_000; // ←/→ 한 번
export const FOLLOW_POLL_MS = 2_000; // 따라 보기 — 새 묶음 받기
export const FOLLOW_BEHIND_MS = 3_000; // 마지막 사건에서 이만큼 뒤를 따라간다
export const FOLLOW_JUMP_MS = 8_000; // 그보다 이만큼 더 뒤처지면(처음·숨었다 돌아옴) 건너뛴다
export const FOLLOW_STOP_MS = 30_000; // 이 동안 새 묶음이 없으면 받기를 멈춘다
export const TOUCH_SHOW_MS = 1_000; // 손가락 표시는 마지막 움직임 뒤 이만큼만(손을 뗀 뒤 남은 원은 거짓이다)
export const BIG_SCROLL_PX = 300; // 사건 목록의 '스크롤 크게' — 한 번 굴림의 이동이 이 이상
const SCROLL_RUN_MS = 1_000; // 스크롤 사건 사이가 이 안이면 한 번 굴림
const MAX_COORD = 20_000;
const MAX_SCROLL = 1_000_000;
const MAX_T = 7_200_000;
const MAX_ELS = 40; // 틀로 보내는 영역 안 스크롤 짝 상한
const CUT_BYTES = 1_048_576 - 16_384; // 조각 1MB 상한에서 묶음 하나(16KB)가 더 들어가지 못한 크기
const CUT_BATCHES = 3_600;
const DEFAULT_VIEW = Object.freeze({ pc: [1280, 800], mobile: [390, 844] });
const STATIC_PATH = Object.freeze({ landing: '/', privacy: '/privacy', 'kimp-chart': '/kimp-chart', 'kimp-history': '/kimp-history' });
const SID = /^[0-9a-f]{16}$/;

export const validSid = (sid) => typeof sid === 'string' && SID.test(sid);
export const liveReq = () => req('replayLive');
export const sessionsReq = (ip) => req('replaySessions', ip ? { ip } : {});
export const sessionReq = (sid, after) => req('replaySession', { sid, after });

// 틀 주소의 경로(사이트 출처는 판 모듈이 붙인다) — 정적 화면은 그 경로, 대시보드는 /app/?tab=<탭>. 모르는 화면은 null(띄우지 않음)
export function framePath(page) {
  if (!SCREEN_PAGES.includes(page)) return null;
  return isDashboard(page) ? `/app/?tab=${page.slice(4)}&kt-replay=1` : `${own(STATIC_PATH, page)}?kt-replay=1`;
}

// --- 사건 읽기 · 시계 맞추기 -----------------------------------------------------------------------

const whole = (v, lo, hi) => (num(v) === null ? null : Math.min(hi, Math.max(lo, Math.round(v))));
const text = (v) => (typeof v === 'string' ? v.slice(0, 64) : '');

// 사건 하나 → { t, k, … } 또는 null(꼴이 틀림 — 버린다). 영역·화면 글자는 표시용으로 남기고, 틀로 보낼 때 꼴을 다시 본다
function parse(e) {
  if (!Array.isArray(e)) return null;
  const t = whole(e[0], 0, MAX_T);
  if (t === null) return null;
  const xy = () => [whole(e[2], 0, MAX_COORD), whole(e[3], 0, MAX_COORD)];
  switch (e[1]) {
    case 'm':
    case 'c': {
      const [x, y] = xy();
      if (x === null || y === null) return null;
      return e[1] === 'm' ? { t, k: 'm', x, y } : { t, k: 'c', x, y, area: text(e[4]) };
    }
    case 's': {
      const y = whole(e[2], 0, MAX_SCROLL);
      return y === null ? null : { t, k: 's', y };
    }
    case 'e': {
      const n = whole(e[3], 0, 999);
      const top = whole(e[4], 0, MAX_SCROLL);
      return !text(e[2]) || n === null || top === null ? null : { t, k: 'e', area: text(e[2]), n, top };
    }
    case 'r': {
      const [w, h] = [whole(e[2], 1, MAX_COORD), whole(e[3], 1, MAX_COORD)];
      return w === null || h === null ? null : { t, k: 'r', w, h };
    }
    case 'v':
      return e[2] === 0 || e[2] === 1 ? { t, k: 'v', on: e[2] === 1 } : null;
    case 'p':
      return text(e[2]) ? { t, k: 'p', page: text(e[2]) } : null;
    case 'a':
      return { t, k: 'a', area: text(e[2]) };
    default:
      return null;
  }
}

// 묶음들 → 서버 시계의 사건(§3.3): 사건 시각 = 묶음 at − 그 묶음 마지막 t + 사건 t. 묶음 사이 방문자 시계 어긋남을 없앤다.
// 시각 순(같으면 받은 차례)
export function alignEvents(batches) {
  const out = [];
  let order = 0;
  for (const b of list(batches)) {
    if (!isObj(b) || num(b.at) === null) continue;
    const evs = list(b.ev).map(parse).filter(Boolean);
    if (!evs.length) continue;
    const base = b.at - Math.max(...evs.map((e) => e.t));
    for (const e of evs) out.push({ ...e, at: base + e.t, o: order++ });
  }
  return out.sort((a, b) => a.at - b.at || a.o - b.o);
}

// 시각 순 배열에서 at ≤ T 인 마지막 것의 자리(없으면 -1)와 그것(없으면 null)
function lastIndex(arr, T) {
  let lo = 0;
  let hi = arr.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid].at <= T) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}
const lastAt = (arr, T) => arr[lastIndex(arr, T)] ?? null;

const viewOf = (meta, dev) => {
  const w = whole(meta?.w, 1, MAX_COORD);
  const h = whole(meta?.h, 1, MAX_COORD);
  return w && h ? [w, h] : DEFAULT_VIEW[dev];
};

// 조각 하나의 재생 자료 — 사건, 시작·끝, 화면 조각(p 로 나뉜 구간 — 영역 안 스크롤·보던 영역은 그 구간 안에서만), 종류별 배열,
// 숨김 구간, 빈 시간 틈. 화면 크기는 meta.w·h(있으면), 없으면 기기 기본값 — r 사건이 오면 그 값
export function timeline(meta, batches) {
  const events = alignEvents(batches);
  const dev = meta?.dev === 'mobile' ? 'mobile' : 'pc';
  const start = events.length ? events[0].at : (num(meta?.first) ?? 0);
  const end = events.length ? events[events.length - 1].at : start;
  const firstP = events.find((e) => e.k === 'p');
  const chunks = [{ from: -Infinity, page: text(meta?.page) || firstP?.page || '', els: new Map() }];
  for (const e of events) {
    if (e.k === 'p' && e.page !== chunks[chunks.length - 1].page) chunks.push({ from: e.at, page: e.page, els: new Map() });
  }
  const kind = (...ks) => events.filter((e) => ks.includes(e.k));
  const hides = [];
  let off = null;
  for (const e of kind('v')) {
    if (!e.on && off === null) off = e.at;
    else if (e.on && off !== null) {
      hides.push([off, e.at]);
      off = null;
    }
  }
  if (off !== null) hides.push([off, end]);
  const tl = {
    meta,
    dev,
    touch: dev === 'mobile',
    start,
    end,
    events,
    chunks,
    base: viewOf(meta, dev),
    pointer: kind('m', 'c'),
    clicks: kind('c'),
    scrolls: kind('s'),
    views: kind('r'),
    vis: kind('v'),
    looks: kind('a'),
    hides,
    gaps: [],
  };
  for (const e of kind('e')) {
    if (!validArea(e.area)) continue;
    const els = chunkOf(tl, e.at).els;
    const key = `${e.area}|${e.n}`;
    if (!els.has(key) && els.size >= MAX_ELS) continue;
    if (!els.has(key)) els.set(key, []);
    els.get(key).push(e);
  }
  for (let i = 1; i < events.length; i++) {
    if (events[i].at - events[i - 1].at > IDLE_GAP_MS) tl.gaps.push({ a: events[i - 1].at, b: events[i].at });
  }
  return tl;
}

function chunkOf(tl, T) {
  let lo = 0;
  let hi = tl.chunks.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (tl.chunks[mid].from <= T) lo = mid;
    else hi = mid - 1;
  }
  return tl.chunks[lo];
}
export const pageAt = (tl, T) => chunkOf(tl, T).page;
export const viewAt = (tl, T) => {
  const r = lastAt(tl.views, T);
  return r ? [r.w, r.h] : tl.base;
};

// --- 한 시각의 틀 상태(§3.4 메시지) ----------------------------------------------------------------
// clickFrom = 재생으로 넘어온 앞 시각(되감기·건너뛰기는 null) — (clickFrom, T] 안의 누름이 있으면 그 자리에 click 참(한 번만).
// 커서는 숨김 구간·첫 포인터 전에는 null, 손가락 기기는 마지막 움직임 뒤 1초까지만. 스크롤은 마지막 s(없으면 0 — 막 연 페이지),
// 영역 안 스크롤은 지금 화면 조각에서 본 (영역, n) 마다 마지막 top(그 전이면 0), 보던 영역은 지금 화면 조각의 마지막 a.
export function frameState(tl, T, clickFrom = null) {
  const chunk = chunkOf(tl, T);
  const v = lastAt(tl.vis, T);
  const hidden = Boolean(v && !v.on);
  let p = lastAt(tl.pointer, T);
  let click = false;
  if (clickFrom !== null && clickFrom < T) {
    const c = lastAt(tl.clicks, T);
    if (c && c.at > clickFrom) [p, click] = [c, true];
  }
  const show = Boolean(p) && !hidden && (click || !tl.touch || T - p.at <= TOUCH_SHOW_MS);
  const s = lastAt(tl.scrolls, T);
  const els = [...chunk.els.values()].map((arr) => [arr[0].area, arr[0].n, lastAt(arr, T)?.top ?? 0]);
  const a = lastAt(tl.looks, T);
  const area = a && a.at >= chunk.from && validArea(a.area) ? a.area : '';
  return {
    page: chunk.page,
    view: viewAt(tl, T),
    hidden,
    message: { type: 'kt-replay', v: 1, x: show ? p.x : null, y: show ? p.y : null, touch: tl.touch, click: show && click, sy: s ? s.y : 0, els, area },
  };
}

// --- 재생 시계 ------------------------------------------------------------------------------------
// 재생 시간 playMs 만큼 나아간 사건 시각. 빈 시간 건너뛰기가 켜져 있으면 5초 넘는 틈은 1초에 지나간다(끝에서 멈춘다)
export function advance(tl, T, playMs, skip) {
  let t = Math.max(tl.start, T);
  let left = Math.max(0, playMs);
  if (!skip) return Math.min(tl.end, t + left);
  let i = tl.gaps.findIndex((g) => g.b > t);
  while (left > 0 && t < tl.end && i >= 0 && i < tl.gaps.length) {
    const g = tl.gaps[i];
    if (t < g.a) {
      const step = Math.min(left, g.a - t);
      t += step;
      left -= step;
      continue;
    }
    const rate = (g.b - g.a) / IDLE_PLAY_MS; // 틈 안에서는 재생 1ms 에 사건 시각 rate ms
    const need = (g.b - t) / rate;
    if (left < need) {
      t += left * rate;
      left = 0;
    } else {
      t = g.b;
      left -= need;
      i += 1;
    }
  }
  return Math.min(tl.end, t + left);
}

// 따라 보기(§3.3) — 새 묶음 뒤 재생 시각: 마지막 사건 − 3초보다 8초 넘게 뒤처졌으면(처음·숨었다 돌아옴) 그리로, 아니면 그대로
export function followTarget(tl, T) {
  const aim = Math.max(tl.start, tl.end - FOLLOW_BEHIND_MS);
  return T === null || T < aim - FOLLOW_JUMP_MS ? aim : T;
}
// 늦음(초) — 방문자 시각(마지막 묶음을 서버가 받은 시각 + 그 뒤 흐른 시간) − 재생 시각
export const lagSec = (latestAt, sinceMs, T) => Math.max(0, Math.round((latestAt + Math.max(0, sinceMs) - T) / 1000));

// 묶음 1MB·3,600개 상한에 닿아 끝이 잘린 조각(§3.5 '앞부분만') — 피드가 truncated 를 주면 그것, 아니면 묶음 수·바이트로 어림
export const truncated = (meta) => meta?.truncated === true || (num(meta?.n) ?? 0) >= CUT_BATCHES || (num(meta?.bytes) ?? 0) >= CUT_BYTES;
