"""Hyperliquid 무기한 선물(perp) 커넥터 — `/info` `meta`(10초) 목록 + 코인마다 `bbo`·`activeAssetCtx` 구독 3샤드 (스펙 047).

perp 원천 넷째 자리(`hyperliquid_perp`). 세 곳(046)과 규칙은 같지만 코드를 공유하지 않는다 — API 모양이 전혀 다르다:
목록은 POST `/info`(`{"type":"meta"}`, 시각 필드가 없어 본문 전체를 비교), 구독은 `coin` 이름으로 **한 메시지에 하나**,
펀딩은 **시간당**(주기 1, 다음 정시는 거래소가 주지 않아 수신 시각에서 계산), 핑은 JSON `{"method":"ping"}`.
가장 중요한 quirk — **목록에 없는 코인·소문자 코인을 구독하면 에러 프레임 없이 서버가 소켓을 닫는다**(그 소켓의 다른
구독까지 끊긴다). 그래서 `meta` 의 `name` 에 정확히 있는 코인만 구독한다. 구독 한도는 IP 당 1,000 이라 두 구독의 합이
900 을 넘으면 목록 순서 앞쪽만 구독한다(§3.3). 메시지마다: 원문 싱크 → 디코드 → PerpSink(행 제자리 갱신) →
그 샤드의 last_message_at(시세만). 심볼 집합 계약(PerpSymbolSource)도 이 커넥터가 구현한다.
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
from app.core.perp import PerpSink, split_multiplier
from app.core.ticks import STALE_AFTER_MS, StreamVerdict

logger = logging.getLogger("marketlens.stream.hyperliquid_perp")

WS_URL = "wss://api.hyperliquid.xyz/ws"
WS_SOURCE = "ws:/ws"
HANDSHAKE_SOURCE = "ws-handshake:/ws"
INFO_URL = "https://api.hyperliquid.xyz/info"
META_SOURCE = "rest:/info#meta"  # 같은 경로에 요청 종류가 본문에 있어 source 에 `#meta` 를 붙인다 (§3.2)
SYMBOLS_KEY = (
    "symbols:perp"  # 10초마다 오는 meta 본문의 원문 싱크 key — 분당 마지막 1건
)
FUNDING_INTERVAL_H = 1  # 펀딩은 매시간 — 목록에 주기가 없어 모든 코인 1 (§3.4)
HOUR_MS = 3_600_000
_BODY_LIMIT = 500  # 핸드셰이크 거부 응답 본문 상한 — 001 §3.1 과 같은 500자
_NAN = float("nan")

SHARDS = 3  # 연결 한도 10 안 — crc32(name) % 3, 한 코인의 두 구독은 같은 샤드 (§3.3)
SUBS_PER_COIN = 2  # bbo + activeAssetCtx (§3.4)
SUBSCRIPTION_LIMIT = (
    900  # IP 당 구독 1,000 한도의 여유 — 두 구독의 합이 이를 넘으면 앞쪽만 (§3.3)
)
SUBSCRIBE_INTERVAL = (
    0.05  # 구독 요청 간격 — 초당 20개(분당 1,200, 메시지 한도 2,000 의 60%) (§3.3)
)
REBALANCE_INTERVAL = 60.0  # set_universe 가 깨우지 않아도 이 주기로 구독 차이를 맞춘다
PING_AFTER_IDLE = 30.0  # 마지막 수신에서 이만큼 조용하면 `{"method":"ping"}` — 서버는 60초 무전송이면 끊는다 (§3.3)
PING_CHECK_INTERVAL = 5.0  # 조용한 시간을 이 간격으로 본다 — 핑은 30~35초 사이에 나간다
PONG_TIMEOUT = 10.0  # 이 안에 pong 이 없으면 끊고 재연결 — 조용히 죽은 TCP 감지 (§3.3)
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
CLOSE_TIMEOUT = 2.0  # 종료 시 취소 + 소켓 3개 동시 close 합계 상한
_PING = json.dumps({"method": "ping"})
_BBO = "bbo"
_CTX = "activeAssetCtx"


def _now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(coin: str) -> int:
    """코인 이름 → 샤드 번호. crc32 라 프로세스·재기동과 무관하게 같다. 이름은 대소문자 그대로(`kPEPE`) (§3.3)."""
    return zlib.crc32(coin.encode()) % SHARDS


def next_hour_ms(at_ms: int) -> int:
    """수신 시각의 다음 정시(UTC 시 경계) epoch ms — 경계 정각에 받은 메시지는 그다음 정시 (§3.4)."""
    return (at_ms // HOUR_MS + 1) * HOUR_MS


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다.

    라이브러리 keepalive(WebSocket ping 프레임)는 쓰지 않는다 — Hyperliquid 는 JSON ping 을 요구하고
    프레임 ping 은 문서에 없다 (§3.3).
    """
    return await ws_connect(url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=None)


