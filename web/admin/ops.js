// 운영 그림 모델(스펙 066 §3.3 — 064 의 수집·경보 계산을 옮김): 수집 원천 상태 띠(정상 얇고 옅게 · 끊김 칸 가득 · 자료 없음 빗금),
// 끊김 목록, 경보 7일 사건 줄(ALARM 구간 띠 · Slack 알림 점), 알림 목록. DOM·echarts 없음(개요·서버가 같이 쓴다).
import { ago, clean, hm, int, isObj, kstText, list, num, own, pct, span } from './common.js';
import { exName, kindName } from './names.js';

const DAY = 86_400_000;
const cut = (text, n) => (clean(text).length > n ? `${clean(text).slice(0, n)}…` : clean(text));

// 짧은 재연결(080 — 5초 안에 다시 붙은 끊김)은 끊김 목록에 없다. 띠 툴팁에 24시간 횟수만 — 0·없음(080 전 서버)이면 줄 없음
const reconnectTip = (ex) => (num(ex.reconnects24h) > 0 ? [['', '재연결 24h', `${int(ex.reconnects24h)}회 · 5초 이하`]] : []);

// 수집 원천 띠 — 원천마다 24시간(정상 바탕 + 실패 구간). 오른쪽 글 = 마지막 성공 · 1시간 성공률. 성공한 적 없는 원천은 자료 없음
export function collectLanes(body, now) {
  const start = now - DAY;
  const exchanges = list(body?.exchanges).filter(isObj);
  const ids = exchanges.map((ex) => ex.exchange);
  const segs = exchanges.map((ex, row) =>
    num(ex.lastSuccessAt) === null
      ? { row, from: start, to: now, tone: 'none', head: exName(ex.exchange), tip: [['', '자료 없음', '성공 기록 없음']] }
      : { row, from: start, to: now, tone: 'ok', head: exName(ex.exchange), tip: [['ok', '1시간 성공률', pct(ex.successRate1h)], ['', '마지막 성공', ago(ex.lastSuccessAt, now)], ...reconnectTip(ex)] },
  );
  for (const o of list(body?.outages).filter((x) => isObj(x) && num(x.startedAt) !== null)) {
    const row = ids.indexOf(o.exchange);
    const to = num(o.endedAt) ?? now;
    if (row < 0 || to < start) continue;
    const tip = [['bad', kindName(o.kind), `×${int(o.count)}`], ['', '시간', `${hm(o.startedAt)} ~ ${num(o.endedAt) === null ? '진행 중' : hm(o.endedAt)}`]];
    if (num(o.statusCode) !== null) tip.push(['', 'HTTP', String(o.statusCode)]);
    if (o.message) tip.push(['', cut(o.message, 160), '']);
    segs.push({ row, from: Math.max(start, o.startedAt), to, tone: 'bad', head: `${exName(o.exchange)} 끊김`, tip });
  }
  return {
    startMs: start,
    endMs: now,
    rows: exchanges.map((ex) => exName(ex.exchange)),
    right: exchanges.map((ex) => `${ago(ex.lastSuccessAt, now)} · ${pct(ex.successRate1h)}`),
    segs,
  };
}

// 끊김 목록(수집 상태 상세 판 — Sentry Crons 의 실행 표처럼): 시작·원천·종류·길이·횟수·HTTP·내용
export function outageRows(body, now) {
  return list(body?.outages)
    .filter((o) => isObj(o) && num(o.startedAt) !== null)
    .map((o) => ({
      start: o.startedAt,
      source: exName(o.exchange),
      kind: kindName(o.kind),
      dur: Math.max(0, ((num(o.endedAt) ?? now) - o.startedAt) / 1000),
      open: num(o.endedAt) === null,
      count: num(o.count),
      http: num(o.statusCode),
      message: cut(o.message, 200),
    }));
}
export const durText = (sec, open) => `${span(sec)}${open ? ' · 진행 중' : ''}`;

// --- 경보(034) ----------------------------------------------------------------------------

