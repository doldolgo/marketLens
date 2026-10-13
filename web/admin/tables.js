// 상세 판의 표(스펙 066 §3.2) — 통계 표(보이는 구간, 계열마다 한 행: 최소·평균·최대·p95·마지막·이전 대비)와 원자료 표(시각 KST ·
// 계열 값 + 이전 기간 값). 값 글자는 그 칸(pane)의 단위 함수, CSV 는 수 칸이면 수 그대로.
import { button, el, int, kstText, pressed } from './common.js';
import { change, csvName, hasValue, seriesCsv, seriesRows, signed, stats } from './calc.js';
import { table } from './ui.js';

export const keyClass = (mark) => (/^c[1-8]$/.test(mark ?? '') ? `k${mark.slice(1)}` : String(mark ?? '').startsWith('k') ? mark : `k${mark}`);
export const diffText = (d, fmt) =>
  d.diff === null ? '–' : `${d.diff > 0 ? '+' : d.diff < 0 ? '−' : ''}${fmt(Math.abs(d.diff))}${d.pct === null ? '' : ` (${signed(d.pct)}%)`}`;
// 표 칸의 CSV 값 — 수 칸은 수 그대로(천 단위 쉼표 없이), 그 밖은 화면 글자
export const csvValue = (c, r) => (c.csv ? c.csv(r) : c.num && typeof r[c.key] === 'number' ? r[c.key] : c.fmt ? c.fmt(r[c.key], r) : r[c.key]);

const fmtOf = (t, s) => t.panes?.[s.pane ?? 0]?.fmt ?? t.fmt ?? int;

export function statTable(node, t, series, lo, hi) {
  const rows = series.map((s) => {
    const st = stats(s.data, lo, hi);
    const ct = hasValue(s.cmp) ? stats(s.cmp, lo, hi) : null;
    return { s, st, d: ct ? change(st.avg, ct.avg) : null, fmt: fmtOf(t, s) };
  });
  const val = (k) => (r) => el('span', 'sv', r.fmt(r.st[k]));
  const who = (r) => {
    const w = el('span', 'who');
    w.append(el('i', `key ${keyClass(r.s.mark)}`), el('span', null, r.s.name));
    return w;
  };
  table(node, [
    { key: 'name', label: '계열', cell: who },
    { key: 'min', label: '최소', num: true, cell: val('min') },
    { key: 'avg', label: '평균', num: true, cell: val('avg') },
    { key: 'max', label: '최대', num: true, cell: val('max') },
    { key: 'p95', label: 'p95', num: true, cell: val('p95') },
    { key: 'last', label: '마지막', num: true, cell: val('last') },
    { key: 'cmp', label: '이전 대비(평균)', num: true, cell: (r) => el('span', null, r.d ? diffText(r.d, r.fmt) : '–') },
  ], rows);
}


// 원자료 — 보이는 계열의 시각마다 값(+ 이전 기간 값). 기본 최근 순
export function rawTable(id, t, series, lo, hi) {
  const columns = [{ key: 'time', label: '시각(KST)', fmt: (v) => kstText(v), csv: (r) => kstText(r.time, true) }];
  series.forEach((s, i) => {
    const fmt = fmtOf(t, s);
    if (hasValue(s.cmp)) {
      columns.push({ key: `v${i}`, label: s.name, num: true, fmt });
      columns.push({ key: `p${i}`, label: `${s.name} (이전)`, num: true, fmt, cls: 'dim' });
    } else columns.push({ key: `v${i}`, label: s.name, num: true, fmt });
  });
  const rows = seriesRows(series, lo, hi).map(([time, ...vals]) => {
    const r = { time };
    let k = 0;
    series.forEach((s, i) => {
      r[`v${i}`] = vals[k++];
      if (hasValue(s.cmp)) r[`p${i}`] = vals[k++];
    });
    return r;
  });
  return { title: '원자료', columns, rows, sort: { key: 'time', dir: 'desc' }, csv: () => [csvName(id, lo, hi), seriesCsv(series, lo, hi)] };
}


// 계열 칩 — 누르면 그 계열을 숨기거나 보인다(그래프·통계·원자료·CSV 가 같이 따른다)
export function seriesChips(series, hidden, onToggle) {
  return series
    .filter((s) => hasValue(s.data) || hasValue(s.cmp))
    .map((s) => {
      const b = button('schip');
      pressed(b, !hidden.has(s.name));
      b.append(el('i', `key ${keyClass(s.mark)}`), el('span', null, s.name));
      b.addEventListener('click', () => onToggle(s.name));
      return b;
    });
}

// 관련 카드 한 줄 — [[판 id, 이름]] → '관련' + 단추들(없으면 빈 줄)
export function relatedButtons(items, onOpen) {
  if (!items.length) return [];
  return [
    el('span', null, '관련'),
    ...items.map(([rid, title]) => {
      const b = button('btn', title);
      b.addEventListener('click', () => onOpen(rid));
      return b;
    }),
  ];
}
