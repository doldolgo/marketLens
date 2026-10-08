"""perp 우주 — 10초 목록 회차·매초 확정·같은 우주 재수신 무동작·실패 유지·즉시 갱신 트리거 (스펙 046 §3.2, §4)."""

import asyncio
import logging

import httpx
import pytest

from app.core.collect import CollectService
from app.core.errors import ExchangeApiError
from app.core.live_store import LiveStore
from app.core.perp import PerpSink
from app.core.perp_universe import PERP_LIST_INTERVAL, PerpUniverse
from app.core.quotes import QuoteSink
from app.core.universe import UNIVERSE_INTERVAL, UniverseRefresher
from tests.test_universe import FakeDomestic, FakeForeign


class FakePerp(FakeForeign):
    """perp 원천 fake — FakeForeign 과 같은 네 면. set_universe 호출을 전부 기록한다."""


class CountingPerp(FakePerp):
    """배정이 같으면 무동작인 실제 커넥터처럼 — 바뀐 우주만 센다."""

    def __init__(self, bases: set[str], id: str) -> None:
        super().__init__(bases, id=id)
        self.changes = 0
        self._current: set[str] | None = None

    def set_universe(self, bases: set[str]) -> None:
        super().set_universe(bases)
        mine = {b for b in bases if b in self._bases}
        if mine != self._current:
            self._current = mine
            self.changes += 1


def build(
    perps: list[FakePerp],
    *,
    domestic: list[str] | None = None,
    foreign: set[str] | None = None,
) -> tuple[UniverseRefresher, PerpUniverse, LiveStore, PerpSink]:
    store = LiveStore()
    sink = QuoteSink(store)
    perp_sink = PerpSink(store)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    universe = PerpUniverse(sink=perp_sink, sources=perps, client=client)
    upbit = FakeDomestic(
        "upbit", [domestic if domestic is not None else ["KRW-BTC", "KRW-ETH"]]
    )
    binance = FakeForeign(foreign if foreign is not None else {"BTC", "ETH"})
    refresher = UniverseRefresher(
        sink=sink, streams=[upbit], foreigns=[binance], client=client, perps=universe
    )
    return refresher, universe, store, perp_sink


async def test_universe_is_fixed_every_second_from_the_kimp_universe() -> None:
    a = FakePerp({"BTC", "PEPE", "MOG"}, id="binance_perp")
    b = FakePerp({"BTC", "PEPE", "ETH"}, id="bybit_perp")
    c = FakePerp({"SOL", "ETH"}, id="bitget_perp")
    refresher, universe, _, perp_sink = build([a, b, c])
    await universe.refresh()
    await refresher.refresh()
    # 김프 우주 = {BTC, ETH}. 2곳 이상 = {BTC, PEPE, ETH}, 김프 ∩ 1곳 이상 = {BTC, ETH} → 합집합
    assert universe.universe == {"BTC", "PEPE", "ETH"}
    assert perp_sink.universe == {"BTC", "PEPE", "ETH"}
    assert a.universes == [{"BTC", "PEPE", "ETH"}]  # 원천마다 우주 전체를 받는다
    assert c.universes == [{"BTC", "PEPE", "ETH"}]
    await refresher.refresh()
    assert len(a.universes) == 2  # 매초 다시 확정해 넘긴다 — 같으면 커넥터가 무동작


async def test_single_source_base_enters_only_when_in_the_kimp_universe() -> None:
    a = FakePerp({"XRP", "BTC"}, id="binance_perp")
    b = FakePerp({"BTC"}, id="bybit_perp")
    refresher, universe, store, perp_sink = build(
        [a, b], domestic=["KRW-XRP", "KRW-DOGE"], foreign={"XRP", "DOGE"}
    )
    await universe.refresh()
    await refresher.refresh()
    assert universe.universe == {"XRP", "BTC"}
    # 원천이 하나뿐이고 김프 우주에도 없는 DOGE 가 perp 행으로 있었다면 그 초에 지워진다
    perp_sink.set_universe({"XRP", "BTC", "DOGE"})
    perp_sink.quote(
        source="binance_perp",
        base="DOGE",
        native_symbol="DOGEUSDT",
        multiplier=1,
        bid=1.0,
        ask=1.1,
        bid_size=1.0,
        ask_size=1.0,
        quote_ts=1,
        received_at_ms=1,
    )
    assert store.perp_row("binance_perp", "DOGE") is not None
    await refresher.refresh()
    assert store.perp_row("binance_perp", "DOGE") is None


