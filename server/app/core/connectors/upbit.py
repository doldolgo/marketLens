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
                self.id,
                url,
                f"업비트 응답 시간 초과: {type(exc).__name__}: {exc}",
                kind="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"업비트 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"업비트 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify(resp.status_code),
                retry_after_sec=_retry_after(resp),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"업비트 JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if not isinstance(data, list):
            raise ExchangeApiError(
                self.id,
                url,
                "업비트 응답이 리스트가 아니다",
                status_code=resp.status_code,
                body=resp.text,
                kind="bad_response",
            )
        return data


def _classify(status: int) -> str:
    """업비트 규칙(스펙 011 §3.2): 429 한도초과, 418 누적 차단, 5xx 장애(점검 포함), 그 외 4xx 요청오류."""
    if status == 429:
        return "rate_limit"
    if status == 418:
        return "banned"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"  # 문서에 없는 상태(3xx 등)는 응답오류로 두고 body 를 남긴다


def _retry_after(resp: httpx.Response) -> int | None:
    """Retry-After 가 초 단위 정수일 때만 값을 남긴다 — 날짜 형식은 무시."""
    raw = resp.headers.get("Retry-After")
    return int(raw) if raw is not None and raw.isdigit() else None
