"""017 §4 — 허브 브로드캐스트·첫 접속 snapshot(표 1장당 압축 1회)·want·느린 클라이언트·지연 구독 (fakeredis, 가짜 소켓)."""

import asyncio
import gzip
import json

import fakeredis

from app.core.redis_bus import WANT_KEY, RedisBus
from app.features.spreads import hub as hub_module
from app.features.spreads.hub import (
    CODE_SLOW,
    HEARTBEAT,
    SEND_QUEUE_LIMIT,
    WAITING,
    Connection,
    SpreadsHub,
    make_snapshot,
    pack,
)
from app.features.spreads.tests.test_push import make_bus, row, table


class FakeWs:
    def __init__(self, *, block: bool = False) -> None:
        self.sent: list[bytes] = []
        self.closed: int | None = None
        self._block = block
        self.gate = asyncio.Event()

    async def send_bytes(self, data: bytes) -> None:
        if self._block:
            await self.gate.wait()
        self.sent.append(data)

    async def close(self, code: int) -> None:
        self.closed = code


def unpack(frame: bytes) -> dict:
    return json.loads(gzip.decompress(frame))


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
    assert all(ws.sent == [WAITING] for ws in sockets)  # latest 없음
    assert await bus._client.ttl(WANT_KEY) == 15  # 첫 접속자 → want 즉시 1회

    hub.on_table(json.dumps(table([row("BTC")])))
    hub.on_table(json.dumps(table([row("BTC", fwd=2.0), row("ETH")])))
    await settle()
    first, second = sockets[0].sent[1], sockets[0].sent[2]
    assert unpack(first)["type"] == "snapshot"  # waiting 뒤 첫 표는 snapshot
    assert unpack(second)["type"] == "delta"
    # 같은 바이트 — diff 도 gzip 도 표 1장당 1회
    assert [ws.sent for ws in sockets] == [sockets[0].sent] * 3

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
    snapshot = unpack(ws.sent[0])
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
        slow_conn.offer(f"m{i}".encode())
        fast_conn.offer(f"m{i}".encode())
        await settle()
    assert slow.closed is None
    slow_conn.offer(b"one more")  # 대기열 가득 → 1008
    await settle()
    assert slow.closed == CODE_SLOW
    assert fast.sent == [f"m{i}".encode() for i in range(SEND_QUEUE_LIMIT + 1)]
    await fast_conn.close(1001)


def test_frames_are_gzip_of_the_json_text() -> None:
    """브라우저 계약 — 프레임을 gzip 으로 풀면 정확히 메시지 JSON 이다. 상수 프레임도 같은 방식."""
    text = make_snapshot(table([row("BTC")]))
    assert gzip.decompress(pack(text)).decode() == text
    assert unpack(HEARTBEAT) == {"type": "heartbeat"}
    assert unpack(WAITING) == {"type": "waiting"}


# --- 2026-09-28 — snapshot 은 표 1장당 한 번만 압축 (§3.2) ---


def count_packs(monkeypatch) -> list[str]:  # noqa: ANN001
    """허브가 부르는 `pack` 을 세는 가짜 — 압축한 메시지 텍스트를 순서대로 남긴다."""
    texts: list[str] = []
    real = hub_module.pack

    def counting(text: str) -> bytes:
        texts.append(text)
        return real(text)

    monkeypatch.setattr(hub_module, "pack", counting)
    return texts


