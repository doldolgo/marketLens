"""`/admin/clarity` 두 호출의 일정·기록·하루 한도 (스펙 040 §3.2·§3.5·§3.7·§4 '일정·한도').

기본 요약 4시간·페이지×기기 묶음 12시간, 한 갱신 안에서 기본 → 묶음. 가짜 Data Export 가 부른 시각(World 시계)·종류를
적고, Redis 는 fakeredis(읽기·쓰기를 따로 깨뜨릴 수 있다)다.
"""

from app.features.admin.tests.clarity_fakes import (
    HOUR_MS,
    NOW,
    TOKEN,
    Clarity,
    World,
)

DAY = 24 * 3600


def most_in_a_day(times: list[float]) -> int:
    """어떤 24시간 창 [t, t+24h) 에 든 호출 수의 최댓값."""
    return max((sum(1 for u in times if t <= u < t + DAY) for t in times), default=0)


def times(clarity: Clarity, kind: str) -> list[float]:
    return [t for t, k in clarity.calls if k == kind]


async def test_first_request_calls_the_summary_then_the_pages_one_at_a_time() -> None:
    w = World(Clarity())
    body = await w.get()
    base, pages = w.clarity.requests
    assert [Clarity.kind(r) for r in (base, pages)] == ["base", "pages"]
    assert dict(base.url.params) == {"numOfDays": "1"}
    assert dict(pages.url.params) == {
        "numOfDays": "3",
        "dimension1": "URL",
        "dimension2": "Device",
    }
    for req in (base, pages):
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert w.clarity.max_active == 1  # 동시에 둘 나가지 않는다
    now_ms = int(NOW * 1000)
    assert (body["refreshSec"], body["nextAt"]) == (14_400, now_ms + 4 * HOUR_MS)
    assert body["pages"]["state"] == "ok"
    assert (body["pages"]["refreshSec"], body["pages"]["nextAt"]) == (
        43_200,
        now_ms + 12 * HOUR_MS,
    )
    assert body["pages"]["numOfDays"] == 3
    assert w.stored()["attemptAt"] == w.stored_pages()["attemptAt"] == now_ms


async def test_gaps_hold_and_a_restarted_app_keeps_both() -> None:
    w = World(Clarity())
    await w.get()
    w.advance(sec=4 * 3600 - 1)
    await w.get()
    assert len(w.clarity.calls) == 2  # 4시간 안 — 0회
    w.advance(sec=1)
    await w.get()
    assert [k for _, k in w.clarity.calls] == [
        "base",
        "pages",
        "base",
    ]  # 4시간 — 기본만
    restarted = w.new_app()  # 배포 — 메모리는 비었고 Redis 기록만 있다
    w.advance(4)
    await w.get(restarted)
    w.advance(sec=4 * 3600 - 1)  # 12시간 − 1초
    await w.get(restarted)
    assert [k for _, k in w.clarity.calls] == ["base", "pages", "base", "base"]
    w.advance(sec=1)  # 12시간 — 기본 → 묶음
    await w.get(w.new_app())
    assert [k for _, k in w.clarity.calls][-2:] == ["base", "pages"]
    assert w.clarity.max_active == 1


async def test_seventy_two_hours_of_minute_requests_stay_within_the_daily_split() -> (
    None
):
    w = World(Clarity())
    for _ in range(72 * 60):
        await w.get()
        w.advance(sec=60)
    base, pages = times(w.clarity, "base"), times(w.clarity, "pages")
    assert most_in_a_day(base) <= 6 and most_in_a_day(pages) <= 2
    assert len(base) == 18 and len(pages) == 6  # 4시간·12시간마다 빠짐없이


async def test_attempts_are_timed_when_sent_so_a_slow_summary_never_fits_three_pages_a_day() -> (
    None
):
    """시도 시각 = 보낸 때(§3.2). 첫 기본이 10초 걸리면 묶음은 10초에 나간다 — 갱신 시작을 적으면 요청이
    정확히 4시간마다 올 때 다음 묶음이 보낸 때로부터 12시간보다 10초 일찍 나가 어떤 24시간에 3회가 든다."""
    w = World(Clarity())
    w.clarity.takes["base"] = 10
    await w.get()
    w.clarity.takes["base"] = 0
    assert w.stored()["attemptAt"] == int(NOW * 1000)
    assert w.stored_pages()["attemptAt"] == int((NOW + 10) * 1000)
    for k in range(1, 19):  # 72시간 — 기본의 때에 맞춰 온다
        w.t = k * 4 * 3600
        await w.get()
    base, pages = times(w.clarity, "base"), times(w.clarity, "pages")
    assert min(b - a for a, b in zip(pages, pages[1:], strict=False)) >= 12 * 3600
    assert most_in_a_day(pages) <= 2 and most_in_a_day(base + pages) <= 8


