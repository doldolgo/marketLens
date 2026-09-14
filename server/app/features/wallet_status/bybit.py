"""바이빗 입출금 상태 조회 — HMAC-SHA256 헤더 서명 (스펙 019 §3.6).

다른 거래소 조회기와 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
바이낸스와 달리 서명이 쿼리가 아니라 헤더에 실리고, HTTP 200 이어도 본문 `retCode != 0` 이면 실패다.
"""

import hashlib
import hmac
import time

import httpx

from app.core.contracts import RawRecorder, noop_record
from app.core.networks import Network
from app.features.wallet_status.models import CoinStatus, WalletStatusError

_BASE_URL = "https://api.bybit.com"
_COIN_INFO_PATH = "/v5/asset/coin/query-info"
_TIMEOUT = 10.0  # 요청별 타임아웃 — 시세용 3초보다 길다 (006 §3.5)
_RECV_WINDOW = "10000"  # 바이낸스 조회기와 같은 시계 오차 허용 (§3.6)


async def fetch_bybit(
    client: httpx.AsyncClient,
    *,
    api_key: str | None,
    secret_key: str | None,
    record: RawRecorder = noop_record,
) -> dict[str, CoinStatus]:
    """GET /v5/asset/coin/query-info — 코인 심볼(대문자) → CoinStatus.

    응답 본문은 상태 코드를 해석하기 전에 원문 싱크에 남긴다 — 서명·API 키는 헤더라 남지 않는다 (006 §3.5).
    """
    if not api_key or not secret_key:
        raise WalletStatusError(
            "BYBIT_API_KEY / BYBIT_SECRET_KEY 가 비어 있습니다.", calls=0
        )
    timestamp = str(int(time.time() * 1000))
    # 서명 평문 = timestamp + api_key + recv_window + queryString — GET 이고 질의가 없으니 queryString 은 빈 문자열 (§3.6)
    signature = hmac.new(
        secret_key.encode(),
        (timestamp + api_key + _RECV_WINDOW).encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
        "X-BAPI-SIGN": signature,
    }
    try:
        resp = await client.get(
            f"{_BASE_URL}{_COIN_INFO_PATH}", headers=headers, timeout=_TIMEOUT
        )
    except httpx.HTTPError as exc:
        # 응답 자체가 없으므로 원문 싱크에 남길 본문도 없다
        raise WalletStatusError(
            f"바이빗 지갑 상태 API 호출 실패: {type(exc).__name__}: {exc}",
            detail={"exchange": "bybit"},
        ) from exc
    record("bybit", f"rest:{_COIN_INFO_PATH}", int(time.time() * 1000), resp.text)
    if resp.status_code != 200:
        raise WalletStatusError(
            f"바이빗 지갑 상태 API 가 {resp.status_code} 를 반환했습니다.",
            detail={"exchange": "bybit", "body": resp.text[:500]},
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise WalletStatusError(
            f"바이빗 지갑 상태 응답 JSON 파싱 실패: {exc}",
            detail={"exchange": "bybit"},
        ) from exc
    if not isinstance(data, dict):
        raise WalletStatusError(
            "바이빗 지갑 상태 응답이 객체가 아닙니다.", detail={"exchange": "bybit"}
        )
    if data.get("retCode") != 0:
        # HTTP 200 + retCode≠0 은 실패 — 원문 body 로 코드 표를 채운다 (§3.8)
        raise WalletStatusError(
            f"바이빗 지갑 상태 API retCode {data.get('retCode')}: {data.get('retMsg', '')}",
            detail={"exchange": "bybit", "body": resp.text[:500]},
        )
    result = data.get("result")
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise WalletStatusError(
            "바이빗 지갑 상태 응답에 result.rows 가 없습니다.",
            detail={"exchange": "bybit"},
        )

    out: dict[str, CoinStatus] = {}
    for item in rows:
        coin = str(item.get("coin") or "").upper()
        chains = item.get("chains") or []
        if not coin or not chains:
            continue  # chains 가 빈 코인은 결과에 없다 (§3.6)
        dep = wd = False
        networks: list[Network] = []
        for chain in chains:
            # "1" 정상·"0" 중단. withdrawFee 는 보지 않는다 — 판정은 chainWithdraw 만 (§3.6)
            n_dep = chain.get("chainDeposit") == "1"
            n_wd = chain.get("chainWithdraw") == "1"
            dep = dep or n_dep  # 코인 단위 dep/wd = 망별 OR
            wd = wd or n_wd
            code = str(chain.get("chain") or "").upper()
            if not code:
                continue  # 망 코드가 빈 항목은 망 목록에서 제외 — 코인 값에만 반영
            name = str(chain.get("chainType") or "") or code
            networks.append(Network(code=code, name=name, dep=n_dep, wd=n_wd))
        out[coin] = CoinStatus(
            deposit_enabled=dep, withdrawal_enabled=wd, networks=networks
        )
    return out
