// 서버 페이지(스펙 064 §3.3) — AWS 시계열(063)·수집 상태·경보·비용·도구.
// 기간 단추(6시간·24시간·7일·30일, 기본 24시간)는 JS 변수 — 바꾸면 /api/admin/aws/series?range= 하나만 곧바로 부른다.
// 시간 차트는 한 무리('time')라 한 칸을 확대·이동하면 같은 페이지의 시간 차트가 함께 움직인다(echarts.connect).
import {
  $,
  ago,
  bytes,
  call,
  clean,
  dec1,
  el,
  exName,
  hm,
  int,
  isExpired,
  isObj,
  list,
  money,
  n0,
  num,
  once,
  own,
  partOf,
  pct,
  plainOf,
  put,
  start,
  stateWord,
  tag,
  usable,
  worstMonthly,
} from './common.js';
import {
  COLOR,
  SERIES,
  TONE,
  axisTip,
  base,
  blank,
  budgetGauge,
  categoryAxis,
  draw,
  hasValue,
  itemTip,
  lanes,
  legend,
  lines,
  markOf,
  mirror,
  ms,
  timeAxis,
  tip,
  unzoom,
  valueAxis,
  when,
} from './charts.js';

const GROUP = 'time';
// 상자 색은 이름을 따른다(걸러도 남은 상자의 색이 바뀌지 않게) — 차트 색 순서의 앞 셋
const BOX_MARK = Object.freeze({ collect: 'c0', data: 'c1', serve: 'c2' });
const boxMark = (box, i) => own(BOX_MARK, box) ?? markOf(i + 3);
const colorOf = (mark) => own(TONE, mark) ?? SERIES[Number(mark.slice(1))];
const ZOOM = [{ type: 'inside', xAxisIndex: 'all', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }];

// 값이 1 이상인 칸이 이어진 구간 [[시작 ms, 끝 ms]] — 상태 검사 실패 띠
export function runs(points, periodSec) {
  const step = n0(periodSec) * 1000;
  const out = [];
  for (const [t, v] of ms(points)) {
    if (!(n0(v) >= 1)) continue;
    const last = out[out.length - 1];
    if (last && last[1] >= t) last[1] = t + step;
    else out.push([t, t + step]);
  }
  return out;
}

// --- AWS 시계열 카드 (063 — 값을 못 그리면 그 칸에 상태 글) --------------------------------------------

function timeCard(app, name, build) {
  const entry = app.current('series');
  const part = partOf(entry);
  tag(`t-${name}`, part);
  once(`c-${name}`, entry, () => {
    if (!usable(part)) return blank(`c-${name}`, stateWord(part));
    return build(part, { startMs: n0(part.startTs) * 1000, endMs: n0(part.endTs) * 1000 });
  });
}
const boxes = (part) => list(part.boxes).filter((b) => isObj(b) && isObj(b.series));
const perBox = (part, key) => boxes(part).map((b, i) => ({ name: clean(b.box), points: ms(b.series[key]), mark: boxMark(b.box, i) }));
const pctLines = (id, part, span, key, refs) =>
  lines(id, { ...span, series: perBox(part, key), max: 100, fmt: (v) => pct(v), axisFmt: (v) => `${v}%`, refs, group: GROUP });

const drawCpu = (app) => timeCard(app, 'cpu', (p, span) => pctLines('c-cpu', p, span, 'cpu'));
const drawMem = (app) => timeCard(app, 'mem', (p, span) => pctLines('c-mem', p, span, 'mem'));
const drawDisk = (app) => timeCard(app, 'disk', (p, span) => pctLines('c-disk', p, span, 'disk', [[75, 'warn'], [85, 'bad']]));
const drawWs = (app) =>
  timeCard(app, 'ws', (p, span) => lines('c-ws', { ...span, series: [{ name: 'WS 접속', points: ms(p.wsClients), mark: 'c0' }], fmt: (v) => int(v), area: true, group: GROUP }));
const flipped = (id, p, span, up, down, names) =>
  mirror(id, { ...span, names, fmt: (v) => bytes(v), group: GROUP, panels: boxes(p).map((b) => ({ title: clean(b.box), up: ms(b.series[up]), down: ms(b.series[down]) })) });
