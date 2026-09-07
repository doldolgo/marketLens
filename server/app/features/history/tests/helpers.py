"""history 테스트 공용 도구 — Influx 를 띄우지 않고 fake 리더로 (architecture.md 원칙)."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.influx import (
    CandleRow,
    InfluxUnavailableError,
    PremiumEventRow,
    PremiumRow,
)
from app.core.live_store import LiveStore
from app.core.premium_events import PremiumEventDetector
from app.main import create_app


class FakeInfluxReader:
    """core.influx.InfluxClient 의 query_premium 시그니처를 그대로 흉내낸다."""

    def __init__(self) -> None:
        # (dom, fx, base) → [(ts, fwd, rev)] — seed 순서 무관, 조회는 ts 오름차순
        self._rows: dict[tuple[str, str, str], list[tuple[int, float, float]]] = {}
        self._events: list[PremiumEventRow] = []
        self._candles: dict[str, list[CandleRow]] = {}  # 014 계층 버킷 이름 → 봉
        self.fail = False

    def seed(
        self, dom: str, fx: str, base: str, rows: list[tuple[int, float, float]]
    ) -> None:
        self._rows.setdefault((dom, fx, base.upper()), []).extend(rows)

    def query_premium(
        self, *, dom: str, fx: str, base: str | None, start: int, stop: int
    ) -> list[PremiumRow]:
        if self.fail:
            raise InfluxUnavailableError("연결 실패 (테스트)")
        out: list[PremiumRow] = []
        for (d, f, b), rows in self._rows.items():
            if d != dom or f != fx:
                continue
            if base is not None and b != base.upper():
                continue
            for ts, fwd, rev in rows:
                if start <= ts < stop:
                    out.append(PremiumRow(base=b, ts=ts, fwd=fwd, rev=rev))
        out.sort(key=lambda r: r.ts)
        return out

    def seed_event(
        self,
        base: str,
        start_ts: int,
        end_ts: int,
        *,
        dom: str = "upbit",
        dir: str = "kimp",
        max_percent: float = 1.5,
        last_ts: int | None = None,
        samples: int = 10,
    ) -> None:
        """사건 점 1개 — end_ts 0 은 진행 중(고아 점 검증용)."""
        self._events.append(
            PremiumEventRow(
                dom=dom,
                fx="binance",
                base=base.upper(),
                dir=dir,
                start_ts=start_ts,
                end_ts=end_ts,
                duration_seconds=end_ts - start_ts if end_ts else 0,
                max_percent=max_percent,
                max_ts=start_ts,
                last_ts=last_ts if last_ts is not None else (end_ts or start_ts),
                samples=samples,
                enter_percent=1.0,
                exit_percent=0.5,
            )
        )

    def query_premium_events(
        self,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        dir: str | None = None,
        base: str | None = None,
    ) -> list[PremiumEventRow]:
        if self.fail:
            raise InfluxUnavailableError("연결 실패 (테스트)")
        return [
            r
            for r in self._events
            if start <= r.start_ts < stop
            and (dom is None or r.dom == dom)
            and (dir is None or r.dir == dir)
            and (base is None or r.base == base.upper())
        ]

    def seed_candle(
        self,
        bucket: str,
        ts: int,
        *,
        base: str = "BTC",
        dom: str = "upbit",
        fwd: tuple[float, float, float, float] = (0.5, 0.9, 0.4, 0.7),
        rev: tuple[float, float, float, float] = (-0.5, -0.4, -0.9, -0.7),
        dw: tuple[int, int, int, int] = (1, 1, 1, 1),
        blocked: tuple[int, int] = (0, 0),
        samples: int = 60,
    ) -> None:
        """봉 점 1개 — dw = (dom_dep, dom_wd, fx_dep, fx_wd) 저장값, blocked = (fwd, rev) 막힌 초."""
        self._candles.setdefault(bucket, []).append(
            CandleRow(
                dom=dom,
                fx="binance",
                base=base.upper(),
                ts=ts,
                fwd_o=fwd[0],
                fwd_h=fwd[1],
                fwd_l=fwd[2],
                fwd_c=fwd[3],
                rev_o=rev[0],
                rev_h=rev[1],
                rev_l=rev[2],
                rev_c=rev[3],
                krw=168_450_000.0,
                usdt=112_010.5,
                rate=1502.5,
                dom_dep=dw[0],
                dom_wd=dw[1],
                fx_dep=dw[2],
                fx_wd=dw[3],
                blocked_fwd_sec=blocked[0],
                blocked_rev_sec=blocked[1],
                samples=samples,
            )
        )

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
        if self.fail:
            raise InfluxUnavailableError("연결 실패 (테스트)")
        out = [
            r
            for r in self._candles.get(bucket, [])
            if start <= r.ts < stop
            and (dom is None or r.dom == dom)
            and (fx is None or r.fx == fx)
            and (base is None or r.base == base.upper())
        ]
        out.sort(key=lambda r: r.ts)
        return out


def make_client(
    reader: FakeInfluxReader | None,
    store: LiveStore | None = None,
    events: PremiumEventDetector | None = None,
) -> TestClient:
    """lifespan 없이 앱 상태를 직접 채운다 — 스트림·틱 루프·네트워크가 돌지 않는다.

    `reader` 는 Influx 자리(None = INFLUX_TOKEN 없음), `store` 는 메모리 시세 자리다 —
    저장소 장애 검증은 둘을 따로 채워 메모리 조회가 살아 있는지 본다 (§3.1).
    """
    app: FastAPI = create_app()
    app.state.live_store = store if store is not None else LiveStore()
    app.state.settings = SimpleNamespace(refresh_token=None)
    app.state.influx = reader
    if events is not None:
        app.state.premium_events = events  # 013 진행 중 사건 자리
    return TestClient(app)
