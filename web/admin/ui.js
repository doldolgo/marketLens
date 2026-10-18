// HTML 조각(스펙 066 §3.1) — 큰 수·상위 목록·100% 띠·문턱 눈금 막대·표·작은 선(SVG)·카드 안 탭. 도넛·반원 게이지 대신이다.
// 글자는 textContent 로만 넣고, 막대 폭·자리는 비율 수만(widthOf·leftOf) 쓴다.
import { $, button, clean, el, int, leftOf, list, num, pct, pressed, svg, widthOf } from './common.js';
import { split100, topRows } from './calc.js';
import { helpNode } from './help.js';

// 큰 수 — 값 + 작은 단위 글. 문턱을 넘은 값만 상태색과 모양(▲ 주의 · ■ 문제)
export function bigNumber(id, text, sub, tone) {
  const node = $(id);
  if (!node) return;
  const kids = [];
  if (tone === 'bad' || tone === 'warn') kids.push(el('i', `shape ${tone}`));
  kids.push(clean(text));
  if (sub) kids.push(el('small', null, sub));
  node.replaceChildren(...kids);
  node.className = tone === 'bad' || tone === 'warn' ? `big ${tone}` : 'big';
}

// 상위 목록 — items [{ key, name, value }], 행 = 이름·값·비율, 글 뒤에 값 ÷ 최댓값 폭의 옅은 막대.
// o = { n(5~8), total(비율 분모 — 없으면 행 합), fmt, onPick(행 — 있으면 행이 단추), more(— '전체 보기', 상세 판), empty }
export function topList(id, items, o) {
  const box = $(id);
  if (!box) return;
  const all = topRows(items, undefined, o.total);
  const rows = all.slice(0, o.n ?? 6);
  if (!rows.length) {
    box.replaceChildren(el('p', 'empty', o.empty ?? '자료 없음'));
    return;
  }
  const holder = el('div', 'list');
  for (const r of rows) {
    const row = o.onPick ? button('lrow') : el('div', 'lrow');
    if (o.onPick) row.addEventListener('click', () => o.onPick(r));
    const bar = el('i', 'bg');
    widthOf(bar, r.ratio);
    const share = r.share === null ? '' : pct(r.share * 100, 0);
    row.append(bar, el('span', null, r.name), el('span', 'v', o.fmt(r.value)), el('span', 'p', share));
    row.title = `${clean(r.name)} · ${o.fmt(r.value)}${share ? ` · ${share}` : ''}`;
    holder.append(row);
  }
  const kids = [holder];
  if (o.more) {
    const foot = el('div', 'list-foot');
    const more = button('linkbtn', '전체 보기');
    more.addEventListener('click', () => o.more());
    foot.append(el('span', null, `${int(rows.length)} / ${int(all.length)}`), more);
    kids.push(foot);
  }
  box.replaceChildren(...kids);
}

// 100% 띠 여럿 — groups [{ name, items:[{ key, name, value, mark }], fmt, onPick?, empty? }]. 다섯 조각 + 기타, 아래 범례(이름·비율)
export function bar100s(id, groups) {
  const box = $(id);
  if (!box) return;
  const nodes = groups.map((g) => {
    const parts = split100(g.items, 5);
    const item = el('div', 'b100-item');
    const head = el('div', 'b100-name');
    head.append(el('span', null, g.name), el('span', null, g.note ?? ''));
    if (!parts.length) {
      item.append(head, el('p', 'empty', g.empty ?? '자료 없음'));
      return item;
    }
    const bar = el('div', 'b100');
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', `${clean(g.name)}: ${parts.map((p) => `${clean(p.name)} ${pct(p.share * 100, 0)}`).join(', ')}`);
    const legend = el('div', 'legend');
    for (const p of parts) {
      const seg = el('i', p.mark ?? 'kx');
      widthOf(seg, p.share);
      seg.title = `${clean(p.name)} · ${g.fmt(p.value)} · ${pct(p.share * 100, 1)}`;
      bar.append(seg);
      const pick = g.onPick && !p.other;
      const entry = pick ? button(null) : el('span');
      if (pick) entry.addEventListener('click', () => g.onPick(p));
      entry.append(el('i', `key sq ${p.mark ?? 'kx'}`), el('span', null, p.name), el('b', null, pct(p.share * 100, 0)));
      entry.title = seg.title;
      legend.append(entry);
    }
    item.append(head, bar, legend);
    return item;
  });
  box.replaceChildren(...nodes);
}

