"""`/admin/clarity` 두 호출이 서로에게 기대는 곳 — 미루기·느린 호출·한쪽 실패·7일·옛 기록 (스펙 040 §3.2·§3.5·§3.7·§4).

기본이 401·403·429 면 묶음은 미루고 시도로도 적지 않는다. 두 기록은 state·값·간격을 따로 갖는다.
"""

import json

from app.features.admin.tests.clarity_fakes import (
    EXPORT,
    HOUR_MS,
    NOW,
    PAGES_EXPORT,
    Clarity,
    World,
)


async def test_a_denied_or_limited_summary_defers_the_pages_call() -> None:
    w = World(Clarity((401, {})))
    body = await w.get()
    assert w.clarity.count("pages") == 0
    assert (body["state"], body["code"]) == ("denied", "http_401")
    assert body["pages"] == {
        "state": "denied",
        "code": "http_401",
        "fetchedAt": None,
        "refreshSec": 43_200,
        "nextAt": None,
        "numOfDays": None,
        "rowsIn": None,
        "rowLimitHit": None,
        "groups": None,
    }
    assert w.stored_pages() is None  # 시도로도 적지 않는다
    limited = World(Clarity((429, {})))
    body = await limited.get()
    assert limited.clarity.count("pages") == 0
    assert (body["pages"]["state"], body["pages"]["code"]) == ("error", "http_429")


async def test_a_summary_back_to_ok_calls_the_deferred_pages() -> None:
    w = World(Clarity((429, {}), (200, EXPORT)))
    await w.get()
    w.advance(4)
    body = await w.get()
    assert [k for _, k in w.clarity.calls] == ["base", "base", "pages"]
    assert body["state"] == "ok" and body["pages"]["state"] == "ok"


async def test_other_summary_failures_still_call_the_pages() -> None:
    for reply in ((500, {}), (0, Clarity.TIMEOUT)):
        w = World(Clarity(reply))
        body = await w.get()
        assert [k for _, k in w.clarity.calls] == ["base", "pages"], reply
        assert body["state"] == "error" and body["pages"]["state"] == "ok"
    assert body["code"] == "timeout"


async def test_a_slow_summary_answers_pending_then_both_values() -> None:
    clarity = Clarity()
    clarity.delay["base"] = 0.5
    w = World(clarity, wait=0.1)
    body = await w.get()
    assert body["state"] == body["pages"]["state"] == "pending"
    assert body["traffic"] is None and body["pages"]["groups"] is None
    for _ in range(50):
        w.advance(sec=1)
        body = await w.get()
        if body["pages"]["state"] != "pending":
            break
    assert body["state"] == body["pages"]["state"] == "ok"
    assert body["traffic"] is not None and body["pages"]["groups"]
    assert clarity.count("base") == clarity.count("pages") == 1


async def test_a_failed_pages_call_keeps_its_values_and_leaves_the_summary_alone() -> (
    None
):
    w = World(Clarity(pages=((200, PAGES_EXPORT), (500, {}))))
    ok = await w.get()
    w.advance(12)
    body = await w.get()
    attempt = int((NOW + 12 * 3600) * 1000)
    assert (body["pages"]["state"], body["pages"]["code"]) == ("error", "http_500")
    assert body["pages"]["groups"] == ok["pages"]["groups"]
    assert body["pages"]["fetchedAt"] == ok["pages"]["fetchedAt"]
    assert body["pages"]["nextAt"] == attempt + 12 * HOUR_MS
    assert (body["state"], body["code"]) == ("ok", None)
    assert body["fetchedAt"] == attempt  # 기본은 같은 갱신에서 따로 성공


async def test_seven_days_drop_each_records_values_separately() -> None:
    w = World(Clarity(pages=((200, PAGES_EXPORT), (500, {}))))
    ok = await w.get()
    w.advance(7 * 24, sec=1)  # 묶음은 마지막 성공에서 7일 + 1초 — 이번 시도도 실패
    body = await w.get()
    assert (body["pages"]["state"], body["pages"]["groups"]) == ("error", None)
    assert body["pages"]["fetchedAt"] is None and body["pages"]["nextAt"] is not None
    assert body["state"] == "ok" and body["traffic"] == ok["traffic"]  # 기본은 따로
    stored = w.stored_pages()
    assert stored["values"] is None and stored["successAt"] is None
    assert stored["code"] == "http_500"  # 시도 기록은 남는다


async def test_an_old_035_record_answers_without_summary_and_countries() -> None:
    now_ms = int(NOW * 1000)
    old = {
        "attemptAt": now_ms - HOUR_MS,
        "state": "ok",
        "code": None,
        "successAt": now_ms - HOUR_MS,
        "values": {
            "numOfDays": 1,
            "traffic": {
                "sessions": 1,
                "botSessions": 0,
                "users": 1,
                "pagesPerSession": 1.0,
            },
            "metrics": [{"name": "PopularPages", "rows": []}],
        },
    }
    w = World(Clarity())
    w.seed("admin:clarity", json.dumps(old))
    body = await w.get()
    assert w.clarity.count("base") == 0  # 035 의 마지막 시도 + 4시간에 부른다
    assert w.clarity.count("pages") == 1  # 묶음은 기록이 없어 첫 요청에
    assert (body["state"], body["summary"], body["countries"]) == ("ok", None, None)
    assert body["traffic"] == old["values"]["traffic"]
    assert body["metrics"] == old["values"]["metrics"]
    assert body["nextAt"] == now_ms + 3 * HOUR_MS


async def test_a_long_deferral_still_drops_week_old_pages_values_from_redis() -> None:
    """기본 401 이 8일 이어져 묶음을 계속 미뤄도, Redis 묶음 기록의 값은 마지막 성공에서 7일 뒤 버린다 —
    시도로 세지 않고(시도 시각·결과 그대로), 사람이 지운 키는 되살리지 않는다."""
    w = World(Clarity((200, EXPORT), (401, {})))
    await w.get()
    attempt = w.stored_pages()["attemptAt"]
    for _ in range(8 * 24):
        w.advance(1)
        body = await w.get()
    stored = w.stored_pages()
    assert stored["values"] is None and stored["successAt"] is None
    assert (stored["attemptAt"], stored["state"]) == (attempt, "ok")
    assert w.clarity.count("pages") == 1 and body["pages"]["groups"] is None
    gone = World(Clarity((200, EXPORT), (401, {})))
    await gone.get()
    for hour in range(8 * 24):
        if hour == 48:
            gone.forget("admin:clarity:pages")
        gone.advance(1)
        await gone.get()
    assert gone.stored_pages() is None and gone.clarity.count("pages") == 1


async def test_week_old_values_leave_redis_on_requests_between_calls_too() -> None:
    """간격 안이라 부르지 않는 요청에서도 — 다음 시도(12시간 뒤)를 기다리지 않는다."""
    w = World(Clarity(pages=((200, PAGES_EXPORT), (500, {}))))
    await w.get()
    w.advance(7 * 24)  # 마지막 성공에서 정확히 7일 — 이번 시도(실패)는 값을 남긴다
    await w.get()
    assert w.stored_pages()["values"] is not None
    w.advance(1)
    body = await w.get()
    assert w.clarity.count("pages") == 2  # 간격 안 — 부르지 않는다
    stored = w.stored_pages()
    assert stored["values"] is None and stored["successAt"] is None
    assert stored["code"] == "http_500" and body["pages"]["groups"] is None
