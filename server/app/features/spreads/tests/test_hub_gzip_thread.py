"""017 §3.2 — 허브 전용 압축 스레드(2026-10-09): 압축하는 동안 루프가 돌고, 접속마다 받는 프레임·순서는 동기 압축과 같다.

압축을 손으로 붙잡는 가짜 `pack` 으로 "압축 중" 구간을 만들고 그 사이에 접속·이탈·새 표·종료를 끼운다. 기대값은
접속마다 파싱 경로(`make_snapshot`·`make_delta`)로 만든 메시지 열이다 — 동기로 압축하던 허브가 그 접속에 넣었을 열이다.
"""

import asyncio
import gzip
import json
import threading

import fakeredis
import pytest

from app.core.redis_bus import RedisBus
from app.features.spreads import hub as hub_module
from app.features.spreads.hub import (
    WAITING,
    SpreadsHub,
    index_rows,
    make_delta,
    make_snapshot,
)
from app.features.spreads.push import encode_table
from app.features.spreads.tests.test_push import make_bus, row, table


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: int | None = None

    async def send_bytes(self, data: bytes) -> None:
        text = gzip.decompress(data).decode()
        if text != '{"type":"heartbeat"}':
            self.sent.append(text)

    async def close(self, code: int) -> None:
        self.closed = code


