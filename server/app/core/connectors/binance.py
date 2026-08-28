"""바이낸스 커넥터 (public API, 인증 없음) — 스펙 001 §3.5.

전 종목 일괄 2회 호출(ticker/price + bookTicker). 단일 심볼 조회보다 weight 가 싸다.
ticker/24hr 는 쓰지 않는다 — closeTime 은 윈도우 끝이지 체결 시각이 아니다.
"""

import asyncio
import time

import httpx

from app.core.connectors.base import ExchangeConnector, FetchResult
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.models import Row

_BASE_URL = "https://api.binance.com"
_QUOTE = "USDT"


class BinanceConnector(ExchangeConnector):
    id = "binance"

    async def fetch_rows(self, client: httpx.AsyncClient) -> FetchResult:
        results = await asyncio.gather(
            self._get_json(client, "/api/v3/ticker/price"),
            self._get_json(client, "/api/v3/ticker/bookTicker"),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                raise r
        price_list, book_list = results
        calls = 2

        # 가격 0 은 거래 없음 → 마지막 가격 없음으로 취급 (호가가 살아 있으면 mid 로 폴백)
        prices: dict[str, float] = {}
        for item in price_list:
            symbol = str(item.get("symbol", ""))
            if not symbol.endswith(_QUOTE):
                continue
            price = float(item["price"])
            if price <= 0:
                continue
            prices[symbol] = price

        now_ms = int(time.time() * 1000)  # 바이낸스는 시세 시각이 없어 수집 시각을 쓴다
        rows: list[Row] = []
        for item in book_list:
            symbol = str(item.get("symbol", ""))
            if not symbol.endswith(_QUOTE):
                continue
            base = symbol[: -len(_QUOTE)]
            if not base:
                continue
            bid = float(item["bidPrice"])
            ask = float(item["askPrice"])
            if bid <= 0 or ask <= 0:
                continue
            bid_qty = float(item.get("bidQty") or 0)
            ask_qty = float(item.get("askQty") or 0)
            price = prices.get(symbol)
            if price is None:
                price = (bid + ask) / 2
            rows.append(
                Row(
                    exchange=self.id,
                    base=base,
                    quote=_QUOTE,
                    native_symbol=symbol,
                    price=price,
                    asks=[[ask, ask_qty]],
                    bids=[[bid, bid_qty]],
                    price_timestamp=now_ms,
                )
            )
        return FetchResult(rows=rows, calls=calls)

    async def _get_json(
        self, client: httpx.AsyncClient, path: str
    ) -> list[dict[str, object]]:
        url = _BASE_URL + path
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"바이낸스 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id, url, f"바이낸스 연결 실패: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이낸스 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"바이낸스 JSON 파싱 실패: {exc}"
            ) from exc
