"""바이낸스 USDT 무기한 선물(perp) 커넥터 — depth5@500ms 3샤드(`/public/ws`) + `!markPrice@arr@1s` 연결 1개(`/market/ws`)
+ exchangeInfo(10초)·fundingInfo(60초) REST (스펙 046 §3.5).

현물 커넥터(012)와 규칙은 같지만 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가. 다른 점: WebSocket 경로가
둘이다(호가는 `/public`, 마크가·펀딩은 `/market`), 로컬 북이 없다(500ms 마다 위 5단계 스냅샷에서 최우선 호가만),
펀딩 연결은 우주와 무관하게 늘 구독 하나이며 판정에 함께 든다(펀딩만 죽어도 원천 실패), 배수 심볼(`1000PEPE`)은
core.perp.split_multiplier 로 1코인 단위가 된다. 메시지마다: 원문 싱크 → 디코드 → PerpSink(행 제자리 갱신) →
그 샤드의 last_message_at(시세만). 심볼 집합 계약(PerpSymbolSource)도 이 커넥터가 구현한다.
"""

import asyncio
import contextlib
import json
import logging
import re
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

logger = logging.getLogger("marketlens.stream.binance_perp")

PUBLIC_WS_URL = "wss://fstream.binance.com/public/ws"  # 호가 (§3.5)
MARKET_WS_URL = "wss://fstream.binance.com/market/ws"  # 마크가·펀딩 (§3.5)
PUBLIC_SOURCE = "ws:/public/ws"
MARKET_SOURCE = "ws:/market/ws"
PUBLIC_HANDSHAKE_SOURCE = "ws-handshake:/public/ws"
MARKET_HANDSHAKE_SOURCE = "ws-handshake:/market/ws"
REST_URL = "https://fapi.binance.com"
EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
EXCHANGE_INFO_URL = REST_URL + EXCHANGE_INFO_PATH
FUNDING_INFO_PATH = "/fapi/v1/fundingInfo"
FUNDING_INFO_URL = REST_URL + FUNDING_INFO_PATH
SYMBOLS_KEY = (
    "symbols:perp"  # 10초마다 오는 exchangeInfo 본문의 원문 싱크 key — 분당 마지막 1건
)
FUNDING_KEY = "funding:all"  # 60초마다 오는 fundingInfo 본문의 key
MARK_KEY = "markPrice:all"  # 매초 오는 전 심볼 마크가 배열 프레임의 key
FUNDING_INFO_INTERVAL_MS = (
    60_000  # fundingInfo 는 /fundingRate 와 5분 500회를 나눠 쓴다 — 60초마다 (§3.5)
)
DEFAULT_INTERVAL_H = (
    8  # fundingInfo 응답에 없는 심볼의 주기 — 문서에 기본값 명문이 없어 가정 (§3.5)
)
_BODY_LIMIT = 500  # 핸드셰이크 거부 응답 본문 상한 — 001 §3.1 과 같은 500자
# exchangeInfo 에서 매 응답 바뀌는 것은 머리의 serverTime 하나다 — 이것만 빼고 직전 본문과 비교한다 (§3.5)
_SERVER_TIME = re.compile(rb'"serverTime":\s*\d+')
_SERVER_TIME_WITHIN = 256
_NAN = float("nan")

SHARDS = 3  # 호가 샤드 — crc32(symbol) % 3
FUNDING_SHARD = 3  # 펀딩 연결의 샤드 번호 (§3.5·§3.8)
DEPTH_STREAM = "depth5@500ms"
MARK_STREAM = "!markPrice@arr@1s"
STREAMS_PER_MESSAGE = 50  # SUBSCRIBE 한 메시지의 params 상한 (§3.5)
CONTROL_INTERVAL = 0.2  # 구독 요청 간격 — 연결당 초당 수신 10개 한도의 절반 (§3.5)
REBALANCE_INTERVAL = 60.0  # set_universe 가 깨우지 않아도 이 주기로 구독 차이를 맞춘다
PING_INTERVAL = (
    20.0  # 라이브러리 keepalive — 서버 ping 프레임엔 라이브러리가 자동 pong (§3.5)
)
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
CLOSE_TIMEOUT = 2.0  # 종료 시 취소 + 소켓 4개 동시 close 합계 상한
_CONTRACT = "PERPETUAL"
_TRADING = "TRADING"
_QUOTE = "USDT"


def _now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(symbol: str) -> int:
    """심볼 → 호가 샤드 번호. crc32 라 프로세스·재기동과 무관하게 같다 (§3.5)."""
    return zlib.crc32(symbol.upper().encode()) % SHARDS