const drawNet = (app) => timeCard(app, 'net', (p, span) => flipped('c-net', p, span, 'netIn', 'netOut', ['들어옴', '나감']));
const drawIo = (app) => timeCard(app, 'io', (p, span) => flipped('c-io', p, span, 'ebsRead', 'ebsWrite', ['읽기', '쓰기']));

// CPU 크레딧 — 값 있는 상자만. 위 칸 잔량 선, 아래 칸 사용 막대 + 초과 과금 빨강 막대(상자끼리 쌓음)
function credits(p, span) {
  const shown = boxes(p).filter((b) => ['creditBalance', 'creditUsage', 'surplusCharged'].some((k) => hasValue(b.series[k])));
  if (!shown.length) return blank('c-credit', '크레딧이 있는 상자 없음');
  const color = (b, i) => colorOf(boxMark(b.box, i));
  const series = [
    ...shown.map((b, i) => ({ type: 'line', name: clean(b.box), data: ms(b.series.creditBalance), showSymbol: false, lineStyle: { width: 2, color: color(b, i) }, itemStyle: { color: color(b, i) } })),
    ...shown.map((b, i) => ({ type: 'bar', name: `${clean(b.box)} 사용`, xAxisIndex: 1, yAxisIndex: 1, data: ms(b.series.creditUsage), barMaxWidth: 6, itemStyle: { color: color(b, i) } })),
    ...shown.map((b) => ({ type: 'bar', name: `${clean(b.box)} 초과 과금`, xAxisIndex: 1, yAxisIndex: 1, stack: 'surplus', data: ms(b.series.surplusCharged), barMaxWidth: 6, itemStyle: { color: COLOR.bad } })),
  ];
  const marks = [...shown.map((b, i) => boxMark(b.box, i)), ...shown.map((b, i) => boxMark(b.box, i)), ...shown.map(() => 'bad')];
  return draw('c-credit', base({
    title: [
      { text: '잔량', left: 4, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: COLOR.muted } },
      { text: '사용 · 초과 과금', left: 4, top: '60%', textStyle: { fontSize: 12, fontWeight: 600, color: COLOR.muted } },
    ],
    legend: legend({ data: shown.map((b) => clean(b.box)) }),
    grid: [
      { left: 8, right: 16, top: 28, height: '42%', containLabel: true },
      { left: 8, right: 16, top: '68%', bottom: 4, containLabel: true },
    ],
    xAxis: [timeAxis(span.startMs, span.endMs, { axisLabel: { show: false } }), timeAxis(span.startMs, span.endMs, { gridIndex: 1 })],
    yAxis: [valueAxis({ min: 0, splitNumber: 3 }), valueAxis({ gridIndex: 1, min: 0, splitNumber: 2 })],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: ZOOM,
    tooltip: axisTip((ps) => tip(when(ps[0]?.value?.[0]), ps.map((q) => [marks[q.seriesIndex], q.seriesName, dec1(q.value?.[1])]))),
    series,
  }), GROUP);
}
const drawCredit = (app) => timeCard(app, 'credit', credits);

// 점검 — 걸린 시간 선과 오류 빨간 점(그 칸의 걸린 시간 위)
function canary(p, span) {
  const duration = ms(p.canary?.durationMs);
  const errors = ms(p.canary?.errors).filter(([, v]) => n0(v) > 0);
  if (!hasValue(duration) && !errors.length) return blank('c-canary', '자료 없음');
  const at = new Map(duration.map(([t, v]) => [t, v]));
  const count = new Map(errors);
  return draw('c-canary', base({
    grid: { left: 8, right: 16, top: 12, bottom: 4, containLabel: true },
    xAxis: timeAxis(span.startMs, span.endMs),
    yAxis: valueAxis({ min: 0, axisLabel: { color: COLOR.muted, fontSize: 12, formatter: (v) => `${int(v)}ms` } }),
    dataZoom: ZOOM,
    tooltip: axisTip((ps) =>
      tip(when(ps[0]?.value?.[0]), ps.map((q) => (q.seriesIndex === 0 ? ['c0', '걸린 시간', `${int(q.value?.[1])}ms`] : ['bad', '오류', int(count.get(q.value?.[0]))]))),
    ),
    series: [
      { type: 'line', name: '걸린 시간', data: duration, showSymbol: false, lineStyle: { width: 2, color: COLOR.accent }, itemStyle: { color: COLOR.accent }, areaStyle: { color: COLOR.accent, opacity: 0.1 } },
      { type: 'scatter', name: '오류', data: errors.map(([t]) => [t, at.get(t) ?? 0]), symbolSize: 10, itemStyle: { color: COLOR.bad, borderColor: COLOR.card, borderWidth: 2 } },
    ],
  }), GROUP);
}
const drawCanary = (app) => timeCard(app, 'canary', canary);