const ALARM_RANK = Object.freeze({ ALARM: 0, INSUFFICIENT_DATA: 1, OK: 2 });
export const ALARM_TONE = Object.freeze({ ALARM: 'bad', INSUFFICIENT_DATA: 'none', OK: 'ok' });
export const ALARM_WORD = Object.freeze({ ALARM: 'ALARM', INSUFFICIENT_DATA: '데이터 부족', OK: 'OK' });
export const alarmName = (name) => clean(name).replace(/^marketlens-/, '');
export const alarmRank = (state) => own(ALARM_RANK, state) ?? 1;
// Slack 글의 머리 그림 → 색(025 문구 규칙)
export const slackTone = (text) => (clean(text).startsWith('🔴') ? 'bad' : clean(text).startsWith('🟢') ? 'ok' : clean(text).startsWith('⚠') ? 'warn' : 'acc');

const alarmSeg = (row, name, from, to, still) => ({
  row,
  from,
  to,
  tone: 'bad',
  head: `${alarmName(name)} ALARM`,
  tip: [['bad', '구간', `${kstText(from).slice(5)} ~ ${still ? '지금' : kstText(to).slice(5)}`], ['', '길이', span((to - from) / 1000)]],
});
// 경보 7일 사건 줄 — 줄 = 지난 7일에 바뀐 적 있거나 지금 OK 가 아닌 경보 + 맨 아래 'Slack'. ALARM 구간 = ALARM 으로 바뀐 때 ~ 다음 바뀜
// (지금 ALARM 이면 지금까지, 첫 바뀜이 ALARM 에서 나간 것이면 창 시작부터). Slack 알림은 점(머리 그림 색)
export function alarmLanes(alarmItems, alertItems, now) {
  const start = now - 7 * DAY;
  const current = new Map(list(alarmItems).filter(isObj).map((a) => [clean(a.name), a.state]));
  const inWindow = (it) => isObj(it) && num(it.at) !== null && it.at >= start && it.at <= now + 60_000;
  const events = list(alertItems).filter((it) => inWindow(it) && it.source === 'alarm').sort((a, b) => a.at - b.at);
  const names = [...new Set([...events.map((e) => clean(e.alarm)), ...[...current].filter(([, s]) => s !== 'OK').map(([n]) => n)])];
  names.sort((a, b) => alarmRank(current.get(a)) - alarmRank(current.get(b)) || a.localeCompare(b));
  const rows = [...names.map(alarmName), 'Slack'];
  const segs = rows.map((name, row) => ({ row, from: start, to: now, tone: 'track', head: name, tip: [] }));
  names.forEach((name, row) => {
    const evs = events.filter((e) => clean(e.alarm) === name);
    let open = (evs.length && evs[0].fromState === 'ALARM') || (!evs.length && current.get(name) === 'ALARM') ? start : null;
    for (const e of evs) {
      if (e.toState === 'ALARM' && open === null) open = e.at;
      else if (e.toState !== 'ALARM' && open !== null) {
        segs.push(alarmSeg(row, name, open, e.at, false));
        open = null;
      }
    }
    if (open !== null) segs.push(alarmSeg(row, name, open, now, true));
  });
  const dots = list(alertItems)
    .filter((it) => inWindow(it) && it.source === 'slack')
    .map((it) => ({
      row: rows.length - 1,
      at: it.at,
      tone: slackTone(it.text),
      head: kstText(it.at),
      tip: [[slackTone(it.text), cut(it.text, 200), it.delivered === false ? '전송 실패' : ''], ['', clean(it.role ?? 'slack'), '']],
    }));
  return { startMs: start, endMs: now, rows, segs, dots, quiet: !names.length && !dots.length };
}

// 알림 목록(경보 상세 판): 시각·출처·이름·바뀜·글
export function alertRows(items) {
  return list(items)
    .filter((it) => isObj(it) && num(it.at) !== null)
    .map((it) => ({
      at: it.at,
      source: it.source === 'alarm' ? '경보' : 'Slack',
      name: it.source === 'alarm' ? alarmName(it.alarm) : clean(it.role ?? ''),
      change:
        it.source === 'alarm'
          ? `${own(ALARM_WORD, it.fromState) ?? clean(it.fromState)} → ${own(ALARM_WORD, it.toState) ?? clean(it.toState)}`
          : it.delivered === false
            ? '전송 실패'
            : '보냄',
      text: cut(it.text, 300),
    }));
}
