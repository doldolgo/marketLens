"""017 §3.3 v2 — patch(바뀐 필드만, 2026-10-23): 글 경로가 파싱 경로와 같은 바이트이고, 브라우저 병합 결과가 delta 와 같은가.

기준은 파싱 경로 `make_patch(index_rows(직전), 새 표)` 다. 표 글은 게시기 인코더로 만든다(`test_hub_text` 의 생성기 —
-0.0·이스케이프할 문자열·쉼표로 끝나는 문자열·행 증감 포함). 병합은 웹(`features/spreads/api.ts` 의 `createRowTable`)과
같은 규칙의 파이썬 판으로 본다 — 같은 표 흐름에서 delta 로 병합한 표와 patch 로 병합한 표가 매 회차 같아야 한다.
허브 갈래(v1·v2 접속자가 섞임, gap 은 patch 없음)와 `/ws/spreads?v=2` 계약은 끝에서 본다.
"""

import gzip
import json
import random

import pytest

from app.core.live_store import LiveStore
from app.core.redis_bus import CHANNEL, GAP_CHANNEL
from app.features.spreads import hub as hub_module
from app.features.spreads.hub import (
    ROW_FIELDS,
    SpreadsHub,
    index_rows,
    index_text,
    make_delta,
    make_patch,
    make_patch_text,
    make_snapshot,
    row_key,
    snapshot_text,
    spark_op,
)
from app.features.spreads.push import encode_table, encode_text_table
from app.features.spreads.service import build_table
from app.features.spreads.tests.test_hub_text import (
    ODD,
    FakeWs,
    _encode,
    _next,
    _row,
    _table,
    real_texts,
    wait_until,
)
from app.features.spreads.tests.test_push import make_bus, seed

DOMS = ("upbit", "bithumb")
FXS = ("binance", "bybit", "bitget", "okx")


def _spark_moves(rng: random.Random, rows: list[dict]) -> list[dict]:
    """`_next` 위에 spark 의 여러 꼴 — 끝값만(같은 분), 한 칸 밀기(분 롤오버), 길이 늘기(복원 직후), 두 칸 밀기, 비우기."""
    out = []
    for r in rows:
        s = list(r["spark"])
        roll = rng.random()
        if roll < 0.3 and s:
            s[-1] = round(s[-1] + rng.choice([-0.001, 0.002, 0.5]), 3)
        elif roll < 0.4 and s:
            s = [*s[1:], round(rng.uniform(-2, 2), 3)]
        elif roll < 0.45:
            s = [*s, round(rng.uniform(-2, 2), 3)]
        elif roll < 0.48 and len(s) > 2:
            s = [*s[2:], 0.25, -0.5]
        elif roll < 0.5:
            s = []
        out.append({**r, "spark": s})
    return out


def _texts_v2(seed_: int, n: int, rows: int = 40) -> list[str]:
    rng = random.Random(seed_)
    cur = [_row(rng, f"S{i}", rng.choice(DOMS), rng.choice(FXS)) for i in range(rows)]
    serial = [rows]
    out: list[str] = []
    for i in range(n):
        if i:
            cur = _spark_moves(rng, _next(rng, cur, serial))
        rate = rng.choice([1400.0, 1400.5])
        warnings = ["경고"] if rng.random() < 0.3 else []
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


def _patch_ref(texts: list[str]) -> list[str]:
    prev = index_rows(json.loads(texts[0]))
    out = []
    for t in texts[1:]:
        msg, prev = make_patch(prev, json.loads(t))
        out.append(msg)
    return out


def _patch_by_text(texts: list[str]) -> list[str]:
    idx = index_text(texts[0], CHANNEL)
    assert idx is not None
    out = []
    for t in texts[1:]:
        done = make_patch_text(idx, t, CHANNEL)
        assert done is not None, t[:120]
        msg, idx = done
        out.append(msg)
    return out


def _delta_ref(texts: list[str]) -> list[str]:
    prev = index_rows(json.loads(texts[0]))
    out = []
    for t in texts[1:]:
        msg, prev = make_delta(prev, json.loads(t))
        out.append(msg)
    return out


