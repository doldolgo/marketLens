"""바이낸스 커넥터 (public API, 인증 없음) — 스펙 001 §3.5.

전 종목 일괄 2회 호출(ticker/price + bookTicker). 단일 심볼 조회보다 weight 가 싸다.
ticker/24hr 는 쓰지 않는다 — closeTime 은 윈도우 끝이지 체결 시각이 아니다.
다단계 호가는 REST 로는 한도에 걸려 불가능하다 — 012 의 깊이 스트림 캐시를 읽어 싣는다.
"""

import asyncio
import time

import httpx

from app.core.connectors.base import ExchangeConnector, FetchResult
from app.core.connectors.binance_depth import STALE_AFTER_MS, DepthSource
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.models import Row
from app.core.rows import NOTIONAL_CAP_USDT, truncate_levels

_BASE_URL = "https://api.binance.com"
_QUOTE = "USDT"


class BinanceConnector(ExchangeConnector):
    id = "binance"

    def __init__(self, depth: DepthSource | None = None) -> None:
        # 깊이 캐시는 주입한다 — fetch_rows 시그니처와 커넥터 공통 인터페이스는 불변이라
        # collector 는 012 를 모른다 (012 §3.4). 없으면 depth_* 가 늘 빈 목록이다.
        self._depth = depth

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
            # 최우선 호가·체결가는 REST 값 그대로 둔다. WS 북과 섞으면 교차 북이 나와
            # 003 의 fwd/rev 정의가 바뀐다 — 깊이는 별도 필드로만 싣는다 (012 §3.5).
            entry = self._depth.get(symbol, now_ms) if self._depth is not None else None
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
                    depth_asks=truncate_levels(entry.asks, NOTIONAL_CAP_USDT)
                    if entry is not None
                    else [],
                    depth_bids=truncate_levels(entry.bids, NOTIONAL_CAP_USDT)
                    if entry is not None
                    else [],
                    depth_at=entry.at if entry is not None else None,
                )
            )

        # 소켓이 조용히 멈추면 예외가 없어 /health/collect 가 초록인 채로 깊이만 얼어붙는다.
        # 정체를 수집 실패로 승격해 011 의 기존 경로를 그대로 태운다. REST 결과는 유효하지만
        # 001 규칙대로 직전 스냅샷이 유지되므로 표에서 행이 사라지지는 않는다 (012 §3.6).
        if self._depth is not None and self._depth.is_stalled(now_ms):
            raise ExchangeApiError(
                self.id,
                None,
                f"바이낸스 깊이 스트림 정체: {STALE_AFTER_MS // 1000}초 이상 무수신",
                kind="stale_stream",
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