class _Shard:
    """소켓 하나 = 샤드 하나. 배정은 코인 이름(`meta` 의 `name` 그대로), 구독은 코인마다 둘 (§3.3·§3.4)."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.state = StreamState(url=WS_URL)
        self.assigned: set[str] = set()  # 이 샤드가 구독해야 할 코인
        self.has_work = (
            asyncio.Event()
        )  # 배정이 생기면 set — 배정 없는 샤드는 연결하지 않는다
        self.subscribed: set[str] = (
            set()
        )  # 이 소켓에 실제로 구독된 코인 — 소켓이 바뀌면 비운다
        self.ws: Any | None = None
        self.task: asyncio.Task[None] | None = None
        self.ping_task: asyncio.Task[None] | None = None
        self.backoff = BACKOFF_START
        self.last_rx_at = (
            0  # 이 소켓에서 마지막으로 무엇이든 받은 시각(ms) — 핑 타이머의 기준 (§3.3)
        )
        self.pong_pending = False  # ping 을 보냈고 아직 pong 이 안 왔다
        self.pong_timed_out = (
            False  # 이번 연결이 pong 없음으로 끊겼다 — 실패 종류를 timeout 으로
        )
        self.lock = asyncio.Lock()  # 연결 직후 구독과 재조정 전송이 겹치지 않게


class HyperliquidPerpStream:
    id = "hyperliquid_perp"
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
        self._symbol_of: dict[str, str] = {}  # base → 코인 이름(`name` 그대로)
        self._base_of: dict[str, str] = {}  # 코인 이름 → base
        self._mult_of: dict[str, int] = {}  # 코인 이름 → 배수
        self._order: list[
            str
        ] = []  # 코인 이름의 `universe` 배열 순서 — 한도 초과 때 앞쪽을 고른다 (§3.3)
        self._truncated = 0  # 한도로 구독하지 못한 코인 수 — 바뀔 때만 경고 1줄
        self._wake = asyncio.Event()  # set_universe 가 재조정 루프를 깨운다
        self._rebalance: asyncio.Task[None] | None = None
        self.decode_failures = 0  # 버린 무효 프레임 수 — 그 자체로 실패가 아니다
        # 직전에 맵까지 만든 200 응답 본문 — 시각 필드가 없어 전체를 비교한다. 같으면 파싱·맵 재생성을 건너뛴다 (§3.2)
        self._symbols_body: bytes | None = None

    # --- 심볼 집합·펀딩 주기 (PerpSymbolSource, §3.2) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """`meta` 1회 → `isDelisted` 가 아닌 코인의 맵. 본문은 해석 전에 원문 싱크로(`symbols:perp`). 호출 수 1."""
        url = INFO_URL
        try:
            resp = await client.post(url, json={"type": "meta"})
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id,
                url,
                f"Hyperliquid 응답 시간 초과: {type(exc).__name__}: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"Hyperliquid 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        self._record(self.id, META_SOURCE, self._clock(), resp.text, SYMBOLS_KEY)
        if resp.status_code == 200 and resp.content == self._symbols_body:
            return 1
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"Hyperliquid 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_rest_status(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"Hyperliquid JSON 파싱 실패: {exc}",
                kind="bad_response",
            ) from exc
        # 존재하지 않는 요청에도 200·`null` 을 주는 API 다 — `universe` 가 없으면 응답 오류 (§3.2)
        items = None
        if isinstance(data, dict):
            items = data.get("universe")
        if not isinstance(items, list):
            raise ExchangeApiError(
                self.id,
                url,
                "Hyperliquid meta 에 universe 가 없다",
                body=resp.text,
                kind="bad_response",
            )
        symbol_of: dict[str, str] = {}
        mult_of: dict[str, int] = {}
        order: list[str] = []
        for item in items:
            if not isinstance(item, dict) or item.get("isDelisted"):
                continue  # 상장폐지 코인은 구독은 되지만 funding 0·midPx null 만 온다 (§3.2)
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            base, mult = split_multiplier(name)
            mult_of[name] = mult
            order.append(name)
            # 같은 base 로 정규화되는 코인이 둘이면 multiplier 1 을, 없으면 목록 순서의 첫 것 (046 §3.2)
            prev = symbol_of.get(base.upper())
            if prev is None or (mult == 1 and mult_of[prev] != 1):
                symbol_of[base.upper()] = name
        self._symbol_of = symbol_of
        self._base_of = {name: base for base, name in symbol_of.items()}
        self._mult_of = {name: mult_of[name] for name in self._base_of}
        self._order = [name for name in order if name in self._base_of]
        self._symbols_body = resp.content
        # 목록 갱신마다 전 코인에 주기 1 — 행이 있으면 바로, 없으면 생길 때 (§3.4)
        for base in self._base_of.values():
            self._sink.set_interval(self.id, base, FUNDING_INTERVAL_H)
        return 1

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
        """perp 우주 확정 → 샤드별 배정 교체. 빠진 코인의 행은 그 자리에서 지우고 재조정을 깨운다.

        우주 전체를 받으므로 자기 맵에 없는 base 는 무시한다. 매초 불리므로 배정이 안 바뀌면 아무것도 하지 않는다.
        두 구독의 합이 900 을 넘으면 `universe` 순서 앞쪽만 구독하고 경고 1줄 (§3.3).
        """
        wanted = {
            self._symbol_of[b]
            for b in (x.upper() for x in bases)
            if b in self._symbol_of
        }
        limit = SUBSCRIPTION_LIMIT // SUBS_PER_COIN
        if len(wanted) > limit:
            desired = set([name for name in self._order if name in wanted][:limit])
        else:
            desired = wanted
        truncated = len(wanted) - len(desired)
        if truncated != self._truncated:
            self._truncated = truncated
            if truncated:
                logger.warning(
                    "Hyperliquid 구독 한도 — 우주 %d코인 중 목록 앞쪽 %d코인만 구독한다(구독 %d개 상한, %d코인 제외)",
                    len(wanted),
                    len(desired),
                    SUBSCRIPTION_LIMIT,
                    truncated,
                )
        changed = False
        for shard in self._shards:
            mine = {name for name in desired if shard_of(name) == shard.index}
            if mine == shard.assigned:
                continue
            changed = True
            for name in shard.assigned - mine:
                self._store.remove_perp_row(self.id, self._base_of.get(name, name))
            shard.assigned = mine
            if mine:
                shard.has_work.set()
            else:
                shard.has_work.clear()
        if changed:
            self._wake.set()

    # --- 판정 (046 §3.8) ---

    def judge(self, now_ms: int) -> StreamVerdict | None:
        targets = [s for s in self._shards if s.assigned]
        if not targets:
            return None  # 우주가 아직 없다
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
                    message=f"Hyperliquid 스트림 끊김: 샤드 {shard.index}",
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
                        f"Hyperliquid 스트림 정체: 샤드 {shard.index} "
                        f"(구독 {len(shard.assigned)}종목) {STALE_AFTER_MS // 1000}초 이상 무수신"
                    ),
                    status_code=None,
                    url=WS_URL,
                ),
            )
        return StreamVerdict(ok=True)

    @staticmethod
    def _quiet_ms(shard: _Shard, now_ms: int) -> float:
        """조용한 시간 — 마지막 시세와 (연결 중이면) 구독 시각 중 최신부터. 둘 다 없으면 무한. pong·응답은 시세가 아니다."""
        state = shard.state
        marks = [state.last_message_at]
        if state.connected:
            marks.append(state.connected_since)
        known = [m for m in marks if m is not None]
        if not known:
            return float("inf")
        return now_ms - max(known)

    def _publish(self) -> None:
        """샤드 상태를 store.stream("hyperliquid_perp") 하나로 집계한다 (046 §3.1). last_error 는 judge 가 정한다.

        `subscribed` 는 구독한 코인 수다 — 구독 수(코인당 둘)의 절반 (§3.5).
        """
        active = [s for s in self._shards if s.assigned]
        self._state.connected = bool(active) and all(s.state.connected for s in active)
        received = [
            s.state.last_message_at for s in self._shards if s.state.last_message_at
        ]
        if received:
            self._state.last_message_at = max(received)
        else:
            self._state.last_message_at = None
        since = [
            s.state.connected_since for s in self._shards if s.state.connected_since
        ]
        if since:
            self._state.connected_since = min(since)
        else:
            self._state.connected_since = None
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
        tasks += [s.ping_task for s in self._shards if s.ping_task is not None]
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
            shard.ping_task = None
        self._rebalance = None
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*(self._close_socket(s) for s in self._shards)),
                max(0.0, deadline - time.monotonic()),
            )

    def _record_rejection(self, exc: BaseException) -> None:
        """핸드셰이크를 거부하며 준 HTTP 본문도 원문이다 — 해석 전에 싱크로 (001 §3.7)."""
        text = _response_text(getattr(exc, "response", None))
        if text:
            self._record(self.id, HANDSHAKE_SOURCE, self._clock(), text)

    async def _run_shard(self, shard: _Shard) -> None:
        while True:
            if not shard.assigned:
                await shard.has_work.wait()  # 배정이 없으면 연결하지 않는다
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
                    "Hyperliquid 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %s",
                    shard.index,
                    shard.backoff,
                    shard.state.last_error.message,
                )
                await self._sleep(shard.backoff)
                shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)
                continue
            shard.ws = ws
            shard.subscribed = set()
            shard.last_rx_at = self._clock()  # 핑 타이머는 소켓이 열린 시각부터 센다
            shard.pong_pending = False
            shard.pong_timed_out = False
            shard.state.connected = True
            # 구독을 보내는 동안은 소켓이 열린 시각을 임시로 — 직전 연결의 수신 시각으로 정체가 되지 않게
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
            except Exception as exc:
                if shard.pong_timed_out:
                    shard.state.last_error = StreamError(
                        kind="timeout",
                        message=f"Hyperliquid 스트림 pong 없음(샤드 {shard.index}): {PONG_TIMEOUT:.0f}초 안에 응답이 없어 끊었다",
                        status_code=None,
                        url=WS_URL,
                    )
                else:
                    # 없는 코인을 구독했을 때의 "말없는 닫힘" 도 여기로 온다 — network 로 보통의 재연결 (§3.3)
                    shard.state.last_error = _classify(exc, shard.index)
                    self._record_rejection(exc)
            finally:
                await self._stop_ping(shard)
                shard.state.connected = False
                shard.state.connected_since = None
                shard.state.subscribed = 0
                shard.subscribed = set()
                self._publish()  # close 가 늦어도 집계는 먼저 미연결이 된다
                await self._close_socket(shard)
            reason = ""
            if shard.state.last_error is not None:
                reason = shard.state.last_error.message
            logger.warning(
                "Hyperliquid 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
                shard.index,
                shard.backoff,
                reason,
            )
            await self._sleep(shard.backoff)
            shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)

    async def _run_rebalance(self) -> None:
        """set_universe 가 깨우거나 60초마다 — 연결된 샤드의 구독 차이를 맞춘다."""
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
                        "Hyperliquid 샤드 %d 재조정 전송 실패: %r", shard.index, exc
                    )

    async def _run_ping(self, shard: _Shard, ws: Any) -> None:
        """마지막 수신에서 30초가 지나면 `{"method":"ping"}` — pong 이 10초 안에 없으면 소켓을 닫아 펌프가 재연결하게 한다 (§3.3).

        시세든 응답이든 pong 이든 무엇이 오면 타이머가 그 시각부터 다시 센다 — 시세가 흐르는 샤드는 핑을 보내지 않는다.
        조용한 시간은 5초 간격으로 보므로 핑은 30~35초 사이에 나간다(서버가 끊는 60초 안).
        """
        idle_ms = int(PING_AFTER_IDLE * 1000)
        while True:
            await self._sleep(PING_CHECK_INTERVAL)
            if self._clock() - shard.last_rx_at < idle_ms:
                continue  # 아직 무엇인가 흐른다
            shard.pong_pending = True
            try:
                await ws.send(_PING)
            except Exception:
                return  # 죽은 소켓 — 펌프가 끊김을 처리한다
            await self._sleep(PONG_TIMEOUT)
            if shard.pong_pending:
                shard.pong_timed_out = True
                logger.warning(
                    "Hyperliquid 샤드 %d pong 없음 — %.0f초 안에 응답이 없어 끊고 재연결한다",
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
        if self._connect is not None:
            return await self._connect(WS_URL)
        return await open_socket(WS_URL)

    async def _close_socket(self, shard: _Shard) -> None:
        ws, shard.ws = shard.ws, None
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.close()

    async def _sync_shard(self, shard: _Shard) -> None:
        """배정과 실제 구독의 차이만 보낸다 — 해지 먼저, 한 메시지에 구독 하나, 초당 20개 (§3.3).

        배정은 `meta` 의 `name` 에 있는 코인뿐이다(set_universe 가 맵으로 걸렀다) — 없는 코인을 보내면
        서버가 소켓을 말없이 닫는다.
        """
        async with shard.lock:
            ws = shard.ws
            if ws is None:
                return
            wanted = set(shard.assigned)
            drop = sorted(shard.subscribed - wanted)
            add = sorted(wanted - shard.subscribed)
            for method, coins in (("unsubscribe", drop), ("subscribe", add)):
                for coin in coins:
                    for kind in (_BBO, _CTX):
                        await ws.send(
                            json.dumps(
                                {
                                    "method": method,
                                    "subscription": {"type": kind, "coin": coin},
                                }
                            )
                        )
                        await self._sleep(SUBSCRIBE_INTERVAL)
            if shard.ws is not ws:
                return  # 보내는 도중 소켓이 바뀌었다 — 죽은 소켓의 구독을 새 소켓 것으로 세지 않는다
            shard.subscribed = wanted
            shard.state.subscribed = len(wanted)
            self._publish()

    async def _pump(self, shard: _Shard, ws: Any) -> None:
        while True:
            raw = await ws.recv()
            at = self._clock()
            shard.last_rx_at = at  # 무엇이 왔든 핑 타이머는 여기서 다시 센다 (§3.3)
            if isinstance(raw, bytes | bytearray):
                try:
                    text = bytes(raw).decode("utf-8")
                except UnicodeDecodeError:
                    self.decode_failures += 1
                    continue
            else:
                text = str(raw)
            msg = _decode(text)
            # 디코드 뒤, 행·상태 갱신 전 — 받은 텍스트 그대로 + 시세 프레임이면 종류:코인 (001 §3.7)
            self._record(self.id, WS_SOURCE, at, text, _quote_key(msg))
            if msg is None:
                self.decode_failures += 1
                continue
            channel = msg.get("channel")
            if channel == "pong":
                shard.pong_pending = False
                continue
            if channel == "subscriptionResponse":
                continue  # 구독·해지 응답 — 시세로 세지 않는다 (§3.3)
            if channel == "error":
                # 깨진 요청에 대한 거부 — 연결은 유지된다. 기록만 남긴다 (§3.3)
                shard.state.last_error = StreamError(
                    kind="bad_request",
                    message=f"Hyperliquid 요청 거부(샤드 {shard.index}): {msg.get('data', '')}",
                    status_code=None,
                    url=WS_URL,
                )
                logger.warning("%s", shard.state.last_error.message)
                continue
            data = msg.get("data")
            if not isinstance(data, dict):
                self.decode_failures += 1
                continue
            try:
                if channel == _BBO:
                    self._on_bbo(data, at)
                elif channel == _CTX:
                    self._on_ctx(data, at)
                else:
                    self.decode_failures += 1
                    continue
            except (KeyError, TypeError, ValueError, IndexError):
                self.decode_failures += 1
                continue
            shard.state.last_message_at = at
            shard.backoff = BACKOFF_START  # 구독까지 성공했다는 증거 = 첫 시세 프레임
            # 시세 프레임이 바꾸는 집계값은 last_message_at 하나뿐이다 — 샤드를 다시 집계하지 않고 올리기만 한다
            last = self._state.last_message_at
            if last is None or at > last:
                self._state.last_message_at = at

    def _on_bbo(self, data: dict[str, Any], at: int) -> None:
        """`bbo[0]` 이 bid, `bbo[1]` 이 ask, `time` 이 호가 시각. null 인 쪽은 NaN(무효) — 호가 불변·수신은 센다 (§3.4)."""
        coin = str(data["coin"])
        base = self._base_of.get(coin)
        if base is None:
            return  # 맵에 없는 코인 — 버린다
        levels = data["bbo"]
        bid, bid_size = _level(levels[0])
        ask, ask_size = _level(levels[1])
        self._sink.quote(
            source=self.id,
            base=base,
            native_symbol=coin,
            multiplier=self._mult_of[coin],
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            quote_ts=int(data["time"]),
            received_at_ms=at,
        )

    def _on_ctx(self, data: dict[str, Any], at: int) -> None:
        """`funding` 은 시간당 비율, `markPx` 는 마크가. 다음 정산은 수신 시각의 다음 정시 — 거래소가 주지 않는다 (§3.4)."""
        coin = str(data["coin"])
        base = self._base_of.get(coin)
        if base is None:
            return
        ctx = data["ctx"]
        if not isinstance(ctx, dict):
            raise TypeError("ctx 가 객체가 아니다")
        self._sink.funding(
            source=self.id,
            base=base,
            multiplier=self._mult_of[coin],
            received_at_ms=at,
            funding_rate=_float_or_none(ctx.get("funding")),
            next_funding_ms=next_hour_ms(at),
            mark=_float_or_none(ctx.get("markPx")),
            funding_interval_h=FUNDING_INTERVAL_H,
        )


def _level(level: object) -> tuple[float, float]:
    """`{"px","sz","n"}` → (가격, 잔량). null(한쪽 호가 없음)은 NaN 둘 — PerpSink 가 무효로 본다 (§3.4)."""
    if not isinstance(level, dict):
        return _NAN, _NAN
    return float(level["px"]), float(level["sz"])


def _float_or_none(value: object) -> float | None:
    """숫자가 아니면(null 포함) None — 그 필드는 두고 나머지만 갱신한다 (§3.4)."""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out:
        return None  # NaN 도 "숫자 아님"
    return out


def _decode(text: str) -> dict[str, Any] | None:
    """프레임 텍스트 → JSON 객체. 객체가 아니거나 JSON 이 아니면 None(무효 프레임)."""
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    if not isinstance(msg, dict):
        return None
    return msg


def _quote_key(msg: dict[str, Any] | None) -> str | None:
    """원문 싱크의 `key` — 시세 프레임은 `bbo:<코인>`·`activeAssetCtx:<코인>`(원본 `name`), 그 밖은 None (§3.4)."""
    if not isinstance(msg, dict):
        return None
    channel = msg.get("channel")
    if channel not in (_BBO, _CTX):
        return None
    data = msg.get("data")
    coin = None
    if isinstance(data, dict):
        coin = data.get("coin")
    if not isinstance(coin, str) or not coin:
        return None
    return f"{channel}:{coin}"


def _classify(exc: BaseException, index: int) -> StreamError:
    """연결·핸드셰이크·끊김 분류 — 핸드셰이크 HTTP 거부는 Hyperliquid REST 규칙(§3.2)으로."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = f"Hyperliquid WebSocket 실패(샤드 {index}): {type(exc).__name__}: {exc}"
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
        return StreamError("network", message, None, WS_URL)  # DNS·거부·TLS·말없는 닫힘
    return StreamError("bad_response", message, None, WS_URL)


def _response_body(response: object) -> str | None:
    """핸드셰이크 거부 응답 본문 앞 500자 — 거래소가 뭐라고 했는지 이력에 남긴다 (011 §3.3)."""
    text = _response_text(response)
    if text is None:
        return None
    return text[:_BODY_LIMIT]


def _response_text(response: object) -> str | None:
    raw = getattr(response, "body", None)
    if not raw:
        return None
    if isinstance(raw, bytes | bytearray):
        return raw.decode("utf-8", "replace")
    return str(raw)


def _classify_rest_status(status: int) -> str:
    """Hyperliquid 규칙(§3.2): 429 한도초과, 5xx 장애, 그 외 4xx 요청오류. IP 차단 규칙은 문서에 없다."""
    if status == 429:
        return "rate_limit"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"
