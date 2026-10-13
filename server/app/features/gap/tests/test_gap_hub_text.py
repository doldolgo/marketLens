"""048 §3.4 — gap 허브의 표 글 diff·압축 스레드(2026-10-09). 허브 클래스는 017 의 것이라 규칙은 spreads 와 같다.

기준은 바꾸기 전 허브의 파싱 경로 `make_delta(..., gap_row_key)`·`make_snapshot` 이다. 표 글은 게시기 인코더
`encode_gap_table` 로 만든다. gap 행은 키 필드(sym·spot·perp) 뒤 첫 필드가 `spotPrice` 이고 `age` 가 마지막 키다.
"""

import asyncio
import gzip
import json
import random
import threading

import pytest

from app.core.live_store import LiveStore
from app.core.redis_bus import GAP_CHANNEL
from app.features.gap.push import encode_gap_table
from app.features.gap.service import build_gap_table
from app.features.gap.tests.helpers import T0, gap_row, gap_table, make_bus, perp, spot
from app.features.spreads import hub as hub_module
from app.features.spreads.hub import (
    SpreadsHub,
    gap_row_key,
    index_rows,
    index_text,
    make_delta,
    make_delta_text,
    make_snapshot,
    snapshot_text,
)

SPOTS = ("binance", "bybit", "bitget", "okx")
PERPS = ("binance_perp", "bybit_perp", "bitget_perp", "hyperliquid_perp")
# 행 경계·age 표식·-0.0 을 흉내 내는 sym — 이스케이프된 따옴표라 표식으로 읽히면 안 된다
ODD_SYMS = ('A"B', "C\\D", '},{"sym":"Z', ',"age":1', "x:-0.0,", "이더")


def _row(rng: random.Random, sym: str, sp: str, pp: str) -> dict:
    """build_gap_table 의 11키 행 — 키 순서까지 같게."""
    fail = rng.random() < 0.1
    return gap_row(
        sym,
        spot=sp,
        perp=pp,
        spotPrice=0.0 if fail else rng.uniform(0.001, 1e5),
        entry=0.0 if fail else rng.uniform(-1, 1),
        exit=0.0 if fail else rng.uniform(-1, 1),
        funding=rng.choice([None, 0.0, -0.0, rng.uniform(-0.1, 0.1)]),
        intervalH=rng.choice([None, 1, 4, 8]),
        nextFundingTs=rng.choice([None, 1_791_475_200]),
        status="fail" if fail else rng.choice(["ok", "ok", "stale"]),
        age=rng.choice([0.0, 0.5, 3.0]),
    )


