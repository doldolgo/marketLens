"""마켓 우주 — 교집합·USDT 제외·구독 목록·상폐 소멸·매초 회차·실패 유지·로그 억제 (스펙 001 §3.2, §4)."""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
import pytest

from app.core.errors import ExchangeApiError
from app.core.live_store import LiveStore
from app.core.quotes import QuoteSink
from app.core.universe import LOG_SUPPRESS_SEC, UNIVERSE_INTERVAL, UniverseRefresher
from tests.conftest import make_row

NOW = datetime.now(UTC)
LOGGER = "marketlens.universe"


class FakeDomestic:
    """마켓 목록을 순서대로 돌려주는(또는 예외를 던지는) 가짜 국내 스트림. 마지막 결과는 반복."""

    def __init__(self, id_: str, results: list[list[str] | Exception]) -> None:
        self.id = id_
        self._results = list(results)
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.markets: list[list[str]] = []
        self.gate: asyncio.Event | None = None  # 있으면 fetch 가 이 이벤트를 기다린다

    async def fetch_markets(self, client: httpx.AsyncClient) -> list[str]:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            result = (
                self._results.pop(0) if len(self._results) > 1 else self._results[0]
            )
            if isinstance(result, Exception):
                raise result
            return list(result)
        finally:
            self.active -= 1

    def set_markets(self, codes: list[str]) -> None:
        self.markets.append(list(codes))


class FakeForeign:
    """바이낸스 심볼 집합 fake — 앞의 `failures` 번은 예외, 그 뒤 성공."""

    def __init__(self, bases: set[str], calls: int = 1, failures: int = 0) -> None:
        self._bases = bases
        self._calls = calls
        self._failures = failures
        self.refreshes = 0
        self.universes: list[set[str]] = []  # set_universe 로 받은 우주 (012 §3.3)

    async def refresh(self, client: httpx.AsyncClient) -> int:
        self.refreshes += 1
        if self.refreshes <= self._failures:
            raise ExchangeApiError("binance", "u", "down", kind="network")
        return self._calls

    def bases(self) -> set[str]:
        return set(self._bases) if self.refreshes > self._failures else set()

    def set_universe(self, bases: set[str]) -> None:
        self.universes.append(set(bases))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )


def build(
    upbit: list[list[str] | Exception],
    bithumb: list[list[str] | Exception],
    foreign: set[str] | FakeForeign,
    clock: Clock | None = None,
) -> tuple[UniverseRefresher, FakeDomestic, FakeDomestic, LiveStore, QuoteSink]:
    store = LiveStore()
    sink = QuoteSink(store)
    up = FakeDomestic("upbit", upbit)
    bt = FakeDomestic("bithumb", bithumb)
    if not isinstance(foreign, FakeForeign):
        foreign = FakeForeign(foreign)
    refresher = UniverseRefresher(
        sink=sink,
        streams=[up, bt],
        foreign=foreign,
        client=_client(),
        monotonic=clock or Clock(),
    )
    return refresher, up, bt, store, sink


async def run_rounds(refresher: UniverseRefresher, rounds: int) -> list[float]:
    """갱신 루프를 `rounds` 회차 돌리고(회차 사이 sleep 은 즉시 반환) sleep 기록을 돌려준다."""
    slept: list[float] = []
    done = asyncio.Event()

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= rounds:
            done.set()
            await asyncio.Event().wait()

    refresher._sleep = sleep  # type: ignore[attr-defined]
    refresher.start()
    await asyncio.wait_for(done.wait(), 1.0)
    await refresher.aclose()
    return slept


async def test_universe_is_intersection_and_streams_get_their_own_krw_list() -> None:
    foreign = FakeForeign({"BTC", "XRP", "SOL"})
    refresher, up, bt, _, sink = build(
        [["KRW-BTC", "KRW-USDT", "KRW-ONLYKR"]],
        [["KRW-BTC", "KRW-XRP", "KRW-USDT"]],
        foreign,
    )
    outcome = await refresher.refresh()
    assert refresher.universe == {"BTC", "XRP"}  # USDT·국내 전용·해외 전용은 없다
    assert sink.universe == {"BTC", "XRP"}
    # 국내 거래소는 자기 KRW 전 마켓(KRW-USDT·국내 전용 포함)을 받는다
    assert up.markets[-1] == ["KRW-BTC", "KRW-USDT", "KRW-ONLYKR"]
    assert bt.markets[-1] == ["KRW-BTC", "KRW-XRP", "KRW-USDT"]
    assert outcome.calls == {"upbit": 1, "bithumb": 1, "binance": 1}
    assert outcome.failures == []
    # 바이낸스는 확정된 우주를 받아 그 심볼만 구독한다 (012 §3.3)
    assert foreign.universes[-1] == {"BTC", "XRP"}


