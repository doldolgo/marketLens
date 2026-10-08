"""관리자 피드 `GET /admin/attention` — 창·게이트·상태·순서·60초 캐시 (스펙 052 §3.6·§4)."""

from collections.abc import Mapping, Sequence

import pytest

from app.core.redis_bus import RedisBus
from app.features.attention.tests.conftest import Rig, kst

KEYS = ["state", "code", "gateAt", "days", "from", "to", "fetchedAt", "rows"]


def _fill(rig: Rig, day: str, fields: Mapping[str, int]) -> None:
    rig.redis.hset(f"attn:d:{day}", mapping=dict(fields))


async def _get(rig: Rig, days: str | None = None) -> dict:
    params = {} if days is None else {"days": days}
    resp = await rig.client.get("/admin/attention", params=params)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    return resp.json()


@pytest.mark.parametrize(
    ("days", "n"),
    [
        ("1", 1),
        ("7", 7),
        ("30", 30),
        ("90", 90),
        (None, 7),
        ("14", 7),
        ("7d", 7),
        ("", 7),
    ],
)
async def test_days_choose_the_window_and_clip_it_at_the_gate(
    rig: Rig, days: str | None, n: int
) -> None:
    rig.clock.now = kst(2027, 3, 1, 9)
    body = await _get(rig, days)
    assert list(body) == KEYS
    assert (body["state"], body["code"], body["days"]) == ("ok", None, n)
    assert body["gateAt"] == int(kst(2026, 10, 18)) * 1000
    assert body["fetchedAt"] == int(kst(2027, 3, 1, 9)) * 1000
    assert body["to"] == "2027-03-01"
    expected = {1: "2027-03-01", 7: "2027-02-23", 30: "2027-01-31", 90: "2026-12-02"}
    assert body["from"] == expected[n]


async def test_window_starting_before_the_gate_starts_at_the_gate(rig: Rig) -> None:
    rig.clock.now = kst(2026, 10, 20, 8)
    _fill(rig, "20261017", {"landing|pc||pv": 50})  # 게이트 전 날 — 창 밖
    _fill(rig, "20261018", {"landing|pc||pv": 2, "landing|pc|hero|ms": 900})
    _fill(rig, "20261020", {"landing|pc||pv": 1, "landing|pc|hero|ms": 100})
    body = await _get(rig, "30")
    assert (body["from"], body["to"]) == ("2026-10-18", "2026-10-20")
    assert body["rows"] == [
        {
            "page": "landing",
            "device": "pc",
            "pv": 3,
            "areas": [{"id": "hero", "ms": 1000, "clicks": 0, "seen": 0}],
        }
    ]


async def test_days_outside_the_window_are_not_summed(rig: Rig) -> None:
    rig.clock.now = kst(2026, 11, 30, 12)
    _fill(rig, "20261123", {"landing|pc||pv": 7})  # 오늘 − 7일 — 7일 창 밖
    _fill(rig, "20261124", {"landing|pc||pv": 1})  # 오늘 − 6일 — 안
    body = await _get(rig, "7")
    assert body["from"] == "2026-11-24" and body["rows"][0]["pv"] == 1
    body = await _get(rig, "1")
    assert body["rows"] == []


async def test_before_the_gate_answers_before_gate_without_reading(rig: Rig) -> None:
    rig.clock.now = kst(2026, 10, 17, 23, 59, 59)
    _fill(rig, "20261017", {"landing|pc||pv": 1})
    body = await _get(rig)
    assert (body["state"], body["code"], body["rows"]) == ("before_gate", None, [])
    assert (body["from"], body["to"]) == ("2026-10-18", "2026-10-17")


async def test_redis_failure_is_state_error_code_redis(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(self: RedisBus, days: Sequence[str]) -> dict:
        raise ConnectionError("redis down")

    monkeypatch.setattr(RedisBus, "attention_days", down)
    body = await _get(rig)
    assert (body["state"], body["code"], body["rows"]) == ("error", "redis", [])


async def test_rows_follow_page_order_skip_zero_pv_and_sort_areas_by_ms(
    rig: Rig,
) -> None:
    _fill(
        rig,
        "20261019",
        {
            "app-history|pc||pv": 2,
            "app-history|pc|chart|ms": 500,
            "app-history|pc|table|ms": 9000,
            "app-history|pc|table|clicks": 4,
            "app-history|pc|table|seen": 2,
            "app-history|pc|list|ms": 500,
            "landing|mobile||pv": 1,
            "landing|mobile|hero|seen": 1,
            "landing|pc|hero|ms": 300,  # pv 0 — 행 없음
            "nope|pc||pv": 3,  # 목록 밖 화면 — 버림
            "landing|tv||pv": 3,  # 목록 밖 기기 — 버림
            "landing|pc|x|y|ms": 1,  # 꼴이 다름 — 버림
        },
    )
    body = await _get(rig)
    assert [(r["page"], r["device"], r["pv"]) for r in body["rows"]] == [
        ("landing", "mobile", 1),
        ("app-history", "pc", 2),
    ]
    history = body["rows"][1]["areas"]
    assert [a["id"] for a in history] == [
        "table",
        "chart",
        "list",
    ]  # ms 큰 순, 같으면 id 순
    assert history[0] == {"id": "table", "ms": 9000, "clicks": 4, "seen": 2}


async def test_same_days_is_cached_for_60_seconds(rig: Rig) -> None:
    _fill(rig, "20261019", {"landing|pc||pv": 1})
    first = await _get(rig, "7")
    _fill(rig, "20261019", {"landing|pc||pv": 5})
    rig.clock.now += 30
    rig.clock.mono += 59.9
    assert await _get(rig, "7") == first
    # 다른 days 는 따로 칸
    assert (await _get(rig, "1"))["rows"][0]["pv"] == 5
    # 잘못된 값은 7 의 칸을 쓴다
    assert await _get(rig, "bad") == first
    rig.clock.mono += 0.1
    again = await _get(rig, "7")
    assert again["rows"][0]["pv"] == 5 and again["fetchedAt"] > first["fetchedAt"]
