"""김프 점 규칙과 저장소 장애 — 틱이 만들고 flusher 가 쓰는 점, 그것을 읽는 /history/* (스펙 005 §3.1·§3.3, §4).

네트워크 없음. 시세는 core API 로 직접 시드하고, Redis 는 fakeredis, Influx 는 tests/conftest 의 fake 다.
점의 수·시각은 009 가 검증하므로 여기서는 점의 **규칙**(자격·원값·dw_fail)과 장애 격리만 본다.
"""

from datetime import UTC, datetime

import fakeredis
import httpx
import pytest

from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.redis_stream import RedisTickStream
from app.core.tick_store import Flusher, TickRelay
from app.core.ticks import TickLoop, build_tick
from app.features.history.tests.helpers import FakeInfluxReader, make_client
from tests.conftest import FakeInflux, make_row

NOW = datetime.now(UTC)
T0 = 1_787_000_000


def _client() -> httpx.AsyncClient:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"테스트에서 네트워크 호출 발생: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(fail))


def seed(rows: list[Row], *, rates: dict[str, tuple[float, float]]) -> LiveStore:
    """행과 USDT 시세를 시드한다 — 행이 있는 거래소는 스트림 수신 시각도 둔다(`/spreads` 의 age 기준)."""
    store = LiveStore()
    store.put_rows(rows, NOW)
    for exchange in {row.exchange for row in rows}:
        store.stream(exchange).last_message_at = int(NOW.timestamp() * 1000)
    for exchange, (ask, bid) in rates.items():
        store.set_rate(exchange, ask, bid, NOW)
    store.mark_received(T0)
    return store


def two_level_books() -> list[Row]:
    """호가를 두 단계로 시드해 $10,000 이 2단계째까지 먹게 — 슬리피지가 0 이 아니다."""
    return [
        make_row(
            "upbit",
            "BTC",
            bids=[[100_000.0, 0.02], [99_000.0, 5.0]],
            asks=[[100_100.0, 0.02], [101_000.0, 5.0]],
        ),
        make_row(
            "binance",
            "BTC",
            bids=[[70.0, 0.02], [69.0, 500.0]],
            asks=[[71.0, 0.02], [72.0, 500.0]],
        ),
    ]


# ---- 점 규칙 (§3.3) ----


def test_domestic_exchange_without_its_own_usdt_rate_is_not_a_dom_in_the_tick() -> None:
    # 국내 행은 호가 + 그 거래소의 USDT 시세가 있어야 한다 — 남의 시세를 빌리지 않는다 (§4)
    store = seed(
        [
            make_row("upbit", "BTC"),
            make_row("bithumb", "BTC"),
            make_row("binance", "BTC"),
        ],
        rates={"upbit": (1400.0, 1390.0)},
    )
    rows = build_tick(store, T0, ()).rows
    assert [(r.dom, r.fx, r.base) for r in rows] == [("upbit", "binance", "BTC")]
    # 빗썸 시세가 생기면 그때부터 빗썸 조합이 등장한다
    store.set_rate("bithumb", 1410.0, 1405.0, NOW)
    assert {r.dom for r in build_tick(store, T0, ()).rows} == {"upbit", "bithumb"}


def test_stored_point_is_the_raw_value_before_slippage() -> None:
    # premium 의 fwd/rev 는 차감 전 원값 — 같은 호가의 /spreads 행은 fwd + slipFwd·rev + slipRev (§4)
    store = seed(two_level_books(), rates={"upbit": (1400.0, 1390.0)})
    [point] = build_tick(store, T0, ()).rows
    resp = make_client(FakeInfluxReader(), store).get("/spreads")
    assert resp.status_code == 200, resp.text
    [row] = resp.json()["rows"]
    assert row["slipFwd"] > 0 and row["slipRev"] > 0  # 차감이 0 이 아닌 상태에서 고정
    assert point.fwd == pytest.approx(row["fwd"] + row["slipFwd"])
    assert point.rev == pytest.approx(row["rev"] + row["slipRev"])
    assert point.fwd != pytest.approx(row["fwd"])


