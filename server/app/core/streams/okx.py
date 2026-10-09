"""OKX 스트림 커넥터 — WebSocket books5(스냅샷)·trades 3샤드 + instruments REST (스펙 045).

바이낸스(012)·바이빗(019)·비트겟(020)과 규칙은 같지만 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
비트겟과 다른 점: `books5` 는 스냅샷만 와서 **로컬 북이 없다**(매 메시지가 행을 통째 다시 만든다), 구독 인자에
`instType` 이 없고(`{channel, instId}`), 핑은 주기가 아니라 **마지막 수신에서 20초가 지나면** 문자열 `ping`,
pong 은 10초 안에 와야 하며, `event:"notice"` 는 서비스 업그레이드 통지라 경고 1줄만 남긴다. 구독·해지 요청은
연결당 시간당 480회 예산 — 80% 를 넘으면 재조정을 다음 회차로 미룬다. instruments 봉투에는 매 응답 바뀌는
시각 필드가 없어 본문 전체 바이트로 직전 응답과 비교한다.
심볼은 crc32 % 3 으로 샤드에 고정 배정되고, 샤드마다 소켓·시계·백오프·예산을 따로 둔다.
메시지마다: 원문 싱크 기록 → 디코드 → 행 갱신(QuoteSink) → 그 샤드의 last_message_at(시세만).
심볼 집합 계약(ForeignSymbolSource)도 이 커넥터가 구현한다 — 우주가 확정되면 차이만 재조정한다.
"""

import asyncio
import contextlib
import json
import logging
import math
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

logger = logging.getLogger("marketlens.stream.okx")

# 포트를 적지 않는다 — `:8443` 은 2026-10-31 에 닫힌다 (§3.2)
WS_URL = "wss://ws.okx.com/ws/v5/public"
WS_SOURCE = "ws:/ws/v5/public"
HANDSHAKE_SOURCE = "ws-handshake:/ws/v5/public"  # 핸드셰이크 거부 응답 본문의 원문 싱크 source (010 §3.1)
REST_URL = "https://openapi.okx.com"  # 2026-05-20 부터 문서가 쓰는 도메인 (§3.3)
INSTRUMENTS_PATH = "/api/v5/public/instruments"
INSTRUMENTS_URL = REST_URL + INSTRUMENTS_PATH + "?instType=SPOT"
SYMBOLS_KEY = "symbols:all"  # 매초 오는 심볼 목록 본문의 원문 싱크 key — 분당 마지막 1건 (001 §3.7)
_BODY_LIMIT = 500  # 핸드셰이크 거부 응답 본문 상한 — 001 §3.1 과 같은 500자
_INF = float("inf")

