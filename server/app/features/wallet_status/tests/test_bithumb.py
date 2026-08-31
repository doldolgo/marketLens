"""빗썸 입출금 조회 — public·정수 비교·형식 검사 (스펙 006 §3.4·§4)."""

import pytest

from app.features.wallet_status.bithumb import fetch_bithumb
from app.features.wallet_status.models import WalletStatusError
from app.features.wallet_status.tests.helpers import json_client


async def test_no_auth_needed_and_integer_one_means_ok() -> None:
    cap, client = json_client(
        {
            "status": "0000",
            "data": [
                {
                    "currency": "ETH",
                    "net_type": "ETH",
                    "deposit_status": 1,
                    "withdrawal_status": 1,
                },
                {
                    "currency": "ETH",
                    "net_type": "ARB_ETH",
                    "deposit_status": 1,
                    "withdrawal_status": 0,
                },
            ],
        }
    )
    out = await fetch_bithumb(client)
    # 키 불필요 — 인증 헤더 없이 1회 호출
    assert len(cap.requests) == 1
    assert "Authorization" not in cap.requests[0].headers
    eth = out["ETH"]
    assert eth.deposit_enabled is True
    assert eth.withdrawal_enabled is True  # 망별 OR
    assert [n.code for n in eth.networks] == ["ETH", "ARB_ETH"]
    assert eth.networks[1].wd is False
    assert eth.networks[0].name == "ETH"  # 응답에 표시명이 없다 → name = code


async def test_string_one_is_stopped() -> None:
    # 정수 비교 — 문자열 "1" 은 stopped (§3.4)
    _, client = json_client(
        {
            "status": "0000",
            "data": [
                {
                    "currency": "BTC",
                    "net_type": "BTC",
                    "deposit_status": "1",
                    "withdrawal_status": 1,
                }
            ],
        }
    )
    out = await fetch_bithumb(client)
    assert out["BTC"].deposit_enabled is False
    assert out["BTC"].withdrawal_enabled is True


async def test_non_0000_status_fails() -> None:
    _, client = json_client({"status": "5100", "data": []})
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bithumb(client)
    assert exc_info.value.message == "빗썸 자산 상태 응답 형식이 올바르지 않습니다."


async def test_data_not_list_fails() -> None:
    _, client = json_client({"status": "0000", "data": {"oops": 1}})
    with pytest.raises(WalletStatusError):
        await fetch_bithumb(client)


async def test_empty_currency_skipped() -> None:
    _, client = json_client(
        {
            "status": "0000",
            "data": [
                {
                    "currency": "",
                    "net_type": "X",
                    "deposit_status": 1,
                    "withdrawal_status": 1,
                }
            ],
        }
    )
    out = await fetch_bithumb(client)
    assert out == {}
