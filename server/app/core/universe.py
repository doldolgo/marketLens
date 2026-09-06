"""마켓 우주 — 스펙 001 §3.2.

우주 = (업비트 KRW base ∪ 빗썸 KRW base) ∩ 바이낸스 USDT base. 매초 세 목록을 동시에 받아
확정하고 `/refresh` 트리거가 즉시 갱신한다. 국내 스트림은 자기 KRW 전 마켓을 구독하고
(KRW-USDT 포함), 우주 밖 base 의 행은 저장되지 않는다. 바이낸스 심볼 집합은 012 가 제공한다
(ForeignSymbolSource). 목록 실패는 직전 목록 유지 — 같은 거래소·같은 원인은 60초에 1줄만 로그.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.contracts import ForeignSymbolSource
from app.core.errors import ExchangeApiError, ExchangeError
from app.core.quotes import QuoteSink

logger = logging.getLogger("marketlens.universe")

UNIVERSE_INTERVAL = 1.0  # 회차가 끝난 뒤 쉬는 시간 — 초당 1회를 넘지 않는다 (§3.2)
LOG_SUPPRESS_SEC = 60.0  # 같은 거래소·같은 원인의 실패 로그 간격 (§3.2)
FOREIGN = "binance"


class DomesticStream(Protocol):
    """우주가 부르는 국내 스트림의 면 — 마켓 목록 REST 와 구독 대상 교체."""

    @property
    def id(self) -> str: ...

    async def fetch_markets(self, client: httpx.AsyncClient) -> list[str]: ...

    def set_markets(self, codes: list[str]) -> None: ...


@dataclass
class RefreshOutcome:
    """갱신 1회의 결과 — 거래소별 REST 호출 수와 실패."""

    calls: dict[str, int] = field(default_factory=dict)
    failures: list[ExchangeError] = field(default_factory=list)


class UniverseRefresher:
    def __init__(
        self,
        *,
        sink: QuoteSink,
        streams: Sequence[DomesticStream],
        foreign: ForeignSymbolSource,
        client: httpx.AsyncClient,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._streams = list(streams)
        self._foreign = foreign
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic
        self._markets: dict[str, list[str]] = {}  # 거래소 → KRW 마켓 코드 전체
        # (거래소, 원인) → (마지막으로 로그한 시각, 그 뒤 억누른 횟수) — 초당 폭주 방지 (§3.2)
        self._muted: dict[tuple[str, str], tuple[float, int]] = {}
        self._task: asyncio.Task[None] | None = None
        self.universe: set[str] = set()

    async def refresh(self) -> RefreshOutcome:
        """세 목록을 동시에 받아 우주·구독을 갱신한다. 실패한 거래소는 직전 목록 유지."""
        outcome = RefreshOutcome()
        results = await asyncio.gather(
            *(self._fetch_domestic(s) for s in self._streams),
            self._fetch_foreign(),
        )
        for exchange, calls, error in results:
            if error is None:
                outcome.calls[exchange] = calls
            else:
                outcome.failures.append(error)
        self._apply()
        return outcome

    async def _fetch_domestic(
        self, stream: DomesticStream
    ) -> tuple[str, int, ExchangeError | None]:
        try:
            self._markets[stream.id] = await stream.fetch_markets(self._client)
        except Exception as exc:
            return stream.id, 0, self._failed(stream.id, exc)
        return stream.id, 1, None

    async def _fetch_foreign(self) -> tuple[str, int, ExchangeError | None]:
        try:
            calls = await self._foreign.refresh(self._client)
        except Exception as exc:
            return FOREIGN, 0, self._failed(FOREIGN, exc)
        return FOREIGN, calls, None

    def _failed(self, exchange: str, exc: Exception) -> ExchangeError:
        """실패 1건 → 60초 억제 로그 + 트리거 요약용 거래소 예외 (§3.2·§3.9).

        거래소 예외가 아닌 예상 밖 예외(버그·예상 밖 타입)도 그 거래소의 목록 실패다 —
        직전 목록을 유지하고 다음 초에 다시 부른다. 갱신 루프는 멈추지 않는다.
        """
        if isinstance(exc, ExchangeError):
            error, cause = exc, exc.kind
        else:
            error = ExchangeApiError(
                exchange, None, f"마켓 목록 갱신 중 예상 밖 예외: {exc!r}"
            )
            cause = type(exc).__name__
        self._log_failure(
            exchange, cause, error, unexpected=not isinstance(exc, ExchangeError)
        )
        return error

    def _log_failure(
        self, exchange: str, cause: str, error: ExchangeError, *, unexpected: bool
    ) -> None:
        now = self._monotonic()
        last, muted = self._muted.get((exchange, cause), (None, 0))
        if last is not None and now - last < LOG_SUPPRESS_SEC:
            self._muted[(exchange, cause)] = (last, muted + 1)
            return
        self._muted[(exchange, cause)] = (now, 0)
        suffix = f" (직전 60초 동안 같은 원인 {muted}회 억제)" if muted else ""
        if unexpected:
            logger.exception(
                "%s 마켓 목록 갱신이 예상 밖 예외로 끝났다 — 직전 목록 유지%s",
                exchange,
                suffix,
            )
        else:
            logger.warning(
                "%s 마켓 목록 갱신 실패 — 직전 목록 유지%s: %s", exchange, suffix, error
            )

    def _apply(self) -> None:
        domestic: set[str] = set()
        for codes in self._markets.values():
            domestic |= {c.split("-", 1)[1].upper() for c in codes if "-" in c}
        self.universe = domestic & self._foreign.bases()
        removed = self._sink.set_universe(self.universe)
        if removed:
            logger.info("우주 갱신으로 행 %d개 소멸 (상폐·교집합 이탈)", removed)
        for stream in self._streams:
            stream.set_markets(self._markets.get(stream.id, []))
        # 바이낸스는 우주의 심볼만 구독한다 — 확정된 우주를 넘기면 커넥터가 차이만 재조정한다 (012 §3.3)
        self._foreign.set_universe(self.universe)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """매초 회차 — 세 목록을 동시에 받고, 회차가 끝난 뒤 1초를 쉰다 (§3.2).

        예상 밖 예외는 로그 후 다음 회차 — 갱신 루프는 멈추지 않는다.
        """
        while True:
            try:
                await self.refresh()
            except Exception:
                logger.exception(
                    "마켓 우주 갱신이 예상 밖 예외로 끝났다 — 다음 회차에 계속"
                )
            await self._sleep(UNIVERSE_INTERVAL)

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
