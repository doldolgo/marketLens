// 재생 틀(스펙 074 §3.3·§3.4) — 공개 사이트의 그 화면(+ kt-replay=1)을 띄우고, 조각의 화면 크기를 칸에 맞춰 줄이고(늘리지 않음), 준비를
// 알린 틀에만 상태 메시지를 보낸다. 틀 주소·postMessage 대상·받는 메시지 출처는 SITE_ORIGIN 하나(065 screens.js 와 같은 값).
// 화면이 바뀌면(p) 다시 띄우고 준비를 기다린다(그동안 holding — 재생 시계가 멈춘다), 5초 안에 준비가 없으면 한 줄을 띄우고 이어 간다.
import { el, frameVars, isObj, num } from './common.js';
import { framePath } from './replaymodel.js';

// 재생으로 띄우는 공개 사이트 — 로컬 확인은 커밋하지 않는 시험 사본에서 바꾼다(065 §4 꼴 — replay.js 의 관리자 출처와 짝)
const SITE_ORIGIN = 'https://kimptrack.com';
export const FRAME_WAIT_MS = 5_000;
const ROOM_PX = 260; // 창 높이에서 판 머리·시간 막대·조절 줄 몫
const MIN_STAGE = 240;
const LINES = Object.freeze({
  loading: '페이지를 띄우는 중…',
  silent: '페이지를 띄우지 못했다(공개 사이트가 관리자 출처를 허용하는지)',
  unknown: '이 화면은 틀로 띄울 수 없다',
});

// onChange() — 준비가 왔거나 5초가 지나 틀 상태가 바뀌었을 때(판이 지금 시각을 다시 그린다)
export function createFrame(onChange) {
  const named = (node, id) => {
    node.id = id;
    return node;
  };
  const wrap = named(el('div', 'rp-wrap'), 'rp-wrap');
  const stage = named(el('div', 'rp-stage'), 'rp-stage');
  const frame = named(el('iframe'), 'rp-frame');
  frame.setAttribute('title', '화면 재생 틀');
  frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');
  frame.setAttribute('referrerpolicy', 'strict-origin');
  frame.setAttribute('tabindex', '-1');
  const shade = named(el('p', 'rp-shade', '방문자가 탭을 숨김'), 'rp-shade');
  const note = named(el('p', 'rp-note'), 'rp-note');
  shade.hidden = true;
  note.hidden = true;
  stage.append(frame, shade, note);
  wrap.append(stage);
  const st = { key: '', page: null, ready: false, silent: false, unknown: false, wait: 0, sent: null, view: null };

  // 틀 주소를 쓰는 곳은 여기 하나 — SITE_ORIGIN + framePath() 또는 about:blank
  function load(url) {
    frame.src = url;
  }
  function line() {
    const text = st.unknown ? LINES.unknown : st.silent ? LINES.silent : LINES.loading;
    if (note.textContent !== text) note.textContent = text;
    note.hidden = !(st.unknown || (st.key !== 'about:blank' && !st.ready));
  }
  // 그 화면을 띄운다 — 바뀔 때만. on 이 거짓(문서가 숨음·판 닫힘)이면 about:blank(대시보드는 실시간 연결을 연다)
  function aim(page, on) {
    const path = framePath(page);
    const want = path && on ? `${SITE_ORIGIN}${path}` : 'about:blank';
    st.unknown = !path;
    if (want !== st.key) {
      clearTimeout(st.wait);
      Object.assign(st, { key: want, page: want === 'about:blank' ? null : page, ready: false, silent: false, wait: 0, sent: null });
      load(want);
      if (want !== 'about:blank') {
        st.wait = setTimeout(() => {
          if (st.key !== want || st.ready) return;
          st.silent = true;
          line();
          onChange();
        }, FRAME_WAIT_MS);
      }
    }
    line();
  }
  // 준비를 기다리는 중 — 재생 시계를 멈춘다(5초가 지나면 이어 간다)
  const holding = () => st.key !== 'about:blank' && !st.ready && !st.silent;

  // 크기 — 조각의 화면 크기(r 사건이면 그 값). 칸 폭·창 높이에 맞춘 몫으로 줄인다
  function size(view) {
    if (st.view && st.view[0] === view[0] && st.view[1] === view[1]) return;
    st.view = view;
    frame.setAttribute('width', String(view[0]));
    frame.setAttribute('height', String(view[1]));
    fit();
  }
  function fit() {
    if (!st.view) return;
    const [w, h] = st.view;
    const room = Math.max(MIN_STAGE, (num(window.innerHeight) ?? 900) - ROOM_PX);
    const f = Math.min(1, (wrap.clientWidth || w) / w, room / h);
    frameVars(stage, f, h * f, w);
  }

  // 보내기 — 준비를 알린 틀에만, 대상 출처는 늘 공개 사이트 하나. 같은 상태는 다시 보내지 않는다(누름은 늘 보낸다)
  function post(message) {
    if (!st.ready) return;
    const sig = JSON.stringify(message);
    if (sig === st.sent && !message.click) return;
    st.sent = sig;
    const win = frame.contentWindow;
    if (win) win.postMessage(message, SITE_ORIGIN);
  }

  // 준비 알림(§3.4) — 출처가 공개 사이트이고 창이 이 틀일 때만, 지금 띄운 화면의 것만. 받을 때마다 상태를 다시 보낸다(대시보드는 두 번)
  function onMessage(event) {
    const win = frame.contentWindow;
    if (event.origin !== SITE_ORIGIN || !win || event.source !== win) return;
    const d = event.data;
    if (!isObj(d) || d.type !== 'kt-replay-ready' || d.v !== 1 || !st.page || d.page !== st.page) return;
    clearTimeout(st.wait);
    Object.assign(st, { ready: true, silent: false, sent: null });
    line();
    onChange();
  }

  return {
    node: wrap,
    aim,
    holding,
    size,
    fit,
    post,
    onMessage,
    shade: (on) => {
      shade.hidden = !on;
    },
    // 판이 닫힌다 — 틀을 비운다
    clear() {
      clearTimeout(st.wait);
      st.key = 'about:blank';
      load('about:blank');
    },
    state: () => st,
  };
}
