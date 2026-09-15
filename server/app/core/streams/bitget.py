"""비트겟 스트림 커넥터 — WebSocket books15(스냅샷)·trade 3샤드 + symbols REST (스펙 020).

바이낸스(012)·바이빗(019)과 규칙은 같지만 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
바이빗과 다른 점: 구독 인자가 토픽 문자열이 아니라 `{instType, channel, instId}` 객체이고, 핑은
JSON 이 아니라 **문자열 `ping`/`pong`** 이며, update 에 `seq` 가 있어 역행분을 버리고, 구독 요청에
시간당 240회/연결 예산이 있어 80% 를 넘으면 재조정을 다음 회차로 미룬다.
심볼은 crc32 % 3 으로 샤드에 고정 배정되고, 샤드마다 소켓·시계·백오프·북·예산을 따로 둔다.
메시지마다: 원문 싱크 기록 → 디코드 → 북·행 갱신(QuoteSink) → 그 샤드의 last_message_at(시세만).
심볼 집합 계약(ForeignSymbolSource)도 이 커넥터가 구현한다 — 우주가 확정되면 차이만 재조정한다.
"""

import asyncio
import contextlib
import json
import logging
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
from app.core.quotes import QuoteSink
from app.core.ticks import STALE_AFTER_MS, StreamVerdict

logger = logging.getLogger("marketlens.stream.bitget")

WS_URL = "wss://ws.bitget.com/v2/ws/public"
WS_SOURCE = "ws:/v2/ws/public"
HANDSHAKE_SOURCE = "ws-handshake:/v2/ws/public"  # 핸드셰이크 거부 응답 본문의 원문 싱크 source (010 §3.1)
REST_URL = "https://api.bitget.com"
SYMBOLS_PATH = "/api/v2/spot/public/symbols"
SYMBOLS_URL = REST_URL + SYMBOLS_PATH  # 파라미터 없음 = 전체 (§3.3)
SYMBOLS_KEY = "symbols:all"  # 매초 오는 심볼 목록 본문의 원문 싱크 key — 분당 마지막 1건 (001 §3.7)
_BODY_LIMIT = 500  # 핸드셰이크 거부 응답 본문 상한 — 001 §3.1 과 같은 500자

INST_TYPE = "SPOT"
BOOKS_CHANNEL = "books15"  # 15단계 스냅샷 — 전체 깊이 `books` 는 update 마다 북 전체를 다시 정렬해 CPU 47% 를 먹었다 (§3.2 개정 2026-09-15)
TRADE_CHANNEL = "trade"
SHARDS = 3
ARGS_PER_MESSAGE = (
    50  # 요청 하나의 args 상한 — 원소 약 60바이트 × 50 = 3,000 < 4,096 (§3.3)
)
CONTROL_INTERVAL = (
    0.2  # 구독 요청 사이 대기 — 초당 5개, 핑을 더해도 초당 10개 안 (§3.3)
)
REBALANCE_INTERVAL = 60.0  # set_universe 가 깨우지 않아도 이 주기로 구독 차이를 맞춘다
PING_INTERVAL = 30.0  # 문자열 ping 주기 — 2분 무핑이면 서버가 끊는다 (§3.2)
PONG_TIMEOUT = 30.0  # 이 안에 pong 이 없으면 끊고 재연결 — 조용히 죽은 TCP 감지 (§3.2)
SUBSCRIBE_BUDGET_PER_HOUR = 240  # 연결당 구독 요청 한도 — 공식 문서 (§3.2)
SUBSCRIBE_WARN_AT = (
    SUBSCRIBE_BUDGET_PER_HOUR * 80 // 100
)  # 192 — 넘으면 경고 1줄·재조정 연기 (§3.3)
BUDGET_WINDOW_MS = 3_600_000
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
CLOSE_TIMEOUT = 2.0  # 종료 시 취소 + 소켓 3개 동시 close 합계 상한
_QUOTE = "USDT"
_OK_CODE = "00000"  # 봉투 code — HTTP 200 이어도 이 값이 아니면 실패 (§3.3·§3.8)
_ONLINE = "online"
_PING = "ping"
_PONG = "pong"


