"""바이낸스 입출금 상태 조회 — HMAC-SHA256 서명 (스펙 006 §3.3).

다른 거래소 조회기와 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
"""

import hashlib
import hmac
import time

import httpx

from app.core.networks import Network
from app.features.wallet_status.models import CoinStatus, WalletStatusError

_BASE_URL = "https://api.binance.com"
_TIMEOUT = 10.0  # 요청별 타임아웃 — 시세용 3초보다 길다 (§3.5)
_RECV_WINDOW = "10000"


async def fetch_binance(
    client: httpx.AsyncClient, *, api_key: str | None, secret_key: str | None
) -> dict[str, CoinStatus]:
    """GET /sapi/v1/capital/config/getall — 코인 심볼(대문자) → CoinStatus."""
    if not api_key or not secret_key:
        raise WalletStatusError(
            "BINANCE_API_KEY / BINANCE_SECRET_KEY 가 비어 있습니다.", calls=0
        )
    # 쿼리 문자열은 정확히 이 두 키·이 순서. 서명은 그 문자열 그대로 — 재정렬·재인코딩 금지 (§3.3)
    query = f"timestamp={int(time.time() * 1000)}&recvWindow={_RECV_WINDOW}"
    signature = hmac.new(
        secret_key.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    url = f"{_BASE_URL}/sapi/v1/capital/config/getall?{query}&signature={signature}"
    try:
        resp = await client.get(
            url, headers={"X-MBX-APIKEY": api_key}, timeout=_TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise WalletStatusError(
            f"바이낸스 지갑 상태 API 호출 실패: {type(exc).__name__}: {exc}",
            detail={"exchange": "binance"},
        ) from exc
    if resp.status_code != 200:
        raise WalletStatusError(
            f"바이낸스 지갑 상태 API 가 {resp.status_code} 를 반환했습니다.",
            detail={"exchange": "binance", "body": resp.text[:500]},
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise WalletStatusError(
            f"바이낸스 지갑 상태 응답 JSON 파싱 실패: {exc}",
            detail={"exchange": "binance"},
        ) from exc
    if not isinstance(data, list):
        raise WalletStatusError(
            "바이낸스 지갑 상태 응답이 배열이 아닙니다.", detail={"exchange": "binance"}
        )

    out: dict[str, CoinStatus] = {}
    for item in data:
        coin = str(item.get("coin") or "").upper()
        network_list = item.get("networkList") or []
        if not coin or not network_list:
            continue  # networkList 가 빈 코인은 결과에 없다 (§3.3)
        # 코인 레벨 depositAllEnable/withdrawAllEnable 은 쓰지 않는다 — 비관 편향 (§3.3)
        dep = wd = False
        networks: list[Network] = []
        for net in network_list:
            n_dep = bool(net.get("depositEnable"))
            n_wd = bool(net.get("withdrawEnable"))
            dep = dep or n_dep  # 코인 단위 dep/wd = 망별 OR
            wd = wd or n_wd
            code = str(net.get("network") or "").upper()
            if not code:
                continue  # 망 코드가 빈 항목은 망 목록에서 제외 — 코인 값에만 반영
            name = str(net.get("name") or "") or code
            networks.append(Network(code=code, name=name, dep=n_dep, wd=n_wd))
        out[coin] = CoinStatus(
            deposit_enabled=dep, withdrawal_enabled=wd, networks=networks
        )
    return out