def apply(state: dict[str, dict], msg: dict) -> None:
    """브라우저 병합(017 §3.4)의 파이썬 판 — 서버 키 → 행. snapshot 은 통째, delta·patch 의 `rows` 는 행 교체, patch 항목은
    필드 덮어쓰기(spark 는 끝값 바꾸기·한 칸 밀기), `removed` 는 삭제. 안 실린 행은 그대로(age 도 서버가 마지막에 준 값)."""
    if msg["type"] == "snapshot":
        state.clear()
    for r in msg["rows"]:
        state[row_key(r)] = dict(r)
    for e in msg.get("patch", []):
        cur = state[row_key(e)]  # 없는 키면 브라우저는 다시 붙는다 — 여기선 실패
        for k, v in e.items():
            if k == "sparkEnd":
                cur["spark"] = [*cur["spark"][:-1], v]
            elif k == "sparkPush":
                cur["spark"] = [*cur["spark"][1:], v]
            else:
                cur[k] = v
    for k in msg.get("removed", []):
        del state[k]


# --- spark 규칙 ---


@pytest.mark.parametrize(
    ("prev", "cur", "want"),
    [
        ([1.0, 2.0, 3.0], [1.0, 2.0, 4.0], ("sparkEnd", 4.0)),
        ([1.0, 2.0, 3.0], [2.0, 3.0, 4.0], ("sparkPush", 4.0)),
        ([5.0], [6.0], ("sparkEnd", 6.0)),
        ([1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0], ("spark", [1.0, 2.0, 3.0, 4.0])),
        ([1.0, 2.0, 3.0], [3.0, 4.0, 5.0], ("spark", [3.0, 4.0, 5.0])),
        ([], [1.0], ("spark", [1.0])),
        ([1.0, 2.0], [], ("spark", [])),
        ([1.0, 2.0], [3.0], ("spark", [3.0])),
    ],
)
def test_spark_sends_only_the_end_value_when_it_can(prev, cur, want) -> None:  # noqa: ANN001
    assert spark_op(prev, cur) == want
    a = _table([{**_row(random.Random(1), "BTC", "upbit", "binance"), "spark": prev}])
    b = _table([{**a["rows"][0], "spark": cur}], at=1)
    texts = [encode_table(a), encode_table(b)]
    got = _patch_by_text(texts)
    assert got == _patch_ref(texts)
    entry = json.loads(got[0])["patch"][0]
    assert entry[want[0]] == want[1]
    assert sum(k.startswith("spark") for k in entry) == 1


# --- 글 경로 = 파싱 경로 ---


@pytest.mark.parametrize("seed_", range(40))
def test_text_patch_is_the_parse_path_bytes(seed_: int) -> None:
    texts = _texts_v2(seed_, 8)
    assert _patch_by_text(texts) == _patch_ref(texts)


def test_real_tables_patch_is_the_parse_path_bytes_and_parses_only_zero_rows(
    monkeypatch,  # noqa: ANN001
) -> None:
    """운영 표 3장 — 글 경로가 파싱하는 행은 값 -0.0 이 든 행뿐이다."""
    parsed: list[str] = []
    real = hub_module._patch_parsed

    def counting(p: str, old: str):  # noqa: ANN202
        parsed.append(p)
        return real(p, old)

    monkeypatch.setattr(hub_module, "_patch_parsed", counting)
    texts = real_texts()
    got = _patch_by_text(texts)
    assert got == _patch_ref(texts)
    assert sum(len(json.loads(m)["patch"]) for m in got) > 100
    assert all(hub_module._zero(p) != p for p in parsed)


