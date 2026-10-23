"""049 §4 — 허브 세 번째 인스턴스: 채널 `pp`·키 `pp:latest`·행 키 `sym|a|b`·`removed` 형식, 표 글 diff 가 파싱 경로와
같은 바이트, 세 허브가 서로의 채널을 받지 않음 (fakeredis, 가짜 소켓).

허브 클래스는 017 의 것(`features/spreads/hub.py`)이고 049 는 표 id 만 등록했다 — spreads·gap 쪽 테스트는 그대로 둔다.
"""

import asyncio
import gzip
import json
import random

import fakeredis
import pytest

from app.core.redis_bus import GAP_CHANNEL, PP_CHANNEL, WANT_KEY, RedisBus, want_key
from app.features.pp.push import encode_pp_table
from app.features.pp.tests.helpers import make_bus, pp_row, pp_table
from app.features.spreads import hub as hub_module
from app.features.spreads.hub import (
    SpreadsHub,
    index_rows,
    index_text,
    make_delta,
    make_delta_text,
    make_snapshot,
    pp_row_key,
    snapshot_text,
)

PERPS = ("binance_perp", "bybit_perp", "bitget_perp", "hyperliquid_perp")
# 행 경계·age 표식·-0.0 을 흉내 내는 sym — 이스케이프된 따옴표라 표식으로 읽히면 안 된다
ODD_SYMS = ('A"B', "C\\D", '},{"sym":"Z', ',"age":1', ',"longA":2', "x:-0.0,", "이더")


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed: int | None = None

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self, code: int) -> None:
        self.closed = code


def unpack(frame: bytes) -> dict:
    return json.loads(gzip.decompress(frame))


async def wait_until(cond, timeout: float = 2.0) -> None:  # noqa: ANN001
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, "조건이 제때 참이 되지 않았다"
        await asyncio.sleep(0.01)


def test_delta_keys_rows_by_sym_a_b_and_lists_removed_in_that_format() -> None:
    _, prev = make_delta({}, pp_table([pp_row("BTC"), pp_row("ETH")]), pp_row_key)
    assert set(prev) == {"BTC|binance_perp|bybit_perp", "ETH|binance_perp|bybit_perp"}
    text, _ = make_delta(
        prev,
        pp_table([pp_row("BTC", age=4.0), pp_row("ETH", longA=0.1)]),  # BTC 는 age 만
        pp_row_key,
    )
    assert [r["sym"] for r in json.loads(text)["rows"]] == ["ETH"]
    text, _ = make_delta(prev, pp_table([pp_row("ETH")]), pp_row_key)
    assert json.loads(text)["removed"] == ["BTC|binance_perp|bybit_perp"]


def _row(rng: random.Random, sym: str, a: str, b: str) -> dict:
    fail = rng.random() < 0.1
    return pp_row(
        sym,
        a=a,
        b=b,
        longA=0.0 if fail else rng.uniform(-1, 1),
        longB=0.0 if fail else rng.uniform(-1, 1),
        fundA=rng.choice([None, 0.0, -0.0, rng.uniform(-0.1, 0.1)]),
        intA=rng.choice([None, 1, 4, 8]),
        nextA=rng.choice([None, 1_791_475_200]),
        fundB=rng.choice([None, 0.0, -0.0, rng.uniform(-0.1, 0.1)]),
        intB=rng.choice([None, 1, 4, 8]),
        nextB=rng.choice([None, 1_791_464_400]),
        status="fail" if fail else rng.choice(["ok", "ok", "stale"]),
        age=rng.choice([0.0, 0.5, 3.0]),
    )


def _texts(seed_: int, n: int) -> list[str]:
    rng = random.Random(seed_)
    syms = [f"S{i}" for i in range(20)] + list(ODD_SYMS)
    cur = []
    for s in syms:
        a, b = sorted(rng.sample(PERPS, 2), key=PERPS.index)
        cur.append(_row(rng, s, a, b))
    out: list[str] = []
    for i in range(n):
        if i:
            nxt: list[dict] = []
            for r in cur:
                if rng.random() < 0.05:
                    continue  # 사라짐 → removed
                roll = rng.random()
                if roll < 0.2:
                    r = {**r, "age": r["age"] + 1.0}  # age 만 — 안 바뀐 행
                elif roll < 0.3:
                    f = r["fundA"]
                    if f == 0.0:  # 0.0 ↔ -0.0 만 — 파싱 경로는 같은 값으로 본다
                        r = {**r, "fundA": 0.0 if str(f) == "-0.0" else -0.0}
                elif roll < 0.5:
                    r = _row(rng, r["sym"], r["a"], r["b"])
                nxt.append(r)
            if rng.random() < 0.5:
                nxt.append(_row(rng, f"N{i}", "bybit_perp", "hyperliquid_perp"))
            cur = nxt
        out.append(encode_pp_table(pp_table(cur)))
    return out


