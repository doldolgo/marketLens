"""봉 집계기·롤업 테스트 공용 도구 — Influx 를 띄우지 않는 fake 와 틱 조립 (스펙 014)."""

import time
from datetime import UTC, datetime

from app.core.influx import CandleRow, InfluxPoint, InfluxUnavailableError
from app.core.models import Tick, TickRow

# 2026-09-07 00:00 KST — 4h·1d 창 정렬 단언이 쉬운 기준점
T0 = int(datetime(2026, 9, 6, 15, tzinfo=UTC).timestamp())

Key = tuple[str, str, str, int]  # (dom, fx, base, ts)


class FakeCandleStore:
    """버킷별 (dom, fx, base, ts) 유일키로 덮어쓰는 메모리 저장소 — core.candles.CandleStore 시그니처."""

    def __init__(self) -> None:
        self.buckets: dict[str, int] = {"marketlens": 0}  # 이름 → retention 초
        self.data: dict[str, dict[Key, dict[str, float | int | str]]] = {}
        self.writes: list[tuple[str, int]] = []  # 쓰기 호출마다 (버킷, 점 수)
        self.fail = False
        self.list_fail = False
        self.query_fail = False
        self.query_delay = 0.0

    # --- CandleStore ---

    def write(self, points: list[InfluxPoint], bucket: str | None = None) -> None:
        if self.fail:
            raise InfluxUnavailableError("쓰기 실패 (테스트)")
        name = bucket or "marketlens"
        self.writes.append((name, len(points)))
        table = self.data.setdefault(name, {})
        for p in points:
            key = (p.tags["dom"], p.tags["fx"], p.tags["base"], p.ts)
            table.setdefault(key, {}).update(p.fields)

    def query_candles(
        self,
        bucket: str,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        fx: str | None = None,
        base: str | None = None,
    ) -> list[CandleRow]:
        self._maybe_fail_query()
        out = [
            r
            for r in self.candles(bucket)
            if start <= r.ts < stop
            and (dom is None or r.dom == dom)
            and (fx is None or r.fx == fx)
            and (base is None or r.base == base.upper())
        ]
        return out

    def latest_candle_ts(self, bucket: str, *, start: int) -> int | None:
        self._maybe_fail_query()
        ts = [r.ts for r in self.candles(bucket) if r.ts >= start]
        return max(ts) if ts else None

    def earliest_candle_ts(self, bucket: str, *, start: int) -> int | None:
        self._maybe_fail_query()
        ts = [r.ts for r in self.candles(bucket) if r.ts >= start]
        return min(ts) if ts else None

    def list_buckets(self) -> set[str]:
        if self.list_fail:
            raise InfluxUnavailableError("버킷 조회 실패 (테스트)")
        return set(self.buckets)

    def create_bucket(self, name: str, retention_sec: int) -> None:
        if self.fail:
            raise InfluxUnavailableError("버킷 생성 실패 (테스트)")
        self.buckets[name] = retention_sec

    # --- 테스트 편의 ---

    def _maybe_fail_query(self) -> None:
        if self.query_delay:
            time.sleep(self.query_delay)
        if self.query_fail:
            raise InfluxUnavailableError("조회 실패 (테스트)")

    def candles(self, bucket: str) -> list[CandleRow]:
        """그 버킷의 봉 전부 — ts 오름차순, 같은 ts 는 dom·fx·base 순."""
        rows = [
            CandleRow(dom=d, fx=f, base=b, ts=ts, **fields)  # type: ignore[arg-type]
            for (d, f, b, ts), fields in self.data.get(bucket, {}).items()
        ]
        rows.sort(key=lambda r: (r.ts, r.dom, r.fx, r.base))
        return rows

    def seed(self, bucket: str, row: CandleRow) -> None:
        fields = {
            k: v
            for k, v in row.__dict__.items()
            if k not in ("dom", "fx", "base", "ts")
        }
        self.data.setdefault(bucket, {})[(row.dom, row.fx, row.base, row.ts)] = fields


def candle(
    ts: int,
    *,
    base: str = "BTC",
    dom: str = "upbit",
    o: float = 0.5,
    h: float = 0.9,
    lo: float = 0.4,
    c: float = 0.7,
    krw: float = 100_000.0,
    blocked_fwd: int = 0,
    samples: int = 60,
    dom_dep: int = 1,
) -> CandleRow:
    """아래 계층 봉 1개 — rev 는 fwd 의 음수, 나머지는 기본값."""
    return CandleRow(
        dom=dom,
        fx="binance",
        base=base,
        ts=ts,
        fwd_o=o,
        fwd_h=h,
        fwd_l=lo,
        fwd_c=c,
        rev_o=-o,
        rev_h=-lo,
        rev_l=-h,
        rev_c=-c,
        krw=krw,
        usdt=70.0,
        rate=1400.0,
        dom_dep=dom_dep,
        dom_wd=1,
        fx_dep=1,
        fx_wd=1,
        blocked_fwd_sec=blocked_fwd,
        blocked_rev_sec=0,
        samples=samples,
    )


def row(
    fwd: float = 0.5,
    *,
    base: str = "BTC",
    dom: str = "upbit",
    rev: float | None = None,
    dom_price: float = 100_000.0,
    fx_price: float = 70.0,
    rate: float = 1400.0,
    dom_dep: bool | None = True,
    dom_wd: bool | None = True,
    fx_dep: bool | None = True,
    fx_wd: bool | None = True,
) -> TickRow:
    return TickRow(
        dom=dom,
        fx="binance",
        base=base,
        fwd=fwd,
        rev=-fwd if rev is None else rev,
        dom_price=dom_price,
        fx_price=fx_price,
        rate=rate,
        dom_dep=dom_dep,
        dom_wd=dom_wd,
        fx_dep=fx_dep,
        fx_wd=fx_wd,
    )


def tick(ts: int, *rows: TickRow) -> Tick:
    return Tick(ts=ts, rows=tuple(rows), dw_failed=())
