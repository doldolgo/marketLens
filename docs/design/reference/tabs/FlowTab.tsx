// 입출금 레이더 탭 — 거래소 입출금 흐름을 코인/주소 기준으로 피벗하며 탐색
import { useState, type CSSProperties, type KeyboardEvent } from 'react';
import type { MockFeed } from '../data/mockFeed';
import type { FlowRow } from '../data/types';
import { fmtAgo, fmtQty, fmtUsd } from '../lib/format';
import { kicker, Seg, segOpt } from '../components/ui';

// 드릴다운 스택의 한 단계: 코인 뷰 또는 주소 뷰
interface NavItem { kind: 'coin' | 'addr'; id: string }

const DOM: Record<string, 1> = { '업비트': 1, '빗썸': 1 };
const shortOf = (ad: string) => ad.slice(0, 6) + '…' + ad.slice(-4);

// 세 가지 뷰(전체/코인/주소)의 테이블 컬럼 구성 — 코인 뷰는 코인 열 생략, 주소 뷰는 주소 열 생략
const GRIDS = {
  base: { cols: '96px 60px 74px 1fr 148px 132px 96px 108px', min: 1000 },
  coin: { cols: '96px 60px 1fr 148px 132px 96px 108px', min: 940 },
  addr: { cols: '96px 60px 84px 1fr 148px 96px 108px', min: 900 },
};

const cell = (extra: CSSProperties = {}): CSSProperties => ({ padding: '0 8px', ...extra });
const headCell: CSSProperties = { padding: '6px 8px' };
const card: CSSProperties = { background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-sm)' };