def test_patch_entry_carries_key_changed_fields_and_age_in_row_order() -> None:
    a = _row(random.Random(3), "BTC", "upbit", "binance")
    a.update(spark=[0.1, 0.2], status="ok", netDom="Bitcoin", dayChg=1.0)
    b = {**a, "fwd": a["fwd"] + 1, "spark": [0.1, 0.3], "age": 9.5, "dayChg": None}
    msg = json.loads(
        _patch_by_text([encode_table(_table([a])), encode_table(_table([b], at=1))])[0]
    )
    assert msg["type"] == "patch" and msg["rows"] == [] and msg["removed"] == []
    assert list(msg["patch"][0].items()) == [
        ("sym", "BTC"), ("dom", "upbit"), ("fx", "binance"), ("fwd", b["fwd"]), ("sparkEnd", 0.3),
        ("age", 9.5), ("dayChg", None),
    ]  # fmt: skip
    assert list(msg)[:6] == [
        "type",
        "rate",
        "notional",
        "warnings",
        "dataReceivedAt",
        "fetchedAt",
    ]


def test_new_rows_and_rows_with_a_different_field_set_go_whole_in_rows() -> None:
    """새 키는 19키 행 그대로. 필드 집합이 달라진 행(계약이 바뀐 배포 직후)도 patch 로 못 적으니 행 통째로."""
    rng = random.Random(7)
    a = _row(rng, "BTC", "upbit", "binance")
    b = _row(rng, "ETH", "upbit", "binance")
    grown = {**a, "fwd": 1.25, "extra": "x"}
    texts = [encode_table(_table([a])), encode_table(_table([grown, b], at=1))]
    got = _patch_by_text(texts)
    assert got == _patch_ref(texts)
    msg = json.loads(got[0])
    assert [r["sym"] for r in msg["rows"]] == ["BTC", "ETH"] and msg["patch"] == []


@pytest.mark.parametrize("odd", ODD)
def test_strings_that_look_like_field_boundaries(odd: str) -> None:
    """쉼표로 끝나는 문자열·행 경계처럼 보이는 문자열 — 자른 조각 수가 늘면 그 행만 파싱한다. 바이트는 같다."""
    rng = random.Random(11)
    a = {**_row(rng, "BTC", "upbit", "binance"), "netDom": odd, "netFx": "x"}
    b = {**a, "fwd": 2.5, "netFx": odd, "age": 1.0}
    c = {**b, "fwd": 3.5, "netDom": "y", "age": 2.0}
    texts = [encode_table(_table([r], at=i)) for i, r in enumerate((a, b, c))]
    assert _patch_by_text(texts) == _patch_ref(texts)


def test_publisher_text_branch_output_takes_the_text_path() -> None:
    """수집 게시기의 글 갈래(`encode_text_table`) 표도 글 경로로 patch 가 선다."""
    store = LiveStore()
    seed(store)
    t1 = encode_text_table(build_table(store, spark_json=store.spark_json()))  # type: ignore[arg-type]
    table = json.loads(t1)
    for r in table["rows"]:
        r["fwd"] += 0.5
        r["age"] += 1.0
    t2 = encode_table(table)
    got = _patch_by_text([t1, t2])
    assert got == _patch_ref([t1, t2])
    assert len(json.loads(got[0])["patch"]) == len(table["rows"])


def test_row_fields_are_the_table_row_order() -> None:
    """글 경로가 기대는 행 필드 이름·순서(003 §3.2) — 바뀌면 행마다 파싱 경로로 가고, 이 테스트가 먼저 알린다."""
    store = LiveStore()
    seed(store)
    for r in build_table(store)["rows"]:  # type: ignore[union-attr]
        assert tuple(r) == ROW_FIELDS


# --- 브라우저 병합: delta 와 patch 가 같은 표를 만든다 ---


@pytest.mark.parametrize("seed_", range(30))
def test_merging_patches_gives_the_same_table_as_merging_deltas(seed_: int) -> None:
    texts = _texts_v2(seed_, 10)
    snap = json.loads(snapshot_text(texts[0]))
    by_delta: dict[str, dict] = {}
    by_patch: dict[str, dict] = {}
    apply(by_delta, snap)
    apply(by_patch, json.loads(json.dumps(snap)))
    for t, d, p in zip(
        texts[1:], _delta_ref(texts), _patch_by_text(texts), strict=True
    ):
        apply(by_delta, json.loads(d))
        apply(by_patch, json.loads(p))
        assert (
            by_patch == by_delta
        )  # 값 비교 — 0.0 과 -0.0 은 같다(파싱 경로의 비교와 같다)
        # age 를 뺀 값은 그 회차 표 그대로다
        assert {
            k: {f: v for f, v in r.items() if f != "age"} for k, r in by_patch.items()
        } == (index_rows(json.loads(t)))