def _now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(symbol: str) -> int:
    """심볼 → 샤드 번호. crc32 라 프로세스·재기동과 무관하게 같다 (§3.3)."""
    return zlib.crc32(symbol.upper().encode()) % SHARDS


def _args(symbol: str) -> list[dict[str, str]]:
    s = symbol.upper()
    return [
        {"instType": INST_TYPE, "channel": BOOKS_CHANNEL, "instId": s},
        {"instType": INST_TYPE, "channel": TRADE_CHANNEL, "instId": s},
    ]


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다.

    라이브러리 keepalive(WebSocket ping 프레임)는 쓰지 않는다 — 비트겟은 문자열 ping 을 요구하고
    프레임 ping 에 대한 응답은 문서에 없다 (§3.2).
    """
    return await ws_connect(url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=None)


class _SubscribeRejected(Exception):
    """`event:"error"` 응답 → bad_request (§3.2)."""


class _Book:
    """심볼 하나의 로컬 북 — 가격 → 잔량 + 마지막 seq. 스냅샷으로 통째 교체, update 로 삽입·교체·삭제 (§3.4)."""

    def __init__(self) -> None:
        self.asks: dict[float, float] = {}
        self.bids: dict[float, float] = {}
        self.seq = -1

    def replace(self, data: dict[str, Any]) -> None:
        self.asks = {float(p): float(q) for p, q in data["asks"]}
        self.bids = {float(p): float(q) for p, q in data["bids"]}

    def apply(self, data: dict[str, Any]) -> None:
        for side, levels in (("asks", self.asks), ("bids", self.bids)):
            for p, q in data.get(side) or []:
                price, size = float(p), float(q)
                if size <= 0:
                    levels.pop(price, None)  # 잔량 0 = 그 가격 삭제 (공식 문서 규칙)
                else:
                    levels[price] = size

    def sorted_levels(self) -> tuple[list[list[float]], list[list[float]]]:
        """asks 오름차순·bids 내림차순 — 행 규칙(001 §3.3)이 기대하는 순서."""
        asks = sorted(([p, q] for p, q in self.asks.items()), key=lambda lv: lv[0])
        bids = sorted(
            ([p, q] for p, q in self.bids.items()), key=lambda lv: lv[0], reverse=True
        )
        return asks, bids


class _Shard:
    """소켓 하나 = 샤드 하나. 시계·백오프·구독 집합·북·구독 예산을 샤드마다 따로 둔다 (§3.5)."""

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
        self.books: dict[str, _Book] = {}  # 심볼 → 북 — 소켓이 바뀌면 비운다 (§3.4)
        self.requests_at: deque[int] = (
            deque()
        )  # 이 소켓으로 보낸 구독 요청 시각(ms) — 새 연결은 예산도 새로 (§3.2)
        self.budget_warned = False  # 예산 소진 경고를 이미 냈다 — 회복될 때까지 한 번만
        self.ws: Any | None = None
        self.task: asyncio.Task[None] | None = None
        self.ping_task: asyncio.Task[None] | None = None
        self.backoff = BACKOFF_START
        self.pong_pending = False  # ping 을 보냈고 아직 pong 이 안 왔다
        self.pong_timed_out = (
            False  # 이번 연결이 pong 없음으로 끊겼다 — 실패 종류를 timeout 으로
        )
        self.lock = asyncio.Lock()  # 연결 직후 구독과 재조정 전송이 겹치지 않게


class BitgetStream:
    id = "bitget"
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
        self._symbol_of: dict[str, str] = {}  # base → 심볼 (symbols baseCoin)
        self._base_of: dict[str, str] = {}  # 심볼 → base
        self._last_trade_ts: dict[
            str, int
        ] = {}  # base → 마지막으로 실은 체결 ts (§3.4)
        self._wake = asyncio.Event()  # set_universe 가 재조정 루프를 깨운다
        self._rebalance: asyncio.Task[None] | None = None
        self.decode_failures = 0  # 버린 무효 프레임 수 — 그 자체로 실패가 아니다

    # --- 심볼 집합 (ForeignSymbolSource, §3.3) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """symbols 1회 → online·USDT 심볼 맵. 응답 본문은 해석 전에 원문 싱크로(`symbols:all`).

        HTTP 200 이어도 `code != "00000"` 이면 실패다 (§3.8).
        """
        url = SYMBOLS_URL
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"비트겟 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"비트겟 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        self._record(
            self.id, f"rest:{SYMBOLS_PATH}", self._clock(), resp.text, SYMBOLS_KEY
        )
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"비트겟 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_rest_status(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"비트겟 JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if not isinstance(data, dict):
            raise ExchangeApiError(
                self.id, url, "비트겟 응답이 객체가 아니다", body=resp.text
            )
        code = data.get("code")
        if code != _OK_CODE:
            # 한도 초과를 본문 code 로 알리는 경우는 문서에 없다 — 전부 bad_response, body 로 표를 채운다 (§3.8)
            raise ExchangeApiError(
                self.id,
                url,
                f"비트겟 code {code}: {data.get('msg', '')}",
                status_code=resp.status_code,
                body=resp.text,
                kind="bad_response",
            )
        items = data.get("data")
        if not isinstance(items, list):
            raise ExchangeApiError(
                self.id, url, "비트겟 symbols 에 data 가 없다", body=resp.text
            )
        symbol_of: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("status") != _ONLINE or item.get("quoteCoin") != _QUOTE:
                continue
            base = str(item["baseCoin"]).upper()
            symbol_of.setdefault(
                base, str(item["symbol"]).upper()
            )  # 둘 이상이면 처음 것
        self._symbol_of = symbol_of
        self._base_of = {symbol: base for base, symbol in symbol_of.items()}
        return 1

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
        """우주 확정 → 샤드별 배정 교체. 빠진 심볼의 행·북은 그 자리에서 지우고 재조정을 깨운다.

        우주 전체를 받으므로 자기 맵에 없는 base 는 무시한다(다른 해외에만 있는 코인).
        매초 불리므로 배정이 하나도 안 바뀌면 아무것도 하지 않는다 (§3.3).
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
                base = self._base_of.get(symbol, symbol)
                self._store.remove_row(self.id, base)
                self._last_trade_ts.pop(base, None)
                shard.books.pop(symbol, None)
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
                    message=f"비트겟 스트림 끊김: 샤드 {shard.index}",
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
                        f"비트겟 스트림 정체: 샤드 {shard.index} "
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
        """샤드 상태를 store.stream("bitget") 하나로 집계한다 (§3.5). last_error 는 judge 가 정한다."""
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
        """태스크 전부 취소 → 소켓 3개 동시 close, 합계 2초 상한."""
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
        """비트겟이 핸드셰이크를 거부하며 준 HTTP 본문도 원문이다 — 해석 전에 싱크로 (001 §3.7)."""
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
                    "비트겟 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %s",
                    shard.index,
                    shard.backoff,
                    shard.state.last_error.message,
                )
                await self._sleep(shard.backoff)
                shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)
                continue
            shard.ws = ws
            shard.subscribed = set()
            shard.books.clear()  # 새 소켓은 새 스냅샷으로 시작한다 — 옛 북에 새 update 를 얹지 않는다 (§3.4)
            shard.requests_at.clear()  # 새 연결은 구독 예산도 새로 (§3.2)
            shard.budget_warned = False
            shard.pong_pending = False
            shard.pong_timed_out = False
            shard.state.connected = True
            # 구독을 보내는 동안은 소켓이 열린 시각을 임시로 — 직전 연결의 수신 시각으로 정체가 되지 않게 (§3.5)
            shard.state.connected_since = self._clock()
            self._publish()
            try:
                await self._sync_shard(shard)
                shard.state.connected_since = (
                    self._clock()
                )  # 구독 시각 = 첫 묶음을 다 보낸 시각
                self._publish()
                shard.ping_task = asyncio.create_task(self._run_ping(shard, ws))
                await self._pump(shard, ws)
            except asyncio.CancelledError:
                raise
            except _SubscribeRejected as exc:
                shard.state.last_error = StreamError(
                    kind="bad_request",
                    message=f"비트겟 구독 거부(샤드 {shard.index}): {exc}",
                    status_code=None,
                    url=WS_URL,
                )
            except Exception as exc:
                if shard.pong_timed_out:
                    shard.state.last_error = StreamError(
                        kind="timeout",
                        message=f"비트겟 스트림 pong 없음(샤드 {shard.index}): {PONG_TIMEOUT:.0f}초 안에 응답이 없어 끊었다",
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
                shard.books.clear()
                self._publish()  # close 가 늦어도 집계는 먼저 미연결이 된다
                await self._close_socket(shard)
            logger.warning(
                "비트겟 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
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
                        "비트겟 샤드 %d 재조정 전송 실패: %r", shard.index, exc
                    )

    async def _run_ping(self, shard: _Shard, ws: Any) -> None:
        """30초마다 문자열 ping — pong 이 30초 안에 없으면 소켓을 닫아 펌프가 재연결하게 한다 (§3.2)."""
        while True:
            await self._sleep(PING_INTERVAL)
            shard.pong_pending = True
            try:
                await ws.send(_PING)
            except Exception:
                return  # 죽은 소켓 — 펌프가 끊김을 처리한다
            await self._sleep(PONG_TIMEOUT)
            if shard.pong_pending:
                shard.pong_timed_out = True
                logger.warning(
                    "비트겟 샤드 %d pong 없음 — %.0f초 안에 응답이 없어 끊고 재연결한다",
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
        """시간당 구독 요청 예산의 80% 안인가 — 넘었으면 경고 1줄, 이번 재조정은 미룬다 (§3.3)."""
        now = self._clock()
        while shard.requests_at and now - shard.requests_at[0] >= BUDGET_WINDOW_MS:
            shard.requests_at.popleft()
        if len(shard.requests_at) < SUBSCRIBE_WARN_AT:
            shard.budget_warned = False
            return True
        if not shard.budget_warned:
            # 소진 중엔 60초마다 회차가 돌아와도 경고는 한 번 — 회복 뒤 다시 소진되면 또 한 번
            shard.budget_warned = True
            logger.warning(
                "비트겟 샤드 %d 구독 요청이 시간당 %d회(예산 %d회의 80%%)에 닿았다 — 재조정을 다음 회차로 미룬다",
                shard.index,
                len(shard.requests_at),
                SUBSCRIBE_BUDGET_PER_HOUR,
            )
        return False

    async def _sync_shard(self, shard: _Shard) -> None:
        """배정과 실제 구독의 차이만 보낸다 — unsubscribe 먼저, args 50개씩, 0.2초 간격, 예산 안에서 (§3.3).

        요청 하나가 나갈 때마다 그 묶음의 심볼을 실제 구독에 반영한다 — 예산에 막혀 중간에 멈춰도
        보낸 만큼은 소켓에 묶인 상태 그대로다.
        """
        async with shard.lock:
            ws = shard.ws
            if ws is None:
                return
            wanted = set(shard.assigned)
            drop = sorted(shard.subscribed - wanted)
            add = sorted(wanted - shard.subscribed)
            for op, symbols in (("unsubscribe", drop), ("subscribe", add)):
                for i in range(0, len(symbols), ARGS_PER_MESSAGE // 2):
                    batch = symbols[i : i + ARGS_PER_MESSAGE // 2]
                    if not self._budget_left(shard):
                        return
                    await ws.send(
                        json.dumps(
                            {"op": op, "args": [a for s in batch for a in _args(s)]}
                        )
                    )
                    shard.requests_at.append(self._clock())
                    if shard.ws is not ws:
                        return  # 보내는 도중 소켓이 바뀌었다 — 죽은 소켓의 구독을 새 소켓 것으로 세지 않는다
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
                # 문자열 pong — 시세도 디코드 실패도 아니다 (§3.2)
                self._record(self.id, WS_SOURCE, at, text)
                shard.pong_pending = False
                continue
            msg = _decode(text)
            # 디코드 뒤, 행·상태 갱신 전 — 받은 텍스트 그대로 + 시세 프레임이면 종류:심볼 (001 §3.7)
            self._record(self.id, WS_SOURCE, at, text, _quote_key(msg))
            if msg is None:
                self.decode_failures += 1
                continue
            if "event" in msg:
                # 구독·해지 응답 — 시세로 세지 않는다. error 는 구독 거부 (§3.2)
                if msg["event"] == "error":
                    raise _SubscribeRejected(
                        f"code {msg.get('code')}: {msg.get('msg', '')}"
                    )
                continue
            action = msg.get("action")
            arg = msg.get("arg")
            if action not in ("snapshot", "update") or not isinstance(arg, dict):
                self.decode_failures += 1  # 시세 모양이 아니다 (§3.2)
                continue
            channel, symbol = arg.get("channel"), str(arg.get("instId") or "").upper()
            base = self._base_of.get(symbol)
            if base is None:
                continue  # 맵에 없는 심볼 — 버린다 (§3.4)
            try:
                if channel == BOOKS_CHANNEL:
                    self._on_books(shard, symbol, base, action, msg["data"], at)
                elif channel == TRADE_CHANNEL:
                    self._on_trade(base, msg["data"])
                else:
                    continue  # 구독하지 않은 채널
            except (KeyError, TypeError, ValueError, IndexError):
                self.decode_failures += 1
                continue
            shard.state.last_message_at = at
            shard.backoff = BACKOFF_START  # 구독까지 성공했다는 증거 = 첫 시세 프레임
            self._publish()

    def _on_books(
        self,
        shard: _Shard,
        symbol: str,
        base: str,
        action: str,
        data: Any,
        at: int,
    ) -> None:
        item = data[0]  # data 는 원소 1개 (§3.2)
        seq = int(item["seq"])
        book = shard.books.get(symbol)
        if action == "snapshot":
            book = _Book()  # 새 스냅샷 → 북 통째 교체 (§3.4)
            book.replace(item)
            shard.books[symbol] = book
        else:
            if book is None:
                return  # 스냅샷 전에 온 update — 북이 없으니 버린다 (§3.4)
            if seq <= book.seq:
                return  # 중복·순서 뒤바뀜 — 버린다 (§3.4)
            book.apply(item)
        book.seq = seq
        asks, bids = book.sorted_levels()
        self._sink.orderbook(
            exchange=self.id,
            base=base,
            quote=_QUOTE,
            native_symbol=symbol,
            asks=asks,
            bids=bids,
            timestamp_ms=int(item["ts"]),
            received_at_ms=at,
        )

    def _on_trade(self, base: str, data: Any) -> None:
        """한 프레임의 체결 중 `ts` 가 가장 큰 것 — 배열 순서를 믿지 않는다. 이미 실은 것보다 오래되면 무시 (§3.4)."""
        if not isinstance(data, list) or not data:
            raise ValueError("trade data 가 비었다")
        latest = max(data, key=lambda t: int(t["ts"]))
        ts = int(latest["ts"])
        if ts < self._last_trade_ts.get(base, -1):
            return  # 첫 스냅샷의 과거 체결 — 값이 뒤로 가지 않는다
        self._last_trade_ts[base] = ts
        self._sink.trade(
            exchange=self.id,
            base=base,
            price=float(latest["price"]),
            price_timestamp=ts,
        )


def _decode(text: str) -> dict[str, Any] | None:
    """프레임 텍스트 → JSON 객체. 객체가 아니거나 JSON 이 아니면 None(무효 프레임)."""
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _quote_key(msg: dict[str, Any] | None) -> str | None:
    """원문 싱크의 `key` — 호가·체결 프레임이면 `orderbook:<심볼>`·`trade:<심볼>` (§3.1)."""
    if msg is None or "action" not in msg or not isinstance(msg.get("arg"), dict):
        return None
    arg = msg["arg"]
    symbol = str(arg.get("instId") or "").upper()
    if not symbol:
        return None
    if arg.get("channel") == BOOKS_CHANNEL:
        return f"orderbook:{symbol}"
    if arg.get("channel") == TRADE_CHANNEL:
        return f"trade:{symbol}"
    return None


def _classify(exc: BaseException, shard: int) -> StreamError:
    """연결·핸드셰이크·끊김 분류 — 핸드셰이크 HTTP 거부는 비트겟 REST 규칙(§3.8)으로."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = f"비트겟 WebSocket 실패(샤드 {shard}): {type(exc).__name__}: {exc}"
    if isinstance(status, int):
        # Retry-After 는 문서에 없다 — retry_after_sec 는 null (§3.8)
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
    """비트겟 규칙(§3.8): 403 차단(반복 초과 시 IP 차단), 429 한도초과, 5xx 장애, 그 외 4xx 요청오류."""
    if status == 403:
        return "banned"
    if status == 429:
        return "rate_limit"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"
