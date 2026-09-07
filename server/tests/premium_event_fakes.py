"""사건 감지기 테스트 공용 도구 — Influx 를 띄우지 않는 fake 와 틱 조립 (스펙 013)."""

import time

from app.core.influx import InfluxPoint, InfluxUnavailableError, PremiumEventRow
from app.core.models import Tick, TickRow
from app.core.premium_events import PremiumEvent, PremiumEventDetector

T0 = 1_700_000_000  # epoch 초


class FakeInflux:
    """(measurement, tags, ts) 유일키로 필드를 합치는 메모리 저장소 — Influx 의 덮어쓰기 규칙."""

    def __init__(self) -> None:
        self.data: dict[
            tuple[str, frozenset[tuple[str, str]], int], dict[str, object]
        ] = {}
        self.write_calls = 0
        self.fail = False
        self.rows: list[PremiumEventRow] = []
        self.query_fail = False
        self.query_delay = 0.0

    def write(self, points: list[InfluxPoint]) -> None:
        self.write_calls += 1
        if self.fail:
            raise InfluxUnavailableError("연결 실패 (테스트)")
        for p in points:
            key = (p.measurement, frozenset(p.tags.items()), p.ts)
            self.data.setdefault(key, {}).update(p.fields)

    def query_premium_events(
        self,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        dir: str | None = None,
        base: str | None = None,
    ) -> list[PremiumEventRow]:
        if self.query_delay:
            time.sleep(self.query_delay)
        if self.query_fail:
            raise InfluxUnavailableError("조회 실패 (테스트)")
        return [r for r in self.rows if start <= r.start_ts < stop]

    def only(self) -> dict[str, object]:
        assert len(self.data) == 1
        return next(iter(self.data.values()))


def row(
    base: str = "SOPH", fwd: float = 0.0, rev: float = 0.0, dom: str = "upbit"
) -> TickRow:
    return TickRow(dom=dom, fx="binance", base=base, fwd=fwd, rev=rev)


def tick(ts: int, *rows: TickRow) -> Tick:
    return Tick(ts=ts, rows=tuple(rows), dw_failed=())


def feed(
    det: PremiumEventDetector, values: list[float | None], t0: int = T0, step: int = 1
) -> int:
    """values[i] 를 t0 + i*step 틱의 SOPH fwd 로 넘긴다. None 은 행 없음(결측). 마지막 ts 를 돌려준다."""
    ts = t0
    for i, v in enumerate(values):
        ts = t0 + i * step
        det.observe(tick(ts) if v is None else tick(ts, row(fwd=v)))
    return ts


def open_one(det: PremiumEventDetector) -> PremiumEvent:
    evs = det.open_events()
    assert len(evs) == 1
    return evs[0]


def restored_row(
    start_ts: int, last_ts: int, end_ts: int = 0, base: str = "SOPH"
) -> PremiumEventRow:
    return PremiumEventRow(
        dom="upbit",
        fx="binance",
        base=base,
        dir="kimp",
        start_ts=start_ts,
        end_ts=end_ts,
        duration_seconds=end_ts - start_ts if end_ts else 0,
        max_percent=1.5,
        max_ts=start_ts,
        last_ts=last_ts,
        samples=10,
        enter_percent=1.0,
        exit_percent=0.5,
    )
