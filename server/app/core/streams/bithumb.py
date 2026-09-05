"""빗썸 스트림 커넥터 — WebSocket 호가·체결가 + 마켓 목록 REST (스펙 001 §3.10·§3.11).

요청·응답 형식이 업비트 v1 과 같지만 코드는 공유하지 않는다. 빗썸 고유 quirk 를 여기서 흡수한다:
- 호가 메시지의 `timestamp` 가 microseconds → ms 로 바꾼다.
- `trade_timestamp` 가 현재보다 1시간 이상 미래면 9시간(KST 벽시계 버그)을 뺀다.
- 잔량 0 유령 호가는 QuoteSink 의 공통 필터(§3.5-1)가 거른다.
- REST 는 HTTP 200 + `{"error":…}` 본문이 실패다 (011 §3.2).
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from app.core.config import WS_OPEN_TIMEOUT
from app.core.contracts import RawRecorder, noop_record
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.models import StreamError
from app.core.quotes import QuoteSink
from app.core.ticks import StreamVerdict, judge_state

logger = logging.getLogger("marketlens.stream.bithumb")

WS_URL = "wss://ws-api.bithumb.com/websocket/v1"
WS_SOURCE = "ws:/websocket/v1"
REST_URL = "https://api.bithumb.com"
MARKETS_PATH = "/v1/market/all"

PING_INTERVAL = 30.0  # 120초 idle 에 끊긴다 — 30초마다 PING 프레임 (§3.10)
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0  # 연결 요청 IP 당 초당 10회 한도 안 — 넘으면 10분 차단
CLOSE_TIMEOUT = 2.0
_IDLE_POLL = 1.0
_QUOTE = "KRW"
KST_OFFSET_MS = 32_400_000  # 9시간
FUTURE_TOLERANCE_MS = 3_600_000  # 현재보다 1시간 이상 미래면 벽시계 버그로 판정


def _now_ms() -> int:
    return int(time.time() * 1000)


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다."""
    return await ws_connect(
        url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=PING_INTERVAL
    )


class _SubscribeRejected(Exception):
    """구독 요청에 대한 에러 응답 → bad_request."""