def _stream_name(symbol: str) -> str:
    return f"{symbol.lower()}@{DEPTH_STREAM}"


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다."""
    return await ws_connect(
        url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=PING_INTERVAL
    )


class _SubscribeRejected(Exception):
    """`{"code":…,"msg":…}` 응답 → bad_request (§3.5)."""


class _Shard:
    """소켓 하나 = 샤드 하나. 0~2 는 호가(`/public`), 3 은 펀딩(`/market`, 구독 늘 하나) (§3.5)."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.is_funding = index == FUNDING_SHARD
        self.url = MARKET_WS_URL if self.is_funding else PUBLIC_WS_URL
        self.state = StreamState(url=self.url)
        # 이 샤드가 구독해야 할 스트림 — 호가 샤드는 심볼(대문자), 펀딩 샤드는 늘 MARK_STREAM 하나
        self.assigned: set[str] = {MARK_STREAM} if self.is_funding else set()
        self.has_work = (
            asyncio.Event()
        )  # 배정이 생기면 set — 배정 없는 샤드는 연결하지 않는다
        if self.is_funding:
            self.has_work.set()
        self.subscribed: set[str] = (
            set()
        )  # 이 소켓에 실제로 구독된 것 — 소켓이 바뀌면 비운다
        self.ws: Any | None = None
        self.task: asyncio.Task[None] | None = None
        self.backoff = BACKOFF_START
        self.next_id = 0
        self.lock = asyncio.Lock()  # 연결 직후 구독과 재조정 전송이 겹치지 않게