async def test_same_universe_again_sends_nothing_to_the_connector() -> None:
    a = CountingPerp({"BTC", "PEPE"}, id="binance_perp")
    b = CountingPerp({"BTC", "PEPE"}, id="bybit_perp")
    refresher, universe, _, _ = build([a, b])
    await universe.refresh()
    for _ in range(3):
        await refresher.refresh()
    assert a.changes == 1 and b.changes == 1


async def test_lists_are_fetched_every_ten_seconds_in_parallel() -> None:
    a = FakePerp({"BTC"}, id="binance_perp")
    b = FakePerp({"BTC"}, id="bybit_perp")
    _, universe, _, _ = build([a, b])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)
        if len(sleeps) >= 3:
            await asyncio.Event().wait()

    universe._sleep = sleep  # type: ignore[assignment]
    universe.start()
    await asyncio.sleep(0.05)
    await universe.aclose()
    assert sleeps[:3] == [PERP_LIST_INTERVAL] * 3
    assert a.refreshes == 3 and b.refreshes == 3
    assert PERP_LIST_INTERVAL == 10.0 and UNIVERSE_INTERVAL == 1.0


async def test_list_failure_keeps_the_previous_list_and_logs_once_a_minute(
    caplog: pytest.LogCaptureFixture,
) -> None:
    a = FakePerp({"BTC", "PEPE"}, id="binance_perp")
    b = FakePerp({"BTC", "PEPE"}, id="bybit_perp")
    refresher, universe, _, _ = build([a, b])
    await universe.refresh()
    await refresher.refresh()
    assert universe.universe == {"BTC", "PEPE"}

    async def boom(client: httpx.AsyncClient) -> int:
        raise ExchangeApiError("bybit_perp", "u", "down", kind="network")

    b.refresh = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="marketlens.perp_universe"):
        for _ in range(3):
            outcome = await universe.refresh()
    assert [e.exchange for e in outcome.failures] == ["bybit_perp"]
    assert outcome.calls == {"binance_perp": 1}
    await refresher.refresh()
    assert universe.universe == {"BTC", "PEPE"}  # 직전 목록 유지
    warnings = [r for r in caplog.records if "bybit_perp" in r.getMessage()]
    assert len(warnings) == 1 and "직전 목록 유지" in warnings[0].getMessage()


async def test_refresh_trigger_fetches_perp_lists_and_reports_perp_rows() -> None:
    a = FakePerp({"BTC"}, id="binance_perp", calls=2)
    b = FakePerp({"BTC"}, id="bybit_perp")
    refresher, universe, store, perp_sink = build([a, b])
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    collector = CollectService(
        store=store, universe=refresher, streams=[], client=client, perps=universe
    )
    summary = await collector.refresh_now()
    assert a.refreshes == 1 and b.refreshes == 1
    assert summary.calls["binance_perp"] == 2 and summary.calls["bybit_perp"] == 1
    assert list(summary.saved) == [
        "upbit",
        "bithumb",
        "binance",
        "bybit",
        "bitget",
        "okx",
        "binance_perp",
        "bybit_perp",
        "bitget_perp",
    ]
    perp_sink.quote(
        source="bybit_perp",
        base="BTC",
        native_symbol="BTCUSDT",
        multiplier=1,
        bid=1.0,
        ask=1.1,
        bid_size=1.0,
        ask_size=1.0,
        quote_ts=1,
        received_at_ms=1,
    )
    summary = await collector.refresh_now()
    assert summary.saved["bybit_perp"] == 1 and summary.saved["binance_perp"] == 0
