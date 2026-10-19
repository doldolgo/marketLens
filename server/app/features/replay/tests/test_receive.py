"""`POST /replay` 받기 — 검사 순서·경계값·게이트·IP 10분 제한·IP 읽기·Redis 실패 (스펙 073 §3.4·§4).

"쓴 것 없음" 은 Redis `rp:*` 키로 본다 — 받기의 결과가 남는 곳은 거기뿐이다.
"""

import json
import logging

import pytest

from app.core import config
from app.core.redis_bus import RedisBus
from app.features.replay.service import ReplayService, gate_ts
from app.features.replay.tests.conftest import (
    GOOD,
    IP,
    NOON,
    SID,
    Rig,
    body,
    kst,
)


def _ev(*events: list[object]) -> bytes:
    return body(ev=list(events))


BAD = {
    "JSON 아님": b"{nope",
    "깨진 UTF-8": b'{"v":1,"sid":"\xff"}',
    "배열": b"[1]",
    "깊은 중첩": b"[" * 2000 + b"]" * 2000,
    "키 빠짐": json.dumps({k: v for k, v in GOOD.items() if k != "h"}).encode(),
    "키 더함": body(extra=1),
    "v 2": body(v=2),
    "v true": body(v=True),
    "v 글자": body(v="1"),
    "v 1.0": json.dumps({**GOOD, "v": 1.0}).encode(),
    "sid 15자": body(sid="0" * 15),
    "sid 17자": body(sid="0" * 17),
    "sid 대문자": body(sid="0123456789ABCDEF"),
    "sid 16진 아님": body(sid="g" * 16),
    "sid 숫자": body(sid=1234567890123456),
    "seq 음수": body(seq=-1),
    "seq 3600": body(seq=3600),
    "seq true": body(seq=True),
    "seq 글자": body(seq="0"),
    "목록 밖 화면": body(page="app-admin"),
    "옛 사본 화면": body(page="privacy-20261011"),
    "화면 목록": body(page=["landing"]),
    "기기": body(dev="tablet"),
    "기기 대문자": body(dev="PC"),
    "기기 객체": body(dev={}),
    "w 음수": body(w=-1),
    "h 20001": body(h=20_001),
    "w 실수": body(w=1280.5),
    "h true": body(h=True),
    "ev 빈 목록": body(ev=[]),
    "ev 601개": body(ev=[[0, "v", 1]] * 601),
    "ev 객체": body(ev={"0": [0, "v", 1]}),
    "사건이 글자": _ev("m"),
    "사건 둘 자리": _ev([0, "v"]),
    "t 음수": _ev([-1, "v", 1]),
    "t 7200001": _ev([7_200_001, "v", 1]),
    "t 실수": _ev([1.5, "v", 1]),
    "t true": _ev([True, "v", 1]),
    "모르는 종류": _ev([0, "x", 1]),
    "종류 숫자": _ev([0, 1, 1]),
    "m 자리 셋": _ev([0, "m", 1]),
    "m 자리 다섯": _ev([0, "m", 1, 2, 3]),
    "m x 20001": _ev([0, "m", 20_001, 0]),
    "m y 음수": _ev([0, "m", 0, -1]),
    "m 실수": _ev([0, "m", 1.5, 2]),
    "c 자리 넷": _ev([0, "c", 1, 2]),
    "c 영역 대문자": _ev([0, "c", 1, 2, "Table"]),
    "c 영역 null": _ev([0, "c", 1, 2, None]),
    "c 영역 33자": _ev([0, "c", 1, 2, "a" * 33]),
    "c 영역 숫자로 시작": _ev([0, "c", 1, 2, "1x"]),
    "c 영역 끝 줄바꿈": _ev([0, "c", 1, 2, "table\n"]),
    "s 자리 없음": _ev([0, "s"]),
    "s 자리 둘": _ev([0, "s", 1, 2]),
    "s 1000001": _ev([0, "s", 1_000_001]),
    "e 영역 빈칸": _ev([0, "e", "", 0, 10]),
    "e n 200": _ev([0, "e", "table", 200, 10]),
    "e n 음수": _ev([0, "e", "table", -1, 10]),
    "e top 1000001": _ev([0, "e", "table", 0, 1_000_001]),
    "e 자리 넷": _ev([0, "e", "table", 0]),
    "r w 0": _ev([0, "r", 0, 800]),
    "r h 20001": _ev([0, "r", 1280, 20_001]),
    "r 자리 셋": _ev([0, "r", 1280]),
    "사건 v 2": _ev([0, "v", 2]),
    "사건 v true": _ev([0, "v", True]),
    "사건 v 자리 넷": _ev([0, "v", 1, 1]),
    "p 목록 밖": _ev([0, "p", "app-admin"]),
    "p 목록": _ev([0, "p", ["landing"]]),
    "p 자리 넷": _ev([0, "p", "landing", 1]),
    "a 빈칸": _ev([0, "a", ""]),
    "a 대문자": _ev([0, "a", "Table"]),
    "a 자리 넷": _ev([0, "a", "table", 1]),
    "동의 상태 모름": body(c="yes"),
    "동의 상태 대문자": body(c="GRANTED"),
    "동의 상태 숫자": body(c=1),
    "동의 상태 null": body(c=None),
    "동의 상태 목록": body(c=["granted"]),
    "사건 하나만 틀림": _ev([0, "v", 1], [0, "m", 1, 2], [0, "q"]),
}


