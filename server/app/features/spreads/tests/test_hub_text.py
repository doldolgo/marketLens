"""017 §3.2 — 허브의 표 글 diff(2026-10-09): 표를 파싱하지 않고 만든 delta·snapshot 이 파싱 경로와 같은 바이트인가.

기준은 바꾸기 전 허브가 쓰던 파싱 경로 `make_delta(index_rows(json.loads(직전)), json.loads(새 표))`·`make_snapshot` 이다.
표 글은 게시기 인코더(`encode_table`·`encode_table_with_spark_json`·글 갈래 `encode_text_table`)로 만든다 — 허브가 받는 것과
같은 글이다. 글 모양이 다르면(공백 있는 인코더·`\\u` 이스케이프·키 사이에 낀 필드·같은 키 행 둘) 글 경로는 None 이고
허브는 파싱 경로로 간다. gap 표는 `gap/tests/test_gap_hub_text.py`, 압축 스레드·프레임 순서는 `test_hub_gzip_thread.py`.
"""

import asyncio
import gzip
import json
import lzma
import random

import pytest

from app.core.live_store import LiveStore
from app.core.redis_bus import CHANNEL
from app.features.spreads import hub as hub_module
from app.features.spreads.hub import (
    SpreadsHub,
    index_rows,
    index_text,
    make_delta,
    make_delta_text,
    make_snapshot,
    snapshot_text,
    split_table,
)
from app.features.spreads.push import (
    encode_table,
    encode_table_with_spark_json,
    encode_text_table,
)
from app.features.spreads.service import build_table
from app.features.spreads.tests.test_push import make_bus, seed
from tests.test_real_tables import DATA, FIELDS

DOMS = ("upbit", "bithumb")
FXS = ("binance", "bybit", "bitget", "okx")
# 행 경계·age 표식·-0.0 을 흉내 내는 문자열 — 이스케이프된 따옴표라 표식으로 읽히면 안 된다
ODD = (
    'Arb "One"',
    "a\\b",
    "이더리움 (ERC20)",
    "x:-0.0,y",
    '},{"sym":"Z',
    ',"age":1,',
    '"spark":[-0.0]',
    "😀 ",
)
WARN = "업비트 USDT 시세가 12초째 갱신되지 않았습니다 — 이 거래소 행의 김프/역프는 낡은 시세 기준입니다."


def _row(rng: random.Random, sym: str, dom: str, fx: str) -> dict:
    """build_table 의 19키 행 — 키 순서까지 같게."""
    return {
        "sym": sym,
        "dom": dom,
        "fx": fx,
        "fwd": rng.uniform(-5, 5),
        "rev": rng.uniform(-5, 5),
        "usd": rng.choice([rng.uniform(0.001, 1e5), 0.0]),
        "spark": [round(rng.uniform(-2, 2), 3) for _ in range(rng.randint(0, 30))],
        "status": rng.choice(["ok", "ok", "ok", "stale", "fail"]),
        "age": rng.choice([0.0, 0.5, 2.0, 7.25]),
        "slipFwd": rng.choice([0.0, rng.uniform(0, 1)]),
        "slipRev": rng.choice([0.0, rng.uniform(0, 1)]),
        "krw": rng.uniform(1, 1e8),
        "netDom": rng.choice([None, "Bitcoin", *ODD]),
        "depDom": rng.choice([None, True, False]),
        "wdDom": rng.choice([None, True, False]),
        "depFx": rng.choice([None, True, False]),
        "wdFx": rng.choice([None, True, False]),
        "netFx": rng.choice([None, "Ethereum (ERC20)", *ODD]),
        "dayChg": rng.choice([None, rng.uniform(-10, 10), 0.0]),
    }


