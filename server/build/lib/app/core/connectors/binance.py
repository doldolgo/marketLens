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
                self.id,
                url,
                f"바이낸스 응답 시간 초과: {type(exc).__name__}: {exc}",
                kind="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이낸스 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이낸스 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify(resp.status_code),
                retry_after_sec=_retry_after(resp),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"바이낸스 JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if not isinstance(data, list):
            raise ExchangeApiError(
                self.id,
                url,
                "바이낸스 응답이 리스트가 아니다",
                status_code=resp.status_code,
                body=resp.text,
                kind="bad_response",
            )
        return data


def _classify(status: int) -> str:
    """바이낸스 규칙(스펙 011 §3.2): 429 한도초과, 418 IP 밴, 403 WAF 차단, 5xx 장애, 그 외 4xx 요청오류."""
    if status == 429:
        return "rate_limit"
    if status in (418, 403):
        return "banned"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"  # 문서에 없는 값은 응답오류로 두고 body 를 남긴다


def _retry_after(resp: httpx.Response) -> int | None:
    """429·418 의 Retry-After(초 단위 정수)만 값으로 남긴다 — 날짜 형식은 무시."""
    raw = resp.headers.get("Retry-After")
    return int(raw) if raw is not None and raw.isdigit() else None
