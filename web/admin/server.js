// 서버 페이지(스펙 066 §3.3) — "어느 상자의 무엇이": 064 의 카드를 두 열로 + 위 상자 칩(collect·data·serve = 범례 겸 거르기 — 누르면
// 그 상자만, 더 누르면 더하기·빼기, 주소 f=box:…). 시간 카드는 한 무리라 확대·십자선이 함께 움직인다(echarts.connect).
// 기간 6시간·24시간·7일·30일, 비교 이전 기간(068 offset)·지난주 같은 때(7일 창에서 잘라).
import { $, call, clean, el, int, isExpired, isObj, list, money, num, once, partOf, plainOf, pressed, put, req, stateWord, tag, usable } from './common.js';
import { change, ms } from './calc.js';
import { blank, budgetOption, draw, eventsOption, lanesOption, timeOption, unzoom } from './charts.js';
import { BOXES, cmpPhrase } from './names.js';
import { seriesCompare, seriesReqs, seriesWindow } from './models.js';
import { ALARM_TONE, ALARM_WORD, alarmLanes, alarmName, alarmRank, collectLanes } from './ops.js';
import { OPS_DETAILS, boxLines, boxesOf, canaryPanes, cmpNote, credits, mirrorPanes, wsLine } from './opsdetail.js';
import { worstMonthly } from './rules.js';
import { start } from './app.js';

const GROUP = 'server';
const fkey = (app) => JSON.stringify(app.view.f);

