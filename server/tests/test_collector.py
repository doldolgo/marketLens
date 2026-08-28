"""수집 사이클 — 교집합·통째 교체·부분 실패·환율 (스펙 001 §3.2, §4). 거래소는 전부 fake."""

import asyncio

import httpx

from app.core.collector import Collector
from app.core.connectors.base import ExchangeConnector, FetchResult
from app.core.errors import ExchangeApiError
from app.core.live_store import LiveStore
from app.core.models import Row
from tests.conftest import FakeConnector, make_row


def usdt_row(exchange: str, ask: float = 1385.0, bid: float = 1384.0) -> Row:
    return make_row(exchange, "USDT", asks=[[ask, 1000.0]], bids=[[bid, 900.0]])


def build(
    store: LiveStore,
    upbit: FakeConnector,
    bithumb: FakeConnector,
    binance: FakeConnector,
    client: httpx.AsyncClient,
) -> Collector:
    return Collector(
        store=store, domestic=[upbit, bithumb], foreign=binance, client=client
    )


async def test_only_intersection_survives_and_usdt_excluded(
    unused_client: httpx.AsyncClient,
) -> None:
    store = LiveStore()
    upbit = FakeConnector(
        "upbit",
        [[make_row("upbit", "BTC"), make_row("upbit", "ONLYKR"), usdt_row("upbit")]],
    )
    bithumb = FakeConnector(
        "bithumb", [[make_row("bithumb", "BTC"), make_row("bithumb", "ETH")]]
    )
    binance = FakeConnector(
        "binance",
        [
            [
                make_row("binance", "BTC"),
                make_row("binance", "ETH"),
                make_row("binance", "ONLYBN"),
            ]
        ],
    )
    result = await build(store, upbit, bithumb, binance, unused_client).run_cycle()

    assert {r.base for r in store.get_all(exchange="upbit")} == {"BTC"}
    assert {r.base for r in store.get_all(exchange="bithumb")} == {"BTC", "ETH"}
    assert {r.base for r in store.get_all(exchange="binance")} == {"BTC", "ETH"}
    assert store.get("upbit", "USDT") is None  # 환율로만 쓰인다
    assert store.get("upbit", "ONLYKR") is None
    assert store.get("binance", "ONLYBN") is None
    assert result.saved == {"upbit": 1, "bithumb": 2, "binance": 2}
    assert result.failures == []


async def test_rate_extracted_per_domestic_exchange(
    unused_client: httpx.AsyncClient,
) -> None:
    store = LiveStore()
    upbit = FakeConnector(
        "upbit", [[make_row("upbit", "BTC"), usdt_row("upbit", 1385.0, 1384.0)]]
    )
    bithumb = FakeConnector(
        "bithumb", [[make_row("bithumb", "BTC"), usdt_row("bithumb", 1390.0, 1388.0)]]
    )
    binance = FakeConnector("binance", [[make_row("binance", "BTC")]])
    result = await build(store, upbit, bithumb, binance, unused_client).run_cycle()

    upbit_rate = store.get_rate("upbit")
    bithumb_rate = store.get_rate("bithumb")
    assert upbit_rate is not None and (upbit_rate.ask, upbit_rate.bid) == (
        1385.0,
        1384.0,
    )
    assert bithumb_rate is not None and (bithumb_rate.ask, bithumb_rate.bid) == (
        1390.0,
        1388.0,
    )
    assert store.get_rate("binance") is None  # 바이낸스 환율은 없다
    assert sorted(result.rates_observed) == ["bithumb", "upbit"]
    assert result.warnings == []


async def test_missing_usdt_keeps_previous_rate_and_warns(
    unused_client: httpx.AsyncClient,
) -> None:
    store = LiveStore()
    upbit = FakeConnector(
        "upbit",
        [
            [make_row("upbit", "BTC"), usdt_row("upbit")],
            [make_row("upbit", "BTC")],  # 2번째 사이클엔 USDT 호가 없음
        ],
    )
    bithumb = FakeConnector(
        "bithumb", [[make_row("bithumb", "BTC"), usdt_row("bithumb", 1390.0, 1388.0)]]
    )
    binance = FakeConnector("binance", [[make_row("binance", "BTC")]])
    collector = build(store, upbit, bithumb, binance, unused_client)

    await collector.run_cycle()
    result = await collector.run_cycle()

    rate = store.get_rate("upbit")
    assert rate is not None and rate.ask == 1385.0  # 직전 값 유지
    assert result.rates_observed == ["bithumb"]
    assert len(result.warnings) == 1
    assert "KRW-USDT 호가가 없어 환율을 못 구한 거래소: upbit" in result.warnings[0]