def test_real_tables_patch_is_much_smaller_than_delta() -> None:
    texts = real_texts()
    deltas = _delta_ref(texts)
    patches = _patch_by_text(texts)
    for d, p in zip(deltas, patches, strict=True):
        assert len(p) < len(d) / 2


# --- 허브 ---


def _unpack(frame: bytes) -> str:
    return gzip.decompress(frame).decode()


async def test_hub_sends_delta_to_old_and_patch_to_v2_connections() -> None:
    """옛 탭(쿼리 없음)은 바꾸기 전과 같은 delta, `v=2` 접속자는 patch — snapshot 은 같은 바이트를 같이 받는다."""
    texts = _texts_v2(21, 8, rows=160)
    bus, _ = make_bus()
    await bus.publish_table(texts[0])
    hub = SpreadsHub(bus=bus)
    old, new = FakeWs(), FakeWs()
    await hub.attach(old)  # type: ignore[arg-type]
    await hub.attach(new, slim=True)  # type: ignore[arg-type]
    for i, t in enumerate(texts[1:], 2):
        hub.on_table(t)
        # 접속마다 대기열이 5개라 한꺼번에 넣으면 느린 접속자로 닫힌다 — 표마다 다 받기를 기다린다
        await wait_until(lambda i=i: len(old.sent) == len(new.sent) == i)
    snap = make_snapshot(json.loads(texts[0]))
    assert old.sent == [snap, *_delta_ref(texts)]
    assert new.sent == [snap, *_patch_ref(texts)]
    await hub.aclose()


async def test_hub_builds_only_the_kind_someone_receives(monkeypatch) -> None:  # noqa: ANN001
    def boom(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("받는 접속자가 없는 메시지를 만들었다")

    texts = _texts_v2(22, 4)
    for slim, banned in (
        (False, ("patch_text", "make_patch")),
        (True, ("delta_text", "make_delta")),
    ):
        with monkeypatch.context() as m:
            for name in banned:
                m.setattr(hub_module, name, boom)
            bus, _ = make_bus()
            await bus.publish_table(texts[0])
            hub = SpreadsHub(bus=bus)
            ws = FakeWs()
            await hub.attach(ws, slim=slim)  # type: ignore[arg-type]
            for t in texts[1:]:
                hub.on_table(t)
            await wait_until(lambda ws=ws: len(ws.sent) == len(texts))
            kinds = {json.loads(s)["type"] for s in ws.sent[1:]}
            assert kinds == {"patch" if slim else "delta"}
            await hub.aclose()


async def test_gap_hub_has_no_patch() -> None:
    """patch 는 spreads 표만 — gap 허브는 `slim` 을 받아도 delta 다(gap·pp 프레임 바이트는 그대로)."""
    bus, _ = make_bus()
    hub = SpreadsHub(bus=bus, table=GAP_CHANNEL)
    conn = await hub.attach(FakeWs(), slim=True)  # type: ignore[arg-type]
    assert conn.slim is False
    await hub.aclose()


async def test_hub_patch_for_odd_shapes_is_the_parse_path() -> None:
    """글 모양이 다른 표(공백 있는 인코더)도 patch 는 파싱 경로 그대로다."""
    texts = [json.dumps(json.loads(t)) for t in _texts_v2(23, 5)]
    bus, _ = make_bus()
    await bus.publish_table(texts[0])
    hub = SpreadsHub(bus=bus)
    ws = FakeWs()
    await hub.attach(ws, slim=True)  # type: ignore[arg-type]
    for t in texts[1:]:
        hub.on_table(t)
    await wait_until(lambda: len(ws.sent) == len(texts))
    assert ws.sent[1:] == _patch_ref(texts)
    await hub.aclose()
