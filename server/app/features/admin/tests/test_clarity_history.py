"""Clarity 날별 기록 `admin:clarity:day:<YYYYMMDD>` 과 `/admin/clarity/history` (스펙 068 §3.2·§3.4·§4 '날별').

기본이 성공할 때마다(그날 차원이 성공할 때도) 그 KST 날의 칸을 덮어쓴다 — 기본의 정규화 값 + 그날 성공한 차원 목록 +
`fetchedAt`, 만료는 그날 끝 + 90일. World 의 시계 0 은 2026-10-01 18:30 KST 다(KST 자정까지 5시간 30분).
"""

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.redis_bus import RedisBus
from app.features.admin.tests.clarity_fakes import (
    CHANNEL_LISTS,
    DEVICE_EXPORT,
    DEVICE_LISTS,
    DIMS_KEYS,
    NOW,
    SESSIONS_EXPORT,
    Clarity,
    World,
    device_row,
)
from app.features.admin.visits import VisitFeeds
from app.main import create_app

KST = timezone(timedelta(hours=9))
TO_MIDNIGHT = 19_800  # 시계 0(18:30 KST)에서 다음 KST 자정까지
ALL_DIMS = {**DEVICE_LISTS, **CHANNEL_LISTS}
NO_DIMS = dict.fromkeys(DIMS_KEYS)
PART = ["state", "code", "fetchedAt", "refreshSec", "days", "rows"]
ROW = ["date", "fetchedAt", "traffic", "summary", "countries", "dims"]


def ms(t: float) -> int:
    return int((NOW + t) * 1000)


def kst_midnight_after(day: str) -> int:
    d = datetime.strptime(day, "%Y%m%d").replace(tzinfo=KST) + timedelta(days=1)
    return int(d.timestamp())


def record(fetched: int, dims: dict | None = None) -> str:
    return json.dumps({"fetchedAt": fetched, "traffic": {"sessions": 1}, "dims": dims})


async def test_a_base_success_writes_its_kst_day_with_that_days_dims() -> None:
    w = World(Clarity((200, SESSIONS_EXPORT)))
    body = await w.get()
    day = w.stored_day("20261001")
    assert day == {
        "fetchedAt": ms(0),
        "traffic": body["traffic"],
        "summary": body["summary"],
        "countries": body["countries"],
        "dims": ALL_DIMS,  # 같은 갱신에서 기본 뒤에 성공한 차원 — 그날 값
    }
    assert body["countries"] and body["summary"]["scrollDepth"] == 57.46
    # 만료 = 그날 끝(다음 날 00:00 KST) + 90일. 주소가 실리는 metrics 는 넣지 않는다
    assert w.day_ttl("20261001") == kst_midnight_after("20261001") + 90 * 86_400
    assert "kimptrack.com" not in json.dumps(day)


async def test_later_successes_overwrite_the_day_and_a_new_day_starts_without_dims() -> (
    None
):
    w = World(Clarity())
    await w.get()
    w.advance(6)  # 10-02 00:30 KST — 기본만
    await w.get()
    assert w.stored_day("20261002")["dims"] == NO_DIMS  # 차원은 전날 성공뿐
    assert w.stored_day("20261002")["fetchedAt"] == ms(6 * 3600)
    assert w.stored_day("20261001")["fetchedAt"] == ms(0)  # 지난날 칸은 그대로
    w.advance(6)
    await w.get()  # 06:30 — 덮어쓴다
    assert w.stored_day("20261002")["fetchedAt"] == ms(12 * 3600)
    w.advance(12)
    await w.get()  # 18:30 — 기본 뒤 차원 둘이 성공해 그날 칸에 든다
    assert w.stored_day("20261002")["fetchedAt"] == ms(24 * 3600)
    assert w.stored_day("20261002")["dims"] == ALL_DIMS
    assert w.day_ttl("20261002") == kst_midnight_after("20261002") + 90 * 86_400