BOOKS_CHANNEL = "books5"  # 로그인 없이 받을 수 있는 가장 깊은 스냅샷 채널 — 5단계, 변화 때 100ms (§3.2)
TRADE_CHANNEL = "trades"
SHARDS = 3
ARGS_PER_MESSAGE = 50  # 요청 하나의 args 상한 — 원소 약 45바이트 × 50 = 2.3KB, 64KB 한도 안. 개수 한도는 문서에 없어 비트겟과 같게 (§3.3)
ARGS_BYTES_LIMIT = 64 * 1024  # 한 구독 요청의 args 총 길이 상한 — 공식 문서 (§3.2)
CONTROL_INTERVAL = 0.2  # 구독 요청 사이 대기 (§3.3)
REBALANCE_INTERVAL = 60.0  # set_universe 가 깨우지 않아도 이 주기로 구독 차이를 맞춘다
PING_AFTER_IDLE = 20.0  # 마지막 수신에서 이만큼 조용하면 문자열 ping — 서버는 30초 무데이터면 끊는다 (§3.2)
PING_CHECK_INTERVAL = 5.0  # 조용한 시간을 이 간격으로 본다 — 핑은 20~25초 사이에 나간다
PONG_TIMEOUT = 10.0  # 이 안에 pong 이 없으면 끊고 재연결 — 조용히 죽은 TCP 감지 (§3.2)
SUBSCRIBE_BUDGET_PER_HOUR = 480  # 연결당 구독·해지 요청 한도 — 공식 문서 (§3.2)
SUBSCRIBE_WARN_AT = (
    SUBSCRIBE_BUDGET_PER_HOUR * 80 // 100
)  # 384 — 넘으면 경고 1줄·재조정 연기 (§3.3)
BUDGET_WINDOW_MS = 3_600_000
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
CLOSE_TIMEOUT = 2.0  # 종료 시 취소 + 소켓 3개 동시 close 합계 상한
_QUOTE = "USDT"
_OK_CODE = "0"  # 봉투 code — HTTP 200 이어도 이 값이 아니면 실패 (§3.3·§3.8)
_RATE_LIMIT_CODE = "50011"  # "Rate limit reached" — 문서가 200·429 양쪽에 둔다 (§3.8)
_LIVE = "live"
_PING = "ping"
_PONG = "pong"
# 수신 루프는 프레임마다(초당 수백 건) 돈다 — 형식 검사에 `bytes | bytearray` 를 쓰면 평가할 때마다
# types.UnionType 을 새로 만든다. 튜플 상수 하나로 둔다
_BYTES_TYPES = (bytes, bytearray)
# json.loads 는 인자 형식·BOM 검사를 거쳐 기본 디코더로 넘긴다. 프레임 텍스트는 늘 str 이라 기본 디코더의
# decode 를 바로 부른다 — 훅 없는 기본 설정 그대로라 결과도, 깨진 JSON·BOM 에서 나는 ValueError 도 같다
_JSON_DECODE = json.JSONDecoder().decode
_BOOK_KEY = "orderbook:"  # 원문 싱크 key 머리 — 뒤에 instId (001 §3.7)
_TRADE_KEY = "trade:"


def _now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(symbol: str) -> int:
    """심볼(instId) → 샤드 번호. crc32 라 프로세스·재기동과 무관하게 같다 (§3.3)."""
    return zlib.crc32(symbol.upper().encode()) % SHARDS


def _args(symbol: str) -> list[dict[str, str]]:
    # 호가·체결 채널은 instType 을 받지 않는다 — instId 만 (§3.2)
    return [
        {"channel": BOOKS_CHANNEL, "instId": symbol},
        {"channel": TRADE_CHANNEL, "instId": symbol},
    ]


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다.

    라이브러리 keepalive(WebSocket ping 프레임)는 쓰지 않는다 — OKX 는 문자열 ping 을 요구하고
    프레임 ping 은 문서에 없다 (§3.2).
    """
    return await ws_connect(url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=None)


class _SubscribeRejected(Exception):
    """`event:"error"` 응답 → bad_request (§3.2)."""


class _Shard:
    """소켓 하나 = 샤드 하나. 시계·백오프·구독 집합·구독 예산을 샤드마다 따로 둔다 (§3.5). 북은 없다 (§3.4)."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.state = StreamState(url=WS_URL)
        self.assigned: set[str] = set()  # 이 샤드가 구독해야 할 심볼(instId)
        self.has_work = (
            asyncio.Event()
        )  # 배정이 생기면 set — 배정 없는 샤드는 연결하지 않는다
        self.subscribed: set[str] = (
            set()
        )  # 이 소켓에 실제로 구독된 심볼 — 소켓이 바뀌면 비운다
        self.requests_at: deque[int] = (
            deque()
        )  # 이 소켓으로 보낸 구독·해지 요청 시각(ms) — 새 연결은 예산도 새로 (§3.2)
        self.budget_warned = False  # 예산 소진 경고를 이미 냈다 — 회복될 때까지 한 번만
        self.ws: Any | None = None
        self.task: asyncio.Task[None] | None = None
        self.ping_task: asyncio.Task[None] | None = None
        self.backoff = BACKOFF_START
        self.last_rx_at = (
            0  # 이 소켓에서 마지막으로 무엇이든 받은 시각(ms) — 핑 타이머의 기준 (§3.2)
        )
        self.pong_pending = False  # ping 을 보냈고 아직 pong 이 안 왔다
        self.pong_timed_out = (
            False  # 이번 연결이 pong 없음으로 끊겼다 — 실패 종류를 timeout 으로
        )
        self.lock = asyncio.Lock()  # 연결 직후 구독과 재조정 전송이 겹치지 않게