async def test_snapshot_is_compressed_once_per_table_and_shared(monkeypatch) -> None:  # noqa: ANN001
    bus, _ = make_bus()
    first_table = table([row("BTC")])
    await bus.publish_table(json.dumps(first_table))
    hub = SpreadsHub(bus=bus)
    packs = count_packs(monkeypatch)

    sockets = [FakeWs() for _ in range(5)]
    conns = [await hub.attach(ws) for ws in sockets]  # type: ignore[arg-type]
    await settle()
    # 같은 표 동안 붙은 접속자 전원이 같은 바이트 — 압축은 1번
    assert [ws.sent for ws in sockets] == [[sockets[0].sent[0]]] * 5
    assert unpack(sockets[0].sent[0]) == json.loads(make_snapshot(first_table))
    assert len(packs) == 1

    # delta 가 오면 캐시는 버려진다 — 다음 접속자는 새 표의 snapshot 을 받는다(옛 표가 아니라)
    second_table = table([row("BTC", fwd=2.0), row("ETH")])
    hub.on_table(json.dumps(second_table))
    late = FakeWs()
    conns.append(await hub.attach(late))  # type: ignore[arg-type]
    late2 = FakeWs()
    conns.append(await hub.attach(late2))  # type: ignore[arg-type]
    await settle()
    assert unpack(late.sent[0]) == json.loads(make_snapshot(second_table))
    assert late2.sent == late.sent
    assert len(packs) == 3  # delta 1 + 새 snapshot 1

    # 0명이 되면 버린다 — 다시 첫 접속자는 latest 를 새로 읽는다
    for conn in conns:
        hub.detach(conn)
    third_table = table([row("XRP")])
    await bus.publish_table(json.dumps(third_table))
    again = FakeWs()
    await hub.attach(again)  # type: ignore[arg-type]
    await settle()
    assert unpack(again.sent[0]) == json.loads(make_snapshot(third_table))
    await hub.aclose()


async def test_first_table_after_waiting_is_the_cached_snapshot(monkeypatch) -> None:  # noqa: ANN001
    bus, _ = make_bus()
    hub = SpreadsHub(bus=bus)
    waiting = FakeWs()
    await hub.attach(waiting)  # type: ignore[arg-type]  # latest 없음 → waiting
    packs = count_packs(monkeypatch)
    hub.on_table(json.dumps(table([row("BTC")])))
    newcomer = FakeWs()
    await hub.attach(newcomer)  # type: ignore[arg-type]
    await settle()
    # waiting 접속자에게 간 snapshot 프레임을 새 접속자도 그대로 받는다 — 압축 1번
    assert waiting.sent[1] == newcomer.sent[0]
    assert unpack(newcomer.sent[0])["type"] == "snapshot"
    assert len(packs) == 1
    await hub.aclose()


# --- 2026-09-28 — 채널은 접속자가 있을 때만 구독 (§3.2) ---


class CountingBus(RedisBus):
    """구독 연결을 연 수·닫은 수를 센다."""

    def __init__(self, client) -> None:  # noqa: ANN001
        super().__init__(client)
        self.opened = 0
        self.closed = 0

    async def subscribe(self):  # noqa: ANN201
        sub = await super().subscribe()
        self.opened += 1
        close = sub.aclose

        async def counted_close() -> None:
            self.closed += 1
            await close()

        sub.aclose = counted_close  # type: ignore[method-assign]
        return sub


async def wait_until(cond, timeout: float = 2.0) -> None:  # noqa: ANN001
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, "조건이 제때 참이 되지 않았다"
        await asyncio.sleep(0.01)


async def test_hub_subscribes_only_while_someone_is_connected(monkeypatch) -> None:  # noqa: ANN001
    # 30초·1초 대신 짧은 값 — 규칙은 같다
    monkeypatch.setattr(hub_module, "IDLE_UNSUBSCRIBE_SEC", 0.3)
    monkeypatch.setattr(hub_module, "SUB_POLL_SEC", 0.02)
    server = fakeredis.FakeServer()
    bus = CountingBus(fakeredis.aioredis.FakeRedis(server=server))
    publisher = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    hub = SpreadsHub(bus=bus)
    hub.start()

    await asyncio.sleep(0.1)
    assert bus.opened == 0  # 접속자 없음 — 구독하지 않는다

    first = FakeWs()
    conn = await hub.attach(first)  # type: ignore[arg-type]
    await wait_until(lambda: bus.opened == 1)
    await publisher.publish_table(json.dumps(table([row("BTC")])))
    await wait_until(lambda: len(first.sent) >= 2)
    assert unpack(first.sent[1])["type"] == "snapshot"  # waiting 뒤 채널로 온 첫 표

    # 떠난 뒤 30초 전에 다시 붙으면 같은 구독을 이어 쓴다
    hub.detach(conn)
    await asyncio.sleep(0.15)
    conn = await hub.attach(FakeWs())  # type: ignore[arg-type]
    hub.detach(conn)
    await asyncio.sleep(0.15)
    assert (bus.opened, bus.closed) == (1, 0)

    # 마지막 접속자가 떠난 뒤 30초 — 구독을 닫는다
    await wait_until(lambda: bus.closed == 1)
    await asyncio.sleep(0.1)
    assert bus.opened == 1

    # 다음 접속자가 오면 다시 구독한다
    again = FakeWs()
    await hub.attach(again)  # type: ignore[arg-type]
    await wait_until(lambda: bus.opened == 2)
    await publisher.publish_table(json.dumps(table([row("ETH")])))
    await wait_until(lambda: len(again.sent) >= 2)
    assert unpack(again.sent[-1])["rows"][0]["sym"] == "ETH"
    await hub.aclose()
    assert bus.closed == 2