async def test_dims_that_succeed_after_midnight_never_blank_the_day_before() -> None:
    """23:59:50 에 기본(10초 걸림)과 기기 차원을 바로 부르면 기기는 다음 날 00:00:00 에 성공한다 — 그 성공은 전날 칸에
    쓰지 않고(전날 18:30 의 기기 목록이 남는다), 다음 날 첫 기본 성공이 새 칸에 싣는다."""
    changed = [
        {"metricName": "Traffic", "information": [device_row(4, "Tablet", "X", "Y")]}
    ]
    w = World(Clarity(device=((200, DEVICE_EXPORT), (200, changed))))
    await w.get()
    w.t = TO_MIDNIGHT - 10
    w.forget("admin:clarity", "admin:clarity:dims")
    w.clarity.takes["base"] = 10
    await w.get()
    w.clarity.takes["base"] = 0
    assert w.clarity.calls[-2:] == [(TO_MIDNIGHT - 10, "base"), (TO_MIDNIGHT, "device")]
    before = w.stored_day("20261001")
    assert before["fetchedAt"] == ms(TO_MIDNIGHT - 10)
    assert before["dims"] == ALL_DIMS  # 18:30 의 기기 목록 그대로
    assert w.stored_day("20261002") is None  # 그날 기본 성공이 아직 없다
    w.t = TO_MIDNIGHT - 10 + 6 * 3600  # 다음 날 05:59:50 — 기본
    await w.get()
    after = w.stored_day("20261002")["dims"]
    assert (after["device"], after["os"]) == ([["Tablet", 4]], [["Y", 4]])
    assert after["channel"] is None  # 유입은 그날 성공이 없다


async def test_a_restarted_app_keeps_the_days_dims_on_its_first_base_call() -> None:
    """다시 뜬 앱의 첫 갱신은 기본이 차원보다 먼저 불린다 — 메모리가 비어도 Redis 의 그날 차원 기록으로 칸을 채운다."""
    w = World(Clarity())
    w.advance(6)  # 10-02 00:30 KST
    await w.get()
    w.advance(6)  # 06:30 — 새 앱, 기본만
    await w.get(w.new_app())
    assert w.clarity.calls[-1][1] == "base"
    day = w.stored_day("20261002")
    assert (day["fetchedAt"], day["dims"]) == (ms(12 * 3600), ALL_DIMS)


async def test_a_failed_day_write_is_written_on_the_next_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="marketlens.admin")
    w = World(Clarity())
    w.broken = {"day-write"}
    assert (await w.get())["state"] == "ok"
    assert w.stored_day("20261001") is None
    w.broken = set()
    w.advance(sec=60)
    await w.get()
    assert len(w.clarity.calls) == 4  # 부르지 않고 그때의 값으로 다시 쓴다
    assert w.stored_day("20261001")["fetchedAt"] == ms(0)
    assert w.stored_day("20261001")["dims"] == ALL_DIMS
    assert [r.getMessage() for r in caplog.records] == [
        "관리자 피드 clarity.day 실패 — redis"
    ]


async def test_history_lists_days_in_order_and_skips_days_without_records() -> None:
    w = World(Clarity())
    for t in (0, 24, 72):  # 10-01·10-02·10-04 — 10-03 은 부르지 않았다
        w.t = t * 3600
        await w.get()
    body = await w.history("7")
    assert list(body) == PART
    assert (body["state"], body["code"], body["fetchedAt"], body["refreshSec"]) == (
        "ok",
        None,
        ms(72 * 3600),
        60,
    )
    assert body["days"] == 7
    assert [r["date"] for r in body["rows"]] == [
        "2026-10-01",
        "2026-10-02",
        "2026-10-04",
    ]
    assert [list(r) for r in body["rows"]] == [ROW] * 3
    assert [r["fetchedAt"] for r in body["rows"]] == [
        ms(0),
        ms(24 * 3600),
        ms(72 * 3600),
    ]
    assert body["rows"][2]["dims"] == ALL_DIMS
    assert body["rows"][0]["traffic"] == (await w.get())["traffic"]


