"""비트겟 입출금 상태 조회 — public, 키 불필요 (스펙 020 §3.6).

다른 거래소 조회기와 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
빗썸처럼 인증이 없어 항상 조회 가능하고, HTTP 200 이어도 봉투 `code != "00000"` 이면 실패다.
망 필드는 문자열 `"true"/"false"` 라 정확히 그 문자열과 비교한다.
"""

import time

import httpx

from app.core.contracts import RawRecorder, noop_record
from app.core.networks import Network
from app.features.wallet_status.models import CoinStatus, WalletStatusError

_BASE_URL = "https://api.bitget.com"
_COINS_PATH = "/api/v2/spot/public/coins"
_TIMEOUT = 10.0  # 요청별 타임아웃 — 시세용 3초보다 길다 (006 §3.5)
_OK_CODE = "00000"


async def fetch_bitget(
    client: httpx.AsyncClient, *, record: RawRecorder = noop_record
) -> dict[str, CoinStatus]:
    """GET /api/v2/spot/public/coins — 코인 심볼(대문자) → CoinStatus.

    응답 본문은 상태 코드를 해석하기 전에 원문 싱크에 남긴다 (006 §3.5). 인증 헤더는 없다.
    """
    url = _BASE_URL + _COINS_PATH
    try:
        resp = await client.get(url, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        # 응답 자체가 없으므로 원문 싱크에 남길 본문도 없다
        raise WalletStatusError(
            f"비트겟 지갑 상태 API 호출 실패: {type(exc).__name__}: {exc}",
            detail={"exchange": "bitget"},
        ) from exc
    record("bitget", f"rest:{_COINS_PATH}", int(time.time() * 1000), resp.text)
    if resp.status_code != 200:
        raise WalletStatusError(
            f"비트겟 지갑 상태 API 가 {resp.status_code} 를 반환했습니다.",
            detail={"exchange": "bitget", "body": resp.text[:500]},
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise WalletStatusError(
            f"비트겟 지갑 상태 응답 JSON 파싱 실패: {exc}",
            detail={"exchange": "bitget"},
        ) from exc
    if not isinstance(data, dict):
        raise WalletStatusError(
            "비트겟 지갑 상태 응답이 객체가 아닙니다.", detail={"exchange": "bitget"}
        )
    if data.get("code") != _OK_CODE:
        # HTTP 200 + code≠00000 은 실패 — 원문 body 로 코드 표를 채운다 (§3.8)
        raise WalletStatusError(
            f"비트겟 지갑 상태 API code {data.get('code')}: {data.get('msg', '')}",
            detail={"exchange": "bitget", "body": resp.text[:500]},
        )
    rows = data.get("data")
    if not isinstance(rows, list):
        raise WalletStatusError(
            "비트겟 지갑 상태 응답에 data 가 없습니다.",
            detail={"exchange": "bitget"},
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
            # 문자열 "true"/"false" — congestion·transfer·needTag 는 판정에 쓰지 않는다 (§3.6)
            n_dep = chain.get("rechargeable") == "true"
            n_wd = chain.get("withdrawable") == "true"
            dep = dep or n_dep  # 코인 단위 dep/wd = 망별 OR
            wd = wd or n_wd
            raw_chain = str(chain.get("chain") or "")
            if not raw_chain:
                continue  # 망 코드가 빈 항목은 망 목록에서 제외 — 코인 값에만 반영
            # 비트겟은 망 표시명 필드가 따로 없다 → name 은 chain 원문 (§3.6)
            networks.append(
                Network(code=raw_chain.upper(), name=raw_chain, dep=n_dep, wd=n_wd)
            )
        out[coin] = CoinStatus(
            deposit_enabled=dep, withdrawal_enabled=wd, networks=networks
        )
    return out