def _table(
    rows: list[dict],
    *,
    rate: float = 1400.0,
    warnings: list[str] | None = None,
    at: int = 0,
) -> dict:
    """build_table 의 최상위 — rate·notional·rows·warnings·시각 둘 순서."""
    return {
        "rate": rate,
        "notional": 1000.0,
        "rows": rows,
        "warnings": warnings if warnings is not None else [],
        "dataReceivedAt": 1_791_522_000_000 + at,
        "fetchedAt": 1_791_522_000_500 + at,
    }


def _next(rng: random.Random, rows: list[dict], serial: list[int]) -> list[dict]:
    """다음 초의 행 — 일부만 바뀌고(age 만 바뀐 행·0.0↔-0.0 만 바뀐 행 포함), 몇 행은 사라지고 새로 생긴다."""
    out: list[dict] = []
    for r in rows:
        if rng.random() < 0.04:
            continue  # 사라짐 → removed
        r = dict(r, spark=list(r["spark"]))
        roll = rng.random()
        if roll < 0.15:
            r["age"] = r["age"] + 1.0  # age 만 — 안 실린다
        elif roll < 0.25:
            # spark 끝값 0.0 ↔ -0.0 — 파싱 경로는 같은 값으로 본다
            if r["spark"]:
                r["spark"][-1] = -0.0 if str(r["spark"][-1]) != "-0.0" else 0.0
            else:
                r["spark"] = [-0.0]  # 빈 목록 → 값 하나는 바뀐 행
        elif roll < 0.3:
            r["dayChg"] = (
                -0.0 if r["dayChg"] == 0.0 and str(r["dayChg"]) != "-0.0" else 0.0
            )
        elif roll < 0.45:
            r["fwd"] = rng.uniform(-5, 5)
            r["spark"] = [*r["spark"][1:], round(rng.uniform(-2, 2), 3)]
        elif roll < 0.5:
            r["status"] = "stale" if r["status"] == "ok" else "ok"
        elif roll < 0.55:
            r["netDom"] = rng.choice([None, *ODD])
        out.append(r)
    for _ in range(rng.randint(0, 3)):
        serial[0] += 1
        out.insert(
            rng.randrange(len(out) + 1),
            _row(rng, f"N{serial[0]}", rng.choice(DOMS), rng.choice(FXS)),
        )
    return out


def _encode(rng: random.Random, table: dict) -> str:
    """게시기의 두 dict 갈래 인코더 중 하나 — 둘 다 같은 바이트다(017 §3.1)."""
    if rng.random() < 0.5:
        return encode_table(table)
    frags = {
        (r["dom"], r["fx"], r["sym"]): json.dumps(r["spark"], separators=(",", ":"))
        for r in table["rows"]
        if rng.random() < 0.8
    }
    return encode_table_with_spark_json(table, frags)  # type: ignore[arg-type]


def _texts(seed_: int, n: int, rows: int = 40) -> list[str]:
    rng = random.Random(seed_)
    cur = [_row(rng, f"S{i}", rng.choice(DOMS), rng.choice(FXS)) for i in range(rows)]
    serial = [rows]
    out: list[str] = []
    for i in range(n):
        if i:
            cur = _next(rng, cur, serial)
        warnings = [WARN] if rng.random() < 0.3 else []
        rate = rng.choice([1400.0, 1400.5])
        out.append(
            _encode(
                rng,
                _table(
                    cur if rng.random() > 0.03 else [],
                    rate=rate,
                    warnings=warnings,
                    at=i,
                ),
            )
        )
    return out


def _ref_chain(texts: list[str]) -> list[str]:
    """바꾸기 전 허브의 파싱 경로 — 첫 표 snapshot, 그 뒤 직전 인덱스 기준 delta."""
    out = [make_snapshot(json.loads(texts[0]))]
    prev = index_rows(json.loads(texts[0]))
    for t in texts[1:]:
        msg, prev = make_delta(prev, json.loads(t))
        out.append(msg)
    return out


