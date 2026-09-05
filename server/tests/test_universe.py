"""마켓 우주 — 교집합·USDT 제외·구독 목록·상폐 소멸·실패 유지·재시도 주기 (스펙 001 §3.2, §4)."""

import asyncio
from datetime import UTC, datetime

import httpx

from app.core.errors import ExchangeApiError
from app.core.live_store import LiveStore
from app.core.quotes import QuoteSink
from app.core.universe import RETRY_INTERVAL, UNIVERSE_INTERVAL, UniverseRefresher
from tests.conftest import make_row

NOW = datetime.now(UTC)


class FakeDomestic:
    """마켓 목록을 순서대로 돌려주는(또는 예외를 던지는) 가짜 국내 스트림. 마지막 결과는 반복."""

    def __init__(self, id_: str, results: list[list[str] | Exception]) -> None:
        self.id = id_
        self._results = list(results)
        self.calls = 0
        self.markets: list[list[str]] = []

    async def fetch_markets(self, client: httpx.AsyncClient) -> list[str]:
        self.calls += 1
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, Exception):
            raise result
        return list(result)

    def set_markets(self, codes: list[str]) -> None:
        self.markets.append(list(codes))


class FakeForeign:
    """바이낸스 심볼 집합 fake — 앞의 `failures` 번은 예외, 그 뒤 성공."""

    def __init__(self, bases: set[str], calls: int = 1, failures: int = 0) -> None:
        self._bases = bases
        self._calls = calls
        self._failures = failures
        self.refreshes = 0

    async def refresh(self, client: httpx.AsyncClient) -> int:
        self.refreshes += 1
        if self.refreshes <= self._failures:
            raise ExchangeApiError("binance", "u", "down", kind="network")
        return self._calls

    def bases(self) -> set[str]:
        return set(self._bases) if self.refreshes > self._failures else set()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )


def build(
    upbit: list[list[str] | Exception],
    bithumb: list[list[str] | Exception],
    foreign: set[str] | FakeForeign,
) -> tuple[UniverseRefresher, FakeDomestic, FakeDomestic, LiveStore, QuoteSink]:
    store = LiveStore()
    sink = QuoteSink(store)
    up = FakeDomestic("upbit", upbit)
    bt = FakeDomestic("bithumb", bithumb)
    if not isinstance(foreign, FakeForeign):
        foreign = FakeForeign(foreign)
    refresher = UniverseRefresher(
        sink=sink, streams=[up, bt], foreign=foreign, client=_client()
    )
    return refresher, up, bt, store, sink


async def run_until_full_refresh(refresher: UniverseRefresher) -> list[float]:
    """기동 재시도가 끝나 10분 주기에 들어갈 때까지 돌리고 sleep 호출 기록을 돌려준다."""
    slept: list[float] = []
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if seconds == UNIVERSE_INTERVAL:
            stop.set()
            await asyncio.Event().wait()

    refresher._sleep = sleep  # type: ignore[attr-defined]
    refresher.start()
    await asyncio.wait_for(stop.wait(), 1.0)
    await refresher.aclose()
    return slept


async def test_universe_is_intersection_and_streams_get_their_own_krw_list() -> None:
    refresher, up, bt, _, sink = build(
        [["KRW-BTC", "KRW-USDT", "KRW-ONLYKR"]],
        [["KRW-BTC", "KRW-XRP", "KRW-USDT"]],
        {"BTC", "XRP", "SOL"},
    )
    outcome = await refresher.refresh()
    assert refresher.universe == {"BTC", "XRP"}  # USDT·국내 전용·해외 전용은 없다
    assert sink.universe == {"BTC", "XRP"}
    # 국내 거래소는 자기 KRW 전 마켓(KRW-USDT·국내 전용 포함)을 받는다
    assert up.markets[-1] == ["KRW-BTC", "KRW-USDT", "KRW-ONLYKR"]
    assert bt.markets[-1] == ["KRW-BTC", "KRW-XRP", "KRW-USDT"]
    assert outcome.calls == {"upbit": 1, "bithumb": 1, "binance": 1}
    assert outcome.failures == []


async def test_dropped_base_disappears_from_memory_on_refresh() -> None:
    refresher, _, _, store, _ = build(
        [["KRW-BTC", "KRW-ETH"], ["KRW-BTC"]], [["KRW-BTC"]], {"BTC", "ETH"}
    )
    await refresher.refresh()
    store.put_rows([make_row("upbit", "BTC"), make_row("upbit", "ETH")], NOW)
    await refresher.refresh()  # 업비트 목록에서 ETH 상폐
    assert refresher.universe == {"BTC"}
    assert store.get("upbit", "ETH") is None and store.get("upbit", "BTC") is not None


async def test_failure_keeps_previous_list_and_reports() -> None:
    err = ExchangeApiError("bithumb", "https://api.bithumb.com/v1/market/all", "500")
    refresher, up, bt, _, _ = build(
        [["KRW-BTC"]], [["KRW-BTC", "KRW-XRP"], err], {"BTC", "XRP"}
    )
    await refresher.refresh()
    outcome = await refresher.refresh()
    assert outcome.failures == [err] and "bithumb" not in outcome.calls
    assert refresher.universe == {"BTC", "XRP"}  # 직전 목록 유지
    assert bt.markets[-1] == ["KRW-BTC", "KRW-XRP"]
    assert refresher.missing() == []


async def test_startup_retries_only_missing_exchanges_every_five_seconds() -> None:
    err = ExchangeApiError("bithumb", "u", "down", kind="network")
    foreign = FakeForeign({"BTC"})
    refresher, up, bt, _, _ = build([["KRW-BTC"]], [err, err, ["KRW-BTC"]], foreign)
    slept = await run_until_full_refresh(refresher)
    # 첫 갱신 실패 → 5초 재시도 2번(빗썸만) → 성공 후 10분 주기
    assert slept == [RETRY_INTERVAL, RETRY_INTERVAL, UNIVERSE_INTERVAL]
    assert up.calls == 1 and bt.calls == 3
    assert foreign.refreshes == 1  # 국내만 재시도하는 동안 바이낸스 심볼 REST 는 없다
    assert bt.markets[-1] == ["KRW-BTC"] and up.markets[0] == ["KRW-BTC"]


async def test_startup_retries_foreign_symbols_alone_when_only_they_failed() -> None:
    foreign = FakeForeign({"BTC"}, failures=3)
    refresher, up, bt, _, _ = build([["KRW-BTC"]], [["KRW-BTC"]], foreign)
    first = await refresher.refresh()
    assert refresher.missing() == ["binance"] and len(first.failures) == 1
    assert refresher.universe == set()  # 심볼이 없으면 우주가 비어 행이 저장되지 않는다
    slept = await run_until_full_refresh(refresher)
    # 기동 전체 갱신(실패) → 5초 재시도 2번은 바이낸스만 부른다 → 성공 후 10분 주기
    assert slept == [RETRY_INTERVAL, RETRY_INTERVAL, UNIVERSE_INTERVAL]
    assert foreign.refreshes == 4 and up.calls == 2 and bt.calls == 2
    assert refresher.missing() == [] and refresher.universe == {"BTC"}


async def test_full_refresh_calls_every_exchange_including_foreign() -> None:
    foreign = FakeForeign({"BTC"})
    refresher, up, bt, _, _ = build([["KRW-BTC"]], [["KRW-BTC"]], foreign)
    await refresher.refresh()
    await refresher.refresh()  # 10분 주기·/refresh 트리거 — 셋을 전부 부른다
    assert (up.calls, bt.calls, foreign.refreshes) == (2, 2, 2)
