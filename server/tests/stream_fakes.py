"""스트림 커넥터 테스트용 가짜 소켓·연결기 — 네트워크 없음 (스펙 001 §4)."""

import asyncio
import json
from typing import Any

from websockets.exceptions import ConnectionClosedOK

from app.core.live_store import LiveStore
from app.core.quotes import QuoteSink


class FakeSocket:
    """미리 정한 프레임을 순서대로 준다. 다 주면 서버가 끊은 것처럼 ConnectionClosedOK."""

    def __init__(
        self, frames: list[str | bytes | BaseException], *, hold: bool = False
    ) -> None:
        self._frames = list(frames)
        self._hold = hold  # True 면 다 준 뒤 끊지 않고 연결을 유지한다
        self.sent: list[str] = []
        self.closed = False
        self.drained = asyncio.Event()  # 프레임을 다 준 시점 — 테스트가 기다린다

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str | bytes:
        if not self._frames:
            self.drained.set()
            if self._hold:
                await asyncio.Event().wait()
            raise ConnectionClosedOK(None, None)
        item = self._frames.pop(0)
        if isinstance(item, BaseException):
            self.drained.set()
            raise item
        await asyncio.sleep(0)
        return item

    async def close(self) -> None:
        self.closed = True

    def subscriptions(self) -> list[list[Any]]:
        return [json.loads(s) for s in self.sent]


class GatedSocket(FakeSocket):
    """테스트가 `push` 한 프레임만 준다 — 정체(무수신)와 회복을 시점별로 흉내 낸다."""

    def __init__(self) -> None:
        super().__init__([], hold=True)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self.delivered = asyncio.Event()  # 마지막 push 가 소비된 시점

    def push(self, frame: str) -> None:
        self.delivered.clear()
        self._queue.put_nowait(frame)

    async def recv(self) -> str | bytes:
        frame = await self._queue.get()
        self.delivered.set()
        return frame


class HangingCloseSocket(FakeSocket):
    """close() 가 돌아오지 않는 소켓 — 종료 상한(§3.11)이 지켜지는지 본다."""

    def __init__(self) -> None:
        super().__init__([], hold=True)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        await asyncio.Event().wait()


class FakeConnector:
    """연결 시도마다 미리 정한 결과(소켓 또는 예외)를 준다. 다 쓰면 영원히 대기한다."""

    def __init__(self, outcomes: list[FakeSocket | BaseException]) -> None:
        self._outcomes = list(outcomes)
        self.urls: list[str] = []
        self.exhausted = asyncio.Event()

    async def __call__(self, url: str) -> FakeSocket:
        self.urls.append(url)
        if not self._outcomes:
            self.exhausted.set()
            await asyncio.Event().wait()  # 테스트가 aclose 로 취소할 때까지
        item = self._outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Sleeps:
    """재연결 백오프 관찰용 — 실제로 자지 않고 값만 기록한다."""

    def __init__(self) -> None:
        self.values: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)
        await asyncio.sleep(0)


class Clock:
    def __init__(self, start_ms: int) -> None:
        self.now = start_ms

    def __call__(self) -> int:
        return self.now


class HandshakeRejected(Exception):
    """핸드셰이크 거부 흉내 — websockets 의 InvalidStatus 처럼 `.response` 를 가진다."""

    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"server rejected WebSocket connection: HTTP {status}")
        self.response = _Response(status, headers or {})


class _Response:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status_code = status
        self.headers = headers


def store_with_universe(bases: set[str]) -> tuple[LiveStore, QuoteSink]:
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe(bases)
    return store, sink


async def until(event: asyncio.Event, timeout: float = 1.0) -> None:
    await asyncio.wait_for(event.wait(), timeout)