class OkxStream:
    id = "okx"
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
        self._symbol_of: dict[str, str] = {}  # base → instId (instruments baseCcy)
        self._base_of: dict[str, str] = {}  # instId → base
        self._last_trade_ts: dict[
            str, int
        ] = {}  # base → 마지막으로 실은 체결 ts (§3.4)
        self._book_ts: dict[
            str, int
        ] = {}  # instId → 마지막으로 실은 호가 시각 — 더 오래된 스냅샷은 버린다 (§3.4)
        self._wake = asyncio.Event()  # set_universe 가 재조정 루프를 깨운다
        self._rebalance: asyncio.Task[None] | None = None
        self.decode_failures = 0  # 버린 무효 프레임 수 — 그 자체로 실패가 아니다
        # 직전에 맵까지 만든 200 응답의 본문 전체 바이트 — 같으면 파싱·맵 재생성을 건너뛴다 (§3.3)
        self._symbols_body: bytes | None = None
        # 그 본문을 디코드한 텍스트와 그때의 문자 인코딩 — 같은 바이트·같은 인코딩이면 디코드 결과도 같으므로
        # 매초 1.2MB 를 다시 디코드하지 않고 이 텍스트를 원문 싱크에 넘긴다(값이 같아 S3 바이트도 같다)
        self._symbols_text: str | None = None
        self._symbols_encoding: str | None = None
        # 직전 set_universe 가 반영한 (심볼 맵, 우주 사본) — 맵 객체가 그대로이고 우주가 같으면 배정도 같다
        self._applied: tuple[dict[str, str], frozenset[str]] | None = None

    # --- 심볼 집합 (ForeignSymbolSource, §3.3) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """instruments 1회 → live·USDT 심볼 맵. 응답 본문은 해석 전에 원문 싱크로(`symbols:all`).

        HTTP 200 이어도 `code != "0"` 이면 실패다 (§3.8). 원문 기록은 매 응답 하고, 200 본문의 바이트가
        직전에 맵을 만든 응답과 같으면 파싱·맵 재생성을 건너뛴다 — OKX 봉투에는 매 응답 바뀌는 시각 필드가
        없어 본문 전체로 비교한다. 바이트가 같으면 맵도 같으므로 낡을 여지가 없다 (§3.3).
        """
        url = INSTRUMENTS_URL
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"OKX 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"OKX 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        # 수신 시각은 본문 비교·디코드보다 먼저 읽는다 — 원문 싱크는 이 시각으로 분 창을 고르므로(010),
        # 디코드 시간만큼 늦게 찍으면 분 경계에서 다른 창에 들어갈 수 있다
        at = self._clock()
        content = resp.content
        # 파싱을 건너뛸지는 본문 바이트만으로 정한다 (§3.3). 문자 인코딩은 텍스트를 다시 쓸지에만 본다 —
        # 바이트가 같아도 charset 헤더가 바뀌면 디코드 결과가 달라질 수 있다
        same = resp.status_code == 200 and content == self._symbols_body
        if same and resp.encoding == self._symbols_encoding:
            text = self._symbols_text
        else:
            text = resp.text
        self._record(self.id, f"rest:{INSTRUMENTS_PATH}", at, text, SYMBOLS_KEY)
        if same:
            return 1
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"OKX 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=text,
                kind=_classify_rest_status(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"OKX JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if not isinstance(data, dict):
            raise ExchangeApiError(self.id, url, "OKX 응답이 객체가 아니다", body=text)
        code = data.get("code")
        if code != _OK_CODE:
            raise ExchangeApiError(
                self.id,
                url,
                f"OKX code {code}: {data.get('msg', '')}",
                status_code=resp.status_code,
                body=text,
                kind=_classify_rest_code(str(code)),
            )
        items = data.get("data")
        if not isinstance(items, list):
            raise ExchangeApiError(
                self.id, url, "OKX instruments 에 data 가 없다", body=text
            )
        symbol_of: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            # preopen·suspend·test·post_only 등은 빠진다 — 상장 공지 직후의 빈 행도 state 가 live 가 아니다 (§3.3)
            if item.get("state") != _LIVE or item.get("quoteCcy") != _QUOTE:
                continue
            base = str(item.get("baseCcy") or "").upper()
            symbol = str(item.get("instId") or "")
            if not base or not symbol:
                continue
            symbol_of.setdefault(base, symbol)  # 둘 이상이면 처음 것
        self._symbol_of = symbol_of
        self._base_of = {symbol: base for base, symbol in symbol_of.items()}
        # 맵을 만든 회차에만 셋을 함께 기억한다 — 실패한 본문은 기억하지 않는다 (§3.3)
        self._symbols_body = content
        self._symbols_text = text
        self._symbols_encoding = resp.encoding
        return 1

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
        """우주 확정 → 샤드별 배정 교체. 빠진 심볼의 행은 그 자리에서 지우고 재조정을 깨운다.

        우주 전체를 받으므로 자기 맵에 없는 base 는 무시한다(다른 해외에만 있는 코인).
        매초 불리므로 배정이 하나도 안 바뀌면 아무것도 하지 않는다 (§3.3).

        배정은 (심볼 맵, 우주)만으로 정해지고 샤드의 배정을 바꾸는 곳은 이 함수 하나다. 맵은 refresh 가 목록이
        바뀐 회차에만 새 객체로 바꾸고 제자리에서 고치지 않는다. 그래서 맵 객체가 직전 호출 때 그대로이고 우주가
        같으면 아래 계산은 어차피 아무것도 바꾸지 못한다 — 매초 오는 같은 우주는 비교 한 번으로 돌아간다(우주 확정).
        """
        symbol_of = self._symbol_of
        applied = self._applied
        if applied is not None and applied[0] is symbol_of and applied[1] == bases:
            return
        # 우주를 한 번 훑으며 샤드별로 나눈다 — 심볼마다 crc32 를 한 번만 계산한다
        mine_of: list[set[str]] = [set() for _ in self._shards]
        for b in bases:
            symbol = symbol_of.get(b.upper())
            if symbol is not None:
                mine_of[shard_of(symbol)].add(symbol)
        changed = False
        for shard in self._shards:
            mine = mine_of[shard.index]
            if mine == shard.assigned:
                continue
            changed = True
            for symbol in shard.assigned - mine:
                base = self._base_of.get(symbol, symbol)
                self._store.remove_row(self.id, base)
                self._last_trade_ts.pop(base, None)
                self._book_ts.pop(symbol, None)
            shard.assigned = mine
            if mine:
                shard.has_work.set()
            else:
                shard.has_work.clear()
        if changed:
            self._wake.set()
        # 우주는 사본으로 든다 — 호출한 쪽이 같은 집합 객체를 고쳐 다시 넘겨도 바뀐 것으로 본다.
        # 맵은 참조로 든다 — 참조를 쥐고 있어야 옛 맵이 해제되지 않아 같은 id 의 새 맵과 헷갈리지 않는다
        self._applied = (symbol_of, frozenset(bases))

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
                    message=f"OKX 스트림 끊김: 샤드 {shard.index}",
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
                        f"OKX 스트림 정체: 샤드 {shard.index} "
                        f"(구독 {len(shard.assigned)}종목) {STALE_AFTER_MS // 1000}초 이상 무수신"
                    ),
                    status_code=None,
                    url=WS_URL,
                ),
            )
        return StreamVerdict(ok=True)

    @staticmethod
    def _quiet_ms(shard: _Shard, now_ms: int) -> float:
        """조용한 시간 — 마지막 시세와 (연결 중이면) 구독 시각 중 최신부터. 둘 다 없으면 무한. pong 은 시세가 아니다."""
        state = shard.state
        marks = [state.last_message_at]
        if state.connected:
            marks.append(state.connected_since)
        known = [m for m in marks if m is not None]
        if not known:
            return float("inf")
        return now_ms - max(known)

    def _publish(self) -> None:
        """샤드 상태를 store.stream("okx") 하나로 집계한다 (§3.5). last_error 는 judge 가 정한다."""
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
        """OKX 가 핸드셰이크를 거부하며 준 HTTP 본문도 원문이다 — 해석 전에 싱크로 (001 §3.7)."""
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
                    "OKX 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %s",
                    shard.index,
                    shard.backoff,
                    shard.state.last_error.message,
                )
                await self._sleep(shard.backoff)
                shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)
                continue
            shard.ws = ws
            shard.subscribed = set()
            shard.requests_at.clear()  # 새 연결은 구독 예산도 새로 (§3.2)
            shard.budget_warned = False
            shard.last_rx_at = self._clock()  # 핑 타이머는 소켓이 열린 시각부터 센다
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
                    message=f"OKX 구독 거부(샤드 {shard.index}): {exc}",
                    status_code=None,
                    url=WS_URL,
                )
            except Exception as exc:
                if shard.pong_timed_out:
                    shard.state.last_error = StreamError(
                        kind="timeout",
                        message=f"OKX 스트림 pong 없음(샤드 {shard.index}): {PONG_TIMEOUT:.0f}초 안에 응답이 없어 끊었다",
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
                self._publish()  # close 가 늦어도 집계는 먼저 미연결이 된다
                await self._close_socket(shard)
            reason = ""
            if shard.state.last_error is not None:
                reason = shard.state.last_error.message
            logger.warning(
                "OKX 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
                shard.index,
                shard.backoff,
                reason,
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
                    logger.warning("OKX 샤드 %d 재조정 전송 실패: %r", shard.index, exc)

    async def _run_ping(self, shard: _Shard, ws: Any) -> None:
        """마지막 수신에서 20초가 지나면 문자열 ping — pong 이 10초 안에 없으면 소켓을 닫아 펌프가 재연결하게 한다 (§3.2).

        시세든 응답이든 pong 이든 무엇이 오면 타이머가 그 시각부터 다시 센다 — 시세가 흐르는 샤드는 핑을 보내지 않는다.
        조용한 시간은 5초 간격으로 보므로 핑은 20~25초 사이에 나간다(서버 기준 30초 안).
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
                    "OKX 샤드 %d pong 없음 — %.0f초 안에 응답이 없어 끊고 재연결한다",
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

    def _budget_left(self, shard: _Shard) -> bool:
        """시간당 구독·해지 요청 예산의 80% 안인가 — 넘었으면 경고 1줄, 이번 재조정은 미룬다 (§3.3)."""
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
                "OKX 샤드 %d 구독·해지 요청이 시간당 %d회(예산 %d회의 80%%)에 닿았다 — 재조정을 다음 회차로 미룬다",
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
        """프레임 하나 = 판별 → 원문 싱크 기록 → (시세면) 행 갱신 (§3.2·001 §3.7).

        초당 수백 건이 지나는 길이라 프레임을 한 번만 훑는다 — `arg` 를 한 번 읽어 원문 key 와 처리 분기에
        같이 쓰고, 디코드·key 계산을 따로 함수로 부르지 않는다. 판별 순서·key 규칙·기록 시점은 그대로다:
        원문은 언제나 응답·거부 처리와 행 갱신보다 먼저 남긴다(거부 프레임도 원문이 남은 뒤에 끊긴다).
        """
        while True:
            raw = await ws.recv()
            at = self._clock()
            shard.last_rx_at = at  # 무엇이 왔든 핑 타이머는 여기서 다시 센다 (§3.2)
            if type(raw) is str:
                text = raw  # 소켓은 텍스트 프레임을 str 로 준다 — 가장 흔한 길을 먼저
            elif isinstance(raw, _BYTES_TYPES):
                try:
                    text = bytes(raw).decode("utf-8")
                except UnicodeDecodeError:
                    self.decode_failures += 1
                    continue
            else:
                text = str(raw)  # str 하위형 등 — 원문 싱크에는 정확히 str 로 넘긴다
            if text == _PONG:
                # 문자열 pong — 시세도 디코드 실패도 아니다 (§3.2)
                self._record(self.id, WS_SOURCE, at, text)
                shard.pong_pending = False
                continue
            try:
                msg = _JSON_DECODE(text)
            except ValueError:
                msg = None
            if not isinstance(msg, dict):
                # 깨진 JSON·객체 아님 — 원문만 남기고(key 없음) 무효 프레임으로 센다
                self._record(self.id, WS_SOURCE, at, text, None)
                self.decode_failures += 1
                continue
            arg = msg.get("arg")
            if "data" not in msg or not isinstance(arg, dict):
                # 시세 모양이 아니다 — key 없음. 구독·해지 응답·거부·통지면 처리하고, 아니면 무효 프레임 (§3.2)
                self._record(self.id, WS_SOURCE, at, text, None)
                if "event" in msg:
                    self._on_event(shard, msg)
                else:
                    self.decode_failures += 1
                continue
            # 시세 모양 — 받은 텍스트 그대로 + 종류:심볼(원본 instId) key 로, 행·상태 갱신 전에 (001 §3.7)
            channel, symbol = arg.get("channel"), str(arg.get("instId") or "")
            if not symbol:
                key = None
            elif channel == BOOKS_CHANNEL:
                key = _BOOK_KEY + symbol
            elif channel == TRADE_CHANNEL:
                key = _TRADE_KEY + symbol
            else:
                key = None
            self._record(self.id, WS_SOURCE, at, text, key)
            if "event" in msg:
                # 시세 모양이어도 event 가 붙었으면 응답이다 — 원문(key 포함)만 남고 시세로 세지 않는다 (§3.2)
                self._on_event(shard, msg)
                continue
            base = self._base_of.get(symbol)
            if base is None:
                continue  # 맵에 없는 심볼 — 버린다 (§3.4)
            try:
                if channel == BOOKS_CHANNEL:
                    self._on_books(symbol, base, msg["data"], at)
                elif channel == TRADE_CHANNEL:
                    self._on_trade(base, msg["data"])
                else:
                    continue  # 구독하지 않은 채널
            except (KeyError, TypeError, ValueError, IndexError):
                self.decode_failures += 1
                continue
            shard.state.last_message_at = at
            shard.backoff = BACKOFF_START  # 구독까지 성공했다는 증거 = 첫 시세 프레임
            # 시세 프레임이 바꾸는 집계값은 last_message_at 하나뿐이다 — 샤드 3개를 다시 집계하지 않고
            # 올리기만 한다. 연결·끊김·구독 변경은 그 자리에서 _publish 가 전체를 다시 집계한다 (§3.5)
            last = self._state.last_message_at
            if last is None or at > last:
                self._state.last_message_at = at

    def _on_event(self, shard: _Shard, msg: dict[str, Any]) -> None:
        """구독·해지 응답 — 시세로 세지 않는다. error 는 구독 거부, notice 는 업그레이드 통지 (§3.2)."""
        event = msg["event"]
        if event == "error":
            raise _SubscribeRejected(f"code {msg.get('code')}: {msg.get('msg', '')}")
        if event == "notice":
            logger.warning(
                "OKX 샤드 %d 서비스 통지: code %s: %s",
                shard.index,
                msg.get("code"),
                msg.get("msg", ""),
            )

    def _on_books(self, symbol: str, base: str, data: Any, at: int) -> None:
        """books5 스냅샷 1건 → 그 심볼의 행을 다시 만든다. 로컬 북은 없다 (§3.4).

        원소는 `[가격, 잔량, "0"(폐기 필드), 주문 수]` — 앞 둘만 쓴다. 호가 시각이 지금 행보다 오래된 스냅샷은
        버리고(원문·수신 시각에는 이미 남았다), asks·bids 가 둘 다 비면 행을 지우지 않고 무시한다. seqId 는 쓰지 않는다.
        """
        item = data[0]  # data 는 원소 1개 (§3.2)
        ts = int(item["ts"])
        if ts < self._book_ts.get(symbol, -1):
            return  # 순서 뒤바뀜 — 더 오래된 스냅샷은 버린다 (§3.4)
        raw_asks, raw_bids = item["asks"], item["bids"]
        if not raw_asks and not raw_bids:
            return  # 빈 북 하트비트 — 행을 지우지 않는다 (§3.4)
        asks = [[float(lv[0]), float(lv[1])] for lv in raw_asks]
        bids = [[float(lv[0]), float(lv[1])] for lv in raw_bids]
        if not (_strictly_ascending(asks) and _strictly_descending(bids)):
            # 깨졌거나 가격이 겹치거나 NaN — 정렬하고 같은 가격은 뒤 것을 남긴다 (§3.4)
            asks = _sorted_levels(asks, reverse=False)
            bids = _sorted_levels(bids, reverse=True)
        self._book_ts[symbol] = ts
        self._sink.orderbook(
            exchange=self.id,
            base=base,
            quote=_QUOTE,
            native_symbol=symbol,
            asks=asks,
            bids=bids,
            timestamp_ms=ts,
            received_at_ms=at,
        )

    def _on_trade(self, base: str, data: Any) -> None:
        """한 프레임의 체결 중 `ts` 가 가장 큰 것 — 배열 순서를 믿지 않는다. 이미 실은 것보다 엄격히 오래되면 무시 (§3.4)."""
        if not isinstance(data, list) or not data:
            raise ValueError("trades data 가 비었다")
        latest = max(data, key=lambda t: int(t["ts"]))
        ts = int(latest["ts"])
        if ts < self._last_trade_ts.get(base, -1):
            return  # 과거 체결 — 값이 뒤로 가지 않는다. 같은 ts 는 최신으로 싣는다
        self._last_trade_ts[base] = ts
        self._sink.trade(
            exchange=self.id,
            base=base,
            price=float(latest["px"]),
            price_timestamp=ts,
        )


