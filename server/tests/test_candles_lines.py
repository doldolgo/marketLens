"""1분 봉 줄 — 분 닫힘이 누적에서 바로 만드는 줄·미전송 상한·5,000줄 배치·기준점 조회 타임아웃 (스펙 014 §3.3~3.5, 2026-09-28).

기준은 고치기 전의 경로다: 내장 max/min 으로 누적하고 봉 행 → `candle_point` → `to_line` 으로 적은 줄.
"""

import math
import random

from app.core.candles import (
    PENDING_LIMIT,
    RESTORE_TIMEOUT_SEC,
    WRITE_BATCH,
    CandleAggregator,
)
from app.core.influx import CandleRow, candle_point, to_line
from app.core.models import TickRow
from tests.candle_fakes import T0, FakeCandleStore, row, tick

M = 60


def _tri(v: bool | None) -> int:
    return -1 if v is None else int(v)


def _reference_lines(minute_rows: list[list[TickRow]], minute: int) -> list[str]:
    """고치기 전 규칙 — 조합별 내장 max/min 누적, 마지막 행 값, 봉 행 → 점 → 줄(조합 이름순)."""
    acc: dict[tuple[str, str, str], dict] = {}
    for rows in minute_rows:
        for r in rows:
            a = acc.get((r.dom, r.fx, r.base))
            if a is None:
                acc[(r.dom, r.fx, r.base)] = a = {
                    "fo": r.fwd, "fh": r.fwd, "fl": r.fwd, "ro": r.rev, "rh": r.rev, "rl": r.rev,
                    "bf": 0, "br": 0, "n": 0,
                }  # fmt: skip
            else:
                a["fh"] = max(a["fh"], r.fwd)
                a["fl"] = min(a["fl"], r.fwd)
                a["rh"] = max(a["rh"], r.rev)
                a["rl"] = min(a["rl"], r.rev)
            a["last"] = r
            a["n"] += 1
            a["bf"] += r.fx_wd is False or r.dom_dep is False
            a["br"] += r.dom_wd is False or r.fx_dep is False
    lines = []
    for (dom, fx, base), a in sorted(acc.items()):
        last = a["last"]
        lines.append(
            to_line(
                candle_point(
                    CandleRow(
                        dom=dom, fx=fx, base=base, ts=minute,
                        fwd_o=a["fo"], fwd_h=a["fh"], fwd_l=a["fl"], fwd_c=last.fwd,
                        rev_o=a["ro"], rev_h=a["rh"], rev_l=a["rl"], rev_c=last.rev,
                        krw=last.dom_price, usdt=last.fx_price, rate=last.rate,
                        dom_dep=_tri(last.dom_dep), dom_wd=_tri(last.dom_wd),
                        fx_dep=_tri(last.fx_dep), fx_wd=_tri(last.fx_wd),
                        blocked_fwd_sec=a["bf"], blocked_rev_sec=a["br"], samples=a["n"],
                        net_dom=last.net_dom, net_fx=last.net_fx,
                    )
                )
            )
        )  # fmt: skip
    return lines


def _random_rows(rng: random.Random, combos: int) -> list[TickRow]:
    # 동률·부호 있는 0·NaN 이 섞여도 결과가 같아야 한다
    specials = [0.0, -0.0, 0.5, math.nan, 1e-12]
    tri = [True, False, None]
    rows = []
    for k in range(combos):
        if rng.random() < 0.1:
            continue  # 그 초에 없는 조합
        rows.append(
            row(
                rng.choice(specials) if rng.random() < 0.3 else rng.uniform(-2, 2),
                base=("BTC", "A,B", "C D", "E=F")[k % 4] + str(k),
                dom=("upbit", "bithumb")[k % 2],
                rev=rng.choice(specials) if rng.random() < 0.3 else rng.uniform(-2, 2),
                dom_price=rng.choice([100_000.0, 7]),
                dom_dep=rng.choice(tri),
                dom_wd=rng.choice(tri),
                fx_dep=rng.choice(tri),
                fx_wd=rng.choice(tri),
                net_dom=rng.choice([None, "Ethereum", 'Q"uote']),
                net_fx=rng.choice([None, "ERC20", "Back\\slash"]),
            )
        )
    return rows


async def test_minute_close_lines_match_max_min_and_point_serialization() -> None:
    rng = random.Random(21)
    store = FakeCandleStore()
    agg = CandleAggregator(store, clock=lambda: T0 + 3 * M)
    minutes: list[list[list[TickRow]]] = [[], []]
    for i in range(2 * M):
        rows = _random_rows(rng, 40)
        minutes[i // M].append(rows)
        agg.observe(tick(T0 + i, *rows))
    agg.observe(tick(T0 + 2 * M))  # 둘째 분 닫힘
    await agg.flush()
    got = [line for _, lines in store.lines for line in lines]
    assert got == _reference_lines(minutes[0], T0) + _reference_lines(
        minutes[1], T0 + M
    )


async def test_pending_cap_30000_and_writes_5000_lines_at_a_time_in_order() -> None:
    assert (PENDING_LIMIT, WRITE_BATCH) == (30_000, 5_000)
    store = FakeCandleStore()
    now = [T0 + 20 * M]
    agg = CandleAggregator(store, clock=lambda: now[0])
    combos = 1_458
    for i in range(8):  # 8분 × 1,458 = 11,664줄
        agg.observe(tick(T0 + i * M, *(row(base=f"C{k:04d}") for k in range(combos))))
    agg.observe(tick(T0 + 8 * M))
    assert agg.pending_count == 8 * combos
    await agg.flush()
    assert [len(b) for _, b in store.lines] == [5_000, 5_000, 1_664]
    # 쓰는 순서 = 분 순서, 분 안에서는 조합 이름순 — 배치가 분을 걸쳐도 그대로다
    order = [
        (int(line.rsplit(" ", 1)[1]), line.split(",", 2)[1])
        for _, b in store.lines
        for line in b
    ]
    assert order == sorted(order) and len(order) == 8 * combos
    assert agg.pending_count == 0


async def test_failed_batch_keeps_unwritten_lines_and_skips_the_rollup() -> None:
    store = FakeCandleStore()
    now = [T0 + 20 * M]
    agg = CandleAggregator(store, clock=lambda: now[0])
    await agg.restore(T0)  # 빈 저장소 — 5m 은 T0 창부터 접는다
    for i in range(8):
        agg.observe(tick(T0 + i * M, *(row(base=f"C{k:04d}") for k in range(1_458))))
    agg.observe(tick(T0 + 8 * M))
    calls = {"n": 0}
    real = store.write_lines

    def second_fails(lines: list[str], bucket: str | None = None) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("Influx 불달 (테스트)")
        real(lines, bucket)

    store.write_lines = second_fails  # type: ignore[method-assign]
    await agg.write_round()
    assert agg.pending_count == 8 * 1_458 - 5_000  # 첫 배치만 빠졌다
    assert store.candles("candles_5m") == []  # 1m 을 다 못 쓴 회차는 롤업하지 않는다
    now[0] += 60
    await agg.write_round()
    assert agg.pending_count == 0 and calls["n"] == 4
    folded = store.candles("candles_5m")
    assert {r.ts for r in folded} == {T0} and len(folded) == 1_458


async def test_restore_edge_queries_carry_the_cap_as_http_timeout() -> None:
    store = FakeCandleStore()
    agg = CandleAggregator(store)
    await agg.restore(T0)
    assert store.edge_timeouts and set(store.edge_timeouts) == {RESTORE_TIMEOUT_SEC}
