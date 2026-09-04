"""60초 캐시·실패 시 unknown 덮기·경고 조립 — 스펙 006 §3.5·§4. 네트워크 없음."""

from collections.abc import Callable

import httpx

from app.core.models import Row
from app.features.wallet_status.service import WalletStatusService


def make_row(
    exchange: str, base: str, *, dep: bool | None = None, wd: bool | None = None
) -> Row:
    return Row(
        exchange=exchange,
        base=base,
        quote="USDT" if exchange == "binance" else "KRW",
        native_symbol=f"{base}USDT" if exchange == "binance" else f"KRW-{base}",
        price=100.0,
        asks=[[101.0, 1.0]],
        bids=[[99.0, 2.0]],
        price_timestamp=1_700_000_000_000,
        deposit_enabled=dep,
        withdrawal_enabled=wd,
    )


_GOOD_BITHUMB = {
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


def routing_client(
    bithumb: Callable[[], httpx.Response],
) -> tuple[list[httpx.Request], httpx.AsyncClient]:
    """호스트별 라우팅 — 빗썸만 응답을 정하고 나머지는 도달하면 실패해야 한다."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.bithumb.com":
            return bithumb()
        raise AssertionError(f"예상 밖 네트워크 호출: {request.url}")

    return requests, httpx.AsyncClient(transport=httpx.MockTransport(handler))


def keyless_service(interval: float = 60.0) -> WalletStatusService:
    return WalletStatusService(
        upbit_api_key=None,
        upbit_secret_key=None,
        binance_api_key=None,
        binance_secret_key=None,
        interval=interval,
    )


async def test_first_cycle_fetches_then_cache_until_interval() -> None:
    requests, client = routing_client(lambda: httpx.Response(200, json=_GOOD_BITHUMB))
    service = keyless_service(interval=60.0)
    # 기동 첫 사이클은 캐시가 비어 즉시 호출 — 키 있는 빗썸만 1회 (§3.5)
    calls = await service.refresh_if_due(client)
    assert calls == {"bithumb": 1}
    assert len(requests) == 1
    # 사이 사이클은 캐시 — 호출 없음
    assert await service.refresh_if_due(client) is None
    assert len(requests) == 1


async def test_keyless_upbit_binance_fail_with_zero_calls_and_warnings() -> None:
    _, client = routing_client(lambda: httpx.Response(200, json=_GOOD_BITHUMB))
    service = keyless_service()
    await service.refresh_if_due(client)
    assert service.availability() == {"upbit": False, "bithumb": True, "binance": False}
    assert service.failed() == ["upbit", "binance"]
    warnings = service.warnings()
    assert warnings == [
        "upbit 입출금 상태 조회 실패 — UPBIT_API_KEY / UPBIT_SECRET_KEY 가 비어 있습니다. (해당 거래소의 deposit_enabled / withdrawal_enabled 는 null)",
        "binance 입출금 상태 조회 실패 — BINANCE_API_KEY / BINANCE_SECRET_KEY 가 비어 있습니다. (해당 거래소의 deposit_enabled / withdrawal_enabled 는 null)",
    ]


async def test_apply_sets_values_and_unknown_for_missing_coin() -> None:
    _, client = routing_client(lambda: httpx.Response(200, json=_GOOD_BITHUMB))
    service = keyless_service()
    await service.refresh_if_due(client)

    btc = make_row("bithumb", "BTC")
    etc = make_row("bithumb", "ETC")  # 응답에 없는 코인 → unknown (§3.1)
    service.apply([btc, etc], "bithumb")
    assert btc.deposit_enabled is True
    assert [n.code for n in btc.networks] == ["BTC"]
    assert etc.deposit_enabled is None
    assert etc.networks == []

    # 키 없는 거래소는 전 코인 unknown·빈 망 목록
    up = make_row("upbit", "BTC", dep=True, wd=True)
    service.apply([up], "upbit")
    assert up.deposit_enabled is None
    assert up.withdrawal_enabled is None
    assert up.networks == []


async def test_failure_cycle_overwrites_previous_success_with_unknown() -> None:
    # 실패 사이클은 직전 성공값을 유지하지 않고 unknown 으로 덮는다 (§3.5)
    responses = [
        httpx.Response(200, json=_GOOD_BITHUMB),
        httpx.Response(500, text="oops"),
    ]
    _, client = routing_client(lambda: responses.pop(0))
    service = keyless_service(interval=0.0)  # 매 호출 조회 — 캐시 만료를 흉내낸다

    await service.refresh_if_due(client)
    row = make_row("bithumb", "BTC")
    service.apply([row], "bithumb")
    assert row.deposit_enabled is True

    await service.refresh_if_due(client)  # 이번엔 500
    service.apply([row], "bithumb")
    assert row.deposit_enabled is None
    assert row.withdrawal_enabled is None
    assert row.networks == []
    assert "bithumb" in service.failed()
    assert any("빗썸 지갑 상태 API 가 500" in w for w in service.warnings())
