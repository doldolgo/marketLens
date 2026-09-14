"""바이빗 입출금 조회 — 헤더 서명·chains 해석·retCode 실패 (스펙 019 §3.6·§4)."""

import hashlib
import hmac
import time

import httpx
import pytest

from app.features.wallet_status.bybit import fetch_bybit
from app.features.wallet_status.models import WalletStatusError
from app.features.wallet_status.tests.helpers import (
    Capture,
    FakeRecorder,
    assert_recorded_only,
    json_client,
)


def ok(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"retCode": 0, "retMsg": "", "result": {"rows": rows}, "time": 1}


async def test_hmac_signature_in_headers_matches_directly_computed_value() -> None:
    cap, client = json_client(ok([]))
    await fetch_bybit(client, api_key="fake-ak", secret_key="fake-sk")

    request = cap.requests[0]
    assert request.url.path == "/v5/asset/coin/query-info"
    assert request.url.query == b""  # 질의 없음 — 서명 평문의 queryString 은 빈 문자열
    headers = request.headers
    assert headers["X-BAPI-API-KEY"] == "fake-ak"
    assert headers["X-BAPI-RECV-WINDOW"] == "10000"
    timestamp = headers["X-BAPI-TIMESTAMP"]
    assert timestamp.isdigit() and len(timestamp) == 13
    expected = hmac.new(
        b"fake-sk", f"{timestamp}fake-ak10000".encode(), hashlib.sha256
    ).hexdigest()
    assert headers["X-BAPI-SIGN"] == expected  # 소문자 hex


async def test_chains_map_to_networks_and_empty_chains_coin_is_absent() -> None:
    _, client = json_client(
        ok(
            [
                {"coin": "AAA", "name": "A", "chains": []},
                {
                    "coin": "GRT",
                    "name": "The Graph",
                    "chains": [
                        {
                            "chain": "ARBI",
                            "chainType": "Arbitrum One",
                            "chainDeposit": "1",
                            "chainWithdraw": "1",
                            "withdrawFee": "0.5",
                        },
                        {
                            "chain": "ETH",
                            "chainType": "Ethereum",
                            "chainDeposit": "1",
                            "chainWithdraw": "0",
                            "withdrawFee": "",
                        },
                    ],
                },
                {
                    "coin": "ZZZ",
                    "chains": [
                        {
                            "chain": "ZZZ",
                            "chainType": "",
                            "chainDeposit": "0",
                            "chainWithdraw": "0",
                        }
                    ],
                },
            ]
        )
    )
    out = await fetch_bybit(client, api_key="ak", secret_key="sk")
    assert "AAA" not in out
    grt = out["GRT"]
    assert grt.deposit_enabled is True and grt.withdrawal_enabled is True  # 망별 OR
    assert [(n.code, n.name, n.dep, n.wd) for n in grt.networks] == [
        ("ARBI", "Arbitrum One", True, True),
        ("ETH", "Ethereum", True, False),
    ]
    zzz = out["ZZZ"]
    assert zzz.deposit_enabled is False and zzz.withdrawal_enabled is False
    assert zzz.networks[0].name == "ZZZ"  # chainType 없으면 code


async def test_empty_chain_code_counts_only_toward_coin_value() -> None:
    _, client = json_client(
        ok(
            [
                {
                    "coin": "BBB",
                    "chains": [
                        {
                            "chain": "",
                            "chainType": "??",
                            "chainDeposit": "1",
                            "chainWithdraw": "1",
                        },
                        {"chain": "BBB", "chainDeposit": "0", "chainWithdraw": "0"},
                    ],
                }
            ]
        )
    )
    out = await fetch_bybit(client, api_key="ak", secret_key="sk")
    assert out["BBB"].deposit_enabled is True
    assert [n.code for n in out["BBB"].networks] == ["BBB"]


async def test_missing_keys_fail_without_any_call() -> None:
    cap, client = json_client(ok([]))
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bybit(client, api_key=None, secret_key="sk")
    assert (
        exc_info.value.message == "BYBIT_API_KEY / BYBIT_SECRET_KEY 가 비어 있습니다."
    )
    assert exc_info.value.calls == 0
    assert cap.requests == []


async def test_ret_code_failure_and_http_500_carry_status_and_no_secret() -> None:
    _, client = json_client(
        {"retCode": 10003, "retMsg": "API key is invalid.", "result": {}}
    )
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bybit(client, api_key="ak", secret_key="top-secret-value")
    err = exc_info.value
    assert "retCode 10003" in err.message and "API key is invalid." in err.message
    assert "top-secret-value" not in err.message and "top-secret-value" not in str(
        err.detail
    )
    cap = Capture([httpx.Response(500, text="y" * 600)])
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_bybit(cap.client(), api_key="ak", secret_key="top-secret-value")
    err = exc_info.value
    assert "500" in err.message and len(err.detail["body"]) <= 500  # type: ignore[arg-type]


async def test_response_body_recorded_verbatim_without_key_or_signature() -> None:
    body = '{"retCode":0,"retMsg":"","result":{"rows":[{"coin":"BTC","chains":[{"chain":"BTC","chainType":"Bitcoin","chainDeposit":"1","chainWithdraw":"1"}]}]}}'
    cap = Capture([httpx.Response(200, content=body.encode())])
    recorder = FakeRecorder()
    before = int(time.time() * 1000)
    out = await fetch_bybit(
        cap.client(), api_key="fake-api-key-xyz", secret_key="sk", record=recorder
    )
    assert out["BTC"].networks[0].name == "Bitcoin"
    assert_recorded_only(
        recorder,
        exchange="bybit",
        source="rest:/v5/asset/coin/query-info",
        body=body,
        before_ms=before,
    )
    signature = cap.requests[0].headers["X-BAPI-SIGN"]
    _, source, _, payload = recorder.lines[0]
    for leak in (signature, "fake-api-key-xyz"):
        assert leak not in source and leak not in payload


async def test_http_500_body_is_recorded_too() -> None:
    cap = Capture([httpx.Response(500, text="bybit-down")])
    recorder = FakeRecorder()
    before = int(time.time() * 1000)
    with pytest.raises(WalletStatusError):
        await fetch_bybit(cap.client(), api_key="ak", secret_key="sk", record=recorder)
    assert_recorded_only(
        recorder,
        exchange="bybit",
        source="rest:/v5/asset/coin/query-info",
        body="bybit-down",
        before_ms=before,
    )
