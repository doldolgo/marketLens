"""`POST /attention` v2 — 요소·깊이·떠난 곳 검사·v4 게이트·열쇠 300 제한·저장 필드 꼴 (스펙 061 §3.3~§3.5·§4).

"더한 것 없음" 은 다음 10초 회차 뒤 Redis 해시로 본다(052 테스트와 같다).
"""

import json

from app.features.attention.tests.conftest import Rig, kst

GOOD = {
    "v": 2,
    "page": "landing",
    "device": "pc",
    "pv": 1,
    "a": {"faq": [1500, 2, 1]},
    "e": [["faq", "link:/kimp-chart", 1, 400, 1500], ["faq", "faq:why", 0, 0, 900]],
    "d": 7,
    "x": "faq",
}


def _v2(**change: object) -> bytes:
    return json.dumps({**GOOD, **change}).encode()


def _item(*values: object) -> bytes:
    return _v2(e=[list(values)])


BAD = {
    "e 151개": _v2(e=[["faq", f"k{i}", 1, 0, 0] for i in range(151)]),
    "열쇠 65자": _item("faq", "k" * 65, 1, 0, 0),
    "빈 열쇠": _item("faq", "", 1, 0, 0),
    "| 든 열쇠": _item("faq", "a|b", 1, 0, 0),
    "제어 문자": _item("faq", "a\nb", 1, 0, 0),
    "짝 없는 대리 문자": b'{"v":2,"page":"landing","device":"pc","pv":1,"a":{},'
    b'"e":[["faq","\\ud800",1,0,0]],"d":null,"x":null}',
    "열쇠 수": _item("faq", 7, 1, 0, 0),
    "영역 꼴": _item("Faq", "k", 1, 0, 0),
    "클릭 51": _item("faq", "k", 51, 0, 0),
    "hover 600001": _item("faq", "k", 0, 600_001, 0),
    "vis 600001": _item("faq", "k", 0, 0, 600_001),
    "음수": _item("faq", "k", 0, -1, 0),
    "불리언": _item("faq", "k", True, 0, 0),
    "항목 넷": _v2(e=[["faq", "k", 1, 0]]),
    "항목이 객체": _v2(e=[{"area": "faq"}]),
    "e 가 객체": _v2(e={}),
    "같은 (영역, 열쇠) 둘": _v2(e=[["faq", "k", 1, 0, 0], ["faq", "k", 1, 0, 0]]),
    "d 11": _v2(d=11),
    "d -1": _v2(d=-1),
    "d true": _v2(d=True),
    "d 실수": _v2(d=7.0),
    "대시보드의 d": _v2(page="app-spread", d=3),
    "x 꼴": _v2(x="Faq"),
    "x 수": _v2(x=3),
    "x 빈 글자": _v2(x=""),
    "v2 키 빠짐": json.dumps({k: v for k, v in GOOD.items() if k != "x"}).encode(),
    "v2 키 더함": _v2(z=1),
    "v1 에 e": json.dumps({**GOOD, "v": 1}).encode(),
    "v 3": _v2(v=3),
}


async def test_each_v2_violation_is_400_and_adds_nothing(rig: Rig) -> None:
    for name, body in BAD.items():
        resp = await rig.post(body)
        assert resp.status_code == 400, name
        assert resp.json()["error"]["code"] == "bad_beacon", name
    await rig.ticks.release()
    assert rig.redis.keys("attn:*") == []


async def test_v1_still_lands_and_v2_writes_element_exit_and_depth_fields(
    rig: Rig,
) -> None:
    v1 = {"v": 1, "page": "landing", "device": "pc", "pv": 1, "a": {"hero": [10, 0, 0]}}
    assert (await rig.post(json.dumps(v1).encode())).status_code == 204
    assert (await rig.post(_v2())).status_code == 204
    # 깊이·떠난 곳이 없는 몸통, 값이 모두 0 인 요소는 필드를 만들지 않는다
    quiet = _v2(pv=0, a={}, e=[["faq", "zero", 0, 0, 0]], d=None, x=None)
    assert (await rig.post(quiet)).status_code == 204
    await rig.ticks.release()
    assert rig.day("20261019") == {
        "landing|pc||pv": 2,
        "landing|pc|hero|ms": 10,
        "landing|pc|faq|ms": 1500,
        "landing|pc|faq|clicks": 2,
        "landing|pc|faq|seen": 1,
        "landing|pc|faq|e|link:/kimp-chart|c": 1,
        "landing|pc|faq|e|link:/kimp-chart|h": 400,
        "landing|pc|faq|e|link:/kimp-chart|v": 1500,
        "landing|pc|faq|e|faq:why|v": 900,
        "landing|pc|faq|x": 1,
        "landing|pc||d7": 1,
    }


