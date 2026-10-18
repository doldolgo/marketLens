// 화면 재생 판(스펙 074 §3.3·§3.5) — 방문자 페이지의 상세 판 `#detail=replay:<sid>`. 왼쪽 틀(replayframe.js)·시간 막대·조절 줄, 오른쪽
// 사건 목록(replayview.js). 재생 시각이 바뀔 때마다 그 시각의 상태를 메시지 하나로 틀에 보낸다(틀은 받은 상태만 그린다).
// 조각은 073 session 을 next 로 끝까지 받고(14일이 지나 S3 로 옮긴 조각이면 — session 의 archived — 078 archive 로 처음부터 다시),
// 따라 보기(지금 보는 중에서 연 판)는 2초마다 after=next 로 새 묶음을 받아 마지막 사건 3초 뒤를
// 1× 로 따라간다. 이 부르기는 store 의 묶음(10초·60초)과 따로다 — 쪽 넘기기·2초 받기는 이 판만의 주기라 call() 을 직접 쓴다(만료 신호는 app 으로).
import { call, el, isExpired, isObj, kstText, list, num, tagText } from './common.js';
import {
  FOLLOW_POLL_MS, FOLLOW_STOP_MS, SEEK_MS,
  advance, archiveReq, barMarks, consentText, devName, eventRows, followTarget, frameState, lagSec, sessionReq, timeText, timeline, truncated, validSid,
} from './replaymodel.js';
import { createFrame } from './replayframe.js';
import { createView } from './replayview.js';

const TICK_MS = 50; // 재생 중 화면 고침 간격
const PAGES_MAX = 20; // 쪽 넘기기 상한(묶음 3,600 ÷ 600 = 6 쪽 — 넉넉히)
const NO_FEED = Object.freeze({ state: 'unconfigured', code: null });
const OK = Object.freeze({ state: 'ok' });
const visible = () => document.visibilityState === 'visible';

let player = null; // 지금 연 재생 판 하나
let followNext = null; // 다음에 여는 판을 따라 보기로(지금 보는 중의 행이 정한다)

// 판 열기 — live 면 따라 보기
export function openReplay(app, sid, live) {
  followNext = live ? sid : null;
  app.openDetail(`replay:${sid}`);
  followNext = null;
}

// page.details 의 'replay' — 판이 바뀌거나 닫히면 leave()(틀을 비우고 주기를 멈춘다)
export const replayDetail = {
  title: '화면 재생',
  accepts: validSid,
  needs: () => [],
  leave() {
    player?.stop();
    player = null;
  },
  model(app, pv, sid) {
    if (player?.sid !== sid) {
      player?.stop();
      player = createPlayer(app, sid, followNext === sid);
    }
    return player.model();
  },
};

// 틀 메시지·창 크기·숨김·키보드(Space 재생/멈춤, ←/→ 5초) — 판이 열려 있을 때만
export function wireReplay() {
  window.addEventListener('message', (event) => player?.frame?.onMessage(event));
  window.addEventListener('resize', () => player?.frame?.fit());
  document.addEventListener('visibilitychange', () => player?.visibility());
  document.addEventListener('keydown', (ev) => {
    if (!player?.frame) return;
    const tag = String(ev.target?.tagName ?? '');
    if (ev.altKey || ev.ctrlKey || ev.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || ev.target?.isContentEditable) return;
    if (ev.key === ' ' || ev.key === 'Spacebar') {
      if (tag === 'BUTTON') return; // 눌린 단추는 제 일을 한다
      ev.preventDefault?.();
      player.toggle();
    } else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') {
      ev.preventDefault?.();
      player.seekBy(ev.key === 'ArrowLeft' ? -SEEK_MS : SEEK_MS);
    }
  });
}

