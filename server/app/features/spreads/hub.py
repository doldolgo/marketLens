"""서빙 프로세스의 구독·diff·브로드캐스트 (스펙 017 §3.2).

Redis 채널 `spreads` 를 구독하는 태스크 하나가 새 표마다 diff 를 1회 만들고, 접속자 전원에게 같은
문자열을 넣는다. 접속자마다 보내기 대기열·보내기 태스크가 따로 있어 느린 한 명이 나머지를 막지 않는다.
접속자가 없으면 상태(직전 표)를 들지 않고 채널 메시지를 파싱하지 않는다 — 배포의 `server` 컨테이너가
같은 코드를 띄워도 비용이 없다.
"""

import asyncio
import contextlib
import json
import logging
import time

from fastapi import WebSocket

from app.core.redis_bus import RedisBus

logger = logging.getLogger("marketlens.spreads_hub")

HEARTBEAT_SEC = 1.0  # 이만큼 아무것도 안 보냈으면 heartbeat (§3.2)
SEND_QUEUE_LIMIT = 5  # 대기열이 이만큼 쌓인 접속자는 1008 로 닫는다 (§3.2)
WANT_REFRESH_SEC = 5.0
SUB_POLL_SEC = 1.0  # 구독 메시지 대기 단위 — want 갱신·취소 확인 주기
BACKOFF_MIN_SEC = 1.0
BACKOFF_MAX_SEC = 30.0
CODE_SLOW = 1008
CODE_GOING_AWAY = 1001

HEARTBEAT = '{"type":"heartbeat"}'
WAITING = '{"type":"waiting"}'
META_KEYS = ("notional", "rate", "warnings", "dataReceivedAt", "fetchedAt")


# --- diff 순수 함수 (§3.2) ---


def row_key(row: dict) -> str:
    return f"{row['sym']}|{row['dom']}|{row['fx']}"


def index_rows(table: dict) -> dict[str, dict]:
    """키 → `age` 를 뺀 행. `age` 만 다른 행을 "안 바뀜" 으로 보기 위한 비교 형태."""
    return {
        row_key(r): {k: v for k, v in r.items() if k != "age"} for r in table["rows"]
    }


def make_snapshot(table: dict) -> str:
    return _dumps({"type": "snapshot", **table})


def make_delta(prev: dict[str, dict], table: dict) -> tuple[str, dict[str, dict]]:
    """직전 인덱스와 새 표 → (delta 메시지, 새 인덱스). 바뀐 행은 현재 `age` 를 그대로 싣는다."""
    cur = index_rows(table)
    changed = [r for r in table["rows"] if prev.get(row_key(r)) != cur[row_key(r)]]
    removed = [k for k in prev if k not in cur]
    delta = {"type": "delta", **{k: table[k] for k in META_KEYS}}
    delta["rows"] = changed
    delta["removed"] = removed
    return _dumps(delta), cur


def _dumps(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# --- 접속 하나 ---


class Connection:
    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SEND_QUEUE_LIMIT)
        self._sender: asyncio.Task[None] | None = None
        self._closing = False

    def start(self) -> None:
        self._sender = asyncio.create_task(self._run_sender())

    def offer(self, message: str) -> None:
        """대기열에 넣는다 — 가득 차 있으면 느린 접속자로 보고 닫는다. 기다리지 않는다."""
        if self._closing:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self.kick(CODE_SLOW)

    def kick(self, code: int) -> None:
        """다른 태스크(허브)에서 닫기 — 보내기 태스크를 멈추고 닫기 프레임은 별도 태스크로."""
        if self._closing:
            return
        self._closing = True
        asyncio.create_task(self.close(code))

    async def close(self, code: int) -> None:
        self._closing = True
        if self._sender is not None:
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._sender
            self._sender = None
        with contextlib.suppress(Exception):
            await self._ws.close(code)

    async def _run_sender(self) -> None:
        """대기열 순서대로 전송. HEARTBEAT_SEC 동안 보낼 게 없으면 heartbeat. 전송 실패면 끝."""
        while True:
            try:
                message = await asyncio.wait_for(self._queue.get(), HEARTBEAT_SEC)
            except TimeoutError:
                message = HEARTBEAT
            try:
                await self._ws.send_text(message)
            except Exception:
                return  # 끊긴 소켓 — 수신 쪽(라우터)이 detach 한다