// 상태 검사 — 상자마다 띠, 실패(값 1)가 이어진 구간을 빨강으로
function checks(p, span) {
  const shown = boxes(p);
  const segs = [];
  shown.forEach((b, row) => {
    const failed = runs(b.series.statusFailed, p.periodSec);
    segs.push({ row, from: span.startMs, to: span.endMs, tone: 'track', head: clean(b.box), tip: [[failed.length ? 'bad' : 'ok', '실패 구간', `${failed.length}개`]] });
    for (const [from, to] of failed) segs.push({ row, from, to, tone: 'bad', head: `${clean(b.box)} 상태 검사 실패`, tip: [['bad', '구간', `${when(from)} ~ ${hm(to)}`]] });
  });
  return lanes('c-checks', { ...span, rows: shown.map((b) => clean(b.box)), segs, group: GROUP });
}
const drawChecks = (app) => timeCard(app, 'checks', checks);

// 기간 단추 — 고른 값만 눌림
function drawRange(app) {
  for (const button of document.querySelectorAll('[data-range]')) button.setAttribute('aria-pressed', String(button.dataset.range === app.picked.series));
}

// --- 수집 상태 (011 — 원천마다 24시간 띠: 정상 초록 · 실패 구간 빨강, 이름 옆 1시간 성공률) ---------------------

// 실패 종류 이름 — 공개 수집 상태 탭(011)의 유형 칩과 같다. 표에 없는 값은 원래 글자 그대로
const KIND_NAME = Object.freeze({
  timeout: '타임아웃',
  network: '연결 실패',
  rate_limit: 'rate limit',
  banned: '차단',
  unavailable: '거래소 오류',
  bad_request: '요청 오류',
  bad_response: '응답 오류',
  stale_stream: '스트림 정체',
});
const kindName = (kind) => own(KIND_NAME, kind) ?? clean(kind);
const cut = (text, n) => (clean(text).length > n ? `${clean(text).slice(0, n)}…` : clean(text));

export function collectLanes(body, now) {
  const start = now - 86_400_000;
  const exchanges = list(body.exchanges).filter(isObj);
  const ids = exchanges.map((ex) => ex.exchange);
  const segs = exchanges.map((ex, row) => ({
    row,
    from: start,
    to: now,
    tone: 'ok',
    head: exName(ex.exchange),
    tip: [['ok', '1시간 성공률', pct(ex.successRate1h)], ['dim', '마지막 성공', ago(ex.lastSuccessAt, now)]],
  }));
  for (const o of list(body.outages).filter((x) => isObj(x) && num(x.startedAt) !== null)) {
    const row = ids.indexOf(o.exchange);
    const to = num(o.endedAt) ?? now;
    if (row < 0 || to < start) continue;
    const rows = [['bad', kindName(o.kind), `×${int(o.count)}`], ['dim', '시간', `${hm(o.startedAt)} ~ ${num(o.endedAt) === null ? '진행 중' : hm(o.endedAt)}`]];
    if (num(o.statusCode) !== null) rows.push(['dim', 'HTTP', String(o.statusCode)]);
    if (o.message) rows.push(['', cut(o.message, 160), '']);
    segs.push({ row, from: Math.max(start, o.startedAt), to, tone: 'bad', head: `${exName(o.exchange)} 실패`, tip: rows });
  }
  return { startMs: start, endMs: now, rows: exchanges.map((ex) => `${exName(ex.exchange)}  ${pct(ex.successRate1h)}`), segs };
}

