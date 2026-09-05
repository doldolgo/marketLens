"""마켓 우주 — 스펙 001 §3.2.

우주 = (업비트 KRW base ∪ 빗썸 KRW base) ∩ 바이낸스 USDT base. 10분마다 갱신하고
`/refresh` 트리거가 즉시 갱신한다. 국내 스트림은 자기 KRW 전 마켓을 구독하고(KRW-USDT 포함),
우주 밖 base 의 행은 저장되지 않는다. 바이낸스 심볼 집합은 012 가 제공한다(ForeignSymbolSource).
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.contracts import ForeignSymbolSource
from app.core.errors import ExchangeError
from app.core.quotes import QuoteSink

logger = logging.getLogger("marketlens.universe")

UNIVERSE_INTERVAL = 600.0  # 10분
RETRY_INTERVAL = 5.0  # 기동 시 목록을 못 받은 거래소의 재시도 간격
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
    ) -> None:
        self._sink = sink
        self._streams = list(streams)
        self._foreign = foreign
        self._client = client
        self._sleep = sleep
        self._markets: dict[str, list[str]] = {}  # 거래소 → KRW 마켓 코드 전체
        self._foreign_fetched = False  # 바이낸스 심볼 refresh 가 한 번이라도 성공했는가
        self._task: asyncio.Task[None] | None = None
        self.universe: set[str] = set()

    async def refresh(self) -> RefreshOutcome:
        """전 거래소 목록을 다시 받아 우주·구독을 갱신한다. 실패는 직전 목록 유지 + 경고."""
        return await self._refresh([*(s.id for s in self._streams), FOREIGN])

    async def _refresh(self, exchanges: list[str]) -> RefreshOutcome:
        outcome = RefreshOutcome()
        for stream in self._streams:
            if stream.id not in exchanges:
                continue
            try:
                self._markets[stream.id] = await stream.fetch_markets(self._client)
                outcome.calls[stream.id] = 1
            except ExchangeError as exc:
                logger.warning(
                    "%s 마켓 목록 갱신 실패 — 직전 목록 유지: %s", stream.id, exc
                )
                outcome.failures.append(exc)
        if FOREIGN in exchanges:
            # 국내 거래소만 재시도하는 동안에는 바이낸스 심볼 REST 를 부르지 않는다 (§3.2)
            try:
                outcome.calls[FOREIGN] = await self._foreign.refresh(self._client)
                self._foreign_fetched = True
            except ExchangeError as exc:
                logger.warning("바이낸스 심볼 목록 갱신 실패 — 직전 목록 유지: %s", exc)
                outcome.failures.append(exc)
        self._apply()
        return outcome

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

    def missing(self) -> list[str]:
        """아직 목록을 한 번도 못 받은 거래소 — 국내 둘과 바이낸스 심볼 집합 (§3.2)."""
        out = [s.id for s in self._streams if s.id not in self._markets]
        if not self._foreign_fetched:
            out.append(FOREIGN)
        return out

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """기동 시 못 받은 거래소만 5초 간격으로 재시도, 그 뒤 10분마다 전체 갱신."""
        await self.refresh()
        while True:
            missing = self.missing()
            if missing:
                await self._sleep(RETRY_INTERVAL)
                await self._refresh(missing)
                continue
            await self._sleep(UNIVERSE_INTERVAL)
            await self.refresh()

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
