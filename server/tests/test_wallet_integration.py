"""키 없이 기동한 수집 사이클 + /spreads·/refresh 통합 — 스펙 006 §4 (네트워크 없음).

시세는 fake 커넥터, 입출금은 실제 WalletStatusService + MockTransport(빗썸만 응답).
"""

import httpx

from app.core.collector import Collector
from app.core.live_store import LiveStore
from app.features.spreads.tests.helpers import FakeCollector, make_client
from app.features.wallet_status.service import WalletStatusService
from tests.conftest import FakeConnector, make_row

_BITHUMB_WALLET = {
    "status": "0000",
    "data": [
        {
            "currency": "BTC",
            "net_type": "BTC",
            "deposit_status": 1,
            "withdrawal_status": 1,
        }
    ],
}


def wallet_client(responses: list[httpx.Response]) -> httpx.AsyncClient:
    """빗썸 입출금 URL 만 응답한다 — 키 없는 업비트·바이낸스는 호출 자체가 없어야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.bithumb.com" and "assetsstatus" in request.url.path:
            return responses.pop(0) if len(responses) > 1 else responses[0]
        raise AssertionError(f"예상 밖 네트워크 호출: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def usdt_row(exchange: str):
    return make_row(exchange, "USDT", asks=[[1400.0, 1000.0]], bids=[[1390.0, 900.0]])


def build_collector(
    store: LiveStore, client: httpx.AsyncClient, *, interval: float = 60.0
) -> Collector:
    wallet = WalletStatusService(
        upbit_api_key=None,
        upbit_secret_key=None,
        binance_api_key=None,
        binance_secret_key=None,
        interval=interval,
    )
    return Collector(
        store=store,
        domestic=[
            FakeConnector("upbit", [[make_row("upbit", "BTC"), usdt_row("upbit")]]),
            FakeConnector(
                "bithumb", [[make_row("bithumb", "BTC"), usdt_row("bithumb")]]
            ),
        ],
        foreign=FakeConnector("binance", [[make_row("binance", "BTC")]]),
        client=client,
        wallet=wallet,
    )


async def test_keyless_startup_spreads_and_refresh_contract() -> None:
    store = LiveStore()
    client = wallet_client([httpx.Response(200, json=_BITHUMB_WALLET)])
    collector = build_collector(store, client)
    result = await collector.run_cycle()

    # 실패 상태 거래소는 dw_failed 로 — persist 가 dw_fail 점을 쓴다 (§3.5)
    assert collector.dw_failed == ["upbit", "binance"]

    # /spreads — 모든 행에 5키, 값은 true/false/null 뿐, netDom 은 문자열 또는 null (§4)
    rows = make_client(store).get("/spreads").json()["rows"]
    assert rows
    for row in rows:
        assert {"netDom", "depDom", "wdDom", "depFx", "wdFx"} <= set(row)
        for key in ("depDom", "wdDom", "depFx", "wdFx"):
            assert row[key] in (True, False, None)
        assert row["netDom"] is None or isinstance(row["netDom"], str)
    # 빗썸은 키 불필요 — depDom 이 null 이 아닌 빗썸 행이 있다
    bithumb_rows = [r for r in rows if r["dom"] == "bithumb"]
    assert any(r["depDom"] is not None for r in bithumb_rows)
    assert all(r["netDom"] == "BTC" for r in bithumb_rows)
    # 키 없는 업비트 행은 전부 unknown
    assert all(r["depDom"] is None for r in rows if r["dom"] == "upbit")

    # /refresh — 빗썸 true, 업비트·바이낸스 false + 입출금 경고 2줄 (§4)
    body = make_client(store, collector=FakeCollector(result)).post("/refresh").json()
    available = {s["exchange"]: s["wallet_status_available"] for s in body["snapshots"]}
    assert available == {"upbit": False, "bithumb": True, "binance": False}
    dw_warnings = [w for w in body["warnings"] if "입출금 상태 조회 실패" in w]
    assert len(dw_warnings) == 2
    assert dw_warnings[0].startswith("upbit ")
    assert dw_warnings[1].startswith("binance ")
    # 빗썸 항목의 calls 에 입출금 호출 1회가 더해진다 (§3.5)
    calls = {s["exchange"]: s["calls"] for s in body["snapshots"]}
    assert calls["bithumb"] == 2  # 시세 1 + 입출금 1
    assert calls["upbit"] == 1  # 키 없음 → 호출 0회로 실패


async def test_failure_cycle_overwrites_rows_to_unknown_in_spreads() -> None:
    # 실패 사이클 후 /spreads 의 해당 거래소 행은 전부 null — 직전 성공값 미유지 (§4)
    store = LiveStore()
    client = wallet_client(
        [httpx.Response(200, json=_BITHUMB_WALLET), httpx.Response(500, text="oops")]
    )
    collector = build_collector(store, client, interval=0.0)

    await collector.run_cycle()
    rows = make_client(store).get("/spreads").json()["rows"]
    assert any(r["depDom"] is True for r in rows if r["dom"] == "bithumb")

    await collector.run_cycle()  # 이번엔 빗썸 500
    assert "bithumb" in collector.dw_failed
    rows = make_client(store).get("/spreads").json()["rows"]
    for row in (r for r in rows if r["dom"] == "bithumb"):
        assert row["depDom"] is None
        assert row["wdDom"] is None
        assert row["netDom"] is None