function drawCollect(app) {
  const entry = app.entry('collect');
  tag('t-collect', plainOf(entry));
  once('c-collect', [entry, Math.floor(Date.now() / 60_000)], () => {
    if (!entry?.body) return blank('c-collect', stateWord(plainOf(entry)));
    return lanes('c-collect', collectLanes(entry.body, Date.now()));
  });
}

// --- 경보 (034 — 경보 이름 알약 + 지난 7일 알림 시각표: 원 Slack, 마름모 경보 상태 변경) ----------------------

const ALARM_RANK = Object.freeze({ ALARM: 0, INSUFFICIENT_DATA: 1, OK: 2 });
const ALARM_TONE = Object.freeze({ ALARM: 'bad', INSUFFICIENT_DATA: 'dim', OK: 'ok' });
const ALARM_WORD = Object.freeze({ ALARM: 'ALARM', INSUFFICIENT_DATA: '데이터 부족', OK: 'OK' });
const alarmName = (name) => clean(name).replace(/^marketlens-/, '');
// Slack 글의 머리 그림 → 색(025 문구 규칙)
const slackTone = (text) => (clean(text).startsWith('🔴') ? 'bad' : clean(text).startsWith('🟢') ? 'ok' : clean(text).startsWith('⚠') ? 'warn' : 'c0');

function alarmPills(part) {
  if (!usable(part)) return [el('p', 'empty', stateWord(part))];
  const items = list(part.items).filter(isObj);
  if (!items.length) return [el('p', 'empty', '경보 없음')];
  items.sort((a, b) => (own(ALARM_RANK, a.state) ?? 1) - (own(ALARM_RANK, b.state) ?? 1) || clean(a.name).localeCompare(clean(b.name)));
  return items.map((a) => {
    const pill = el('span', `pill ${own(ALARM_TONE, a.state) ?? 'dim'}`, alarmName(a.name));
    pill.title = [clean(a.name), own(ALARM_WORD, a.state) ?? clean(a.state), clean(a.reason)].filter(Boolean).join(' — ');
    return pill;
  });
}

export function alertPoints(items, now) {
  const start = now - 7 * 86_400_000;
  const rows = list(items).filter((it) => isObj(it) && num(it.at) !== null && it.at >= start && it.at <= now + 60_000);
  const slack = rows.filter((it) => it.source === 'slack');
  const alarm = rows.filter((it) => it.source === 'alarm');
  const tipOf = (it) =>
    it.source === 'alarm'
      ? [[own(ALARM_TONE, it.toState) ?? 'dim', alarmName(it.alarm), `${own(ALARM_WORD, it.fromState) ?? clean(it.fromState)} → ${own(ALARM_WORD, it.toState) ?? clean(it.toState)}`], ...(it.text ? [['', cut(it.text, 200), '']] : [])]
      : [[slackTone(it.text), cut(it.text, 200), it.delivered === false ? '전송 실패' : ''], ['dim', clean(it.role ?? 'slack'), '']];
  return { start, slack, alarm, tipOf };
}

function drawAlarms(app) {
  const part = partOf(app.entry('aws'), 'alarms');
  tag('t-alarms', part);
  once('alarm-pills', [part], () => $('alarm-pills').replaceChildren(...alarmPills(part)));
  const alerts = app.entry('alerts');
  once('c-alerts', [alerts, Math.floor(Date.now() / 60_000)], () => {
    if (!alerts?.body) return blank('c-alerts', stateWord(plainOf(alerts)));
    const now = Date.now();
    const { start, slack, alarm, tipOf } = alertPoints(alerts.body.items, now);
    if (!slack.length && !alarm.length) return blank('c-alerts', '지난 7일 알림 없음');
    const dot = (it, tone) => ({ value: [it.at, it.source === 'alarm' ? '경보' : 'Slack'], itemStyle: { color: colorOf(tone), borderColor: COLOR.card, borderWidth: 2 } });
    return draw('c-alerts', base({
      grid: { left: 8, right: 16, top: 8, bottom: 4, containLabel: true },
      xAxis: timeAxis(start, now),
      yAxis: categoryAxis(['Slack', '경보'], { axisLine: { show: false }, axisLabel: { color: COLOR.text, fontSize: 12 } }),
      tooltip: itemTip((p) => {
        const it = (p.seriesIndex === 0 ? slack : alarm)[p.dataIndex];
        return it ? tip(when(it.at), tipOf(it)) : '';
      }),
      series: [
        { type: 'scatter', name: 'Slack', symbol: 'circle', symbolSize: 11, data: slack.map((it) => dot(it, slackTone(it.text))) },
        { type: 'scatter', name: '경보', symbol: 'diamond', symbolSize: 13, data: alarm.map((it) => dot(it, own(ALARM_TONE, it.toState) ?? 'dim')) },
      ],
    }));
  });
}