class HeldPack:
    """허브의 `pack` 자리 — 루프 밖(압축 스레드)에서 부르면 `release()` 전까지 붙잡는다. 부른 스레드 이름·글을 남긴다."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._real = hub_module.pack
        self._open = threading.Event()
        self._open.set()
        self.calls: list[tuple[str, str]] = []
        monkeypatch.setattr(hub_module, "pack", self)

    def __call__(self, text: str) -> bytes:
        name = threading.current_thread().name
        self.calls.append((name, text))
        if threading.current_thread() is not threading.main_thread():
            assert self._open.wait(5)
        return self._real(text)

    def hold(self) -> None:
        self._open.clear()

    def release(self) -> None:
        self._open.set()


@pytest.fixture
def to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """작은 시험 표도 스레드로 압축하게 — 기준(16KB)은 실제 표 크기에서 따로 본다."""
    monkeypatch.setattr(hub_module, "THREAD_PACK_MIN", 0)


async def wait_until(cond, timeout: float = 3.0) -> None:  # noqa: ANN001
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, "조건이 제때 참이 되지 않았다"
        await asyncio.sleep(0.005)


def t(n: int, **over: object) -> str:
    """n 번째 표 — 행 BTC·ETH(n 마다 fwd 가 바뀜), 짝수 표엔 XRP. 시각 필드로 몇 번째 표인지 드러난다."""
    rows = [row("BTC", fwd=float(n), **over), row("ETH", fwd=float(n % 3))]
    if n % 2 == 0:
        rows.append(row("XRP"))
    return encode_table({**table(rows), "fetchedAt": n})


def snap(text: str) -> str:
    return make_snapshot(json.loads(text))


def delta(prev: str, cur: str) -> str:
    return make_delta(index_rows(json.loads(prev)), json.loads(cur))[0]


async def test_loop_runs_while_compressing_and_frames_leave_in_order(
    monkeypatch, to_thread
) -> None:  # noqa: ANN001
    held = HeldPack(monkeypatch)
    bus, _ = make_bus()
    await bus.publish_table(t(0))
    hub = SpreadsHub(bus=bus)
    held.hold()
    ws = FakeWs()
    await hub.attach(ws)  # type: ignore[arg-type]  # snapshot 압축이 붙잡혀 있어도 attach 는 돌아온다
    hub.on_table(t(1))
    hub.on_table(t(2))
    await asyncio.sleep(0.05)  # 압축이 붙잡힌 동안에도 루프는 돈다
    assert ws.sent == []
    held.release()
    await wait_until(lambda: len(ws.sent) == 3)
    assert ws.sent == [snap(t(0)), delta(t(0), t(1)), delta(t(1), t(2))]
    assert {name for name, _ in held.calls} == {
        "spreads-gzip_0"
    }  # 허브 전용 스레드 하나
    await hub.aclose()


async def test_connection_attached_while_a_delta_compresses_gets_the_new_snapshot_only(
    monkeypatch, to_thread
) -> None:  # noqa: ANN001
    held = HeldPack(monkeypatch)
    bus, _ = make_bus()
    await bus.publish_table(t(0))
    hub = SpreadsHub(bus=bus)
    a, b = FakeWs(), FakeWs()
    await hub.attach(a)  # type: ignore[arg-type]
    await wait_until(lambda: len(a.sent) == 1)
    held.hold()
    hub.on_table(t(1))  # A 몫 delta — 압축 중
    await hub.attach(b)  # type: ignore[arg-type]  # 표 1 의 snapshot — 표 0→1 delta 는 받지 않는다
    hub.on_table(t(2))
    held.release()
    await wait_until(lambda: (len(a.sent), len(b.sent)) == (3, 2))
    assert a.sent == [snap(t(0)), delta(t(0), t(1)), delta(t(1), t(2))]
    assert b.sent == [snap(t(1)), delta(t(1), t(2))]
    await hub.aclose()


async def test_connections_joining_while_the_snapshot_compresses_share_one_compression(
    monkeypatch, to_thread
) -> None:  # noqa: ANN001
    held = HeldPack(monkeypatch)
    bus, _ = make_bus()
    hub = SpreadsHub(bus=bus)
    a = FakeWs()
    await hub.attach(a)  # type: ignore[arg-type]  # latest 없음 → waiting
    held.hold()
    hub.on_table(t(1))  # waiting 뒤 첫 표 = 이 표의 snapshot 칸, 압축 중
    late = [FakeWs() for _ in range(3)]
    for ws in late:
        await hub.attach(ws)  # type: ignore[arg-type]
    held.release()
    await wait_until(lambda: all(len(ws.sent) == 1 for ws in late))
    assert gzip.decompress(WAITING).decode() == a.sent[0]
    assert a.sent[1:] == [snap(t(1))]
    assert all(ws.sent == [snap(t(1))] for ws in late)
    assert [text for _, text in held.calls].count(snap(t(1))) == 1  # 압축은 한 번
    await hub.aclose()


async def test_last_detach_while_compressing_drops_the_frame_and_the_next_viewer_starts_from_latest(
    monkeypatch,  # noqa: ANN001
    to_thread,  # noqa: ANN001
) -> None:
    held = HeldPack(monkeypatch)
    bus, _ = make_bus()
    await bus.publish_table(t(0))
    hub = SpreadsHub(bus=bus)
    a = FakeWs()
    conn = await hub.attach(a)  # type: ignore[arg-type]
    await wait_until(lambda: len(a.sent) == 1)
    held.hold()
    hub.on_table(t(1))
    hub.on_table(t(2))  # 아직 스레드에 들어가지 못한 압축 — 0명이 되면 빠진다
    hub.detach(conn)
    await asyncio.sleep(0.01)  # 취소가 스레드 쪽 작업까지 닿는 루프 한 바퀴
    held.release()
    await asyncio.sleep(0.05)
    assert a.sent == [snap(t(0))]  # 버린 프레임은 아무에게도 가지 않는다
    assert hub.connections == 0
    await bus.publish_table(t(5))
    c = FakeWs()
    await hub.attach(c)  # type: ignore[arg-type]
    hub.on_table(t(6))
    await wait_until(lambda: len(c.sent) == 2)
    assert c.sent == [snap(t(5)), delta(t(5), t(6))]
    # 붙잡혔던 표 1 의 압축만 돌았고 표 2 는 스레드에서 빠졌다
    assert [text for _, text in held.calls].count(delta(t(1), t(2))) == 0
    await hub.aclose()


class GatedLatestBus(RedisBus):
    """`<id>:latest` 읽기를 `gate` 가 열릴 때까지 붙잡는다."""

    def __init__(self, client) -> None:  # noqa: ANN001
        super().__init__(client)
        self.gate = asyncio.Event()

    async def latest(self, table: str = "spreads") -> str | None:
        await self.gate.wait()
        return await super().latest(table)


async def test_table_arriving_during_the_first_latest_read_keeps_the_synchronous_order(
    monkeypatch, to_thread
) -> None:  # noqa: ANN001
    """첫 접속자의 latest 읽기 중에 표가 오면 그 표의 snapshot, 이어서 latest 의 snapshot, 그 뒤 latest 기준 delta —
    동기로 압축하던 허브가 그 접속에 넣던 순서 그대로다(압축 중인 첫 snapshot 을 latest snapshot 이 앞지르지 않는다)."""
    held = HeldPack(monkeypatch)
    bus = GatedLatestBus(fakeredis.aioredis.FakeRedis())
    await bus.publish_table(t(4))
    hub = SpreadsHub(bus=bus)
    a = FakeWs()
    attaching = asyncio.ensure_future(hub.attach(a))  # type: ignore[arg-type]
    await asyncio.sleep(0.01)
    held.hold()
    hub.on_table(t(5))  # 접속자는 있지만 상태가 없다 — snapshot, 압축 중
    bus.gate.set()
    await attaching  # latest(표 4) 의 snapshot 이 그 뒤에 선다
    hub.on_table(t(6))
    held.release()
    await wait_until(lambda: len(a.sent) == 3)
    assert a.sent == [snap(t(5)), snap(t(4)), delta(t(4), t(6))]
    await hub.aclose()


async def test_aclose_while_compressing_sends_nothing_more(
    monkeypatch, to_thread
) -> None:  # noqa: ANN001
    held = HeldPack(monkeypatch)
    bus, _ = make_bus()
    await bus.publish_table(t(0))
    hub = SpreadsHub(bus=bus)
    a = FakeWs()
    await hub.attach(a)  # type: ignore[arg-type]
    await wait_until(lambda: len(a.sent) == 1)
    held.hold()
    hub.on_table(t(1))
    await hub.aclose()
    held.release()
    await asyncio.sleep(0.05)
    assert a.sent == [snap(t(0))] and a.closed == 1001
    # 닫은 뒤 다시 붙어도 새 스레드로 돈다
    await bus.publish_table(t(2))
    b = FakeWs()
    await hub.attach(b)  # type: ignore[arg-type]
    await wait_until(lambda: len(b.sent) == 1)
    assert b.sent == [snap(t(2))]
    await hub.aclose()


async def test_failed_compression_resends_a_snapshot_to_everyone(
    monkeypatch, to_thread, caplog
) -> None:  # noqa: ANN001
    real = hub_module.pack
    fail_on = delta(t(0), t(1))

    def flaky(text: str) -> bytes:
        if text == fail_on:
            raise MemoryError("압축 실패")
        return real(text)

    monkeypatch.setattr(hub_module, "pack", flaky)
    bus, _ = make_bus()
    await bus.publish_table(t(0))
    hub = SpreadsHub(bus=bus)
    a = FakeWs()
    await hub.attach(a)  # type: ignore[arg-type]
    hub.on_table(t(1))
    await wait_until(lambda: "압축 실패" in caplog.text)
    hub.on_table(t(2))  # 상태를 버렸으니 이 표는 전원에게 snapshot
    await wait_until(lambda: len(a.sent) == 2)
    assert a.sent == [snap(t(0)), snap(t(2))]
    await hub.aclose()


async def _scripted(threshold: int, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """접속·이탈·빈 표가 섞인 한 흐름 — 매 표 뒤 그 표의 프레임이 접속마다 다 나간 것을 보고 다음으로 간다."""
    monkeypatch.setattr(hub_module, "THREAD_PACK_MIN", threshold)
    bus, _ = make_bus()
    await bus.publish_table(t(0))
    hub = SpreadsHub(bus=bus)
    socks = [FakeWs() for _ in range(3)]
    expect = [0, 0, 0]
    live: dict[int, object] = {}

    async def attach(i: int) -> None:
        live[i] = await hub.attach(socks[i])  # type: ignore[arg-type]
        expect[i] += 1

    await attach(0)
    for n in range(1, 9):
        if n == 3:
            await attach(1)
        if n == 6:
            hub.detach(live.pop(0))  # type: ignore[arg-type]
            await attach(2)
        hub.on_table(t(n) if n != 4 else encode_table(table([])))
        for i in live:
            expect[i] += 1
        await wait_until(lambda: [len(s.sent) for s in socks] == expect)
    await asyncio.sleep(0.05)
    await hub.aclose()
    return [s.sent for s in socks]


async def test_thread_and_loop_compression_send_identical_frames(monkeypatch) -> None:  # noqa: ANN001
    on_loop = await _scripted(10**9, monkeypatch)
    on_thread = await _scripted(0, monkeypatch)
    assert on_thread == on_loop
    assert [len(s) for s in on_loop] == [6, 7, 4]


async def test_real_size_frames_go_to_the_thread_and_small_ones_stay_on_the_loop(
    monkeypatch,
) -> None:  # noqa: ANN001
    """기준 16KB — 빈 delta·작은 표는 루프에서, 수백 행 표는 허브 전용 스레드에서. 작은 프레임도 앞 프레임을 앞지르지 않는다."""
    held = HeldPack(monkeypatch)
    big = [row(f"C{i}", fwd=float(i)) for i in range(120)]
    t0 = encode_table({**table(big), "fetchedAt": 0})
    t1 = encode_table(
        {**table([{**r, "fwd": r["fwd"] + 0.5} for r in big]), "fetchedAt": 1}
    )  # 전 행 바뀜 — 큰 delta
    t2 = encode_table(
        {**table([{**r, "fwd": r["fwd"] + 0.5} for r in big]), "fetchedAt": 2}
    )  # 바뀐 행 0 — 작은 delta
    bus, _ = make_bus()
    await bus.publish_table(t0)
    hub = SpreadsHub(bus=bus)
    a = FakeWs()
    await hub.attach(a)  # type: ignore[arg-type]
    await wait_until(lambda: len(a.sent) == 1)
    held.hold()
    hub.on_table(t1)
    hub.on_table(t2)  # 루프에서 바로 압축되지만 큰 delta 뒤에 선다
    await asyncio.sleep(0.03)
    assert len(a.sent) == 1
    held.release()
    await wait_until(lambda: len(a.sent) == 3)
    assert a.sent == [snap(t0), delta(t0, t1), delta(t1, t2)]
    names = {text: name for name, text in held.calls}
    assert names[snap(t0)] == names[delta(t0, t1)] == "spreads-gzip_0"
    assert names[delta(t1, t2)] == "MainThread"
    await hub.aclose()