# --- 허브 ---


class SpreadsHub:
    def __init__(self, *, bus: RedisBus) -> None:
        self._bus = bus
        self._conns: set[Connection] = set()
        self._table: dict | None = None  # 직전 표 — 접속자가 있을 때만
        self._index: dict[str, dict] | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def connections(self) -> int:
        return len(self._conns)

    async def attach(self, ws: WebSocket) -> Connection:
        """접속 등록 → 첫 접속자면 `spreads:latest` 로 시작 표 → snapshot 또는 waiting."""
        conn = Connection(ws)
        conn.start()
        first = not self._conns
        self._conns.add(conn)
        if first:
            await self._load_latest()
            await self._refresh_want()  # 수집이 표를 만들기 시작하도록 지금 1회 (§3.1)
        conn.offer(make_snapshot(self._table) if self._table is not None else WAITING)
        return conn

    def detach(self, conn: Connection) -> None:
        self._conns.discard(conn)
        if not self._conns:
            self._table = self._index = None  # 0명 — 상태를 버린다 (§3.2)

    def on_table(self, text: str) -> None:
        """새 표 1장 — 접속자가 없으면 파싱조차 안 한다. 있으면 diff 1회, 전원에게 같은 문자열."""
        if not self._conns:
            return
        table = json.loads(text)
        if self._index is None:
            message = make_snapshot(table)  # waiting 중이던 접속자들의 첫 표
            self._index = index_rows(table)
        else:
            message, self._index = make_delta(self._index, table)
        self._table = table
        for conn in list(self._conns):
            conn.offer(message)

    async def _load_latest(self) -> None:
        try:
            text = await self._bus.latest()
        except Exception as exc:
            logger.warning("spreads:latest 읽기 실패 — waiting 으로 시작: %r", exc)
            return
        if text is None:
            return
        self._table = json.loads(text)
        self._index = index_rows(self._table)

    async def _refresh_want(self) -> None:
        if not self._conns:
            return
        try:
            await self._bus.want()
        except Exception as exc:
            logger.warning("spreads:want 갱신 실패: %r", exc)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="spreads_hub")

    async def run(self) -> None:
        """구독 태스크 하나 — 채널 수신 + 5초마다 want 갱신. 끊기면 1→2→…→30초 백오프."""
        backoff = BACKOFF_MIN_SEC
        while True:
            try:
                sub = await self._bus.subscribe()
            except Exception as exc:
                logger.warning(
                    "Redis 구독 연결 실패 — %.0f초 뒤 재시도: %r", backoff, exc
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                continue
            backoff = BACKOFF_MIN_SEC
            next_want = time.monotonic()
            try:
                while True:
                    text = await sub.get(SUB_POLL_SEC)
                    if text is not None:
                        self.on_table(text)
                    if time.monotonic() >= next_want:
                        await self._refresh_want()
                        next_want = time.monotonic() + WANT_REFRESH_SEC
            except asyncio.CancelledError:
                await sub.aclose()
                raise
            except Exception as exc:
                logger.warning("Redis 구독 끊김 — %.0f초 뒤 재연결: %r", backoff, exc)
                with contextlib.suppress(Exception):
                    await sub.aclose()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)

    async def aclose(self) -> None:
        """구독을 멈추고 접속자 전원을 1001 로 닫는다."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        conns, self._conns = list(self._conns), set()
        await asyncio.gather(*(c.close(CODE_GOING_AWAY) for c in conns))
        self._table = self._index = None
