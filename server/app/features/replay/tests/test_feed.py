"""관리자 피드 셋 — live 60초 경계·순서·50개, sessions ip·200개·bad_ip, session after/next/more·만료 null·bad_sid, redis 오류
(스펙 073 §3.6·§4)."""

import json
import zlib

import pytest

from app.core.redis_bus import RedisBus
from app.features.replay.tests.conftest import IP, NOON, SID, Rig, body

NOON_MS = int(NOON * 1000)
ROW_KEYS = [
    "sid",
    "ip",
    "dev",
    "page",
    "pages",
    "now",
    "first",
    "last",
    "n",
    "clicks",
    "w",
    "h",
    "cut",
]


def sid(n: int) -> str:
    return f"{n:016x}"


async def _json(rig: Rig, path: str, **params: str) -> dict:
    resp = await rig.get(path, **params)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["content-type"] == "application/json"
    return resp.json()


# --- live -----------------------------------------------------------------------------


async def test_live_keeps_sessions_seen_in_the_last_60_seconds_newest_first(
    rig: Rig,
) -> None:
    for i in range(3):
        rig.clock.now = NOON + i
        ev = [[0, "c", 1, 1, ""], [1, "p", "app-gap"]] if i == 2 else [[0, "v", 1]]
        await rig.post(body(sid=sid(i), ev=ev))
    rig.clock.now = NOON + 61
    got = await _json(rig, "live")
    assert (got["state"], got["code"], got["now"]) == ("ok", None, NOON_MS + 61_000)
    # last = now − 60,000 은 든다(경계), − 61,000 은 빠진다
    assert [r["sid"] for r in got["rows"]] == [sid(2), sid(1)]
    assert list(got["rows"][0]) == ROW_KEYS
    assert got["rows"][0] == {
        "sid": sid(2),
        "ip": IP,
        "dev": "pc",
        "page": "app-spread",
        "pages": ["app-spread", "app-gap"],
        "now": "app-gap",
        "first": NOON_MS + 2000,
        "last": NOON_MS + 2000,
        "n": 1,
        "clicks": 1,
        "w": 1280,
        "h": 800,
        "cut": False,
    }
    rig.clock.now = NOON + 62.001
    assert (await _json(rig, "live"))["rows"] == []


async def test_live_has_at_most_50_rows_and_skips_expired_heads(rig: Rig) -> None:
    for i in range(51):
        rig.clock.now = NOON + i * 0.01
        await rig.post(body(sid=sid(i)))
    got = await _json(rig, "live")
    assert [r["sid"] for r in got["rows"]] == [sid(i) for i in range(50, 0, -1)]
    # 머리가 만료돼 집합에만 남은 sid 는 행이 아니다
    rig.redis.delete(f"rp:m:{sid(50)}")
    got = await _json(rig, "live")
    assert [r["sid"] for r in got["rows"]][:2] == [sid(49), sid(48)]


# --- sessions -------------------------------------------------------------------------


async def test_sessions_by_ip_and_for_all_ips(rig: Rig) -> None:
    other = "2001:db8::1"
    plan = [(0, IP, 0), (1, other, 1), (2, IP, 2), (3, None, 3)]
    for n, ip, at in plan:
        rig.clock.now = NOON + at
        await rig.post(body(sid=sid(n)), ip=ip)
    # sid(0) 이 나중에 다시 움직이면 모든 IP 목록(last 순)의 맨 앞, IP 목록(first 순)은 그대로
    rig.clock.now = NOON + 10
    await rig.post(body(sid=sid(0), seq=1))
    by_ip = await _json(rig, "sessions", ip=IP)
    assert (by_ip["state"], by_ip["code"], by_ip["ip"]) == ("ok", None, IP)
    assert [r["sid"] for r in by_ip["rows"]] == [sid(2), sid(0)]
    assert [r["sid"] for r in (await _json(rig, "sessions", ip=other))["rows"]] == [
        sid(1)
    ]
    assert [r["sid"] for r in (await _json(rig, "sessions", ip="(없음)"))["rows"]] == [
        sid(3)
    ]
    every = await _json(rig, "sessions")
    assert every["ip"] is None
    assert [r["sid"] for r in every["rows"]] == [sid(0), sid(3), sid(2), sid(1)]
    assert list(every["rows"][0]) == ROW_KEYS
    assert (await _json(rig, "sessions", ip="198.51.100.200"))["rows"] == []


async def test_sessions_have_at_most_200_rows(rig: Rig) -> None:
    for i in range(201):
        rig.clock.now = NOON + i * 0.01
        await rig.post(body(sid=sid(i)))
    newest = [sid(i) for i in range(200, 0, -1)]
    assert [r["sid"] for r in (await _json(rig, "sessions"))["rows"]] == newest
    assert [r["sid"] for r in (await _json(rig, "sessions", ip=IP))["rows"]] == newest


