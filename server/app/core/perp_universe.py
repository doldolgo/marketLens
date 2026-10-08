"""perp 우주 — 스펙 046 §3.2.

원천 3곳(047 뒤 4곳)의 목록·펀딩 주기를 **10초**마다 병렬로 받고(실패는 직전 목록 유지·같은 원인 60초 1줄),
우주 확정은 김프 우주를 따라 **매초** — UniverseRefresher 가 자기 우주를 확정할 때마다 `apply` 를 부른다.
perp 우주 = {원천 2곳 이상} ∪ (김프 우주 ∩ {1곳 이상}). 우주 밖 행은 그 초에 지워지고 각 원천은 자기 맵에
있는 심볼만 구독한다(차이만 재조정). `/refresh` 는 이 목록도 그 자리에서 한 번 더 받는다.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

import httpx

from app.core.contracts import PerpSymbolSource
from app.core.errors import ExchangeApiError, ExchangeError
from app.core.perp import PerpSink, perp_universe
from app.core.universe import LOG_SUPPRESS_SEC, RefreshOutcome

logger = logging.getLogger("marketlens.perp_universe")

PERP_LIST_INTERVAL = 10.0  # 목록 회차 사이 쉬는 시간 — 바이낸스 fundingInfo 의 5분 500회 한도를 아끼려 1초가 아니다 (§3.2)


class PerpUniverse:
    def __init__(
        self,
        *,
        sink: PerpSink,
        sources: Sequence[PerpSymbolSource],
        client: httpx.AsyncClient,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._sources = list(sources)
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic
        # (원천, 원인) → (마지막으로 로그한 시각, 그 뒤 억누른 횟수) — 001 §3.2 와 같은 억제
        self._muted: dict[tuple[str, str], tuple[float, int]] = {}
        self._task: asyncio.Task[None] | None = None
        self.universe: set[str] = set()

    async def refresh(self) -> RefreshOutcome:
        """원천 목록을 동시에 받는다. 실패한 원천은 직전 목록 유지. 우주 확정은 다음 초의 apply 가 한다."""
        outcome = RefreshOutcome()
        results = await asyncio.gather(*(self._fetch(s) for s in self._sources))
        for source, calls, error in results:
            if error is None:
                outcome.calls[source] = calls
            else:
                outcome.failures.append(error)
        return outcome

    async def _fetch(
        self, source: PerpSymbolSource
    ) -> tuple[str, int, ExchangeError | None]:
        try:
            calls = await source.refresh(self._client)
        except Exception as exc:
            return source.id, 0, self._failed(source.id, exc)
        return source.id, calls, None

    def _failed(self, source: str, exc: Exception) -> ExchangeError:
        if isinstance(exc, ExchangeError):
            error, cause = exc, exc.kind
        else:
            error = ExchangeApiError(
                source, None, f"perp 목록 갱신 중 예상 밖 예외: {exc!r}"
            )
            cause = type(exc).__name__
        now = self._monotonic()
        last, muted = self._muted.get((source, cause), (None, 0))
        if last is not None and now - last < LOG_SUPPRESS_SEC:
            self._muted[(source, cause)] = (last, muted + 1)
            return error
        self._muted[(source, cause)] = (now, 0)
        suffix = f" (직전 60초 동안 같은 원인 {muted}회 억제)" if muted else ""
        if isinstance(exc, ExchangeError):
            logger.warning(
                "%s perp 목록 갱신 실패 — 직전 목록 유지%s: %s", source, suffix, error
            )
        else:
            logger.exception(
                "%s perp 목록 갱신이 예상 밖 예외로 끝났다 — 직전 목록 유지%s",
                source,
                suffix,
            )
        return error

    def apply(self, kimp: set[str]) -> None:
        """김프 우주가 확정될 때마다(매초) perp 우주를 다시 확정해 싱크·원천에 넘긴다 (§3.2)."""
        self.universe = perp_universe(kimp, (s.bases() for s in self._sources))
        removed = self._sink.set_universe(self.universe)
        if removed:
            logger.info("perp 우주 갱신으로 행 %d개 소멸", removed)
        for source in self._sources:
            source.set_universe(self.universe)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """10초 회차 — 목록을 받고 회차가 끝난 뒤 10초를 쉰다. 예상 밖 예외는 로그 후 다음 회차."""
        while True:
            try:
                await self.refresh()
            except Exception:
                logger.exception(
                    "perp 목록 갱신이 예상 밖 예외로 끝났다 — 다음 회차에 계속"
                )
            await self._sleep(PERP_LIST_INTERVAL)

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
