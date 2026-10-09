"""바이빗 스트림 커넥터 — WebSocket orderbook.200(스냅샷+델타)·publicTrade 3샤드 + instruments-info REST (스펙 019).

바이낸스(012)와 규칙은 같지만 코드를 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
바이낸스와 다른 점: 호가가 자기완결 스냅샷이 아니라 스냅샷 뒤 델타라 심볼마다 로컬 북을 들고,
핑은 라이브러리 프레임이 아니라 JSON `{"op":"ping"}` 을 20초마다 보내며 pong 이 없으면 끊는다.
심볼은 crc32 % 3 으로 샤드에 고정 배정되고, 샤드마다 소켓·시계·백오프·북을 따로 둔다.
메시지마다: 원문 싱크 기록 → 디코드 → 북·행 갱신(QuoteSink) → 그 샤드의 last_message_at(시세만).
심볼 집합 계약(ForeignSymbolSource)도 이 커넥터가 구현한다 — 우주가 확정되면 차이만 재조정한다.
"""

import asyncio
import contextlib
import json
import logging
import re
import time
import zlib
from bisect import bisect_left
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

logger = logging.getLogger("marketlens.stream.bybit")

WS_URL = "wss://stream.bybit.com/v5/public/spot"
WS_SOURCE = "ws:/v5/public/spot"
HANDSHAKE_SOURCE = "ws-handshake:/v5/public/spot"  # 핸드셰이크 거부 응답 본문의 원문 싱크 source (010 §3.1)
REST_URL = "https://api.bybit.com"
INSTRUMENTS_PATH = "/v5/market/instruments-info"
# 현물 전체·거래 중만 — 커넥터가 어차피 거르는 조건이라 심볼 집합은 같고 본문만 줄어든다 (§3.3)
INSTRUMENTS_QUERY = "?category=spot&status=Trading"
INSTRUMENTS_URL = REST_URL + INSTRUMENTS_PATH + INSTRUMENTS_QUERY
SYMBOLS_KEY = "symbols:all"  # 매초 오는 심볼 목록 본문의 원문 싱크 key — 분당 마지막 1건 (001 §3.7)
_BODY_LIMIT = 500  # 핸드셰이크 거부 응답 본문 상한 — 001 §3.1 과 같은 500자
# instruments-info 에서 매 응답 바뀌는 것은 봉투 꼬리의 `"time":<ms>` 하나다 — 이것만 빼고 직전 본문과 비교한다 (§3.3)
_ENVELOPE_TIME = re.compile(rb'"time":\s*\d+\s*\}\s*$')
# 꼬리 몇 바이트 안에서만 찾는다 — 못 찾으면 본문 전체로 비교한다(매번 달라 파싱으로 간다)
_ENVELOPE_TIME_WITHIN = 64

DEPTH = (
    200  # 현물 호가 단계 — 1·50·200·1000 중 100ms 주기라 메시지가 50단계의 1/5 (§3.2)
)
ORDERBOOK_TOPIC = f"orderbook.{DEPTH}."  # + 심볼
TRADE_TOPIC = "publicTrade."  # + 심볼
SHARDS = 3
ARGS_PER_MESSAGE = 10  # 현물 구독 요청 하나의 args 상한 — 공식 문서 (§3.2)
CONTROL_INTERVAL = 0.1  # 구독 요청 사이 대기 — 빈도 한도가 문서에 없어 둔 보수값 (§3.3)
REBALANCE_INTERVAL = 60.0  # set_universe 가 깨우지 않아도 이 주기로 구독 차이를 맞춘다
PING_INTERVAL = 20.0  # JSON ping 주기 — 공식 문서 요구 (§3.2)
PONG_TIMEOUT = 20.0  # 이 안에 pong 이 없으면 끊고 재연결 — 조용히 죽은 TCP 감지 (§3.2)
# 심볼당 행 발행(정렬 + QuoteSink.orderbook) 최소 간격 — 델타는 100ms 마다 오지만 표는 1초에 1번만 읽으므로
# 메시지마다 200단계를 정렬·행 재생성하던 비용(py-spy: 수집기 CPU 22%)을 1/5 로 줄인다 (§3.2)
PUBLISH_INTERVAL_MS = 500
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
CLOSE_TIMEOUT = 2.0  # 종료 시 취소 + 소켓 3개 동시 close 합계 상한
_QUOTE = "USDT"
_RATE_LIMIT_RET_CODE = 10006  # "Too many visits!" — HTTP 200 본문의 retCode (§3.8)


