"""사건 감지의 빠른 길과 결측 판정 — 판정·점이 모든 행·방향을 보는 규칙과 같다 (스펙 013 §3.2, 2026-09-28)."""

import math
import random

from app.core.models import Tick, TickRow
from app.core.premium_events import (
    ENTER_PERCENT,
    EXIT_PERCENT,
    MAX_GAP_SEC,
    MIN_DURATION_SEC,
    WRITE_INTERVAL_SEC,
    Key,
    PremiumEvent,
    PremiumEventDetector,
)
from tests.premium_event_fakes import T0, FakeInflux

_VALUES = (-2.0, 0.2, 0.49, 0.5, 0.51, 0.9, 0.99, 1.0, 1.01, 1.5, 3.0, math.nan)


class EveryRowDetector(PremiumEventDetector):
    """기준 — 모든 행·방향의 키를 만들고, 이번 틱에 본 키 집합으로 결측을 가른다."""

    def observe(self, tick: Tick) -> None:
        ts = tick.ts
        seen: set[Key] = set()
        for row in tick.rows:
            for dir_, value in (("kimp", row.fwd), ("reverse", row.rev)):
                key: Key = (row.dom, row.fx, row.base, dir_)
                seen.add(key)
                ev = self._open.get(key)
                if ev is None:
                    if value >= ENTER_PERCENT:
                        self._open[key] = PremiumEvent(
                            dom=row.dom,
                            fx=row.fx,
                            base=row.base,
                            dir=dir_,
                            start_ts=ts,
                            end_ts=None,
                            max_percent=value,
                            max_ts=ts,
                            last_ts=ts,
                            samples=1,
                            net_dom=row.net_dom,
                            net_fx=row.net_fx,
                        )
                elif value > EXIT_PERCENT:
                    ev.samples += 1
                    ev.last_ts = ts
                    if value > ev.max_percent:
                        ev.max_percent = value
                        ev.max_ts = ts
                    ev.net_dom, ev.net_fx = row.net_dom, row.net_fx
                else:
                    ev.net_dom, ev.net_fx = row.net_dom, row.net_fx
                    self._close_observed(ev, ts)
        for key, ev in list(self._open.items()):
            if key not in seen and ts - ev.last_ts > MAX_GAP_SEC:
                self._close_at(ev, ev.last_ts)
        refresh = ts - self._last_refresh_ts >= WRITE_INTERVAL_SEC
        for ev in self._open.values():
            if not ev.written and ts - ev.start_ts > MIN_DURATION_SEC:
                ev.written = True
                self._stage(ev)
            elif ev.written and refresh:
                self._stage(ev)
        if refresh:
            self._last_refresh_ts = ts


def _state(det: PremiumEventDetector) -> list[tuple]:
    return sorted(
        (
            ev.key,
            ev.start_ts,
            repr(ev.max_percent),
            ev.max_ts,
            ev.last_ts,
            ev.samples,
            ev.net_dom,
            ev.net_fx,
            ev.written,
        )
        for ev in det.open_events()
    )


def _points(db: FakeInflux) -> dict:
    return {k: repr(sorted(v.items())) for k, v in db.data.items()}


async def test_fast_path_matches_the_every_row_rule_with_gaps_and_threshold_crossings() -> (
    None
):
    rng = random.Random(13)
    combos = [
        (dom, fx, base)
        for dom in ("upbit", "bithumb")
        for fx in ("binance", "bybit")
        for base in ("A", "B", "C", "D")
    ]
    fast_db, ref_db = FakeInflux(), FakeInflux()
    fast = PremiumEventDetector(writer=fast_db)
    ref = EveryRowDetector(writer=ref_db)
    ts = T0
    for step in range(3_000):
        # 가끔 700초 공백(결측허용 600초 초과) — 그 사이 열린 사건은 last_ts 로 닫힌다
        ts += 700 if rng.random() < 0.01 else rng.choice((1, 1, 1, 5, 30))
        rows = []
        for dom, fx, base in combos:
            if rng.random() < 0.2:
                continue  # 결측
            # 대부분 문턱 아래, 가끔 문턱을 오간다(NaN 포함)
            fwd = rng.choice(_VALUES) if rng.random() < 0.3 else rng.uniform(-1, 0.9)
            rev = rng.choice(_VALUES) if rng.random() < 0.3 else rng.uniform(-1, 0.9)
            rows.append(TickRow(dom, fx, base, fwd, rev, net_dom=f"n{step % 3}"))
        tick = Tick(ts=ts, rows=tuple(rows), dw_failed=())
        fast.observe(tick)
        ref.observe(tick)
        assert _state(fast) == _state(ref)
        if step % 250 == 0:
            await fast.flush()
            await ref.flush()
            assert _points(fast_db) == _points(ref_db)
    await fast.flush()
    await ref.flush()
    assert fast_db.data and _points(fast_db) == _points(ref_db)
