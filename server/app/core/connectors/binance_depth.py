"""바이낸스 깊이 스트림 — WebSocket partial depth (스펙 012).

REST 로는 못 얻는 해외 다단계 호가를 상시 연결로 받아 프로세스 메모리 dict 에 둔다.
읽는 쪽(커넥터)은 `await` 없는 동기 dict 조회 하나뿐이다 — 003 의 호가 소진 계산이
동기 함수여야 하기 때문(§3.4). 그래서 락이 없다.

바이낸스 quirk 는 이 모듈과 `binance.py` 안에서만 흡수한다(커넥터끼리 코드 공유 금지).
"""

import asyncio
import contextlib
import json
import logging
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from websockets.asyncio.client import connect as ws_connect

logger = logging.getLogger("marketlens.binance_depth")

# 시세 전용 호스트. 결합 스트림(`/stream`)이라 메시지에 stream 이름이 함께 온다.
WS_URL = "wss://data-stream.binance.vision/stream"

# 단계 수 20 은 partial depth 최대값(5·10·20), 갱신 주기 1000ms 는 그 기본값이다.
# 수집이 1초 주기이고 LiveStore 도 1초마다 교체되므로 100ms 는 9/10 을 버린다.
# 둘 다 상수로 두어 내릴 여지를 남긴다 (§3.2).
DEPTH_LEVELS = 20
DEPTH_UPDATE_MS = 1000

DEPTH_TTL_MS = 10_000  # 이보다 낡은 깊이는 없는 것으로 친다 (§3.4)
STALE_AFTER_MS = 30_000  # 구독 중인데 이만큼 무수신이면 수집 실패로 승격한다 (§3.6)

SHARD_COUNT = 3  # 한 소켓이 죽어도 1/3 만 잃는다 (§3.3)
REBALANCE_INTERVAL = 60.0  # 구독 대상 재조정 주기(초)
SUBSCRIBE_CHUNK = 100  # 제어 메시지 1개의 params 상한 (§3.3)

# 재연결 지수 백오프 — 연결 시도는 IP 당 5분에 300회 한도다 (§3.3)
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0

CLOSE_TIMEOUT = 2.0  # 종료 시 소켓 close 합계 상한 (§3.4)


@dataclass(frozen=True)
class DepthEntry:
    """심볼 1개의 깊이 스냅샷. partial depth 는 자기완결이라 통째로 갈아끼운다."""

    asks: list[list[float]]  # [price, size] 오름차순
    bids: list[list[float]]  # [price, size] 내림차순
    at: int  # 수신 시각 epoch ms


class DepthSource(Protocol):
    """커넥터가 보는 깊이 캐시 계약 — 동기 읽기 둘뿐이다. 테스트는 가짜를 넣는다."""

    def get(self, symbol: str, now_ms: int) -> DepthEntry | None: ...

    def is_stalled(self, now_ms: int) -> bool: ...


def now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(symbol: str) -> int:
    """심볼 문자열의 안정 해시 % 샤드 수 — 상장·상폐가 나머지 배정을 흔들지 않는다.

    파이썬 `hash()` 는 프로세스마다 값이 달라져(PYTHONHASHSEED) 쓸 수 없다.
    """
    return zlib.crc32(symbol.encode()) % SHARD_COUNT


def stream_name(symbol: str) -> str:
    """`btcusdt@depth20`. 1000ms 는 partial depth 기본이라 접미사가 붙지 않는다."""
    suffix = "" if DEPTH_UPDATE_MS == 1000 else f"@{DEPTH_UPDATE_MS}ms"
    return f"{symbol.lower()}@depth{DEPTH_LEVELS}{suffix}"


