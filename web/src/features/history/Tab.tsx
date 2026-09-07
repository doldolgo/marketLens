// 기록/통계 탭 — 사건 로그를 /history/streaks 실데이터로 (스펙 005 §3.6).
// 참조 디자인(docs/design/reference/tabs/HistoryTab.tsx)의 티커별 표·요약·타임라인은 후속 스펙 몫이라 아직 없다 — mock 으로 남기지 않는다.
import { useState } from 'react'
import { fmtPct, fmtTime, pctColor } from '../../shared/format'
import { Empty, NumField, Pill, Seg, searchInput, segOpt, card, gridHead, hint, kicker } from '../../shared/ui'
import { useStreaks } from './api'
import type { HistoryEvent, StreaksResponse } from './types'

type Per = '7d' | '30d'
type TypeFilter = 'all' | 'kimp' | 'rev'
type Dom = 'upbit' | 'bithumb'

// 3달은 1초 기록 위에서 60초 안에 못 돌아온다 — 1분 롤업 후속에서 복원 (§3.6)
const PER_LABEL: Record<Per, string> = { '7d': '1주', '30d': '1달' }
const PER_SEC: Record<Per, number> = { '7d': 7 * 86_400, '30d': 30 * 86_400 }
const DOM_LABEL: Record<Dom, string> = { upbit: '업비트', bithumb: '빗썸' }

/** 유형 | 시작 | 종료 | 지속 | 최대 스프레드 */
const LOG_GRID = '70px 1fr 1fr 110px 120px'

/** 지속 초 → 사람이 읽는 표기. */
function fmtDur(sec: number): string {
  const min = sec / 60
  if (min < 60) return `${Math.round(min)}분`
  return `${(min / 60).toFixed(1)}시간`
}

/** 두 방향 segments 를 합쳐 startTs 내림차순 (§3.6). 유형 필터는 여기서 거른다. */
function toEvents(data: StreaksResponse, type: TypeFilter): HistoryEvent[] {
  const out: HistoryEvent[] = []
  if (type !== 'rev') for (const seg of data.kimp.segments) out.push({ type: 'kimp', seg })
  if (type !== 'kimp') for (const seg of data.reverse.segments) out.push({ type: 'rev', seg })
  return out.sort((a, b) => b.seg.startTs - a.seg.startTs)
}

export default function HistoryTab({ now, selSym, onSelect }: {
  now: number; selSym: string; onSelect: (sym: string) => void
}) {
  const [per, setPer] = useState<Per>('7d')
  const [type, setType] = useState<TypeFilter>('all')
  const [dom, setDom] = useState<Dom>('upbit')
  const [thr, setThr] = useState(1.0)

  const { result, loading } = useStreaks({ base: selSym, dom, threshold: thr, periodSec: PER_SEC[per] })
  const data = result?.kind === 'ok' ? result.data : null
  const events = data ? toEvents(data, type) : []

  // 진행 중 = 그 방향의 마지막 구간이 마지막 기록까지 이어진 경우 (§3.6) — 방향마다 많아야 1건.
  // segments 는 시간순이라 마지막 원소가 최신이다.
  const ongoingKey = new Set<string>()
  if (data) {
    const from = data.lastUpdatedTs - data.maxGapSeconds
    for (const [t, dir] of [['kimp', data.kimp], ['rev', data.reverse]] as const) {
      const last = dir.segments.at(-1)
      if (last && last.endTs >= from) ongoingKey.add(`${t}-${last.startTs}`)
    }
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
      <div style={{ padding: 'var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>

        {/* 필터바 (§3.6) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <input className="input" value={selSym} placeholder="심볼" aria-label="심볼"
            // 서버 base 패턴(영숫자)에 맞춰 대문자로 정규화 — 빈 값이면 조회하지 않는다
            onChange={(e) => onSelect(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 20))}
            style={{ ...searchInput, width: 90, fontWeight: 500 }} />
          <Seg opts={(['7d', '30d'] as Per[]).map((p) => segOpt(PER_LABEL[p], per === p, () => setPer(p)))} />
          <Seg opts={[['all', '전체'], ['kimp', '김프만'], ['rev', '역프만']].map(([id, l]) => segOpt(l, type === id, () => setType(id as TypeFilter)))} />
          <Seg opts={(['upbit', 'bithumb'] as Dom[]).map((d) => segOpt(DOM_LABEL[d], dom === d, () => setDom(d)))} />
          <NumField label="사건 기준 스프레드 ≥" value={thr} step={0.1} onChange={setThr} />
          <span style={{ ...hint, marginLeft: 'auto' }}>
            사건 = 원값 스프레드가 기준 이상인 연속 구간(10분 넘게 끊기면 새 구간) · 기간 내 {events.length}건
          </span>
        </div>

        {/* 사건 로그 — 최근 20건 (§3.6) */}
        <div style={{ ...card, padding: 'var(--space-2) 0' }}>
          <div style={{ ...kicker, padding: 'var(--space-4) var(--space-6) var(--space-2)', display: 'flex', gap: 'var(--space-4)' }}>
            <span>사건 로그 · {selSym || '–'} · {DOM_LABEL[dom]} · 최근 20건</span>
            {loading && <span style={{ color: 'var(--color-accent-300)' }}>조회 중…</span>}
          </div>
          <div style={gridHead(LOG_GRID)}>
            <span style={{ padding: '6px 8px 6px 0' }}>유형</span>
            <span style={{ padding: '6px 8px', textAlign: 'right' }}>시작</span>
            <span style={{ padding: '6px 8px', textAlign: 'right' }}>종료</span>
            <span style={{ padding: '6px 8px', textAlign: 'right' }}>지속시간</span>
            <span style={{ padding: '6px 8px', textAlign: 'right' }}>최대 스프레드</span>
          </div>
          {events.slice(0, 20).map(({ type: t, seg }) => {
            const ongoing = ongoingKey.has(`${t}-${seg.startTs}`)
            const dur = ongoing ? Math.max(0, now / 1000 - seg.startTs) : seg.durationSeconds
            // 역프는 음수 부호로 — pctColor 의 한국식 색 규약(>0 빨강, <0 파랑)을 그대로 탄다
            const peak = t === 'kimp' ? seg.maxPercent : -seg.maxPercent
            return (
              <div key={`${t}-${seg.startTs}`} style={{ display: 'grid', gridTemplateColumns: LOG_GRID, alignItems: 'center', height: 32, padding: '0 var(--space-6)', borderBottom: '1px solid color-mix(in srgb, #e9e9ed 6%, transparent)' }}>
                <Pill tone={t === 'kimp' ? 'accent' : 'neutral'} style={t === 'kimp' ? { borderColor: 'var(--color-accent-700)' } : { borderColor: 'var(--color-neutral-700)' }}>
                  {t === 'kimp' ? '김프' : '역프'}
                </Pill>
                <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--color-neutral-300)' }}>{fmtTime(seg.startTs * 1000)}</span>
                <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: ongoing ? 'var(--color-accent-300)' : 'var(--color-neutral-400)' }}>{ongoing ? '진행 중' : fmtTime(seg.endTs * 1000)}</span>
                <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtDur(dur)}</span>
                <span style={{ padding: '0 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 500, color: pctColor(peak) }}>{fmtPct(peak)}</span>
              </div>
            )
          })}
          {result?.kind === 'error' && (
            <Empty size={12}>기록을 불러오지 못했습니다 (HTTP {result.status})</Empty>
          )}
          {result?.kind !== 'error' && events.length === 0 && !loading && (
            <Empty size={12}>기간 내 사건 없음</Empty>
          )}
        </div>

      </div>
    </div>
  )
}