class BithumbStream:
    id = "bithumb"
    url = WS_URL

    def __init__(
        self,
        *,
        store: LiveStore,
        sink: QuoteSink,
        record: RawRecorder = noop_record,
        connect: Callable[[str], Awaitable[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self._state = store.stream(self.id)
        self._state.url = WS_URL
        self._sink = sink
        self._record = record
        self._connect = connect
        self._sleep = sleep
        self._clock = clock
        self._markets: list[str] = []
        self._ws: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._resubscribe: asyncio.Task[None] | None = None
        self._backoff = BACKOFF_START
        self.decode_failures = 0

    # --- 마켓 목록 (§3.2) ---

    async def fetch_markets(self, client: httpx.AsyncClient) -> list[str]:
        """`GET /v1/market/all` 의 KRW- 마켓 코드(≈480). 본문은 해석 전에 원문 싱크로."""
        url = REST_URL + MARKETS_PATH
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"빗썸 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"빗썸 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        self._record(self.id, f"rest:{MARKETS_PATH}", self._clock(), resp.text)
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"빗썸 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_rest_status(resp.status_code),
                retry_after_sec=_retry_after(resp.headers.get("Retry-After")),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"빗썸 JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if isinstance(data, list):
            return [
                str(m["market"])
                for m in data
                if isinstance(m, dict)
                and str(m.get("market", "")).startswith(f"{_QUOTE}-")
            ]
        # 빗썸 quirk: 에러를 HTTP 200 + {"error":{"name","message"}} 로 준다 (011 §3.2)
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            name = error.get("name")
            kind = (
                _classify_rest_status(name)
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
                retry_after_sec=_retry_after(resp.headers.get("Retry-After")),
            )
        raise ExchangeApiError(
            self.id, url, "빗썸 마켓 목록이 리스트가 아니다", body=resp.text
        )

    def set_markets(self, codes: Iterable[str]) -> None:
        """구독 대상 전체 목록. 바뀌었고 연결 중이면 전 목록으로 재구독한다 (§3.11)."""
        markets = sorted({c.upper() for c in codes})
        if markets == self._markets:
            return
        self._markets = markets
        if self._ws is not None:
            self._resubscribe = asyncio.create_task(self._resubscribe_now(self._ws))

    async def _resubscribe_now(self, ws: Any) -> None:
        try:
            await self._subscribe(ws)
        except Exception as exc:
            logger.warning("빗썸 재구독 전송 실패: %r", exc)

    # --- 판정 (§3.8) ---

    def judge(self, now_ms: int) -> StreamVerdict | None:
        return judge_state(self._state, now_ms, WS_URL)

    # --- 수명 ---

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def aclose(self) -> None:
        """태스크 취소 후 소켓 close, 합계 2초 상한 (§3.11)."""
        tasks = [t for t in (self._task, self._resubscribe) if t is not None]
        for task in tasks:
            task.cancel()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), CLOSE_TIMEOUT
            )
        self._task = self._resubscribe = None
        await self._close_socket()

    async def run(self) -> None:
        while True:
            if not self._markets:
                await self._sleep(_IDLE_POLL)
                continue
            try:
                ws = await self._open()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._state.last_error = _classify(exc)
                logger.warning(
                    "빗썸 스트림 연결 실패 — %.0f초 뒤 재시도: %s",
                    self._backoff,
                    self._state.last_error.message,
                )
                await self._sleep(self._backoff)
                self._grow_backoff()
                continue
            self._ws = ws
            self._state.connected = True
            self._state.connected_since = self._clock()
            try:
                await self._subscribe(ws)
                await self._pump(ws)
            except asyncio.CancelledError:
                raise
            except _SubscribeRejected as exc:
                self._state.last_error = StreamError(
                    kind="bad_request",
                    message=f"빗썸 구독 거부: {exc}",
                    status_code=None,
                    url=WS_URL,
                )
            except Exception as exc:
                self._state.last_error = _classify(exc)
            finally:
                self._state.connected = False
                self._state.connected_since = None
                self._state.subscribed = 0
                await self._close_socket()
            logger.warning(
                "빗썸 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
                self._backoff,
                self._state.last_error.message if self._state.last_error else "",
            )
            await self._sleep(self._backoff)
            self._grow_backoff()

    async def _open(self) -> Any:
        factory = self._connect if self._connect is not None else open_socket
        return await factory(WS_URL)

    async def _close_socket(self) -> None:
        ws, self._ws = self._ws, None
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.close()

    def _grow_backoff(self) -> None:
        self._backoff = min(self._backoff * 2, BACKOFF_MAX)

    async def _subscribe(self, ws: Any) -> None:
        """연결 직후 1회(재구독 시 전체 교체). 호가는 응답 자체가 최대 15단계 (§3.10)."""
        codes = list(self._markets)
        await ws.send(
            json.dumps(
                [
                    {"ticket": str(uuid.uuid4())},
                    {"type": "orderbook", "codes": codes},
                    {"type": "ticker", "codes": codes},
                    {"format": "DEFAULT"},
                ]
            )
        )
        self._state.subscribed = len(codes)

    async def _pump(self, ws: Any) -> None:
        while True:
            raw = await ws.recv()
            at = self._clock()
            if isinstance(raw, bytes | bytearray):
                try:
                    text = bytes(raw).decode("utf-8")
                except UnicodeDecodeError:
                    self.decode_failures += 1
                    continue
            else:
                text = str(raw)
            self._record(self.id, WS_SOURCE, at, text)  # 해석보다 먼저 (§3.7)
            try:
                msg = json.loads(text)
            except ValueError:
                self.decode_failures += 1
                continue
            if not isinstance(msg, dict):
                self.decode_failures += 1
                continue
            error = msg.get("error")
            if isinstance(error, dict):
                raise _SubscribeRejected(
                    f"{error.get('name')} {error.get('message', '')}"
                )
            kind = msg.get("type")
            try:
                if kind == "orderbook":
                    self._on_orderbook(msg, at)
                elif kind == "ticker":
                    self._on_ticker(msg, at)
                else:
                    continue  # {"status":"UP"} 등은 시세 수신으로 세지 않는다
            except (KeyError, TypeError, ValueError):
                self.decode_failures += 1
                continue
            self._state.last_message_at = at
            self._backoff = BACKOFF_START

    def _on_orderbook(self, msg: dict[str, Any], at: int) -> None:
        code = str(msg["code"])
        quote, base = code.split("-", 1)
        units = msg["orderbook_units"]
        self._sink.orderbook(
            exchange=self.id,
            base=base,
            quote=quote,
            native_symbol=code,
            asks=[[float(u["ask_price"]), float(u["ask_size"])] for u in units],
            bids=[[float(u["bid_price"]), float(u["bid_size"])] for u in units],
            timestamp_ms=int(msg["timestamp"]) // 1000,  # 빗썸 호가 timestamp 는 µs
            received_at_ms=at,
        )

    def _on_ticker(self, msg: dict[str, Any], at: int) -> None:
        code = str(msg["code"])
        base = code.split("-", 1)[1]
        trade_ts = int(msg["trade_timestamp"])
        if trade_ts - at >= FUTURE_TOLERANCE_MS:
            trade_ts -= KST_OFFSET_MS  # KST 벽시계 버그 방어 — 빗썸이 고치면 자동 통과
        self._sink.trade(
            exchange=self.id,
            base=base,
            price=float(msg["trade_price"]),
            price_timestamp=trade_ts,
        )


def _classify(exc: BaseException) -> StreamError:
    """연결·핸드셰이크·끊김 분류 (§3.8). 빗썸 429 는 초당 10회 초과, 지속 시 10분 차단."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = f"빗썸 WebSocket 실패: {type(exc).__name__}: {exc}"
    if isinstance(status, int):
        if status == 429:
            kind = "rate_limit"
        elif status in (403, 418):
            kind = "banned"
        elif 500 <= status < 600:
            kind = "unavailable"
        else:
            kind = "bad_response"
        headers = getattr(response, "headers", None)
        retry = headers.get("Retry-After") if headers is not None else None
        return StreamError(kind, message, status, WS_URL, _retry_after(retry))
    if isinstance(exc, TimeoutError):
        return StreamError("timeout", message, None, WS_URL)
    if isinstance(exc, OSError | ConnectionClosed):
        return StreamError("network", message, None, WS_URL)
    return StreamError("bad_response", message, None, WS_URL)


def _classify_rest_status(status: int) -> str:
    """빗썸 REST 규칙(011 §3.2) — 업비트와 같은 v1 규칙이지만 코드는 공유하지 않는다."""
    if status == 429:
        return "rate_limit"
    if status == 418:
        return "banned"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"


def _retry_after(raw: object) -> int | None:
    """Retry-After 가 초 단위 정수일 때만 값을 남긴다 — 날짜 형식은 무시."""
    return int(raw) if isinstance(raw, str) and raw.isdigit() else None
