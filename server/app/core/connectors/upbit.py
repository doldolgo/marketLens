"""업비트 커넥터 (public API, 인증 없음) — 스펙 001 §3.5.

빗썸과 경로·응답 형태가 같아 보여도 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
"""

import asyncio
import time

import httpx

from app.core.connectors.base import ExchangeConnector, FetchResult
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.models import Row
from app.core.rows import resolve_price, truncate_levels

_BASE_URL = "https://api.upbit.com"
_CHUNK_SIZE = 100  # URI 길이 414 방지 — 마켓 100개씩 나눠 동시 호출
_MARKET_CACHE_TTL = 600.0  # 마켓 목록 10분 캐시


class UpbitConnector(ExchangeConnector):
    id = "upbit"

    def __init__(self) -> None:
        self._markets: list[str] | None = None
        self._markets_cached_at: float = 0.0

    async def fetch_rows(self, client: httpx.AsyncClient) -> FetchResult:
        calls = 0
        if (
            self._markets is None
            or time.monotonic() - self._markets_cached_at >= _MARKET_CACHE_TTL
        ):
            data = await self._get_json(client, "/v1/market/all")
            calls += 1
            self._markets = [
                m["market"] for m in data if str(m.get("market", "")).startswith("KRW-")
            ]
            self._markets_cached_at = time.monotonic()
        markets = self._markets

        chunks = [
            markets[i : i + _CHUNK_SIZE] for i in range(0, len(markets), _CHUNK_SIZE)
        ]
        results = await asyncio.gather(
            *[
                self._get_json(client, "/v1/orderbook", {"markets": ",".join(c)})
                for c in chunks
            ],
            *[
                self._get_json(client, "/v1/ticker", {"markets": ",".join(c)})
                for c in chunks
            ],
            return_exceptions=True,
        )
        calls += len(chunks) * 2
        for r in results:
            if isinstance(r, BaseException):
                raise r
        orderbooks = [entry for part in results[: len(chunks)] for entry in part]
        tickers = {t["market"]: t for part in results[len(chunks) :] for t in part}

        rows: list[Row] = []
        for ob in orderbooks:
            market = str(ob["market"])
            units = ob.get("orderbook_units") or []
            # 받은 순서대로 담는다 — 업비트는 같은 단계의 bid/ask 가 한 쌍이고 정렬돼 온다
            asks = truncate_levels(
                [[float(u["ask_price"]), float(u["ask_size"])] for u in units]
            )
            bids = truncate_levels(
                [[float(u["bid_price"]), float(u["bid_size"])] for u in units]
            )
            if not asks or not bids:
                continue
            ticker = tickers.get(market)
            trade_price = ticker.get("trade_price") if ticker else None
            price = resolve_price(trade_price, bids, asks)
            if price is None:
                continue
            if ticker and ticker.get("trade_timestamp"):
                price_ts = int(ticker["trade_timestamp"])
            else:
                # 체결 이력이 없으면 호가 시각으로 대신한다
                price_ts = int(ob.get("timestamp") or 0)
            rows.append(
                Row(
                    exchange=self.id,
                    base=market.split("-", 1)[1],
                    quote="KRW",
                    native_symbol=market,
                    price=price,
                    asks=asks,
                    bids=bids,
                    price_timestamp=price_ts,
                )
            )
        return FetchResult(rows=rows, calls=calls)

    async def _get_json(
        self, client: httpx.AsyncClient, path: str, params: dict[str, str] | None = None
    ) -> list[dict[str, object]]:
        url = _BASE_URL + path
        try:
            resp = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"업비트 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id, url, f"업비트 연결 실패: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"업비트 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"업비트 JSON 파싱 실패: {exc}"
            ) from exc
