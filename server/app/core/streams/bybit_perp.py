"""바이빗 USDT 무기한 선물(perp) 커넥터 — `tickers.<symbol>` 3샤드(100ms snapshot+delta) + instruments-info(linear, 10초) REST (스펙 046 §3.6).

현물 커넥터(019)와 규칙은 같지만 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가. 다른 점: 로컬 북이 없다
(tickers 한 토픽에 최우선 호가·마크가·펀딩이 함께 온다 — 첫 `snapshot` 뒤 바뀐 필드만 든 `delta`), 목록은
`nextPageCursor` 로 이어 받으며 `fundingInterval`(분)이 주기이고, 구독 거부는 기록만 하고 연결을 유지한다,
배수 심볼(`1000PEPE`·`SHIB1000`)은 core.perp.split_multiplier 로 1코인 단위가 된다. 핑은 JSON `{"op":"ping"}`
20초. 메시지마다: 원문 싱크 → 디코드 → PerpSink(행 제자리 갱신) → 그 샤드의 last_message_at(시세만).
"""

import asyncio
import contextlib
import json
import logging
import re
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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

logger = logging.getLogger("marketlens.stream.bybit_perp")

WS_URL = "wss://stream.bybit.com/v5/public/linear"
WS_SOURCE = "ws:/v5/public/linear"
HANDSHAKE_SOURCE = "ws-handshake:/v5/public/linear"
REST_URL = "https://api.bybit.com"
INSTRUMENTS_PATH = "/v5/market/instruments-info"
INSTRUMENTS_URL = REST_URL + INSTRUMENTS_PATH + "?category=linear&limit=1000"
SYMBOLS_KEY = (
    "symbols:perp"  # 10초마다 오는 목록 본문의 원문 싱크 key — 분당 마지막 1건
)
_BODY_LIMIT = 500
# 봉투 꼬리의 `"time":<ms>` 하나만 매 응답 바뀐다 — 이것만 빼고 직전 본문과 비교한다 (§3.6)
_ENVELOPE_TIME = re.compile(rb'"time":\s*\d+\s*\}\s*$')
_ENVELOPE_TIME_WITHIN = 64
_NAN = float("nan")

TICKERS_TOPIC = "tickers."
SHARDS = 3
ARGS_PER_MESSAGE = 50  # args 전체 21,000자 한도 — 50개씩이면 한참 아래 (§3.6)
CONTROL_INTERVAL = 0.2
REBALANCE_INTERVAL = 60.0
PING_INTERVAL = 20.0  # JSON ping 주기 — 10분 무왕래면 서버가 끊는다 (§3.6)
PONG_TIMEOUT = (
    20.0  # 이 안에 pong 이 없으면 끊고 재연결 — 조용히 죽은 TCP 감지 (019 와 같다)
)
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
CLOSE_TIMEOUT = 2.0
_CONTRACT = "LinearPerpetual"
_TRADING = "Trading"
_QUOTE = "USDT"
_RATE_LIMIT_RET_CODE = 10006  # "Too many visits!" — HTTP 200 본문의 retCode (§3.6)


def _now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(symbol: str) -> int:
    """심볼 → 샤드 번호. crc32 라 프로세스·재기동과 무관하게 같다 (§3.6)."""
    return zlib.crc32(symbol.upper().encode()) % SHARDS


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 바이빗은 JSON ping 을 요구하므로 라이브러리 keepalive 는 끈다 (§3.6)."""
    return await ws_connect(url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=None)


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


class _Shard:
    """소켓 하나 = 샤드 하나. 시계·백오프·구독 집합을 샤드마다 따로 둔다."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.state = StreamState(url=WS_URL)
        self.assigned: set[str] = set()  # 이 샤드가 구독해야 할 심볼(대문자)
        self.has_work = asyncio.Event()
        self.subscribed: set[str] = (
            set()
        )  # 이 소켓에 실제로 구독된 심볼 — 소켓이 바뀌면 비운다
        self.ws: Any | None = None
        self.task: asyncio.Task[None] | None = None
        self.ping_task: asyncio.Task[None] | None = None
        self.backoff = BACKOFF_START
        self.pong_pending = False
        self.pong_timed_out = (
            False  # 이번 연결이 pong 없음으로 끊겼다 — 실패 종류를 timeout 으로
        )
        self.lock = asyncio.Lock()