def _text_chain(texts: list[str]) -> list[str]:
    idx = index_text(texts[0], CHANNEL)
    assert idx is not None
    out = [snapshot_text(texts[0])]
    for t in texts[1:]:
        done = make_delta_text(idx, t, CHANNEL)
        assert done is not None, t[:120]
        msg, idx = done
        out.append(msg)
    return out


@pytest.mark.parametrize("seed_", range(40))
def test_text_delta_and_snapshot_are_the_parse_path_bytes(seed_: int) -> None:
    texts = _texts(seed_, 8)
    assert _text_chain(texts) == _ref_chain(texts)
    for t in texts:
        assert snapshot_text(t) == make_snapshot(json.loads(t))


def test_zero_sign_only_change_is_not_a_changed_row_but_strings_with_zero_are() -> None:
    """spark 끝값·dayChg 의 0.0 ↔ -0.0 은 파싱 경로처럼 안 실린다. 문자열 안의 `:-0.0,`·`[-0.0]` 은 값이 아니라 그대로 비교한다."""
    a = {
        **_row(random.Random(1), "BTC", "upbit", "binance"),
        "spark": [0.1, 0.0],
        "dayChg": 0.0,
        "netDom": "x:-0.0,y",
    }
    b = {
        **a,
        "spark": [0.1, -0.0],
        "dayChg": -0.0,
        "age": 9.0,
    }  # 0 부호와 age 만 — 안 바뀐 행
    c = {**b, "netDom": "x:0.0,y"}  # 문자열 안 — 바뀐 행
    d = {
        **a,
        "spark": [0.1, -0.0, -0.0],
        "netFx": '"spark":[-0.0]',
    }  # 값 개수가 다르다 — 바뀐 행
    texts = [encode_table(_table([r])) for r in (a, b, c, d)]
    got = _text_chain(texts)
    assert got == _ref_chain(texts)
    assert [len(json.loads(m)["rows"]) for m in got[1:]] == [0, 1, 1]


def real_texts() -> list[str]:
    """운영 표 3장(1,458행, spark 의 -0.0 6행) — 행 열 그대로 19키로 펴서 게시기 인코더로 쓴 글."""
    tables = json.loads(lzma.decompress(DATA.read_bytes()))
    texts: list[str] = []
    for i, t in enumerate(tables):
        rows = []
        for vals in t["rows"]:
            d = dict(zip(FIELDS, vals, strict=True))
            usd = float(d["usd"])
            fwd = (float(d["krw"]) / (usd * t["rate"]) - 1) * 100 if usd else 0.0
            rows.append(
                {
                    "sym": d["sym"], "dom": d["dom"], "fx": d["fx"], "fwd": fwd, "rev": -fwd, "usd": usd,
                    "spark": d["spark"], "status": "ok" if usd else "fail", "age": 0.5 * i, "slipFwd": 0.0,
                    "slipRev": 0.0, "krw": float(d["krw"]), "netDom": d["netDom"], "depDom": d["depDom"],
                    "wdDom": d["wdDom"], "depFx": d["depFx"], "wdFx": d["wdFx"], "netFx": d["netFx"],
                    "dayChg": d["dayChg"],
                }
            )  # fmt: skip
        texts.append(encode_table(_table(rows, rate=t["rate"], at=i)))
    return texts


def test_real_tables_with_minute_rollover_values() -> None:
    texts = real_texts()
    assert (
        sum('"spark":[' in p and "-0.0" in p for p in split_table(texts[0], CHANNEL)[2])
        > 0
    )  # type: ignore[index]
    assert _text_chain(texts) == _ref_chain(texts)


def test_publisher_text_branch_output_takes_the_text_path() -> None:
    """수집 게시기가 실제로 쓰는 글 갈래(`encode_text_table`)와 dict 갈래 둘 다 글 경로가 선다 — 허브가 파싱하지 않는다."""
    store = LiveStore()
    seed(store)
    text = encode_text_table(build_table(store, spark_json=store.spark_json()))  # type: ignore[arg-type]
    assert index_text(text, CHANNEL) is not None
    assert snapshot_text(text) == make_snapshot(json.loads(text))