async def test_wholesale_replace_drops_delisted_coin(
    unused_client: httpx.AsyncClient,
) -> None:
    store = LiveStore()
    upbit = FakeConnector(
        "upbit",
        [
            [make_row("upbit", "BTC"), make_row("upbit", "ETH")],
            [make_row("upbit", "BTC")],  # ETH 상폐
        ],
    )
    bithumb = FakeConnector("bithumb", [[]])
    binance = FakeConnector(
        "binance", [[make_row("binance", "BTC"), make_row("binance", "ETH")]]
    )
    collector = build(store, upbit, bithumb, binance, unused_client)

    await collector.run_cycle()
    assert store.get("upbit", "ETH") is not None
    await collector.run_cycle()
    assert store.get("upbit", "ETH") is None  # 자동 소멸
    assert store.get("upbit", "BTC") is not None


async def test_failed_exchange_keeps_previous_snapshot(
    unused_client: httpx.AsyncClient,
) -> None:
    store = LiveStore()
    upbit = FakeConnector(
        "upbit",
        [
            [make_row("upbit", "BTC", price=100.0), make_row("upbit", "ETH")],
            [make_row("upbit", "BTC", price=200.0), make_row("upbit", "ETH")],
        ],
    )
    bithumb = FakeConnector("bithumb", [[make_row("bithumb", "BTC")]])
    binance = FakeConnector(
        "binance",
        [
            [make_row("binance", "BTC"), make_row("binance", "ETH")],
            ExchangeApiError(
                "binance",
                "https://api.binance.com/api/v3/ticker/price",
                "비-200 응답: 500",
                status_code=500,
                body="oops",
            ),
        ],
    )
    collector = build(store, upbit, bithumb, binance, unused_client)

    await collector.run_cycle()
    first_binance_updated = store.get("binance", "BTC").updated_at

    result = await collector.run_cycle()

    # 실패한 바이낸스는 직전 스냅샷이 updated_at 그대로 남는다
    assert store.get("binance", "BTC").updated_at == first_binance_updated
    assert store.get("binance", "ETH") is not None
    # 성공한 국내는 갱신된다 (교집합은 유지된 바이낸스 세트로 계산)
    assert store.get("upbit", "BTC").price == 200.0
    assert store.get("upbit", "ETH") is not None
    assert result.failures == [
        {
            "exchange": "binance",
            "error_code": "exchange_api_error",
            "message": "비-200 응답: 500",
        }
    ]
    assert result.saved["binance"] == 0
    assert result.saved["upbit"] == 2


async def test_never_succeeded_exchange_means_empty_intersection(
    unused_client: httpx.AsyncClient,
) -> None:
    store = LiveStore()
    upbit = FakeConnector("upbit", [[make_row("upbit", "BTC"), usdt_row("upbit")]])
    bithumb = FakeConnector("bithumb", [[make_row("bithumb", "BTC")]])
    binance = FakeConnector(
        "binance",
        [ExchangeApiError("binance", "https://api.binance.com/x", "연결 실패")],
    )
    result = await build(store, upbit, bithumb, binance, unused_client).run_cycle()

    assert (
        store.get_all() == []
    )  # 바이낸스가 한 번도 성공한 적 없으면 교집합은 비어 있다
    assert result.saved == {"upbit": 0, "bithumb": 0, "binance": 0}
    assert store.get_rate("upbit") is not None  # 환율은 교집합과 무관하게 관측된다
    assert store.received_at is not None


async def test_unexpected_exception_is_recorded_as_internal_error(
    unused_client: httpx.AsyncClient,
) -> None:
    store = LiveStore()
    upbit = FakeConnector("upbit", [KeyError("orderbook_units")])
    bithumb = FakeConnector("bithumb", [[make_row("bithumb", "BTC")]])
    binance = FakeConnector("binance", [[make_row("binance", "BTC")]])
    result = await build(store, upbit, bithumb, binance, unused_client).run_cycle()

    assert [f["exchange"] for f in result.failures] == ["upbit"]
    assert result.failures[0]["error_code"] == "internal_error"
    assert store.get("bithumb", "BTC") is not None  # 나머지 거래소는 저장된다