// 비교 한 줄 — 계열마다 보이는 창 평균의 증감('어제 같은 때보다 평균 — collect ▲ 12% · data ▼ 3%')
export function cmpLine(series, phrase) {
  const avg = (pts) => {
    const vals = list(pts).map((p) => num(p[1])).filter((v) => v !== null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  };
  const parts = series
    .filter((s) => s.cmp)
    .map((s) => {
      const d = change(avg(s.data), avg(s.cmp));
      if (d.pct === null) return null;
      const r = Math.round(d.pct);
      return `${clean(s.name)} ${r > 0 ? '▲' : r < 0 ? '▼' : '–'} ${Math.abs(r)}%`;
    })
    .filter(Boolean);
  return parts.length ? `${phrase}보다 평균 — ${parts.join(' · ')}` : '';
}

// 시간 카드 하나 — AWS 시계열(063)로 그린다. build(app, w) → { panes, series }
function timeCard(name, build, foot = false) {
  return (app) => {
    const w = seriesWindow(app, app.view.range, app.view.cmp);
    tag(`t-${name}`, w.part);
    once(`c-${name}`, [...w.deps, app.view.range, app.view.cmp, fkey(app)], () => {
      if (!w.ok) {
        if (foot) put(`f-${name}`, '');
        return blank(`c-${name}`, stateWord(w.part));
      }
      const model = build(app, w);
      if (foot) put(`f-${name}`, cmpLine(model.series, cmpPhrase(app.view.range, app.view.cmp)) || cmpNote(w));
      if (!model.series.some((s) => list(s.data).some((p) => num(p[1]) !== null))) return blank(`c-${name}`, model.empty ?? '자료 없음');
      return draw(`c-${name}`, timeOption({ startMs: w.startMs, endMs: w.endMs, zoom: 'card', ...model }), GROUP);
    });
  };
}

// 값이 1 이상인 칸이 이어진 구간(실패)과 값이 없는 칸이 이어진 구간(자료 없음) — 상태 검사 띠
export function runs(points, periodMs, test) {
  const out = [];
  for (const [t, v] of list(points)) {
    if (!test(v)) continue;
    const last = out[out.length - 1];
    if (last && last[1] >= t) last[1] = t + periodMs;
    else out.push([t, t + periodMs]);
  }
  return out;
}
function drawChecks(app) {
  const w = seriesWindow(app, app.view.range, 'none');
  tag('t-checks', w.part);
  once('c-checks', [...w.deps, app.view.range, fkey(app)], () => {
    if (!w.ok) return blank('c-checks', stateWord(w.part));
    const step = (num(w.periodSec) ?? 300) * 1000;
    const boxes = boxesOf(app, w);
    const segs = [];
    boxes.forEach((b, row) => {
      const pts = ms(b.series.statusFailed).filter((p) => p[0] >= w.startMs && p[0] <= w.endMs);
      const failed = runs(pts, step, (v) => num(v) !== null && v >= 1);
      segs.push({ row, from: w.startMs, to: w.endMs, tone: 'ok', head: b.box, tip: [[failed.length ? 'bad' : 'ok', '실패 구간', `${failed.length}개`]] });
      for (const [from, to] of runs(pts.slice(0, -1), step, (v) => num(v) === null)) segs.push({ row, from, to, tone: 'none', head: `${b.box} 자료 없음`, tip: [] });
      for (const [from, to] of failed) segs.push({ row, from, to, tone: 'bad', head: `${b.box} 상태 검사 실패`, tip: [['bad', '구간', `${new Date(from).toTimeString().slice(0, 5)} ~ ${new Date(to).toTimeString().slice(0, 5)}`]] });
    });
    return boxes.length ? draw('c-checks', lanesOption({ startMs: w.startMs, endMs: w.endMs, rows: boxes.map((b) => b.box), segs, zoom: true }), GROUP) : blank('c-checks', '자료 없음');
  });
}

function drawCollect(app) {
  const entry = app.entry(req('collect'));
  tag('t-collect', plainOf(entry));
  once('c-collect', [entry, Math.floor(Date.now() / 60_000)], () => {
    if (!entry?.body) return blank('c-collect', stateWord(plainOf(entry)));
    const lanes = collectLanes(entry.body, Date.now());
    return lanes.rows.length ? draw('c-collect', lanesOption(lanes)) : blank('c-collect', '자료 없음');
  });
}

// 경보 — 지금 OK 가 아닌 경보 알약(모양 + 이름) + 'OK n', 아래 7일 사건 줄
function drawAlarms(app) {
  const aws = app.entry(req('aws'));
  const part = partOf(aws, 'alarms');
  tag('t-alarms', part);
  once('alarm-pills', [aws], () => {
    const box = $('alarm-pills');
    if (!usable(part)) return box.replaceChildren(el('span', 'dim', stateWord(part)));
    const items = list(part.items).filter(isObj).sort((a, b) => alarmRank(a.state) - alarmRank(b.state) || clean(a.name).localeCompare(clean(b.name)));
    const loud = items.filter((a) => a.state !== 'OK');
    const pill = (tone, text, title) => {
      const node = el('span', `chip ${tone === 'bad' ? 'bad' : tone === 'warn' ? 'warn' : ''}`.trim());
      node.append(el('i', `shape ${tone}`), el('span', null, text));
      node.title = clean(title);
      return node;
    };
    const ok = items.length - loud.length;
    return box.replaceChildren(
      ...loud.map((a) => pill(ALARM_TONE[a.state] ?? 'none', alarmName(a.name), [ALARM_WORD[a.state] ?? a.state, a.reason].filter(Boolean).join(' — '))),
      ...(ok ? [pill('ok', `OK ${int(ok)}`, items.filter((a) => a.state === 'OK').map((a) => alarmName(a.name)).join(', '))] : []),
      ...(items.length ? [] : [el('span', 'dim', '경보 없음')]),
    );
  });
  const alerts = app.entry(req('alerts'));
  once('c-alarms', [aws, alerts, Math.floor(Date.now() / 60_000)], () => {
    if (!alerts?.body) return blank('c-alarms', stateWord(plainOf(alerts)));
    const lanes = alarmLanes(usable(part) ? part.items : [], alerts.body.items, Date.now());
    return lanes.quiet ? blank('c-alarms', '지난 7일 경보·알림 없음') : draw('c-alarms', eventsOption(lanes));
  });
}

// 비용 — 남기는 게이지 하나(실제·예측 두 바늘) + 예산마다 실제 · 예측 · 한도
function drawCost(app) {
  const aws = app.entry(req('aws'));
  const part = partOf(aws, 'budget');
  tag('t-cost', part);
  once('cost', [aws], () => {
    const rows = $('cost-rows');
    if (!usable(part)) {
      rows.replaceChildren();
      return blank('c-cost', stateWord(part));
    }
    const worst = worstMonthly(part);
    if (worst) draw('c-cost', budgetOption(worst.b, (v) => money(v, worst.b.unit)));
    else blank('c-cost', '월 예산 없음');
    return rows.replaceChildren(
      ...list(part.items)
        .filter(isObj)
        .flatMap((b) => [el('dt', null, b.name ?? '예산'), el('dd', null, `실제 ${money(b.actual, b.unit)} · 예측 ${money(b.forecast, b.unit)} · 한도 ${money(b.limit, b.unit)}`)]),
    );
  });
}

// 상자 칩 — 누르면 그 상자만(거르기 없음 → 그것만, 있으면 더하기·빼기, 모두 빠지면 거르기 없음)
function drawBoxChips(app) {
  const pick = app.filters('box');
  for (const b of document.querySelectorAll('[data-box]')) pressed(b, !pick.length || pick.includes(b.dataset.box));
}
function toggleBox(app, box) {
  const pick = app.filters('box');
  if (!pick.length) app.addFilter('box', box);
  else if (pick.includes(box)) app.removeFilter('box', box);
  else app.addFilter('box', box);
}

// 도구 — 즉시 갱신(029·003 그대로: 토큰은 입력칸과 변수에만, 401 은 토큰 오류)
function wire(app) {
  for (const b of document.querySelectorAll('[data-box]')) b.addEventListener('click', () => toggleBox(app, b.dataset.box));
  $('unzoom')?.addEventListener('click', () => unzoom(GROUP));
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
      // 단추는 만료 표시를 지우지 않는다(지우는 것은 폴링 묶음뿐) — 만료 신호면 새로고침 한 번 또는 알림
      if (isExpired(err)) app.expired();
      put('refresh-status', isExpired(err) ? '로그인 만료·연결 끊김' : '보내지 못함', 'num-bad');
      for (const id of ['refresh-saved', 'refresh-failures', 'refresh-warnings']) put(id, '–');
    } finally {
      button.disabled = false;
    }
  });
}

