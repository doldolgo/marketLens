// 실시간 스프레드 탭 — 코인별 최적 김프/역프 페어 + 입출금 가능 여부
import { useState } from 'react';
import type { MockFeed } from '../data/mockFeed';
import type { IoState, SpreadRow } from '../data/types';
import { HIGHLIGHT_THRESHOLD, STALE_SECONDS, pctColor } from '../config';
import { fmtKRW, fmtPct } from '../lib/format';
import { Seg, segOpt, NumField, ToggleBtn } from '../components/ui';

const FXS = ['Binance', 'Bybit', 'Bitget', 'MEXC', 'Gate.io', 'Hyperliquid'];
const GRID = '112px 1fr 264px 148px 88px';

// 슬리피지 반영 시 페어에 붙는 추가 필드
type CalcRow = SpreadRow & { slip?: number; fwdRaw?: number };

interface Agg {
  sym: string;
  bestF: CalcRow | null; bestR: CalcRow | null;
  fwd: number | null; rev: number | null; fwdRaw: number | null;
  usd: number | undefined;
  age: number; failAll: boolean; staleAll: boolean;
}

export default function SpreadTab({ feed, onPivot }: { feed: MockFeed; onPivot: (sym: string) => void }) {
  const [q, setQ] = useState('');
  const [dom, setDom] = useState('all');
  const [thr, setThr] = useState<number | null>(null);
  const [onlyHot, setOnlyHot] = useState(false);
  const [onlyIO, setOnlyIO] = useState(false);
  const [sortKey, setSortKey] = useState('fwd');
  const [sortDir, setSortDir] = useState(-1);
  const [view, setView] = useState<'kimp' | 'rev'>('kimp');
  const [basis, setBasis] = useState<'mid' | 'slip'>('mid');
  const [notional, setNotional] = useState(10000);
  const [fxOff, setFxOff] = useState<Record<string, boolean>>({});

  const thrVal = thr ?? HIGHLIGHT_THRESHOLD;

  // 체결 규모 대비 호가 유동성으로 슬리피지 추정 (양측 합산, 상한 6%)
  const slipOf = (liq: number) => Math.min(6, 100 * Math.pow(notional / Math.max(liq, 1), 0.85));

  // 코인별로 페어를 모아 최적 순방향/역방향 선택
  const byCoin = new Map<string, CalcRow[]>();
  for (const r0 of feed.spreads) {
    if (dom !== 'all' && r0.dom !== dom) continue;
    if (fxOff[r0.fx]) continue;
    let r: CalcRow = r0;
    if (basis === 'slip') {
      const sl = slipOf(r0.liqFx) + slipOf(r0.liqDom);
      r = { ...r0, fwd: Math.round((r0.fwd - sl) * 100) / 100, rev: Math.round((r0.rev - sl) * 100) / 100, slip: sl, fwdRaw: r0.fwd };
    }
    if (!byCoin.has(r.sym)) byCoin.set(r.sym, []);
    byCoin.get(r.sym)!.push(r);
  }
  const agg: Agg[] = [];
  byCoin.forEach((prs, sym) => {
    const live = prs.filter(r => r.status !== 'fail');
    const bestF = live.length ? live.reduce((a, b) => b.fwd > a.fwd ? b : a) : null;
    const bestR = live.length ? live.reduce((a, b) => b.rev > a.rev ? b : a) : null;
    agg.push({
      sym, bestF, bestR,
      fwd: bestF ? bestF.fwd : null, rev: bestR ? bestR.rev : null,
      fwdRaw: bestF ? (bestF.fwdRaw ?? bestF.fwd) : null,
      usd: bestF ? bestF.usd : prs[0]?.usd,
      age: live.length ? Math.min(...live.map(r => r.age)) : Infinity,
      failAll: !live.length,
      staleAll: live.length > 0 && live.every(r => r.age >= STALE_SECONDS),
    });
  });

  // 최적 페어의 출금/입금 다리 — 순방향은 해외에서 출금해 국내 입금
  const ioLegs = (c: Agg) => {
    const b = view === 'kimp' ? c.bestF : c.bestR;
    if (!b) return null;
    const wdEx = view === 'kimp' ? b.fx : b.dom;
    const depEx = view === 'kimp' ? b.dom : b.fx;
    const wi = feed.io[c.sym + '|' + wdEx], di = feed.io[c.sym + '|' + depEx];
    // 값이 없으면 **확인 불가**(null)다. 예전엔 !! 로 접어 false(막힘)로
    // 만들었는데, 그래서 백엔드가 아는 코인까지 전부 "중단"으로 보였다.
    return { wdEx, depEx, wd: wi?.wd ?? null, dep: di?.dep ?? null, net: wi?.net || di?.net || '–' };
  };
  // "옮길 수 있다"고 말하려면 양쪽 다 **확인된** 열림이어야 한다.
  // null(확인 불가)은 열림이 아니다 — 여기서 접으면 안 된다.
  const ioOk = (c: Agg) => { const l = ioLegs(c); return !!l && l.wd === true && l.dep === true; };
  // 막힌 건 아니지만 확인도 못 한 상태 (표시를 다르게 하려고 구분한다)
  const ioUnknown = (c: Agg) => {
    const l = ioLegs(c);
    return !!l && l.wd !== false && l.dep !== false && (l.wd === null || l.dep === null);
  };

  let list = agg.filter(c =>
    (q === '' || c.sym.toLowerCase().includes(q.toLowerCase())) &&
    (!onlyHot || (!c.failAll && ((view === 'kimp' ? c.fwd : c.rev) ?? -99) >= thrVal)) &&
    // "입출금 가능만" 은 옮길 수 있는 것만 보여달라는 뜻이다. 확인 불가는
    // 옮길 수 있다고 보장하지 못하므로 통과시키지 않는다. 조회 장애로 목록이
    // 비면 그것 자체가 신호이고, 필터를 끄면 전체가 그대로 보인다.
    (!onlyIO || ioOk(c)));
  const key = sortKey === 'fwd' || sortKey === 'rev' ? (view === 'kimp' ? 'fwd' : 'rev') : sortKey;
  list = list.slice().sort((a, b) => {
    if (a.failAll !== b.failAll) return a.failAll ? 1 : -1;
    let va: unknown = a[key as keyof Agg], vb: unknown = b[key as keyof Agg];
    if (key === 'krw') { va = a.usd; vb = b.usd; }
    // 가능(2) > 확인 불가(1) > 중단(0) — 세 상태를 정렬에서도 구분한다
    const ioRank = (c: Agg) => (ioOk(c) ? 2 : ioUnknown(c) ? 1 : 0);
    if (key === 'io') { va = ioRank(a); vb = ioRank(b); }
    // 네트워크는 문자열 정렬. 미확인('–')은 코드포인트가 알파벳보다 커서
    // 오름차순에서 자연히 뒤로 밀린다.
    if (key === 'net') { va = ioLegs(a)?.net ?? '–'; vb = ioLegs(b)?.net ?? '–'; }
    if (va == null || vb == null) return va == null ? 1 : -1;
    if (typeof va === 'string') return va < (vb as string) ? -sortDir : va > (vb as string) ? sortDir : 0;
    return ((va as number) - (vb as number)) * sortDir;
  });

  const headers: [string, string, 'left' | 'right'][] = [
    ['sym', '심볼', 'left'], ['krw', '국내가 KRW', 'right'],
    ['fwd', view === 'kimp' ? '김프' : '역프', 'right'], ['io', '입출금', 'right'],
    ['net', '네트워크', 'right'],
  ];
  const onSort = (k: string) => {
    if (k === sortKey) setSortDir(-sortDir);
    else { setSortKey(k); setSortDir(k === 'sym' || k === 'net' ? 1 : -1); }
  };

  const fxAllOn = FXS.every(fx => !fxOff[fx]);
  const okC = 'var(--color-accent-300)', badC = 'var(--color-neutral-600)';
  const unkC = 'var(--color-neutral-500)';
  // 세 상태를 세 모양으로 그린다. **확인 불가를 초록(열림)으로 칠하지 않는다**
  // — 확인 못 한 경로를 옮길 수 있다고 말하는 셈이다. 그렇다고 "중단"과 같게
  // 그리면 반대로 멀쩡한 코인을 막혔다고 말하게 된다.
  const tagStyle = (state: IoState) => ({
    fontSize: 10, padding: '2px 6px', borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap' as const,
    border: state === null
      ? '1px dashed var(--color-neutral-800)'
      : `1px solid ${state ? 'var(--color-accent-800)' : 'var(--color-neutral-800)'}`,
    color: state === null ? unkC : state ? okC : badC,
  });
  // 확인 불가는 '?' 로 — "가능/중단" 어느 쪽으로도 읽히면 안 된다
  const ioLabel = (kind: string, state: IoState) =>
    state === null ? `${kind} ?` : state ? `${kind} 가능` : `${kind} 중단`;
  const exTag = {
    fontSize: 10, letterSpacing: '0.04em', padding: '2px 7px', border: '1px solid var(--color-neutral-800)',
    borderRadius: 'var(--radius-sm)', color: 'var(--color-neutral-400)', background: 'var(--color-surface)', whiteSpace: 'nowrap' as const,
  };

  return (
    <>
      {/* 필터바 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', padding: 'var(--space-3) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap' }}>
        <input className="input" placeholder="심볼 검색" value={q} onChange={e => setQ(e.target.value)}
          style={{ width: 150, fontSize: 12, padding: '5px 10px' }} />
        <span style={{ fontSize: 12, color: 'var(--color-neutral-400)' }}>기준 국내 거래소</span>
        <Seg opts={[['all', '모두'], ['업비트', '업비트'], ['빗썸', '빗썸']].map(([id, label]) => segOpt(label, dom === id, () => setDom(id)))} />
        <NumField label="하이라이트 임계값" value={thrVal} step={0.1} onChange={setThr} />
        <ToggleBtn on={onlyHot} label="임계 초과만" onClick={() => setOnlyHot(!onlyHot)} />
        <ToggleBtn on={onlyIO} label="입출금 가능만" onClick={() => setOnlyIO(!onlyIO)} />
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-neutral-600)', fontVariantNumeric: 'tabular-nums' }}>{list.length} / {agg.length} 코인 표시</span>
        <div style={{ flexBasis: '100%', display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: 'var(--color-neutral-400)' }}>기준 보기</span>
          <Seg pad="4px 10px" opts={[['kimp', '김프 기준'], ['rev', '역프 기준']].map(([id, label]) => segOpt(label, view === id, () => setView(id as 'kimp' | 'rev')))} />
          <span style={{ width: 1, height: 14, background: 'var(--color-neutral-800)' }} />
          <span style={{ fontSize: 12, color: 'var(--color-neutral-400)' }}>가격 기준</span>
          <Seg pad="4px 10px" opts={[['mid', '현재가'], ['slip', '슬리피지 반영']].map(([id, label]) => segOpt(label, basis === id, () => setBasis(id as 'mid' | 'slip')))} />
          {basis === 'slip' && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <span style={{ fontSize: 12, color: 'var(--color-neutral-400)' }}>체결 규모</span>
              <Seg pad="4px 9px" opts={[10000, 50000, 100000, 500000].map(v => segOpt('$' + v / 1000 + 'k', notional === v, () => setNotional(v)))} />
            </span>
          )}
          <span style={{ fontSize: 11, color: 'var(--color-neutral-600)' }}>
            {basis === 'slip' ? '호가창 시장가 체결 기준 · 매수·매도 양측 슬리피지 차감' : '최우선 호가 기준 · 체결 비용 미반영'}
          </span>
          <span style={{ width: 1, height: 14, background: 'var(--color-neutral-800)' }} />
          <span style={{ fontSize: 12, color: 'var(--color-neutral-400)' }}>비교 해외 거래소</span>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer', color: fxAllOn ? 'var(--color-accent-300)' : 'var(--color-neutral-400)' }}>
            <input type="checkbox" checked={fxAllOn}
              onChange={() => setFxOff(fxAllOn ? Object.fromEntries(FXS.map(fx => [fx, true])) : {})}
              style={{ accentColor: 'var(--color-accent)', width: 13, height: 13, cursor: 'pointer' }} />모두
          </label>
          <span style={{ width: 1, height: 14, background: 'var(--color-neutral-800)' }} />
          {FXS.map(fx => (
            <label key={fx} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer', color: fxOff[fx] ? 'var(--color-neutral-600)' : 'var(--color-neutral-300)' }}>
              <input type="checkbox" checked={!fxOff[fx]} onChange={() => setFxOff({ ...fxOff, [fx]: !fxOff[fx] })}
                style={{ accentColor: 'var(--color-accent)', width: 13, height: 13, cursor: 'pointer' }} />{fx}
            </label>
          ))}
        </div>
      </div>

      {/* 테이블 */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', justifyContent: 'center', padding: '0 var(--space-6)' }}>
        <div style={{ minWidth: 820, maxWidth: 1080, flex: 1, overflow: 'auto', borderLeft: '1px solid var(--color-divider)', borderRight: '1px solid var(--color-divider)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: GRID, position: 'sticky', top: 0, zIndex: 2, background: 'var(--color-bg)', borderBottom: '1px solid var(--color-neutral-800)', padding: '0 var(--space-6)' }}>
            {headers.map(([k, label, align]) => (
              <button key={k} onClick={() => onSort(k)} className="hv-txt"
                style={{
                  appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 10.5,
                  letterSpacing: '0.07em', textTransform: 'uppercase', padding: '8px 8px', cursor: 'pointer',
                  textAlign: align, whiteSpace: 'nowrap',
                  color: k === sortKey ? 'var(--color-accent-300)' : 'var(--color-neutral-600)',
                }}>
                {label}{k === sortKey ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}
              </button>
            ))}
          </div>
          {!feed.spreads.length && (
            <div style={{ padding: 'var(--space-8) var(--space-6)', textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 13 }}>
              백엔드에서 스프레드를 받는 중입니다…
            </div>
          )}
          {!!feed.spreads.length && !list.length && (
            <div style={{ padding: 'var(--space-8) var(--space-6)', textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 13 }}>
              조건에 맞는 코인이 없습니다. 필터를 넓혀 보세요.
            </div>
          )}
          {list.map(c => {
            const fail = c.failAll, stale = c.staleAll;
            const best = view === 'kimp' ? c.bestF : c.bestR;
            const val = best ? (view === 'kimp' ? best.fwd : best.rev) : null;
            const hot = !fail && !stale && (val ?? -99) >= thrVal;
            const krw = c.usd != null ? c.usd * feed.rate * (1 + ((c.fwdRaw ?? c.fwd) ?? 0) / 100) : null;
            const leg = ioLegs(c);
            return (
              <div key={c.sym} onClick={() => onPivot(c.sym)} className="hv-row"
                style={{
                  display: 'grid', gridTemplateColumns: GRID, alignItems: 'center', padding: '0 var(--space-6)',
                  height: 40, borderBottom: '1px solid color-mix(in srgb, #e9e9ed 7%, transparent)', cursor: 'pointer',
                  background: hot ? 'color-mix(in srgb, var(--color-accent) 8%, transparent)' : 'transparent',
                  opacity: stale ? 0.45 : 1,
                }}>
                <div style={{ padding: '0 8px', display: 'flex', alignItems: 'center', gap: 6, fontWeight: 500, fontSize: 13.5, color: hot ? 'var(--color-accent-300)' : 'var(--color-text)' }}>
                  <span style={{ width: 4, height: 4, borderRadius: '50%', background: hot ? 'var(--color-accent)' : 'transparent', flex: 'none' }} />{c.sym}
                </div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                  {fail || krw == null ? '–' : '₩' + fmtKRW(krw)}
                </div>
                <div style={{ padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8 }}>
                  <span style={exTag}>{best ? (view === 'kimp' ? best.fx : best.dom) : '–'}</span>
                  <span style={{ fontSize: 10, color: 'var(--color-neutral-600)' }}>→</span>
                  <span style={exTag}>{best ? (view === 'kimp' ? best.dom : best.fx) : '–'}</span>
                  <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)', whiteSpace: 'nowrap' }}>
                    {basis === 'slip' && best?.slip != null ? '슬 −' + best.slip.toFixed(2) + '%p' : ''}
                  </span>
                  <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minWidth: 72, textAlign: 'right', color: val != null ? pctColor(val) : 'var(--color-neutral-700)' }}>
                    {val != null ? fmtPct(val) : '–'}
                  </span>
                </div>
                <div style={{ padding: '0 8px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 5 }}>
                  <span style={tagStyle(leg ? leg.wd : null)}>{leg ? ioLabel('출금', leg.wd) : '출금 ?'}</span>
                  <span style={tagStyle(leg ? leg.dep : null)}>{leg ? ioLabel('입금', leg.dep) : '입금 ?'}</span>
                </div>
                <div style={{ padding: '0 8px', textAlign: 'right', fontSize: 11, color: 'var(--color-neutral-500)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {leg ? leg.net : '–'}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
