// 화면 페이지의 오른쪽 판(스펙 065 §3.6) — 탭 셋: ① 요소(영역 칩 거르기·표·바닥 줄) ② 영역(053 순위 목록) ③ 떠난 곳·깊이.
// 판 폭 360px 에 맞춰 표는 세 칸(순위 · 이름 · 막대와 값) — 나머지 칸은 ⤢ 상세 판(screensdetail.js)과 CSV 에. 행을 누르면 틀에 focus 를
// 보내고 그 행을 강조한다. 글자는 textContent·title 로만.
import { $, button, clean, el, int, n0, once, pct, pressed, put, stateWord, tag, widthOf } from './common.js';
import { METRIC_NAME, SMALL_PV, current, elementTag, isDashboard, ui } from './screensmodel.js';
import { areaName, elName } from './screensnames.js';

const PANELS = Object.freeze(['elements', 'areas', 'exits']);

// 단계 막대 — 값 ÷ 최대 폭, 덮어 보기와 같은 단계 색(lv0 = 보이기만)
function bar(cls, ratio, label) {
  const box = el('span', 'scr-bar');
  const fill = el('i', cls);
  widthOf(fill, ratio);
  box.append(fill);
  box.title = clean(label);
  return box;
}
// 이름 칸 — 이름 한 줄(넘치면 줄임표) + 흐린 작은 둘째 줄(영역·지금 화면에 없음 등), 거의 안 닿음이면 이름 앞에 ▲
function nameCell(name, sub, low = false) {
  const box = el('span', 'scr-name');
  const line = el('span', 'scr-line');
  if (low) line.append(el('i', 'shape warn'));
  line.append(clean(name));
  box.append(line);
  if (sub) box.append(el('small', 'scr-sub', sub));
  return box;
}
// 값 칸 — 단계 막대(값 ÷ 최대 폭) + 그 값
function valueCell(cls, ratio, label, text) {
  const box = el('span', 'scr-val');
  box.append(bar(cls, ratio, label), el('b', null, text));
  return box;
}

// 표 — columns [{ label, num?, cls?, text(행) | cell(행) }]. onRow(행) 이 함수를 돌려주는 행만 누를 수 있다(Enter 도). tail = 끝의 흐린 한 줄
function grid(node, columns, rows, o) {
  if (!node) return;
  const head = el('tr');
  for (const c of columns) head.append(el('th', [c.num ? 'n' : '', c.cls ?? ''].join(' ').trim() || null, c.label));
  const thead = el('thead');
  thead.append(head);
  const tbody = el('tbody');
  for (const r of rows) {
    const tr = el('tr', o.rowClass(r) || null);
    for (const c of columns) {
      const td = el('td', [c.num ? 'n' : '', c.cls ?? ''].join(' ').trim() || null);
      if (c.cell) td.append(c.cell(r));
      else td.textContent = clean(c.text(r));
      tr.append(td);
    }
    tr.title = clean(o.title(r));
    const go = o.onRow(r);
    if (go) {
      tr.className = `${tr.className} go`.trim();
      tr.setAttribute('tabindex', '0');
      tr.addEventListener('click', go);
      tr.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') go();
      });
    }
    tbody.append(tr);
  }
  if (o.tail) {
    const tr = el('tr', 'gone');
    const td = el('td', null, o.tail);
    td.setAttribute('colspan', String(columns.length));
    tr.append(td);
    tbody.append(tr);
  }
  node.replaceChildren(thead, tbody);
}

// ① 요소 — 고른 지표 큰 순(지금 화면에 없음·(기타)는 끝에 흐리게), 위에 영역 칩, 행 → focus(id·key)
function drawElements(app, c, send) {
  const all = c.elements.rows;
  const areas = [...new Set([...(c.row?.areas ?? []).map((a) => a?.id), ...all.map((e) => e.area)])].filter((id) => all.some((e) => e.area === id));
  if (ui.area && !areas.includes(ui.area)) ui.area = '';
  $('el-areas')?.replaceChildren(
    ...['', ...areas].map((id) => {
      const b = button('kchip', id ? areaName(c.page, id) : '전체');
      pressed(b, id === ui.area);
      b.addEventListener('click', () => {
        ui.area = id;
        app.paint();
      });
      return b;
    }),
  );
  const rows = ui.area ? all.filter((e) => e.area === ui.area) : all;
  put('el-head', `요소 · ${METRIC_NAME[c.metric]} 큰 순`);
  const max = c.elements.max;
  const where = (e) => [areaName(c.page, e.area), e.state === 'gone' ? '지금 화면에 없음' : ''].filter(Boolean).join(' · ');
  const columns = [
    { label: '순위', cls: 'rank', text: (e) => (e.rank ? `#${e.rank}` : '–') },
    { label: '이름', cls: 'scr-namecell', cell: (e) => nameCell(elName(c.page, e.key), e.state === 'other' ? '' : where(e)) },
    {
      label: METRIC_NAME[c.metric],
      cls: 'scr-valcell',
      cell: (e) => valueCell(`lv${e.level ?? 0}`, max ? e.m / max : 0, e.level === null ? '단계 없음' : `단계 ${e.level}`, elementTag(e, c.metric)),
    },
  ];
  grid($('tbl-elements'), columns, rows, {
    rowClass: (e) => [e.state === 'ok' ? '' : 'gone', ui.focus === `${e.area}|${e.key}` ? 'on' : ''].join(' ').trim(),
    title: (e) => [elName(c.page, e.key), areaName(c.page, e.area), e.key].join(' · '),
    onRow: (e) =>
      e.state === 'ok'
        ? () => {
            ui.focus = `${e.area}|${e.key}`;
            send({ type: 'kt-attention-focus', id: e.area, key: e.key });
            app.paint();
          }
        : null,
    tail: c.elements.capped ? '그 밖은 피드 상한 300' : '',
  });
  const keys = rows.filter((e) => e.state !== 'other');
  put('el-foot', `열쇠 ${int(keys.length)}개 · 지금 화면에 없음 ${int(keys.filter((e) => e.state === 'gone').length)}개`);
}

