"""서빙 프로세스의 구독·diff·브로드캐스트 (스펙 017 §3.2).

Redis 채널 `spreads` 를 구독하는 태스크 하나가 새 표마다 diff 를 1회 만들고 gzip 도 1회 해서, 접속자
전원에게 같은 바이트를 넣는다. 압축을 여기서 한 번만 하는 이유 — uvicorn 의 permessage-deflate 는 접속마다
따로 압축해서 api CPU 가 접속자 수에 비례했다(실측 2026-09-26: 50명에 1코어 포화). 그래서 Dockerfile 이
그 압축을 끄고, 브라우저는 gzip 바이너리 프레임을 DecompressionStream 으로 푼다. 새 접속자에게 주는
snapshot 도 표 1장당 한 번만 압축해 둔다(2026-09-28) — 배포 직후 전원이 다시 붙을 때 접속마다 790KB 를
압축하면 루프가 수 초씩 멈췄다.
접속자마다 보내기 대기열·보내기 태스크가 따로 있어 느린 한 명이 나머지를 막지 않는다.
채널은 첫 접속자가 붙을 때 구독하고 마지막 접속자가 떠난 뒤 30초가 지나면 닫는다(2026-09-28) — 접속자가
없는 허브(배포의 `server` 컨테이너, 아무도 안 보는 api)가 매초 790KB 를 받아 버리지 않게.
"""

import asyncio
import contextlib
import gzip
import json
import logging
import time

from fastapi import WebSocket

from app.core.redis_bus import RedisBus

logger = logging.getLogger("marketlens.spreads_hub")

HEARTBEAT_SEC = 1.0  # 이만큼 아무것도 안 보냈으면 heartbeat (§3.2)
SEND_QUEUE_LIMIT = 5  # 대기열이 이만큼 쌓인 접속자는 1008 로 닫는다 (§3.2)
WANT_REFRESH_SEC = 5.0
SUB_POLL_SEC = 1.0  # 구독 메시지 대기 단위 — want 갱신·취소·유휴 확인 주기
IDLE_UNSUBSCRIBE_SEC = (
    30.0  # 마지막 접속자가 떠난 뒤 이만큼 지나면 구독을 닫는다 (§3.2)
)
BACKOFF_MIN_SEC = 1.0
BACKOFF_MAX_SEC = 30.0
CODE_SLOW = 1008
CODE_GOING_AWAY = 1001
GZIP_LEVEL = 6  # 표 1장당 1회라 CPU 보다 크기를 택한다 (9 는 시간이 2배인데 크기 차이가 거의 없다)


def pack(text: str) -> bytes:
    """브라우저로 나가는 프레임 1개 — 모든 메시지가 같은 방식이라 클라이언트가 분기하지 않는다."""
    return gzip.compress(text.encode(), compresslevel=GZIP_LEVEL)


HEARTBEAT = pack('{"type":"heartbeat"}')
WAITING = pack('{"type":"waiting"}')
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
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=SEND_QUEUE_LIMIT)
        self._sender: asyncio.Task[None] | None = None
        self._closing = False

    def start(self) -> None:
        self._sender = asyncio.create_task(self._run_sender())

    def offer(self, message: bytes) -> None:
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
                await self._ws.send_bytes(message)
            except Exception:
                return  # 끊긴 소켓 — 수신 쪽(라우터)이 detach 한다


# --- 허브 ---


