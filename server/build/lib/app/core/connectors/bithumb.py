"""빗썸 커넥터 (public API, 인증 없음) — 스펙 001 §3.5.

경로·응답 형태가 업비트 v1 과 같지만 코드는 공유하지 않는다.
빗썸 고유 quirk 2개를 여기서 흡수한다:
- 잔량 0 인 유령 호가가 드물게 섞인다 → size<=0 단계는 버린다.
- ticker.trade_timestamp 가 KST 벽시계를 epoch 처럼 찍어 정확히 9시간 미래로 온다.
"""

import asyncio
import time

import httpx

from app.core.connectors.base import ExchangeConnector, FetchResult
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.models import Row
from app.core.rows import resolve_price, truncate_levels

_BASE_URL = "https://api.bithumb.com"
_CHUNK_SIZE = 100  # URI 길이 414 방지 — 마켓 100개씩 나눠 동시 호출
_MARKET_CACHE_TTL = 600.0  # 마켓 목록 10분 캐시
_KST_OFFSET_MS = 32_400_000  # 9시간 — 빗썸 trade_timestamp 버그 보정량
_FUTURE_TOLERANCE_MS = (
    3_600_000  # 현재보다 1시간 이상 미래면 버그로 판정 (빗썸이 고치면 자동 통과)
)


class BithumbConnector(ExchangeConnector):
    id = "bithumb"

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

        now_ms = int(time.time() * 1000)
        rows: list[Row] = []
        for ob in orderbooks:
            market = str(ob["market"])
            units = ob.get("orderbook_units") or []
            # 잔량 0 유령 호가 제거 — 최우선 호가도 잔량>0 인 첫 단계가 된다
            asks = truncate_levels(
                [
                    [float(u["ask_price"]), float(u["ask_size"])]
                    for u in units
                    if float(u["ask_size"]) > 0
                ]
            )
            bids = truncate_levels(
                [
                    [float(u["bid_price"]), float(u["bid_size"])]
                    for u in units
                    if float(u["bid_size"]) > 0
                ]
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
                # KST 벽시계 버그: 현재보다 1시간 이상 미래면 9시간을 뺀다 (호가의 timestamp 는 정상)
                if price_ts - now_ms >= _FUTURE_TOLERANCE_MS:
                    price_ts -= _KST_OFFSET_MS
            else:
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
                f"빗썸 응답 시간 초과: {type(exc).__name__}: {exc}",
                kind="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"빗썸 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"빗썸 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify(resp.status_code),
                retry_after_sec=_retry_after(resp),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"빗썸 JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if isinstance(data, list):
            return data
        # 빗썸 quirk: 에러를 HTTP 200 + {"error":{"name","message"}} 본문으로 준다 (스펙 011 §3.2).
        # error.name 이 정수면 HTTP 상태처럼 분류하고, 아니면 응답오류. status_code 는 실제 값(200).
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            name = error.get("name")
            kind = (
                _classify(name)
                if isinstance(name, int) and not isinstance(name, bool)
                else "bad_response"
            )
            raise ExchangeApiError(
                self.id,
                url,
                f"빗썸 에러 응답: {name} {error.get('message', '')}",
                status_code=resp.status_code,
                body=resp.text,
                kind=kind,
                retry_after_sec=_retry_after(resp),
            )
        raise ExchangeApiError(
            self.id,
            url,
            "빗썸 응답이 리스트가 아니다",
            status_code=resp.status_code,
            body=resp.text,
            kind="bad_response",
        )


def _classify(status: int) -> str:
    """빗썸 규칙(스펙 011 §3.2) — 업비트와 같은 v1 규칙이지만 코드는 공유하지 않는다."""
    if status == 429:
        return "rate_limit"
    if status == 418:
        return "banned"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"  # 문서에 없는 값은 응답오류로 두고 body 를 남긴다


def _retry_after(resp: httpx.Response) -> int | None:
    """Retry-After 가 초 단위 정수일 때만 값을 남긴다 — 날짜 형식은 무시."""
    raw = resp.headers.get("Retry-After")
    return int(raw) if raw is not None and raw.isdigit() else None