def _strictly_ascending(levels: list[list[float]]) -> bool:
    """가격이 앞 단계보다 엄격히 큰가 — NaN 은 비교가 거짓이라 정렬 안 됨으로 본다."""
    prev = -_INF
    for price, _ in levels:
        if not prev < price:
            return False
        prev = price
    return True


def _strictly_descending(levels: list[list[float]]) -> bool:
    """가격이 앞 단계보다 엄격히 작은가 — NaN 은 비교가 거짓이라 정렬 안 됨으로 본다."""
    prev = _INF
    for price, _ in levels:
        if not prev > price:
            return False
        prev = price
    return True


def _sorted_levels(levels: list[list[float]], *, reverse: bool) -> list[list[float]]:
    """가격으로 정렬, 같은 가격은 뒤 것이 남는다 — dict 는 뒤 대입이 이긴다 (§3.4). NaN 가격은 비교 불가라 버린다."""
    merged = {p: q for p, q in levels if not math.isnan(p)}
    return sorted(
        ([p, q] for p, q in merged.items()), key=lambda lv: lv[0], reverse=reverse
    )


def _classify(exc: BaseException, shard: int) -> StreamError:
    """연결·핸드셰이크·끊김 분류 — 핸드셰이크 HTTP 거부는 OKX REST 규칙(§3.8)으로."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = f"OKX WebSocket 실패(샤드 {shard}): {type(exc).__name__}: {exc}"
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
    if text is None:
        return None
    return text[:_BODY_LIMIT]


def _response_text(response: object) -> str | None:
    """핸드셰이크 거부 응답 본문 전문 — 거래소가 준 것이라 원문 싱크에 그대로 넘긴다 (001 §3.7)."""
    raw = getattr(response, "body", None)
    if not raw:
        return None
    if isinstance(raw, bytes | bytearray):
        return raw.decode("utf-8", "replace")
    return str(raw)


def _classify_rest_status(status: int) -> str:
    """OKX 규칙(§3.8): 403 차단, 429 한도초과, 5xx 장애, 그 외 4xx 요청오류."""
    if status == 403:
        return "banned"
    if status == 429:
        return "rate_limit"
    if 500 <= status < 600:
        return "unavailable"
    if 400 <= status < 500:
        return "bad_request"
    return "bad_response"


def _classify_rest_code(code: str) -> str:
    """HTTP 200 인데 봉투 code 가 실패인 경우(§3.8): 50011 은 한도초과, 그 외는 bad_response."""
    if code == _RATE_LIMIT_CODE:
        return "rate_limit"
    return "bad_response"
