// Clarity 페이지 그림(스펙 066 §3.3 — 쉽게 읽기): 불만 신호 줄(이름·쉬운 풀이·좋음/주의/나쁨·방문 100칸·전날), 페이지 × 기기 줄
// (방문 막대·페이지 모양 스크롤 깊이·머문/움직인 시간 띠·가장 잦은 신호), 카드 한 문장. 모델은 insightdetail.js, 여기는 DOM 만.
// 글자는 el() 의 textContent 로만, 막대 폭은 widthOf(비율 수)로만. 칸 색칠은 클래스로 한다(style 을 쓰지 않는다).
import { $, button, clean, el, int, pct, widthOf } from './common.js';
import { LEVEL_WORD, dur, per100, trendText } from './insightdetail.js';

// 카드 한 문장 — 글이 없으면 숨긴다. 여러 줄(페이지 × 기기)은 줄마다 항목
export function say(id, text) {
  const node = $(id);
  if (!node) return;
  if (node.textContent !== clean(text)) node.textContent = clean(text);
  node.hidden = !text;
}
export function sayLines(id, lines) {
  const node = $(id);
  if (!node) return;
  node.replaceChildren(...lines.map((t) => el('li', null, t)));
  node.hidden = !lines.length;
}

// 등급 표 — 모양(● ▲ ■)과 글을 함께(색만으로 가르지 않는다)
function levelTag(level) {
  const node = el('span', `lvl ${level ?? 'none'}`);
  node.append(el('i', `shape ${level ?? ''}`.trim()), LEVEL_WORD[level] ?? '자료 없음');
  return node;
}

// 방문 100칸 — 칸 하나 = 방문 1번, 색칠한 칸 = 그 신호가 나온 방문. 0 보다 크고 반 칸이 안 되면 첫 칸을 옅게
function hundred(it) {
  const box = el('span', `cells ${it.level ?? 'none'}`);
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label', `방문 100번 중 ${per100(it.value)}`);
  for (let i = 0; i < 100; i += 1) box.append(el('i', i < it.cells ? 'on' : i === 0 && it.part ? 'part' : null));
  return box;
}

// 불만 신호 줄 여섯 — 머리(이름·등급 … 100번 중 n번), 풀이, 100칸 + 전날
export function signalList(box, m) {
  box.replaceChildren(
    ...m.items.map((it) => {
      const row = el('div', 'sig');
      const head = el('div', 'sig-head');
      head.append(el('b', null, it.name), levelTag(it.level), el('span', 'sig-n', `100번 중 ${per100(it.value)}`));
      const foot = el('div', 'sig-foot');
      foot.append(hundred(it), el('span', 'sig-was', trendText(it)));
      row.append(head, el('p', 'sig-plain', it.plain), foot);
      row.title = `${it.name} · 신호가 있던 방문 ${pct(it.value)}${it.before === null ? '' : ` · 전날 ${pct(it.before)}`}`;
      return row;
    }),
  );
}

// 페이지 모양 — 위에서부터 10% 씩 열 칸, 평균으로 내려본 데까지 밝게
function pageShape(lit, depth) {
  const box = el('span', 'pshape');
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label', `평균 ${pct(depth, 0)} 까지 내려봄`);
  for (let i = 0; i < 10; i += 1) box.append(el('i', i < lit ? 'on' : null));
  return box;
}
function bar(cls, ratio, inner) {
  const track = el('span', `pbar ${cls}`);
  const fill = el('i', 'fill');
  widthOf(fill, ratio);
  if (inner !== undefined) {
    const act = el('i', 'act');
    widthOf(act, inner);
    fill.append(act);
  }
  track.append(fill);
  return track;
}
// 칸 — 좁은 화면(머리 줄을 숨긴다)에서만 보이는 칸 이름을 앞에 둔다
const cell = (at, ...kids) => {
  const node = el('span', `pc c${at}`);
  if (at > 0) node.append(el('small', 'plabel', PLACE_HEAD[at]));
  node.append(...kids);
  return node;
};

// 페이지 × 기기 줄 — 머리 한 줄 + 줄마다 단추(누르면 상세 표). n 줄까지, 나머지는 '전체 보기'
export const PLACE_HEAD = Object.freeze(['페이지 · 기기', '방문 수', '어디까지 내려봤나', '머문 시간 중 움직인 시간', '눈에 띄는 불만 신호']);
export function placeList(box, rows, o) {
  const head = el('div', 'prow phead');
  head.setAttribute('aria-hidden', 'true');
  head.append(...PLACE_HEAD.map((t, i) => el('span', `pc c${i}`, t)));
  const shown = rows.slice(0, o.n);
  const items = shown.map((r) => {
    const row = button(`prow${r.thin ? ' thin' : ''}`);
    row.addEventListener('click', () => o.onPick(r));
    const name = cell(0, el('b', null, r.page), el('small', null, r.thin ? `${r.device} · 표본 적음` : r.device));
    const visits = cell(1, bar('visits', r.sessShare), el('span', 'pv', `${int(r.sessions)}번`));
    const depth = cell(2, pageShape(r.lit, r.scrollDepth), el('span', 'pv', r.scrollDepth === null ? '–' : `${pct(r.scrollDepth, 0)} 까지`));
    const time = cell(3, bar('time', r.timeShare, r.share === null ? 1 : r.share / 100), el('span', 'pv', r.share === null ? dur(r.activeSec) : `${dur(r.activeSec)} / ${dur(r.totalSec)}`));
    const sig = r.worst ? cell(4, el('i', `shape ${r.worst.level}`), el('b', null, r.worst.name), el('small', null, `100번 중 ${per100(r.worst.value)}`)) : cell(4, el('small', null, '–'));
    row.append(name, visits, depth, time, sig);
    row.title = `${r.where} · 방문 ${int(r.sessions)}번 · 평균 ${pct(r.scrollDepth, 0)} 까지 내려봄 · 머문 ${dur(r.totalSec)} 중 움직인 ${dur(r.activeSec)}${r.worst ? ` · ${r.worst.name} 방문 100번 중 ${per100(r.worst.value)}` : ''}`;
    return row;
  });
  const kids = [head, ...items];
  if (rows.length > shown.length) {
    const foot = el('div', 'list-foot');
    const more = button('linkbtn', '전체 보기');
    more.addEventListener('click', () => o.more());
    foot.append(el('span', null, `${int(shown.length)} / ${int(rows.length)}`), more);
    kids.push(foot);
  }
  box.replaceChildren(...kids);
}
