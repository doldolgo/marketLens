"""입출금 상태 60초 캐시 — 수집 루프에 등록되는 조회기 묶음 (스펙 006 §3.5).

collector(core) 는 이 클래스를 Protocol(WalletStatusProvider) 로만 알고,
배선은 main.py lifespan 이 한다 — core 가 features 를 import 하지 않게.
키는 생성자로 주입받는다 — .env 는 pydantic-settings 가 런타임에 읽는다.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field

import httpx

from app.core.models import Row
from app.features.wallet_status.binance import fetch_binance
from app.features.wallet_status.bithumb import fetch_bithumb
from app.features.wallet_status.models import CoinStatus, WalletStatusError
from app.features.wallet_status.upbit import fetch_upbit

logger = logging.getLogger("marketlens.wallet_status")

# 조회 주기(초) — 사이 사이클은 캐시 (§3.5)
WALLET_REFRESH_INTERVAL = 60.0

# 경고·실패 목록의 순서 고정 — 사이클마다 순서가 바뀌면 비교가 성가시다
_EXCHANGES = ("upbit", "bithumb", "binance")


@dataclass
class _ExchangeState:
    """거래소 1개의 최근 조회 결과. 실패면 statuses 는 비운다 — 직전 성공값 유지 금지 (§3.5)."""

    available: bool
    statuses: dict[str, CoinStatus] = field(default_factory=dict)
    message: str | None = None  # 실패 메시지 (경고 1줄 조립용)


class WalletStatusService:
    def __init__(
        self,
        *,
        upbit_api_key: str | None,
        upbit_secret_key: str | None,
        binance_api_key: str | None,
        binance_secret_key: str | None,
        interval: float = WALLET_REFRESH_INTERVAL,
    ) -> None:
        self._upbit_api_key = upbit_api_key
        self._upbit_secret_key = upbit_secret_key
        self._binance_api_key = binance_api_key
        self._binance_secret_key = binance_secret_key
        self._interval = interval
        self._last_at: float | None = None
        self._states: dict[str, _ExchangeState] = {}

    # --- 수집 루프(core.collector)가 부르는 계약 ---

    async def refresh_if_due(self, client: httpx.AsyncClient) -> dict[str, int] | None:
        """60초가 지났으면 세 거래소를 병렬 조회하고 거래소별 호출 수를 돌려준다.

        캐시가 유효한 사이클은 None. 기동 첫 사이클은 캐시가 비어 즉시 호출한다.
        한 거래소 실패는 그 거래소만 unknown 으로 — 예외는 여기서 삼킨다 (§3.5).
        """
        now = time.monotonic()
        if self._last_at is not None and now - self._last_at < self._interval:
            return None
        self._last_at = now
        calls = await asyncio.gather(
            self._fetch_one(
                "upbit",
                fetch_upbit(
                    client,
                    api_key=self._upbit_api_key,
                    secret_key=self._upbit_secret_key,
                ),
            ),
            self._fetch_one("bithumb", fetch_bithumb(client)),
            self._fetch_one(
                "binance",
                fetch_binance(
                    client,
                    api_key=self._binance_api_key,
                    secret_key=self._binance_secret_key,
                ),
            ),
        )
        return {ex: n for ex, n in zip(_EXCHANGES, calls, strict=True) if n > 0}

    def apply(self, rows: list[Row], exchange: str) -> None:
        """캐시를 스냅샷 행에 반영한다 — 확인 불가면 null·빈 망 목록으로 덮는다 (§3.1·§3.5)."""
        state = self._states.get(exchange)
        for row in rows:
            status = (
                state.statuses.get(row.base.upper())
                if state is not None and state.available
                else None
            )
            if status is None:
                # 키 없음·조회 실패·응답에 그 코인이 없음 → 전부 unknown
                row.deposit_enabled = None
                row.withdrawal_enabled = None
                row.networks = []
            else:
                row.deposit_enabled = status.deposit_enabled
                row.withdrawal_enabled = status.withdrawal_enabled
                row.networks = list(status.networks)

    def availability(self) -> dict[str, bool]:
        """거래소별 최근 조회 성공 여부 — /refresh 의 wallet_status_available (§3.5)."""
        return {ex: st.available for ex, st in self._states.items()}

    def warnings(self) -> list[str]:
        """현재 실패 상태인 거래소의 경고 1줄씩 — 사이클마다 /refresh warnings 에 실린다."""
        out: list[str] = []
        for ex in _EXCHANGES:
            st = self._states.get(ex)
            if st is not None and not st.available:
                out.append(
                    f"{ex} 입출금 상태 조회 실패 — {st.message} (해당 거래소의 deposit_enabled / withdrawal_enabled 는 null)"
                )
        return out

    def failed(self) -> list[str]:
        """현재 실패 상태인 거래소 id — persist 루프가 dw_fail 점을 쓴다 (§3.5)."""
        return [
            ex
            for ex in _EXCHANGES
            if (st := self._states.get(ex)) is not None and not st.available
        ]

    # --- 내부 ---

    async def _fetch_one(
        self, exchange: str, coro: Awaitable[dict[str, CoinStatus]]
    ) -> int:
        """조회 1건 — 성공·실패를 상태로 바꾸고 나간 호출 수를 돌려준다."""
        try:
            statuses = await coro
        except WalletStatusError as exc:
            logger.warning("%s 입출금 상태 조회 실패: %s", exchange, exc.message)
            self._states[exchange] = _ExchangeState(
                available=False, message=exc.message
            )
            return exc.calls
        except Exception as exc:
            # 조회기 버그도 그 거래소만 unknown 으로 — 시세 수집은 무관해야 한다
            logger.exception("%s 입출금 상태 조회 중 예상 밖 예외", exchange)
            self._states[exchange] = _ExchangeState(
                available=False, message=f"{type(exc).__name__}: {exc}"
            )
            return 1
        self._states[exchange] = _ExchangeState(available=True, statuses=statuses)
        return 1