def test_table_contract_the_text_path_relies_on() -> None:
    """글 경로가 기대는 표 모양(003 §3.2·017 §3.1) — 최상위에서 rows 다음이 warnings, 행은 sym·dom·fx·fwd 로 시작하고
    age 가 있으며 객체 값이 없다. 이 모양이 바뀌면 글 경로가 None 이 되어 파싱 경로로 가고, 이 테스트가 먼저 알린다."""
    store = LiveStore()
    seed(store)
    table = build_table(store)
    keys = list(table)
    assert keys[keys.index("rows") + 1] == "warnings"
    assert "type" not in table and "removed" not in table
    for r in table["rows"]:  # type: ignore[union-attr]
        assert list(r)[:4] == ["sym", "dom", "fx", "fwd"] and "age" in r
        assert not any(isinstance(v, dict) for v in r.values())


def _odd_shapes() -> dict[str, str]:
    base = _table(
        [
            _row(random.Random(2), "BTC", "upbit", "binance"),
            _row(random.Random(3), "ETH", "upbit", "bybit"),
        ],
        warnings=[WARN],
    )
    inserted = json.loads(json.dumps(base))
    inserted["rows"] = [
        {**{k: r[k] for k in ("sym", "dom", "fx")}, "fxName": "Binance", **r}
        for r in inserted["rows"]
    ]
    no_sym_first = json.loads(json.dumps(base))
    no_sym_first["rows"] = [{"dom": r["dom"], **r} for r in no_sym_first["rows"]]
    dup = json.loads(json.dumps(base))
    dup["rows"] = [dup["rows"][0], dup["rows"][0]]
    no_age = json.loads(json.dumps(base))
    no_age["rows"] = [
        {k: v for k, v in r.items() if k != "age"} for r in no_age["rows"]
    ]
    typed = {"type": "x", **base}
    control = json.loads(json.dumps(base))
    control["rows"][0]["netDom"] = (
        "a\x01b"  # 제어문자는 게시기 글에서도 \u0001 — 파싱 경로로 간다
    )
    return {
        "spaces": json.dumps(base),
        "ascii_escapes": json.dumps(
            base, ensure_ascii=False, separators=(",", ":")
        ).replace("업", "\\uc5c5"),
        "ensure_ascii": json.dumps(base, separators=(",", ":")),
        "key_between": encode_table(inserted),
        "sym_not_first": encode_table(no_sym_first),
        "duplicate_key": encode_table(dup),
        "no_age": encode_table(no_age),
        "type_key": encode_table(typed),
        "control_char": encode_table(control),
    }


@pytest.mark.parametrize("name", list(_odd_shapes()))
def test_tables_not_in_the_publisher_shape_fall_back_to_parsing(name: str) -> None:
    good = encode_table(_table([_row(random.Random(4), "BTC", "upbit", "binance")]))
    odd = _odd_shapes()[name]
    assert index_text(odd, CHANNEL) is None
    assert make_delta_text(index_text(good, CHANNEL), odd, CHANNEL) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("name", list(_odd_shapes()))
async def test_hub_sends_the_parse_path_bytes_for_odd_shapes_and_returns_to_text(
    name: str,
) -> None:
    """모양이 다른 표가 끼어도 허브가 내보내는 글은 파싱 경로 그대로이고, 다음 정상 표부터 다시 글 경로다."""
    rng = random.Random(5)
    rows = [_row(rng, "BTC", "upbit", "binance"), _row(rng, "ETH", "upbit", "bybit")]
    texts = [
        encode_table(_table(rows)),
        _odd_shapes()[name],
        encode_table(_table(rows[::-1], at=2)),
    ]
    texts.append(encode_table(_table([{**rows[0], "fwd": 9.5}], at=3)))
    bus, _ = make_bus()
    await bus.publish_table(texts[0])
    hub = SpreadsHub(bus=bus)
    ws = FakeWs()
    await hub.attach(ws)  # type: ignore[arg-type]
    for t in texts[1:]:
        hub.on_table(t)
    await wait_until(lambda: len(ws.sent) == 4)
    assert ws.sent == [make_snapshot(json.loads(texts[0]))] + [
        _delta(texts[i - 1], texts[i]) for i in (1, 2, 3)
    ]
    await hub.aclose()


