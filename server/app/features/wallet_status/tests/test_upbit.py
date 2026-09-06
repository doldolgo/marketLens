"""업비트 입출금 조회 — JWT 서명·wallet_state 해석·망 목록 (스펙 006 §3.2·§4)."""

import time

import httpx
import jwt
import pytest

from app.features.wallet_status.models import WalletStatusError
from app.features.wallet_status.tests.helpers import (
    Capture,
    FakeRecorder,
    assert_recorded_only,
    json_client,
)
from app.features.wallet_status.upbit import fetch_upbit

# HS256 최소 권장 키 길이(32바이트)를 채운 가짜 키 — 짧으면 PyJWT 가 경고를 낸다
_FAKE_SECRET = "fake-secret-key-0123456789abcdef"
_LEAKABLE_SECRET = "top-secret-value-0123456789abcdef"


async def test_jwt_hs256_signature_and_fresh_nonce_per_call() -> None:
    # 서명은 결정적이다 — 같은 secret 으로 디코드해 payload 를 확인한다 (§4 서명 테스트)
    cap, client = json_client([])
    await fetch_upbit(client, api_key="fake-ak", secret_key=_FAKE_SECRET)
    await fetch_upbit(client, api_key="fake-ak", secret_key=_FAKE_SECRET)

    assert len(cap.requests) == 2
    nonces = []
    for request in cap.requests:
        assert str(request.url).endswith("/v1/status/wallet")
        assert not request.url.query  # 쿼리 없음 — query_hash 를 넣지 않는 근거
        auth = request.headers["Authorization"]
        assert auth.startswith("Bearer ")
        payload = jwt.decode(
            auth.removeprefix("Bearer "), _FAKE_SECRET, algorithms=["HS256"]
        )
        assert payload["access_key"] == "fake-ak"
        assert payload["nonce"]
        nonces.append(payload["nonce"])
    # 요청마다 새 UUID4 — 두 번 호출하면 nonce 가 다르다
    assert nonces[0] != nonces[1]


async def test_withdraw_only_maps_dep_stopped_wd_ok() -> None:
    _, client = json_client(
        [{"currency": "BTC", "wallet_state": "withdraw_only", "net_type": "BTC"}]
    )
    out = await fetch_upbit(client, api_key="ak", secret_key=_FAKE_SECRET)
    assert out["BTC"].deposit_enabled is False
    assert out["BTC"].withdrawal_enabled is True


@pytest.mark.parametrize("state", ["paused", "unsupported", "정의안된문자열"])
async def test_paused_and_undefined_states_map_stopped(state: str) -> None:
    _, client = json_client(
        [{"currency": "BTC", "wallet_state": state, "net_type": "BTC"}]
    )
    out = await fetch_upbit(client, api_key="ak", secret_key=_FAKE_SECRET)
    assert out["BTC"].deposit_enabled is False
    assert out["BTC"].withdrawal_enabled is False


async def test_two_networks_or_and_order_preserved() -> None:
    # 같은 코인 2행(망 2개, 한쪽만 출금 ok) → 코인 단위 wd ok, 망 목록 2개 순서 보존 (§4)
    _, client = json_client(
        [
            {
                "currency": "GRT",
                "wallet_state": "deposit_only",
                "net_type": "ARBITRUM",
                "network_name": "Arbitrum One",
            },
            {
                "currency": "GRT",
                "wallet_state": "working",
                "net_type": "ETH",
                "network_name": "Ethereum",
            },
        ]
    )
    out = await fetch_upbit(client, api_key="ak", secret_key=_FAKE_SECRET)
    grt = out["GRT"]
    assert grt.withdrawal_enabled is True  # 망별 OR
    assert grt.deposit_enabled is True
    assert [n.code for n in grt.networks] == ["ARBITRUM", "ETH"]
    assert grt.networks[0].wd is False
    assert grt.networks[1].name == "Ethereum"


async def test_network_name_falls_back_to_code_and_bad_rows_skipped() -> None:
    _, client = json_client(
        [
            {"currency": "SEI", "wallet_state": "working", "net_type": "SEI"},
            {"currency": "", "wallet_state": "working", "net_type": "X"},
            {"currency": "ETC", "wallet_state": "", "net_type": "ETC"},
        ]
    )
    out = await fetch_upbit(client, api_key="ak", secret_key=_FAKE_SECRET)
    assert set(out) == {"SEI"}
    assert out["SEI"].networks[0].name == "SEI"  # network_name 없으면 code


async def test_missing_keys_fail_without_any_call() -> None:
    cap, client = json_client([])
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_upbit(client, api_key=None, secret_key=_FAKE_SECRET)
    assert (
        exc_info.value.message == "UPBIT_API_KEY / UPBIT_SECRET_KEY 가 비어 있습니다."
    )
    assert exc_info.value.calls == 0
    assert cap.requests == []  # 호출 0회로 실패


async def test_http_500_message_has_status_and_no_secret() -> None:
    cap = Capture([httpx.Response(500, text="x" * 600)])
    client = cap.client()
    with pytest.raises(WalletStatusError) as exc_info:
        await fetch_upbit(client, api_key="ak", secret_key=_LEAKABLE_SECRET)
    err = exc_info.value
    assert "500" in err.message
    assert err.detail["exchange"] == "upbit"
    assert len(err.detail["body"]) <= 500  # detail body 앞 500자
    assert _LEAKABLE_SECRET not in err.message
    assert _LEAKABLE_SECRET not in str(err.detail)


async def test_response_body_recorded_to_raw_sink_without_auth_header() -> None:
    # 성공 응답 본문이 exchange·rest:<경로>·수신 시각과 함께 그대로 남고, 헤더·토큰은 없다 (§4)
    body = '[{"currency":"BTC","wallet_state":"working","net_type":"BTC"}]'
    cap = Capture([httpx.Response(200, content=body.encode())])
    recorder = FakeRecorder()
    before = int(time.time() * 1000)
    await fetch_upbit(
        cap.client(), api_key="ak", secret_key=_LEAKABLE_SECRET, record=recorder
    )
    assert_recorded_only(
        recorder,
        exchange="upbit",
        source="rest:/v1/status/wallet",
        body=body,
        before_ms=before,
    )
    token = cap.requests[0].headers["Authorization"].removeprefix("Bearer ")
    assert token not in recorder.lines[0][3]
    assert _LEAKABLE_SECRET not in recorder.lines[0][3]


async def test_http_500_body_is_recorded_too() -> None:
    # 실패 응답도 받은 그대로 원문 싱크에 — 성공·실패 모두 (§3.5)
    cap = Capture([httpx.Response(500, text="upbit-down")])
    recorder = FakeRecorder()
    before = int(time.time() * 1000)
    with pytest.raises(WalletStatusError):
        await fetch_upbit(
            cap.client(), api_key="ak", secret_key=_FAKE_SECRET, record=recorder
        )
    assert_recorded_only(
        recorder,
        exchange="upbit",
        source="rest:/v1/status/wallet",
        body="upbit-down",
        before_ms=before,
    )


async def test_transport_failure_records_nothing() -> None:
    # 응답 자체가 없으면 남길 본문도 없다 (§3.5)
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    recorder = FakeRecorder()
    with pytest.raises(WalletStatusError):
        await fetch_upbit(
            client, api_key="ak", secret_key=_FAKE_SECRET, record=recorder
        )
    assert recorder.lines == []