def _now_ms() -> int:
    return int(time.time() * 1000)


def shard_of(symbol: str) -> int:
    """심볼 → 샤드 번호. crc32 라 프로세스·재기동과 무관하게 같다 (§3.3)."""
    return zlib.crc32(symbol.upper().encode()) % SHARDS


def _topics(symbol: str) -> list[str]:
    s = symbol.upper()
    return [f"{ORDERBOOK_TOPIC}{s}", f"{TRADE_TOPIC}{s}"]


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다.

    라이브러리 keepalive(WebSocket ping 프레임)는 쓰지 않는다 — 바이빗은 JSON ping 을 요구하고
    프레임 ping 에 대한 응답은 문서에 없다 (§3.2).
    """
    return await ws_connect(url, open_timeout=WS_OPEN_TIMEOUT, ping_interval=None)


class _SubscribeRejected(Exception):
    """`success:false` 응답 → bad_request (§3.2)."""


class _Book:
    """심볼 하나의 로컬 북 — 가격 순으로 정렬된 `[가격, 잔량]` 단계 목록. 스냅샷으로 통째 교체, 델타로 삽입·교체·삭제 (§3.4).

    asks 는 가격 오름차순, bids 는 가격 내림차순이고, 이분 탐색용 키 열을 나란히 든다(bids 키는 부호를 뒤집은
    가격이라 오름차순). 북을 늘 정렬된 채로 두는 이유: 델타는 바뀐 단계만 와서(2026-10-09 캡처 평균 5단계) 그
    자리만 찾아 고치면 되지만, 발행은 심볼당 500ms 마다 북 전체(쪽마다 약 200단계)를 행으로 내보낸다. 가격 →
    잔량 dict 로 들면 발행마다 정렬하고 `[가격, 잔량]` 목록 약 400개를 새로 만들어야 하고, 그 목록들은 옛 행이
    교체될 때 다시 해제된다. 정렬된 목록이면 발행은 얕은 복사뿐이다 — 같은 캡처 60초 재생에서 바이빗 처리(행
    저장 포함) CPU 가 31.7 → 17.9ms/초로 43% 준다(로컬 Mac). 대신 델타 반영은 조금 비싸진다(델타 1건 1.2 → 3.1µs).

    결과는 dict 를 정렬하던 것과 순서·값·가격 객체까지 같다: 같은 가격의 단계는 하나뿐이고, 잔량만 바뀌면
    처음 들어온 가격 객체를 지킨다(dict 키가 그렇듯이).
    """

    def __init__(self) -> None:
        self.ask_keys: list[float] = []  # 가격 오름차순 — asks 와 같은 자리
        self.asks: list[list[float]] = []
        self.bid_keys: list[
            float
        ] = []  # -가격 오름차순(= 가격 내림차순) — bids 와 같은 자리
        self.bids: list[list[float]] = []
        self.ts = 0  # 마지막으로 반영한 프레임의 `ts` — 발행 시 행의 호가 시각
        self.received_at = 0  # 그 프레임의 수신 시각 — 발행 시 행의 `updated_at`
        self.published_at = 0  # 마지막으로 행을 내보낸 시각 (§3.2 발행 제한)
        self.dirty = False  # 델타를 반영했지만 아직 행으로 내보내지 않았다

    def replace(self, data: dict[str, Any]) -> None:
        # 같은 가격이 둘 이상 오면 뒤 잔량·처음 가격 객체 — dict 로 먼저 모아 그 규칙을 그대로 지킨다.
        # 잔량 0 단계도 북에 남긴다(행 규칙 clean_levels 가 거른다) — 스냅샷을 dict 로 받던 때와 같다
        asks = {float(p): float(q) for p, q in data["a"]}
        bids = {float(p): float(q) for p, q in data["b"]}
        self.ask_keys = sorted(asks)
        self.asks = [[p, asks[p]] for p in self.ask_keys]
        prices = sorted(bids, reverse=True)
        self.bids = [[p, bids[p]] for p in prices]
        self.bid_keys = [-p for p in prices]

    def apply(self, data: dict[str, Any]) -> None:
        # 단계 원소 `[가격, 잔량]` 은 clean_levels 를 지나 행에 그대로 실린다 — 제자리에서 고치면 이미 내보낸
        # 행의 호가가 바뀐다. 잔량이 바뀌면 반드시 새 목록으로 갈아 끼운다(levels[i][1] = … 금지).
        # 매도·매수를 따로 쓴 것은 매수 키의 부호 뒤집기를 단계마다 분기 없이 하려는 것이다.
        keys, levels = self.ask_keys, self.asks
        for p, q in data.get("a") or []:
            price, size = float(p), float(q)
            i = bisect_left(keys, price)
            if i < len(keys) and keys[i] == price:
                if size <= 0:
                    del keys[i]  # 잔량 0 = 그 가격 삭제 (공식 문서 규칙)
                    del levels[i]
                else:
                    levels[i] = [levels[i][0], size]  # 처음 가격 객체를 지킨 새 목록
            elif size > 0:
                keys.insert(
                    i, price
                )  # 없는 가격 = 삽입. 없는 가격의 잔량 0 은 할 일이 없다
                levels.insert(i, [price, size])
        keys, levels = self.bid_keys, self.bids
        for p, q in data.get("b") or []:
            price, size = float(p), float(q)
            key = -price
            i = bisect_left(keys, key)
            if i < len(keys) and keys[i] == key:
                if size <= 0:
                    del keys[i]
                    del levels[i]
                else:
                    levels[i] = [levels[i][0], size]
            elif size > 0:
                keys.insert(i, key)
                levels.insert(i, [price, size])

    def sorted_levels(self) -> tuple[list[list[float]], list[list[float]]]:
        """asks 오름차순·bids 내림차순 — 행 규칙(001 §3.3)이 기대하는 순서. 이미 정렬돼 있어 얕은 복사만 한다."""
        # 복사를 지우지 않는다 — 행 쪽(clean_levels)이 거를 것이 없을 때 입력 목록을 그대로 돌려주게 바뀌어도
        # 북의 del·insert 가 이미 내보낸 행의 단계 목록을 바꾸지 않게 하는 복사다. 원소는 북과 행이 함께 물지만
        # apply 가 원소를 제자리에서 고치지 않으므로 안전하다.
        return self.asks[:], self.bids[:]


class _Shard:
    """소켓 하나 = 샤드 하나. 시계·백오프·구독 집합·북을 샤드마다 따로 둔다 (§3.5)."""

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
        self.ws: Any | None = None
        self.task: asyncio.Task[None] | None = None
        self.ping_task: asyncio.Task[None] | None = None
        # dirty 북을 주기적으로 발행 (§3.2) — 소켓과 수명이 같다
        self.flush_task: asyncio.Task[None] | None = None
        self.backoff = BACKOFF_START
        self.next_id = 0
        self.pong_pending = False  # ping 을 보냈고 아직 pong 이 안 왔다
        self.pong_timed_out = (
            False  # 이번 연결이 pong 없음으로 끊겼다 — 실패 종류를 timeout 으로
        )
        self.lock = asyncio.Lock()  # 연결 직후 구독과 재조정 전송이 겹치지 않게


class BybitStream:
    id = "bybit"
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
        self._symbol_of: dict[str, str] = {}  # base → 심볼 (instruments-info baseCoin)
        self._base_of: dict[str, str] = {}  # 심볼 → base
        # 직전 set_universe 가 끝까지 처리한 (심볼 맵 객체, 우주 사본) — 둘 다 같으면 배정도 같다 (§3.3).
        # id 가 아니라 맵 객체를 들고 있어, 버려진 맵의 id 를 새 맵이 물려받아 같다고 오인할 일이 없다
        self._applied: tuple[dict[str, str], frozenset[str]] | None = None
        self._wake = asyncio.Event()  # set_universe 가 재조정 루프를 깨운다
        self._rebalance: asyncio.Task[None] | None = None
        self.decode_failures = 0  # 버린 무효 프레임 수 — 그 자체로 실패가 아니다
        # 직전에 맵까지 만든 200 응답의 비교용 바이트(꼬리 time 제외) — 같으면 파싱·맵 재생성을 건너뛴다 (§3.3)
        self._symbols_body: bytes | None = None

    # --- 심볼 집합 (ForeignSymbolSource, §3.3) ---

    async def refresh(self, client: httpx.AsyncClient) -> int:
        """instruments-info 1회 → Trading·USDT 심볼 맵. 응답 본문은 해석 전에 원문 싱크로(`symbols:all`).

        HTTP 200 이어도 `retCode != 0` 이면 실패다 (§3.8). 원문 기록은 매 응답 하고, 200 본문에서 꼬리의
        time 만 뺀 바이트가 직전에 맵을 만든 응답과 같으면 파싱·맵 재생성을 건너뛴다 — 바이트가 같으면 맵도
        같으므로 낡을 여지가 없다 (§3.3).
        """
        url = INSTRUMENTS_URL
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"바이빗 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이빗 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        self._record(
            self.id, f"rest:{INSTRUMENTS_PATH}", self._clock(), resp.text, SYMBOLS_KEY
        )
        comparable = _without_envelope_time(resp.content)
        if resp.status_code == 200 and comparable == self._symbols_body:
            return 1
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이빗 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=_classify_rest_status(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"바이빗 JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if not isinstance(data, dict):
            raise ExchangeApiError(
                self.id, url, "바이빗 응답이 객체가 아니다", body=resp.text
            )
        ret_code = data.get("retCode")
        if ret_code != 0:
            raise ExchangeApiError(
                self.id,
                url,
                f"바이빗 retCode {ret_code}: {data.get('retMsg', '')}",
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
                "바이빗 instruments-info 에 result.list 가 없다",
                body=resp.text,
            )
        symbol_of: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("status") != "Trading" or item.get("quoteCoin") != _QUOTE:
                continue
            base = str(item["baseCoin"]).upper()
            symbol_of.setdefault(
                base, str(item["symbol"]).upper()
            )  # 둘 이상이면 처음 것
        self._symbol_of = symbol_of
        self._base_of = {symbol: base for base, symbol in symbol_of.items()}
        self._symbols_body = comparable
        return 1

    def bases(self) -> set[str]:
        return set(self._symbol_of)

    def set_universe(self, bases: set[str]) -> None:
        """우주 확정 → 샤드별 배정 교체. 빠진 심볼의 행·북은 그 자리에서 지우고 재조정을 깨운다.

        우주 전체를 받으므로 자기 맵에 없는 base 는 무시한다(다른 해외에만 있는 코인).
        매초 불리므로 배정이 하나도 안 바뀌면 아무것도 하지 않는다 (§3.3).

        심볼 맵 객체와 우주가 직전 호출과 같으면 샤드 배정을 다시 계산하지 않고 바로 돌아온다 — 매초 배정 심볼 수백
        개의 crc32(샤드마다 한 번씩)를 다시 계산해 봐야 같은 배정이 나오기 때문이다(우주 407·심볼 390 에서 호출 1번
        116 → 2µs, 로컬 Mac). 목록·우주는 상장·상폐 때만 바뀌므로 거의 모든 호출이 이 지름길로 끝난다. 기준 동작과 같은 근거:
        맵은 refresh 가 본문을 다시 파싱할 때만 새 객체로 바뀌고 제자리에서 고치지 않으며, shard.assigned 를
        바꾸는 곳은 이 함수뿐이다. 그래서 두 입력이 같으면 다시 계산해도 모든 샤드에서 mine == shard.assigned 라 할 일이 없다.
        """
        applied = self._applied
        if (
            applied is not None
            and applied[0] is self._symbol_of
            and applied[1] == bases
        ):
            return
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
                shard.books.pop(symbol, None)
            shard.assigned = mine
            if mine:
                shard.has_work.set()
            else:
                shard.has_work.clear()
        if changed:
            self._wake.set()
        # 끝까지 처리한 뒤에만 기억한다 — 중간에 예외가 나면 다음 호출이 처음부터 다시 맞춘다.
        # 우주는 사본으로 든다 — 호출자가 같은 집합을 제자리에서 고쳐 다시 넘겨도 바뀐 것으로 본다
        self._applied = (self._symbol_of, frozenset(bases))

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
                    message=f"바이빗 스트림 끊김: 샤드 {shard.index}",
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
                        f"바이빗 스트림 정체: 샤드 {shard.index} "
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
        """샤드 상태를 store.stream("bybit") 하나로 집계한다 (§3.5). last_error 는 judge 가 정한다."""
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
        """바이빗이 핸드셰이크를 거부하며 준 HTTP 본문도 원문이다 — 해석 전에 싱크로 (001 §3.7)."""
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
                    "바이빗 샤드 %d 연결 실패 — %.0f초 뒤 재시도: %s",
                    shard.index,
                    shard.backoff,
                    shard.state.last_error.message,
                )
                await self._sleep(shard.backoff)
                shard.backoff = min(shard.backoff * 2, BACKOFF_MAX)
                continue
            shard.ws = ws
            shard.subscribed = set()
            shard.books.clear()  # 새 소켓은 새 스냅샷으로 시작한다 — 옛 북에 새 델타를 얹지 않는다 (§3.4)
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
                shard.flush_task = asyncio.create_task(self._run_flush(shard))
                await self._pump(shard, ws)
            except asyncio.CancelledError:
                raise
            except _SubscribeRejected as exc:
                shard.state.last_error = StreamError(
                    kind="bad_request",
                    message=f"바이빗 구독 거부(샤드 {shard.index}): {exc}",
                    status_code=None,
                    url=WS_URL,
                )
            except Exception as exc:
                if shard.pong_timed_out:
                    shard.state.last_error = StreamError(
                        kind="timeout",
                        message=f"바이빗 스트림 pong 없음(샤드 {shard.index}): {PONG_TIMEOUT:.0f}초 안에 응답이 없어 끊었다",
                        status_code=None,
                        url=WS_URL,
                    )
                else:
                    shard.state.last_error = _classify(exc, shard.index)
                    self._record_rejection(exc)
            finally:
                await self._stop_ping(shard)
                await self._stop_flush(shard)
                shard.state.connected = False
                shard.state.connected_since = None
                shard.state.subscribed = 0
                shard.subscribed = set()
                shard.books.clear()
                self._publish()  # close 가 늦어도 집계는 먼저 미연결이 된다
                await self._close_socket(shard)
            logger.warning(
                "바이빗 샤드 %d 스트림이 끊겼다 — %.0f초 뒤 재연결: %s",
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
                        "바이빗 샤드 %d 재조정 전송 실패: %r", shard.index, exc
                    )

    async def _run_ping(self, shard: _Shard, ws: Any) -> None:
        """20초마다 JSON ping — pong 이 20초 안에 없으면 소켓을 닫아 펌프가 재연결하게 한다 (§3.2)."""
        while True:
            await self._sleep(PING_INTERVAL)
            shard.pong_pending = True
            shard.next_id += 1
            try:
                await ws.send(json.dumps({"req_id": str(shard.next_id), "op": "ping"}))
            except Exception:
                return  # 죽은 소켓 — 펌프가 끊김을 처리한다
            await self._sleep(PONG_TIMEOUT)
            if shard.pong_pending:
                shard.pong_timed_out = True
                logger.warning(
                    "바이빗 샤드 %d pong 없음 — %.0f초 안에 응답이 없어 끊고 재연결한다",
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

    async def _run_flush(self, shard: _Shard) -> None:
        """PUBLISH_INTERVAL 마다 델타로 바뀌었지만 아직 안 내보낸 북을 전부 행으로 만든다 (§3.2).

        델타가 끊긴 심볼도 마지막 델타 뒤 한 주기 안에는 반드시 발행된다 — 누락 없음.
        발행 실패가 이 태스크를 죽이면 dirty 북이 영원히 안 나가므로 로그만 남기고 계속 돈다.
        """
        while True:
            await self._sleep(PUBLISH_INTERVAL_MS / 1000)
            try:
                self._flush(shard)
            except Exception:
                logger.exception("바이빗 샤드 %d 호가 행 발행 실패", shard.index)

    def _flush(self, shard: _Shard) -> None:
        now = self._clock()
        for symbol, book in shard.books.items():
            if not book.dirty:
                continue
            base = self._base_of.get(symbol)
            if base is None:
                continue  # 맵에서 빠진 심볼 — 펌프와 같은 규칙으로 버린다 (§3.4)
            self._publish_book(symbol, base, book, now)

    async def _stop_flush(self, shard: _Shard) -> None:
        task, shard.flush_task = shard.flush_task, None
        if task is None:
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
        """배정과 실제 구독의 차이만 보낸다 — unsubscribe 먼저, args 10개씩, 0.1초 간격 (§3.3)."""
        async with shard.lock:
            ws = shard.ws
            if ws is None:
                return
            wanted = set(shard.assigned)
            drop = sorted(shard.subscribed - wanted)
            add = sorted(wanted - shard.subscribed)
            for op, symbols in (("unsubscribe", drop), ("subscribe", add)):
                args = [topic for s in symbols for topic in _topics(s)]
                for i in range(0, len(args), ARGS_PER_MESSAGE):
                    shard.next_id += 1
                    await ws.send(
                        json.dumps(
                            {
                                "req_id": str(shard.next_id),
                                "op": op,
                                "args": args[i : i + ARGS_PER_MESSAGE],
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
            if "success" in msg:
                # 구독·핑 응답 — 시세로 세지 않는다. 실패 응답은 구독 거부 (§3.2)
                if msg["success"] is False:
                    raise _SubscribeRejected(str(msg.get("ret_msg", "")))
                if msg.get("op") in ("ping", "pong"):
                    shard.pong_pending = False
                continue
            topic = msg.get("topic")
            if not isinstance(topic, str):
                continue
            split = _split_topic(topic)
            if split is None:
                continue
            kind, symbol = split
            base = self._base_of.get(symbol)
            if base is None:
                continue  # 맵에 없는 심볼 — 버린다 (§3.4)
            try:
                if kind == "orderbook":
                    self._on_orderbook(shard, symbol, base, msg, at)
                else:
                    self._on_trade(base, msg["data"])
            except (KeyError, TypeError, ValueError):
                self.decode_failures += 1
                continue
            shard.state.last_message_at = at
            shard.backoff = BACKOFF_START  # 구독까지 성공했다는 증거 = 첫 시세 프레임
            # 시세 프레임이 바꾸는 집계값은 last_message_at 하나뿐이다 — 샤드 3개를 다시 집계하지 않고
            # 올리기만 한다. 연결·끊김·구독 변경은 그 자리에서 _publish 가 전체를 다시 집계한다 (§3.5)
            last = self._state.last_message_at
            if last is None or at > last:
                self._state.last_message_at = at

    def _on_orderbook(
        self, shard: _Shard, symbol: str, base: str, msg: dict[str, Any], at: int
    ) -> None:
        data = msg["data"]
        kind = msg.get("type")
        ts = int(msg["ts"])
        book = shard.books.get(symbol)
        if kind == "snapshot" or data.get("u") == 1:
            # 새 스냅샷·서비스 재시작(u=1) → 북 통째 교체 (§3.4). 교체 직후 상태는 간격과 무관하게 바로 내보낸다
            book = _Book()
            book.replace(data)
            shard.books[symbol] = book
            book.ts, book.received_at = ts, at
            self._publish_book(symbol, base, book, at)
        elif kind == "delta":
            if book is None:
                return  # 스냅샷 전에 온 델타 — 북이 없으니 버린다 (§3.4)
            book.apply(data)  # 델타는 바뀐 단계만 오므로 하나도 빠짐없이 북에 반영한다
            book.ts, book.received_at = ts, at
            if at - book.published_at >= PUBLISH_INTERVAL_MS:
                self._publish_book(symbol, base, book, at)  # 조용하던 심볼은 바로
            else:
                # 최근에 내보냈다 — 다음 주기(_run_flush)에 묶어서 (§3.2)
                book.dirty = True
        else:
            raise ValueError(f"알 수 없는 orderbook type: {kind!r}")

    def _publish_book(self, symbol: str, base: str, book: _Book, now: int) -> None:
        """북 → 정렬 → 행 1회. 호가 시각·수신 시각은 그 북에 마지막으로 반영된 프레임의 것 (§3.2)."""
        asks, bids = book.sorted_levels()
        self._sink.orderbook(
            exchange=self.id,
            base=base,
            quote=_QUOTE,
            native_symbol=symbol,
            asks=asks,
            bids=bids,
            timestamp_ms=book.ts,
            received_at_ms=book.received_at,
        )
        book.dirty = False
        book.published_at = now

    def _on_trade(self, base: str, data: Any) -> None:
        """한 프레임의 체결 중 `T` 가 가장 큰 것 — 배열 순서를 믿지 않는다 (§3.4)."""
        if not isinstance(data, list) or not data:
            raise ValueError("publicTrade data 가 비었다")
        latest = max(data, key=lambda t: int(t["T"]))
        self._sink.trade(
            exchange=self.id,
            base=base,
            price=float(latest["p"]),
            price_timestamp=int(latest["T"]),
        )


def _without_envelope_time(content: bytes) -> bytes:
    """비교용 바이트 — 봉투 꼬리의 `"time":<ms>}` 를 뺀 것. 없으면 본문 그대로."""
    m = _ENVELOPE_TIME.search(content, max(0, len(content) - _ENVELOPE_TIME_WITHIN))
    return content if m is None else content[: m.start()]


def _decode(text: str) -> dict[str, Any] | None:
    """프레임 텍스트 → JSON 객체. 객체가 아니거나 JSON 이 아니면 None(무효 프레임)."""
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _split_topic(topic: str) -> tuple[str, str] | None:
    """`orderbook.200.BTCUSDT` → ("orderbook", "BTCUSDT"), `publicTrade.BTCUSDT` → ("trade", "BTCUSDT")."""
    if topic.startswith(ORDERBOOK_TOPIC):
        symbol = topic[len(ORDERBOOK_TOPIC) :]
        return ("orderbook", symbol.upper()) if symbol else None
    if topic.startswith(TRADE_TOPIC):
        symbol = topic[len(TRADE_TOPIC) :]
        return ("trade", symbol.upper()) if symbol else None
    return None


def _quote_key(msg: dict[str, Any] | None) -> str | None:
    """원문 싱크의 `key` — 호가·체결 프레임이면 `orderbook:<심볼>`·`trade:<심볼>` (§3.1)."""
    if msg is None or not isinstance(msg.get("topic"), str):
        return None
    split = _split_topic(msg["topic"])
    return f"{split[0]}:{split[1]}" if split else None


def _classify(exc: BaseException, shard: int) -> StreamError:
    """연결·핸드셰이크·끊김 분류 — 핸드셰이크 HTTP 거부는 바이빗 REST 규칙(§3.8)으로."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = f"바이빗 WebSocket 실패(샤드 {shard}): {type(exc).__name__}: {exc}"
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
    """바이빗 규칙(§3.8): 403 차단("access too frequent", 10분 이상), 429 한도초과, 5xx 장애, 그 외 4xx 요청오류."""
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
    """HTTP 200 본문의 retCode — 10006 은 한도초과, 그 밖은 응답오류로 두고 body 를 보며 표를 채운다 (§3.8)."""
    return "rate_limit" if ret_code == _RATE_LIMIT_RET_CODE else "bad_response"
