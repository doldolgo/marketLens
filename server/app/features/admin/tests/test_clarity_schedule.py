"""`/admin/clarity` 네 호출의 일정·기록·하루 한도 (스펙 040 §3.2·§3.5·068 §3.1·§4 '일정').

기본 요약 6시간·페이지×기기 묶음 12시간·기기·유입 차원 24시간씩, 한 갱신 안에서 기본 → 묶음 → 기기 → 유입. 가짜 Data
Export 가 부른 시각(World 시계)·종류를 적고, Redis 는 fakeredis(읽기·쓰기를 따로 깨뜨릴 수 있다)다.
"""

import random

import pytest

from app.features.admin.tests.clarity_fakes import (
    HOUR_MS,
    NOW,
    TOKEN,
    Clarity,
    World,
)

DAY = 24 * 3600
KINDS = ["base", "pages", "device", "channel"]
GAP = {"base": 21_600, "pages": 43_200, "device": DAY, "channel": DAY}
KEYS = ("admin:clarity", "admin:clarity:pages", "admin:clarity:dims")


def most_in_a_day(times: list[float]) -> int:
    """어떤 24시간 창 [t, t+24h) 에 든 호출 수의 최댓값."""
    return max((sum(1 for u in times if t <= u < t + DAY) for t in times), default=0)


def times(clarity: Clarity, kind: str | None = None) -> list[float]:
    return [t for t, k in clarity.calls if kind is None or k == kind]


def kinds(clarity: Clarity) -> list[str]:
    return [k for _, k in clarity.calls]


async def test_first_request_calls_the_four_one_at_a_time() -> None:
    w = World(Clarity())
    body = await w.get()
    base, pages, device, channel = w.clarity.requests
    assert [Clarity.kind(r) for r in w.clarity.requests] == KINDS
    assert dict(base.url.params) == {"numOfDays": "1"}
    assert dict(pages.url.params) == {
        "numOfDays": "3",
        "dimension1": "URL",
        "dimension2": "Device",
    }
    assert dict(device.url.params) == {
        "numOfDays": "1",
        "dimension1": "Device",
        "dimension2": "Browser",
        "dimension3": "OS",
    }
    assert dict(channel.url.params) == {
        "numOfDays": "1",
        "dimension1": "Channel",
        "dimension2": "Source",
        "dimension3": "Country/Region",
    }
    for req in w.clarity.requests:
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert w.clarity.max_active == 1  # 동시에 둘 나가지 않는다
    now_ms = int(NOW * 1000)
    assert (body["refreshSec"], body["nextAt"]) == (21_600, now_ms + 6 * HOUR_MS)
    assert (body["pages"]["refreshSec"], body["pages"]["nextAt"]) == (
        43_200,
        now_ms + 12 * HOUR_MS,
    )
    assert (body["dims"]["state"], body["dims"]["refreshSec"]) == ("ok", 86_400)
    assert w.stored()["attemptAt"] == w.stored_pages()["attemptAt"] == now_ms
    assert w.stored_dims("device")["attemptAt"] == now_ms
    assert w.stored_dims("channel")["attemptAt"] == now_ms


async def test_gaps_hold_and_a_restarted_app_keeps_all_four() -> None:
    w = World(Clarity())
    await w.get()
    w.advance(sec=6 * 3600 - 1)
    await w.get()
    assert len(w.clarity.calls) == 4  # 6시간 안 — 0회
    w.advance(sec=1)
    await w.get()
    assert kinds(w.clarity) == KINDS + ["base"]  # 6시간 — 기본만
    restarted = w.new_app()  # 배포 — 메모리는 비었고 Redis 기록만 있다
    w.advance(6)
    await w.get(restarted)
    assert kinds(w.clarity)[-2:] == ["base", "pages"]  # 12시간 — 기본 → 묶음
    w.advance(6)
    await w.get(restarted)
    w.advance(sec=6 * 3600 - 1)  # 24시간 − 1초 — 차원 둘은 아직
    await w.get(restarted)
    assert kinds(w.clarity)[4:] == ["base", "base", "pages", "base"]
    w.advance(sec=1)  # 24시간 — 넷 다, 또 새로 뜬 앱
    await w.get(w.new_app())
    assert kinds(w.clarity)[-4:] == KINDS
    assert w.clarity.max_active == 1


