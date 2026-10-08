"""비트겟 USDT 무기한 선물(perp) 커넥터 — `ticker` 3샤드(스냅샷) + contracts(USDT-FUTURES, 10초) REST (스펙 046 §3.7).

현물 커넥터(020)와 규칙은 같지만 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가. 다른 점: 로컬 북이 없다
(`ticker` 한 채널에 최우선 호가·마크가·펀딩이 함께 온다, `action` 은 늘 snapshot), `fundInterval`(시간 문자열)이
주기이고, 구독 거부(`code` 가 숫자)는 기록만 하고 연결을 유지한다, pong 은 10초 안에 와야 한다, 배수 심볼은
core.perp.split_multiplier 로 1코인 단위가 된다. 핑은 문자열 `ping` 30초, 구독 요청은 시간당 240회/연결 예산.
메시지마다: 원문 싱크 → 디코드 → PerpSink(행 제자리 갱신) → 그 샤드의 last_message_at(시세만).
"""

import asyncio
import contextlib
import json
import logging
import re
import time
import zlib
from collections import deque
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
from app.core.perp import PerpSink, split_multiplier
from app.core.ticks import STALE_AFTER_MS, StreamVerdict

logger = logging.getLogger("marketlens.stream.bitget_perp")

WS_URL = "wss://ws.bitget.com/v2/ws/public"  # 현물과 같은 주소·다른 연결 (§3.7)
WS_SOURCE = "ws:/v2/ws/public"
HANDSHAKE_SOURCE = "ws-handshake:/v2/ws/public"
REST_URL = "https://api.bitget.com"
CONTRACTS_PATH = "/api/v2/mix/market/contracts"
CONTRACTS_URL = REST_URL + CONTRACTS_PATH + "?productType=USDT-FUTURES"
SYMBOLS_KEY = "symbols:perp"
_BODY_LIMIT = 500
_REQUEST_TIME = re.compile(rb'"requestTime":\s*\d+')
_REQUEST_TIME_WITHIN = 256
_NAN = float("nan")

INST_TYPE = "USDT-FUTURES"
TICKER_CHANNEL = "ticker"
SHARDS = 3
ARGS_PER_MESSAGE = 50  # 원소 약 70바이트 × 50 = 3,500 < 4,096 (§3.7)
CONTROL_INTERVAL = 0.2
REBALANCE_INTERVAL = 60.0
PING_INTERVAL = 30.0  # 문자열 ping 주기 — 2분 무핑이면 서버가 끊는다 (§3.7)
PONG_TIMEOUT = 10.0  # 이 안에 pong 이 없으면 끊고 재연결 (§3.7)
SUBSCRIBE_BUDGET_PER_HOUR = 240
SUBSCRIBE_WARN_AT = SUBSCRIBE_BUDGET_PER_HOUR * 80 // 100  # 192
BUDGET_WINDOW_MS = 3_600_000
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
CLOSE_TIMEOUT = 2.0
_QUOTE = "USDT"
_OK_CODE = "00000"
_PERPETUAL = "perpetual"
_NORMAL = "normal"
_PERP_SYMBOL_TYPE = "1"  # ticker 의 symbolType — "2" 는 기간물이라 버린다 (§3.7)
_PING = "ping"
_PONG = "pong"


def _now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(symbol: str) -> int:
    """instId → 샤드 번호. crc32 라 프로세스·재기동과 무관하게 같다 (§3.7)."""
    return zlib.crc32(symbol.upper().encode()) % SHARDS


