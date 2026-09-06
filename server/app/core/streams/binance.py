"""바이낸스 스트림 커넥터 — WebSocket depth20·miniTicker 3샤드 + exchangeInfo REST (스펙 012).

국내 커넥터와 형식이 전혀 다르고 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
심볼은 crc32 % 3 으로 샤드에 고정 배정되고, 샤드마다 소켓·시계·백오프를 따로 둔다.
메시지마다: 원문 싱크 기록 → 디코드 → 행 갱신(QuoteSink) → 그 샤드의 last_message_at(시세만).
심볼 집합 계약(ForeignSymbolSource)도 이 커넥터가 구현한다 — 우주가 확정되면 차이만 재조정한다.
"""

import asyncio
import contextlib
import json
import logging
import time
import zlib
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from app.core.config import WS_OPEN_TIMEOUT
from app.core.contracts import RawRecorder, noop_record
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.models import StreamError, StreamState
from app.core.quotes import QuoteSink
from app.core.ticks import STALE_AFTER_MS, StreamVerdict

logger = logging.getLogger("marketlens.stream.binance")

WS_URL = "wss://data-stream.binance.vision/stream"
WS_SOURCE = "ws:/stream"
QUOTE_KINDS = (
    "depth20",
    "miniTicker",
)  # 원문 싱크 key 를 붙이는 시세 프레임 종류 (§3.1)
HANDSHAKE_SOURCE = (
    "ws-handshake:/stream"  # 핸드셰이크 거부 응답 본문의 원문 싱크 source (010 §3.1)
)
REST_URL = "https://api.binance.com"
EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"
# 응답을 줄이는 질의 — 커넥터가 어차피 거르는 조건이라 심볼 집합은 같다(TRADING·USDT 487개 동일).
# 본문 17.5MB → 2.5MB, 매초 파싱+원문 기록이 EC2 코어에서 505ms → 88ms 다 (§3.3).
EXCHANGE_INFO_QUERY = "?showPermissionSets=false&symbolStatus=TRADING"
EXCHANGE_INFO_URL = REST_URL + EXCHANGE_INFO_PATH + EXCHANGE_INFO_QUERY
SYMBOLS_KEY = "symbols:all"  # 매초 오는 exchangeInfo 본문의 원문 싱크 key — 분당 마지막 1건 (001 §3.7)
_BODY_LIMIT = 500  # 핸드셰이크 거부 응답 본문 상한 — 001 §3.1 과 같은 500자

SHARDS = 3
PARAMS_PER_MESSAGE = 100  # SUBSCRIBE 한 메시지의 params 상한 (§3.3)
CONTROL_INTERVAL = 0.25  # 제어 메시지 간격 — 초당 4개 이하 (§3.3)
REBALANCE_INTERVAL = 60.0  # set_universe 가 깨우지 않아도 이 주기로 구독 차이를 맞춘다
PING_INTERVAL = 20.0  # 라이브러리 keepalive — 죽은 TCP 를 40초 안에 감지 (§3.3)
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
CLOSE_TIMEOUT = 2.0  # 종료 시 취소 + 소켓 3개 동시 close 합계 상한 (§3.6)
_QUOTE = "USDT"
_SHUTDOWN_STREAM = "!serverShutdown"


def _now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(symbol: str) -> int:
    """심볼 → 샤드 번호. crc32 라 프로세스·재기동과 무관하게 같다 (§3.3)."""
    return zlib.crc32(symbol.upper().encode()) % SHARDS


def _stream_names(symbol: str) -> list[str]:
    s = symbol.lower()
    return [f"{s}@depth20", f"{s}@miniTicker"]


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다."""
    return await ws_connect(
        url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=PING_INTERVAL
    )


class _SubscribeRejected(Exception):
    """구독 요청에 대한 에러 응답(`error` 키) → bad_request (§3.3)."""


class _ServerShutdown(Exception):
    """`!serverShutdown` 수신 — 백오프 없이 즉시 재연결 (§3.2)."""


class _Shard:
    """소켓 하나 = 샤드 하나. 시계·백오프·구독 집합을 샤드마다 따로 둔다 (§3.5)."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.state = StreamState(url=WS_URL)
        self.assigned: set[str] = set()  # 이 샤드가 구독해야 할 심볼(대문자)
        self.has_work = (
            asyncio.Event()
        )  # 배정이 생기면 set — 배정 없는 샤드는 연결하지 않는다
        self.subscribed: set[str] = (
            set()
        )  # 이 소켓에 실제로 구독된 심볼 — 소켓이 바뀌면 비운다
        self.ws: Any | None = None
        self.task: asyncio.Task[None] | None = None
        self.backoff = BACKOFF_START
        self.next_id = 0
        self.lock = asyncio.Lock()  # 연결 직후 구독과 재조정 전송이 겹치지 않게


