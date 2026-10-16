// 재생 판의 조절 줄·시간 막대·사건 목록(스펙 074 §3.3) — DOM 만 만들고 고친다(재생 시계는 replaysheet.js). 막대 위 자리·폭은 0~1 비율
// 수만(leftOf·widthOf), 글자는 textContent 로만. 시간 막대: 숨김 구간 회색 칸 · 화면 바뀜 세로 눈금 · 누름 빨간 점, 끌거나 눌러 옮긴다.
import { button, el, leftOf, num, pressed, widthOf } from './common.js';
import { SPEEDS, rowAt } from './replaymodel.js';

const LIST_MAX = 1_500; // 사건 목록 줄 상한(따라 보기는 뒤쪽을 남긴다)

// on = { seekRatio(0~1), seekAt(시각), toggle(), speed(배), skip() }
export function createView(on) {
  const named = (node, id) => {
    node.id = id;
    return node;
  };
  // 시간 막대
  const bar = named(el('div', 'rp-bar'), 'rp-bar');
  bar.setAttribute('role', 'slider');
  bar.setAttribute('tabindex', '0');
  bar.setAttribute('aria-label', '시간 막대 — 끌거나 눌러 옮긴다(←/→ 5초)');
  const marks = named(el('div', 'rp-marks'), 'rp-marks');
  const fill = named(el('i', 'rp-fill'), 'rp-fill');
  const head = named(el('i', 'rp-head'), 'rp-head');
  bar.append(marks, fill, head);
  let dragging = false;
  const at = (ev) => {
    const r = bar.getBoundingClientRect();
    if (r.width > 0) on.seekRatio(Math.min(1, Math.max(0, ((num(ev.clientX) ?? r.left) - r.left) / r.width)));
  };
  bar.addEventListener('pointerdown', (ev) => {
    dragging = true;
    bar.setPointerCapture?.(ev.pointerId);
    at(ev);
  });
  bar.addEventListener('pointermove', (ev) => dragging && at(ev));
  for (const type of ['pointerup', 'pointercancel']) bar.addEventListener(type, () => (dragging = false));

  // 조절 줄 — ▶/❚❚ · 속도 1× 2× 4× 8× · 빈 시간 건너뛰기 · HH:MM:SS KST · 경과 m:ss / 전체 m:ss
  const play = named(button('btn rp-play'), 'rp-play');
  play.addEventListener('click', () => on.toggle());
  const speeds = named(el('div', 'seg'), 'rp-speed');
  speeds.setAttribute('role', 'group');
  speeds.setAttribute('aria-label', '속도');
  const speedBtns = SPEEDS.map((s) => {
    const b = button(null, `${s}×`);
    b.dataset.speed = String(s);
    b.addEventListener('click', () => on.speed(s));
    return b;
  });
  speeds.append(...speedBtns);
  const skip = named(button('btn', '빈 시간 건너뛰기'), 'rp-skip');
  skip.title = '사건 사이 5초 넘는 틈을 1초로';
  skip.addEventListener('click', () => on.skip());
  const time = named(el('span', 'rp-time mono'), 'rp-time');
  const controls = el('div', 'rp-ctl');
  controls.append(play, speeds, skip, time);

  // 사건 목록 — 시각 · 종류 · 영역(행을 누르면 그 시각으로)
  const count = named(el('span', 'unit'), 'rp-count');
  const list = named(el('div', 'rp-list'), 'rp-list');
  const side = el('aside', 'rp-side');
  const sideHead = el('div', 'panel-head');
  sideHead.append(el('span', null, '사건'), count);
  side.append(sideHead, list);

  let items = [];
  let lines = [];
  let cur = -1;
  const write = (node, text) => {
    if (node.textContent !== text) node.textContent = text;
  };

  return {
    bar,
    controls,
    side,
    // 막대 표시 — barMarks() 의 자리들
    marks(m) {
      const mark = (cls, ratio, width) => {
        const node = el('i', cls);
        leftOf(node, ratio);
        if (width !== undefined) widthOf(node, width);
        return node;
      };
      marks.replaceChildren(
        ...m.hides.map(([x, w]) => mark('rp-hide', x, w)),
        ...m.pages.map((x) => mark('rp-tick', x)),
        ...m.clicks.map((x) => mark('rp-dot', x)),
      );
      marks.title = `누름 ${m.clicks.length} · 화면 바뀜 ${m.pages.length} · 숨김 ${m.hides.length}`;
    },
    // 사건 목록 — eventRows() 의 줄. 너무 많으면 앞(따라 보기는 뒤) 1,500줄
    list(rows, tail) {
      items = rows.length > LIST_MAX ? (tail ? rows.slice(-LIST_MAX) : rows.slice(0, LIST_MAX)) : rows;
      write(count, items.length < rows.length ? `${items.length} / ${rows.length}` : String(rows.length));
      lines = items.map((r) => {
        const line = button('rp-ev');
        line.dataset.kind = r.kind;
        line.append(el('span', 'mono', r.time), el('b', null, r.name), el('span', null, r.detail));
        line.addEventListener('click', () => on.seekAt(r.at));
        return line;
      });
      list.replaceChildren(...(lines.length ? lines : [el('p', 'empty', '움직임 말고는 사건 없음')]));
      cur = -1;
    },
    // 지금 — 지난 만큼·자리, 시각 글, 단추 눌림, 지금 시각의 목록 줄(aria-current)
    show(o) {
      widthOf(fill, o.ratio);
      leftOf(head, o.ratio);
      bar.setAttribute('aria-valuetext', o.text);
      write(time, o.text);
      write(play, o.playing ? '❚❚' : '▶');
      play.setAttribute('aria-label', o.playing ? '멈춤 (Space)' : '재생 (Space)');
      for (const b of speedBtns) pressed(b, Number(b.dataset.speed) === o.speed);
      pressed(skip, o.skip);
      const now = rowAt(items, o.T);
      if (now !== cur) {
        lines[cur]?.removeAttribute('aria-current');
        lines[now]?.setAttribute('aria-current', 'true');
        cur = now;
      }
    },
  };
}
