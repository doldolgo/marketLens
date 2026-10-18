// '?' 설명 말풍선(스펙 071 §3.1) — 카드 머리·큰 수 타일·판 머리의 `.help`. 글은 HTML title 에 칸 넷(보여 주는 것 · 낱말 뜻 ·
// 자료 출처 · 읽는 법)으로 두고, 페이지가 뜰 때 data-help 로 옮겨 브라우저 기본 툴팁이 겹치지 않게 한다(스크립트가 못 돌면 기본 툴팁이
// 칸 이름 붙은 줄 그대로 보인다). 마우스 올림으로 연 말풍선은 '?' 와 말풍선 밖으로 떠나면 닫히고, 누름·Enter·Space 로 연 것은
// 다시 누름·밖 누름·Esc·페이지 스크롤까지 남는다. 말풍선은 하나뿐이고, 길면 말풍선 안에서 스크롤한다.
// 누름은 문서의 잡기 단계에서 받는다 — 큰 수 타일(단추) 안의 '?' 를 눌러도 타일이 눌리지 않게.
import { el } from './common.js';

const GAP = 6; // '?' 와 말풍선 사이(말풍선의 ::before 다리가 이 틈을 덮어 마우스가 건너가도 닫히지 않는다)
const EDGE = 8; // 화면 가장자리 여백
const MAX_H = 600; // 말풍선 높이 한도 — 넘으면 안에서 스크롤(admin.css .help-body 의 max-height 와 같은 값)
const MIN_H = 120; // 자리가 좁아도 이만큼은 보인다

// 칸 — [열쇠, 글의 칸 이름]. 글 순서도 이 순서다
export const HELP_PARTS = Object.freeze([
  ['what', '보여 주는 것'],
  ['words', '낱말 뜻'],
  ['source', '자료 출처'],
  ['how', '읽는 법'],
]);