async def test_each_shape_violation_is_400_and_stores_nothing(rig: Rig) -> None:
    for name, content in BAD.items():
        resp = await rig.post(content)
        assert resp.status_code == 400, name
        assert resp.json() == {
            "error": {
                "code": "bad_replay",
                "message": resp.json()["error"]["message"],
                "detail": None,
            }
        }, name
    assert rig.keys() == []


async def test_boundary_values_of_every_kind_are_accepted(rig: Rig) -> None:
    edge = [
        [0, "m", 0, 0],
        [7_200_000, "m", 20_000, 20_000],
        [1, "c", 20_000, 0, ""],
        [2, "c", 0, 20_000, "a" * 32],
        [3, "s", 0],
        [4, "s", 1_000_000],
        [5, "e", "table", 0, 0],
        [6, "e", "z9-", 199, 1_000_000],
        [7, "r", 1, 1],
        [8, "r", 20_000, 20_000],
        [9, "v", 0],
        [10, "v", 1],
        [11, "p", "kimp-history"],
        [12, "a", "hero"],
    ]
    filler = [[0, "v", 1]] * (600 - len(edge))
    content = body(
        sid="ffffffffffffffff", seq=3599, w=20_000, h=20_000, ev=edge + filler
    )
    assert len(content) <= 16_384
    assert (await rig.post(content)).status_code == 204
    # 머리 w·h 0 = 모름(숨은 채 열린 페이지) — r 은 1 부터
    assert (await rig.post(body(dev="mobile", w=0, h=0))).status_code == 204
    assert rig.batches("ffffffffffffffff")[0]["ev"] == edge + filler
    assert rig.meta()["dev"] == "mobile"
    assert (rig.meta()["w"], rig.meta()["h"]) == ("0", "0")


async def test_cross_site_is_403_before_reading_and_missing_header_passes(
    rig: Rig,
) -> None:
    for value in ("cross-site", "same-site", "none"):
        resp = await rig.post(body(), **{"Sec-Fetch-Site": value})
        assert resp.status_code == 403, value
        assert resp.json()["error"]["code"] == "cross_site"
    # 몸통이 크거나 틀려도 403 이 먼저다
    big = b" " * 100_000
    assert (await rig.post(big, **{"Sec-Fetch-Site": "cross-site"})).status_code == 403
    assert (await rig.post(b"{", **{"Sec-Fetch-Site": "cross-site"})).status_code == 403
    assert rig.keys() == []
    assert (
        await rig.post(body(), **{"Sec-Fetch-Site": "same-origin"})
    ).status_code == 204
    assert (await rig.post(body(seq=1))).status_code == 204
    assert rig.meta()["n"] == "2"


async def test_body_over_16384_bytes_is_413_before_the_shape_check(rig: Rig) -> None:
    content = body()
    exact = content + b" " * (16_384 - len(content))
    assert len(exact) == 16_384
    assert (await rig.post(exact)).status_code == 204
    over = body(seq=1) + b" " * (16_385 - len(body(seq=1)))
    resp = await rig.post(over)
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "too_large"
    # 틀린 몸통이어도 크면 413
    assert (await rig.post(b"{" * 16_385)).status_code == 413
    assert rig.meta()["n"] == "1"