def _arg(symbol: str) -> dict[str, str]:
    return {"instType": INST_TYPE, "channel": TICKER_CHANNEL, "instId": symbol.upper()}


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 비트겟은 문자열 ping 을 요구하므로 라이브러리 keepalive 는 끈다 (§3.7)."""
    return await ws_connect(url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=None)


class _Shard:
    def __init__(self, index: int) -> None:
        self.index = index
        self.state = StreamState(url=WS_URL)
        self.assigned: set[str] = set()
        self.has_work = asyncio.Event()
        self.subscribed: set[str] = set()
        self.requests_at: deque[int] = (
            deque()
        )  # 이 소켓으로 보낸 구독 요청 시각(ms) — 새 연결은 예산도 새로
        self.budget_warned = False
        self.ws: Any | None = None
        self.task: asyncio.Task[None] | None = None
        self.ping_task: asyncio.Task[None] | None = None
        self.backoff = BACKOFF_START
        self.pong_pending = False
        self.pong_timed_out = False
        self.lock = asyncio.Lock()


class BitgetPerpStream:
    id = "bitget_perp"
    url = WS_URL

    def __init__(
        self,
        *,
        store: LiveStore,
        sink: PerpSink,
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
        self._symbol_of: dict[str, str] = {}
        self._base_of: dict[str, str] = {}
        self._mult_of: dict[str, int] = {}
        self._wake = asyncio.Event()
        self._rebalance: asyncio.Task[None] | None = None
        self.decode_failures = 0
        self._symbols_body: bytes | None = None

    # --- 심볼 집합·펀딩 주기 (PerpSymbolSource, §3.2·§3.7) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """contracts 1회 → perpetual·normal·USDT 심볼 맵·주기. requestTime 만 뺀 바이트가 같으면 파싱 생략 (§3.7)."""
        url = CONTRACTS_URL
        try:
            resp = await client.get(url)
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
        self._record(
            self.id, f"rest:{CONTRACTS_PATH}", self._clock(), resp.text, SYMBOLS_KEY
        )
        comparable = _without_request_time(resp.content)
        if resp.status_code == 200 and comparable == self._symbols_body:
            return 1
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
                self.id, url, "비트겟 perp contracts 에 data 가 없다", body=resp.text
            )
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
        self._symbols_body = comparable
        for symbol, base in self._base_of.items():
            self._sink.set_interval(self.id, base, interval_of[symbol])
        return 1

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
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
                self._store.remove_perp_row(self.id, self._base_of.get(symbol, symbol))
            shard.assigned = mine
            if mine:
                shard.has_work.set()
            else:
                shard.has_work.clear()
        if changed:
            self._wake.set()

    # --- 판정 (§3.8) ---

    def judge(self, now_ms: int) -> StreamVerdict | None:
        targets = [s for s in self._shards if s.assigned]
        if not targets:
            return None
        verdicts = [(s, self._judge_shard(s, now_ms)) for s in targets]
        bad = [(s, v) for s, v in verdicts if v is not None and not v.ok]
        if bad:
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
                return None
            return StreamVerdict(
                ok=False,
                error=StreamError(
                    kind="network",
                    message=f"Bitget perp 스트림 끊김: 샤드 {shard.index}",
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
                        f"Bitget perp 스트림 정체: 샤드 {shard.index} "
                        f"(구독 {len(shard.assigned)}종목) {STALE_AFTER_MS // 1000}초 이상 무수신"
                    ),
                    status_code=None,
                    url=WS_URL,
                ),
            )
        return StreamVerdict(ok=True)

    @staticmethod
    def _quiet_ms(shard: _Shard, now_ms: int) -> float:
        state = shard.state
        marks = [state.last_message_at]
        if state.connected:
            marks.append(state.connected_since)
        known = [m for m in marks if m is not None]
        return now_ms - max(known) if known else float("inf")

    def _publish(self) -> None:
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
        text = _response_text(getattr(exc, "response", None))
        if text:
            self._record(self.id, HANDSHAKE_SOURCE, self._clock(), text)

    async def _run_shard(self, shard: _Shard) -> None:
        while True:
            if not shard.assigned:
                await shard.has_work.wait()
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
                    "비트겟 perp 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %s",
                    shard.index,
                    shard.backoff,
                    shard.state.last_error.message,
                )
                await self._sleep(shard.backoff)
                shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)
                continue
            shard.ws = ws
            shard.subscribed = set()
            shard.requests_at.clear()
            shard.budget_warned = False
            shard.pong_pending = False
            shard.pong_timed_out = False
            shard.state.connected = True
            shard.state.connected_since = self._clock()
            self._publish()
            try:
                await self._sync_shard(shard)
                shard.state.connected_since = self._clock()
                self._publish()
                shard.ping_task = asyncio.create_task(self._run_ping(shard, ws))
                await self._pump(shard, ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if shard.pong_timed_out:
                    shard.state.last_error = StreamError(
                        kind="timeout",
                        message=f"Bitget perp 스트림 pong 없음(샤드 {shard.index}): {PONG_TIMEOUT:.0f}초 안에 응답이 없어 끊었다",
                        status_code=None,
                        url=WS_URL,
                    )
                else:
                    shard.state.last_error = _classify(exc, shard.index)
                    self._record_rejection(exc)
            finally:
                await self._stop_ping(shard)
                shard.state.connected = False
                shard.state.connected_since = None
                shard.state.subscribed = 0
                shard.subscribed = set()
                self._publish()
                await self._close_socket(shard)
            logger.warning(
                "비트겟 perp 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
                shard.index,
                shard.backoff,
                shard.state.last_error.message if shard.state.last_error else "",
            )
            await self._sleep(shard.backoff)
            shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)

    async def _run_rebalance(self) -> None:
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
                    logger.warning(
                        "비트겟 perp 샤드 %d 재조정 전송 실패: %r", shard.index, exc
                    )

    async def _run_ping(self, shard: _Shard, ws: Any) -> None:
        """30초마다 문자열 ping — pong 이 10초 안에 없으면 소켓을 닫아 펌프가 재연결하게 한다 (§3.7)."""
        while True:
            await self._sleep(PING_INTERVAL)
            shard.pong_pending = True
            try:
                await ws.send(_PING)
            except Exception:
                return
            await self._sleep(PONG_TIMEOUT)
            if shard.pong_pending:
                shard.pong_timed_out = True
                logger.warning(
                    "비트겟 perp 샤드 %d pong 없음 — %.0f초 안에 응답이 없어 끊고 재연결한다",
                    shard.index,
                    PONG_TIMEOUT,
                )
                with contextlib.suppress(Exception):
                    await ws.close()
                return

    async def _stop_ping(self, shard: _Shard) -> None:
        task, shard.ping_task = shard.ping_task, None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _open(self) -> Any:
        factory = self._connect if self._connect is not None else open_socket
        return await factory(WS_URL)

    async def _close_socket(self, shard: _Shard) -> None:
        ws, shard.ws = shard.ws, None
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.close()

    def _budget_left(self, shard: _Shard) -> bool:
        """시간당 구독 요청 예산의 80% 안인가 — 넘었으면 경고 1줄, 이번 재조정은 미룬다 (§3.7)."""
        now = self._clock()
        while shard.requests_at and now - shard.requests_at[0] >= BUDGET_WINDOW_MS:
            shard.requests_at.popleft()
        if len(shard.requests_at) < SUBSCRIBE_WARN_AT:
            shard.budget_warned = False
            return True
        if not shard.budget_warned:
            shard.budget_warned = True
            logger.warning(
                "비트겟 perp 샤드 %d 구독 요청이 시간당 %d회(예산 %d회의 80%%)에 닿았다 — 재조정을 다음 회차로 미룬다",
                shard.index,
                len(shard.requests_at),
                SUBSCRIBE_BUDGET_PER_HOUR,
            )
        return False

    async def _sync_shard(self, shard: _Shard) -> None:
        """배정과 실제 구독의 차이만 보낸다 — unsubscribe 먼저, args 50개씩, 0.2초 간격, 예산 안에서 (§3.7)."""
        async with shard.lock:
            ws = shard.ws
            if ws is None:
                return
            wanted = set(shard.assigned)
            drop = sorted(shard.subscribed - wanted)
            add = sorted(wanted - shard.subscribed)
            for op, symbols in (("unsubscribe", drop), ("subscribe", add)):
                for i in range(0, len(symbols), ARGS_PER_MESSAGE):
                    batch = symbols[i : i + ARGS_PER_MESSAGE]
                    if not self._budget_left(shard):
                        return
                    await ws.send(
                        json.dumps({"op": op, "args": [_arg(s) for s in batch]})
                    )
                    shard.requests_at.append(self._clock())
                    if shard.ws is not ws:
                        return
                    if op == "subscribe":
                        shard.subscribed |= set(batch)
                    else:
                        shard.subscribed -= set(batch)
                    shard.state.subscribed = len(shard.subscribed)
                    self._publish()
                    await self._sleep(CONTROL_INTERVAL)

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
            if text == _PONG:
                self._record(self.id, WS_SOURCE, at, text)
                shard.pong_pending = False
                continue
            msg = _decode(text)
            self._record(self.id, WS_SOURCE, at, text, _quote_key(msg))
            if msg is None:
                self.decode_failures += 1
                continue
            if "event" in msg:
                # 구독·해지 응답 — 시세로 세지 않는다. error 는 기록만 하고 연결은 유지한다 (`code` 는 숫자, §3.7)
                if msg["event"] == "error":
                    shard.state.last_error = StreamError(
                        kind="bad_request",
                        message=f"Bitget perp 구독 거부(샤드 {shard.index}): code {msg.get('code')}: {msg.get('msg', '')}",
                        status_code=None,
                        url=WS_URL,
                    )
                    logger.warning("%s", shard.state.last_error.message)
                continue
            arg = msg.get("arg")
            if msg.get("action") != "snapshot" or not isinstance(arg, dict):
                self.decode_failures += 1
                continue
            if arg.get("channel") != TICKER_CHANNEL:
                continue
            symbol = str(arg.get("instId") or "").upper()
            base = self._base_of.get(symbol)
            if base is None:
                continue
            try:
                self._on_ticker(symbol, base, msg["data"], at)
            except (KeyError, TypeError, ValueError, IndexError):
                self.decode_failures += 1
                continue
            shard.state.last_message_at = at
            shard.backoff = BACKOFF_START
            last = self._state.last_message_at
            if last is None or at > last:
                self._state.last_message_at = at

    def _on_ticker(self, symbol: str, base: str, data: Any, at: int) -> None:
        """ticker 스냅샷 → 호가·펀딩·마크를 한 번에. symbolType "2"(기간물)는 버린다 (§3.7)."""
        item = data[0]
        if str(item.get("symbolType", _PERP_SYMBOL_TYPE)) != _PERP_SYMBOL_TYPE:
            return
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
        self._sink.funding(
            source=self.id,
            base=base,
            multiplier=mult,
            received_at_ms=at,
            funding_rate=_float_or_none(item.get("fundingRate")),
            next_funding_ms=_int_or_none(item.get("nextFundingTime")),
            mark=_float_or_none(item.get("markPrice")),
        )


def _quote_field(item: dict[str, Any], name: str) -> float:
    """스냅샷의 호가 필드 — 없거나 숫자가 아니면 NaN(무효) — 지금 행의 값으로 메우지 않는다 (§3.4)."""
    try:
        return float(item[name])
    except (KeyError, TypeError, ValueError):
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


def _decode(text: str) -> dict[str, Any] | None:
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _quote_key(msg: dict[str, Any] | None) -> str | None:
    """원문 싱크의 `key` — ticker 프레임이면 `ticker:<심볼>` (§3.7)."""
    if msg is None or "action" not in msg or not isinstance(msg.get("arg"), dict):
        return None
    arg = msg["arg"]
    symbol = str(arg.get("instId") or "").upper()
    if symbol and arg.get("channel") == TICKER_CHANNEL:
        return f"ticker:{symbol}"
    return None


def _classify(exc: BaseException, shard: int) -> StreamError:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = f"Bitget perp WebSocket 실패(샤드 {shard}): {type(exc).__name__}: {exc}"
    if isinstance(status, int):
        return StreamError(
            _classify_rest_status(status),
            message,
            status,
            WS_URL,
            None,
            _response_body(response),
        )
    if isinstance(exc, TimeoutError):
        return StreamError("timeout", message, None, WS_URL)
    if isinstance(exc, OSError | ConnectionClosed):
        return StreamError("network", message, None, WS_URL)
    return StreamError("bad_response", message, None, WS_URL)


def _response_body(response: object) -> str | None:
    text = _response_text(response)
    return text[:_BODY_LIMIT] if text is not None else None


def _response_text(response: object) -> str | None:
    raw = getattr(response, "body", None)
    if not raw:
        return None
    return (
        raw.decode("utf-8", "replace")
        if isinstance(raw, bytes | bytearray)
        else str(raw)
    )


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