// ② 영역 — 053 순위 목록(순위 · 이름(거의 안 닿음 ▲) · 평균 보인 시간 막대와 그 값 — 도달률·100뷰당 클릭은 상세 판에), 행 → focus(id)
function drawAreas(app, c, send) {
  const v = c.areas;
  const low = (a) => a.low && a.state !== 'none';
  const sub = (a) => (a.state === 'gone' ? '지금 화면에 없음' : a.state === 'none' ? '기록 없음' : '');
  grid(
    $('tbl-areas'),
    [
      { label: '순위', cls: 'rank', text: (a) => (a.rank ? `#${a.rank}` : '–') },
      { label: '영역', cls: 'scr-namecell', cell: (a) => nameCell(areaName(c.page, a.id), sub(a), low(a)) },
      {
        label: '평균 보인 시간',
        cls: 'scr-valcell',
        cell: (a) => valueCell(`lv${a.level}`, v.top ? a.ms / v.top : 0, `단계 ${a.level}`, a.state === 'none' ? '–' : `${a.t.toFixed(1)}초`),
      },
    ],
    [...v.ranked, ...v.blank, ...v.away],
    {
      rowClass: (a) => [a.state === 'gone' ? 'gone' : '', ui.focusArea === a.id ? 'on' : ''].join(' ').trim(),
      title: (a) => [areaName(c.page, a.id), a.id, ...(a.state === 'none' ? [] : [`도달 ${a.r}%`, `100뷰당 클릭 ${a.c.toFixed(1)}`]), ...(low(a) ? ['거의 안 닿음'] : [])].join(' · '),
      onRow: (a) =>
        a.state === 'gone'
          ? null
          : () => {
              ui.focusArea = a.id;
              send({ type: 'kt-attention-focus', id: a.id });
              app.paint();
            },
      tail: '',
    },
  );
}

// ③ 떠난 곳(066 상위 목록 꼴 — 이름·%·n) · 깊이 계단 막대(0%~100% 지점 11줄, 도달이 처음 50% 밑으로 가는 줄에 '절반이 여기 전에 떠남')
function drawExits(c) {
  const x = c.exits;
  const max = Math.max(0, ...x.rows.map((r) => r.n));
  $('l-exits')?.replaceChildren(
    ...(x.rows.length
      ? x.rows.map((r) => {
          const row = el('div', 'lrow');
          const bg = el('i', 'bg');
          widthOf(bg, max ? r.n / max : 0);
          row.append(bg, el('span', null, areaName(c.page, r.id)), el('span', 'v', pct(r.pct, 0)), el('span', 'p', int(r.n)));
          row.title = `${clean(areaName(c.page, r.id))} · ${pct(r.pct, 1)} · ${int(r.n)}`;
          return row;
        })
      : [el('p', 'empty', '떠난 곳 기록 없음')]),
  );
  put('exit-note', x.total ? `pagehide 로 끝난 pv ${int(x.total)} — 전체 pv 의 ${pct(x.share, 0)}` : '');
  const d = c.depth;
  const box = $('l-depth');
  if (!box) return;
  if (!d || !d.total) {
    box.replaceChildren();
    put('depth-note', isDashboard(c.page) ? '탭 화면은 깊이 없음' : '깊이 기록 없음');
    return;
  }
  box.replaceChildren(
    ...d.reach.map((r, j) => {
      const row = el('div', j === d.half ? 'stair half' : 'stair');
      row.append(el('span', null, `${j * 10}%`), bar('fill', r / 100, `${j * 10}% 지점까지 ${pct(r, 1)}`), el('b', null, pct(r, 0)));
      if (j === d.half) row.append(el('span', 'stair-mark', '절반이 여기 전에 떠남'));
      return row;
    }),
  );
  put('depth-note', `${int(d.total)} pv 기준`);
}

// 판 — 탭·상태 배지·바탕 한 줄(불러오는 중·연결 안 됨·이 기간 기록 없음·표본 적음), 기대는 값이 바뀔 때만 다시 그린다
export function drawPanel(app, send) {
  const c = current(app);
  tag('t-panel', c.part);
  for (const b of document.querySelectorAll('[data-panel]')) pressed(b, b.dataset.panel === ui.panel);
  for (const id of PANELS) {
    const box = $(`panel-${id}`);
    if (box) box.hidden = ui.panel !== id;
  }
  const pv = n0(c.row?.pv);
  put('panel-note', !c.page ? '' : !c.ok ? stateWord(c.part) : !c.row ? '이 기간 기록 없음' : pv < SMALL_PV ? `표본 적음 — 페이지뷰 ${int(pv)}(5 미만)이라 단계·순위가 흔들린다` : '');
  once('scr-panel', [c.entry, c.page, c.device, c.metric, c.ready, ui.panel, ui.area, ui.focus, ui.focusArea], () => {
    if (!c.ok || !c.row) {
      for (const id of ['el-areas', 'tbl-elements', 'tbl-areas', 'l-exits', 'l-depth']) $(id)?.replaceChildren();
      for (const id of ['el-foot', 'exit-note', 'depth-note']) put(id, '');
      return;
    }
    if (ui.panel === 'elements') drawElements(app, c, send);
    else if (ui.panel === 'areas') drawAreas(app, c, send);
    else drawExits(c);
  });
}