async def test_before_the_gate_is_204_and_dropped(rig: Rig) -> None:
    gate = kst(2026, 10, 18)
    rig.clock.now = gate - 0.001
    assert (await rig.post(body())).status_code == 204
    # 검사는 게이트보다 먼저 — 어긋난 몸통은 게이트 전에도 400
    assert (await rig.post(b"{}")).status_code == 400
    assert rig.keys() == []
    rig.clock.now = gate
    assert (await rig.post(body())).status_code == 204
    assert rig.meta()["first"] == str(int(gate * 1000))


async def test_default_gate_is_the_privacy_v7_effective_date(rig: Rig) -> None:
    gate = gate_ts(config.PRIVACY_V7_EFFECTIVE)
    now = [gate - 1.0]
    service = ReplayService(clock=lambda: now[0])
    assert await service.accept(body(), None, IP, rig.bus) == "dropped"
    assert rig.keys() == []
    now[0] = float(gate)
    assert await service.accept(body(), None, IP, rig.bus) == "stored"


async def test_same_ip_over_600_batches_in_ten_minutes_is_dropped(rig: Rig) -> None:
    # NOON 은 10분 창의 시작이다 — 창 안에서 601번
    assert NOON % 600 == 0
    for seq in range(600):
        assert (await rig.post(body(seq=seq))).status_code == 204
    assert (await rig.post(body(seq=600))).status_code == 204
    assert rig.meta()["n"] == "600"
    # 다른 IP·IP 없음(`(없음)` 하나로 센다)은 따로 센다
    other = "0000000000000001"
    assert (await rig.post(body(sid=other), ip="198.51.100.1")).status_code == 204
    assert (await rig.post(body(sid="0000000000000002"), ip=None)).status_code == 204
    assert rig.meta(other)["n"] == "1" and rig.meta("0000000000000002")["n"] == "1"
    # 창이 바뀌면 다시 받는다
    rig.clock.now = NOON + 599.999
    assert (await rig.post(body(seq=601))).status_code == 204
    assert rig.meta()["n"] == "600"
    rig.clock.now = NOON + 600
    assert (await rig.post(body(seq=602))).status_code == 204
    assert rig.meta()["n"] == "601"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("203.0.113.7, 10.0.0.1", "203.0.113.7"),
        ("  2001:DB8:0::1 , 1.1.1.1", "2001:db8::1"),
        ("2001:db8:0:0:0:0:0:1", "2001:db8::1"),
        ("garbage", "(없음)"),
        ("", "(없음)"),
        ("[2001:db8::1]", "(없음)"),
        ("203.0.113.7:443", "(없음)"),
        (None, "(없음)"),
    ],
)
async def test_ip_is_the_first_x_client_ip_value_in_canonical_text(
    rig: Rig, header: str | None, expected: str
) -> None:
    assert (await rig.post(body(), ip=header)).status_code == 204
    assert rig.meta()["ip"] == expected
    assert rig.redis.zrange(f"rp:ip:{expected}", 0, -1) == [SID.encode()]


async def test_redis_failure_drops_the_batch_with_a_warning_every_30_seconds(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def down(self: RedisBus, sid: str, day: str) -> tuple[dict, int]:
        raise ConnectionError("redis down")

    monkeypatch.setattr(RedisBus, "replay_load", down)
    caplog.set_level(logging.WARNING, logger="marketlens.replay")

    def warnings() -> int:
        return sum(r.name == "marketlens.replay" for r in caplog.records)

    assert (await rig.post(body())).status_code == 204
    assert warnings() == 1
    rig.clock.mono += 29.9
    assert (await rig.post(body(seq=1))).status_code == 204
    assert warnings() == 1
    rig.clock.mono += 0.1
    assert (await rig.post(body(seq=2))).status_code == 204
    assert warnings() == 2
    assert rig.keys() == []


async def test_without_a_bus_the_batch_is_dropped(rig: Rig) -> None:
    rig.app.state.spreads_bus = None
    assert (await rig.post(body())).status_code == 204
    assert rig.keys() == []


async def test_only_post_is_routed(rig: Rig) -> None:
    resp = await rig.client.get("/replay")
    assert resp.status_code == 405
