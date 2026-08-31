"""collect(core) 테스트 공용 도구 — 네트워크 호출 없음, 거래소는 fake 로 대체."""

import asyncio
from dataclasses import replace

import httpx
import pytest

from app.core.connectors.base import ExchangeConnector, FetchResult
from app.core.models import Row


def make_row(
    exchange: str,
    base: str,
    *,
    quote: str | None = None,
    price: float = 100.0,
    asks: list[list[float]] | None = None,
    bids: list[list[float]] | None = None,
    price_timestamp: int = 1_700_000_000_000,
) -> Row:
    if quote is None:
        quote = "USDT" if exchange == "binance" else "KRW"
    native = f"{base}USDT" if exchange == "binance" else f"KRW-{base}"
    return Row(
        exchange=exchange,
        base=base,
        quote=quote,
        native_symbol=native,
        price=price,
        asks=asks if asks is not None else [[101.0, 1.0]],
        bids=bids if bids is not None else [[99.0, 2.0]],
        price_timestamp=price_timestamp,
    )


class FakeConnector(ExchangeConnector):
    """사이클마다 미리 정한 결과(행 목록 또는 예외)를 순서대로 돌려준다. 마지막 결과는 반복된다."""

    def __init__(
        self,
        exchange_id: str,
        results: list[list[Row] | Exception],
        calls: int = 1,
        delay: float = 0.0,
    ) -> None:
        self.id = exchange_id
        self._results = list(results)
        self.calls = calls
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def fetch_rows(self, client: httpx.AsyncClient) -> FetchResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            result = (
                self._results.pop(0) if len(self._results) > 1 else self._results[0]
            )
            if isinstance(result, Exception):
                raise result
            # 저장소가 행을 변경(updated_at)하므로 사이클마다 새 객체를 준다
            return FetchResult(rows=[replace(r) for r in result], calls=self.calls)
        finally:
            self.active -= 1


@pytest.fixture
def unused_client() -> httpx.AsyncClient:
    """fake 커넥터용 — 실제로 요청이 나가면 실패한다."""

    def _fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"테스트에서 네트워크 호출 발생: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(_fail))