def _texts(seed_: int, n: int, rows: int = 60) -> list[str]:
    rng = random.Random(seed_)
    syms = [f"S{i}" for i in range(rows // 4)] + list(ODD_SYMS)
    cur = [_row(rng, s, rng.choice(SPOTS), p) for s in syms for p in rng.sample(PERPS, 2)]
    serial = 0
    out: list[str] = []
    for i in range(n):
        if i:
            nxt: list[dict] = []
            for r in cur:
                if rng.random() < 0.04:
                    continue  # 사라짐 → removed
                roll = rng.random()
                if roll < 0.2:
                    r = {**r, "age": r["age"] + 1.0}  # age 만
                elif roll < 0.3:
                    f = r["funding"]
                    # funding 0.0 ↔ -0.0 만 — 파싱 경로는 같은 값으로 본다
                    r = {**r, "funding": (0.0 if str(f) == "-0.0" else -0.0) if f == 0.0 else f}
                elif roll < 0.5:
                    r = {**r, "entry": rng.uniform(-1, 1), "exit": rng.uniform(-1, 1)}
                elif roll < 0.55:
                    r = {**r, "status": "stale" if r["status"] == "ok" else "ok"}
                nxt.append(r)
            for _ in range(rng.randint(0, 3)):
                serial += 1
                nxt.insert(rng.randrange(len(nxt) + 1), _row(rng, f"N{serial}", rng.choice(SPOTS), rng.choice(PERPS)))
            cur = nxt
        table = gap_table(cur if rng.random() > 0.03 else [])
        table["warnings"] = ["현물 시세가 늦다 — 표시용"] if rng.random() < 0.3 else []
        table["fetchedAt"] = i
        out.append(encode_gap_table(table))
    return out


@pytest.mark.parametrize("seed_", range(30))
def test_gap_text_delta_and_snapshot_are_the_parse_path_bytes(seed_: int) -> None:
    texts = _texts(seed_, 8)
    idx = index_text(texts[0], GAP_CHANNEL)
    assert idx is not None
    prev = index_rows(json.loads(texts[0]), gap_row_key)
    for t in texts[1:]:
        want, prev = make_delta(prev, json.loads(t), gap_row_key)
        done = make_delta_text(idx, t, GAP_CHANNEL)
        assert done is not None
        assert done[0] == want
        idx = done[1]
        assert snapshot_text(t) == make_snapshot(json.loads(t))


def test_funding_zero_sign_only_is_not_a_changed_row() -> None:
    a = gap_row("BTC", funding=0.0, entry=0.25)
    texts = [
        encode_gap_table(gap_table([a])),
        encode_gap_table(gap_table([{**a, "funding": -0.0, "age": 7.0}])),  # 0 부호와 age 만
        encode_gap_table(gap_table([{**a, "funding": -0.0, "entry": -0.0}])),  # entry 0.25 → -0.0 — 바뀐 행
    ]
    idx = index_text(texts[0], GAP_CHANNEL)
    prev = index_rows(json.loads(texts[0]), gap_row_key)
    got: list[int] = []
    for t in texts[1:]:
        want, prev = make_delta(prev, json.loads(t), gap_row_key)
        msg, idx = make_delta_text(idx, t, GAP_CHANNEL)  # type: ignore[arg-type, misc]
        assert msg == want
        got.append(len(json.loads(msg)["rows"]))
    assert got == [0, 1]


def test_gap_table_contract_the_text_path_relies_on() -> None:
    """gap 행은 sym·spot·perp·spotPrice 로 시작하고 age 가 마지막, 최상위에서 rows 다음이 warnings — 게시기 글은 글 경로다."""
    store = LiveStore()
    spot(store, "binance", "BTC")
    spot(store, "bybit", "ETH")
    perp(store, "bybit_perp", "BTC")
    perp(store, "binance_perp", "ETH", funding_rate=-0.0)
    table = build_gap_table(store, now=T0)
    keys = list(table)
    assert keys[keys.index("rows") + 1] == "warnings"
    assert "type" not in table and "removed" not in table
    assert table["rows"]
    for r in table["rows"]:  # type: ignore[union-attr]
        assert list(r)[:4] == ["sym", "spot", "perp", "spotPrice"] and list(r)[-1] == "age"
        assert not any(isinstance(v, dict) for v in r.values())
    text = encode_gap_table(table)
    assert index_text(text, GAP_CHANNEL) is not None
    assert snapshot_text(text) == make_snapshot(json.loads(text))


def test_gap_key_between_key_fields_and_spot_price_falls_back() -> None:
    rows = [{"sym": "BTC", "spot": "binance", "perp": "bybit_perp", "venue": "x", **gap_row("BTC")}]
    assert index_text(encode_gap_table(gap_table(rows)), GAP_CHANNEL) is None
    assert index_text(json.dumps(gap_table([gap_row("BTC")])), GAP_CHANNEL) is None  # 공백 있는 글


# --- 허브 끝까지 ---


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_bytes(self, data: bytes) -> None:
        text = gzip.decompress(data).decode()
        if text != '{"type":"heartbeat"}':
            self.sent.append(text)

    async def close(self, code: int) -> None:
        return None


async def wait_until(cond, timeout: float = 3.0) -> None:  # noqa: ANN001
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, "조건이 제때 참이 되지 않았다"
        await asyncio.sleep(0.005)


async def test_gap_hub_attach_during_compression_and_parse_path_bytes(monkeypatch) -> None:  # noqa: ANN001
    """gap 표(200여 행, 스레드 압축) — 압축 중에 붙은 접속자는 새 표의 snapshot 만 받고 그 delta 는 받지 않는다.
    접속마다 받은 글은 파싱 경로로 만든 기대값과 같고, 압축은 gap 허브 전용 스레드에서 돈다. 파싱 경로는 타지 않는다."""
    real_pack = hub_module.pack
    open_ = threading.Event()
    open_.set()
    names: set[str] = set()

    def held(text: str) -> bytes:
        names.add(threading.current_thread().name)
        if threading.current_thread() is not threading.main_thread():
            assert open_.wait(5)
        return real_pack(text)

    parsed: list[int] = []
    real_delta = hub_module.make_delta

    def counting(*args, **kw):  # noqa: ANN002, ANN003, ANN202
        parsed.append(1)
        return real_delta(*args, **kw)

    monkeypatch.setattr(hub_module, "pack", held)
    monkeypatch.setattr(hub_module, "make_delta", counting)
    texts = _texts(7, 6, rows=200)
    bus, _ = make_bus()
    await bus.publish_table(texts[0], GAP_CHANNEL)
    hub = SpreadsHub(bus=bus, table=GAP_CHANNEL)
    a, b = FakeWs(), FakeWs()
    await hub.attach(a)  # type: ignore[arg-type]
    await wait_until(lambda: len(a.sent) == 1)
    open_.clear()
    hub.on_table(texts[1])
    await hub.attach(b)  # type: ignore[arg-type]
    for t in texts[2:]:
        hub.on_table(t)
    open_.set()

    def d(i: int) -> str:
        return make_delta(index_rows(json.loads(texts[i - 1]), gap_row_key), json.loads(texts[i]), gap_row_key)[0]

    want_a = [make_snapshot(json.loads(texts[0]))] + [d(i) for i in range(1, 6)]
    want_b = [make_snapshot(json.loads(texts[1]))] + [d(i) for i in range(2, 6)]
    await wait_until(lambda: (len(a.sent), len(b.sent)) == (len(want_a), len(want_b)))
    assert a.sent == want_a
    assert b.sent == want_b
    assert "gap-gzip_0" in names
    assert parsed == []
    await hub.aclose()