class SlowLatestBus(RedisBus):
    """`spreads:latest` 읽기를 `gate` 가 열릴 때까지 붙잡는다 — 첫 접속자의 읽기 중에 다른 접속자가 붙는 상황."""

    def __init__(self, client) -> None:  # noqa: ANN001
        super().__init__(client)
        self.gate = asyncio.Event()

    async def latest(self) -> str | None:
        await self.gate.wait()
        return await super().latest()


async def test_connections_attached_while_latest_is_loading_get_the_snapshot() -> None:
    """배포 직후 전원 재접속 — 첫 접속자의 latest 읽기 중에 붙은 접속자도 waiting 이 아니라 같은 snapshot 을 받는다.

    waiting 을 받은 접속자에게 latest 기준 delta 가 이어지면 브라우저 표에는 바뀐 행만 남는다.
    """
    bus = SlowLatestBus(fakeredis.aioredis.FakeRedis())
    await bus.publish_table(json.dumps(table([row("BTC"), row("ETH")])))
    hub = SpreadsHub(bus=bus)
    sockets = [FakeWs() for _ in range(4)]
    attaching = [asyncio.ensure_future(hub.attach(ws)) for ws in sockets]  # type: ignore[arg-type]
    await settle()
    bus.gate.set()
    await asyncio.gather(*attaching)
    hub.on_table(json.dumps(table([row("BTC", fwd=2.0), row("ETH")])))
    await settle()
    assert unpack(sockets[0].sent[0])["type"] == "snapshot"
    assert unpack(sockets[0].sent[1])["type"] == "delta"
    assert [ws.sent for ws in sockets] == [sockets[0].sent] * 4
    await hub.aclose()


class RefusedSubBus(RedisBus):
    """구독은 열리지만 첫 get 에서 거부된다 — Redis 가 메모리 상한에서 SUBSCRIBE 를 거절할 때와 같은 모양."""

    async def subscribe(self):  # noqa: ANN201
        class Refused:
            async def get(self, timeout: float) -> str | None:
                raise RuntimeError(
                    "OOM command not allowed when used memory > 'maxmemory'"
                )

            async def aclose(self) -> None:
                return None

        return Refused()


async def test_backoff_grows_while_the_subscription_is_refused(monkeypatch) -> None:  # noqa: ANN001
    """구독 오류가 첫 get 에서야 드러나도 재연결 간격은 1→2→4초로 는다 — 매초 재연결·경고를 되풀이하지 않는다."""
    waits: list[float] = []
    sleep = asyncio.sleep

    async def recorded(delay: float, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if delay >= hub_module.BACKOFF_MIN_SEC:  # 허브의 백오프만 기록하고 짧게 잔다
            waits.append(delay)
            delay = 0.001
        return await sleep(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", recorded)
    hub = SpreadsHub(
        bus=RefusedSubBus(fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer()))
    )
    hub.start()
    await hub.attach(FakeWs())  # type: ignore[arg-type]
    await wait_until(lambda: len(waits) >= 3)
    assert waits[:3] == [1, 2, 4]
    await hub.aclose()