def test_unknown_table_id_has_no_text_path() -> None:
    good = encode_table(_table([_row(random.Random(4), "BTC", "upbit", "binance")]))
    assert index_text(good, "pp") is None


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


def _delta(prev: str, cur: str) -> str:
    return make_delta(index_rows(json.loads(prev)), json.loads(cur))[0]


@pytest.mark.parametrize("odd_first", [False, True])
async def test_hub_session_frames_match_the_parse_path(
    monkeypatch, odd_first: bool
) -> None:  # noqa: ANN001
    """접속·이탈이 섞인 한 흐름 — 접속마다 받은 메시지가 파싱 경로로 만든 기대값과 글자까지 같다.

    표는 160행(약 90KB)이라 압축은 스레드에서 돈다. `odd_first` 면 첫 표만 공백 있는 글이다 — 파싱 경로로 한 번 갔다가
    다음 표부터 글 경로로 돌아온다(파싱 경로가 계속 붙어 있지 않다).
    """
    parsed: list[int] = []
    real = hub_module.make_delta

    def counting(*args, **kw):  # noqa: ANN002, ANN003, ANN202
        parsed.append(1)
        return real(*args, **kw)

    monkeypatch.setattr(hub_module, "make_delta", counting)
    texts = _texts(99, 14, rows=160)
    if odd_first:
        texts[0] = json.dumps(json.loads(texts[0]))
    bus, _ = make_bus()
    await bus.publish_table(texts[0])
    hub = SpreadsHub(bus=bus)
    a, b, c = FakeWs(), FakeWs(), FakeWs()
    conn_a = await hub.attach(a)  # type: ignore[arg-type]
    for t in texts[1:5]:
        hub.on_table(t)
    conn_b = await hub.attach(b)  # type: ignore[arg-type]
    for t in texts[5:8]:
        hub.on_table(t)
    hub.detach(conn_a)  # 압축 중인 delta 도 A 몫은 A 에게 간다 — 만들 때 정한 접속자다
    for t in texts[8:10]:
        hub.on_table(t)
    # 마지막 접속자가 떠나면 압축 중인 프레임까지 버린다 — 실제로는 소켓이 닫힌 뒤라 받을 곳이 없다. 다 받은 뒤 떠나게 한다
    await wait_until(lambda: len(b.sent) == 6)
    hub.detach(conn_b)  # 0명 — 상태를 버린다
    await bus.publish_table(texts[10])
    await hub.attach(c)  # type: ignore[arg-type]
    for t in texts[11:14]:
        hub.on_table(t)
    want_a = [make_snapshot(json.loads(texts[0]))] + [
        _delta(texts[i - 1], texts[i]) for i in range(1, 8)
    ]
    want_b = [make_snapshot(json.loads(texts[4]))] + [
        _delta(texts[i - 1], texts[i]) for i in range(5, 10)
    ]
    want_c = [make_snapshot(json.loads(texts[10]))] + [
        _delta(texts[i - 1], texts[i]) for i in range(11, 14)
    ]
    await wait_until(
        lambda: (
            (len(a.sent), len(b.sent), len(c.sent))
            == (len(want_a), len(want_b), len(want_c))
        )
    )
    await asyncio.sleep(0.05)
    assert a.sent == want_a
    assert b.sent == want_b
    assert c.sent == want_c
    # 파싱 경로는 공백 글을 이은 첫 delta 한 번뿐이다
    assert len(parsed) == (1 if odd_first else 0)
    await hub.aclose()
