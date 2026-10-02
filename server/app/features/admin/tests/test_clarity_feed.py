"""`/admin/clarity` 피드 — 토큰·3시간 간격·재시작·상태·7일·느린 호출 (스펙 035 §3.3·§3.5·§4).

Clarity 는 httpx MockTransport, Redis 는 fakeredis. 피드를 테스트 루프에서 직접 부르고 시계는 손으로 민다.
"""

import asyncio
import json

import httpx
import pytest

from app.features.admin.tests.clarity_fakes import (
    EXPORT,
    HOUR_MS,
    NOW,
    TOKEN,
    Clarity,
    World,
)
from app.features.admin.visits import VisitFeeds


async def test_without_token_is_unconfigured_and_calls_nothing() -> None:
    w = World(Clarity(), token=None)
    body = await w.get()
    assert body == {
        "state": "unconfigured",
        "code": None,
        "fetchedAt": None,
        "refreshSec": 10_800,
        "nextAt": None,
        "numOfDays": None,
        "traffic": None,
        "summary": None,
        "countries": None,
        "metrics": None,
    }
    assert w.clarity.requests == [] and w.stored() is None


async def test_first_request_calls_once_with_bearer_and_one_day() -> None:
    w = World(Clarity())
    body = await w.get()
    (req,) = w.clarity.requests
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert str(req.url).split("?")[0] == (
        "https://www.clarity.ms/export-data/api/v1/project-live-insights"
    )
    assert dict(req.url.params) == {"numOfDays": "1"}
    now_ms = int(NOW * 1000)
    assert (body["state"], body["code"], body["fetchedAt"]) == ("ok", None, now_ms)
    assert body["nextAt"] == now_ms + 3 * HOUR_MS and body["numOfDays"] == 1
    assert body["traffic"] == {
        "sessions": 120,
        "botSessions": 8,
        "users": 95,
        "pagesPerSession": 1.75,
    }
    assert [m["name"] for m in body["metrics"]] == [
        "Popular Pages",
        "Referrer URL",
        "Page Title",
    ]
    assert w.stored()["attemptAt"] == now_ms


async def test_within_three_hours_and_after_restart_no_call_then_one_after() -> None:
    w = World(Clarity())
    first = await w.get()
    w.advance(sec=3 * 3600 - 1)
    assert await w.get() == first
    restarted = w.new_app()  # 배포 — 메모리는 비었고 Redis 기록만 있다
    assert await w.get(restarted) == first
    assert len(w.clarity.requests) == 1
    w.advance(sec=1)  # 3시간
    body = await w.get(restarted)
    assert len(w.clarity.requests) == 2
    assert body["fetchedAt"] == int((NOW + 3 * 3600) * 1000)


@pytest.mark.parametrize(
    ("status", "state"),
    [(401, "denied"), (403, "denied"), (429, "error"), (503, "error")],
)
async def test_failures_keep_the_last_success_and_wait_three_hours(
    status: int, state: str
) -> None:
    w = World(Clarity((200, EXPORT), (status, {"message": "Exceeded daily limit"})))
    ok = await w.get()
    w.advance(3)
    body = await w.get()
    attempt = int((NOW + 3 * 3600) * 1000)
    assert (body["state"], body["code"]) == (state, f"http_{status}")
    assert (
        body["fetchedAt"] == ok["fetchedAt"]
    )  # 마지막 성공 시각 — 경과는 이것이 말한다
    assert body["traffic"] == ok["traffic"] and body["metrics"] == ok["metrics"]
    assert body["nextAt"] == attempt + 3 * HOUR_MS
    w.advance(1)
    assert (await w.get())["state"] == state  # 실패도 시도로 센다 — 부르지 않는다
    assert len(w.clarity.requests) == 2
    assert "Exceeded" not in json.dumps(body)


async def test_timeout_is_error_timeout() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    w = World(Clarity())
    feeds = VisitFeeds(
        access_dir=None,
        clarity_token=TOKEN,
        transport=httpx.MockTransport(boom),
        clock=lambda: NOW,
        mono=lambda: 0.0,
    )
    body = await w.get(feeds)
    assert (body["state"], body["code"], body["traffic"]) == ("error", "timeout", None)


async def test_values_are_dropped_seven_days_after_the_last_success() -> None:
    w = World(Clarity((200, EXPORT), (429, {})))
    await w.get()
    w.advance(7 * 24 - 1)  # 7일 − 1시간 — 실패, 값은 아직 남는다
    assert (await w.get())["traffic"] is not None
    w.advance(1, sec=1)  # 7일 + 1초 — 마지막 시도에서 1시간이라 부르지 않는다
    body = await w.get()
    assert len(w.clarity.requests) == 2
    assert (body["state"], body["fetchedAt"], body["traffic"], body["metrics"]) == (
        "error",
        None,
        None,
        None,
    )
    assert body["nextAt"] is not None  # 시도 기록은 남는다
    w.advance(2)  # 다음 시도 — 저장값에서도 값이 빠진다
    await w.get()
    stored = w.stored()
    assert stored["values"] is None and stored["successAt"] is None
    assert stored["state"] == "error" and stored["attemptAt"] == int(
        (NOW + (7 * 24 + 2) * 3600 + 1) * 1000
    )


async def test_slow_call_answers_pending_then_the_same_call_fills_it() -> None:
    clarity = Clarity()
    clarity.gate = asyncio.Event()
    w = World(clarity, wait=0.1)
    first, second = await asyncio.gather(w.get(), w.get())
    assert first["state"] == second["state"] == "pending"
    assert first["nextAt"] is None and first["traffic"] is None
    clarity.gate.set()
    for _ in range(50):
        await asyncio.sleep(0.02)
        body = await w.get()
        if body["state"] != "pending":
            break
    assert body["state"] == "ok" and len(clarity.requests) == 1