// 문턱 눈금 막대(게이지 대신 — 조사 §2-9). 정상은 회색, 문턱을 넘으면 그 상태색 + 모양. 눈금 = 주의·문제 문턱
export function meter(value, red, amber) {
  const wrap = el('span', 'meters');
  const bar = el('span', 'meter');
  const v = num(value);
  const tone = v === null ? null : v >= red ? 'bad' : v >= amber ? 'warn' : 'ok';
  const fill = el('i', tone === 'bad' || tone === 'warn' ? `fill ${tone}` : 'fill');
  widthOf(fill, (v ?? 0) / 100);
  bar.append(fill);
  for (const at of [amber, red]) {
    const tick = el('i', 'tick');
    leftOf(tick, at / 100);
    bar.append(tick);
  }
  const text = el('b');
  if (tone === 'bad' || tone === 'warn') text.append(el('i', `shape ${tone}`));
  text.append(v === null ? '–' : `${Math.round(v)}%`);
  wrap.append(bar, text);
  wrap.title = `${pct(v)} · 주의 ${amber}% · 문제 ${red}%`;
  return wrap;
}

// 표 — columns [{ key, label, num?, fmt?(값, 행), cell?(행) → 노드, sort?(false 면 정렬 안 함), cls? }], rows [객체].
// o = { sort: { key, dir }, onSort(key), onRow(행), expand(행) → 노드, rowTitle(행) → 글 } — 머리 단추로 정렬, 행을 누르거나 Enter 면 onRow,
// expand 가 있으면 행을 눌러 그 아래 한 줄을 펼치고 접는다(최근 요청의 나머지 칸)
export function table(node, columns, rows, o = {}) {
  if (!node) return;
  const head = el('tr');
  for (const c of columns) {
    const th = el('th', c.num ? 'n' : null);
    if (o.onSort && c.sort !== false) {
      const b = button(null, c.label);
      b.addEventListener('click', () => o.onSort(c.key));
      th.append(b);
      if (o.sort?.key === c.key) th.setAttribute('aria-sort', o.sort.dir === 'asc' ? 'ascending' : 'descending');
    } else th.textContent = clean(c.label);
    if (c.help) th.append(helpNode(c.help)); // 칸 설명(082 §3.3) — 눌러도 정렬 단추가 아니다(help.js 가 잡기 단계에서 받는다)
    head.append(th);
  }
  const thead = el('thead');
  thead.append(head);
  const tbody = el('tbody');
  for (const r of list(rows)) {
    const tr = el('tr', o.onRow ? 'go' : null);
    if (o.rowTitle) tr.title = clean(o.rowTitle(r));
    for (const c of columns) {
      const td = el('td', [c.num ? 'n' : '', c.cls ?? '', c.tone?.(r) ?? ''].join(' ').trim() || null);
      if (c.cell) td.append(c.cell(r));
      else td.textContent = clean(c.fmt ? c.fmt(r[c.key], r) : (r[c.key] ?? '–'));
      tr.append(td);
    }
    const act = o.onRow ? () => o.onRow(r) : null;
    let more = null;
    if (o.expand) {
      more = el('tr', 'rowmore');
      const td = el('td');
      td.setAttribute('colspan', String(columns.length));
      td.append(o.expand(r));
      more.append(td);
      more.hidden = true;
      tr.setAttribute('aria-expanded', 'false');
    }
    const go = act ?? (more ? () => {
      more.hidden = !more.hidden;
      tr.setAttribute('aria-expanded', String(!more.hidden));
    } : null);
    if (go) {
      tr.className = 'go';
      tr.setAttribute('tabindex', '0');
      tr.addEventListener('click', go);
      tr.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') go();
      });
    }
    tbody.append(tr);
    if (more) tbody.append(more);
  }
  node.replaceChildren(thead, tbody);
}

// 표 칸의 작은 선 — 칸마다 ECharts 를 띄우지 않고 SVG 꺾은선 하나(값은 수뿐)
export function sparkSvg(values) {
  const w = 96;
  const h = 22;
  const vals = list(values).map((v) => num(v) ?? 0);
  const box = svg('svg', { class: 'svgspark', viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' });
  const max = Math.max(0, ...vals);
  if (vals.length < 2 || !max) return box;
  const points = vals.map((v, i) => `${Math.round((i / (vals.length - 1)) * w * 10) / 10},${Math.round((h - 2 - (v / max) * (h - 4)) * 10) / 10}`).join(' ');
  box.append(svg('polyline', { points, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'vector-effect': 'non-scaling-stroke' }));
  return box;
}

// 카드 안 탭(목록 둘셋을 한 카드에) — options [[키, 이름]], 고른 것만 눌림
export function tabs(id, options, current, onPick) {
  const box = $(id);
  if (!box) return;
  box.replaceChildren(
    ...options.map(([key, label]) => {
      const b = button(null, label);
      pressed(b, key === current);
      b.addEventListener('click', () => onPick(key));
      return b;
    }),
  );
}
