"""빗썸 입출금 상태 조회 — public, 키 불필요 (스펙 006 §3.4).

다른 거래소 조회기와 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
코인 단위 엔드포인트(/public/assetsstatus/ALL)는 망을 모르므로 쓰지 않는다.
"""

import httpx

from app.core.networks import Network
from app.features.wallet_status.models import CoinStatus, WalletStatusError

_BASE_URL = "https://api.bithumb.com"
_TIMEOUT = 10.0  # 요청별 타임아웃 — 시세용 3초보다 길다 (§3.5)

_FORMAT_MESSAGE = "빗썸 자산 상태 응답 형식이 올바르지 않습니다."


async def fetch_bithumb(client: httpx.AsyncClient) -> dict[str, CoinStatus]:
    """GET /public/assetsstatus/multichain/ALL — 코인 심볼(대문자) → CoinStatus."""
    url = _BASE_URL + "/public/assetsstatus/multichain/ALL"
    try:
        resp = await client.get(url, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise WalletStatusError(
            f"빗썸 지갑 상태 API 호출 실패: {type(exc).__name__}: {exc}",
            detail={"exchange": "bithumb"},
        ) from exc
    if resp.status_code != 200:
        raise WalletStatusError(
            f"빗썸 지갑 상태 API 가 {resp.status_code} 를 반환했습니다.",
            detail={"exchange": "bithumb", "body": resp.text[:500]},
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise WalletStatusError(
            _FORMAT_MESSAGE, detail={"exchange": "bithumb"}
        ) from exc
    if (
        not isinstance(body, dict)
        or body.get("status") != "0000"
        or not isinstance(body.get("data"), list)
    ):
        raise WalletStatusError(_FORMAT_MESSAGE, detail={"exchange": "bithumb"})

    out: dict[str, CoinStatus] = {}
    for item in body["data"]:
        currency = str(item.get("currency") or "").upper()
        if not currency:
            continue
        # 정수 비교 — 문자열 "1" 은 stopped (§3.4)
        dep = item.get("deposit_status") == 1
        wd = item.get("withdrawal_status") == 1
        code = str(item.get("net_type") or "").upper()
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
            # 빗썸 응답엔 표시명이 없다 → name = code (§3.1)
            status.networks.append(Network(code=code, name=code, dep=dep, wd=wd))
    return out