async def test_seventy_two_hours_of_minute_requests_stay_at_eight_a_day() -> None:
    """어떤 24시간에도 기본 4 + 묶음 2 + 기기 1 + 유입 1 = 8회 — 하루 10회 한도에서 사람 몫 2회가 남는다(068 §3.1)."""
    w = World(Clarity())
    for _ in range(72 * 60):
        await w.get()
        w.advance(sec=60)
    assert [most_in_a_day(times(w.clarity, k)) for k in KINDS] == [4, 2, 1, 1]
    assert most_in_a_day(times(w.clarity)) == 8
    # 6·12·24·24시간마다 빠짐없이
    assert [len(times(w.clarity, k)) for k in KINDS] == [12, 6, 3, 3]


def next_due(w: World) -> float:
    """Redis 기록으로 본 가장 이른 다음 때(World 초) — 기록이 없으면 지금."""
    records = {
        "base": w.stored(),
        "pages": w.stored_pages(),
        "device": w.stored_dims("device"),
        "channel": w.stored_dims("channel"),
    }
    due = [r["attemptAt"] / 1000 - NOW + GAP[k] for k, r in records.items() if r]
    return min(due, default=w.t)


@pytest.mark.parametrize("seed", range(5))
async def test_random_gaps_slow_calls_and_deletes_never_pass_eight_or_ten(
    seed: int,
) -> None:
    """30일 동안 무작위 요청 — 절반은 가장 이른 다음 때(+0·1·60초)에 맞춰 오고 나머지는 간격 경계(± 1초)를 노린다, 호출은
    0~10초 걸린다 — 어떤 24시간에도 8회. 같은 요청에 키를 무작위로 지우면(사람의 바로 부르기, 다시 뜨지 않는 한
    프로세스) 어떤 24시간에도 10회."""
    rng = random.Random(seed)
    gaps = (1, 30, 60, 600, 3_599, 3_600, 21_599, 21_600, 21_601, 43_201)
    for deletes, limit in ((False, 8), (True, 10)):
        w = World(Clarity())
        while w.t < 30 * DAY:
            for kind in KINDS:
                w.clarity.takes[kind] = rng.choice((0, 0, 1, 5, 10))
            if deletes and rng.random() < 0.2:
                w.forget(*rng.sample(KEYS, rng.randint(1, len(KEYS))))
            await w.get()
            if rng.random() < 0.5:
                w.t = max(w.t + 1, next_due(w) + rng.choice((0, 0, 1, 60)))
            else:
                w.advance(sec=rng.choice(gaps))
        assert most_in_a_day(times(w.clarity)) <= limit, (seed, deletes)
        assert w.clarity.max_active == 1


async def test_attempts_are_timed_when_sent_so_slow_calls_never_fit_more_a_day() -> (
    None
):
    """시도 시각 = 보낸 때(040 §3.2). 첫 기본이 10초 걸리면 나머지 셋은 10초에 나간다 — 갱신 시작을 적으면 요청이
    정확히 6시간마다 올 때 다음 묶음·차원이 보낸 때로부터 간격보다 10초 일찍 나가 어떤 24시간에 하나가 더 든다."""
    w = World(Clarity())
    w.clarity.takes["base"] = 10
    await w.get()
    w.clarity.takes["base"] = 0
    assert w.stored()["attemptAt"] == int(NOW * 1000)
    for stored in (w.stored_pages(), w.stored_dims("device"), w.stored_dims("channel")):
        assert stored["attemptAt"] == int((NOW + 10) * 1000)
    for k in range(1, 13):  # 72시간 — 기본의 때에 맞춰 온다
        w.t = k * 6 * 3600
        await w.get()
    for kind, gap in (("pages", 12), ("device", 24), ("channel", 24)):
        sent = times(w.clarity, kind)
        assert min(b - a for a, b in zip(sent, sent[1:], strict=False)) >= gap * 3600
    assert most_in_a_day(times(w.clarity)) <= 8


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


