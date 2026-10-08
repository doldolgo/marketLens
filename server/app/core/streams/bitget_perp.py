"""비트겟 USDT 무기한 선물(perp) 원천 — contracts(USDT-FUTURES, 10초) 목록 + 전체 티커 REST **매초 1회** 폴링 (스펙 046 §3.7).

WebSocket 을 쓰지 않는다(§1 결정 2026-10-08 — `ticker` 채널은 심볼당 초당 약 10건, 샤드당 2,300 프레임·1.3MB 였다).
한 응답이 한 메시지다 — `data[]` 중 자기 맵에 있고 우주 안인 심볼만 PerpSink 로 넘긴다(호가 넷·마크가·펀딩률,
호가 시각 = 항목 `ts`). 응답에 다음 정산 시각이 없어 목록의 `fundInterval` 로 다음 배수 시각을 계산한다.
목록은 현물 커넥터(020)와 규칙만 같고 코드를 공유하지 않는다. 상태는 001 §3.3 모양을 폴링 뜻으로 쓴다.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.contracts import RawRecorder, noop_record
from app.core.errors import ExchangeApiError, ExchangeError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.models import StreamError
from app.core.perp import PerpSink, split_multiplier
from app.core.ticks import STALE_AFTER_MS, StreamVerdict

logger = logging.getLogger("marketlens.stream.bitget_perp")

REST_URL = "https://api.bitget.com"
CONTRACTS_PATH = "/api/v2/mix/market/contracts"
CONTRACTS_URL = REST_URL + CONTRACTS_PATH + "?productType=USDT-FUTURES"
TICKERS_PATH = "/api/v2/mix/market/tickers"
TICKERS_URL = REST_URL + TICKERS_PATH + "?productType=USDT-FUTURES"
SYMBOLS_KEY = "symbols:perp"
TICKERS_KEY = "tickers:all"
POLL_INTERVAL = 1.0  # 회차가 끝난 뒤 쉬는 시간 — IP 당 초당 20회 한도의 1/20 (§3.7)
LOG_SUPPRESS_SEC = 60.0
_REQUEST_TIME = re.compile(rb'"requestTime":\s*\d+')
_REQUEST_TIME_WITHIN = 256
_NAN = float("nan")
_HOUR_MS = 3_600_000
_QUOTE = "USDT"
_OK_CODE = "00000"
_PERPETUAL = "perpetual"
_NORMAL = "normal"


def _now_ms() -> int:
    return int(time.time() * 1000)


def next_funding_ms(now_ms: int, interval_h: int) -> int:
    """다음 정산 시각 — 비트겟 정산은 UTC 00:00 부터 주기 간격이라 주기의 다음 배수 (§3.7)."""
    period = interval_h * _HOUR_MS
    return (now_ms // period + 1) * period


class BitgetPerpStream:
    id = "bitget_perp"
    url = TICKERS_URL

    def __init__(
        self,
        *,
        store: LiveStore,
        sink: PerpSink,
        client: httpx.AsyncClient,
        record: RawRecorder = noop_record,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self._store = store
        self._state = store.stream(self.id)
        self._state.url = TICKERS_URL
        self._sink = sink
        self._client = client
        self._record = record
        self._sleep = sleep
        self._clock = clock
        self._symbol_of: dict[str, str] = {}
        self._base_of: dict[str, str] = {}
        self._mult_of: dict[str, int] = {}
        self._interval_of: dict[
            str, int | None
        ] = {}  # 심볼 → 주기(시간) — 다음 정산 계산용
        self._wanted: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._muted: dict[str, tuple[int, int]] = {}
        self.decode_failures = 0
        self._symbols_body: bytes | None = None

    # --- 심볼 집합·펀딩 주기 (PerpSymbolSource, §3.2·§3.7) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """contracts 1회 → perpetual·normal·USDT 심볼 맵·주기. requestTime 만 뺀 바이트가 같으면 파싱 생략 (§3.7)."""
        url = CONTRACTS_URL
        resp = await self._get(client, url)
        self._record(
            self.id, f"rest:{CONTRACTS_PATH}", self._clock(), resp.text, SYMBOLS_KEY
        )
        comparable = _without_request_time(resp.content)
        if resp.status_code == 200 and comparable == self._symbols_body:
            return 1
        items = self._envelope(resp, url)
        symbol_of: dict[str, str] = {}
        mult_of: dict[str, int] = {}
        interval_of: dict[str, int | None] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if (
                item.get("symbolType") != _PERPETUAL
                or item.get("symbolStatus") != _NORMAL
                or item.get("quoteCoin") != _QUOTE
            ):
                continue
            base, mult = split_multiplier(str(item["baseCoin"]).upper())
            symbol = str(item["symbol"]).upper()
            mult_of[symbol] = mult
            interval_of[symbol] = _int_or_none(item.get("fundInterval"))
            prev = symbol_of.get(base)
            if prev is None or (mult == 1 and mult_of[prev] != 1):
                symbol_of[base] = symbol
        self._symbol_of = symbol_of
        self._base_of = {symbol: base for base, symbol in symbol_of.items()}
        self._mult_of = {s: mult_of[s] for s in self._base_of}
        self._interval_of = {s: interval_of[s] for s in self._base_of}
        self._symbols_body = comparable
        for symbol, base in self._base_of.items():
            self._sink.set_interval(self.id, base, interval_of[symbol])
        return 1

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        try:
            return await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"비트겟 perp 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"비트겟 perp 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc

    def _envelope(self, resp: httpx.Response, url: str) -> list[Any]:
        """공통 봉투 검사 — 비-200·JSON 아님·`code != "00000"`·data 없음은 실패 (§3.7). data 배열을 돌려준다."""
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"비트겟 perp 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_rest_status(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"비트겟 perp JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if not isinstance(data, dict):
            raise ExchangeApiError(
                self.id, url, "비트겟 perp 응답이 객체가 아니다", body=resp.text
            )
        code = data.get("code")
        if code != _OK_CODE:
            raise ExchangeApiError(
                self.id,
                url,
                f"비트겟 perp code {code}: {data.get('msg', '')}",
                status_code=resp.status_code,
                body=resp.text,
                kind="bad_response",
            )
        items = data.get("data")
        if not isinstance(items, list):
            raise ExchangeApiError(
                self.id, url, "비트겟 perp 응답에 data 가 없다", body=resp.text
            )
        return items

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
        """perp 우주 확정 → 반영 대상 심볼 교체. 빠진 심볼의 행은 그 자리에서 지운다. 같으면 무동작 (§3.7)."""
        desired = {
            self._symbol_of[b]
            for b in (x.upper() for x in bases)
            if b in self._symbol_of
        }
        if desired == self._wanted:
            return
        for symbol in self._wanted - desired:
            self._store.remove_perp_row(self.id, self._base_of.get(symbol, symbol))
        self._wanted = desired
        self._state.subscribed = len(desired)

    # --- 판정 (§3.8) ---

    def judge(self, now_ms: int) -> StreamVerdict | None:
        state = self._state
        if state.last_message_at is None and state.last_error is None:
            return None
        if not state.connected and state.last_error is not None:
            return StreamVerdict(ok=False, error=state.last_error)
        if (
            state.last_message_at is not None
            and now_ms - state.last_message_at >= STALE_AFTER_MS
        ):
            return StreamVerdict(
                ok=False,
                error=StreamError(
                    kind="stale_stream",
                    message=f"Bitget perp 티커 정체: {STALE_AFTER_MS // 1000}초 이상 성공한 조회 없음",
                    status_code=None,
                    url=TICKERS_URL,
                ),
            )
        return StreamVerdict(ok=True)

    # --- 수명 ---

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                logger.exception(
                    "비트겟 perp 티커 회차가 예상 밖 예외로 끝났다 — 다음 초에 계속"
                )
            await self._sleep(POLL_INTERVAL)

    async def poll(self) -> None:
        """티커 1회 → 우주 안 심볼의 행 갱신. 실패는 상태에 분류로 남기고 행은 건드리지 않는다 (§3.7)."""
        url = TICKERS_URL
        try:
            resp = await self._get(self._client, url)
            at = self._clock()
            self._record(self.id, f"rest:{TICKERS_PATH}", at, resp.text, TICKERS_KEY)
            items = self._envelope(resp, url)
        except ExchangeError as exc:
            self._failed(exc)
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper()
            if symbol not in self._wanted:
                continue  # 맵에 없거나 우주 밖 — 버린다
            try:
                self._on_item(symbol, item, at)
            except (KeyError, TypeError, ValueError):
                self.decode_failures += 1
        if not self._state.connected:
            self._state.connected_since = at
        self._state.connected = True
        self._state.last_error = None
        self._state.last_message_at = at

    def _on_item(self, symbol: str, item: dict[str, Any], at: int) -> None:
        base = self._base_of[symbol]
        mult = self._mult_of[symbol]
        self._sink.quote(
            source=self.id,
            base=base,
            native_symbol=symbol,
            multiplier=mult,
            bid=_quote_field(item, "bidPr"),
            ask=_quote_field(item, "askPr"),
            bid_size=_quote_field(item, "bidSz"),
            ask_size=_quote_field(item, "askSz"),
            quote_ts=int(item["ts"]),
            received_at_ms=at,
        )
        interval = self._interval_of.get(symbol)
        self._sink.funding(
            source=self.id,
            base=base,
            multiplier=mult,
            received_at_ms=at,
            funding_rate=_float_or_none(item.get("fundingRate")),
            # 응답에 다음 정산 시각이 없다 — 주기의 다음 배수로 (§3.7)
            next_funding_ms=next_funding_ms(at, interval) if interval else None,
            mark=_float_or_none(item.get("markPrice")),
        )

    def _failed(self, exc: ExchangeError) -> None:
        self._state.connected = False
        self._state.connected_since = None
        self._state.last_error = StreamError(
            kind=exc.kind,
            message=f"Bitget perp 티커 조회 실패: {exc.message}",
            status_code=exc.status_code,
            url=TICKERS_URL,
            retry_after_sec=exc.retry_after_sec,
            body=exc.body,
        )
        now = self._clock()
        last, muted = self._muted.get(exc.kind, (None, 0))
        if last is not None and now - last < LOG_SUPPRESS_SEC * 1000:
            self._muted[exc.kind] = (last, muted + 1)
            return
        self._muted[exc.kind] = (now, 0)
        suffix = f" (직전 60초 동안 같은 원인 {muted}회 억제)" if muted else ""
        logger.warning("비트겟 perp 티커 조회 실패 — 다음 초에 다시%s: %s", suffix, exc)

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


def _quote_field(item: dict[str, Any], name: str) -> float:
    """호가 필드 — 없거나 숫자가 아니면 NaN(무효) — 지금 행의 값으로 메우지 않는다 (§3.4)."""
    value = item.get(name)
    if value is None or value == "":
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _without_request_time(content: bytes) -> bytes:
    m = _REQUEST_TIME.search(content, 0, _REQUEST_TIME_WITHIN)
    return content if m is None else content[: m.start()] + content[m.end() :]


def _classify_rest_status(status: int) -> str:
    """비트겟 규칙(§3.7): 403 차단, 429 한도초과, 5xx 장애, 그 외 4xx 요청오류."""
    if status == 403:
        return "banned"
    if status == 429:
        return "rate_limit"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"
