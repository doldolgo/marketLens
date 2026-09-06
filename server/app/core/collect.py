"""즉시 갱신 트리거 — 스펙 001 §3.9. `POST /refresh`(003) 가 부른다.

순서: 마켓 우주 즉시 갱신(REST) → 변경분 재구독 → 006 조회 즉시 실행 → 요약.
시세는 묻지 않는다 — 상시 스트림이 이미 갱신한다. 동시 호출은 직렬화하고 틱 루프와 독립이다.
"""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx

from app.core.config import DOMESTIC_EXCHANGES, EXCHANGES
from app.core.contracts import StreamJudge, WalletStatusProvider
from app.core.live_store import LiveStore
from app.core.universe import UniverseRefresher


@dataclass
class RefreshSummary:
    """트리거 1회의 요약 — 003 이 POST /refresh 응답으로 노출한다."""

    saved: dict[str, int]  # 거래소별 현재 메모리 행 수
    calls: dict[str, int]  # 거래소별 이 트리거로 나간 REST 호출 수
    rates_observed: list[str]  # 지금 USDT 시세가 있는 국내 거래소 id
    failures: list[dict[str, str]]  # {exchange, error_code, message}
    warnings: list[str]
    duration_ms: float
    fetched_at: int  # epoch ms
    wallet_status_available: dict[str, bool] = field(default_factory=dict)


class CollectService:
    def __init__(
        self,
        *,
        store: LiveStore,
        universe: UniverseRefresher,
        streams: Sequence[StreamJudge],
        client: httpx.AsyncClient,
        wallet: WalletStatusProvider | None = None,
    ) -> None:
        self._store = store
        self._universe = universe
        self._streams = list(streams)
        self._client = client
        self._wallet = wallet
        self._lock = asyncio.Lock()  # 동시 호출은 직렬화 (§3.9)

    async def refresh_now(self) -> RefreshSummary:
        async with self._lock:
            return await self._run()

    async def _run(self) -> RefreshSummary:
        started = time.monotonic()
        fetched_at = int(time.time() * 1000)
        outcome = await self._universe.refresh()
        calls = dict(outcome.calls)
        failures = [
            {"exchange": exc.exchange, "error_code": exc.code, "message": str(exc)}
            for exc in outcome.failures
        ]
        warnings: list[str] = []
        available: dict[str, bool] = {}
        if self._wallet is not None:
            wallet_calls = await self._wallet.refresh_if_due(self._client, force=True)
            for ex, n in (wallet_calls or {}).items():
                calls[ex] = calls.get(ex, 0) + n
            for ex in EXCHANGES:
                self._wallet.apply(self._store.get_all(exchange=ex), ex)
            warnings.extend(self._wallet.warnings())
            available = self._wallet.availability()

        # 지금 실패 판정인 스트림도 failures 에 — error_code 는 실패 종류(kind)
        now_ms = int(time.time() * 1000)
        for stream in self._streams:
            verdict = stream.judge(now_ms)
            if verdict is None or verdict.ok or verdict.error is None:
                continue
            failures.append(
                {
                    "exchange": stream.id,
                    "error_code": verdict.error.kind,
                    "message": verdict.error.message,
                }
            )

        rates_observed = [ex for ex in DOMESTIC_EXCHANGES if self._store.get_rate(ex)]
        missing_rate = [ex for ex in DOMESTIC_EXCHANGES if ex not in rates_observed]
        if missing_rate:
            warnings.append(
                "KRW-USDT 호가가 없어 USDT 시세를 못 구한 거래소: "
                + ", ".join(missing_rate)
                + " (해당 국내 거래소의 김프 계산은 빠진다)."
            )
        return RefreshSummary(
            saved={ex: len(self._store.get_all(exchange=ex)) for ex in EXCHANGES},
            calls=calls,
            rates_observed=rates_observed,
            failures=failures,
            warnings=warnings,
            duration_ms=(time.monotonic() - started) * 1000,
            fetched_at=fetched_at,
            wallet_status_available=available,
        )
