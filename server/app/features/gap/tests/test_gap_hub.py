"""048 §4 — 허브 일반화: 채널 `gap`·키 `gap:latest`·행 키 `sym|spot|perp`, 두 허브 분리, want 미갱신 (fakeredis, 가짜 소켓).

허브 클래스는 017 의 것(`features/spreads/hub.py`)을 표 id 로 만든 인스턴스다 — spreads 쪽 테스트는 그대로 둔다.
"""

import asyncio
import gzip
import json

import fakeredis

from app.core.redis_bus import GAP_CHANNEL, WANT_KEY, RedisBus
from app.features.gap.tests.helpers import gap_row, gap_table, make_bus
from app.features.spreads import hub as hub_module
from app.features.spreads.hub import (
    CODE_SLOW,
    HEARTBEAT,
    SEND_QUEUE_LIMIT,
    WAITING,
    SpreadsHub,
    gap_row_key,
    make_delta,
)


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


async def wait_until(cond, timeout: float = 2.0) -> None:  # noqa: ANN001
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, "조건이 제때 참이 되지 않았다"
        await asyncio.sleep(0.01)


def test_delta_keys_rows_by_sym_spot_perp_and_lists_removed_in_that_format() -> None:
    _, prev = make_delta({}, gap_table([gap_row("BTC"), gap_row("ETH")]), gap_row_key)
    assert set(prev) == {"BTC|binance|bybit_perp", "ETH|binance|bybit_perp"}
    text, _ = make_delta(
        prev,
        gap_table(
            [gap_row("BTC", age=4.0), gap_row("ETH", entry=0.9)]
        ),  # BTC 는 age 만
        gap_row_key,
    )
    delta = json.loads(text)
    assert [r["sym"] for r in delta["rows"]] == ["ETH"]
    assert set(delta) == {
        "type",
        "rows",
        "removed",
        "warnings",
        "dataReceivedAt",
        "fetchedAt",
    }
    text, _ = make_delta(prev, gap_table([gap_row("ETH")]), gap_row_key)
    assert json.loads(text)["removed"] == ["BTC|binance|bybit_perp"]


async def test_gap_hub_starts_from_gap_latest_and_never_touches_want() -> None:
    bus, _ = make_bus()
    await bus.publish_table(json.dumps(gap_table([gap_row("BTC")])), GAP_CHANNEL)
    hub = SpreadsHub(bus=bus, table=GAP_CHANNEL)
    assert hub.table_id == "gap"
    ws = FakeWs()
    await hub.attach(ws)  # type: ignore[arg-type]
    await settle()
    snapshot = unpack(ws.sent[0])
    assert (
        snapshot["type"] == "snapshot" and snapshot["rows"][0]["perp"] == "bybit_perp"
    )
    assert (
        await bus._client.exists(WANT_KEY) == 0
    )  # spreads:want 는 gap 접속으로 갱신되지 않는다
    await hub.aclose()
    assert ws.closed == 1001


async def test_gap_hub_waiting_then_snapshot_delta_heartbeat_and_slow_client() -> None:
    bus, _ = make_bus()
    hub = SpreadsHub(bus=bus, table=GAP_CHANNEL)
    ws = FakeWs()
    conn = await hub.attach(ws)  # type: ignore[arg-type]
    await settle()
    assert ws.sent == [WAITING]
    hub.on_table(json.dumps(gap_table([gap_row("BTC")])))
    hub.on_table(json.dumps(gap_table([gap_row("BTC", entry=1.0), gap_row("ETH")])))
    await settle()
    assert unpack(ws.sent[1])["type"] == "snapshot"
    delta = unpack(ws.sent[2])
    assert delta["type"] == "delta" and [r["sym"] for r in delta["rows"]] == [
        "BTC",
        "ETH",
    ]
    await asyncio.sleep(hub_module.HEARTBEAT_SEC + 0.2)
    assert ws.sent[-1] == HEARTBEAT
    hub.detach(conn)

    slow = FakeWs(block=True)
    slow_conn = await hub.attach(slow)  # type: ignore[arg-type]
    await settle()
    for i in range(SEND_QUEUE_LIMIT + 2):
        hub.on_table(json.dumps(gap_table([gap_row("BTC", entry=float(i))])))
        await settle()
    assert slow.closed == CODE_SLOW
    hub.detach(slow_conn)
    await hub.aclose()


async def test_two_hubs_subscribe_their_own_channel_only_and_unsubscribe_when_idle(
    monkeypatch,  # noqa: ANN001
) -> None:
    monkeypatch.setattr(hub_module, "IDLE_UNSUBSCRIBE_SEC", 0.3)
    monkeypatch.setattr(hub_module, "SUB_POLL_SEC", 0.02)
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    publisher = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    spreads_hub = SpreadsHub(bus=bus)
    gap_hub = SpreadsHub(bus=bus, table=GAP_CHANNEL)
    spreads_hub.start()
    gap_hub.start()

    s_ws, g_ws = FakeWs(), FakeWs()
    s_conn = await spreads_hub.attach(s_ws)  # type: ignore[arg-type]
    g_conn = await gap_hub.attach(g_ws)  # type: ignore[arg-type]
    await wait_until(
        lambda: server.connected and len(s_ws.sent) == 1 and len(g_ws.sent) == 1
    )
    await asyncio.sleep(0.1)  # 두 구독이 열릴 시간
    spreads_table = {
        "notional": 1000.0,
        "rate": 1400.0,
        "rows": [],
        "warnings": [],
        "dataReceivedAt": 1,
        "fetchedAt": 2,
    }
    await publisher.publish_table(json.dumps(spreads_table))
    await publisher.publish_table(json.dumps(gap_table([gap_row("BTC")])), GAP_CHANNEL)
    await wait_until(lambda: len(s_ws.sent) >= 2 and len(g_ws.sent) >= 2)
    await asyncio.sleep(0.1)
    # 각자 자기 채널의 표 1장만 — 서로의 채널을 받지 않는다
    assert [unpack(f)["type"] for f in s_ws.sent] == ["waiting", "snapshot"]
    assert unpack(s_ws.sent[1])["rate"] == 1400.0
    assert [unpack(f)["type"] for f in g_ws.sent] == ["waiting", "snapshot"]
    assert unpack(g_ws.sent[1])["rows"][0]["perp"] == "bybit_perp"

    # gap 접속자가 떠나고 30초(여기선 0.3초) — gap 구독만 닫힌다, spreads 는 계속 받는다
    gap_hub.detach(g_conn)
    await asyncio.sleep(0.5)
    await publisher.publish_table(json.dumps({**spreads_table, "rate": 1401.0}))
    await wait_until(lambda: len(s_ws.sent) >= 3)
    assert unpack(s_ws.sent[2])["rate"] == 1401.0
    assert gap_hub.connections == 0
    spreads_hub.detach(s_conn)
    await spreads_hub.aclose()
    await gap_hub.aclose()