function createPlayer(app, sid, followMode) {
  // phase: loading · ready · missing(없음·만료) · off(피드 없음 — 404) · error
  const st = { phase: 'loading', part: undefined, bad: '', meta: null, batches: [], seq: -1, next: 0, latestAt: null, recvAt: 0, lastNew: 0, archived: false };
  // follow: on · gone(떠났거나 멈춤) · null. 재생은 다 받으면 곧바로
  const play = { tl: null, rows: [], T: null, playing: true, speed: 1, skip: true, follow: followMode ? 'on' : null, timer: 0, poll: 0, last: 0, dead: false };
  const me = { sid, frame: null, view: null, box: null, root: null };

  // --- 받기 ----------------------------------------------------------------------------------------

  async function fetchPage(after) {
    try {
      const { status, body } = await call((st.archived ? archiveReq : sessionReq)(sid, after).url);
      if (status === 404) return { off: true };
      if (status !== 200 || !isObj(body)) {
        const code = typeof body?.error?.code === 'string' ? body.error.code.slice(0, 40) : '';
        return { part: { why: `응답 오류 (HTTP ${status}${code ? ` · ${code}` : ''})` }, code };
      }
      if (body.state !== 'ok') return { part: { state: String(body.state), code: typeof body.code === 'string' ? body.code : null } };
      return { body };
    } catch (err) {
      const gone = isExpired(err);
      if (gone) app.expired();
      return { part: { why: gone ? '로그인 만료·연결 끊김' : '불러오지 못함' } };
    }
  }
  // 응답 하나를 붙인다 — meta 는 마지막 것, 묶음은 seq 가 지난 것보다 큰 것만. 새 묶음 수
  function absorb(body) {
    if (isObj(body.meta)) st.meta = body.meta;
    let fresh = 0;
    for (const b of list(body.batches)) {
      if (!isObj(b) || num(b.at) === null || (num(b.seq) !== null && b.seq <= st.seq)) continue;
      st.batches.push(b);
      st.seq = num(b.seq) ?? st.seq;
      st.latestAt = Math.max(st.latestAt ?? b.at, b.at);
      fresh += 1;
    }
    if (fresh) st.recvAt = Date.now();
    st.next = num(body.next) ?? st.next + list(body.batches).length;
    return fresh;
  }
  async function loadAll() {
    for (let i = 0; i < PAGES_MAX; i++) {
      const got = await fetchPage(st.next);
      if (play.dead) return;
      if (got.off) return settle('off', NO_FEED);
      if (got.part) return settle('error', got.part, got.code);
      if (!isObj(got.body.meta)) return settle('missing', OK);
      // 078 — Redis 엔 머리만 남고 묶음은 S3 에 있다. 끝난 조각이라 따라 보기는 없다
      if (got.body.archived === true && !st.archived) {
        Object.assign(st, { archived: true, next: 0 });
        play.follow = null;
        continue;
      }
      absorb(got.body);
      if (got.body.more !== true) break;
    }
    rebuild();
    play.T = play.follow === 'on' ? followTarget(play.tl, null) : play.tl.start;
    st.lastNew = Date.now();
    settle('ready', OK);
  }
  function settle(phase, part, code = '') {
    Object.assign(st, { phase, part, bad: code });
    if (phase !== 'ready') play.follow = null;
    app.paint();
    if (phase === 'ready') schedulePoll(FOLLOW_POLL_MS);
  }
  function rebuild() {
    play.tl = timeline(st.meta, st.batches);
    play.rows = eventRows(play.tl);
    if (!me.view) return;
    me.view.marks(barMarks(play.tl));
    me.view.list(play.rows, play.follow === 'on');
  }

  // 따라 보기 — 2초마다 after=next. 새 묶음이면 다시 펴서 마지막 사건 − 3초를 따라가고, 30초 동안 없으면 받기를 멈춘다
  function schedulePoll(ms) {
    clearTimeout(play.poll);
    play.poll = 0;
    if (!play.dead && play.follow === 'on' && visible()) play.poll = setTimeout(poll, ms);
  }
  async function poll() {
    play.poll = 0;
    if (play.dead || play.follow !== 'on') return;
    const began = Date.now();
    const got = await fetchPage(st.next);
    if (play.dead || play.follow !== 'on') return;
    const fresh = got.body ? absorb(got.body) : 0;
    const now = Date.now();
    if (fresh) {
      st.lastNew = now;
      rebuild();
      play.T = followTarget(play.tl, play.T);
      show(null);
    } else if (now - st.lastNew > FOLLOW_STOP_MS) {
      play.follow = 'gone';
      app.paint();
      return;
    }
    schedulePoll(got.body?.more === true ? 0 : Math.max(0, FOLLOW_POLL_MS - (now - began)));
  }
  // 따라 보기를 끈다(뒤로 끌기·멈춤·속도 바꿈) — 받기를 멈추고 받은 만큼 재생으로
  function leaveFollow() {
    if (play.follow !== 'on') return;
    play.follow = null;
    clearTimeout(play.poll);
    play.poll = 0;
    app.paint();
  }

  // --- 판 머리 ---------------------------------------------------------------------------------------

  function badge() {
    if (play.follow === 'on' && play.tl) {
      const lag = lagSec(st.latestAt ?? play.tl.end, Date.now() - st.recvAt, play.T ?? play.tl.end);
      return ['live', `● 따라 보는 중 · ${lag}초 늦음`, '2초마다 새 묶음을 받아 마지막 사건 3초 뒤를 1× 로 따라간다 — 뒤로 끌거나 멈추면 끈다'];
    }
    if (play.follow === 'gone') return ['dim', '방문자가 떠났거나 멈춤', '30초 동안 새 묶음이 없어 받기를 멈췄다'];
    if (st.phase === 'ready' && truncated(st.meta)) return ['warn', '앞부분만', '조각 상한(1MB · 묶음 3,600개)에 닿아 그 뒤 움직임은 저장되지 않았다'];
    return null;
  }
  function note() {
    if (st.phase === 'missing') return '기록이 없거나 지났다';
    if (st.phase === 'off') return '이 서버에는 화면 기록 피드가 아직 없다';
    if (st.bad === 'bad_sid') return '이 조각은 볼 수 없다 — 조각 id 꼴이 아니다(bad_sid)';
    return '';
  }
  function unit() {
    const m = st.meta;
    if (!isObj(m)) return '';
    const first = num(m.first) ?? play.tl?.start ?? null;
    return [typeof m.ip === 'string' ? m.ip : '–', devName(m.dev), first === null ? '–' : `${kstText(first, true)} KST`, consentText(m)].join(' · ');
  }

  // --- 붙이기 · 그리기 ----------------------------------------------------------------------------------

  function mount(box) {
    if (me.box === box) return;
    me.frame = createFrame(() => show(null));
    me.view = createView({
      seekRatio: (r) => play.tl && seek(play.tl.start + r * (play.tl.end - play.tl.start)),
      seekAt: (T) => seek(T),
      toggle: () => toggle(),
      speed: (s) => {
        leaveFollow();
        play.speed = s;
        show(null);
      },
      skip: () => {
        play.skip = !play.skip;
        show(null);
      },
    });
    const main = el('div', 'rp-main');
    main.append(me.frame.node, me.view.bar, me.view.controls);
    me.root = el('div', 'rp');
    me.root.append(main, me.view.side);
    box.className = 'panel rp-panel';
    box.replaceChildren(me.root);
    me.box = box;
    rebuild();
    show(null);
    play.last = Date.now();
    schedule();
  }

  // 지금 시각을 그린다 — clickFrom 은 재생으로 넘어온 앞 시각(되감기·건너뛰기는 null)
  function show(clickFrom) {
    if (!me.frame || !play.tl || play.dead) return;
    const s = frameState(play.tl, play.T, clickFrom);
    me.frame.aim(s.page, visible());
    me.frame.size(s.view);
    me.frame.shade(s.hidden);
    me.frame.post(s.message);
    const span = play.tl.end - play.tl.start;
    me.view.show({ T: play.T, ratio: span > 0 ? (play.T - play.tl.start) / span : 1, text: timeText(play.tl, play.T), playing: play.playing, speed: play.speed, skip: play.skip });
    const b = badge();
    if (b) tagText('t-sheet', b);
  }

  // 재생 — 50ms 마다 흐른 시간 × 속도(빈 시간 건너뛰기), 따라 보기는 1× 로 마지막 사건까지. 틀 준비를 기다리는 동안은 멈춘다
  function tick() {
    play.timer = 0;
    if (play.dead || !play.tl) return;
    const now = Date.now();
    const dt = Math.min(1_000, Math.max(0, now - play.last)); // 숨었다 돌아온 큰 틈은 1초까지만
    play.last = now;
    const prev = play.T;
    if (play.playing && !me.frame.holding()) {
      play.T = play.follow === 'on' ? Math.min(play.tl.end, play.T + dt) : advance(play.tl, play.T, dt * play.speed, play.skip);
      if (play.follow !== 'on' && play.T >= play.tl.end) play.playing = false;
    }
    show(play.T > prev ? prev : null);
    schedule();
  }
  function schedule() {
    clearTimeout(play.timer);
    play.timer = 0;
    if (!play.dead && me.frame && visible() && (play.playing || play.follow === 'on')) play.timer = setTimeout(tick, TICK_MS);
  }

  function seek(T) {
    if (!play.tl) return;
    const to = Math.min(play.tl.end, Math.max(play.tl.start, T));
    if (to < play.T) leaveFollow(); // 뒤로 끌면 따라 보기를 끄고 재생으로
    play.T = to;
    show(null);
  }
  function toggle() {
    if (!play.tl) return;
    leaveFollow();
    if (!play.playing && play.T >= play.tl.end) play.T = play.tl.start; // 끝에서 다시 누르면 처음부터
    play.playing = !play.playing;
    play.last = Date.now();
    show(null);
    schedule();
  }

  function stop() {
    play.dead = true;
    clearTimeout(play.timer);
    clearTimeout(play.poll);
    me.frame?.clear();
    me.root?.remove();
  }

  loadAll();
  return Object.assign(me, {
    stop,
    toggle,
    seekBy: (ms) => play.tl && seek(play.T + ms),
    // 숨으면 틀을 비우고 주기를 멈춘다. 보이면 틀을 다시 띄우고(준비까지 시계를 멈춤) 따라 보기 받기를 곧바로
    visibility() {
      if (!me.frame || play.dead) return;
      show(null);
      play.last = Date.now();
      schedule();
      if (visible()) schedulePoll(0);
      else clearTimeout(play.poll);
    },
    model: () => ({
      part: st.part,
      deps: [sid, st.phase],
      title: '화면 재생',
      unit: unit(),
      badge: badge(),
      note: note(),
      custom: st.phase === 'ready' ? (box) => mount(box) : undefined,
    }),
    state: () => ({ st, play }), // node 시험이 본다
  });
}
