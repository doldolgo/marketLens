"""`/admin/alerts` — 보낸 Slack 알림(`alerts:log`) + 경보 이력 7일 타임라인 (스펙 034 §3.3·§4)."""

import json
import re
from typing import Any

import fakeredis

from app.core.redis_bus import RedisBus
from app.features.admin.feeds import AdminFeeds
from app.features.admin.tests.aws_fakes import (
    ARN_TEXT,
    NOW,
    Clients,
    Raising,
    history_item,
)

DAY_MS = 86_400_000
NOW_MS = int(NOW * 1000)
ACCOUNT_ID = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")


def feeds(clients: Any, region: str | None = "ap-northeast-2") -> AdminFeeds:
    return AdminFeeds(
        region=region, client=clients, clock=lambda: NOW, mono=lambda: 0.0, wait_sec=5.0
    )


def log_line(at: int, text: str = "수집 실패", **extra: Any) -> str:
    return json.dumps(
        {
            "at": at,
            "role": "collector",
            "key": "k",
            "text": text,
            "delivered": True,
            **extra,
        },
        ensure_ascii=False,
    )


async def bus_with(*lines: str) -> RedisBus:
    """`alerts:log` 는 왼쪽에 넣는다 — 인자는 오래된 것부터."""
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer()))
    for line in lines:
        await bus.alert_log_push(line)
    return bus


def history(*items: dict) -> Clients:
    c = Clients("cloudwatch")
    c["cloudwatch"].add_response(
        "describe_alarm_history", {"AlarmHistoryItems": list(items)}
    )
    return c


async def test_timeline_merges_both_sources_newest_first_within_seven_days() -> None:
    bus = await bus_with(
        log_line(NOW_MS - 8 * DAY_MS, "7일 밖"),
        "{깨진 줄",
        json.dumps({"at": "어제", "text": "x"}),
        log_line(NOW_MS - 3_000, "수집 실패 " + ARN_TEXT, delivered=False),
        log_line(NOW_MS - 1_000, "기동"),
    )
    c = history(
        history_item("marketlens-canary", NOW - 2, "OK", "ALARM", "사유 " + ARN_TEXT),
        history_item("other-team-alarm", NOW - 1, "OK", "ALARM"),
    )
    body = await feeds(c).alerts(bus=bus, slack_configured=True)
    assert [(i["source"], i["at"]) for i in body["items"]] == [
        ("slack", NOW_MS - 1_000),
        ("alarm", NOW_MS - 2_000),
        ("slack", NOW_MS - 3_000),
    ]
    slack, alarm = body["items"][0], body["items"][1]
    assert slack == {
        "at": NOW_MS - 1_000,
        "source": "slack",
        "text": "기동",
        "role": "collector",
        "key": "k",
        "delivered": True,
        "alarm": None,
        "fromState": None,
        "toState": None,
    }
    assert (alarm["alarm"], alarm["fromState"], alarm["toState"]) == (
        "marketlens-canary",
        "OK",
        "ALARM",
    )
    assert alarm["role"] is None and alarm["key"] is None and alarm["delivered"] is None
    assert body["items"][2]["delivered"] is False
    assert body["slack"] == {
        "state": "ok",
        "code": None,
        "fetchedAt": NOW_MS,
        "refreshSec": 0,
    }
    assert body["alarms"] == {
        "state": "ok",
        "code": None,
        "fetchedAt": NOW_MS,
        "refreshSec": 60,
    }
    raw = json.dumps(body, ensure_ascii=False)
    assert "arn:aws:" not in raw and not ACCOUNT_ID.search(raw)


async def test_timeline_is_capped_at_two_hundred() -> None:
    bus = await bus_with(*(log_line(NOW_MS - 100_000 + i) for i in range(150)))
    items = [
        history_item("marketlens-x", NOW - 200 + i * 0.5, "OK", "ALARM")
        for i in range(100)
    ]
    body = await feeds(history(*items)).alerts(bus=bus, slack_configured=True)
    ats = [i["at"] for i in body["items"]]
    assert len(ats) == 200 and ats == sorted(ats, reverse=True)
    assert {i["source"] for i in body["items"]} == {"slack", "alarm"}


async def test_one_side_failing_keeps_the_other_sides_items() -> None:
    bus = await bus_with(log_line(NOW_MS - 1_000))
    body = await feeds(lambda s: Raising(ConnectionRefusedError())).alerts(
        bus=bus, slack_configured=True
    )
    assert (body["alarms"]["state"], body["alarms"]["code"]) == (
        "error",
        "ConnectionRefusedError",
    )
    assert [i["source"] for i in body["items"]] == ["slack"]

    class DownBus:
        async def alert_log_recent(self, limit: int) -> list[str]:
            raise ConnectionError("down")

    c = history(history_item("marketlens-canary", NOW - 5, "ALARM", "OK"))
    body = await feeds(c).alerts(bus=DownBus(), slack_configured=True)
    assert body["slack"] == {
        "state": "error",
        "code": "redis",
        "fetchedAt": None,
        "refreshSec": 0,
    }
    assert [i["source"] for i in body["items"]] == ["alarm"]


async def test_no_webhook_and_no_region_are_unconfigured_without_calls() -> None:
    made: list[str] = []
    bus = await bus_with(log_line(NOW_MS - 1_000))
    body = await feeds(lambda s: made.append(s), region=None).alerts(
        bus=bus, slack_configured=False
    )
    assert body == {
        "items": [],
        "slack": {
            "state": "unconfigured",
            "code": None,
            "fetchedAt": None,
            "refreshSec": 0,
        },
        "alarms": {
            "state": "unconfigured",
            "code": None,
            "fetchedAt": None,
            "refreshSec": 60,
        },
    }
    assert made == []


async def test_missing_bus_before_lifespan_is_a_redis_error() -> None:
    body = await feeds(None, region=None).alerts(bus=None, slack_configured=True)
    assert (body["slack"]["state"], body["slack"]["code"]) == ("error", "redis")
