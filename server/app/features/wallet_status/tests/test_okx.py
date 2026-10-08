"""OKX 입출금 조회 — 키 3개·HMAC Base64 서명·(ccy, chain) 행 합치기·code 실패 (스펙 045 §3.6·§4)."""

import base64
import hashlib
import hmac
import time

import httpx
import pytest

from app.features.wallet_status.models import WalletStatusError
from app.features.wallet_status.okx import fetch_okx
from app.features.wallet_status.tests.helpers import (
    Capture,
    FakeRecorder,
    assert_recorded_only,
    json_client,
)

KEYS = {
    "api_key": "key-honggildong",
    "secret_key": "secret-honggildong",
    "passphrase": "pass-honggildong",
}


def ok(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"code": "0", "msg": "", "data": rows}


def row(ccy: str, chain: str, dep: object, wd: object) -> dict[str, object]:
    return {
        "ccy": ccy,
        "chain": chain,
        "canDep": dep,
        "canWd": wd,
        "canInternal": True,
        "mainNet": True,
        "minWd": "0.001",
        "fee": "0.0001",
        "depEstOpenTime": "",
        "wdEstOpenTime": "",
    }


async def test_request_carries_four_headers_and_a_base64_hmac_signature() -> None:
    cap, client = json_client(ok([]))
    fixed = 1_607_418_537.715  # 2020-12-08T09:08:57.715Z — 공식 문서의 예시 시각
    await fetch_okx(client, **KEYS, clock=lambda: fixed)

    request = cap.requests[0]
    assert request.url.host == "openapi.okx.com"
    assert request.url.path == "/api/v5/asset/currencies"
    assert request.url.query == b""  # 파라미터 없음 = 전 통화·체인
    timestamp = request.headers["OK-ACCESS-TIMESTAMP"]
    assert timestamp == "2020-12-08T09:08:57.715Z"
    prehash = f"{timestamp}GET/api/v5/asset/currencies".encode()
    expected = base64.b64encode(
        hmac.new(b"secret-honggildong", prehash, hashlib.sha256).digest()
    ).decode()
    assert request.headers["OK-ACCESS-SIGN"] == expected
    assert request.headers["OK-ACCESS-KEY"] == "key-honggildong"
    assert request.headers["OK-ACCESS-PASSPHRASE"] == "pass-honggildong"


@pytest.mark.parametrize("missing", ["api_key", "secret_key", "passphrase"])
async def test_any_missing_key_fails_without_a_request(missing: str) -> None:
    cap, client = json_client(ok([]))
    keys = {**KEYS, missing: None}
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_okx(client, **keys)
    err = exc_info.value
    assert (
        err.message == "OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE 가 비어 있습니다."
    )
    assert err.calls == 0 and cap.requests == []  # 호출 자체가 없다


async def test_chain_rows_are_grouped_per_coin_and_prefix_is_stripped() -> None:
    _, client = json_client(
        ok(
            [
                row("USDT", "USDT-ERC20", True, False),
                row("BTC", "BTC-Bitcoin", True, True),
                row("USDT", "USDT-TRC20", True, True),
                row("ETH", "ETH-Arbitrum One", False, False),
                row("ETH", "ETH-ERC20", False, True),
                row("sol", "SOL-Solana", True, True),  # 심볼은 대문자로
                row(
                    "ZZZ", "", True, True
                ),  # 체인이 빈 행 — 네트워크 목록엔 없고 코인 값에만
            ]
        )
    )
    out = await fetch_okx(client, **KEYS)
    usdt = out["USDT"]
    assert (
        usdt.deposit_enabled is True and usdt.withdrawal_enabled is True
    )  # 네트워크별 OR
    assert [(n.code, n.name, n.dep, n.wd) for n in usdt.networks] == [
        (
            "ERC20",
            "ERC20",
            True,
            False,
        ),  # "<ccy>-" 를 뗀 나머지 — name 은 원문, code 는 대문자·공백 제거
        ("TRC20", "TRC20", True, True),
    ]
    eth = out["ETH"]
    assert eth.deposit_enabled is False and eth.withdrawal_enabled is True
    assert [(n.code, n.name) for n in eth.networks] == [
        ("ARBITRUMONE", "Arbitrum One"),
        ("ERC20", "ERC20"),
    ]
    assert [(n.code, n.name) for n in out["BTC"].networks] == [("BITCOIN", "Bitcoin")]
    assert [(n.code, n.name) for n in out["SOL"].networks] == [("SOLANA", "Solana")]
    zzz = out["ZZZ"]
    assert zzz.deposit_enabled is True and zzz.networks == []
    assert "ETC" not in out  # 응답에 없는 코인은 unknown — 조회기 결과에도 없다