// 글 → 칸 목록 [{ key, head, text, items: [[낱말, 뜻]…] }]. 줄이 '칸 이름:' 으로 시작하면 새 칸, '·' 로 시작하면 그 칸의 낱말 한 줄
// ('낱말 — 뜻'), 그 밖의 줄은 앞 칸 글에 잇는다. 칸 이름이 하나도 없는 글(071 첫 판의 두 줄)은 첫 줄 = 보여 주는 것, 둘째 줄 = 읽는 법
export function helpParts(text) {
  const lines = String(text ?? '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
  const headOf = (line) => HELP_PARTS.find(([, head]) => line.startsWith(`${head}:`));
  const part = (key, body = '') => ({ key, head: HELP_PARTS.find(([k]) => k === key)[1], text: body, items: [] });
  if (!lines.some(headOf)) return lines.slice(0, 2).map((line, i) => part(i === 0 ? 'what' : 'how', line));
  const out = [];
  let cur = null;
  for (const line of lines) {
    const found = headOf(line);
    if (found) {
      cur = part(found[0], line.slice(found[1].length + 1).trim());
      out.push(cur);
      continue;
    }
    if (!cur) out.push((cur = part('what')));
    if (line.startsWith('·')) {
      const body = line.slice(1).trim();
      const at = body.indexOf(' — ');
      cur.items.push(at > 0 ? [body.slice(0, at), body.slice(at + 3)] : ['', body]);
    } else cur.text = cur.text ? `${cur.text} ${line}` : line;
  }
  return out;
}

// 상세 판 머리 아래 안내 줄(071 §3.3)이 쓰는 한 문장 — 보여 주는 것 칸의 글
export const helpWhat = (text) => helpParts(text).find((p) => p.key === 'what')?.text ?? '';

// 말풍선 자리 — '?' 아래가 기본, 아래에 다 안 들어가고 위에 들어가면 위, 둘 다 모자라면 넓은 쪽에 그 높이만큼(안에서 스크롤), 600 을 넘지 않는다.
// 좌우는 가운데 맞춤을 화면 안으로 민다. rect = '?' 의 getBoundingClientRect, [w, h] = 말풍선이 줄이지 않았을 때의 폭·높이
export function placeOf(rect, [w, h], [vw, vh]) {
  const below = vh - EDGE - (rect.bottom + GAP);
  const above = rect.top - GAP - EDGE;
  const side = h <= below || (h > above && below >= above) ? 'below' : 'above';
  const height = Math.min(h, MAX_H, Math.max(MIN_H, side === 'below' ? below : above));
  const top = side === 'below' ? rect.bottom + GAP : rect.top - GAP - height;
  const left = Math.min(Math.max(EDGE, rect.left + rect.width / 2 - w / 2), Math.max(EDGE, vw - EDGE - w));
  return { top: Math.round(top), left: Math.round(left), height: Math.round(height), side };
}

let pop = null; // 말풍선 노드(처음 열 때 만든다)
let body = null; // 말풍선 안 스크롤 칸
let anchor = null; // 지금 연 '?'
let pinned = false; // 누름으로 열었나

const helpOf = (node) => (node && typeof node.closest === 'function' ? node.closest('.help') : null);
// 말풍선 안의 노드인가 — 브라우저는 contains, 시험의 가짜 노드는 부모를 거슬러 본다
function inPop(node) {
  if (!pop || !node) return false;
  if (typeof pop.contains === 'function') return pop.contains(node);
  for (let n = node; n; n = n.parent) if (n === pop) return true;
  return false;
}

// title → data-help(한 번). 키보드로 닿게 단추 역할을 준다
function adopt(h) {
  if (!h || h.dataset.help !== undefined) return;
  h.dataset.help = h.getAttribute('title') ?? '';
  h.removeAttribute('title');
  h.setAttribute('role', 'button');
  h.setAttribute('tabindex', '0');
  h.setAttribute('aria-label', '설명');
  h.setAttribute('aria-expanded', 'false');
}

// 칸 하나 — 굵은 칸 이름, 글, 낱말 목록(낱말은 굵게). 글자는 모두 글로만 넣는다
function partNode(p) {
  const box = el('section', 'help-part');
  box.append(el('h3', null, p.head));
  if (p.text) box.append(el('p', null, p.text));
  if (p.items.length) {
    const ul = el('ul');
    for (const [word, meaning] of p.items) {
      const li = el('li');
      if (word) li.append(el('b', null, word), el('span', null, ` — ${meaning}`));
      else li.append(el('span', null, meaning));
      ul.append(li);
    }
    box.append(ul);
  }
  return box;
}

function open(h, pin) {
  adopt(h);
  if (anchor && anchor !== h) closeHelp();
  if (!pop) {
    pop = el('div', 'help-pop');
    pop.id = 'help-pop';
    pop.setAttribute('role', 'tooltip');
    pop.hidden = true;
    body = el('div', 'help-body');
    pop.append(body);
    document.body.append(pop);
  }
  if (anchor !== h) {
    body.replaceChildren(...helpParts(h.dataset.help).map(partNode));
    body.scrollTop = 0;
  }
  anchor = h;
  pinned = pinned || pin;
  h.setAttribute('aria-expanded', 'true');
  h.setAttribute('aria-describedby', 'help-pop');
  pop.hidden = false;
  // 줄이지 않은 높이 = 안 칸의 글 높이 + 말풍선 테두리
  const chrome = (Number(pop.offsetHeight) || 0) - (Number(body.offsetHeight) || 0);
  const natural = (Number(body.scrollHeight) || 0) + chrome;
  const place = placeOf(h.getBoundingClientRect(), [Number(pop.offsetWidth) || 0, natural], [window.innerWidth, window.innerHeight]);
  // 다 들어가면 높이를 묶지 않는다(반올림으로 한 줄이 잘려 스크롤 막대가 생기지 않게), 모자라면 남은 자리만큼
  body.style.maxHeight = place.height >= natural ? '' : `${Math.max(0, place.height - chrome)}px`;
  pop.dataset.side = place.side;
  pop.style.top = `${place.top}px`;
  pop.style.left = `${place.left}px`;
}

// 열린 말풍선을 닫는다 — 닫은 것이 있으면 true(Esc 가 상세 판까지 닫지 않게 app.js 가 먼저 묻는다)
export function closeHelp() {
  if (!anchor) return false;
  anchor.setAttribute('aria-expanded', 'false');
  anchor.removeAttribute('aria-describedby');
  anchor = null;
  pinned = false;
  if (pop) pop.hidden = true;
  return true;
}

export function mountHelp() {
  for (const h of document.querySelectorAll('.help[title]')) adopt(h);
  document.addEventListener(
    'click',
    (ev) => {
      const h = helpOf(ev.target);
      if (h) {
        ev.preventDefault();
        ev.stopPropagation();
        // 터치는 누르기 전에 mouseover 로 이미 열려 있다 — 그때 누름은 닫기가 아니라 고정
        if (anchor === h && pinned) closeHelp();
        else open(h, true);
        return;
      }
      if (anchor && !inPop(ev.target)) closeHelp();
    },
    true,
  );
  document.addEventListener(
    'keydown',
    (ev) => {
      const h = helpOf(ev.target);
      if (!h || (ev.key !== 'Enter' && ev.key !== ' ')) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (anchor === h && pinned) closeHelp();
      else open(h, true);
    },
    true,
  );
  document.addEventListener('mouseover', (ev) => {
    const h = helpOf(ev.target);
    if (h && anchor !== h && !pinned) open(h, false);
  });
  // 마우스로 연 것 — '?' 와 말풍선 사이를 오가는 동안은 남고, 둘 밖으로 나가면 닫힌다
  document.addEventListener('mouseout', (ev) => {
    if (!anchor || pinned) return;
    const from = helpOf(ev.target) === anchor || inPop(ev.target);
    if (!from) return;
    const to = ev.relatedTarget;
    if (helpOf(to) === anchor || inPop(to)) return;
    closeHelp();
  });
  document.addEventListener('focusout', (ev) => {
    if (helpOf(ev.target) === anchor && !pinned) closeHelp();
  });
  // 페이지 스크롤은 닫고, 말풍선 안 스크롤은 그대로 둔다
  window.addEventListener(
    'scroll',
    (ev) => {
      if (!inPop(ev.target)) closeHelp();
    },
    true,
  );
  window.addEventListener('resize', () => closeHelp());
}