@pytest.mark.parametrize(
    "ip",
    ["", "1.2.3", "2001:DB8::1", "2001:db8:0::1", "010.0.0.1", "1.2.3.4/24", "(기타)"],
)
async def test_bad_ip_is_a_400(rig: Rig, ip: str) -> None:
    resp = await rig.get("sessions", ip=ip)
    assert resp.status_code == 400 and resp.headers["cache-control"] == "no-store"
    assert resp.json()["error"] == {
        "code": "bad_ip",
        "message": "ip 는 IPv4·IPv6 주소(정규 글자) 또는 (없음)이어야 합니다.",
        "detail": None,
    }


# --- session --------------------------------------------------------------------------


async def test_session_returns_the_head_and_the_stored_batches(rig: Rig) -> None:
    first = [[0, "m", 5, 6], [10, "c", 5, 6, "kpi"]]
    await rig.post(body(ev=first))
    rig.clock.now = NOON + 2
    await rig.post(body(seq=1, ev=[[2000, "p", "app-flow"]]))
    got = await _json(rig, "session", sid=SID)
    assert list(got) == ["state", "code", "meta", "batches", "next", "more", "archived"]
    assert got["archived"] is False
    assert (got["state"], got["code"], got["next"], got["more"]) == (
        "ok",
        None,
        2,
        False,
    )
    assert got["meta"]["pages"] == ["app-spread", "app-flow"]
    assert (got["meta"]["n"], got["meta"]["last"]) == (2, NOON_MS + 2000)
    assert got["batches"] == [
        {"at": NOON_MS, "seq": 0, "ev": first},
        {"at": NOON_MS + 2000, "seq": 1, "ev": [[2000, "p", "app-flow"]]},
    ]


async def test_session_pages_600_batches_with_after_next_more(rig: Rig) -> None:
    await rig.post(body())
    for seq in range(1, 1201):
        raw = json.dumps({"at": NOON_MS + seq, "seq": seq, "ev": [[seq, "v", 1]]})
        rig.redis.rpush(f"rp:e:{SID}", zlib.compress(raw.encode()))
    pages = []
    for after in (None, "600", "1200", "5000"):
        params = {"sid": SID} if after is None else {"sid": SID, "after": after}
        got = await _json(rig, "session", **params)
        pages.append(
            (
                [b["seq"] for b in got["batches"]][:1],
                len(got["batches"]),
                got["next"],
                got["more"],
            )
        )
    assert pages == [
        ([0], 600, 600, True),
        ([600], 600, 1200, True),
        ([1200], 1, 1201, False),
        ([], 0, 5000, False),
    ]
    # 0 이상의 정수 글자가 아니면 0 부터
    for after in ("", "abc", "-1", "1.5", "1e3"):
        got = await _json(rig, "session", sid=SID, after=after)
        assert (got["batches"][0]["seq"], got["next"]) == (0, 600), after


async def test_unknown_or_expired_session_has_null_meta(rig: Rig) -> None:
    got = await _json(rig, "session", sid=sid(7), after="3")
    assert got == {
        "state": "ok",
        "code": None,
        "meta": None,
        "batches": [],
        "next": 3,
        "more": False,
        "archived": False,
    }


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"sid": ""},
        {"sid": "0123456789ABCDEF"},
        {"sid": "0" * 15},
        {"sid": "0" * 17},
        {"sid": "g" * 16},
        {"sid": "0123456789abcde\n"},
    ],
)
async def test_bad_sid_is_a_400(rig: Rig, params: dict[str, str]) -> None:
    resp = await rig.get("session", **params)
    assert resp.status_code == 400 and resp.headers["cache-control"] == "no-store"
    assert resp.json()["error"] == {
        "code": "bad_sid",
        "message": "sid 는 16자 소문자 16진수여야 합니다.",
        "detail": None,
    }


# --- Redis 오류 -----------------------------------------------------------------------


async def test_redis_failure_is_state_error_code_redis_on_all_three(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(self: RedisBus, *args: object) -> object:
        raise ConnectionError("redis down")

    for name in ("replay_recent", "replay_ip", "replay_batches"):
        monkeypatch.setattr(RedisBus, name, down)
    live = await _json(rig, "live")
    assert (live["state"], live["code"], live["rows"]) == ("error", "redis", [])
    for params in ({}, {"ip": IP}):
        got = await _json(rig, "sessions", **params)
        assert (got["state"], got["code"], got["rows"]) == ("error", "redis", [])
    got = await _json(rig, "session", sid=SID, after="2")
    assert got == {
        "state": "error",
        "code": "redis",
        "meta": None,
        "batches": [],
        "next": 2,
        "more": False,
        "archived": False,
    }


async def test_without_a_bus_the_feeds_are_state_error(rig: Rig) -> None:
    rig.app.state.spreads_bus = None
    assert (await _json(rig, "live"))["code"] == "redis"
    assert (await _json(rig, "sessions"))["code"] == "redis"
    assert (await _json(rig, "session", sid=SID))["state"] == "error"