class BybitPerpStream:
    id = "bybit_perp"
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
        self._symbol_of: dict[str, str] = {}  # base → 심볼
        self._base_of: dict[str, str] = {}  # 심볼 → base
        self._mult_of: dict[str, int] = {}  # 심볼 → 배수
        self._pages: list[_Page] = []  # 직전에 맵을 만든 응답 페이지들
        self._wake = asyncio.Event()
        self._rebalance: asyncio.Task[None] | None = None
        self.decode_failures = 0

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
            try:
                resp = await client.get(url)
            except httpx.TimeoutException as exc:
                raise ExchangeTimeoutError(
                    self.id,
                    url,
                    f"바이빗 perp 응답 시간 초과: {type(exc).__name__}: {exc}",
                ) from exc
            except httpx.HTTPError as exc:
                raise ExchangeApiError(
                    self.id,
                    url,
                    f"바이빗 perp 연결 실패: {type(exc).__name__}: {exc}",
                    kind="network",
                ) from exc
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

    def _parse_page(self, resp: httpx.Response, url: str) -> tuple[str, list[_Entry]]:
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
        """perp 우주 확정 → 샤드별 배정 교체. 빠진 심볼의 행은 그 자리에서 지우고 재조정을 깨운다 (019 §3.3 그대로)."""
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
                    message=f"Bybit perp 스트림 끊김: 샤드 {shard.index}",
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
                        f"Bybit perp 스트림 정체: 샤드 {shard.index} "
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
        """샤드 상태를 store.stream("bybit_perp") 하나로 집계한다. last_error 는 judge 가 정한다."""
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
                    "바이빗 perp 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %s",
                    shard.index,
                    shard.backoff,
                    shard.state.last_error.message,
                )
                await self._sleep(shard.backoff)
                shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)
                continue
            shard.ws = ws
            shard.subscribed = set()
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
                        message=f"Bybit perp 스트림 pong 없음(샤드 {shard.index}): {PONG_TIMEOUT:.0f}초 안에 응답이 없어 끊었다",
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
                "바이빗 perp 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
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
                        "바이빗 perp 샤드 %d 재조정 전송 실패: %r", shard.index, exc
                    )

    async def _run_ping(self, shard: _Shard, ws: Any) -> None:
        """20초마다 `{"op":"ping"}` — pong 이 20초 안에 없으면 소켓을 닫아 펌프가 재연결하게 한다 (§3.6)."""
        while True:
            await self._sleep(PING_INTERVAL)
            shard.pong_pending = True
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception:
                return
            await self._sleep(PONG_TIMEOUT)
            if shard.pong_pending:
                shard.pong_timed_out = True
                logger.warning(
                    "바이빗 perp 샤드 %d pong 없음 — %.0f초 안에 응답이 없어 끊고 재연결한다",
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

    async def _sync_shard(self, shard: _Shard) -> None:
        """배정과 실제 구독의 차이만 보낸다 — unsubscribe 먼저, args 50개씩, 0.2초 간격 (§3.6)."""
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
                    await ws.send(
                        json.dumps(
                            {"op": op, "args": [TICKERS_TOPIC + s for s in batch]}
                        )
                    )
                    await self._sleep(CONTROL_INTERVAL)
            if shard.ws is not ws:
                return
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
            self._record(self.id, WS_SOURCE, at, text, _quote_key(msg))
            if msg is None:
                self.decode_failures += 1
                continue
            if "success" in msg:
                # 구독·핑 응답 — 시세로 세지 않는다. 거부는 기록만 하고 연결은 유지한다 (§3.6)
                if msg["success"] is False:
                    shard.state.last_error = StreamError(
                        kind="bad_request",
                        message=f"Bybit perp 구독 거부(샤드 {shard.index}): {msg.get('ret_msg', '')}",
                        status_code=None,
                        url=WS_URL,
                    )
                    logger.warning("%s", shard.state.last_error.message)
                elif msg.get("op") in ("ping", "pong"):
                    shard.pong_pending = False
                continue
            topic = msg.get("topic")
            if not isinstance(topic, str) or not topic.startswith(TICKERS_TOPIC):
                continue
            symbol = topic[len(TICKERS_TOPIC) :].upper()
            base = self._base_of.get(symbol)
            if base is None:
                continue  # 맵에 없는 심볼 — 버린다
            try:
                self._on_ticker(symbol, base, msg, at)
            except (KeyError, TypeError, ValueError):
                self.decode_failures += 1
                continue
            shard.state.last_message_at = at
            shard.backoff = BACKOFF_START
            last = self._state.last_message_at
            if last is None or at > last:
                self._state.last_message_at = at

    def _on_ticker(self, symbol: str, base: str, msg: dict[str, Any], at: int) -> None:
        """snapshot 은 전 필드, delta 는 바뀐 필드만 — 호가 넷은 온 것만 바꾸고 검사는 합친 결과로 (§3.4·§3.6)."""
        data = msg["data"]
        snapshot = msg.get("type") == "snapshot"
        # 스냅샷에 빠진 호가 필드는 무효(NaN), delta 에 없는 필드는 "안 왔다"(None)
        missing = _NAN if snapshot else None
        mult = self._mult_of[symbol]
        quote_fields = [
            _quote_field(data, name, missing)
            for name in ("bid1Price", "ask1Price", "bid1Size", "ask1Size")
        ]
        if snapshot or any(v is not None for v in quote_fields):
            self._sink.quote(
                source=self.id,
                base=base,
                native_symbol=symbol,
                multiplier=mult,
                bid=quote_fields[0],
                ask=quote_fields[1],
                bid_size=quote_fields[2],
                ask_size=quote_fields[3],
                quote_ts=int(msg["ts"]),
                received_at_ms=at,
            )
        self._sink.funding(
            source=self.id,
            base=base,
            multiplier=mult,
            received_at_ms=at,
            funding_rate=_float_or_none(data.get("fundingRate")),
            next_funding_ms=_int_or_none(data.get("nextFundingTime")),
            mark=_float_or_none(data.get("markPrice")),
            funding_interval_h=_int_or_none(data.get("fundingIntervalHour")),
        )


def _quote_field(
    data: dict[str, Any], name: str, missing: float | None
) -> float | None:
    """호가 필드 — 없으면 `missing`, 있는데 숫자가 아니면 NaN(무효)."""
    value = data.get(name)
    if value is None or value == "":
        return missing
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


def _decode(text: str) -> dict[str, Any] | None:
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _quote_key(msg: dict[str, Any] | None) -> str | None:
    """원문 싱크의 `key` — tickers 프레임이면 `tickers:<심볼>` (§3.6)."""
    if msg is None:
        return None
    topic = msg.get("topic")
    if isinstance(topic, str) and topic.startswith(TICKERS_TOPIC) and "data" in msg:
        symbol = topic[len(TICKERS_TOPIC) :].upper()
        return f"tickers:{symbol}" if symbol else None
    return None


def _classify(exc: BaseException, shard: int) -> StreamError:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = f"Bybit perp WebSocket 실패(샤드 {shard}): {type(exc).__name__}: {exc}"
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
