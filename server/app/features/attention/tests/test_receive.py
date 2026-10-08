"""`POST /attention` 받기 — 검사·교차 사이트·크기·게이트·IP 10분 제한 (스펙 052 §3.4·§4).

"더한 것 없음" 은 다음 10초 회차 뒤 Redis 해시로 본다 — 받기의 결과가 남는 곳은 거기뿐이다.
"""

import json

import pytest

from app.features.attention.tests.conftest import EFFECTIVE, Rig, beacon, kst

GOOD = {
    "v": 1,
    "page": "app-spread",
    "device": "pc",
    "pv": 1,
    "a": {"table": [1500, 2, 1]},
}


def _with(**change: object) -> bytes:
    return json.dumps({**GOOD, **change}).encode()


BAD = {
    "JSON 아님": b"{nope",
    "깨진 UTF-8": b'{"v":1,"page":"\xff"}',
    "배열": b"[1]",
    "키 빠짐": json.dumps({k: v for k, v in GOOD.items() if k != "pv"}).encode(),
    "키 더함": _with(extra=1),
    "v 2": _with(v=2),
    "v true": _with(v=True),
    "v 1.0": b'{"v":1.0,"page":"landing","device":"pc","pv":1,"a":{}}',
    "목록 밖 화면": _with(page="app-admin"),
    "옛 사본 화면": _with(page="privacy-20261011"),
    "기기": _with(device="tablet"),
    "pv 2": _with(pv=2),
    "pv true": _with(pv=True),
    "a 배열": _with(a=[]),
    "a 41키": _with(a={f"a{i}": [1, 0, 0] for i in range(41)}),
    "대문자 id": _with(a={"Table": [1, 0, 0]}),
    "숫자로 시작": _with(a={"1x": [1, 0, 0]}),
    "밑줄": _with(a={"a_b": [1, 0, 0]}),
    "33자": _with(a={"a" * 33: [1, 0, 0]}),
    "끝 줄바꿈": _with(a={"table\n": [1, 0, 0]}),
    "값 둘": _with(a={"table": [1, 0]}),
    "값 객체": _with(a={"table": {"ms": 1}}),
    "ms 초과": _with(a={"table": [600_001, 0, 0]}),
    "ms 음수": _with(a={"table": [-1, 0, 0]}),
    "클릭 초과": _with(a={"table": [1, 51, 0]}),
    "seen 2": _with(a={"table": [1, 0, 2]}),
    "실수": b'{"v":1,"page":"landing","device":"pc","pv":1,"a":{"hero":[1.5,0,0]}}',
    "불리언": _with(a={"table": [1, 0, True]}),
    "깊은 중첩": b"[" * 2000 + b"]" * 2000,
}


async def test_each_shape_violation_is_400_and_adds_nothing(rig: Rig) -> None:
    for name, body in BAD.items():
        resp = await rig.post(body)
        assert resp.status_code == 400, name
        assert resp.json() == {
            "error": {
                "code": "bad_beacon",
                "message": resp.json()["error"]["message"],
                "detail": None,
            }
        }, name
    await rig.ticks.release()
    assert rig.redis.keys("attn:*") == []


async def test_good_beacon_is_204_and_lands_after_the_ten_second_batch(
    rig: Rig,
) -> None:
    resp = await rig.post(_with(a={"table": [1500, 2, 1], "filters": [0, 0, 0]}))
    assert resp.status_code == 204 and resp.content == b""
    # 10초 회차 전에는 Redis 에 없다
    assert rig.redis.keys("attn:*") == []
    await rig.ticks.release()
    assert rig.ticks.seconds[0] == 10.0
    # 0 인 값은 더하지 않는다
    assert rig.day("20261019") == {
        "app-spread|pc||pv": 1,
        "app-spread|pc|table|ms": 1500,
        "app-spread|pc|table|clicks": 2,
        "app-spread|pc|table|seen": 1,
    }


async def test_limits_and_empty_areas_are_accepted(rig: Rig) -> None:
    edge = _with(pv=0, a={"a" * 32: [600_000, 50, 1], "z9-": [0, 0, 0]})
    assert (await rig.post(edge)).status_code == 204
    assert (
        await rig.post(_with(a={f"a{i}": [1, 0, 0] for i in range(40)}))
    ).status_code == 204
    assert (await rig.post(_with(pv=0, a={}))).status_code == 204
    await rig.ticks.release()
    got = rig.day("20261019")
    assert got[f"app-spread|pc|{'a' * 32}|ms"] == 600_000
    assert got["app-spread|pc|a0|ms"] == 1  # 두 번째 비콘의 a0
    assert got["app-spread|pc||pv"] == 1