async def test_deleting_every_key_every_hour_never_passes_ten_a_day() -> None:
    """키 셋을 매시간 지워도 — 바로 부르기는 종류마다 24시간에 한 번, 프로세스 전체로 24시간에 두 번(사람 몫)이라
    어떤 24시간에도 8 + 2 = 10회를 넘지 않는다(068 §3.1)."""
    w = World(Clarity())
    await w.get()
    w.advance(1)
    w.forget(*KEYS)
    await w.get()
    # 지운 뒤 첫 요청 — 기본·묶음이 사람 몫 두 번을 쓰고, 차원 둘은 기록을 되살려 간격을 따른다
    assert kinds(w.clarity)[4:] == ["base", "pages"]
    assert w.stored_dims("device")["attemptAt"] == int(NOW * 1000)
    w.advance(1)
    w.forget(*KEYS)
    await w.get()
    assert (
        len(w.clarity.calls) == 6
    )  # 24시간 안 두 번째 지움 — 부르지 않고 기록을 되살린다
    assert w.stored()["attemptAt"] == int((NOW + 3600) * 1000)
    assert w.stored_pages() is not None and w.stored_dims("channel") is not None
    for minute in range(70 * 60):
        if minute % 60 == 0:
            w.forget(*KEYS)
        await w.get()
        w.advance(sec=60)
    assert most_in_a_day(times(w.clarity)) <= 10
    for kind, most in zip(KINDS, (5, 3, 2, 2), strict=True):
        assert most_in_a_day(times(w.clarity, kind)) <= most, kind


async def test_deleting_the_dims_key_calls_both_dims_and_spends_the_two_human_calls() -> (
    None
):
    w = World(Clarity())
    await w.get()
    w.advance(1)
    w.forget("admin:clarity:dims")
    await w.get()
    assert kinds(w.clarity)[4:] == ["device", "channel"]  # 둘 다 곧바로 — 사람 몫 2회
    w.advance(1)
    w.forget("admin:clarity")  # 그 24시간 안 — 사람 몫이 남지 않았다
    await w.get()
    assert len(w.clarity.calls) == 6
    assert w.stored()["attemptAt"] == int(NOW * 1000)  # 기록을 되살려 간격을 따른다
    w.t = 6 * 3600  # 되살린 기록의 때 — 기본만
    await w.get()
    assert kinds(w.clarity)[6:] == ["base"]


async def test_failed_writes_never_call_again_within_the_gap_and_heal_later() -> None:
    w = World(Clarity())
    w.broken = {"write"}  # 빈 Redis 에서 첫 쓰기부터 실패
    first = await w.get()
    assert first["state"] == "ok" and len(w.clarity.calls) == 4
    w.advance(sec=60)
    second = await w.get()
    assert len(w.clarity.calls) == 4  # 기록은 메모리에 — 다시 부르지 않는다
    assert second["nextAt"] == first["nextAt"]
    assert w.stored() is None and w.stored_pages() is None
    assert w.stored_dims("device") is None
    w.broken = set()
    w.advance(sec=60)
    await w.get()
    assert len(w.clarity.calls) == 4
    now_ms = int(NOW * 1000)
    assert w.stored()["attemptAt"] == w.stored_pages()["attemptAt"] == now_ms
    assert w.stored_dims("device")["attemptAt"] == now_ms
    assert w.stored_dims("channel")["attemptAt"] == now_ms


async def test_unreadable_redis_calls_nothing_and_every_part_says_redis() -> None:
    w = World(Clarity())
    ok = await w.get()
    w.advance(25)
    w.broken = {"read"}
    body = await w.get()
    # 간격이 지났어도 시도 시각을 모르면 부르지 않는다
    assert len(w.clarity.calls) == 4
    for part in (body, body["pages"], body["dims"]):
        assert (part["state"], part["code"]) == ("error", "redis")
    assert body["traffic"] == ok["traffic"]  # 값은 메모리의 마지막 기록
    assert body["pages"]["groups"] == ok["pages"]["groups"]
    assert body["dims"]["device"] == ok["dims"]["device"]


async def test_lost_records_and_a_new_app_call_all_four_at_once() -> None:
    """남는 위험(040 §3.2) — 기록을 잃은 Redis 에 새로 뜬 api 는 메모리도 비어 첫 설치처럼 넷 다 곧바로 부른다."""
    w = World(Clarity())
    await w.get()
    w.advance(1)
    w.forget(*KEYS)
    await w.get(w.new_app())
    assert kinds(w.clarity) == KINDS + KINDS