class BinancePerpStream:
    id = "binance_perp"
    url = PUBLIC_WS_URL

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
        self._state.url = PUBLIC_WS_URL
        self._sink = sink
        self._record = record
        self._connect = connect
        self._sleep = sleep
        self._clock = clock
        self._shards = [_Shard(i) for i in range(SHARDS + 1)]
        self._symbol_of: dict[str, str] = {}  # base → 심볼 (exchangeInfo symbol)
        self._base_of: dict[str, str] = {}  # 심볼 → base
        self._mult_of: dict[str, int] = {}  # 심볼 → 배수
        self._interval_of: dict[str, int] | None = (
            None  # 심볼 → 주기(시간) — fundingInfo 를 한 번도 못 받았으면 None
        )
        self._funding_fetched_at: int | None = None  # 마지막 fundingInfo 성공 시각(ms)
        self._wake = asyncio.Event()  # set_universe 가 재조정 루프를 깨운다
        self._rebalance: asyncio.Task[None] | None = None
        self.decode_failures = 0  # 버린 무효 프레임 수 — 그 자체로 실패가 아니다
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
        """perp 우주 확정 → 호가 샤드별 배정 교체. 빠진 심볼의 행은 그 자리에서 지우고 재조정을 깨운다.

        우주 전체를 받으므로 자기 맵에 없는 base 는 무시한다. 매초 불리므로 배정이 안 바뀌면 아무것도 하지 않는다.
        펀딩 샤드의 구독은 우주와 무관하다 (§3.5).
        """
        desired = {
            self._symbol_of[b]
            for b in (x.upper() for x in bases)
            if b in self._symbol_of
        }
        changed = False
        for shard in self._shards[:SHARDS]:
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
        quote_shards = [s for s in self._shards[:SHARDS] if s.assigned]
        if not quote_shards:
            return None  # 우주가 아직 없다 — 펀딩 연결만으로는 판정하지 않는다
        targets = quote_shards + [
            self._shards[FUNDING_SHARD]
        ]  # 펀딩만 죽어도 원천 실패
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
                    message=f"Binance perp 스트림 끊김: 샤드 {shard.index}",
                    status_code=None,
                    url=shard.url,
                ),
            )
        if self._quiet_ms(shard, now_ms) >= STALE_AFTER_MS:
            what = "펀딩" if shard.is_funding else f"구독 {len(shard.assigned)}종목"
            return StreamVerdict(
                ok=False,
                error=StreamError(
                    kind="stale_stream",
                    message=(
                        f"Binance perp 스트림 정체: 샤드 {shard.index} "
                        f"({what}) {STALE_AFTER_MS // 1000}초 이상 무수신"
                    ),
                    status_code=None,
                    url=shard.url,
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
        """샤드 상태를 store.stream("binance_perp") 하나로 집계한다 (§3.1). last_error 는 judge 가 정한다.

        `subscribed` 는 호가 샤드의 심볼 수만 — 펀딩 구독 하나는 심볼이 아니다.
        """
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
        self._state.subscribed = sum(
            s.state.subscribed for s in self._shards if not s.is_funding
        )

    # --- 수명 ---

    def start(self) -> None:
        for shard in self._shards:
            shard.task = asyncio.create_task(self._run_shard(shard))
        self._rebalance = asyncio.create_task(self._run_rebalance())

    async def aclose(self) -> None:
        """태스크 전부 취소 → 소켓 4개 동시 close, 합계 2초 상한."""
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

    def _record_rejection(self, shard: _Shard, exc: BaseException) -> None:
        """핸드셰이크를 거부하며 준 HTTP 본문도 원문이다 — 해석 전에 싱크로 (001 §3.7)."""
        text = _response_text(getattr(exc, "response", None))
        if text:
            source = (
                MARKET_HANDSHAKE_SOURCE if shard.is_funding else PUBLIC_HANDSHAKE_SOURCE
            )
            self._record(self.id, source, self._clock(), text)

    async def _run_shard(self, shard: _Shard) -> None:
        while True:
            if not shard.assigned:
                await shard.has_work.wait()  # 배정이 없으면 연결하지 않는다
                continue
            try:
                ws = await self._open(shard)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                shard.state.last_error = _classify(exc, shard)
                self._record_rejection(shard, exc)
                self._publish()
                logger.warning(
                    "바이낸스 perp 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %s",
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
            # 구독을 보내는 동안은 소켓이 열린 시각을 임시로 — 직전 연결의 수신 시각으로 정체가 되지 않게
            shard.state.connected_since = self._clock()
            self._publish()
            try:
                await self._sync_shard(shard)
                shard.state.connected_since = (
                    self._clock()
                )  # 구독 시각 = 첫 묶음을 다 보낸 시각
                self._publish()
                await self._pump(shard, ws)
            except asyncio.CancelledError:
                raise
            except _SubscribeRejected as exc:
                shard.state.last_error = StreamError(
                    kind="bad_request",
                    message=f"Binance perp 구독 거부(샤드 {shard.index}): {exc}",
                    status_code=None,
                    url=shard.url,
                )
            except Exception as exc:
                shard.state.last_error = _classify(exc, shard)
                self._record_rejection(shard, exc)
            finally:
                shard.state.connected = False
                shard.state.connected_since = None
                shard.state.subscribed = 0
                shard.subscribed = set()
                self._publish()  # close 가 늦어도 집계는 먼저 미연결이 된다
                await self._close_socket(shard)
            logger.warning(
                "바이낸스 perp 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
                shard.index,
                shard.backoff,
                shard.state.last_error.message if shard.state.last_error else "",
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
                        "바이낸스 perp 샤드 %d 재조정 전송 실패: %r", shard.index, exc
                    )

    async def _open(self, shard: _Shard) -> Any:
        factory = self._connect if self._connect is not None else open_socket
        return await factory(shard.url)

    async def _close_socket(self, shard: _Shard) -> None:
        ws, shard.ws = shard.ws, None
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.close()

    async def _sync_shard(self, shard: _Shard) -> None:
        """배정과 실제 구독의 차이만 보낸다 — UNSUBSCRIBE 먼저, 50 스트림씩, 0.2초 간격 (§3.5)."""
        async with shard.lock:
            ws = shard.ws
            if ws is None:
                return
            wanted = set(shard.assigned)
            drop = sorted(shard.subscribed - wanted)
            add = sorted(wanted - shard.subscribed)
            for method, names in (("UNSUBSCRIBE", drop), ("SUBSCRIBE", add)):
                params = [n if shard.is_funding else _stream_name(n) for n in names]
                for i in range(0, len(params), STREAMS_PER_MESSAGE):
                    shard.next_id += 1
                    await ws.send(
                        json.dumps(
                            {
                                "method": method,
                                "params": params[i : i + STREAMS_PER_MESSAGE],
                                "id": shard.next_id,
                            }
                        )
                    )
                    await self._sleep(CONTROL_INTERVAL)
            if shard.ws is not ws:
                return  # 보내는 도중 소켓이 바뀌었다 — 죽은 소켓의 구독을 새 소켓 것으로 세지 않는다
            shard.subscribed = wanted
            shard.state.subscribed = len(wanted)
            self._publish()

    async def _pump(self, shard: _Shard, ws: Any) -> None:
        source = MARKET_SOURCE if shard.is_funding else PUBLIC_SOURCE
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
            self._record(self.id, source, at, text, _quote_key(msg))
            if msg is None:
                self.decode_failures += 1
                continue
            if isinstance(msg, dict):
                if "error" in msg or ("code" in msg and "e" not in msg):
                    # 구독 거부 — `{"code":…,"msg":…}` (문서) 또는 `{"error":{…}}` (현물과 같은 꼴)
                    error = (
                        msg.get("error") if isinstance(msg.get("error"), dict) else msg
                    )
                    raise _SubscribeRejected(
                        f"{error.get('code')} {error.get('msg', '')}"
                    )
                if msg.get("e") != "depthUpdate":
                    continue  # {"result":null,"id":1} 등은 시세 수신으로 세지 않는다
                try:
                    self._on_depth(msg, at)
                except (KeyError, TypeError, ValueError, IndexError):
                    self.decode_failures += 1
                    continue
            else:
                # 배열 = `!markPrice@arr@1s` 한 프레임(전 심볼) — 우주 안 심볼만 반영한다 (§3.5)
                try:
                    self._on_mark(msg, at)
                except (KeyError, TypeError, ValueError):
                    self.decode_failures += 1
                    continue
            shard.state.last_message_at = at
            shard.backoff = BACKOFF_START  # 구독까지 성공했다는 증거 = 첫 시세 프레임
            # 시세 프레임이 바꾸는 집계값은 last_message_at 하나뿐이다 — 샤드를 다시 집계하지 않고 올리기만 한다
            last = self._state.last_message_at
            if last is None or at > last:
                self._state.last_message_at = at

    def _on_depth(self, msg: dict[str, Any], at: int) -> None:
        """depth5 스냅샷 → b[0]·a[0] 가 최우선 호가, T 가 호가 시각. 빈 쪽은 NaN(무효) — 행을 메우지 않는다 (§3.4)."""
        symbol = str(msg["s"]).upper()
        base = self._base_of.get(symbol)
        if base is None:
            return  # 맵에 없는 심볼 — 버린다
        bids, asks = msg["b"], msg["a"]
        bid, bid_size = (float(bids[0][0]), float(bids[0][1])) if bids else (_NAN, _NAN)
        ask, ask_size = (float(asks[0][0]), float(asks[0][1])) if asks else (_NAN, _NAN)
        self._sink.quote(
            source=self.id,
            base=base,
            native_symbol=symbol,
            multiplier=self._mult_of[symbol],
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            quote_ts=int(msg["T"]),
            received_at_ms=at,
        )

    def _on_mark(self, items: list[Any], at: int) -> None:
        for item in items:
            if not isinstance(item, dict) or item.get("e") != "markPriceUpdate":
                continue
            symbol = str(item["s"]).upper()
            base = self._base_of.get(symbol)
            if base is None:
                continue  # 맵에 없는 심볼(비우주·기간물) — 버린다
            self._sink.funding(
                source=self.id,
                base=base,
                multiplier=self._mult_of[symbol],
                received_at_ms=at,
                funding_rate=_float_or_none(item.get("r")),
                next_funding_ms=_int_or_none(item.get("T")),
                mark=_float_or_none(item.get("p")),
            )


def _float_or_none(value: object) -> float | None:
    """숫자가 아니면 None — 그 필드는 두고 나머지만 갱신한다 (§3.4)."""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN 도 "숫자 아님"


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _without_server_time(content: bytes) -> bytes:
    """비교용 바이트 — 머리의 `"serverTime":<ms>` 하나만 뺀 것. 없으면 본문 그대로."""
    m = _SERVER_TIME.search(content, 0, _SERVER_TIME_WITHIN)
    return content if m is None else content[: m.start()] + content[m.end() :]


def _decode(text: str) -> dict[str, Any] | list[Any] | None:
    """프레임 텍스트 → JSON 객체 또는 배열(마크가 프레임). 둘 다 아니거나 JSON 이 아니면 None(무효 프레임)."""
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict | list) else None


def _quote_key(msg: dict[str, Any] | list[Any] | None) -> str | None:
    """원문 싱크의 `key` — depth 프레임은 `depth5:<심볼>`, 마크가 배열은 `markPrice:all` (§3.5)."""
    if isinstance(msg, list):
        return MARK_KEY
    if isinstance(msg, dict) and msg.get("e") == "depthUpdate":
        symbol = str(msg.get("s") or "").upper()
        return f"depth5:{symbol}" if symbol else None
    return None


def _classify(exc: BaseException, shard: _Shard) -> StreamError:
    """연결·핸드셰이크·끊김 분류 — 핸드셰이크 HTTP 거부는 바이낸스 REST 규칙(§3.5)으로."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = (
        f"Binance perp WebSocket 실패(샤드 {shard.index}): {type(exc).__name__}: {exc}"
    )
    if isinstance(status, int):
        # Retry-After 는 문서에 없다 — retry_after_sec 는 null
        return StreamError(
            _classify_rest_status(status),
            message,
            status,
            shard.url,
            None,
            _response_body(response),
        )
    if isinstance(exc, TimeoutError):
        return StreamError("timeout", message, None, shard.url)
    if isinstance(exc, OSError | ConnectionClosed):
        return StreamError("network", message, None, shard.url)  # DNS·거부·TLS·끊김
    return StreamError("bad_response", message, None, shard.url)


def _response_body(response: object) -> str | None:
    """핸드셰이크 거부 응답 본문 앞 500자 — 거래소가 뭐라고 했는지 이력에 남긴다 (011 §3.3)."""
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
