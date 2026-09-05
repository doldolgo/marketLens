"""즉시 갱신 트리거 — REST 호출 수·현재 행 수·실패 스트림·USDT 경고·직렬화 (스펙 001 §3.9, §4)."""

import asyncio
from datetime import UTC, datetime

import httpx

from app.core.collect import CollectService
from app.core.errors import ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.quotes import QuoteSink
from app.core.universe import UniverseRefresher
from tests.conftest import FakeStream, make_row
from tests.test_universe import FakeForeign

NOW = datetime.now(UTC)


class GatedDomestic(FakeStream):
    """fetch_markets 가 gate 를 기다린다 — 동시 호출 직렬화 확인용."""

    def __init__(self, id_: str, result: list[str] | Exception) -> None:
        super().__init__(id_)
        self._result = result
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.gate = asyncio.Event()
        self.gate.set()

    async def fetch_markets(self, client: httpx.AsyncClient) -> list[str]:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self.gate.wait()
            if isinstance(self._result, Exception):
                raise self._result
            return list(self._result)
        finally:
            self.active -= 1

    def set_markets(self, codes: list[str]) -> None:
        pass


class WalletStub:
    def __init__(self) -> None:
        self.forced: list[bool] = []

    async def refresh_if_due(
        self, client: httpx.AsyncClient, *, force: bool = False
    ) -> dict[str, int] | None:
        self.forced.append(force)
        return {"bithumb": 1}

    def apply(self, rows: list, exchange: str) -> None:  # type: ignore[type-arg]
        pass

    def availability(self) -> dict[str, bool]:
        return {"bithumb": True}

    def warnings(self) -> list[str]:
        return ["bithumb 경고"]

    def failed(self) -> list[str]:
        return ["upbit"]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )


def build(
    upbit: GatedDomestic, bithumb: GatedDomestic, *, wallet: WalletStub | None = None
) -> tuple[CollectService, LiveStore]:
    store = LiveStore()
    universe = UniverseRefresher(
        sink=QuoteSink(store),
        streams=[upbit, bithumb],
        foreign=FakeForeign({"BTC"}, calls=0),
        client=_client(),
    )
    service = CollectService(
        store=store,
        universe=universe,
        streams=[upbit, bithumb],
        client=_client(),
        wallet=wallet,
    )
    return service, store


async def test_trigger_reports_calls_saved_failures_and_warnings() -> None:
    upbit = GatedDomestic("upbit", ["KRW-BTC", "KRW-USDT"])
    upbit.succeed()
    bithumb = GatedDomestic(
        "bithumb", ExchangeTimeoutError("bithumb", "https://api.bithumb.com/x", "느림")
    )
    bithumb.fail("stale_stream", "스트림 정체")
    wallet = WalletStub()
    service, store = build(upbit, bithumb, wallet=wallet)
    store.put_rows([make_row("upbit", "BTC"), make_row("binance", "BTC")], NOW)
    store.set_rate("upbit", 1400.0, 1390.0, NOW)

    result = await service.refresh_now()
    assert upbit.calls == 1 and result.calls == {"upbit": 1, "binance": 0, "bithumb": 1}
    assert result.saved == {"upbit": 1, "bithumb": 0, "binance": 1}
    assert result.rates_observed == ["upbit"]
    assert result.failures == [
        {"exchange": "bithumb", "error_code": "exchange_timeout", "message": "느림"},
        {"exchange": "bithumb", "error_code": "stale_stream", "message": "스트림 정체"},
    ]
    assert result.warnings == [
        "bithumb 경고",
        "KRW-USDT 호가가 없어 USDT 시세를 못 구한 거래소: bithumb (해당 국내 거래소의 김프 계산은 빠진다).",
    ]
    assert wallet.forced == [True]  # 006 조회 즉시 실행
    assert result.wallet_status_available == {"bithumb": True}
    assert result.duration_ms >= 0 and result.fetched_at > 1_700_000_000_000


async def test_concurrent_triggers_are_serialized() -> None:
    upbit = GatedDomestic("upbit", ["KRW-BTC"])
    bithumb = GatedDomestic("bithumb", ["KRW-BTC"])
    service, _ = build(upbit, bithumb)
    upbit.gate.clear()
    first = asyncio.create_task(service.refresh_now())
    second = asyncio.create_task(service.refresh_now())
    await asyncio.sleep(0.01)
    assert upbit.calls == 1  # 두 번째는 락에서 기다린다
    upbit.gate.set()
    await asyncio.gather(first, second)
    assert upbit.calls == 2 and upbit.max_active == 1
