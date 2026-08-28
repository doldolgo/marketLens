"""바이낸스 입출금 조회 — HMAC 서명·networkList 해석 (스펙 006 §3.3·§4)."""

import hashlib
import hmac
import re

import httpx
import pytest

from app.features.wallet_status.binance import fetch_binance
from app.features.wallet_status.models import WalletStatusError
from app.features.wallet_status.tests.helpers import Capture, json_client


async def test_hmac_signature_matches_directly_computed_value() -> None:
    # 가짜 secret 으로 기대값을 직접 계산한다 — 서명은 결정적 (§4 서명 테스트)
    cap, client = json_client([])
    await fetch_binance(client, api_key="fake-ak", secret_key="fake-sk")

    request = cap.requests[0]
    assert request.url.path == "/sapi/v1/capital/config/getall"
    assert request.headers["X-MBX-APIKEY"] == "fake-ak"
    query = request.url.query.decode()
    # 쿼리는 정확히 이 두 키·이 순서 + 뒤에 signature (§3.3)
    m = re.fullmatch(
        r"(timestamp=(\d+)&recvWindow=10000)&signature=([0-9a-f]{64})", query
    )
    assert m is not None
    signed_part, _, signature = m.group(1), m.group(2), m.group(3)
    expected = hmac.new(b"fake-sk", signed_part.encode(), hashlib.sha256).hexdigest()
    assert signature == expected


async def test_empty_network_list_coin_absent_and_or_beats_all_enable() -> None:
    # networkList 빈 코인은 결과에 없다. depositAllEnable=false 여도 한 망이 열려 있으면 dep ok (§4)
    _, client = json_client(
        [
            {"coin": "AAA", "networkList": []},
            {
                "coin": "GRT",
                "depositAllEnable": False,
                "withdrawAllEnable": False,
                "networkList": [
                    {
                        "network": "ARBITRUM",
                        "name": "Arbitrum One",
                        "depositEnable": True,
                        "withdrawEnable": True,
                    },
                    {
                        "network": "ETH",
                        "name": "Ethereum (ERC20)",
                        "depositEnable": True,
                        "withdrawEnable": False,
                    },
                ],
            },
        ]
    )
    out = await fetch_binance(client, api_key="ak", secret_key="sk")
    assert "AAA" not in out
    grt = out["GRT"]
    assert grt.deposit_enabled is True  # 코인 레벨 depositAllEnable 은 쓰지 않는다
    assert grt.withdrawal_enabled is True
    assert [n.code for n in grt.networks] == ["ARBITRUM", "ETH"]
    assert grt.networks[1].wd is False


async def test_empty_network_code_counts_only_toward_coin_value() -> None:
    _, client = json_client(
        [
            {
                "coin": "BBB",
                "networkList": [
                    {
                        "network": "",
                        "name": "??",
                        "depositEnable": True,
                        "withdrawEnable": True,
                    },
                    {"network": "BBB", "depositEnable": False, "withdrawEnable": False},
                ],
            }
        ]
    )
    out = await fetch_binance(client, api_key="ak", secret_key="sk")
    assert out["BBB"].deposit_enabled is True  # 코인 값에는 반영
    assert [n.code for n in out["BBB"].networks] == ["BBB"]  # 망 목록에선 제외
    assert out["BBB"].networks[0].name == "BBB"  # name 없으면 code


async def test_missing_keys_fail_without_any_call() -> None:
    cap, client = json_client([])
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_binance(client, api_key="ak", secret_key=None)
    assert (
        exc_info.value.message
        == "BINANCE_API_KEY / BINANCE_SECRET_KEY 가 비어 있습니다."
    )
    assert exc_info.value.calls == 0
    assert cap.requests == []


async def test_http_500_message_has_status_and_no_secret() -> None:
    cap = Capture([httpx.Response(500, text="y" * 600)])
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_binance(cap.client(), api_key="ak", secret_key="top-secret-value")
    err = exc_info.value
    assert "500" in err.message
    assert len(err.detail["body"]) <= 500
    assert "top-secret-value" not in err.message
    assert "top-secret-value" not in str(err.detail)
