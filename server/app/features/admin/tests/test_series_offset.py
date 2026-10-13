"""`/admin/aws/series?offset=prev` — 이전 기간: 창을 그 길이만큼 앞으로, 꼴·주기·격자는 그대로, (창, 이전 기간) 마다 캐시
(갱신 주기 두 배), 목록 밖 offset 은 없는 것과 같다 (스펙 068 §3.3·§4 'AWS offset')."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.features.admin.tests.aws_fakes import (
    NOW,
    Clients,
    bounds,
    capture_metric_data,
    dt,
    series_data_response,
    series_ids,
    stub_discovery,
)
from app.features.admin.tests.test_series_feed import KEYS, World
from app.main import create_app


def prev_bounds(window: int, period: int) -> tuple[int, int]:
    """이전 기간 — 끝 = 지금 창의 시작."""
    start, _ = bounds(window, period)
    return start - window, start


@pytest.mark.parametrize(
    ("name", "window", "period", "refresh", "points"),
    [
        ("6h", 21_600, 300, 600, 72),
        ("24h", 86_400, 300, 600, 288),
        ("7d", 604_800, 3_600, 3_600, 168),
        ("30d", 2_592_000, 10_800, 7_200, 240),
    ],
)
async def test_prev_reads_the_window_before_on_the_same_grid(
    name: str, window: int, period: int, refresh: int, points: int
) -> None:
    c = Clients("cloudwatch")
    start, end = prev_bounds(window, period)
    stub_discovery(c)
    c["cloudwatch"].add_response(
        "get_metric_data", series_data_response(series_ids(), start, end, period)
    )
    captured = capture_metric_data(c)
    body = await World(c).feeds.aws_series(name, "prev")
    assert list(body) == KEYS
    assert (body["state"], body["range"], body["offset"]) == ("ok", name, "prev")
    assert (body["refreshSec"], body["periodSec"]) == (refresh, period)
    assert (body["startTs"], body["endTs"]) == (start, end)
    assert end == bounds(window, period)[0] and end % period == 0
    cpu = body["boxes"][1]["series"]["cpu"]
    assert len(cpu) == points and cpu[0][0] == start and cpu[-1][0] == end - period
    assert cpu[0][1] is not None and cpu[1][1] is None  # 격자의 빈 칸은 null
    sent = captured[0]
    assert (sent["StartTime"], sent["EndTime"]) == (dt(start), dt(end))
    assert {q["MetricStat"]["Period"] for q in sent["MetricDataQueries"]} == {period}


async def test_now_and_prev_are_separate_caches_and_prev_keeps_twice_as_long() -> None:
    c = Clients("cloudwatch")
    stub_discovery(c)
    for lo, hi in (bounds(86_400, 300), prev_bounds(86_400, 300)):
        c["cloudwatch"].add_response(
            "get_metric_data", series_data_response(series_ids(), lo, hi, 300)
        )
    captured = capture_metric_data(c)
    w = World(c)
    now, prev = await w.feeds.aws_series("24h"), await w.feeds.aws_series("24h", "prev")
    assert (now["offset"], now["refreshSec"]) == (None, 300)
    assert (prev["offset"], prev["refreshSec"]) == ("prev", 600)
    assert prev["endTs"] == now["startTs"]
    assert len(captured) == 2
    w.advance(300)  # 지금 창만 다시 읽는다
    c["cloudwatch"].add_response("get_metric_data", {"MetricDataResults": []})
    assert (await w.feeds.aws_series("24h", "prev")) == prev
    again = await w.feeds.aws_series("24h")
    assert again["endTs"] == int(NOW + 300) // 300 * 300 and len(captured) == 3
    w.advance(300)  # 600초 — 이전 기간도 다시
    c["cloudwatch"].add_response("get_metric_data", {"MetricDataResults": []})
    later = await w.feeds.aws_series("24h", "prev")
    assert later["endTs"] == int(NOW + 600) // 300 * 300 - 86_400
    assert len(captured) == 4
    c["cloudwatch"].assert_no_pending_responses()


@pytest.mark.parametrize("asked", ["next", "PREV", "", "prev2"])
async def test_an_offset_outside_the_list_is_the_current_window(asked: str) -> None:
    c = Clients("cloudwatch")
    stub_discovery(c)
    c["cloudwatch"].add_response(
        "get_metric_data",
        series_data_response(series_ids(), *bounds(604_800, 3_600), 3_600),
    )
    w = World(c)
    plain = await w.feeds.aws_series("7d")
    body = await w.feeds.aws_series("7d", asked)  # 같은 칸 — 다시 읽지 않는다
    assert body == plain and body["offset"] is None
    assert (body["startTs"], body["endTs"]) == bounds(604_800, 3_600)
    c["cloudwatch"].assert_no_pending_responses()


async def test_no_region_keeps_range_and_offset_and_calls_nothing() -> None:
    made: list[str] = []
    body = await World(lambda s: made.append(s), region=None).feeds.aws_series(
        "30d", "prev"
    )
    assert body == {
        **dict.fromkeys(KEYS),
        "state": "unconfigured",
        "refreshSec": 7_200,
        "range": "30d",
        "offset": "prev",
    }
    assert made == []


@pytest.mark.parametrize(
    ("query", "offset", "refresh"),
    [
        ("?range=7d&offset=prev", "prev", 3_600),
        ("?offset=prev", "prev", 600),
        ("?range=7d&offset=zzz", None, 1_800),
        ("?range=7d&offset=", None, 1_800),
    ],
)
def test_http_series_takes_the_offset_query(
    monkeypatch: pytest.MonkeyPatch, query: str, offset: str | None, refresh: int
) -> None:
    monkeypatch.delenv("ROLE", raising=False)
    monkeypatch.delenv("ADMIN_AWS_REGION", raising=False)
    monkeypatch.setenv("INFLUX_TOKEN", "")
    get_settings.cache_clear()
    try:
        resp = TestClient(create_app()).get(f"/admin/aws/series{query}")
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 200 and list(resp.json()) == KEYS
    assert (resp.json()["offset"], resp.json()["refreshSec"]) == (offset, refresh)