class BinanceStream:
    id = "binance"
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
        self._store = store
        self._state = store.stream(self.id)
        self._state.url = WS_URL
        self._sink = sink
        self._record = record
        self._connect = connect
        self._sleep = sleep
        self._clock = clock
        self._shards = [_Shard(i) for i in range(SHARDS)]
        self._symbol_of: dict[str, str] = {}  # base → 심볼 (exchangeInfo baseAsset)
        self._base_of: dict[str, str] = {}  # 심볼 → base
        self._wake = asyncio.Event()  # set_universe 가 재조정 루프를 깨운다
        self._rebalance: asyncio.Task[None] | None = None
        self.decode_failures = 0  # 버린 무효 프레임 수 — 그 자체로 실패가 아니다

    # --- 심볼 집합 (ForeignSymbolSource, §3.3) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """exchangeInfo 1회 → TRADING·USDT 심볼 맵. 응답 본문은 해석 전에 원문 싱크로(`symbols:all`)."""
        url = EXCHANGE_INFO_URL
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"바이낸스 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이낸스 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        self._record(
            self.id, f"rest:{EXCHANGE_INFO_PATH}", self._clock(), resp.text, SYMBOLS_KEY
        )
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이낸스 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_rest_status(resp.status_code),
                retry_after_sec=_retry_after(resp.headers.get("Retry-After")),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"바이낸스 JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        symbols = data.get("symbols") if isinstance(data, dict) else None
        if not isinstance(symbols, list):
            raise ExchangeApiError(
                self.id,
                url,
                "바이낸스 exchangeInfo 에 symbols 목록이 없다",
                body=resp.text,
            )
        symbol_of: dict[str, str] = {}
        for item in symbols:
            if not isinstance(item, dict):
                continue
            if item.get("status") != "TRADING" or item.get("quoteAsset") != _QUOTE:
                continue
            base = str(item["baseAsset"]).upper()
            symbol_of.setdefault(
                base, str(item["symbol"]).upper()
            )  # 둘 이상이면 처음 것
        self._symbol_of = symbol_of
        self._base_of = {symbol: base for base, symbol in symbol_of.items()}
        return 1

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
        """우주 확정 → 샤드별 배정 교체. 빠진 심볼의 행은 그 자리에서 지우고 재조정을 깨운다.

        매초 불리므로 배정이 하나도 안 바뀌면 아무것도 하지 않는다 — 재조정을 깨우지 않는다 (§3.3).
        """
        desired = {
            self._symbol_of[b]
            for b in (x.upper() for x in bases)
            if b in self._symbol_of
        }
        changed = False
        for shard in self._shards:
            mine = {s for s in desired if shard_of(s) == shard.index}
            if mine == shard.assigned:
                continue
            changed = True
            for symbol in shard.assigned - mine:
                self._store.remove_row(self.id, self._base_of.get(symbol, symbol))
            shard.assigned = mine
            if mine:
                shard.has_work.set()
            else:
                shard.has_work.clear()
        if changed:
            self._wake.set()

    # --- 판정 (§3.5) ---

    def judge(self, now_ms: int) -> StreamVerdict | None:
        targets = [s for s in self._shards if s.assigned]
        if not targets:
            return None
        verdicts = [(s, self._judge_shard(s, now_ms)) for s in targets]
        bad = [(s, v) for s, v in verdicts if v is not None and not v.ok]
        if bad:
            # 가장 오래 조용한 샤드, 동률이면 작은 번호
            shard, verdict = max(
                bad, key=lambda sv: (self._quiet_ms(sv[0], now_ms), -sv[0].index)
            )
            self._state.last_error = verdict.error
            return verdict
        if all(v is None for _, v in verdicts):
            return None
        self._state.last_error = None
        return StreamVerdict(ok=True)

    def _judge_shard(self, shard: _Shard, now_ms: int) -> StreamVerdict | None:
        state = shard.state
        if not state.connected:
            if state.last_error is not None:
                return StreamVerdict(ok=False, error=state.last_error)
            if state.last_message_at is None:
                return None  # 첫 연결 시도의 결과가 아직 없다
            return StreamVerdict(
                ok=False,
                error=StreamError(
                    kind="network",
                    message=f"바이낸스 스트림 끊김: 샤드 {shard.index}",
                    status_code=None,
                    url=WS_URL,
                ),
            )
        if self._quiet_ms(shard, now_ms) >= STALE_AFTER_MS:
            return StreamVerdict(
                ok=False,
                error=StreamError(
                    kind="stale_stream",
                    message=(
                        f"바이낸스 스트림 정체: 샤드 {shard.index} "
                        f"(구독 {len(shard.assigned)}종목) {STALE_AFTER_MS // 1000}초 이상 무수신"
                    ),
                    status_code=None,
                    url=WS_URL,
                ),
            )
        return StreamVerdict(ok=True)

    @staticmethod
    def _quiet_ms(shard: _Shard, now_ms: int) -> float:
        """조용한 시간 — 마지막 시세와 (연결 중이면) 구독 시각 중 최신부터. 둘 다 없으면 무한."""
        state = shard.state
        marks = [state.last_message_at]
        if state.connected:
            marks.append(state.connected_since)
        known = [m for m in marks if m is not None]
        return now_ms - max(known) if known else float("inf")

    def _publish(self) -> None:
        """샤드 상태를 store.stream("binance") 하나로 집계한다 (§3.5). last_error 는 judge 가 정한다."""
        active = [s for s in self._shards if s.assigned]
        self._state.connected = bool(active) and all(s.state.connected for s in active)
        received = [
            s.state.last_message_at for s in self._shards if s.state.last_message_at
        ]
        self._state.last_message_at = max(received) if received else None
        since = [
            s.state.connected_since for s in self._shards if s.state.connected_since
        ]
        self._state.connected_since = min(since) if since else None
        self._state.subscribed = sum(s.state.subscribed for s in self._shards)

    # --- 수명 ---

    def start(self) -> None:
        for shard in self._shards:
            shard.task = asyncio.create_task(self._run_shard(shard))
        self._rebalance = asyncio.create_task(self._run_rebalance())

    async def aclose(self) -> None:
        """태스크 전부 취소 → 소켓 3개 동시 close, 합계 2초 상한 (§3.6)."""
        deadline = time.monotonic() + CLOSE_TIMEOUT
        tasks = [s.task for s in self._shards if s.task is not None]
        if self._rebalance is not None:
            tasks.append(self._rebalance)
        for task in tasks:
            task.cancel()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), CLOSE_TIMEOUT
            )
        for shard in self._shards:
            shard.task = None
        self._rebalance = None
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*(self._close_socket(s) for s in self._shards)),
                max(0.0, deadline - time.monotonic()),
            )

    def _record_rejection(self, exc: BaseException) -> None:
        """바이낸스가 핸드셰이크를 거부하며 준 HTTP 본문도 원문이다 — 해석 전에 싱크로 (001 §3.7)."""
        text = _response_text(getattr(exc, "response", None))
        if text:
            self._record(self.id, HANDSHAKE_SOURCE, self._clock(), text)

    async def _run_shard(self, shard: _Shard) -> None:
        while True:
            if not shard.assigned:
                await shard.has_work.wait()  # 배정이 없으면 연결하지 않는다 (§3.3)
                continue
            try:
                ws = await self._open()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                shard.state.last_error = _classify(exc, shard.index)
                self._record_rejection(exc)
                self._publish()
                logger.warning(
                    "바이낸스 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %s",
                    shard.index,
                    shard.backoff,
                    shard.state.last_error.message,
                )
                await self._sleep(shard.backoff)
                shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)
                continue
            shard.ws = ws
            shard.subscribed = set()
            shard.state.connected = True
            # 구독을 보내는 동안은 소켓이 열린 시각을 임시로 — 직전 연결의 수신 시각으로 정체가 되지 않게 (§3.5)
            shard.state.connected_since = self._clock()
            self._publish()
            immediate = False
            try:
                await self._sync_shard(shard)
                shard.state.connected_since = (
                    self._clock()
                )  # 구독 시각 = 첫 묶음을 다 보낸 시각
                self._publish()
                await self._pump(shard, ws)
            except asyncio.CancelledError:
                raise
            except _ServerShutdown:
                immediate = True
                logger.info(
                    "바이낸스 샤드 %d serverShutdown — 즉시 재연결", shard.index
                )
            except _SubscribeRejected as exc:
                shard.state.last_error = StreamError(
                    kind="bad_request",
                    message=f"바이낸스 구독 거부(샤드 {shard.index}): {exc}",
                    status_code=None,
                    url=WS_URL,
                )
            except Exception as exc:
                shard.state.last_error = _classify(exc, shard.index)
                self._record_rejection(exc)
            finally:
                shard.state.connected = False
                shard.state.connected_since = None
                shard.state.subscribed = 0
                shard.subscribed = set()
                self._publish()  # close 가 늦어도 집계는 먼저 미연결이 된다
                await self._close_socket(shard)
            if immediate:
                continue
            logger.warning(
                "바이낸스 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
                shard.index,
                shard.backoff,
                shard.state.last_error.message if shard.state.last_error else "",
            )
            await self._sleep(shard.backoff)
            shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)

    async def _run_rebalance(self) -> None:
        """set_universe 가 깨우거나 60초마다 — 연결된 샤드의 구독 차이를 맞춘다 (§3.3)."""
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), REBALANCE_INTERVAL)
            self._wake.clear()
            for shard in self._shards:
                if shard.ws is None:
                    continue
                try:
                    await self._sync_shard(shard)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # 보내기 실패는 펌프가 곧 끊김으로 감지해 재연결한다
                    logger.warning(
                        "바이낸스 샤드 %d 재조정 전송 실패: %r", shard.index, exc
                    )

    async def _open(self) -> Any:
        factory = self._connect if self._connect is not None else open_socket
        return await factory(WS_URL)

    async def _close_socket(self, shard: _Shard) -> None:
        ws, shard.ws = shard.ws, None
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.close()

    async def _sync_shard(self, shard: _Shard) -> None:
        """배정과 실제 구독의 차이만 보낸다 — UNSUBSCRIBE 먼저, 100 params 씩, 0.25초 간격."""
        async with shard.lock:
            ws = shard.ws
            if ws is None:
                return
            wanted = set(shard.assigned)
            drop = sorted(shard.subscribed - wanted)
            add = sorted(wanted - shard.subscribed)
            for method, symbols in (("UNSUBSCRIBE", drop), ("SUBSCRIBE", add)):
                params = [name for s in symbols for name in _stream_names(s)]
                for i in range(0, len(params), PARAMS_PER_MESSAGE):
                    shard.next_id += 1
                    await ws.send(
                        json.dumps(
                            {
                                "method": method,
                                "params": params[i : i + PARAMS_PER_MESSAGE],
                                "id": shard.next_id,
                            }
                        )
                    )
                    await self._sleep(CONTROL_INTERVAL)
            if shard.ws is not ws:
                return  # 보내는 도중 소켓이 바뀌었다 — 죽은 소켓의 구독을 새 소켓 것으로 세지 않는다 (§3.3)
            shard.subscribed = wanted
            shard.state.subscribed = len(wanted)
            self._publish()

    async def _pump(self, shard: _Shard, ws: Any) -> None:
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
            msg = _decode(text)
            # 디코드 뒤, 행·상태 갱신 전 — 받은 텍스트 그대로 + 시세 프레임이면 종류:심볼 (001 §3.7)
            self._record(self.id, WS_SOURCE, at, text, _quote_key(msg))
            if msg is None:
                self.decode_failures += 1
                continue
            if isinstance(msg.get("error"), dict):
                error = msg["error"]
                raise _SubscribeRejected(f"{error.get('code')} {error.get('msg', '')}")
            stream = msg.get("stream")
            if stream == _SHUTDOWN_STREAM:
                raise _ServerShutdown
            data = msg.get("data")
            if not isinstance(stream, str) or not isinstance(data, dict):
                continue  # {"result":null,"id":1} 등은 시세 수신으로 세지 않는다
            name, _, kind = stream.partition("@")
            symbol = name.upper()
            base = self._base_of.get(symbol)
            if base is None:
                continue  # 맵에 없는 심볼 — 버린다 (§3.4)
            try:
                if kind == "depth20":
                    self._on_depth(symbol, base, data, at)
                elif kind == "miniTicker":
                    self._on_mini(base, data)
                else:
                    continue
            except (KeyError, TypeError, ValueError):
                self.decode_failures += 1
                continue
            shard.state.last_message_at = at
            shard.backoff = BACKOFF_START  # 구독까지 성공했다는 증거 = 첫 시세 프레임
            self._publish()

    def _on_depth(self, symbol: str, base: str, data: dict[str, Any], at: int) -> None:
        # depth20 에는 거래소 시각이 없다 — 체결가가 없을 때의 price_timestamp 는 수신 시각 (§3.4)
        self._sink.orderbook(
            exchange=self.id,
            base=base,
            quote=_QUOTE,
            native_symbol=symbol,
            asks=[[float(p), float(q)] for p, q in data["asks"]],
            bids=[[float(p), float(q)] for p, q in data["bids"]],
            timestamp_ms=at,
            received_at_ms=at,
        )

    def _on_mini(self, base: str, data: dict[str, Any]) -> None:
        self._sink.trade(
            exchange=self.id,
            base=base,
            price=float(data["c"]),
            price_timestamp=int(data["E"]),
        )