@pytest.mark.parametrize(
    ("asked", "days", "rows"),
    [
        ("7", 7, 1),
        ("30", 30, 2),
        ("90", 90, 3),
        (None, 30, 2),
        ("365", 30, 2),
        ("", 30, 2),
    ],
)
async def test_history_days_choose_the_window(
    asked: str | None, days: int, rows: int
) -> None:
    w = World(Clarity())
    for back in (0, 10, 40, 100):  # 오늘(10-01)을 넣어 거꾸로 — 창 밖의 날은 빠진다
        day = (datetime(2026, 10, 1) - timedelta(days=back)).strftime("%Y%m%d")
        w.seed(f"admin:clarity:day:{day}", record(back))
    body = await w.history(asked)
    assert (body["days"], len(body["rows"])) == (days, rows)
    assert [r["fetchedAt"] for r in body["rows"]] == sorted(
        (r["fetchedAt"] for r in body["rows"]), reverse=True
    )  # 날 오름차순 = 거꾸로 센 날은 내림차순
    assert w.clarity.calls == []  # 날별 기록 읽기는 Clarity 를 부르지 않는다


async def test_history_is_cached_for_sixty_seconds_per_window() -> None:
    w = World(Clarity())
    w.seed("admin:clarity:day:20261001", record(1))
    assert len((await w.history("7"))["rows"]) == 1
    w.seed("admin:clarity:day:20260930", record(2))
    w.advance(sec=59)
    assert len((await w.history("7"))["rows"]) == 1
    assert len((await w.history("30"))["rows"]) == 2  # 창마다 따로
    w.advance(sec=1)
    assert len((await w.history("7"))["rows"]) == 2


async def test_history_skips_malformed_records_and_nulls_wrong_fields() -> None:
    w = World(Clarity())
    w.seed("admin:clarity:day:20260929", "not json")
    w.seed("admin:clarity:day:20260930", "[1, 2]")
    w.seed(
        "admin:clarity:day:20261001",
        json.dumps({"fetchedAt": "x", "traffic": [1], "summary": {"a": 1}, "dims": 3}),
    )
    (row,) = (await w.history("7"))["rows"]
    assert row == {
        "date": "2026-10-01",
        "fetchedAt": None,
        "traffic": None,
        "summary": {"a": 1},
        "countries": None,
        "dims": None,
    }


async def test_history_without_token_is_unconfigured_and_reads_nothing() -> None:
    w = World(Clarity(), token=None)
    assert await w.history("7") == {
        "state": "unconfigured",
        "code": None,
        "fetchedAt": None,
        "refreshSec": 60,
        "days": 7,
        "rows": None,
    }
    assert w.redis_calls == []


async def test_history_unreadable_redis_is_error_redis_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="marketlens.admin")
    w = World(Clarity())
    w.broken = {"day-read"}
    body = await w.history()
    assert (body["state"], body["code"], body["days"], body["rows"]) == (
        "error",
        "redis",
        30,
        None,
    )
    assert (await w.feeds.clarity_history("7", bus=None))["code"] == "redis"
    assert [r.getMessage() for r in caplog.records] == [
        "관리자 피드 clarity.history 실패 — redis"
    ]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    app = create_app()  # lifespan 없음 — Redis 자리만 fakeredis 로 채운다
    server = fakeredis.FakeServer()
    today = datetime.now(KST).strftime("%Y%m%d")
    fakeredis.FakeRedis(server=server).set(f"admin:clarity:day:{today}", record(5))
    app.state.spreads_bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    yield TestClient(app)
    get_settings.cache_clear()


def test_http_history_answers_one_part_and_reads_only_redis(api: TestClient) -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    assert api.get("/admin/clarity/history?days=7").json()["state"] == "unconfigured"
    api.app.state.admin_visits = VisitFeeds(
        access_dir=None, clarity_token="t", transport=httpx.MockTransport(handle)
    )
    resp = api.get("/admin/clarity/history?days=90")
    assert (
        resp.status_code == 200 and resp.headers["content-type"] == "application/json"
    )
    body = resp.json()
    assert list(body) == PART and (body["state"], body["days"]) == ("ok", 90)
    assert [r["fetchedAt"] for r in body["rows"]] == [5] and calls == []
