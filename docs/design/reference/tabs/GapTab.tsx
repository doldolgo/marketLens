// 선물–현물 갭 탭 — 해외 현물 매수 + 선물 숏 조합의 베이시스 스캔
import { useState } from 'react';
import type { MockFeed } from '../data/mockFeed';
import type { GapSpot, GapPerp } from '../data/types';
import { STALE_SECONDS, pctColor } from '../config';
import { fmtPct, fmtUSDT } from '../lib/format';
import { Seg, segOpt, NumField, ToggleBtn } from '../components/ui';

const GRID = '100px 1fr 320px 150px';

interface BestPair { gap: number; sp: GapSpot; pp: GapPerp }
interface Row {
  sym: string; base: number;
  bp: BestPair | null; bn: BestPair | null;
  fail: boolean; pos: number | null; neg: number | null;
  fundP: number | null; fundN: number | null;
  age: number; stale: boolean;
}

export default function GapTab({ feed, now }: { feed: MockFeed; now: number }) {
  const [q, setQ] = useState('');
  const [gView, setGView] = useState<'pos' | 'neg'>('pos');
  const [thr, setThr] = useState(0.5);
  const [only, setOnly] = useState(false);
  const [sort, setSort] = useState('pos');
  const [dirState, setDirState] = useState<number | null>(null);

  // 코인별로 현물×선물 전 조합 중 최대(진입)/최소(정리) 갭 페어 선택
  const rows: Row[] = feed.gapd.map(g => {
    const ls = g.spots.filter(x => x.status !== 'fail'), lp = g.perps.filter(x => x.status !== 'fail');
    let bp: BestPair | null = null, bn: BestPair | null = null;
    for (const sp of ls) for (const pp of lp) {
      const gap = ((1 + pp.prem / 100) / (1 + sp.off / 100) - 1) * 100;
      if (!bp || gap > bp.gap) bp = { gap, sp, pp };
      if (!bn || gap < bn.gap) bn = { gap, sp, pp };
    }
    const age = bp ? Math.max(bp.sp.age, bp.pp.age) : Infinity;
    return { sym: g.sym, base: g.base, bp, bn, fail: !bp, pos: bp ? bp.gap : null, neg: bn ? bn.gap : null, fundP: bp ? bp.pp.funding : null, fundN: bn ? bn.pp.funding : null, age, stale: bp ? age >= STALE_SECONDS : false };
  });

  const list = rows.filter(r =>
    (q === '' || r.sym.toLowerCase().includes(q.toLowerCase())) &&
    (!only || (!r.fail && (gView === 'pos' ? (r.pos ?? -99) >= thr : (r.neg ?? 99) <= -thr))));
  const gk = sort === 'pos' || sort === 'neg' ? gView : sort;
  const gd = dirState ?? (gView === 'pos' ? -1 : 1);
  list.sort((a, b) => {
    if (a.fail !== b.fail) return a.fail ? 1 : -1;
    const va: unknown = a[gk as keyof Row], vb: unknown = b[gk as keyof Row];
    if (va == null || vb == null) return va == null ? 1 : -1;
    if (typeof va === 'string') return va < (vb as string) ? -gd : va > (vb as string) ? gd : 0;
    return ((va as number) - (vb as number)) * gd;
  });

  // 다음 펀딩 시각 (Hyperliquid는 1시간 주기, 나머지 8시간)
  const nextFund = (ex: string) => {
    const cyc = (ex === 'Hyperliquid' ? 1 : 8) * 3600e3;
    const rem = cyc - (now % cyc); const m = Math.floor(rem / 60e3);
    return '펀딩 ' + (m < 60 ? m + '분 후' : Math.floor(m / 60) + '시간 ' + (m % 60) + '분 후');
  };
  const fmtF = (v: number) => (v > 0 ? '+' : '') + v.toFixed(3) + '%';

  const headers: [string, string, 'left' | 'right'][] = [
    ['sym', '심볼', 'left'], ['spot', '현물가 USDT', 'right'],
    [gView, gView === 'pos' ? '진입 갭 · 현물 → 선물' : '정리 갭 · 현물 → 선물', 'right'],
    ['fund', '펀딩비', 'right'],
  ];
  const onSort = (k: string) => {
    if (k === gk) setDirState(-gd);
    else { setSort(k); setDirState(k === 'sym' ? 1 : gView === 'neg' ? 1 : -1); }
  };

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', padding: 'var(--space-3) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap' }}>
        <input className="input" placeholder="심볼 검색" value={q} onChange={e => setQ(e.target.value)}
          style={{ width: 150, fontSize: 12, padding: '5px 10px' }} />
        <span style={{ fontSize: 12, color: 'var(--color-neutral-400)' }}>기준 보기</span>
        <Seg pad="4px 10px" opts={[['pos', '진입 기준'], ['neg', '정리 기준']].map(([id, label]) =>
          segOpt(label, gView === id, () => { setGView(id as 'pos' | 'neg'); setSort(id); setDirState(id === 'pos' ? -1 : 1); }))} />
        <NumField label="하이라이트 임계값" value={thr} step={0.1} onChange={setThr} />
        <ToggleBtn on={only} label="임계 초과만" onClick={() => setOnly(!only)} />
        <span style={{ fontSize: 11, color: 'var(--color-neutral-600)' }}>양의 갭 = perp &gt; 현물 → 현물 매수 + 선물 숏 · 음의 갭 = 반대 방향</span>
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
                  color: k === gk ? 'var(--color-accent-300)' : 'var(--color-neutral-600)',
                }}>
                {label}{k === gk ? (gd < 0 ? ' ▾' : ' ▴') : ''}
              </button>
            ))}
          </div>
          {list.map(r => {
            const b = gView === 'pos' ? r.bp : r.bn;
            const hot = !r.fail && !r.stale && (gView === 'pos' ? (r.pos ?? -99) >= thr : (r.neg ?? 99) <= -thr);
            return (
              <div key={r.sym} className="hv-row"
                style={{
                  display: 'grid', gridTemplateColumns: GRID, alignItems: 'center', padding: '0 var(--space-6)', height: 40,
                  borderBottom: '1px solid color-mix(in srgb, #e9e9ed 7%, transparent)',
                  background: hot ? 'color-mix(in srgb, var(--color-accent) 8%, transparent)' : 'transparent',
                  opacity: r.stale ? 0.45 : 1,
                }}>
                <div style={{ padding: '0 8px', display: 'flex', alignItems: 'center', gap: 6, fontWeight: 500, fontSize: 13.5, color: hot ? 'var(--color-accent-300)' : 'var(--color-text)' }}>
                  <span style={{ width: 4, height: 4, borderRadius: '50%', background: hot ? 'var(--color-accent)' : 'transparent', flex: 'none' }} />{r.sym}
                </div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' }}>
                  {b ? fmtUSDT(r.base * (1 + b.sp.off / 100)) : '–'}
                </div>
                <div style={{ padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 10, letterSpacing: '0.04em', padding: '2px 7px', border: '1px solid var(--color-neutral-800)', borderRadius: 'var(--radius-sm)', color: 'var(--color-neutral-400)', background: 'var(--color-surface)', whiteSpace: 'nowrap' }}>{b ? b.sp.ex : '–'} 현물</span>
                  <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>→</span>
                  <span style={{ fontSize: 10, letterSpacing: '0.04em', padding: '2px 7px', border: '1px solid var(--color-accent-800)', borderRadius: 'var(--radius-sm)', color: 'var(--color-accent-300)', background: 'var(--color-surface)', whiteSpace: 'nowrap' }}>{b ? b.pp.ex : '–'} 선물</span>
                  <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 66, textAlign: 'right', color: b ? pctColor(b.gap) : 'var(--color-neutral-700)' }}>
                    {b ? fmtPct(b.gap) : '–'}
                  </span>
                </div>
                <div style={{ padding: '0 8px', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1 }}>
                  <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: b ? pctColor(b.pp.funding) : 'var(--color-neutral-700)' }}>{b ? fmtF(b.pp.funding) : '–'}</span>
                  <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>{b ? nextFund(b.pp.ex) : ''}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