async def test_only_json_true_opens_a_network() -> None:
    _, client = json_client(
        ok(
            [
                row(
                    "BBB", "BBB-Chain", "true", 1
                ),  # 문자열·숫자는 boolean true 가 아니다
                row(
                    "CCC", "Other-Name", True, True
                ),  # 접두사가 ccy 가 아니면 chain 전체
            ]
        )
    )
    out = await fetch_okx(client, **KEYS)
    assert [(n.code, n.dep, n.wd) for n in out["BBB"].networks] == [
        ("CHAIN", False, False)
    ]
    assert out["BBB"].deposit_enabled is False
    assert [(n.code, n.name) for n in out["CCC"].networks] == [
        ("OTHER-NAME", "Other-Name")
    ]


async def test_code_failure_http_500_and_bad_body_raise() -> None:
    _, client = json_client({"code": "50113", "msg": "Invalid Sign", "data": []})
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_okx(client, **KEYS)
    err = exc_info.value
    assert "code 50113" in err.message and "Invalid Sign" in err.message
    assert err.detail["exchange"] == "okx"
    cap = Capture([httpx.Response(500, text="y" * 600)])
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_okx(cap.client(), **KEYS)
    err = exc_info.value
    assert "500" in err.message and len(err.detail["body"]) <= 500  # type: ignore[arg-type]
    _, client = json_client({"code": "0", "msg": ""})
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_okx(client, **KEYS)
    assert "data" in exc_info.value.message
    cap = Capture([httpx.Response(200, text="<html>")])
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_okx(cap.client(), **KEYS)
    assert "JSON" in exc_info.value.message


async def test_network_failure_raises_without_recording() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    recorder = FakeRecorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_okx(client, **KEYS, record=recorder)
    assert "ConnectError" in exc_info.value.message
    assert recorder.lines == []  # 응답이 없으니 원문도 없다


async def test_response_body_recorded_verbatim_and_headers_nowhere() -> None:
    body = '{"code":"0","msg":"","data":[{"ccy":"BTC","chain":"BTC-Bitcoin","canDep":true,"canWd":true}]}'
    cap = Capture([httpx.Response(200, content=body.encode())])
    recorder = FakeRecorder()
    before = int(time.time() * 1000)
    out = await fetch_okx(cap.client(), **KEYS, record=recorder)
    assert out["BTC"].networks[0].name == "Bitcoin"
    assert_recorded_only(
        recorder,
        exchange="okx",
        source="rest:/api/v5/asset/currencies",
        body=body,
        before_ms=before,
    )
    recorded = repr(recorder.lines)
    assert "honggildong" not in recorded  # 키·패스프레이즈·서명은 어디에도 남지 않는다


async def test_http_500_body_is_recorded_too() -> None:
    cap = Capture([httpx.Response(500, text="okx-down")])
    recorder = FakeRecorder()
    before = int(time.time() * 1000)
    with pytest.raises(WalletStatusError):
        await fetch_okx(cap.client(), **KEYS, record=recorder)
    assert_recorded_only(
        recorder,
        exchange="okx",
        source="rest:/api/v5/asset/currencies",
        body="okx-down",
        before_ms=before,
    )
