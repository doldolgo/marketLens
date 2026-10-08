"""바이빗 USDT 무기한 선물(perp) 원천 — instruments-info(linear, 10초) 목록 + 전체 티커 REST **매초 1회** 폴링 (스펙 046 §3.6).

WebSocket 을 쓰지 않는다(§1 결정 2026-10-08 — `tickers.<symbol>` 100ms delta 는 샤드당 초당 약 490 프레임이라 수집기 CPU 를
두 배로 만들었다). 한 응답이 한 메시지다 — `result.list[]` 중 자기 맵에 있고 우주 안인 심볼만 PerpSink 로 넘긴다
(호가 넷·마크가·펀딩률·다음 정산·주기, 호가 시각 = 봉투 `time`). 목록·주기는 현물 커넥터(019)와 규칙만 같고
코드를 공유하지 않는다. 상태는 001 §3.3 모양을 폴링 뜻으로 쓴다 — connected = 마지막 회차 성공.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.contracts import RawRecorder, noop_record
from app.core.errors import ExchangeApiError, ExchangeError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.models import StreamError
from app.core.perp import PerpSink, split_multiplier
from app.core.ticks import STALE_AFTER_MS, StreamVerdict

logger = logging.getLogger("marketlens.stream.bybit_perp")

REST_URL = "https://api.bybit.com"
INSTRUMENTS_PATH = "/v5/market/instruments-info"
INSTRUMENTS_URL = REST_URL + INSTRUMENTS_PATH + "?category=linear&limit=1000"
TICKERS_PATH = "/v5/market/tickers"
TICKERS_URL = REST_URL + TICKERS_PATH + "?category=linear"
SYMBOLS_KEY = (
    "symbols:perp"  # 10초마다 오는 목록 본문의 원문 싱크 key — 분당 마지막 1건
)
TICKERS_KEY = "tickers:all"  # 매초 오는 전체 티커 본문의 key — 분당 마지막 1건
POLL_INTERVAL = 1.0  # 회차가 끝난 뒤 쉬는 시간 — 초당 1회를 넘지 않는다 (§3.6)
LOG_SUPPRESS_SEC = 60.0  # 같은 원인의 회차 실패 로그 간격 (§3.6)
# 봉투 꼬리의 `"time":<ms>` 하나만 매 응답 바뀐다 — 목록은 이것만 빼고 직전 본문과 비교한다 (§3.6)
_ENVELOPE_TIME = re.compile(rb'"time":\s*\d+\s*\}\s*$')
_ENVELOPE_TIME_WITHIN = 64
_NAN = float("nan")
_CONTRACT = "LinearPerpetual"
_TRADING = "Trading"
_QUOTE = "USDT"
_RATE_LIMIT_RET_CODE = 10006  # "Too many visits!" — HTTP 200 본문의 retCode (§3.6)


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _Entry:
    base: str
    symbol: str
    multiplier: int
    interval_h: int | None


@dataclass
class _Page:
    """목록 한 페이지 — 비교용 바이트·다음 커서·해석한 항목. 바이트가 같으면 파싱하지 않고 그대로 쓴다 (§3.6)."""

    comparable: bytes
    cursor: str
    entries: list[_Entry]


class BybitPerpStream:
    id = "bybit_perp"
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
        self._client = (
            client  # 매초 티커 폴링이 쓰는 앱 공용 클라이언트 (타임아웃 3초 — 001 §3.1)
        )
        self._record = record
        self._sleep = sleep
        self._clock = clock
        self._symbol_of: dict[str, str] = {}  # base → 심볼
        self._base_of: dict[str, str] = {}  # 심볼 → base
        self._mult_of: dict[str, int] = {}  # 심볼 → 배수
        self._pages: list[_Page] = []  # 직전에 맵을 만든 응답 페이지들
        self._wanted: set[str] = set()  # 우주 안 심볼(반영 대상)
        self._task: asyncio.Task[None] | None = None
        # (원인) → (마지막으로 로그한 시각 ms, 그 뒤 억누른 횟수)
        self._muted: dict[str, tuple[int, int]] = {}
        self.decode_failures = 0  # 버린 무효 항목 수 — 그 자체로 실패가 아니다

    # --- 심볼 집합·펀딩 주기 (PerpSymbolSource, §3.2·§3.6) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """instruments-info(linear) → LinearPerpetual·Trading·USDT 심볼 맵·주기. 페이지마다 호출 1회.

        HTTP 200 이어도 `retCode != 0` 이면 실패. 원문 기록은 매 페이지 하고, 꼬리 `time` 만 뺀 바이트가 직전 같은
        페이지와 같으면 그 페이지는 파싱하지 않는다 — 전 페이지가 같으면 맵도 그대로다 (§3.6).
        """
        pages: list[_Page] = []
        cursor = ""
        changed = False
        while True:
            url = INSTRUMENTS_URL + (f"&cursor={cursor}" if cursor else "")
            resp = await self._get(client, url)
            self._record(
                self.id,
                f"rest:{INSTRUMENTS_PATH}",
                self._clock(),
                resp.text,
                SYMBOLS_KEY,
            )
            comparable = _without_envelope_time(resp.content)
            index = len(pages)
            prev = self._pages[index] if index < len(self._pages) else None
            if (
                resp.status_code == 200
                and prev is not None
                and comparable == prev.comparable
            ):
                pages.append(prev)
            else:
                changed = True
                pages.append(_Page(comparable, *self._parse_page(resp, url)))
            cursor = pages[-1].cursor
            if not cursor:
                break
        if changed or len(pages) != len(self._pages):
            self._rebuild(pages)
        return len(pages)

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        try:
            return await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"바이빗 perp 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이빗 perp 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc

    def _envelope(self, resp: httpx.Response, url: str) -> dict[str, Any]:
        """공통 봉투 검사 — 비-200·JSON 아님·retCode != 0 은 실패 (§3.6)."""
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이빗 perp 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_rest_status(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"바이빗 perp JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if not isinstance(data, dict):
            raise ExchangeApiError(
                self.id, url, "바이빗 perp 응답이 객체가 아니다", body=resp.text
            )
        ret_code = data.get("retCode")
        if ret_code != 0:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이빗 perp retCode {ret_code}: {data.get('retMsg', '')}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_ret_code(ret_code),
            )
        return data

    def _parse_page(self, resp: httpx.Response, url: str) -> tuple[str, list[_Entry]]:
        data = self._envelope(resp, url)
        result = data.get("result")
        items = result.get("list") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise ExchangeApiError(
                self.id,
                url,
                "바이빗 perp instruments-info 에 result.list 가 없다",
                body=resp.text,
            )
        entries: list[_Entry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if (
                item.get("contractType") != _CONTRACT
                or item.get("status") != _TRADING
                or item.get("quoteCoin") != _QUOTE
            ):
                continue
            base, mult = split_multiplier(str(item["baseCoin"]).upper())
            entries.append(
                _Entry(
                    base,
                    str(item["symbol"]).upper(),
                    mult,
                    _interval_hours(item.get("fundingInterval")),
                )
            )
        cursor = result.get("nextPageCursor") if isinstance(result, dict) else None
        return (str(cursor) if cursor else ""), entries

    def _rebuild(self, pages: list[_Page]) -> None:
        symbol_of: dict[str, str] = {}
        mult_of: dict[str, int] = {}
        interval_of: dict[str, int | None] = {}
        for page in pages:
            for e in page.entries:
                mult_of[e.symbol] = e.multiplier
                interval_of[e.symbol] = e.interval_h
                # 같은 base 로 정규화되는 심볼이 둘이면 multiplier 1 을, 없으면 목록 순서의 첫 것 (§3.2)
                prev = symbol_of.get(e.base)
                if prev is None or (e.multiplier == 1 and mult_of[prev] != 1):
                    symbol_of[e.base] = e.symbol
        self._symbol_of = symbol_of
        self._base_of = {symbol: base for base, symbol in symbol_of.items()}
        self._mult_of = {s: mult_of[s] for s in self._base_of}
        self._pages = pages
        # 목록 갱신마다 그 원천의 전 심볼에 주기 반영 — 행이 있으면 바로, 없으면 생길 때 (§3.4)
        for symbol, base in self._base_of.items():
            self._sink.set_interval(self.id, base, interval_of[symbol])

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
        """perp 우주 확정 → 반영 대상 심볼 교체. 빠진 심볼의 행은 그 자리에서 지운다. 같으면 무동작 (§3.6)."""
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
            return None  # 첫 회차 결과가 아직 없다
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
                    message=f"Bybit perp 티커 정체: {STALE_AFTER_MS // 1000}초 이상 성공한 조회 없음",
                    status_code=None,
                    url=TICKERS_URL,
                ),
            )
        return StreamVerdict(ok=True)

    # --- 수명 ---

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """매초 회차 — 티커 1회, 회차가 끝난 뒤 1초를 쉰다. 예상 밖 예외도 회차 실패로 남기고 멈추지 않는다."""
        while True:
            try:
                await self.poll()
            except Exception:
                logger.exception(
                    "바이빗 perp 티커 회차가 예상 밖 예외로 끝났다 — 다음 초에 계속"
                )
            await self._sleep(POLL_INTERVAL)

    async def poll(self) -> None:
        """티커 1회 → 우주 안 심볼의 행 갱신. 실패는 상태에 분류로 남기고 행은 건드리지 않는다 (§3.6)."""
        url = TICKERS_URL
        try:
            resp = await self._get(self._client, url)
            at = self._clock()
            self._record(self.id, f"rest:{TICKERS_PATH}", at, resp.text, TICKERS_KEY)
            data = self._envelope(resp, url)
            result = data.get("result")
            items = result.get("list") if isinstance(result, dict) else None
            if not isinstance(items, list):
                raise ExchangeApiError(
                    self.id,
                    url,
                    "바이빗 perp tickers 에 result.list 가 없다",
                    body=resp.text,
                )
            quote_ts = _int_or_none(data.get("time"))
            if quote_ts is None:
                raise ExchangeApiError(
                    self.id, url, "바이빗 perp tickers 에 time 이 없다", body=resp.text
                )
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
                self._on_item(symbol, item, quote_ts, at)
            except (KeyError, TypeError, ValueError):
                self.decode_failures += 1
        if not self._state.connected:
            self._state.connected_since = at
        self._state.connected = True
        self._state.last_error = None
        self._state.last_message_at = at

    def _on_item(
        self, symbol: str, item: dict[str, Any], quote_ts: int, at: int
    ) -> None:
        base = self._base_of[symbol]
        mult = self._mult_of[symbol]
        self._sink.quote(
            source=self.id,
            base=base,
            native_symbol=symbol,
            multiplier=mult,
            bid=_quote_field(item, "bid1Price"),
            ask=_quote_field(item, "ask1Price"),
            bid_size=_quote_field(item, "bid1Size"),
            ask_size=_quote_field(item, "ask1Size"),
            quote_ts=quote_ts,
            received_at_ms=at,
        )
        self._sink.funding(
            source=self.id,
            base=base,
            multiplier=mult,
            received_at_ms=at,
            funding_rate=_float_or_none(item.get("fundingRate")),
            next_funding_ms=_int_or_none(item.get("nextFundingTime")),
            mark=_float_or_none(item.get("markPrice")),
            funding_interval_h=_int_or_none(item.get("fundingIntervalHour")),
        )

    def _failed(self, exc: ExchangeError) -> None:
        """회차 실패 → 상태에 분류, 같은 원인 60초에 로그 1줄 (§3.6)."""
        self._state.connected = False
        self._state.connected_since = None
        self._state.last_error = StreamError(
            kind=exc.kind,
            message=f"Bybit perp 티커 조회 실패: {exc.message}",
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
        logger.warning("바이빗 perp 티커 조회 실패 — 다음 초에 다시%s: %s", suffix, exc)

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


def _interval_hours(minutes: object) -> int | None:
    """`fundingInterval`(분) ÷ 60 → 정수 시간. 숫자가 아니면 None (§3.6)."""
    try:
        return int(minutes) // 60  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _without_envelope_time(content: bytes) -> bytes:
    m = _ENVELOPE_TIME.search(content, max(0, len(content) - _ENVELOPE_TIME_WITHIN))
    return content if m is None else content[: m.start()] + content[m.end() :]


def _classify_rest_status(status: int) -> str:
    """바이빗 규칙(§3.6): 403 차단(10분 뒤 자동 해제), 429 한도초과, 5xx 장애, 그 외 4xx 요청오류."""
    if status == 403:
        return "banned"
    if status == 429:
        return "rate_limit"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"


def _classify_ret_code(ret_code: object) -> str:
    return "rate_limit" if ret_code == _RATE_LIMIT_RET_CODE else "bad_response"