@pytest.mark.parametrize("seed_", range(8))
def test_pp_text_delta_and_snapshot_are_the_parse_path_bytes(seed_: int) -> None:
    texts = _texts(seed_, 6)
    prev_text = index_text(texts[0], PP_CHANNEL)
    prev_parse = index_rows(json.loads(texts[0]), pp_row_key)
    assert prev_text is not None and len(prev_text) == len(prev_parse)
    assert snapshot_text(texts[0]) == make_snapshot(json.loads(texts[0]))
    for text in texts[1:]:
        done = make_delta_text(prev_text, text, PP_CHANNEL)
        assert done is not None, "게시기 글은 글 경로로 가야 한다"
        message, prev_text = done
        ref, prev_parse = make_delta(prev_parse, json.loads(text), pp_row_key)
        assert message == ref


def test_pp_key_between_key_fields_and_long_a_falls_back_to_parse_path() -> None:
    """키 필드(sym·a·b)와 `longA` 사이에 필드가 끼면 글 경로가 받지 않는다 — 파싱 경로로 간다."""
    row = pp_row("BTC")
    odd = {"sym": "BTC", "a": row["a"], "x": 1, "b": row["b"], **row}
    text = encode_pp_table(pp_table([odd]))
    assert index_text(text, PP_CHANNEL) is None


async def test_pp_hub_starts_from_pp_latest_and_writes_only_pp_want() -> None:
    bus, _ = make_bus()
    await bus.publish_table(encode_pp_table(pp_table([pp_row("BTC")])), PP_CHANNEL)
    hub = SpreadsHub(bus=bus, table=PP_CHANNEL)
    assert hub.table_id == "pp"
    ws = FakeWs()
    await hub.attach(ws)  # type: ignore[arg-type]
    await wait_until(lambda: len(ws.sent) == 1)
    snapshot = unpack(ws.sent[0])
    assert snapshot["type"] == "snapshot" and snapshot["rows"][0]["b"] == "bybit_perp"
    assert len(snapshot["rows"][0]) == 13
    assert (
        await bus._client.exists(WANT_KEY) == 0
    )  # spreads:want 는 pp 접속으로 갱신되지 않는다
    assert await bus._client.exists(want_key(GAP_CHANNEL)) == 0
    # pp:want 는 첫 접속 때 곧바로 — 수집이 이 키를 보고 pp 표를 만들기 시작한다 (049 §3.3)
    assert await bus._client.ttl(want_key(PP_CHANNEL)) == 15
    await hub.aclose()
    assert ws.closed == 1001


async def test_three_hubs_subscribe_their_own_channel_only(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(hub_module, "SUB_POLL_SEC", 0.02)
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    publisher = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    hubs = {
        "spreads": SpreadsHub(bus=bus),
        GAP_CHANNEL: SpreadsHub(bus=bus, table=GAP_CHANNEL),
        PP_CHANNEL: SpreadsHub(bus=bus, table=PP_CHANNEL),
    }
    sockets = {k: FakeWs() for k in hubs}
    for k, hub in hubs.items():
        hub.start()
        await hub.attach(sockets[k])  # type: ignore[arg-type]
    await wait_until(lambda: all(len(ws.sent) == 1 for ws in sockets.values()))
    await asyncio.sleep(0.1)  # 세 구독이 열릴 시간
    spreads_table = {
        "notional": 1000.0,
        "rate": 1400.0,
        "rows": [],
        "warnings": [],
        "dataReceivedAt": 1,
        "fetchedAt": 2,
    }
    gap_table = {"rows": [], "warnings": [], "dataReceivedAt": 3, "fetchedAt": 4}
    spreads_bytes = json.dumps(spreads_table)
    await publisher.publish_table(spreads_bytes)
    await publisher.publish_table(json.dumps(gap_table), GAP_CHANNEL)
    await publisher.publish_table(
        encode_pp_table(pp_table([pp_row("BTC")])), PP_CHANNEL
    )
    await wait_until(lambda: all(len(ws.sent) >= 2 for ws in sockets.values()))
    await asyncio.sleep(0.1)
    for ws in sockets.values():
        assert [unpack(f)["type"] for f in ws.sent] == ["waiting", "snapshot"]
    assert unpack(sockets["spreads"].sent[1]) == {"type": "snapshot", **spreads_table}
    assert unpack(sockets[GAP_CHANNEL].sent[1])["dataReceivedAt"] == 3
    assert unpack(sockets[PP_CHANNEL].sent[1])["rows"][0]["sym"] == "BTC"
    for hub in hubs.values():
        await hub.aclose()