class Wallet:
    """입출금 조회기 fake — `failed()` 가 돌려주는 목록만 바뀐다."""

    def __init__(self) -> None:
        self.failing: list[str] = []

    async def refresh_if_due(
        self, client: httpx.AsyncClient, *, force: bool = False
    ) -> dict[str, int] | None:
        return None

    def apply(self, rows: list[Row], exchange: str) -> None:
        pass

    def availability(self) -> dict[str, bool]:
        return {}

    def warnings(self) -> list[str]:
        return []

    def failed(self) -> list[str]:
        return list(self.failing)


async def test_wallet_failures_ride_the_tick_and_become_dw_fail_points() -> None:
    # 실패 상태인 거래소는 틱의 dwFailed 에 담기고 flusher 가 그 ts 로 dw_fail 1점을 쓴다; 없으면 0점 (§4)
    stream = RedisTickStream(fakeredis.aioredis.FakeRedis())
    influx = FakeInflux()
    store = seed(two_level_books(), rates={"upbit": (1400.0, 1390.0)})
    relay = TickRelay(stream=stream, store=store)
    wallet = Wallet()
    loop = TickLoop(
        store=store,
        streams=[],
        client=_client(),
        handoff=relay,
        wallet=wallet,  # type: ignore[arg-type]
    )
    wallet.failing = ["binance", "upbit"]
    first = loop.tick(T0)
    assert first.dw_failed == ("binance", "upbit")
    wallet.failing = []
    second = loop.tick(T0 + 1)  # T0 인계
    assert second.dw_failed == ()
    loop.tick(T0 + 2)  # T0+1 인계
    await relay.drain()
    assert await Flusher(stream=stream, writer=influx).flush_once() is True

    dw = sorted((p.tags["exchange"], p.ts) for p in influx.stored("dw_fail"))
    assert dw == [("binance", T0), ("upbit", T0)]
    # 실패 없는 틱(T0+1)은 premium 점만 남긴다
    assert sorted(p.ts for p in influx.stored("premium")) == [T0, T0 + 1]


# ---- 저장소 장애 격리 (§3.1) ----

HISTORY_ROUTES = [
    ("/history/premium", {"base": "BTC", "unit": "week"}),
    ("/history/streaks", {"base": "BTC"}),
    ("/history/streaks/bulk", {}),
]


def test_influx_outage_keeps_memory_routes_alive_and_history_503() -> None:
    # Influx 가 닿지 않아도 /health 200·/spreads 는 메모리로 동작, /history/* 만 503 (§4)
    reader = FakeInfluxReader()
    reader.fail = True
    store = seed(two_level_books(), rates={"upbit": (1400.0, 1390.0)})
    client = make_client(reader, store)
    assert client.get("/health").json()["status"] == "ok"
    spreads = client.get("/spreads")
    assert spreads.status_code == 200 and len(spreads.json()["rows"]) == 1
    for path, params in HISTORY_ROUTES:
        res = client.get(path, params=params)
        assert res.status_code == 503, path
        assert res.json()["error"]["code"] == "storage_unavailable"


def test_missing_token_makes_every_history_route_503() -> None:
    # INFLUX_TOKEN 없이 기동 → /history/* 503, 메모리 조회는 그대로 (§4)
    store = seed(two_level_books(), rates={"upbit": (1400.0, 1390.0)})
    client = make_client(None, store)
    assert client.get("/spreads").status_code == 200
    for path, params in HISTORY_ROUTES:
        res = client.get(path, params=params)
        assert res.status_code == 503, path
        assert res.json()["error"]["code"] == "storage_unavailable"


# ---- bulk 전 코인 (§4) ----


def test_bulk_threshold_zero_counts_every_coin_over_a_hundred() -> None:
    # coinCount == len(coins) 이고 100 을 넘는다 — 전 코인이 한 응답에 담긴다
    reader = FakeInfluxReader()
    for i in range(120):
        reader.seed("upbit", "binance", f"C{i:03d}", [(T0 + i, 1.0, -1.0)])
    res = make_client(reader).get("/history/streaks/bulk", params={"threshold": 0})
    assert res.status_code == 200
    body = res.json()
    assert body["coinCount"] == len(body["coins"]) == 120 > 100
    assert all(c["kimp"]["count"] == 1 for c in body["coins"])