// --- 비용 (034 — 예산 게이지: 실제·예측 두 바늘, 금액) ---------------------------------------------------

function drawCost(app) {
  const part = partOf(app.entry('aws'), 'budget');
  tag('t-cost', part);
  once('cost', [part], () => {
    if (!usable(part)) {
      $('cost-rows').replaceChildren();
      return blank('c-cost', stateWord(part));
    }
    const worst = worstMonthly(part);
    if (worst) budgetGauge('c-cost', worst.b, (v) => money(v, worst.b.unit));
    else blank('c-cost', '월 예산 없음');
    return $('cost-rows').replaceChildren(
      ...list(part.items).filter(isObj).flatMap((b) => [
        el('dt', null, b.name ?? '예산'),
        el('dd', null, `실제 ${money(b.actual, b.unit)} · 예측 ${money(b.forecast, b.unit)} · 한도 ${money(b.limit, b.unit)}`),
      ]),
    );
  });
}

// --- 도구 — 즉시 갱신(029·003 그대로: 토큰은 입력칸과 변수에만, 401 은 토큰 오류) -------------------------------

function wireTools(app) {
  $('unzoom').addEventListener('click', () => unzoom(GROUP));
  for (const button of document.querySelectorAll('[data-range]')) button.addEventListener('click', () => app.pick('series', button.dataset.range));
  $('refresh').addEventListener('click', async () => {
    const button = $('refresh');
    const token = $('token').value;
    button.disabled = true;
    $('refresh-result').hidden = false;
    try {
      const { status, body } = await call('/api/refresh', { method: 'POST', headers: token ? { 'X-Refresh-Token': token } : {} });
      const ok = status === 200 && body !== null;
      put('refresh-status', `${status}${status === 401 ? ' 토큰 오류' : status === 403 ? ' 권한·설정 오류' : ''}`, ok ? 'num-ok' : 'num-bad');
      put('refresh-saved', ok ? int(body.totalSaved) : '–');
      put('refresh-failures', ok ? list(body.failures).filter(isObj).map((f) => `${clean(f.exchange)} · ${clean(f.errorCode)}`).join(', ') || '없음' : '–');
      put('refresh-warnings', ok ? list(body.warnings).map(clean).join(' / ') || '없음' : '–');
    } catch (err) {
      // 버튼은 만료 표시를 지우지 않는다(지우는 것은 폴링 묶음뿐) — 만료 신호면 새로고침 한 번 또는 알림
      if (isExpired(err)) app.expired();
      put('refresh-status', isExpired(err) ? '로그인 만료·연결 끊김' : '보내지 못함', 'num-bad');
      for (const id of ['refresh-saved', 'refresh-failures', 'refresh-warnings']) put(id, '–');
    } finally {
      button.disabled = false;
    }
  });
}

const app = start({
  home: '/server.html',
  slow: ['aws', 'series', 'alerts'],
  picks: { series: '24h' },
  cards: [
    ['range', drawRange],
    ['cpu', drawCpu],
    ['net', drawNet],
    ['mem', drawMem],
    ['disk', drawDisk],
    ['credit', drawCredit],
    ['io', drawIo],
    ['ws', drawWs],
    ['canary', drawCanary],
    ['checks', drawChecks],
    ['collect', drawCollect],
    ['alarms', drawAlarms],
    ['cost', drawCost],
  ],
});
wireTools(app);