async def test_edges_and_a_16384_byte_v2_body_are_accepted(rig: Rig) -> None:
    edge = _v2(
        page="app-flow",
        device="mobile",
        e=[["a" * 32, "k" * 64, 50, 600_000, 600_000], ["t", "row:BTC", 1, 0, 0]],
        d=None,
        x="a" * 32,
    )
    assert (await rig.post(edge)).status_code == 204
    body = _v2(d=0)
    exact = body + b" " * (16_384 - len(body))
    assert (await rig.post(exact)).status_code == 204
    assert (await rig.post(exact + b" ")).status_code == 413
    await rig.ticks.release()
    got = rig.day("20261019")
    assert got[f"app-flow|mobile|{'a' * 32}|e|{'k' * 64}|h"] == 600_000
    assert got["app-flow|mobile|t|e|row:BTC|c"] == 1
    assert got[f"app-flow|mobile|{'a' * 32}|x"] == 1
    assert got["landing|pc||d0"] == 1


async def test_before_the_v4_gate_only_areas_are_added(rig: Rig) -> None:
    rig.clock.now = kst(2026, 10, 18, 23, 59, 59)  # v3 게이트 뒤·v4 게이트 전
    assert (await rig.post(_v2())).status_code == 204
    rig.clock.now = kst(2026, 10, 19)  # v4 00:00 — 이때부터 요소도
    assert (await rig.post(_v2(pv=0, a={}))).status_code == 204
    await rig.ticks.release()
    assert rig.day("20261018") == {
        "landing|pc||pv": 1,
        "landing|pc|faq|ms": 1500,
        "landing|pc|faq|clicks": 2,
        "landing|pc|faq|seen": 1,
    }
    assert rig.day("20261019") == {
        "landing|pc|faq|e|link:/kimp-chart|c": 1,
        "landing|pc|faq|e|link:/kimp-chart|h": 400,
        "landing|pc|faq|e|link:/kimp-chart|v": 1500,
        "landing|pc|faq|e|faq:why|v": 900,
        "landing|pc|faq|x": 1,
        "landing|pc||d7": 1,
    }


async def test_the_301st_new_key_of_a_day_screen_and_device_is_other(
    rig: Rig,
) -> None:
    def keys(start: int, n: int, area: str = "faq") -> list[list[object]]:
        return [[area, f"k{i}", 1, 0, 0] for i in range(start, start + n)]

    for start in (0, 150):
        body = _v2(pv=0, a={}, e=keys(start, 150), d=None, x=None)
        assert (await rig.post(body)).status_code == 204
    # 301·302번째 새 열쇠 — (기타)로 합친다. 이미 센 열쇠와 다른 화면·기기는 그대로
    late = [*keys(300, 2), ["faq", "k0", 1, 0, 0], ["foot", "k0", 1, 0, 0]]
    assert (await rig.post(_v2(pv=0, a={}, e=late, d=None, x=None))).status_code == 204
    other = _v2(device="mobile", pv=0, a={}, e=keys(300, 1), d=None, x=None)
    assert (await rig.post(other)).status_code == 204
    await rig.ticks.release()
    got = rig.day("20261019")
    assert got["landing|pc|faq|e|(기타)|c"] == 2
    assert got["landing|pc|faq|e|k0|c"] == 2
    assert "landing|pc|faq|e|k300|c" not in got
    # (영역, 열쇠) 짝으로 센다 — 300개가 찬 뒤 새 짝은 다른 영역이어도 (기타)
    assert got["landing|pc|foot|e|(기타)|c"] == 1
    assert got["landing|mobile|faq|e|k300|c"] == 1
    # 다음 날은 새로 센다
    rig.clock.now = kst(2026, 10, 20, 9)
    assert (
        await rig.post(_v2(pv=0, a={}, e=keys(300, 1), d=None, x=None))
    ).status_code == 204
    await rig.ticks.release()
    assert rig.day("20261020") == {"landing|pc|faq|e|k300|c": 1}