export default function FlowTab({ feed }: { feed: MockFeed }) {
  const [nav, setNav] = useState<NavItem[]>([]);
  const [q, setQ] = useState('');
  const [noMatch, setNoMatch] = useState(false);
  const [dir, setDir] = useState<'all' | 'in' | 'out'>('all');
  const [region, setRegion] = useState<'all' | 'ovs' | 'dom'>('all');
  const [exSel, setExSel] = useState<Record<string, boolean>>({});

  const all = feed.flow;
  const cur = nav.length ? nav[nav.length - 1] : null;
  const goCoin = (sym: string) => { setNav(n => [...n, { kind: 'coin', id: sym }]); setQ(''); setNoMatch(false); };
  const goAddr = (ad: string) => { setNav(n => [...n, { kind: 'addr', id: ad }]); setQ(''); setNoMatch(false); };

  // 현재 드릴다운 범위 → 방향/권역/거래소 필터 적용
  const scope = cur ? all.filter(r => cur.kind === 'coin' ? r.coin === cur.id : r.addr === cur.id) : all;
  const exOn = Object.keys(exSel).filter(k => exSel[k]);
  const list = scope.filter(r =>
    (dir === 'all' || r.dir === dir) &&
    (region === 'all' || (region === 'dom' ? !!DOM[r.ex] : !DOM[r.ex])) &&
    (exOn.length === 0 || exOn.includes(r.ex)));

  // 검색어를 코인 심볼 또는 주소/라벨로 해석
  const resolve = (raw: string): NavItem | null => {
    const t = raw.trim(); if (!t) return null;
    const up = t.toUpperCase();
    if (feed.flowPx[up] != null) return { kind: 'coin', id: up };
    const lo = t.toLowerCase();
    const a = feed.flowAddrs.find(x => x.id.toLowerCase().includes(lo) || x.label.toLowerCase().includes(lo));
    return a ? { kind: 'addr', id: a.id } : null;
  };
  const onSearchKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return;
    const hit = resolve((e.target as HTMLInputElement).value || '');
    if (hit) { setNav([...nav, hit]); setQ(''); setNoMatch(false); }
    else setNoMatch(true);
  };

  // 코인별 건수 칩 (베이스 뷰)
  const cnt: Record<string, number> = {};
  all.forEach(r => { cnt[r.coin] = (cnt[r.coin] || 0) + 1; });
  const chips = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]);

  // 요약 스트립
  const sumU = (arr: FlowRow[]) => arr.reduce((t, r) => t + (r.usd || 0), 0);
  const ovsOut = list.filter(r => !DOM[r.ex] && r.dir === 'out');
  const domIn = list.filter(r => DOM[r.ex] && r.dir === 'in');
  const route = [
    { label: '해외 출금', value: `${ovsOut.length}건 · ${fmtUsd(sumU(ovsOut))}`, color: 'var(--color-text)' },
    { label: '국내 입금', value: `${domIn.length}건 · ${fmtUsd(sumU(domIn))}`, color: 'var(--color-accent-300)' },
    { label: '입금 / 출금', value: `${list.filter(r => r.dir === 'in').length} / ${list.filter(r => r.dir === 'out').length}건`, color: 'var(--color-neutral-300)' },
    { label: '표시 총액', value: fmtUsd(sumU(list)), color: 'var(--color-neutral-300)' },
  ];

  // 거래소 필터 칩 — 국내 우선, 건수순
  const exCnt: Record<string, number> = {};
  scope.forEach(r => { exCnt[r.ex] = (exCnt[r.ex] || 0) + 1; });
  const exChips = Object.keys(exCnt).sort((a, b) => ((DOM[b] ? 1 : 0) - (DOM[a] ? 1 : 0)) || exCnt[b] - exCnt[a]);
  const filtered = dir !== 'all' || region !== 'all' || exOn.length > 0;

  const crumbs = [{ label: '전체', color: nav.length ? 'var(--color-neutral-400)' : 'var(--color-text)', sep: nav.length ? '→' : '', onClick: () => setNav([]) }]
    .concat(nav.map((n, i) => ({
      label: n.kind === 'coin' ? n.id : shortOf(n.id),
      color: i === nav.length - 1 ? 'var(--color-accent-300)' : 'var(--color-neutral-400)',
      sep: i === nav.length - 1 ? '' : '→',
      onClick: () => setNav(nav.slice(0, i + 1)),
    })));

  const viewKind = cur ? cur.kind : 'base';
  const grid = GRIDS[viewKind];

  // 테이블 행 공통 셀 렌더러
  const dirTag = (r: FlowRow) => (
    <span style={{
      justifySelf: 'start', margin: '0 8px', fontSize: 10, padding: '2px 7px', borderRadius: 'var(--radius-sm)',
      border: `1px solid ${r.dir === 'in' ? 'var(--color-accent-800)' : 'var(--color-neutral-800)'}`,
      color: r.dir === 'in' ? 'var(--color-accent-300)' : 'var(--color-neutral-400)',
    }}>{r.dir === 'in' ? '입금' : '출금'}</span>
  );
  const coinBtn = (r: FlowRow) => (
    <button onClick={() => goCoin(r.coin)} className="hv-txt"
      style={{ justifySelf: 'start', margin: '0 8px', appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 12.5, fontWeight: 500, padding: 0, cursor: 'pointer', color: 'var(--color-text)' }}>
      {r.coin}
    </button>
  );
  const addrBtn = (r: FlowRow) => (
    <button onClick={() => goAddr(r.addr)} className="hv-txt"
      style={{ justifySelf: 'start', margin: '0 8px', appearance: 'none', background: 'none', border: 'none', font: 'inherit', padding: 0, cursor: 'pointer', display: 'inline-flex', alignItems: 'baseline', gap: 8, textAlign: 'left', color: 'var(--color-text)' }}>
      <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>{r.short}</span>
      <span style={{ fontSize: 10.5, color: 'var(--color-neutral-500)' }}>{r.label}</span>
    </button>
  );
  const timeCell = (r: FlowRow) => (
    <span style={{ padding: '0 8px 0 0', fontSize: 11.5, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)' }}>{fmtAgo(r.age)}</span>
  );
  const qtyCell = (r: FlowRow) => (
    <span style={cell({ textAlign: 'right', fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' })}>{fmtQty(r.qty)}</span>
  );
  const usdCell = (r: FlowRow) => (
    <span style={cell({
      textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12.5,
      fontWeight: r.usd != null && r.usd >= 1e6 ? 600 : 400,
      color: r.usd == null ? 'var(--color-neutral-600)' : r.usd >= 1e6 ? 'var(--color-accent-300)' : 'var(--color-text)',
    })}>{fmtUsd(r.usd)}</span>
  );
  const exCell = (r: FlowRow) => (
    <span style={cell({ fontSize: 11.5, color: 'var(--color-neutral-300)' })}>{r.ex}</span>
  );
  const stTag = (r: FlowRow) => {
    const done = r.status === 'sweep 확정' || r.status === '확정';
    return (
      <span style={{
        justifySelf: 'start', margin: '0 8px', fontSize: 10, padding: '2px 7px', borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap',
        border: `1px solid ${done ? 'var(--color-neutral-800)' : 'var(--color-accent-800)'}`,
        color: done ? 'var(--color-neutral-300)' : 'var(--color-accent-300)',
      }}>{r.status}</span>
    );
  };

  // 코인 뷰 요약 카드용 집계
  const ins = scope.filter(r => r.dir === 'in'), outs = scope.filter(r => r.dir === 'out');
  const scopeExCnt: Record<string, number> = {};
  scope.forEach(r => { scopeExCnt[r.ex] = (scopeExCnt[r.ex] || 0) + 1; });
  const scopeExOrder = Object.keys(scopeExCnt).sort((a, b) => scopeExCnt[b] - scopeExCnt[a]);
  const maxExCnt = Math.max(1, ...Object.values(scopeExCnt));
  // 주소 뷰 요약 카드용 집계
  const addrInfo = cur?.kind === 'addr' ? (feed.flowAddrs.find(x => x.id === cur.id) ?? { label: '–' }) : null;
  const scopeCoinCnt: Record<string, number> = {};
  scope.forEach(r => { scopeCoinCnt[r.coin] = (scopeCoinCnt[r.coin] || 0) + 1; });

  return (
    <>
      {/* 검색 + 방향 필터 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', padding: 'var(--space-3) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap' }}>
        <input className="input" placeholder="코인 심볼 또는 주소 검색 후 Enter" value={q}
          onChange={e => { setQ(e.target.value); setNoMatch(false); }} onKeyDown={onSearchKey}
          style={{ width: 270, fontSize: 12, padding: '5px 10px' }} />
        <Seg opts={[['all', '전체'], ['in', '입금'], ['out', '출금']].map(([id, label]) => segOpt(label, dir === id, () => setDir(id as typeof dir)))} />
        {noMatch && <span style={{ fontSize: 11.5, color: 'var(--color-neutral-500)' }}>일치하는 코인·주소 없음</span>}
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-neutral-600)', fontVariantNumeric: 'tabular-nums' }}>{list.length} / {all.length}건 표시</span>
      </div>

      {/* 권역 + 거래소 필터 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', padding: 'var(--space-2) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap' }}>
        <span style={kicker}>권역</span>
        <Seg pad="4px 11px" opts={[['all', '전체'], ['ovs', '해외'], ['dom', '국내']].map(([id, label]) => segOpt(label, region === id, () => setRegion(id as typeof region)))} />
        <span style={{ ...kicker, marginLeft: 'var(--space-2)' }}>거래소</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          {exChips.map(ex => {
            const on = !!exSel[ex];
            return (
              <button key={ex} onClick={() => setExSel({ ...exSel, [ex]: !on })} className="hv-bd"
                style={{
                  appearance: 'none', font: 'inherit', display: 'inline-flex', alignItems: 'baseline', gap: 6,
                  fontSize: 11.5, padding: '3px 10px', cursor: 'pointer', borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${on ? 'var(--color-accent)' : 'var(--color-neutral-800)'}`,
                  background: on ? 'var(--color-neutral-900)' : 'transparent',
                  color: on ? 'var(--color-accent-300)' : 'var(--color-neutral-300)',
                }}>
                {ex}<span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)' }}>{exCnt[ex]}건</span>
              </button>
            );
          })}
        </div>
        {filtered && (
          <button onClick={() => { setDir('all'); setRegion('all'); setExSel({}); }} className="hv-txt"
            style={{ appearance: 'none', font: 'inherit', background: 'none', border: 'none', fontSize: 11.5, padding: 0, cursor: 'pointer', color: 'var(--color-neutral-500)' }}>
            필터 초기화
          </button>
        )}
      </div>

      {/* 요약 스트립 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-6)', padding: 'var(--space-3) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap' }}>
        {route.map(k => (
          <span key={k.label} style={{ display: 'inline-flex', flexDirection: 'column', gap: 1 }}>
            <span style={kicker}>{k.label}</span>
            <span style={{ fontSize: 14, fontVariantNumeric: 'tabular-nums', color: k.color }}>{k.value}</span>
          </span>
        ))}
      </div>

      {/* 브레드크럼 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-2) var(--space-6)', borderBottom: '1px solid var(--color-divider)', flex: 'none', flexWrap: 'wrap' }}>
        {nav.length > 0 && (
          <button onClick={() => setNav(nav.slice(0, -1))} className="hv-bd-txt"
            style={{ appearance: 'none', font: 'inherit', fontSize: 11.5, padding: '3px 10px', cursor: 'pointer', borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-neutral-800)', background: 'transparent', color: 'var(--color-neutral-300)' }}>
            ← 뒤로
          </button>
        )}
        {crumbs.map((c, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <button onClick={c.onClick} className="hv-txt"
              style={{ appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 12, padding: 0, cursor: 'pointer', color: c.color }}>
              {c.label}
            </button>
            <span style={{ fontSize: 11, color: 'var(--color-neutral-700)' }}>{c.sep}</span>
          </span>
        ))}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--color-neutral-600)' }}>행의 주소·코인을 클릭하면 그 기준으로 피벗</span>
      </div>

      {/* 본문 */}
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        <div style={{ maxWidth: 1240, margin: '0 auto', padding: 'var(--space-4) var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>

          {viewKind === 'base' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
              <span style={kicker}>코인별 입출금 건수 — 클릭해 코인 뷰로</span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
                {chips.map(sym => (
                  <button key={sym} onClick={() => goCoin(sym)} className="hv-bd-txt"
                    style={{ appearance: 'none', font: 'inherit', display: 'inline-flex', alignItems: 'baseline', gap: 7, fontSize: 12.5, padding: '5px 12px', cursor: 'pointer', borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-neutral-800)', background: 'var(--color-surface)', color: 'var(--color-text)' }}>
                    {sym}<span style={{ fontSize: 11, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)' }}>{cnt[sym]}건</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {viewKind === 'coin' && cur && (
            <div style={{ ...card, padding: 'var(--space-4) var(--space-6)', display: 'flex', gap: 'var(--space-8)', flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div style={{ minWidth: 150 }}>
                <div style={{ ...kicker, marginBottom: 3 }}>코인</div>
                <div style={{ fontFamily: 'var(--font-heading)', fontSize: 24, fontWeight: 500 }}>{cur.id}</div>
                <div style={{ fontSize: 11.5, color: 'var(--color-neutral-500)', fontVariantNumeric: 'tabular-nums' }}>{scope.length}건 · {fmtUsd(sumU(scope))}</div>
              </div>
              <div>
                <div style={{ ...kicker, marginBottom: 3 }}>총 입금</div>
                <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums', color: 'var(--color-accent-300)' }}>{ins.length}건</div>
                <div style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-400)' }}>{fmtUsd(sumU(ins))}</div>
              </div>
              <div>
                <div style={{ ...kicker, marginBottom: 3 }}>총 출금</div>
                <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{outs.length}건</div>
                <div style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-400)' }}>{fmtUsd(sumU(outs))}</div>
              </div>
              <div style={{ flex: 1, minWidth: 260 }}>
                <div style={{ ...kicker, marginBottom: 6 }}>거래소별 분포</div>
                <div style={{ display: 'grid', gridTemplateColumns: '74px 1fr 44px', gap: '5px 10px', alignItems: 'center' }}>
                  {scopeExOrder.map(ex => (
                    <span key={ex} style={{ display: 'contents' }}>
                      <span style={{ fontSize: 11.5, color: 'var(--color-neutral-400)' }}>{ex}</span>
                      <span style={{ position: 'relative', height: 8, background: 'var(--color-bg)', borderRadius: 4, overflow: 'hidden' }}>
                        <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: (scopeExCnt[ex] / maxExCnt * 100).toFixed(0) + '%', background: 'var(--color-accent-500)', borderRadius: 4 }} />
                      </span>
                      <span style={{ fontSize: 11, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)', textAlign: 'right' }}>{scopeExCnt[ex]}건</span>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {viewKind === 'addr' && cur && addrInfo && (
            <div style={{ ...card, padding: 'var(--space-4) var(--space-6)', display: 'flex', gap: 'var(--space-8)', flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div style={{ minWidth: 190 }}>
                <div style={{ ...kicker, marginBottom: 3 }}>주소</div>
                <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: 16 }}>{shortOf(cur.id)}</div>
                <div style={{ fontSize: 11.5, color: 'var(--color-accent-300)', marginTop: 2 }}>{addrInfo.label}</div>
              </div>
              <div>
                <div style={{ ...kicker, marginBottom: 3 }}>총 거래</div>
                <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{scope.length}건</div>
                <div style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-400)' }}>입금 {ins.length} · 출금 {scope.length - ins.length}</div>
              </div>
              <div>
                <div style={{ ...kicker, marginBottom: 3 }}>총액</div>
                <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{fmtUsd(sumU(scope))}</div>
              </div>
              <div>
                <div style={{ ...kicker, marginBottom: 3 }}>주 이용 거래소</div>
                <div style={{ fontSize: 17 }}>{scopeExOrder[0] || '–'}</div>
                <div style={{ fontSize: 12, color: 'var(--color-neutral-400)' }}>
                  {scopeExOrder.length > 1 ? `외 ${scopeExOrder.length - 1}곳 · ${scopeExOrder.slice(1).join(', ')}` : '단일 거래소'}
                </div>
              </div>
              <div style={{ flex: 1, minWidth: 220 }}>
                <div style={{ ...kicker, marginBottom: 6 }}>다룬 코인 — 클릭해 코인 뷰로</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {Object.keys(scopeCoinCnt).sort((a, b) => scopeCoinCnt[b] - scopeCoinCnt[a]).map(sym => (
                    <button key={sym} onClick={() => goCoin(sym)} className="hv-bd-txt"
                      style={{ appearance: 'none', font: 'inherit', display: 'inline-flex', alignItems: 'baseline', gap: 6, fontSize: 12, padding: '3px 10px', cursor: 'pointer', borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-neutral-800)', background: 'var(--color-bg)', color: 'var(--color-text)' }}>
                      {sym}<span style={{ fontSize: 10.5, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-500)' }}>{scopeCoinCnt[sym]}건</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* 입출금 테이블 */}
          <div style={{ ...card, padding: 'var(--space-2) 0' }}>
            <div style={{ fontSize: 10, letterSpacing: '0.04em', color: 'var(--color-neutral-600)', padding: 'var(--space-4) var(--space-6) var(--space-2)' }}>
              {cur ? (cur.kind === 'coin' ? `${cur.id} 최근 입출금` : `${shortOf(cur.id)} 전체 입출금 · 코인 무관`) : '전체 최근 입출금 · 최신순'}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: grid.min }}>
                <div style={{ display: 'grid', gridTemplateColumns: grid.cols, padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)', fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--color-neutral-600)' }}>
                  <span style={{ padding: '6px 8px 6px 0' }}>시각</span>
                  <span style={headCell}>방향</span>
                  {viewKind !== 'coin' && <span style={headCell}>코인</span>}
                  {viewKind !== 'addr' && <span style={headCell}>주소</span>}
                  <span style={{ ...headCell, textAlign: 'right' }}>수량</span>
                  <span style={{ ...headCell, textAlign: 'right' }}>USD</span>
                  <span style={headCell}>거래소</span>
                  <span style={headCell}>상태</span>
                </div>
                {list.map((r, i) => (
                  <div key={i} className="hv-row4"
                    style={{ display: 'grid', gridTemplateColumns: grid.cols, alignItems: 'center', height: 38, padding: '0 var(--space-6)', borderBottom: '1px solid color-mix(in srgb, #e9e9ed 6%, transparent)' }}>
                    {timeCell(r)}
                    {dirTag(r)}
                    {viewKind !== 'coin' && coinBtn(r)}
                    {viewKind !== 'addr' && addrBtn(r)}
                    {qtyCell(r)}
                    {usdCell(r)}
                    {exCell(r)}
                    {stTag(r)}
                  </div>
                ))}
              </div>
            </div>
            {list.length === 0 && (
              <div style={{ padding: 'var(--space-8) var(--space-6)', textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 12 }}>해당 조건의 입출금 내역 없음</div>
            )}
          </div>

        </div>
      </div>

      {/* 레이더 전용 푸터 */}
      <div style={{ flex: 'none', display: 'flex', gap: 'var(--space-6)', padding: 'var(--space-2) var(--space-6)', borderTop: '1px solid var(--color-divider)', fontSize: 11, color: 'var(--color-neutral-600)', flexWrap: 'wrap' }}>
        <span>주소 + entity 라벨까지 — 온체인상 개인 신원은 확인 불가</span>
        <span style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}>⚠️ 시현용 mock 데이터 · 실제 온체인 연동 아님</span>
      </div>
    </>
  );
}
