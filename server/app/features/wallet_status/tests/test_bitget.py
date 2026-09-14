"""비트겟 입출금 조회 — 인증 없음·chains 해석·code 실패 (스펙 020 §3.6·§4)."""

import time

import httpx
import pytest

from app.features.wallet_status.bitget import fetch_bitget
from app.features.wallet_status.models import WalletStatusError
from app.features.wallet_status.tests.helpers import (
    Capture,
    FakeRecorder,
    assert_recorded_only,
    json_client,
)


def ok(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"code": "00000", "msg": "success", "requestTime": 1, "data": rows}


async def test_request_has_no_auth_headers_or_query() -> None:
    cap, client = json_client(ok([]))
    await fetch_bitget(client)

    request = cap.requests[0]
    assert request.url.host == "api.bitget.com"
    assert request.url.path == "/api/v2/spot/public/coins"
    assert request.url.query == b""
    assert not any(
        h.lower().startswith("access-") for h in request.headers
    )  # 서명 없음


async def test_chains_map_to_networks_and_empty_chains_coin_is_absent() -> None:
    _, client = json_client(
        ok(
            [
                {"coinId": "1", "coin": "AAA", "transfer": "true", "chains": []},
                {
                    "coinId": "2",
                    "coin": "GRT",
                    "transfer": "true",
                    "chains": [
                        {
                            "chain": "ARBITRUM",
                            "needTag": "false",
                            "withdrawable": "true",
                            "rechargeable": "true",
                            "withdrawFee": "0.5",
                            "congestion": "congested",
                        },
                        {
                            "chain": "erc20",
                            "needTag": "false",
                            "withdrawable": "false",
                            "rechargeable": "true",
                            "withdrawFee": "1",
                            "congestion": "normal",
                        },
                    ],
                },
                {
                    "coinId": "3",
                    "coin": "zzz",
                    "chains": [
                        {
                            "chain": "ZZZ",
                            "withdrawable": "false",
                            "rechargeable": "false",
                        }
                    ],
                },
            ]
        )
    )
    out = await fetch_bitget(client)
    assert "AAA" not in out
    grt = out["GRT"]
    assert grt.deposit_enabled is True and grt.withdrawal_enabled is True  # 망별 OR
    assert [(n.code, n.name, n.dep, n.wd) for n in grt.networks] == [
        ("ARBITRUM", "ARBITRUM", True, True),  # congestion 은 판정에 안 쓴다
        ("ERC20", "erc20", True, False),  # code 는 대문자, name 은 원문
    ]
    zzz = out["ZZZ"]  # 코인 심볼도 대문자로
    assert zzz.deposit_enabled is False and zzz.withdrawal_enabled is False


async def test_only_the_exact_string_true_opens_a_network() -> None:
    _, client = json_client(
        ok(
            [
                {
                    "coin": "BBB",
                    "chains": [
                        {"chain": "BBB", "withdrawable": True, "rechargeable": "TRUE"},
                        {"chain": "", "withdrawable": "true", "rechargeable": "true"},
                    ],
                }
            ]
        )
    )
    out = await fetch_bitget(client)
    # 불리언 True·대문자 "TRUE" 는 "true" 가 아니다 — 문자열 비교 (§3.6)
    assert [(n.code, n.dep, n.wd) for n in out["BBB"].networks] == [
        ("BBB", False, False)
    ]
    # 망 코드가 빈 항목은 망 목록엔 없지만 코인 값에는 반영된다
    assert out["BBB"].deposit_enabled is True and out["BBB"].withdrawal_enabled is True


async def test_code_failure_http_500_and_bad_body_raise() -> None:
    _, client = json_client({"code": "40001", "msg": "params error", "data": None})
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bitget(client)
    err = exc_info.value
    assert "code 40001" in err.message and "params error" in err.message
    assert err.detail["exchange"] == "bitget"
    cap = Capture([httpx.Response(500, text="y" * 600)])
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bitget(cap.client())
    err = exc_info.value
    assert "500" in err.message and len(err.detail["body"]) <= 500  # type: ignore[arg-type]
    _, client = json_client({"code": "00000", "msg": "success"})
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bitget(client)
    assert "data" in exc_info.value.message
    cap = Capture([httpx.Response(200, text="<html>")])
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bitget(cap.client())
    assert "JSON" in exc_info.value.message


async def test_network_failure_raises_without_recording() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    recorder = FakeRecorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bitget(client, record=recorder)
    assert "ConnectError" in exc_info.value.message
    assert recorder.lines == []  # 응답이 없으니 원문도 없다


async def test_response_body_recorded_verbatim() -> None:
    body = '{"code":"00000","msg":"success","requestTime":1,"data":[{"coin":"BTC","chains":[{"chain":"BTC","rechargeable":"true","withdrawable":"true"}]}]}'
    cap = Capture([httpx.Response(200, content=body.encode())])
    recorder = FakeRecorder()
    before = int(time.time() * 1000)
    out = await fetch_bitget(cap.client(), record=recorder)
    assert out["BTC"].networks[0].name == "BTC"
    assert_recorded_only(
        recorder,
        exchange="bitget",
        source="rest:/api/v2/spot/public/coins",
        body=body,
        before_ms=before,
    )


async def test_http_500_body_is_recorded_too() -> None:
    cap = Capture([httpx.Response(500, text="bitget-down")])
    recorder = FakeRecorder()
    before = int(time.time() * 1000)
    with pytest.raises(WalletStatusError):
        await fetch_bitget(cap.client(), record=recorder)
    assert_recorded_only(
        recorder,
        exchange="bitget",
        source="rest:/api/v2/spot/public/coins",
        body="bitget-down",
        before_ms=before,
    )
