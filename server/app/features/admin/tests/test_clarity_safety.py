"""`/admin/clarity` 가 남기지 않는 것 — 주소 쿼리·해시·토큰, Redis 불달·쓰기 실패·키 지우기 (스펙 035 §3.3·§3.5·§4)."""

import json
import logging

import fakeredis
import pytest

from app.features.admin.tests.clarity_fakes import (
    DOC_NAMES,
    EXPORT,
    REAL_EXPORT,
    TOKEN,
    Clarity,
    World,
)


async def test_addresses_lose_query_and_hash_and_referrers_become_origins() -> None:
    w = World(Clarity())
    body = await w.get()
    pages, referrers, titles = body["metrics"]
    assert pages["rows"][:2] == [
        {"url": "https://kimptrack.com/app/", "visits": "40"},
        {"url": "/privacy", "visits": "3"},
    ]
    assert len(pages["rows"]) == 20
    assert referrers["rows"] == [
        {"url": "https://www.google.com:443", "sessions": "7"},
        {"url": "Direct", "sessions": "50"},
    ]
    assert titles["rows"] == [{"title": "T" * 200, "nested": ["/a"]}]
    stored = json.dumps(w.stored())
    for leak in ("gclid", "utm_source", "secret", "user@", "#top", "consent"):
        assert leak not in stored and leak not in json.dumps(body), leak


async def test_token_never_reaches_logs_redis_or_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    w = World(Clarity((200, EXPORT), (401, {"token": TOKEN})))
    first = await w.get()
    w.advance(3)
    second = await w.get()
    raw = fakeredis.FakeRedis(server=w.server).get("admin:clarity").decode()
    for text in (json.dumps(first), json.dumps(second), raw, caplog.text):
        assert TOKEN not in text
    assert [r.levelname for r in caplog.records if r.name == "marketlens.admin"] == [
        "WARNING"
    ]


class DeadRedis:
    async def clarity_load(self) -> str | None:
        raise ConnectionError("redis://10.0.0.5:6379 refused")

    async def clarity_save(self, data: str) -> None:
        raise ConnectionError("down")


async def test_unreachable_redis_means_no_call_and_error_redis(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="marketlens.admin")
    w = World(Clarity())
    body = await w.feeds.clarity(bus=DeadRedis())
    assert (body["state"], body["code"], body["traffic"]) == ("error", "redis", None)
    assert (await w.feeds.clarity(bus=None))["code"] == "redis"  # lifespan 전
    assert w.clarity.requests == []
    assert [r.getMessage() for r in caplog.records] == [
        "관리자 피드 clarity 실패 — redis"
    ]
    assert "10.0.0.5" not in caplog.text


async def test_failed_save_still_blocks_calls_for_three_hours() -> None:
    class SaveFails:
        async def clarity_load(self) -> str | None:
            return None

        async def clarity_save(self, data: str) -> None:
            raise ConnectionError("down")

    w = World(Clarity())
    store = SaveFails()
    assert (await w.feeds.clarity(bus=store))["state"] == "ok"
    w.advance(1)
    body = await w.feeds.clarity(bus=store)
    assert body["state"] == "ok" and len(w.clarity.requests) == 1
    w.advance(2)
    await w.feeds.clarity(bus=store)
    assert len(w.clarity.requests) == 2


async def test_deleting_the_key_calls_on_the_next_request() -> None:
    w = World(Clarity())
    await w.get()
    fakeredis.FakeRedis(server=w.server).delete("admin:clarity")  # 런북 — 바로 부르기
    w.advance(sec=60)
    await w.get()
    assert len(w.clarity.requests) == 2


async def test_every_address_form_loses_query_hash_and_user_info() -> None:
    export = [
        {
            "metricName": "Popular Pages",
            "information": [
                {"url": "HTTPS://kimptrack.com/app/?gclid=SECRET1#top"},
                {"url": "  https://kimptrack.com/?q=SECRET2"},
                {"url": "kimptrack.com/app/?utm_source=SECRET3"},
                {"url": "https://u:SECRET4@kimptrack.com/privacy?x=1"},
                {"https://kimptrack.com/?q=SECRET5": "4"},  # 키에 든 주소
                {"title": "김프란? | KimpTrack", "os": "Android/14?"},  # 주소 꼴이 아님
            ],
        },
        {
            "metricName": "Referrer URL",
            "information": [
                {"url": "android-app://com.google.android.gm/?x=SECRET6"},
                {"url": "www.google.com/search?q=SECRET7"},
            ],
        },
    ]
    w = World(Clarity((200, export)))
    body = await w.get()
    pages, referrers = body["metrics"]
    assert pages["rows"] == [
        {"url": "https://kimptrack.com/app/"},
        {"url": "https://kimptrack.com/"},
        {"url": "kimptrack.com/app/"},
        {"url": "https://kimptrack.com/privacy"},
        {"https://kimptrack.com/": "4"},
        {"title": "김프란? | KimpTrack", "os": "Android/14?"},
    ]
    assert referrers["rows"] == [
        {"url": "android-app://com.google.android.gm"},
        {"url": "www.google.com"},
    ]
    stored = json.dumps(w.stored())
    for leak in ("SECRET", "gclid", "u:", "#top"):
        assert leak not in stored and leak not in json.dumps(body), leak