async def test_three_lists_are_fetched_concurrently() -> None:
    refresher, up, bt, _, _ = build([["KRW-BTC"]], [["KRW-BTC"]], {"BTC"})
    up.gate = bt.gate = asyncio.Event()
    task = asyncio.create_task(refresher.refresh())
    await asyncio.sleep(0.01)
    assert up.active == 1 and bt.active == 1  # 둘 다 동시에 진행 중 (§3.2 병렬)
    up.gate.set()
    await task
    assert refresher.universe == {"BTC"}


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


async def test_loop_refreshes_every_second_and_retries_failed_exchange_next_second() -> (
    None
):
    err = ExchangeApiError("bithumb", "u", "down", kind="network")
    foreign = FakeForeign({"BTC"})
    refresher, up, bt, _, _ = build([["KRW-BTC"]], [err, err, ["KRW-BTC"]], foreign)
    slept = await run_rounds(refresher, 3)
    # 회차마다 세 거래소 각 1회, 회차 사이 1초 — 빗썸은 실패한 초에도 다음 초에 다시 불린다
    assert slept == [UNIVERSE_INTERVAL] * 3
    assert up.calls == 3 and bt.calls == 3 and foreign.refreshes == 3
    # 첫 회차부터 업비트 구독은 있고(다른 거래소는 그대로), 빗썸은 받는 순간 구독이 시작된다
    assert up.markets[0] == ["KRW-BTC"]
    assert bt.markets[:3] == [[], [], ["KRW-BTC"]]


async def test_foreign_failure_alone_keeps_universe_empty_until_it_arrives() -> None:
    foreign = FakeForeign({"BTC"}, failures=2)
    refresher, up, bt, _, _ = build([["KRW-BTC"]], [["KRW-BTC"]], foreign)
    first = await refresher.refresh()
    assert len(first.failures) == 1 and first.failures[0].exchange == "binance"
    assert refresher.universe == set()  # 심볼이 없으면 우주가 비어 행이 저장되지 않는다
    await run_rounds(refresher, 2)
    assert foreign.refreshes == 3 and up.calls == 3 and bt.calls == 3
    assert refresher.universe == {"BTC"}


async def test_same_cause_is_logged_once_per_minute_with_muted_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    err = ExchangeApiError("bithumb", "u", "down", kind="network")
    refresher, _, _, _, _ = build([["KRW-BTC"]], [err], {"BTC"}, clock)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(5):  # 5초 연속 실패 → 1줄
            await refresher.refresh()
            clock.now += 1
        assert len(caplog.records) == 1 and "bithumb" in caplog.records[0].getMessage()
        clock.now += LOG_SUPPRESS_SEC  # 60초가 지나면 다시 1줄, 억눌린 횟수(4)를 적는다
        await refresher.refresh()
        assert len(caplog.records) == 2 and "4회" in caplog.records[1].getMessage()
        await refresher.refresh()
        assert len(caplog.records) == 2


async def test_different_cause_or_exchange_is_logged_right_away(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    net = ExchangeApiError("bithumb", "u", "down", kind="network")
    limit = ExchangeApiError("bithumb", "u", "429", kind="rate_limit")
    up_err = ExchangeApiError("upbit", "u", "down", kind="network")
    refresher, _, _, _, _ = build([up_err], [net, limit, net], {"BTC"}, clock)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await refresher.refresh()  # upbit network + bithumb network → 2줄
        await refresher.refresh()  # bithumb rate_limit 은 새 원인 → 1줄, upbit 은 억제
        await refresher.refresh()  # bithumb network 는 60초 안 → 억제
        messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 3
    assert sum("upbit" in m for m in messages) == 1
    assert sum("bithumb" in m for m in messages) == 2


async def test_unexpected_error_is_that_exchanges_failure_and_loop_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 거래소 예외가 아닌 예외(버그·예상 밖 타입)도 그 거래소의 목록 실패 — 루프는 다음 회차를 돈다 (§3.2)
    refresher, up, bt, _, _ = build(
        [RuntimeError("bug"), ["KRW-BTC"]], [["KRW-BTC"]], {"BTC"}
    )
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        first = await refresher.refresh()
    assert [(f.exchange, f.code, f.kind) for f in first.failures] == [
        ("upbit", "exchange_api_error", "bad_response")
    ]
    assert len(caplog.records) == 1 and "upbit" in caplog.records[0].getMessage()
    assert bt.markets[-1] == ["KRW-BTC"] and refresher.universe == {"BTC"}
    slept = await run_rounds(refresher, 2)
    assert slept == [UNIVERSE_INTERVAL] * 2
    assert up.calls == 3 and up.markets[-1] == ["KRW-BTC"]


async def test_full_refresh_calls_every_exchange_including_foreign() -> None:
    foreign = FakeForeign({"BTC"})
    refresher, up, bt, _, _ = build([["KRW-BTC"]], [["KRW-BTC"]], foreign)
    await refresher.refresh()
    await refresher.refresh()  # 매초 회차·/refresh 트리거 — 셋을 전부 부른다
    assert (up.calls, bt.calls, foreign.refreshes) == (2, 2, 2)
