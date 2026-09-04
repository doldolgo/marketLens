// 선선갭 탭 — perp ↔ perp 거래소 간 가격갭·펀딩비갭 스캔
import { useState } from 'react';
import type { MockFeed } from '../data/mockFeed';
import type { GapPerp } from '../data/types';
import { STALE_SECONDS, pctColor } from '../config';
import { fmtPct } from '../lib/format';
import { NumField, ToggleBtn } from '../components/ui';

const GRID = '100px 2.2fr 150px';

interface Row {
  sym: string;
  bp: { pgap: number; low: GapPerp; high: GapPerp; pFund: number } | null;
  bf: { fg: number; fLong: GapPerp; fShort: GapPerp; fPrice: number } | null;
  fail: boolean;
  pgap: number | null; pfg: number | null; fgap: number | null; fpg: number | null;
  age: number; stale: boolean;
}

export default function PPTab({ feed }: { feed: MockFeed }) {
  const [q, setQ] = useState('');
  const [pThr, setPThr] = useState(0.3);
  const [fThr, setFThr] = useState(20);
  const [only, setOnly] = useState(false);
  const [sort, setSort] = useState('pgap');
  const [dir, setDir] = useState(-1);

  // 거래소별 펀딩 주기가 달라 시간당으로 정규화해서 비교
  const cyc = (ex: string) => ex === 'Hyperliquid' ? 1 : ex === 'Bitget' ? 4 : 8;
  const fHr = (x: GapPerp) => x.funding / cyc(x.ex);

  // perp 전 조합 중 최대 가격갭 페어와 최대 펀딩갭 페어를 각각 선택
  const rows: Row[] = feed.gapd.map(g => {
    const lp = g.perps.filter(x => x.status !== 'fail');
    let bp: Row['bp'] = null, bf: Row['bf'] = null;
    for (let i = 0; i < lp.length; i++) for (let j = i + 1; j < lp.length; j++) {
      const a = lp[i], b = lp[j];
      const low = a.prem <= b.prem ? a : b, high = a.prem <= b.prem ? b : a;
      const pgap = ((1 + high.prem / 100) / (1 + low.prem / 100) - 1) * 100;
      const fg = Math.abs(fHr(a) - fHr(b));
      const fLong = fHr(a) <= fHr(b) ? a : b, fShort = fHr(a) <= fHr(b) ? b : a;
      if (!bp || pgap > bp.pgap) bp = { pgap, low, high, pFund: fHr(high) - fHr(low) };
      if (!bf || fg > bf.fg) bf = { fg, fLong, fShort, fPrice: ((1 + fShort.prem / 100) / (1 + fLong.prem / 100) - 1) * 100 };
    }
    const age = bp ? Math.max(bp.low.age, bp.high.age) : Infinity;
    return { sym: g.sym, bp, bf, fail: !bp, pgap: bp ? bp.pgap : null, pfg: bp ? bp.pFund : null, fgap: bf ? bf.fg : null, fpg: bf ? bf.fPrice : null, age, stale: bp ? age >= STALE_SECONDS : false };
  });

  const list = rows.filter(r =>
    (q === '' || r.sym.toLowerCase().includes(q.toLowerCase())) &&
    (!only || (!r.fail && ((r.pgap ?? -99) >= pThr || (r.fgap ?? 0) * 24 * 365 >= fThr))));
  list.sort((a, b) => {
    if (a.fail !== b.fail) return a.fail ? 1 : -1;
    const va: unknown = a[sort as keyof Row], vb: unknown = b[sort as keyof Row];
    if (va == null || vb == null) return va == null ? 1 : -1;
    if (typeof va === 'string') return va < (vb as string) ? -dir : va > (vb as string) ? dir : 0;
    return ((va as number) - (vb as number)) * dir;
  });

  const fmtH = (v: number) => (v > 0 ? '+' : '') + v.toFixed(4) + '%/h';
  const headers: [string, string, 'left' | 'right'][] = [
    ['sym', '심볼', 'left'], ['pgap', '가격갭', 'right'], ['pfg', '펀딩갭', 'right'],
  ];
  const onSort = (k: string) => {
    if (k === sort) setDir(-dir);
    else { setSort(k); setDir(k === 'sym' ? 1 : -1); }
  };

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', padding: 'var(--space-3) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap' }}>
        <input className="input" placeholder="심볼 검색" value={q} onChange={e => setQ(e.target.value)}
          style={{ width: 150, fontSize: 12, padding: '5px 10px' }} />
        <NumField label="가격갭 임계값" value={pThr} step={0.1} onChange={setPThr} />
        <NumField label="펀딩갭 임계값 · 연" value={fThr} step={5} onChange={setFThr} />
        <ToggleBtn on={only} label="임계 초과만" onClick={() => setOnly(!only)} />
        <span style={{ fontSize: 11, color: 'var(--color-neutral-600)' }}>perp ↔ perp · 싼 쪽 롱 + 비싼 쪽 숏 · 펀딩비갭은 시간당 정규화</span>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-neutral-600)', fontVariantNumeric: 'tabular-nums' }}>{list.length} / {rows.length} 코인 표시</span>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'flex', justifyContent: 'center', padding: '0 var(--space-6)' }}>
        <div style={{ minWidth: 760, maxWidth: 1080, flex: 1, overflow: 'auto', borderLeft: '1px solid var(--color-divider)', borderRight: '1px solid var(--color-divider)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: GRID, position: 'sticky', top: 0, zIndex: 2, background: 'var(--color-bg)', borderBottom: '1px solid var(--color-neutral-800)', padding: '0 var(--space-6)' }}>
            {headers.map(([k, label, align]) => (
              <button key={k} onClick={() => onSort(k)} className="hv-txt"
                style={{
                  appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 10.5,
                  letterSpacing: '0.07em', textTransform: 'uppercase', padding: '8px 8px', cursor: 'pointer',
                  textAlign: align, whiteSpace: 'nowrap',
                  color: k === sort ? 'var(--color-accent-300)' : 'var(--color-neutral-600)',
                }}>
                {label}{k === sort ? (dir < 0 ? ' ▾' : ' ▴') : ''}
              </button>
            ))}
          </div>
          {list.map(r => {
            const hot = !r.fail && !r.stale && (r.pgap ?? -99) >= pThr;
            return (
              <div key={r.sym} className="hv-row"
                style={{
                  display: 'grid', gridTemplateColumns: GRID, alignItems: 'center', padding: '0 var(--space-6)', height: 48,
                  borderBottom: '1px solid color-mix(in srgb, #e9e9ed 7%, transparent)',
                  background: hot ? 'color-mix(in srgb, var(--color-accent) 8%, transparent)' : 'transparent',
                  opacity: r.stale ? 0.45 : 1,
                }}>
                <div style={{ padding: '0 8px', display: 'flex', alignItems: 'center', gap: 6, fontWeight: 500, fontSize: 13.5, color: hot ? 'var(--color-accent-300)' : 'var(--color-text)' }}>
                  <span style={{ width: 4, height: 4, borderRadius: '50%', background: hot ? 'var(--color-accent)' : 'transparent', flex: 'none' }} />{r.sym}
                </div>
                <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 9.5, padding: '2px 6px', border: '1px solid var(--color-accent-800)', borderRadius: 'var(--radius-sm)', color: 'var(--color-accent-300)', background: 'var(--color-surface)', whiteSpace: 'nowrap' }}>{r.bp ? r.bp.low.ex : '–'}</span>
                    <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>↔</span>
                    <span style={{ fontSize: 9.5, padding: '2px 6px', border: '1px solid var(--color-accent-800)', borderRadius: 'var(--radius-sm)', color: 'var(--color-accent-300)', background: 'var(--color-surface)', whiteSpace: 'nowrap' }}>{r.bp ? r.bp.high.ex : '–'}</span>
                    <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 84, textAlign: 'right', color: r.bp ? pctColor(r.bp.pgap) : 'var(--color-neutral-700)' }}>
                      {r.bp ? fmtPct(r.bp.pgap) : '–'}
                    </span>
                  </div>
                  <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', whiteSpace: 'nowrap' }}>
                    {r.bp ? `${r.bp.low.ex} 롱 / ${r.bp.high.ex} 숏` : '–'}
                  </span>
                </div>
                <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1 }}>
                  <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: r.bp ? pctColor(r.bp.pFund) : 'var(--color-neutral-700)' }}>{r.bp ? fmtH(r.bp.pFund) : '–'}</span>
                  <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>해당 조합 펀딩갭</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
