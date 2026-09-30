"""수집 기동 순서 — 우주·스트림을 복원보다 먼저, 틱 루프·인계·게시기·허브는 복원 4개가 끝난 뒤 (스펙 001 §3.6·009 §3.7, 2026-09-28).

종료 때는 사건 감지기가 쓰기 태스크를 멈추기 전에 사본 저장·미전송 쓰기를 한 번 한다(013 §3.3).

lifespan 을 실제로 돌린다(소켓·REST 거부, Redis 불달, Influx 없음). 시작·복원 호출에 기록만 끼운다.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.candles import CandleAggregator
from app.core.outages import OutageTracker
from app.core.premium_events import PremiumEventDetector
from app.core.streams.binance import BinanceStream
from app.core.streams.bitget import BitgetStream
from app.core.streams.bithumb import BithumbStream
from app.core.streams.bybit import BybitStream
from app.core.streams.upbit import UpbitStream
from app.core.tick_store import TickRelay
from app.core.ticks import TickLoop
from app.core.universe import UniverseRefresher
from app.features.spreads.hub import SpreadsHub
from app.features.spreads.push import SpreadsPublisher
from tests.test_raw_archive import _boot


def test_streams_start_before_restores_and_the_tick_loop_after_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _boot(monkeypatch)
    order: list[str] = []

    def mark_start(cls: type, name: str) -> None:
        # 시작은 기록만 — 스트림·틱이 실제로 돌 필요가 없다
        monkeypatch.setattr(cls, "start", lambda self: order.append(name))

    def mark_restore(cls: type, name: str) -> None:
        real = cls.restore

        async def wrapped(self: Any, *args: Any) -> None:
            order.append(name)
            await real(self, *args)

        monkeypatch.setattr(cls, "restore", wrapped)

    mark_start(UniverseRefresher, "universe")
    for cls in (UpbitStream, BithumbStream, BinanceStream, BybitStream, BitgetStream):
        mark_start(cls, "stream")
    mark_restore(OutageTracker, "outages")
    mark_restore(PremiumEventDetector, "events")
    mark_restore(CandleAggregator, "candles")
    import app.main as main_mod

    real_spark = main_mod.restore_spark

    async def spark(*args: Any) -> int:
        order.append("spark")
        return await real_spark(*args)

    monkeypatch.setattr(main_mod, "restore_spark", spark)
    real_final = PremiumEventDetector.final_round

    async def final(self: PremiumEventDetector) -> None:
        order.append("final")
        await real_final(self)

    monkeypatch.setattr(PremiumEventDetector, "final_round", final)
    mark_start(TickLoop, "ticks")
    mark_start(TickRelay, "handoff")
    mark_start(SpreadsPublisher, "publisher")
    mark_start(SpreadsHub, "hub")

    with TestClient(app):
        pass
    assert order == ["universe"] + ["stream"] * 5 + [
        "outages",
        "events",
        "candles",
        "spark",
        "ticks",
        "handoff",
        "publisher",
        "hub",
        "final",  # 종료 — 사건 사본 저장·미전송 쓰기 1회(013 §3.3)
    ]
