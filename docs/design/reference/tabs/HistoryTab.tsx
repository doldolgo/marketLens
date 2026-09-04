// 기록/통계 탭 — 과거 스프레드 사건(임계 초과 구간)의 티커별 통계와 타임라인
import { useState, type CSSProperties } from 'react';
import type { MockFeed } from '../data/mockFeed';
import { POS, NEG } from '../config';
import { fmtPct, fmtDur, fmtDT, fmtD, fmtRel } from '../lib/format';
import { kicker, Seg, segOpt, NumField } from '../components/ui';

type Period = '1주' | '1달' | '3달';
const SPAN_MS: Record<Period, number> = { '1주': 6048e5, '1달': 2592e6, '3달': 7776e6 };

interface Rank {
  sym: string; cnt: number;
  maxDur: number; avgDur: number;
  maxK: number | null; avgK: number | null;
  maxR: number | null; avgR: number | null;
  last: number;
}

const card: CSSProperties = { background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-sm)' };

export default function HistoryTab({ feed, now, hSym, onSelect }: {
  feed: MockFeed; now: number; hSym: string; onSelect: (sym: string) => void;
}) {
  const [per, setPer] = useState<Period>('1달');
  const [type, setType] = useState<'all' | 'kimp' | 'rev'>('all');
  const [evDom, setEvDom] = useState('all');
  const [evThr, setEvThr] = useState(1.0);
  const [sort, setSort] = useState<keyof Rank>('cnt');
  const [dir, setDir] = useState(-1);

  const spanMs = SPAN_MS[per];
  const events = feed.events(per, now);
  const filt = events.filter(e => e.peak >= evThr && (type === 'all' || e.type === type) && (evDom === 'all' || e.dom === evDom));

  // 티커별 랭킹 집계
  const bySym = new Map<string, typeof filt>();
  filt.forEach(e => { if (!bySym.has(e.sym)) bySym.set(e.sym, []); bySym.get(e.sym)!.push(e); });
  const rank: Rank[] = [...bySym.entries()].map(([sy, es]) => {
    const ks = es.filter(e => e.type === 'kimp'), rs = es.filter(e => e.type === 'rev');
    const ds = es.map(e => e.durMin);
    return {
      sym: sy, cnt: es.length,
      maxDur: Math.max(...ds), avgDur: ds.reduce((a, b) => a + b, 0) / ds.length,
      maxK: ks.length ? Math.max(...ks.map(e => e.peak)) : null,
      avgK: ks.length ? ks.reduce((a, b) => a + b.peak, 0) / ks.length : null,
      maxR: rs.length ? Math.max(...rs.map(e => e.peak)) : null,
      avgR: rs.length ? rs.reduce((a, b) => a + b.peak, 0) / rs.length : null,
      last: Math.max(...es.map(e => e.start)),
    };
  });
  rank.sort((a, b) => {
    const va = a[sort] as number | null, vb = b[sort] as number | null;
    if (va == null || vb == null) return va == null ? 1 : -1;
    return (va - vb) * dir;
  });

  const evHdrs: [keyof Rank, string][] = [
    ['cnt', '횟수'], ['maxDur', '최대 지속'], ['avgDur', '평균 지속'],
    ['maxK', '최대 김프'], ['avgK', '평균 김프'], ['maxR', '최대 역프'], ['avgR', '평균 역프'], ['last', '최신'],
  ];
  const onSort = (k: keyof Rank) => {
    if (k === sort) setDir(-dir);
    else { setSort(k); setDir(-1); }
  };

  // 선택 티커의 사건 목록·타임라인
  const evs = filt.filter(e => e.sym === hSym).sort((a, b) => b.start - a.start);
  const kEvs = evs.filter(e => e.type === 'kimp'), rEvs = evs.filter(e => e.type === 'rev');
  const durs = evs.map(e => e.durMin);
  const sumDur = durs.reduce((a, b) => a + b, 0);
  const t0 = now - spanMs;
  const mkBar = (e: typeof evs[number]) => ({
    left: ((e.start - t0) / spanMs * 100).toFixed(2) + '%',
    width: Math.max(0.4, e.durMin * 60e3 / spanMs * 100).toFixed(2) + '%',
    title: (e.type === 'kimp' ? '김프' : '역프') + ' · ' + fmtDT(e.start) + ' 시작 · ' + fmtDur(e.durMin) + ' 지속 · 최대 ' + fmtPct(e.peak),
  });

  const rankCell: CSSProperties = { fontSize: 11.5, fontVariantNumeric: 'tabular-nums', textAlign: 'right', padding: '0 4px' };

  return (
    <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
      <div style={{ padding: 'var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>

        {/* 필터바 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <Seg opts={(['1주', '1달', '3달'] as Period[]).map(pp => segOpt(pp, per === pp, () => setPer(pp)))} />
          <Seg opts={[['all', '전체'], ['kimp', '김프만'], ['rev', '역프만']].map(([id, label]) => segOpt(label, type === id, () => setType(id as typeof type)))} />
          <Seg opts={[['all', '전체'], ['업비트', '업비트'], ['빗썸', '빗썸']].map(([id, label]) => segOpt(label, evDom === id, () => setEvDom(id)))} />
          <NumField label="사건 기준 스프레드 ≥" value={evThr} step={0.1} onChange={setEvThr} />
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--color-neutral-600)' }}>사건 = 스프레드가 기준값 이상으로 출현한 시점부터 소멸까지 · 기간 내 {filt.length}건</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 'var(--space-4)', alignItems: 'start' }}>

          {/* 티커별 사건 통계 */}
          <div style={{ ...card, padding: 'var(--space-4) 0' }}>
            <div style={{ ...kicker, padding: '0 var(--space-6) var(--space-2)' }}>티커별 사건 통계 · {per} — 열 클릭으로 정렬</div>
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: 720 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '64px repeat(8, 1fr)', padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)' }}>
                  <span style={{ fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--color-neutral-600)', padding: '7px 0' }}>티커</span>
                  {evHdrs.map(([k, label]) => (
                    <button key={k} onClick={() => onSort(k)} className="hv-txt"
                      style={{
                        appearance: 'none', background: 'none', border: 'none', font: 'inherit', fontSize: 10.5,
                        letterSpacing: '0.04em', textTransform: 'uppercase', padding: '7px 4px', cursor: 'pointer',
                        textAlign: 'right', whiteSpace: 'nowrap',
                        color: k === sort ? 'var(--color-accent-300)' : 'var(--color-neutral-600)',
                      }}>
                      {label}{k === sort ? (dir < 0 ? ' ▾' : ' ▴') : ''}
                    </button>
                  ))}
                </div>
                {rank.slice(0, 30).map(x => (
                  <button key={x.sym} onClick={() => onSelect(x.sym)} className="hv-row"
                    style={{
                      display: 'grid', gridTemplateColumns: '64px repeat(8, 1fr)', alignItems: 'center', width: '100%',
                      appearance: 'none', border: 'none', font: 'inherit',
                      background: x.sym === hSym ? 'color-mix(in srgb, var(--color-accent) 10%, transparent)' : 'transparent',
                      padding: '0 var(--space-6)', height: 30, cursor: 'pointer', textAlign: 'left',
                      borderBottom: '1px solid color-mix(in srgb, #e9e9ed 5%, transparent)',
                    }}>
                    <span style={{ fontWeight: 500, fontSize: 12.5, color: x.sym === hSym ? 'var(--color-accent-300)' : 'var(--color-text)' }}>{x.sym}</span>
                    <span style={rankCell}>{x.cnt}</span>
                    <span style={{ ...rankCell, color: 'var(--color-neutral-300)' }}>{fmtDur(x.maxDur)}</span>
                    <span style={{ ...rankCell, color: 'var(--color-neutral-300)' }}>{fmtDur(x.avgDur)}</span>
                    <span style={{ ...rankCell, color: x.maxK != null ? POS : 'var(--color-neutral-700)' }}>{x.maxK != null ? fmtPct(x.maxK) : '–'}</span>
                    <span style={{ ...rankCell, color: x.maxK != null ? POS : 'var(--color-neutral-700)' }}>{x.avgK != null ? fmtPct(x.avgK) : '–'}</span>
                    <span style={{ ...rankCell, color: x.maxR != null ? NEG : 'var(--color-neutral-700)' }}>{x.maxR != null ? fmtPct(x.maxR) : '–'}</span>
                    <span style={{ ...rankCell, color: x.maxR != null ? NEG : 'var(--color-neutral-700)' }}>{x.avgR != null ? fmtPct(x.avgR) : '–'}</span>
                    <span style={{ ...rankCell, fontSize: 11, color: 'var(--color-neutral-500)' }}>{fmtRel(now - x.last)}</span>
                  </button>
                ))}
              </div>
            </div>
            {rank.length === 0 && (
              <div style={{ padding: 'var(--space-8) var(--space-6)', textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 12 }}>기준을 만족하는 사건이 없습니다 — 임계값을 낮춰보세요</div>
            )}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            {/* 선택 티커 요약 + 타임라인 */}
            <div style={{ ...card, padding: 'var(--space-6)' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
                <span style={{ fontFamily: 'var(--font-heading)', fontSize: 22, fontWeight: 500 }}>{hSym}</span>
                <span style={{ fontSize: 11, color: 'var(--color-neutral-600)' }}>왼쪽 목록에서 티커를 클릭해 선택</span>
              </div>
              <div style={{ display: 'flex', gap: 'var(--space-8)', flexWrap: 'wrap', marginBottom: 'var(--space-6)' }}>
                <div>
                  <div style={{ ...kicker, marginBottom: 2 }}>총 사건</div>
                  <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{evs.length}건 <span style={{ fontSize: 11, color: 'var(--color-neutral-500)' }}>김프 {kEvs.length} · 역프 {rEvs.length}</span></div>
                </div>
                <div>
                  <div style={{ ...kicker, marginBottom: 2 }}>평균 지속</div>
                  <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{durs.length ? fmtDur(sumDur / durs.length) : '–'}</div>
                </div>
                <div>
                  <div style={{ ...kicker, marginBottom: 2 }}>최장 지속</div>
                  <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{durs.length ? fmtDur(Math.max(...durs)) : '–'}</div>
                </div>
                <div>
                  <div style={{ ...kicker, marginBottom: 2 }}>기간 점유율</div>
                  <div style={{ fontSize: 17, fontVariantNumeric: 'tabular-nums' }}>{durs.length ? (sumDur * 60e3 / spanMs * 100).toFixed(1) + '%' : '–'}</div>
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '40px 1fr', gap: '6px 10px', alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: 'var(--color-accent-300)' }}>김프</span>
                <div style={{ position: 'relative', height: 20, background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
                  {kEvs.map((e, i) => { const b = mkBar(e); return <span key={i} title={b.title} style={{ position: 'absolute', top: 3, bottom: 3, left: b.left, width: b.width, minWidth: 2, background: 'var(--color-accent-500)', borderRadius: 2 }} />; })}
                </div>
                <span style={{ fontSize: 11, color: 'var(--color-neutral-400)' }}>역프</span>
                <div style={{ position: 'relative', height: 20, background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
                  {rEvs.map((e, i) => { const b = mkBar(e); return <span key={i} title={b.title} style={{ position: 'absolute', top: 3, bottom: 3, left: b.left, width: b.width, minWidth: 2, background: 'var(--color-neutral-600)', borderRadius: 2 }} />; })}
                </div>
                <span />
                <div style={{ position: 'relative', height: 14 }}>
                  {[0, 0.25, 0.5, 0.75, 1].map(f => (
                    <span key={f} style={{ position: 'absolute', left: (f * 100).toFixed(0) + '%', transform: 'translateX(-50%)', fontSize: 10, fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-600)' }}>{fmtD(t0 + f * spanMs)}</span>
                  ))}
                </div>
              </div>
            </div>

            {/* 사건 로그 */}
            <div style={{ ...card, padding: 'var(--space-2) 0' }}>
              <div style={{ ...kicker, padding: 'var(--space-4) var(--space-6) var(--space-2)' }}>사건 로그 · {hSym} 최근 20건</div>
              <div style={{ display: 'grid', gridTemplateColumns: '70px 1fr 1fr 110px 120px', padding: '0 var(--space-6)', borderBottom: '1px solid var(--color-neutral-800)', fontSize: 10.5, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--color-neutral-600)' }}>
                <span style={{ padding: '6px 8px 6px 0' }}>유형</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>시작</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>종료</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>지속시간</span>
                <span style={{ padding: '6px 8px', textAlign: 'right' }}>최대 스프레드</span>
              </div>
              {evs.slice(0, 20).map((e, i) => {
                const endT = e.start + e.durMin * 60e3;
                const ongoing = endT > now;
                return (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: '70px 1fr 1fr 110px 120px', alignItems: 'center', height: 32, padding: '0 var(--space-6)', borderBottom: '1px solid color-mix(in srgb, #e9e9ed 6%, transparent)' }}>
                    <span style={{
                      justifySelf: 'start', fontSize: 10, padding: '2px 7px', borderRadius: 'var(--radius-sm)',
                      border: `1px solid ${e.type === 'kimp' ? 'var(--color-accent-700)' : 'var(--color-neutral-700)'}`,
                      color: e.type === 'kimp' ? 'var(--color-accent-300)' : 'var(--color-neutral-400)',
                    }}>{e.type === 'kimp' ? '김프' : '역프'}</span>
                    <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' }}>{fmtDT(e.start)}</span>
                    <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: ongoing ? 'var(--color-accent-300)' : 'var(--color-neutral-400)' }}>{ongoing ? '진행 중' : fmtDT(endT)}</span>
                    <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtDur(ongoing ? (now - e.start) / 60e3 : e.durMin)}</span>
                    <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 500, color: e.type === 'kimp' ? POS : NEG }}>{fmtPct(e.peak)}</span>
                  </div>
                );
              })}
              {evs.length === 0 && (
                <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 12 }}>기간 내 사건 없음</div>
              )}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