async def test_a_direct_call_is_timed_when_sent_too() -> None:
    """바로 부르기의 24시간도 보낸 때부터 — 첫 바로 부르기의 묶음이 10초 늦게 나갔으면 24시간 뒤 갱신 시작에는
    아직 바로 부르지 않는다."""
    w = World(Clarity())
    await w.get()
    w.advance(1)
    w.forget("admin:clarity", "admin:clarity:pages")
    w.clarity.takes["base"] = 10
    await w.get()  # 둘 다 바로 부르기 — 묶음은 1시간 10초에 나간다
    w.clarity.takes["base"] = 0
    w.t = 3600 + 12 * 3600 + 10  # 묶음의 때 — 기본 → 묶음
    await w.get()
    assert times(w.clarity, "pages") == [0, 3610, 46810]
    w.t = 3600 + 24 * 3600  # 바로 부른 묶음을 보낸 때로부터 24시간 − 10초
    w.forget("admin:clarity:pages")
    await w.get()
    assert times(w.clarity, "pages") == [0, 3610, 46810]  # 부르지 않고 기록을 되살린다
    assert w.stored_pages()["attemptAt"] == int((NOW + 46810) * 1000)


async def test_deleting_both_keys_every_hour_never_passes_ten_a_day() -> None:
    w = World(Clarity())
    await w.get()
    w.advance(1)
    w.forget("admin:clarity", "admin:clarity:pages")
    await w.get()
    assert len(w.clarity.calls) == 4  # 지운 뒤 첫 요청 — 둘 다 곧바로
    w.advance(1)
    w.forget("admin:clarity", "admin:clarity:pages")
    await w.get()
    assert (
        len(w.clarity.calls) == 4
    )  # 24시간 안 두 번째 지움 — 부르지 않고 기록을 되살린다
    assert w.stored() is not None and w.stored_pages() is not None
    assert w.stored()["attemptAt"] == int((NOW + 3600) * 1000)
    for minute in range(70 * 60):
        if minute % 60 == 0:
            w.forget("admin:clarity", "admin:clarity:pages")
        await w.get()
        w.advance(sec=60)
    base, pages = times(w.clarity, "base"), times(w.clarity, "pages")
    assert most_in_a_day(base) <= 7 and most_in_a_day(pages) <= 3
    assert most_in_a_day(base + pages) <= 10


async def test_failed_writes_never_call_again_within_the_gap_and_heal_later() -> None:
    w = World(Clarity())
    w.broken = {"write"}  # 빈 Redis 에서 첫 쓰기부터 실패
    first = await w.get()
    assert first["state"] == "ok" and len(w.clarity.calls) == 2
    w.advance(sec=60)
    second = await w.get()
    assert len(w.clarity.calls) == 2  # 기록은 메모리에 — 다시 부르지 않는다
    assert second["nextAt"] == first["nextAt"]
    assert w.stored() is None and w.stored_pages() is None
    w.broken = set()
    w.advance(sec=60)
    await w.get()
    assert len(w.clarity.calls) == 2
    assert w.stored()["attemptAt"] == w.stored_pages()["attemptAt"] == int(NOW * 1000)


async def test_unreadable_redis_calls_nothing_and_both_parts_say_redis() -> None:
    w = World(Clarity())
    ok = await w.get()
    w.advance(5)
    w.broken = {"read"}
    body = await w.get()
    assert (
        len(w.clarity.calls) == 2
    )  # 4시간이 지났어도 시도 시각을 모르면 부르지 않는다
    assert (body["state"], body["code"]) == ("error", "redis")
    assert (body["pages"]["state"], body["pages"]["code"]) == ("error", "redis")
    assert body["traffic"] == ok["traffic"]  # 값은 메모리의 마지막 기록
    assert body["pages"]["groups"] == ok["pages"]["groups"]


async def test_lost_records_and_a_new_app_call_both_at_once() -> None:
    """남는 위험(§3.2) — 기록을 잃은 Redis 에 새로 뜬 api 는 메모리도 비어 첫 설치처럼 둘 다 곧바로 부른다."""
    w = World(Clarity())
    await w.get()
    w.advance(1)
    w.forget("admin:clarity", "admin:clarity:pages")
    await w.get(w.new_app())
    assert [k for _, k in w.clarity.calls] == ["base", "pages", "base", "pages"]
