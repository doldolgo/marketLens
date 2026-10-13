// 피드 부르기(064 §3.1 규칙을 066 이 이어받음) — 열쇠(주소)마다 마지막 결과 하나 { body, text, okAt, why, off }. 같은 경로는 겹쳐
// 부르지 않고(앞 호출이 끝난 뒤 다음 — 042), 기다리는 사이 더는 쓰지 않게 된 열쇠는 부르지 않는다. 실패해도 마지막 값은 두고
// 사유만 단다(066 §3.1 '불러오지 못함 · n분 전 값'). 404 = 그 피드가 이 서버에 아직 없다(배포 전 — 본문과 무관하게 '연결 안 됨').
import { HEALTH, call, isExpired } from './common.js';

// o = { wanted(열쇠) → 지금 쓰는가, stopped() → 새로고침을 기다리는가 }
export function createStore(o) {
  const got = new Map();
  const okAt = new Map(); // 열쇠 → 마지막 성공 시각(같은 응답이면 결과 객체는 그대로 두므로 따로)
  const chains = new Map(); // 경로 → 마지막 호출
  const pending = new Map(); // 열쇠 → 떠 있거나 기다리는 호출
  const okSince = new Set(); // 마지막 만료 신호 뒤 신호 없이 끝난 열쇠

  // 같은 응답이면 결과 객체를 그대로 둔다 — 그 값으로 그린 카드를 다시 그리지 않게(036). 실패는 마지막 값을 두고 사유만 단다
  function keep(key, next) {
    const prev = got.get(key);
    if (next.text !== undefined && !next.why) okAt.set(key, Date.now());
    if (next.why && next.body !== undefined) next.okAt = okAt.get(key);
    const same = prev && prev.text === next.text && prev.why === next.why && Boolean(prev.off) === Boolean(next.off);
    if (!same) got.set(key, next);
  }
  const failed = (key, why) => {
    const prev = got.get(key);
    return prev?.body !== undefined && !prev.off ? { body: prev.body, text: prev.text, why } : { why };
  };

  // 경로 하나를 부르고 결과를 둔다 — 돌려주는 값 = 만료 신호였는가
  async function fetchOne(r) {
    try {
      const { status, body, text } = await call(r.url);
      const fine = status === 200 || (status === 503 && HEALTH.includes(r.name));
      if (status === 404) keep(r.key, { off: true });
      else if (status === 403) keep(r.key, failed(r.key, '권한·설정 오류 (403)'));
      else if (body === null || !fine) keep(r.key, failed(r.key, `응답 오류 (HTTP ${status})`));
      else keep(r.key, { body, text });
      okSince.add(r.key);
      return false;
    } catch (err) {
      const gone = isExpired(err);
      keep(r.key, failed(r.key, gone ? '로그인 만료·연결 끊김' : '불러오지 못함'));
      if (!gone) okSince.add(r.key);
      return gone;
    }
  }
  // 같은 경로는 한 줄로 — 기다리는 사이 보기가 바뀌어 더는 쓰지 않는 열쇠면 부르지 않는다(마지막에 고른 것만, 042)
  function load(r) {
    const held = pending.get(r.key);
    if (held) return held;
    const path = r.url.split('?')[0];
    const job = (chains.get(path) ?? Promise.resolve()).then(() => (o.stopped() || (!r.fast && !o.wanted(r.key)) ? false : fetchOne(r)));
    chains.set(path, job);
    pending.set(r.key, job);
    job.then(() => {
      pending.delete(r.key);
      if (chains.get(path) === job) chains.delete(path);
    });
    return job;
  }
  return { entry: (key) => got.get(key), busy: (key) => pending.has(key), load, okSince };
}
