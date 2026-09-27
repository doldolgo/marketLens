"""landing 테스트 공용 도구 — Redis·Influx 는 fake, 시계는 손으로 돌린다 (스펙 022 §4)."""

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.influx import CandleRow, InfluxUnavailableError, PremiumEventRow
from app.features.landing.service import LandingService
from app.main import create_app

NOW = 1_790_509_107  # 고정 시계의 시작(epoch 초)


class Clock:
    """벽시계·단조 시계 둘 다 이것 하나 — `advance` 로만 흐른다."""

    def __init__(self, t: float = NOW) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += sec


def row(
    sym: str,
    *,
    dom: str = "upbit",
    fx: str = "binance",
    fwd: float = 0.0,
    rev: float = 0.0,
    status: str = "ok",
    dep_dom: bool | None = True,
    wd_dom: bool | None = True,
    dep_fx: bool | None = True,
    wd_fx: bool | None = True,
    slip_fwd: float = 0.05,
    slip_rev: float = 0.04,
    krw: float = 12.4,
    usd: float = 0.0089,
    net_dom: str | None = "ERC20",
    net_fx: str | None = "Ethereum",
) -> dict:
    """표(`spreads:latest`) 행 하나 — 003 의 행 19키 그대로(camelCase)."""
    return {
        "sym": sym,
        "dom": dom,
        "fx": fx,
        "fwd": fwd,
        "rev": rev,
        "usd": usd,
        "spark": [],
        "status": status,
        "age": 0.1,
        "slipFwd": slip_fwd,
        "slipRev": slip_rev,
        "krw": krw,
        "netDom": net_dom,
        "depDom": dep_dom,
        "wdDom": wd_dom,
        "depFx": dep_fx,
        "wdFx": wd_fx,
        "netFx": net_fx,
        "dayChg": None,
    }


def table(
    rows: list[dict], *, rate: float = 1360.0, received: int = 1_790_509_107_000
) -> str:
    """017 게시기가 `spreads:latest` 에 넣는 표 한 장(JSON 문자열)."""
    return json.dumps(
        {
            "rate": rate,
            "notional": 1000.0,
            "rows": rows,
            "warnings": [],
            "dataReceivedAt": received,
            "fetchedAt": received + 142,
        }
    )


class FakeBus:
    """`spreads:latest` 읽기 자리 — 읽은 횟수를 센다. `fail` 이면 연결 실패처럼 예외를 낸다."""

    def __init__(self, text: str | None = None, *, delay: float = 0.0) -> None:
        self.text = text
        self.fail = False
        self.reads = 0
        self.delay = delay

    async def latest(self) -> str | None:
        self.reads += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("Redis 불달 (테스트)")
        return self.text


class FakeInflux:
    """core.influx.InfluxClient 의 봉·사건 조회 시그니처 — 부른 인자를 남긴다. 순서는 넣은 그대로 돌려준다."""

    def __init__(self) -> None:
        self.candles: list[CandleRow] = []
        self.events: list[PremiumEventRow] = []
        self.fail = False
        self.candle_calls: list[dict] = []
        self.event_calls: list[dict] = []

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
        self.candle_calls.append(
            {
                "bucket": bucket,
                "start": start,
                "stop": stop,
                "dom": dom,
                "fx": fx,
                "base": base,
            }
        )
        if self.fail:
            raise InfluxUnavailableError("조회 실패 (테스트)")
        return [
            c
            for c in self.candles
            if (dom is None or c.dom == dom)
            and (fx is None or c.fx == fx)
            and (base is None or c.base == base.upper())
            and start <= c.ts < stop
        ]

    def query_premium_events(
        self,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        dir: str | None = None,
        base: str | None = None,
    ) -> list[PremiumEventRow]:
        self.event_calls.append({"start": start, "stop": stop})
        if self.fail:
            raise InfluxUnavailableError("조회 실패 (테스트)")
        return [e for e in self.events if start <= e.start_ts < stop]


def candle(
    ts: int,
    *,
    dom: str = "upbit",
    fx: str = "binance",
    base: str = "BTC",
    fwd_c: float = 0.0,
    rev_c: float = 0.0,
) -> CandleRow:
    """1분 봉 하나 — 랜딩은 종가 두 개만 본다. 나머지는 아무 값."""
    return CandleRow(
        dom=dom,
        fx=fx,
        base=base,
        ts=ts,
        fwd_o=fwd_c,
        fwd_h=fwd_c,
        fwd_l=fwd_c,
        fwd_c=fwd_c,
        rev_o=rev_c,
        rev_h=rev_c,
        rev_l=rev_c,
        rev_c=rev_c,
        krw=100.0,
        usdt=0.07,
        rate=1360.0,
        dom_dep=1,
        dom_wd=1,
        fx_dep=1,
        fx_wd=1,
        blocked_fwd_sec=0,
        blocked_rev_sec=0,
        samples=60,
    )


def event(
    base: str,
    start_ts: int,
    *,
    dom: str = "upbit",
    fx: str = "binance",
    dir: str = "kimp",
    max_percent: float = 1.5,
    end_ts: int = 0,
    last_ts: int | None = None,
) -> PremiumEventRow:
    """사건 점 하나 — `end_ts` 0 은 진행 중."""
    return PremiumEventRow(
        dom=dom,
        fx=fx,
        base=base,
        dir=dir,
        start_ts=start_ts,
        end_ts=end_ts,
        duration_seconds=end_ts - start_ts if end_ts else 0,
        max_percent=max_percent,
        max_ts=start_ts,
        last_ts=last_ts if last_ts is not None else (end_ts or start_ts + 90),
        samples=90,
        enter_percent=1.0,
        exit_percent=0.5,
    )


def make_app(
    bus: object | None = None,
    influx: FakeInflux | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """lifespan 없이 앱 상태를 직접 채운다 — `bus`·`influx` 가 None 이면 그 저장소가 없는 것과 같다."""
    app = create_app()
    app.state.spreads_bus = bus
    app.state.influx = influx
    clock = clock if clock is not None else Clock()
    app.state.landing = LandingService(wall=clock, mono=clock)
    return app


def make_client(
    bus: object | None = None,
    influx: FakeInflux | None = None,
    clock: Clock | None = None,
) -> TestClient:
    return TestClient(make_app(bus, influx, clock))