async def test_cycle_result_summary_fields(unused_client: httpx.AsyncClient) -> None:
    store = LiveStore()
    upbit = FakeConnector(
        "upbit", [[make_row("upbit", "BTC"), usdt_row("upbit")]], calls=7
    )
    bithumb = FakeConnector("bithumb", [[make_row("bithumb", "BTC")]], calls=11)
    binance = FakeConnector("binance", [[make_row("binance", "BTC")]], calls=2)
    result = await build(store, upbit, bithumb, binance, unused_client).run_cycle()

    assert result.calls == {"upbit": 7, "bithumb": 11, "binance": 2}
    assert result.duration_ms >= 0
    assert result.fetched_at > 1_000_000_000_000  # epoch ms 자릿수


async def test_cycles_never_overlap(unused_client: httpx.AsyncClient) -> None:
    store = LiveStore()
    upbit = FakeConnector("upbit", [[make_row("upbit", "BTC")]], delay=0.02)
    bithumb = FakeConnector("bithumb", [[]])
    binance = FakeConnector("binance", [[make_row("binance", "BTC")]])
    collector = build(store, upbit, bithumb, binance, unused_client)

    await asyncio.gather(
        collector.run_cycle(), collector.run_cycle(), collector.run_cycle()
    )
    assert upbit.max_active == 1  # 루프와 수동 트리거가 겹치면 뒤의 것이 기다린다


async def test_connector_interface_is_satisfied_by_fakes() -> None:
    # 공통 인터페이스 하나로 collector 가 동작한다 — 새 거래소 추가 = 구현체 추가
    assert issubclass(FakeConnector, ExchangeConnector)
    assert isinstance(FetchResult(rows=[], calls=0), FetchResult)


# ---- 리뷰 확정 결함 회귀: 지갑 조회가 시세 교체를 막지 않는다 (006 §3.5) ----


class GatedWallet:
    """gate 를 열어줄 때까지 refresh_if_due 가 매달리는 fake — 지연·취소 관찰용."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.cancelled = False
        self.applied = 0

    async def refresh_if_due(self, client: httpx.AsyncClient) -> dict[str, int] | None:
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return {}

    def apply(self, rows, exchange: str) -> None:
        self.applied += 1

    def warnings(self) -> list[str]:
        return []

    def failed(self) -> list[str]:
        return []

    def availability(self) -> dict[str, bool]:
        return {}


def build_with_wallet(
    store: LiveStore, wallet: GatedWallet, client: httpx.AsyncClient
) -> Collector:
    upbit = FakeConnector("upbit", [[usdt_row("upbit"), make_row("upbit", "BTC")]])
    bithumb = FakeConnector("bithumb", [[usdt_row("bithumb")]])
    binance = FakeConnector("binance", [[make_row("binance", "BTC", quote="USDT")]])
    return Collector(
        store=store,
        domestic=[upbit, bithumb],
        foreign=binance,
        client=client,
        wallet=wallet,
    )


async def test_hanging_wallet_does_not_delay_snapshot_replacement(unused_client):
    store = LiveStore()
    wallet = GatedWallet()
    collector = build_with_wallet(store, wallet, unused_client)
    cycle = asyncio.create_task(collector.run_cycle())
    # 지갑이 매달려 있어도(gate 닫힘) 시세 교체·mark_received 는 끝나 있어야 한다
    for _ in range(200):
        if store.get_all(exchange="upbit"):
            break
        await asyncio.sleep(0.005)
    assert store.get_all(exchange="upbit"), "지갑 대기 중에 시세 교체가 일어나지 않았다"
    assert not cycle.done()
    wallet.gate.set()
    result = await cycle
    assert result.saved["upbit"] >= 1
    assert wallet.applied > 0  # 합류 뒤 캐시 반영은 여전히 수행된다


async def test_cancelled_cycle_cancels_wallet_task(unused_client):
    store = LiveStore()
    wallet = GatedWallet()
    collector = build_with_wallet(store, wallet, unused_client)
    cycle = asyncio.create_task(collector.run_cycle())
    for _ in range(200):
        if store.get_all(exchange="upbit"):
            break
        await asyncio.sleep(0.005)
    cycle.cancel()
    try:
        await cycle
    except asyncio.CancelledError:
        pass
    assert wallet.cancelled, "사이클 취소 시 지갑 태스크가 고아로 남았다"