async def test_cross_site_is_403_before_reading_and_missing_header_passes(
    rig: Rig,
) -> None:
    for value in ("cross-site", "same-site", "none"):
        resp = await rig.post(beacon(), **{"Sec-Fetch-Site": value})
        assert resp.status_code == 403, value
        assert resp.json()["error"]["code"] == "cross_site"
    # 몸통이 커도 403 이 먼저다
    big = b" " * 10_000
    assert (await rig.post(big, **{"Sec-Fetch-Site": "cross-site"})).status_code == 403
    assert (
        await rig.post(beacon(), **{"Sec-Fetch-Site": "same-origin"})
    ).status_code == 204
    assert (await rig.post(beacon())).status_code == 204
    await rig.ticks.release()
    assert rig.day("20261019") == {"landing|pc||pv": 2}


async def test_body_over_4096_bytes_is_413(rig: Rig) -> None:
    body = beacon()
    exact = body + b" " * (4096 - len(body))
    assert len(exact) == 4096
    assert (await rig.post(exact)).status_code == 204
    resp = await rig.post(exact + b" ")
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "too_large"
    await rig.ticks.release()
    assert rig.day("20261019") == {"landing|pc||pv": 1}


async def test_before_the_gate_is_204_and_dropped(rig: Rig) -> None:
    gate = kst(2026, 10, 18)
    rig.clock.now = gate - 1
    assert (await rig.post(beacon())).status_code == 204
    # 검사는 게이트보다 먼저 — 어긋난 몸통은 게이트 전에도 400
    assert (await rig.post(b"{}")).status_code == 400
    await rig.ticks.release()
    assert rig.redis.keys("attn:*") == []
    rig.clock.now = gate
    assert (await rig.post(beacon())).status_code == 204
    await rig.ticks.release()
    assert rig.day(EFFECTIVE.replace("-", "")) == {"landing|pc||pv": 1}


async def test_same_ip_is_cut_at_the_121st_in_a_ten_minute_window(rig: Rig) -> None:
    window = kst(2026, 10, 19, 12, 0)  # 600 의 배수 시각 — 고정 창의 시작
    assert window % 600 == 0
    rig.clock.now = window
    ip = {"X-Client-IP": "203.0.113.7"}
    for _ in range(120):
        assert (await rig.post(beacon(), **ip)).status_code == 204
    assert (
        await rig.post(beacon(), **ip)
    ).status_code == 204  # 121번째 — 받지만 버린다
    # 다른 IP 는 따로 센다
    assert (
        await rig.post(beacon(), **{"X-Client-IP": "198.51.100.1"})
    ).status_code == 204
    rig.clock.now = window + 599
    assert (await rig.post(beacon(), **ip)).status_code == 204  # 같은 창 — 버림
    await rig.ticks.release()
    assert rig.day("20261019") == {"landing|pc||pv": 121}
    rig.clock.now = window + 600  # 다음 창 — 다시 받는다
    assert (await rig.post(beacon(), **ip)).status_code == 204
    await rig.ticks.release()
    assert rig.day("20261019") == {"landing|pc||pv": 122}


async def test_without_client_ip_there_is_no_limit(rig: Rig) -> None:
    for _ in range(200):
        assert (await rig.post(beacon())).status_code == 204
    await rig.ticks.release()
    assert rig.day("20261019") == {"landing|pc||pv": 200}


async def test_ip_never_reaches_redis_or_the_response(rig: Rig) -> None:
    ip = "203.0.113.77"
    resp = await rig.post(beacon(), **{"X-Client-IP": ip})
    await rig.ticks.release()
    dump = repr({k: rig.redis.hgetall(k) for k in rig.redis.keys("*")})
    assert ip not in dump and ip not in resp.text


@pytest.mark.parametrize(
    ("moment", "key", "expire"),
    [
        ((2026, 10, 19, 23, 59, 59), "20261019", (2027, 1, 18)),
        ((2026, 10, 20, 0, 0, 0), "20261020", (2027, 1, 19)),
    ],
)
async def test_kst_day_boundary_picks_the_key_and_expireat(
    rig: Rig, moment: tuple[int, ...], key: str, expire: tuple[int, int, int]
) -> None:
    rig.clock.now = kst(*moment)
    assert (await rig.post(beacon())).status_code == 204
    await rig.ticks.release()
    assert rig.redis.keys("attn:*") == [f"attn:d:{key}".encode()]
    # 그 KST 날의 끝(다음 날 00:00 KST) + 90일
    assert rig.redis.expiretime(f"attn:d:{key}") == int(kst(*expire))