start({
  id: 'server',
  home: '/server.html',
  ranges: ['6h', '24h', '7d', '30d'],
  range: '24h',
  compare: seriesCompare,
  filters: { box: (v) => BOXES.includes(v) },
  filterName: (k, v) => ['상자', v],
  needs: (app) => [req('aws'), req('alerts'), ...seriesReqs(app.view.range, app.view.cmp)],
  rules: (app) => ({ aws: app.entry(req('aws')), series: app.entry(seriesReqs(app.view.range, 'none')[0]), alerts: app.entry(req('alerts')) }),
  cards: [
    ['boxes', drawBoxChips],
    ['cpu', timeCard('cpu', boxLines('cpu'), true)],
    ['net', timeCard('net', mirrorPanes('netIn', 'netOut', '들어옴', '나감'))],
    ['mem', timeCard('mem', boxLines('mem'), true)],
    ['disk', timeCard('disk', boxLines('disk'), true)],
    ['credit', timeCard('credit', credits)],
    ['io', timeCard('io', mirrorPanes('ebsRead', 'ebsWrite', '읽기', '쓰기'))],
    ['collect', drawCollect],
    ['alarms', drawAlarms],
    ['cost', drawCost],
    ['ws', timeCard('ws', wsLine, true)],
    ['canary', timeCard('canary', canaryPanes)],
    ['checks', drawChecks],
  ],
  details: OPS_DETAILS,
  wire,
});