class DepthCache:
    """심볼 → 깊이. 샤드 태스크가 쓰고 커넥터가 읽는 평범한 dict 다 (§3.4).

    스트림 정체 판정에 필요한 두 값(구독 수·마지막 수신 시각)도 여기 있다.
    커넥터에 주입되는 것이 하나여야 `fetch_rows` 시그니처가 그대로 남는다.
    """

    def __init__(self) -> None:
        self._entries: dict[str, DepthEntry] = {}
        self._subscribed: dict[int, int] = {}  # 샤드 → 구독 심볼 수
        self._last_message_at: int | None = None
        self._subscribed_since: int | None = None

    # --- 쓰기 (샤드 태스크) ---

    def put(
        self, symbol: str, asks: list[list[float]], bids: list[list[float]], at: int
    ) -> None:
        self._entries[symbol] = DepthEntry(asks=asks, bids=bids, at=at)

    def discard(self, symbol: str) -> None:
        """구독을 끊은 심볼의 깊이는 지운다 — 상폐 코인의 낡은 북을 남기지 않는다."""
        self._entries.pop(symbol, None)

    def note_message(self, at: int) -> None:
        """어느 샤드든 깊이 메시지를 받으면 부른다 — 정체 판정의 기준 시각."""
        self._last_message_at = at

    def set_subscribed(self, shard: int, count: int, at: int) -> None:
        was = self.subscribed_count()
        self._subscribed[shard] = count
        if was == 0 and self.subscribed_count() > 0:
            # 무수신 시계는 구독한 순간부터 센다 — 기동 직후 구독 0 은 실패가 아니다
            self._subscribed_since = at

    # --- 읽기 (커넥터, 동기 — await 없음) ---

    def get(self, symbol: str, now_ms: int) -> DepthEntry | None:
        entry = self._entries.get(symbol)
        if entry is None or now_ms - entry.at > DEPTH_TTL_MS:
            return None
        return entry

    def subscribed_count(self) -> int:
        return sum(self._subscribed.values())

    def silent_ms(self, now_ms: int) -> int | None:
        """구독 중일 때 마지막 수신 이후 경과 ms. 구독이 없으면 None(판정 대상 아님)."""
        if self.subscribed_count() == 0:
            return None
        since = max(self._last_message_at or 0, self._subscribed_since or 0)
        if since == 0:
            return None
        return max(0, now_ms - since)

    def is_stalled(self, now_ms: int) -> bool:
        """어느 샤드도 30초 동안 메시지를 못 받았는가 (§3.6)."""
        silent = self.silent_ms(now_ms)
        return silent is not None and silent >= STALE_AFTER_MS


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다."""
    return await ws_connect(url)


class _Shard:
    """샤드 1개 = 소켓 1개. 죽어도 나머지 2/3 의 깊이는 남는다 (§3.3)."""

    def __init__(
        self,
        index: int,
        cache: DepthCache,
        symbols: Callable[[], set[str]],
        connect: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._index = index
        self._cache = cache
        self._symbols = symbols
        self._connect = connect
        self._ws: Any | None = None
        self._subscribed: set[str] = set()
        self._next_id = 1

    def mine(self) -> set[str]:
        return {s for s in self._symbols() if shard_of(s) == self._index}

    async def run(self) -> None:
        backoff = BACKOFF_START
        while True:
            try:
                ws = await self._open()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "바이낸스 깊이 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %r",
                    self._index,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            self._ws = ws
            self._subscribed = set()
            try:
                await self._resubscribe(ws)
                backoff = BACKOFF_START  # 구독까지 성공하면 백오프를 되돌린다
                await self._pump(ws)
            except asyncio.CancelledError:
                raise  # 소켓은 취소 대기가 끝난 뒤 aclose 가 닫는다 (§3.4)
            except Exception as exc:
                logger.warning(
                    "바이낸스 깊이 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %r",
                    self._index,
                    backoff,
                    exc,
                )
            self._ws = None
            with contextlib.suppress(Exception):
                await ws.close()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.close()

    async def _open(self) -> Any:
        # 모듈 전역을 호출 시점에 찾는다 — 테스트가 open_socket 을 갈아끼울 수 있게
        factory = self._connect if self._connect is not None else open_socket
        return await factory(WS_URL)

    async def _pump(self, ws: Any) -> None:
        """메시지를 읽으면서 재조정 시각이 되면 구독을 갱신한다.

        수신 대기에 재조정 남은 시간을 타임아웃으로 걸어 태스크 하나로 둘을 처리한다.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REBALANCE_INTERVAL
        while True:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), max(0.0, deadline - loop.time())
                )
            except TimeoutError:
                raw = None
            if raw is not None:
                self._on_message(raw)
            if loop.time() >= deadline:
                await self._resubscribe(ws)
                deadline = loop.time() + REBALANCE_INTERVAL

    async def _resubscribe(self, ws: Any) -> None:
        desired = self.mine()
        added = desired - self._subscribed
        removed = self._subscribed - desired
        if added:
            await self._send(ws, "SUBSCRIBE", sorted(added))
        if removed:
            await self._send(ws, "UNSUBSCRIBE", sorted(removed))
            for symbol in removed:
                self._cache.discard(symbol)
        self._subscribed = desired
        self._cache.set_subscribed(self._index, len(desired), now_ms())
        if added or removed:
            logger.info(
                "바이낸스 깊이 샤드 %d 구독 완료: %d 종목 (+%d/-%d)",
                self._index,
                len(desired),
                len(added),
                len(removed),
            )

    async def _send(self, ws: Any, method: str, symbols: list[str]) -> None:
        """한 메시지의 params 는 100 스트림까지 — 제어 메시지는 연결당 5개/초 한도다 (§3.3)."""
        for i in range(0, len(symbols), SUBSCRIBE_CHUNK):
            chunk = symbols[i : i + SUBSCRIBE_CHUNK]
            await ws.send(
                json.dumps(
                    {
                        "method": method,
                        "params": [stream_name(s) for s in chunk],
                        "id": self._next_id,
                    }
                )
            )
            self._next_id += 1

    def _on_message(self, raw: str | bytes) -> None:
        """결합 스트림 메시지 1개를 캐시에 반영한다. 깊이가 아닌 프레임은 조용히 버린다."""
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        stream = msg.get("stream")
        data = msg.get("data")
        if not isinstance(stream, str) or not isinstance(data, dict):
            # 구독 응답 {"result":null,"id":1} 등 — 깊이가 아니면 수신으로 세지 않는다
            return
        symbol = stream.split("@", 1)[0].upper()
        try:
            asks = [[float(p), float(q)] for p, q in data["asks"]]
            bids = [[float(p), float(q)] for p, q in data["bids"]]
        except (KeyError, TypeError, ValueError):
            return
        at = now_ms()
        self._cache.put(symbol, asks, bids, at)
        self._cache.note_message(at)


class BinanceDepthStream:
    """샤드 3개 묶음. 기동·종료는 앱 lifespan 이 부른다 (010 snapshot 루프와 같은 자리)."""

    def __init__(
        self,
        *,
        cache: DepthCache,
        symbols: Callable[[], set[str]],
        connect: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._cache = cache
        self._shards = [_Shard(i, cache, symbols, connect) for i in range(SHARD_COUNT)]
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(s.run()) for s in self._shards]

    async def aclose(self) -> None:
        """취소하고 기다린 다음 소켓을 닫는다 — 종료 로그에 잔여 예외를 남기지 않는다 (§3.4)."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        # 소켓 3개는 동시에, 상한 안에 닫는다. 상대가 close 프레임을 안 돌려주면 하나당
        # 최대 20초를 기다려 배포의 docker stop(10초)보다 종료가 길어진다 — 남은 TCP 는
        # 프로세스가 끝날 때 OS 가 정리한다 (§3.4).
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*(shard.close() for shard in self._shards)),
                CLOSE_TIMEOUT,
            )
