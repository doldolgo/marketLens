"""OKX 입출금 상태 조회 — HMAC-SHA256 Base64 서명·패스프레이즈 (스펙 045 §3.6).

다른 거래소 조회기와 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
입출금 상태는 인증이 필요한 엔드포인트뿐이라 키 3개(API 키·시크릿·패스프레이즈)가 전부 있어야 조회한다.
응답은 (통화, 체인) 한 쌍이 한 행이라 같은 `ccy` 행을 모아 코인 하나로 만든다. HTTP 200 이어도 봉투 `code != "0"` 이면 실패다.
"""

import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.core.contracts import RawRecorder, noop_record
from app.core.networks import Network
from app.features.wallet_status.models import CoinStatus, WalletStatusError

_BASE_URL = "https://openapi.okx.com"
_CURRENCIES_PATH = "/api/v5/asset/currencies"
_TIMEOUT = 10.0  # 요청별 타임아웃 — 시세용 3초보다 길다 (006 §3.5)
_OK_CODE = "0"
_METHOD = "GET"


def _timestamp(now: float) -> str:
    """OK-ACCESS-TIMESTAMP — UTC ISO 8601 밀리초, 예 `2020-12-08T09:08:57.715Z` (§3.6)."""
    return (
        datetime.fromtimestamp(now, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def sign(secret_key: str, timestamp: str) -> str:
    """OK-ACCESS-SIGN = Base64(HMAC-SHA256(secret, timestamp + "GET" + 경로)) — 질의·본문 없음 (§3.6)."""
    prehash = f"{timestamp}{_METHOD}{_CURRENCIES_PATH}".encode()
    digest = hmac.new(secret_key.encode(), prehash, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


async def fetch_okx(
    client: httpx.AsyncClient,
    *,
    api_key: str | None,
    secret_key: str | None,
    passphrase: str | None,
    record: RawRecorder = noop_record,
    clock: Callable[
        [], float
    ] = time.time,  # 테스트가 고정 시각으로 서명 기대값을 비교한다
) -> dict[str, CoinStatus]:
    """GET /api/v5/asset/currencies — 코인 심볼(대문자) → CoinStatus.

    응답 본문은 상태 코드를 해석하기 전에 원문 싱크에 남긴다 — 요청 헤더(키·서명)는 어디에도 남기지 않는다 (§3.6).
    """
    if not api_key or not secret_key or not passphrase:
        raise WalletStatusError(
            "OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE 가 비어 있습니다.", calls=0
        )
    timestamp = _timestamp(clock())
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sign(secret_key, timestamp),
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
    }
    url = _BASE_URL + _CURRENCIES_PATH
    try:
        resp = await client.get(url, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        # 응답 자체가 없으므로 원문 싱크에 남길 본문도 없다
        raise WalletStatusError(
            f"OKX 지갑 상태 API 호출 실패: {type(exc).__name__}: {exc}",
            detail={"exchange": "okx"},
        ) from exc
    record("okx", f"rest:{_CURRENCIES_PATH}", int(time.time() * 1000), resp.text)
    if resp.status_code != 200:
        raise WalletStatusError(
            f"OKX 지갑 상태 API 가 {resp.status_code} 를 반환했습니다.",
            detail={"exchange": "okx", "body": resp.text[:500]},
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise WalletStatusError(
            f"OKX 지갑 상태 응답 JSON 파싱 실패: {exc}",
            detail={"exchange": "okx"},
        ) from exc
    if not isinstance(data, dict):
        raise WalletStatusError(
            "OKX 지갑 상태 응답이 객체가 아닙니다.", detail={"exchange": "okx"}
        )
    if data.get("code") != _OK_CODE:
        # HTTP 200 + code≠0 은 실패 — 인증 오류(50102 시각·50111 키·50113 서명)도 여기로, code·msg 를 경고에 (§3.8)
        raise WalletStatusError(
            f"OKX 지갑 상태 API code {data.get('code')}: {data.get('msg', '')}",
            detail={"exchange": "okx", "body": resp.text[:500]},
        )
    rows = data.get("data")
    if not isinstance(rows, list):
        raise WalletStatusError(
            "OKX 지갑 상태 응답에 data 가 없습니다.", detail={"exchange": "okx"}
        )

    out: dict[str, CoinStatus] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        coin = str(item.get("ccy") or "").upper()
        if not coin:
            continue
        # JSON boolean — 정확히 true 일 때만 열림. canInternal·depEstOpenTime·minWd·fee 는 판정에 쓰지 않는다 (§3.6)
        n_dep = item.get("canDep") is True
        n_wd = item.get("canWd") is True
        status = out.get(coin)
        if status is None:
            status = CoinStatus(deposit_enabled=False, withdrawal_enabled=False)
            out[coin] = status
        status.deposit_enabled = (
            status.deposit_enabled or n_dep
        )  # 코인 단위 dep/wd = 네트워크별 OR
        status.withdrawal_enabled = status.withdrawal_enabled or n_wd
        raw_chain = str(item.get("chain") or "")
        if not raw_chain:
            continue  # 체인이 빈 행은 네트워크 목록에서 제외 — 코인 값에만 반영
        # `chain` 은 `<ccy>-<네트워크>` — 앞의 "<ccy>-" 를 뗀 나머지가 네트워크 표기. 없으면 chain 전체 (§3.6)
        name = raw_chain
        if raw_chain.upper().startswith(coin + "-"):
            name = raw_chain[len(coin) + 1 :]
        if not name:
            name = raw_chain
        code = name.upper().replace(" ", "")
        status.networks.append(Network(code=code, name=name, dep=n_dep, wd=n_wd))
    return out