@pytest.mark.parametrize("spelling", ["real", "documented"])
async def test_named_metrics_match_in_real_camel_case_and_documented_spelling(
    spelling: str,
) -> None:
    """실제 응답(`ReferrerUrl`·`PopularPages`)과 문서 철자(`Referrer URL`·`Popular Pages`)가 같게 다뤄진다 (§3.3)."""
    export = [
        {**m, "metricName": DOC_NAMES.get(m["metricName"], m["metricName"])}
        if spelling == "documented"
        else m
        for m in REAL_EXPORT
    ]
    w = World(Clarity((200, export)))
    body = await w.get()
    assert body["traffic"] == {
        "sessions": 3,
        "botSessions": 0,
        "users": 2,
        "pagesPerSession": 1.5,
    }
    dead, pages, referrers = body["metrics"]
    # 이름은 받은 그대로 싣는다
    assert [dead["name"], pages["name"], referrers["name"]] == [
        m["metricName"] for m in export[1:]
    ]
    assert dead["rows"] == REAL_EXPORT[1]["information"]
    assert pages["rows"] == [
        {"url": "https://kimptrack.com/app/"},
        {"url": "/privacy"},
    ]
    assert referrers["rows"] == [
        {"url": "https://www.google.com:443"},
        {"url": "android-app://com.google.android.gm"},
        {"url": "www.google.com"},
        {"url": "Direct"},
    ]
    stored = json.dumps(w.stored())
    for leak in ("SECRET", "gclid", "u:", "/search", "#top", "consent"):
        assert leak not in stored and leak not in json.dumps(body), leak


NOT_JSON_STANDARD = (
    b'[{"metricName":"Traffic","information":[{"totalSessionCount":"5",'
    b'"PagesPerSessionPercentage":NaN}]},'
    b'{"metricName":"Scroll Depth","information":[{"avg":NaN,"max":Infinity,'
    b'"min":-Infinity,"big":1e400,"n":2.5}]},'
    b'{"metricName":"Page Title","information":[{"title":"Kimp \\ud83d","\\udc00k":"1"}]}]'
)


async def test_non_finite_numbers_become_null_and_lone_surrogates_question_marks() -> (
    None
):
    w = World(Clarity((200, NOT_JSON_STANDARD)))
    body = await w.get()
    assert body["state"] == "ok"
    assert body["traffic"] == {
        "sessions": 5,
        "botSessions": None,
        "users": None,
        "pagesPerSession": None,
    }
    depth, title = body["metrics"]
    assert depth["rows"] == [
        {"avg": None, "max": None, "min": None, "big": None, "n": 2.5}
    ]
    assert title["rows"] == [{"title": "Kimp ?", "?k": "1"}]
    raw = fakeredis.FakeRedis(server=w.server).get("admin:clarity")
    assert b"NaN" not in raw and b"Infinity" not in raw
    json.loads(raw, parse_constant=lambda c: pytest.fail(c))  # 표준 JSON 으로 저장


@pytest.mark.parametrize(
    "junk",
    [["a", "b"], [{"metricName": "Traffic", "information": "notalist"}], [{}]],
)
async def test_a_list_without_any_metric_is_error_and_keeps_the_last_success(
    junk: list,
) -> None:
    w = World(Clarity((200, EXPORT), (200, junk), (200, [])))
    ok = await w.get()
    w.advance(3)
    body = await w.get()
    assert (body["state"], body["code"]) == ("error", "ValueError")
    assert body["fetchedAt"] == ok["fetchedAt"] and body["traffic"] == ok["traffic"]
    assert w.stored()["values"]["traffic"] == ok["traffic"]
    w.advance(3)
    empty = await w.get()  # 빈 목록 = 자료 없음 — 성공이다
    assert (empty["state"], empty["traffic"], empty["metrics"]) == ("ok", None, [])
