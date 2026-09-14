"""017 §4 — 허브 브로드캐스트·첫 접속 snapshot·want·느린 클라이언트 (fakeredis, 가짜 소켓)."""

import asyncio
import json

from app.core.redis_bus import WANT_KEY
from app.features.spreads.hub import (
    CODE_SLOW,
    SEND_QUEUE_LIMIT,
    Connection,
    SpreadsHub,
    make_snapshot,
)
from app.features.spreads.tests.test_push import make_bus, row, table


class FakeWs:
    def __init__(self, *, block: bool = False) -> None:
        self.sent: list[str] = []
        self.closed: int | None = None
        self._block = block
        self.gate = asyncio.Event()

    async def send_text(self, text: str) -> None:
        if self._block:
            await self.gate.wait()
        self.sent.append(text)

    async def close(self, code: int) -> None:
        self.closed = code


async def settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


async def test_hub_sends_identical_bytes_to_all_and_ignores_when_empty() -> None:
    bus, _ = make_bus()
    hub = SpreadsHub(bus=bus)
    hub.on_table("this is not json")  # 접속자 0명 — 파싱하지 않으므로 예외도 없다

    sockets = [FakeWs() for _ in range(3)]
    conns = [await hub.attach(ws) for ws in sockets]  # type: ignore[arg-type]
    await settle()
    assert all(ws.sent == ['{"type":"waiting"}'] for ws in sockets)  # latest 없음
    assert await bus._client.ttl(WANT_KEY) == 15  # 첫 접속자 → want 즉시 1회

    hub.on_table(json.dumps(table([row("BTC")])))
    hub.on_table(json.dumps(table([row("BTC", fwd=2.0), row("ETH")])))
    await settle()
    first, second = sockets[0].sent[1], sockets[0].sent[2]
    assert json.loads(first)["type"] == "snapshot"  # waiting 뒤 첫 표는 snapshot
    assert json.loads(second)["type"] == "delta"
    assert [ws.sent for ws in sockets] == [sockets[0].sent] * 3  # 같은 바이트

    for conn in conns:
        hub.detach(conn)
    assert hub.connections == 0
    hub.on_table("garbage again")  # 0명으로 돌아가면 다시 파싱하지 않는다
    await hub.aclose()


async def test_first_connection_gets_snapshot_from_latest_key() -> None:
    bus, _ = make_bus()
    await bus.publish_table(json.dumps(table([row("BTC")])))
    hub = SpreadsHub(bus=bus)
    ws = FakeWs()
    await hub.attach(ws)  # type: ignore[arg-type]
    await settle()
    snapshot = json.loads(ws.sent[0])
    assert snapshot["type"] == "snapshot" and snapshot["rows"][0]["sym"] == "BTC"
    assert json.loads(make_snapshot(table([row("BTC")]))) == snapshot
    await hub.aclose()
    assert ws.closed == 1001


async def test_slow_client_is_closed_1008_without_delaying_others() -> None:
    slow, fast = FakeWs(block=True), FakeWs()
    slow_conn, fast_conn = Connection(slow), Connection(fast)  # type: ignore[arg-type]
    slow_conn.start()
    fast_conn.start()
    # 첫 메시지는 보내기 태스크가 집어 전송 중 멈추고, 그 뒤 SEND_QUEUE_LIMIT 개가 대기열을 채운다
    for i in range(SEND_QUEUE_LIMIT + 1):
        slow_conn.offer(f"m{i}")
        fast_conn.offer(f"m{i}")
        await settle()
    assert slow.closed is None
    slow_conn.offer("one more")  # 대기열 가득 → 1008
    await settle()
    assert slow.closed == CODE_SLOW
    assert fast.sent == [f"m{i}" for i in range(SEND_QUEUE_LIMIT + 1)]
    await fast_conn.close(1001)
