// '?' 설명 말풍선(스펙 071 §3.1) — 카드 머리·큰 수 타일·판 머리의 `.help`. 글은 HTML title 에 두 줄(뜻 · 읽는 법)로 두고,
// 페이지가 뜰 때 data-help 로 옮겨 브라우저 기본 툴팁이 겹치지 않게 한다(스크립트가 못 돌면 기본 툴팁이 그대로 보인다).
// 마우스 올림으로 연 말풍선은 떠나면 닫히고, 누름·Enter·Space 로 연 것은 다시 누름·밖 누름·Esc·스크롤까지 남는다. 말풍선은 하나뿐.
// 누름은 문서의 잡기 단계에서 받는다 — 큰 수 타일(단추) 안의 '?' 를 눌러도 타일이 눌리지 않게.
import { el } from './common.js';

const GAP = 6; // '?' 와 말풍선 사이
const EDGE = 8; // 화면 가장자리 여백

// 글 → [뜻, 읽는 법] — 줄바꿈으로 나눈 앞 두 줄(빈 줄은 버림). 한 줄이면 뜻만
export function helpLines(text) {
  return String(text ?? '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 2);
}

// 말풍선 자리 — '?' 아래(넘치면 위), 가운데 맞춤을 화면 안으로 민다. rect = '?' 의 getBoundingClientRect, size = 말풍선 [폭, 높이]
export function placeOf(rect, [w, h], [vw, vh]) {
  let top = rect.bottom + GAP;
  if (top + h > vh - EDGE && rect.top - GAP - h >= EDGE) top = rect.top - GAP - h;
  const left = Math.min(Math.max(EDGE, rect.left + rect.width / 2 - w / 2), Math.max(EDGE, vw - EDGE - w));
  return { top: Math.round(top), left: Math.round(left) };
}

let pop = null; // 말풍선 노드(처음 열 때 만든다)
let anchor = null; // 지금 연 '?'
let pinned = false; // 누름으로 열었나

const helpOf = (node) => (node && typeof node.closest === 'function' ? node.closest('.help') : null);

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

function open(h, pin) {
  adopt(h);
  if (anchor && anchor !== h) closeHelp();
  if (!pop) {
    pop = el('div', 'help-pop');
    pop.id = 'help-pop';
    pop.setAttribute('role', 'tooltip');
    pop.hidden = true;
    document.body.append(pop);
  }
  if (anchor !== h) {
    const [what, how] = helpLines(h.dataset.help);
    const para = (head, text) => {
      const p = el('p');
      p.append(el('b', null, head), el('span', null, text));
      return p;
    };
    pop.replaceChildren(...(what ? [para('뜻', what)] : []), ...(how ? [para('읽는 법', how)] : []));
  }
  anchor = h;
  pinned = pinned || pin;
  h.setAttribute('aria-expanded', 'true');
  h.setAttribute('aria-describedby', 'help-pop');
  pop.hidden = false;
  const r = h.getBoundingClientRect();
  const { top, left } = placeOf(r, [pop.offsetWidth, pop.offsetHeight], [window.innerWidth, window.innerHeight]);
  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;
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
      if (anchor && !(pop && typeof pop.contains === 'function' && pop.contains(ev.target))) closeHelp();
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
  document.addEventListener('mouseout', (ev) => {
    const h = helpOf(ev.target);
    if (!h || h !== anchor || pinned) return;
    if (helpOf(ev.relatedTarget) === h) return;
    closeHelp();
  });
  document.addEventListener('focusout', (ev) => {
    if (helpOf(ev.target) === anchor && !pinned) closeHelp();
  });
  window.addEventListener('scroll', () => closeHelp(), true);
  window.addEventListener('resize', () => closeHelp());
}
