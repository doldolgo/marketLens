"""업비트 입출금 상태 조회 — JWT HS256 인증 (스펙 006 §3.2).

다른 거래소 조회기와 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
"""

import uuid

import httpx
import jwt

from app.core.networks import Network
from app.features.wallet_status.models import CoinStatus, WalletStatusError

_BASE_URL = "https://api.upbit.com"
_TIMEOUT = 10.0  # 요청별 타임아웃 — 시세용 3초보다 길다 (§3.5)

# wallet_state 해석 — 목록에 없는 문자열은 전부 stopped/stopped (§3.2)
_STATE_MAP: dict[str, tuple[bool, bool]] = {
    "working": (True, True),
    "withdraw_only": (False, True),
    "deposit_only": (True, False),
}


async def fetch_upbit(
    client: httpx.AsyncClient, *, api_key: str | None, secret_key: str | None
) -> dict[str, CoinStatus]:
    """GET /v1/status/wallet — 코인 심볼(대문자) → CoinStatus. 실패는 예외로 던진다."""
    if not api_key or not secret_key:
        # 키가 비면 호출하지 않고 실패로 끝낸다 (§3.2)
        raise WalletStatusError(
            "UPBIT_API_KEY / UPBIT_SECRET_KEY 가 비어 있습니다.", calls=0
        )
    # 쿼리가 없으므로 query_hash 는 넣지 않는다. nonce 는 요청마다 새 UUID4.
    payload = {"access_key": api_key, "nonce": str(uuid.uuid4())}
    token = jwt.encode(payload, secret_key, algorithm="HS256")
    url = _BASE_URL + "/v1/status/wallet"
    try:
        resp = await client.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT
        )
    except httpx.HTTPError as exc:
        # 예외 메시지엔 타입·사유만 — 키·토큰이 새지 않게 헤더를 담지 않는다
        raise WalletStatusError(
            f"업비트 지갑 상태 API 호출 실패: {type(exc).__name__}: {exc}",
            detail={"exchange": "upbit"},
        ) from exc
    if resp.status_code != 200:
        raise WalletStatusError(
            f"업비트 지갑 상태 API 가 {resp.status_code} 를 반환했습니다.",
            detail={"exchange": "upbit", "body": resp.text[:500]},
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise WalletStatusError(
            f"업비트 지갑 상태 응답 JSON 파싱 실패: {exc}",
            detail={"exchange": "upbit"},
        ) from exc
    if not isinstance(data, list):
        raise WalletStatusError(
            "업비트 지갑 상태 응답이 배열이 아닙니다.", detail={"exchange": "upbit"}
        )

    out: dict[str, CoinStatus] = {}
    for item in data:
        currency = str(item.get("currency") or "").upper()
        wallet_state = str(item.get("wallet_state") or "")
        if not currency or not wallet_state:
            continue  # currency 나 wallet_state 가 비면 그 행은 건너뛴다
        dep, wd = _STATE_MAP.get(wallet_state, (False, False))
        code = str(item.get("net_type") or "").upper()
        name = str(item.get("network_name") or "") or code
        status = out.get(currency)
        if status is None:
            out[currency] = status = CoinStatus(
                deposit_enabled=dep, withdrawal_enabled=wd
            )
        else:
            # 같은 코인 여러 행(망마다 1행) → 코인 단위 값은 망별 OR (§3.1)
            status.deposit_enabled = status.deposit_enabled or dep
            status.withdrawal_enabled = status.withdrawal_enabled or wd
        if code:  # 망 코드가 빈 행은 코인 값에만 반영하고 망 목록엔 넣지 않는다
            status.networks.append(Network(code=code, name=name, dep=dep, wd=wd))
    return out