class SpreadsHub:
    def __init__(self, *, bus: RedisBus) -> None:
        self._bus = bus
        self._conns: set[Connection] = set()
        self._table: dict | None = None  # 직전 표 — 접속자가 있을 때만
        self._index: dict[str, dict] | None = None
        # 지금 표의 snapshot 프레임 — 표 1장당 한 번만 압축한다. 표가 바뀌거나 버려지는 모든 자리에서 비운다:
        # 옛 표의 snapshot 위에 새 표 기준 delta 가 얹히면 행이 틀어진다 (§3.2)
        self._snap: bytes | None = None
        self._task: asyncio.Task[None] | None = None
        self._wanted = (
            asyncio.Event()
        )  # 접속자가 생기면 켠다 — 구독 태스크가 이것을 기다렸다 구독한다
        self._idle_since: float | None = None  # 마지막 접속자가 떠난 시각(monotonic)
        self._loaded = asyncio.Event()  # 첫 접속자의 latest 읽기가 끝났는가
        self._loaded.set()

    @property
    def connections(self) -> int:
        return len(self._conns)

    async def attach(self, ws: WebSocket) -> Connection:
        """접속 등록 → 첫 접속자면 `spreads:latest` 로 시작 표 → snapshot 또는 waiting."""
        conn = Connection(ws)
        conn.start()
        first = not self._conns
        self._conns.add(conn)
        self._idle_since = None
        self._wanted.set()  # 구독이 닫혀 있으면 구독 태스크가 연다 — latest 읽기와 겹쳐 돈다
        if first:
            self._loaded = asyncio.Event()
            try:
                await self._load_latest()
            finally:
                self._loaded.set()
            await (
                self._refresh_want()
            )  # 보는 사람이 있다는 흔적을 지금 1회 — 수집은 이 키를 읽지 않는다 (§3.1)
        else:
            # 첫 접속자가 latest 를 읽는 중이면 끝날 때까지 기다린다 — 그 사이 waiting 을 받은 접속자에게
            # latest 기준 delta 가 이어지면 snapshot 없이 바뀐 행만 받는다 (§3.2, 배포 직후 전원 재접속)
            await self._loaded.wait()
        if self._table is not None:
            if self._snap is None:
                self._snap = pack(make_snapshot(self._table))
            conn.offer(self._snap)
        else:
            conn.offer(WAITING)
        return conn

    def detach(self, conn: Connection) -> None:
        self._conns.discard(conn)
        if not self._conns:
            self._table = self._index = None  # 0명 — 상태를 버린다 (§3.2)
            self._snap = None
            self._idle_since = time.monotonic()

    def on_table(self, text: str) -> None:
        """새 표 1장 — 접속자가 없으면 파싱조차 안 한다. 있으면 diff 1회·gzip 1회, 전원에게 같은 바이트."""
        if not self._conns:
            return
        table = json.loads(text)
        if self._index is None:
            # waiting 중이던 접속자들의 첫 표 — 이 프레임이 곧 이 표의 snapshot 이라 그대로 캐시로 둔다
            frame = pack(make_snapshot(table))
            self._index = index_rows(table)
            self._snap = frame
        else:
            message, self._index = make_delta(self._index, table)
            frame = pack(message)
            self._snap = None  # 새 표 — 다음 접속자가 한 번 만든다
        self._table = table
        for conn in list(self._conns):
            conn.offer(frame)

    async def _load_latest(self) -> None:
        try:
            text = await self._bus.latest()
        except Exception as exc:
            logger.warning("spreads:latest 읽기 실패 — waiting 으로 시작: %r", exc)
            return
        if text is None or not self._conns:
            return  # 읽는 사이 전원이 떠났으면 상태를 들지 않는다 — 0명은 상태가 없다
        self._table = json.loads(text)
        self._index = index_rows(self._table)
        self._snap = None

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
        """구독 태스크 하나 — 접속자가 생기면 구독해 채널 수신 + 5초마다 want 갱신, 0명 30초면 구독을 닫고
        다음 접속자를 기다린다. 끊기면 1→2→…→30초 백오프."""
        backoff = BACKOFF_MIN_SEC
        while True:
            await self._wanted.wait()
            try:
                sub = await self._bus.subscribe()
            except Exception as exc:
                logger.warning(
                    "Redis 구독 연결 실패 — %.0f초 뒤 재시도: %r", backoff, exc
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                continue
            next_want = time.monotonic()
            try:
                while not self._idle_expired():
                    text = await sub.get(SUB_POLL_SEC)
                    # 백오프는 구독이 실제로 받는 것을 본 뒤에야 되돌린다 — SUBSCRIBE 거부(Redis 메모리 상한 등)는
                    # 첫 get 에서야 드러나서, 연결 직후에 되돌리면 매초 재연결·경고를 되풀이한다
                    backoff = BACKOFF_MIN_SEC
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
                continue
            # 0명 30초 — 구독을 닫는다. 닫는 사이에 누가 붙으면 attach 가 다시 켜 두어 곧바로 다시 구독한다
            self._wanted.clear()
            with contextlib.suppress(Exception):
                await sub.aclose()

    def _idle_expired(self) -> bool:
        return (
            not self._conns
            and self._idle_since is not None
            and time.monotonic() - self._idle_since >= IDLE_UNSUBSCRIBE_SEC
        )

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
        self._snap = None
