"""바이낸스 USDT 무기한 선물(perp) 원천 — exchangeInfo(10초)·fundingInfo(60초) 목록 + 전체 bookTicker·premiumIndex REST
**매초 1회** 폴링 (스펙 046 §3.5).

WebSocket 을 쓰지 않는다(§1 결정 2026-10-09 — 1단계 호가만 쓰는데 depth5@500ms 3샤드가 초당 약 750 프레임이라
1 vCPU 수집기 CPU 를 먹었다). 한 회차 = 두 요청을 **병렬**로(bookTicker 가중치 5 + premiumIndex 10 = 분당 900,
IP 한도 2,400 의 37.5%) — 둘 다 성공해야 회차 성공이고, 하나라도 실패하면 그 회차는 행을 건드리지 않는다(펀딩만
낡은 채 갭 표에 실리지 않게). bookTicker 의 `X-MBX-USED-WEIGHT-1M` 헤더는 문서가 "정확하지 않으니 무시" 라 하므로
한도는 주기로만 지킨다 — 429·418 뒤엔 다음 회차까지 기다린다. 자기 맵에 있고 우주 안인 심볼만 PerpSink 로 넘긴다
(호가 넷·호가 시각 = 항목 `time`, 마크가·펀딩률·다음 정산). 목록은 현물 커넥터(012)와 규칙만 같고 코드를
공유하지 않는다. 상태는 001 §3.3 모양을 폴링 뜻으로 쓴다 — connected = 마지막 회차 성공.
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

logger = logging.getLogger("marketlens.stream.binance_perp")

REST_URL = "https://fapi.binance.com"
EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
EXCHANGE_INFO_URL = REST_URL + EXCHANGE_INFO_PATH
FUNDING_INFO_PATH = "/fapi/v1/fundingInfo"
FUNDING_INFO_URL = REST_URL + FUNDING_INFO_PATH
BOOK_TICKER_PATH = "/fapi/v1/ticker/bookTicker"
BOOK_TICKER_URL = REST_URL + BOOK_TICKER_PATH  # symbol 생략 = 전체 (가중치 5)
PREMIUM_INDEX_PATH = "/fapi/v1/premiumIndex"
PREMIUM_INDEX_URL = REST_URL + PREMIUM_INDEX_PATH  # symbol 생략 = 전체 (가중치 10)
SYMBOLS_KEY = (
    "symbols:perp"  # 10초마다 오는 exchangeInfo 본문의 원문 싱크 key — 분당 마지막 1건
)
FUNDING_KEY = "funding:all"  # 60초마다 오는 fundingInfo 본문의 key
BOOK_TICKER_KEY = (
    "bookTicker:all"  # 매초 오는 전체 최우선 호가 본문의 key — 분당 마지막 1건
)
PREMIUM_INDEX_KEY = (
    "premiumIndex:all"  # 매초 오는 전체 마크가·펀딩 본문의 key — 분당 마지막 1건
)
POLL_INTERVAL = 1.0  # 회차가 끝난 뒤 쉬는 시간 — 초당 1회를 넘지 않는다 (§3.5)
LOG_SUPPRESS_SEC = 60.0  # 같은 원인의 회차 실패 로그 간격 (§3.5)
FUNDING_INFO_INTERVAL_MS = (
    60_000  # fundingInfo 는 /fundingRate 와 5분 500회를 나눠 쓴다 — 60초마다 (§3.5)
)
DEFAULT_INTERVAL_H = (
    8  # fundingInfo 응답에 없는 심볼의 주기 — 문서에 기본값 명문이 없어 가정 (§3.5)
)
# exchangeInfo 에서 매 응답 바뀌는 것은 머리의 serverTime 하나다 — 이것만 빼고 직전 본문과 비교한다 (§3.5)
_SERVER_TIME = re.compile(rb'"serverTime":\s*\d+')
_SERVER_TIME_WITHIN = 256
_NAN = float("nan")
_CONTRACT = "PERPETUAL"
_TRADING = "TRADING"
_QUOTE = "USDT"


def _now_ms() -> int:
    return int(time.time() * 1000)


class BinancePerpStream:
    id = "binance_perp"
    url = BOOK_TICKER_URL

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
        self._state.url = BOOK_TICKER_URL
        self._sink = sink
        self._client = (
            client  # 매초 티커 폴링이 쓰는 앱 공용 클라이언트 (타임아웃 3초 — 001 §3.1)
        )
        self._record = record
        self._sleep = sleep
        self._clock = clock
        self._symbol_of: dict[str, str] = {}  # base → 심볼 (exchangeInfo symbol)
        self._base_of: dict[str, str] = {}  # 심볼 → base
        self._mult_of: dict[str, int] = {}  # 심볼 → 배수
        self._interval_of: dict[str, int] | None = (
            None  # 심볼 → 주기(시간) — fundingInfo 를 한 번도 못 받았으면 None
        )
        self._funding_fetched_at: int | None = None  # 마지막 fundingInfo 성공 시각(ms)
        self._wanted: set[str] = set()  # 우주 안 심볼(반영 대상)
        self._task: asyncio.Task[None] | None = None
        # (원인) → (마지막으로 로그한 시각 ms, 그 뒤 억누른 횟수)
        self._muted: dict[str, tuple[int, int]] = {}
        self.decode_failures = 0  # 버린 무효 항목 수 — 그 자체로 실패가 아니다
        # 직전에 맵까지 만든 200 응답의 비교용 바이트(serverTime 제외) — 같으면 파싱·맵 재생성을 건너뛴다 (§3.2)
        self._symbols_body: bytes | None = None

    # --- 심볼 집합·펀딩 주기 (PerpSymbolSource, §3.2·§3.5) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """exchangeInfo 1회(+ 60초가 지났으면 fundingInfo 1회) → PERPETUAL·TRADING·USDT 심볼 맵·주기. 나간 호출 수를 돌려준다."""
        calls = await self._refresh_symbols(client)
        now = self._clock()
        if (
            self._funding_fetched_at is None
            or now - self._funding_fetched_at >= FUNDING_INFO_INTERVAL_MS
        ):
            calls += await self._refresh_funding(client)
        return calls

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        try:
            return await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id,
                url,
                f"바이낸스 perp 응답 시간 초과: {type(exc).__name__}: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이낸스 perp 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc

    def _parse(self, resp: httpx.Response, url: str) -> Any:
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이낸스 perp 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_rest_status(resp.status_code),
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이낸스 perp JSON 파싱 실패: {exc}",
                kind="bad_response",
            ) from exc

    async def _refresh_symbols(self, client: httpx.AsyncClient) -> int:
        """exchangeInfo → 심볼 맵. 본문은 해석 전에 원문 싱크로(`symbols:perp`). serverTime 만 뺀 바이트가 같으면 파싱 생략."""
        url = EXCHANGE_INFO_URL
        resp = await self._get(client, url)
        self._record(
            self.id, f"rest:{EXCHANGE_INFO_PATH}", self._clock(), resp.text, SYMBOLS_KEY
        )
        comparable = _without_server_time(resp.content)
        if resp.status_code == 200 and comparable == self._symbols_body:
            return 1
        data = self._parse(resp, url)
        items = data.get("symbols") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ExchangeApiError(
                self.id,
                url,
                "바이낸스 perp exchangeInfo 에 symbols 가 없다",
                body=resp.text,
            )
        symbol_of: dict[str, str] = {}
        mult_of: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if (
                item.get("contractType") != _CONTRACT
                or item.get("status") != _TRADING
                or item.get("quoteAsset") != _QUOTE
            ):
                continue
            base, mult = split_multiplier(str(item["baseAsset"]).upper())
            symbol = str(item["symbol"]).upper()
            mult_of[symbol] = mult
            # 같은 base 로 정규화되는 심볼이 둘이면 multiplier 1 을, 없으면 목록 순서의 첫 것 (§3.2)
            prev = symbol_of.get(base)
            if prev is None or (mult == 1 and mult_of[prev] != 1):
                symbol_of[base] = symbol
        self._symbol_of = symbol_of
        self._base_of = {symbol: base for base, symbol in symbol_of.items()}
        self._mult_of = {s: mult_of[s] for s in self._base_of}
        self._symbols_body = comparable
        self._apply_intervals()
        return 1

    async def _refresh_funding(self, client: httpx.AsyncClient) -> int:
        """fundingInfo → 심볼 → 주기(시간). 응답에 없는 심볼은 8 (§3.5). 원문 key `funding:all`."""
        url = FUNDING_INFO_URL
        resp = await self._get(client, url)
        self._record(
            self.id, f"rest:{FUNDING_INFO_PATH}", self._clock(), resp.text, FUNDING_KEY
        )
        data = self._parse(resp, url)
        if not isinstance(data, list):
            raise ExchangeApiError(
                self.id,
                url,
                "바이낸스 perp fundingInfo 가 배열이 아니다",
                body=resp.text,
            )
        interval_of: dict[str, int] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                interval_of[str(item["symbol"]).upper()] = int(
                    item["fundingIntervalHours"]
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._interval_of = interval_of
        self._funding_fetched_at = self._clock()
        self._apply_intervals()
        return 1

    def _apply_intervals(self) -> None:
        """목록·주기 갱신마다 이 원천의 전 심볼에 주기를 반영한다 — 행이 있으면 바로, 없으면 생길 때 (§3.4)."""
        if self._interval_of is None:
            return
        for symbol, base in self._base_of.items():
            self._sink.set_interval(
                self.id, base, self._interval_of.get(symbol, DEFAULT_INTERVAL_H)
            )

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
        """perp 우주 확정 → 반영 대상 심볼 교체. 빠진 심볼의 행은 그 자리에서 지운다. 같으면 무동작 (§3.5)."""
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
                    message=f"Binance perp 티커 정체: {STALE_AFTER_MS // 1000}초 이상 성공한 조회 없음",
                    status_code=None,
                    url=BOOK_TICKER_URL,
                ),
            )
        return StreamVerdict(ok=True)

    # --- 수명 ---

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """매초 회차 — 두 요청 1회씩, 회차가 끝난 뒤 1초를 쉰다. 예상 밖 예외도 회차 실패로 남기고 멈추지 않는다."""
        while True:
            try:
                await self.poll()
            except Exception:
                logger.exception(
                    "바이낸스 perp 티커 회차가 예상 밖 예외로 끝났다 — 다음 초에 계속"
                )
            await self._sleep(POLL_INTERVAL)

    async def poll(self) -> None:
        """bookTicker·premiumIndex 를 병렬로 1회씩 → 우주 안 심볼의 행 갱신. 하나라도 실패면 회차 실패 — 행은 건드리지 않는다 (§3.5).

        두 요청을 끝까지 받고 나서 판단한다(`return_exceptions`) — 한쪽이 먼저 실패해도 다른 쪽 요청이 다음 회차까지
        떠다니지 않게. 펀딩이 호가보다 먼저 와도 행이 없으면 싱크가 보류하므로 호가를 먼저 싣는다.
        """
        results = await asyncio.gather(
            self._fetch(BOOK_TICKER_URL, BOOK_TICKER_PATH, BOOK_TICKER_KEY),
            self._fetch(PREMIUM_INDEX_URL, PREMIUM_INDEX_PATH, PREMIUM_INDEX_KEY),
            return_exceptions=True,
        )
        for outcome in results:
            if isinstance(outcome, ExchangeError):
                self._failed(outcome)
                return
        for outcome in results:
            if isinstance(outcome, BaseException):
                raise outcome  # 거래소 실패가 아닌 예외 — run 이 로그로 남긴다
        books, premiums = results
        at = self._clock()
        for item in books:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper()
            if symbol not in self._wanted:
                continue  # 맵에 없거나 우주 밖 — 버린다
            try:
                self._on_book(symbol, item, at)
            except (KeyError, TypeError, ValueError):
                self.decode_failures += 1
        for item in premiums:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper()
            if symbol not in self._wanted:
                continue  # USDC·기간물도 섞여 온다 — 맵에 없으니 여기서 빠진다
            try:
                self._on_premium(symbol, item, at)
            except (KeyError, TypeError, ValueError):
                self.decode_failures += 1
        if not self._state.connected:
            self._state.connected_since = at
        self._state.connected = True
        self._state.last_error = None
        self._state.last_message_at = at

    async def _fetch(self, url: str, path: str, key: str) -> list[Any]:
        """전체 응답 1회 — 원문은 해석 전에 싱크로(실패 본문도). 배열이 아니면 실패."""
        resp = await self._get(self._client, url)
        self._record(self.id, f"rest:{path}", self._clock(), resp.text, key)
        data = self._parse(resp, url)
        if not isinstance(data, list):
            raise ExchangeApiError(
                self.id, url, f"바이낸스 perp {path} 가 배열이 아니다", body=resp.text
            )
        return data

    def _on_book(self, symbol: str, item: dict[str, Any], at: int) -> None:
        self._sink.quote(
            source=self.id,
            base=self._base_of[symbol],
            native_symbol=symbol,
            multiplier=self._mult_of[symbol],
            bid=_quote_field(item, "bidPrice"),
            ask=_quote_field(item, "askPrice"),
            bid_size=_quote_field(item, "bidQty"),
            ask_size=_quote_field(item, "askQty"),
            quote_ts=int(item["time"]),
            received_at_ms=at,
        )

    def _on_premium(self, symbol: str, item: dict[str, Any], at: int) -> None:
        self._sink.funding(
            source=self.id,
            base=self._base_of[symbol],
            multiplier=self._mult_of[symbol],
            received_at_ms=at,
            funding_rate=_float_or_none(item.get("lastFundingRate")),
            next_funding_ms=_int_or_none(item.get("nextFundingTime")),
            mark=_float_or_none(item.get("markPrice")),
        )

    def _failed(self, exc: ExchangeError) -> None:
        """회차 실패 → 상태에 분류(url 은 실패한 쪽 엔드포인트), 같은 원인 60초에 로그 1줄 (§3.5)."""
        self._state.connected = False
        self._state.connected_since = None
        self._state.last_error = StreamError(
            kind=exc.kind,
            message=f"Binance perp 티커 조회 실패: {exc.message}",
            status_code=exc.status_code,
            url=exc.url,
            retry_after_sec=exc.retry_after_sec,
            body=exc.body,
        )
        now = self._clock()
        last, muted = self._muted.get(exc.kind, (None, 0))
        if last is not None and now - last < LOG_SUPPRESS_SEC * 1000:
            self._muted[exc.kind] = (last, muted + 1)
            return
        self._muted[exc.kind] = (now, 0)
        suffix = ""
        if muted:
            suffix = f" (직전 60초 동안 같은 원인 {muted}회 억제)"
        logger.warning(
            "바이낸스 perp 티커 조회 실패 — 다음 초에 다시%s: %s", suffix, exc
        )

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
    """숫자가 아니면 None — 그 필드는 두고 나머지만 갱신한다 (§3.4)."""
    if value is None or value == "":
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out:
        return None  # NaN 도 "숫자 아님"
    return out


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _without_server_time(content: bytes) -> bytes:
    """비교용 바이트 — 머리의 `"serverTime":<ms>` 하나만 뺀 것. 없으면 본문 그대로."""
    m = _SERVER_TIME.search(content, 0, _SERVER_TIME_WITHIN)
    return content if m is None else content[: m.start()] + content[m.end() :]


def _classify_rest_status(status: int) -> str:
    """바이낸스 규칙(§3.5): 429 한도초과, 418 IP 차단, 403 WAF 차단, 5xx 장애, 그 외 4xx 요청오류."""
    if status == 429:
        return "rate_limit"
    if status in (418, 403):
        return "banned"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"