def _decode(text: str) -> dict[str, Any] | None:
    """프레임 텍스트 → JSON 객체. 객체가 아니거나 JSON 이 아니면 None(무효 프레임)."""
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _quote_key(msg: dict[str, Any] | None) -> str | None:
    """원문 싱크의 `key` — `<symbol>@depth20`·`<symbol>@miniTicker` 프레임이면 `"<종류>:<대문자 심볼>"` (§3.1)."""
    if msg is None or not isinstance(msg.get("stream"), str):
        return None
    name, _, kind = msg["stream"].partition("@")
    if kind in QUOTE_KINDS and name:
        return f"{kind}:{name.upper()}"
    return None


def _classify(exc: BaseException, shard: int) -> StreamError:
    """연결·핸드셰이크·끊김 분류 — 핸드셰이크 HTTP 거부는 바이낸스 REST 규칙(011 §3.2)으로."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = f"바이낸스 WebSocket 실패(샤드 {shard}): {type(exc).__name__}: {exc}"
    if isinstance(status, int):
        headers = getattr(response, "headers", None)
        retry = headers.get("Retry-After") if headers is not None else None
        return StreamError(
            _classify_rest_status(status),
            message,
            status,
            WS_URL,
            _retry_after(retry),
            _response_body(response),
        )
    if isinstance(exc, TimeoutError):
        return StreamError("timeout", message, None, WS_URL)
    if isinstance(exc, OSError | ConnectionClosed):
        return StreamError("network", message, None, WS_URL)  # DNS·거부·TLS·끊김
    return StreamError("bad_response", message, None, WS_URL)


def _response_body(response: object) -> str | None:
    """핸드셰이크 거부 응답 본문 앞 500자 — 거래소가 뭐라고 했는지 이력에 남긴다 (011 §3.3)."""
    text = _response_text(response)
    return text[:_BODY_LIMIT] if text is not None else None


def _response_text(response: object) -> str | None:
    """핸드셰이크 거부 응답 본문 전문 — 거래소가 준 것이라 원문 싱크에 그대로 넘긴다 (001 §3.7)."""
    raw = getattr(response, "body", None)
    if not raw:
        return None
    return (
        raw.decode("utf-8", "replace")
        if isinstance(raw, bytes | bytearray)
        else str(raw)
    )


def _classify_rest_status(status: int) -> str:
    """바이낸스 규칙(011 §3.2): 429 한도초과, 418·403 차단, 5xx 장애, 그 외 4xx 요청오류."""
    if status == 429:
        return "rate_limit"
    if status in (418, 403):
        return "banned"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"


def _retry_after(raw: object) -> int | None:
    """Retry-After 가 초 단위 정수일 때만 값을 남긴다 — 날짜 형식은 무시."""
    return int(raw) if isinstance(raw, str) and raw.isdigit() else None
