"""창·게이트 — `window` 고르기·`gateAt`·`startTs`·`hourly` 길이·시계로 열리는 창·읽기 시작점 (스펙 038 §3.1·§3.2·§4)."""

import asyncio
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.core.config import get_settings
from app.features.admin import access_cache
from app.features.admin.tests.access_fakes import (
    AFTER,
    DAY,
    GATE,
    HOUR,
    IPHONE,
    NOW,
    START_TS,
    Feeds,
    line,
    parked,
    rotated,
    watch_opens,
    write,
)
from app.features.admin.visits import VisitFeeds
from app.main import create_app

QUERIES = [
    "",
    "?window=24h",
    "?window=7d",
    "?window=30d",
    "?window=7D",
    "?window=1y",
    "?window=",
]


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    return watch_opens(monkeypatch)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


@pytest.mark.parametrize("now", [NOW, AFTER])
def test_http_window_falls_back_to_24h_unless_exact_and_open(
    client: TestClient, tmp_path: Path, now: float
) -> None:
    write(tmp_path, "access.log", [line(now - 60, "/")])
    client.app.state.admin_visits = Feeds(tmp_path, now).feeds
    for query in QUERIES:
        resp = client.get(f"/admin/access{query}")
        assert resp.status_code == 200, query
        body = resp.json()
        expect = query[8:] if now > GATE and query[8:] in ("7d", "30d") else "24h"
        assert (body["state"], body["window"]) == ("ok", expect), query
        assert body["windows"] == (["24h", "7d", "30d"] if now > GATE else ["24h"])
        assert body["gateAt"] == GATE * 1000


async def test_gate_at_is_midnight_kst_of_the_one_constant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "PRIVACY_V2_EFFECTIVE", "2026-10-12")
    write(tmp_path, "access.log", [line(NOW, "/")])
    # 실제 시계 — 게이트를 지난 날 돌아도 DB-IP 받기는 띄우지 않는다(039)
    feeds = VisitFeeds(access_dir=str(tmp_path), clarity_token=None, geo_start=parked)
    assert (await feeds.access())["gateAt"] == 1_791_730_800_000  # 2026-10-11T15:00Z


@pytest.mark.parametrize(
    ("now", "window", "start", "hours"),
    [
        (NOW, "24h", START_TS, 24),
        (GATE + DAY + 5 * HOUR + 60, "7d", GATE, 30),  # 게이트 이튿날 — 게이트부터
        (GATE + 10 * DAY + 60, "7d", GATE + 10 * DAY - 167 * HOUR, 168),
        (GATE + 40 * DAY + 60, "30d", GATE + 40 * DAY - 719 * HOUR, 720),
    ],
)
async def test_start_and_hourly_length(
    tmp_path: Path, now: float, window: str, start: int, hours: int
) -> None:
    write(tmp_path, "access.log", [line(now - 1, "/")])
    body = await Feeds(tmp_path, now).get(window)
    assert (body["window"], body["startTs"], body["endTs"]) == (window, start, int(now))
    assert len(body["hourly"]) == hours and body["hourly"][0]["ts"] == start
    assert body["hourly"][-1]["ts"] == int(now) - int(now) % HOUR


async def test_the_same_app_opens_three_windows_once_the_clock_passes_the_gate(
    tmp_path: Path,
) -> None:
    write(
        tmp_path, "access.log", [line(GATE - 100, "/"), line(GATE - 50, "/", ua=IPHONE)]
    )
    f = Feeds(tmp_path, GATE - 30)
    before = await f.get("7d")
    assert (before["window"], before["windows"]) == ("24h", ["24h"])
    assert before["visitors"]["code"] == "before_gate"
    f.t = 61
    after = await f.get("7d")
    assert (after["window"], after["windows"], after["startTs"]) == (
        "7d",
        ["24h", "7d", "30d"],
        GATE,
    )
    assert after["visitors"]["state"] == "ok" and after["totals"]["requests"] == 0
    day = await f.get()
    assert day["totals"]["requests"] == 2 and day["visitors"]["sinceTs"] == GATE


async def test_before_the_gate_nothing_outside_24h_is_read_or_counted(
    tmp_path: Path, opened: Counter[str]
) -> None:
    old = rotated(tmp_path, [line(START_TS - 2 * HOUR, "/old-file")])
    write(
        tmp_path,
        "access.log",
        [line(START_TS - 1, "/too-early"), line(START_TS + 1, "/in")],
    )
    body = await Feeds(tmp_path, NOW).get("30d")
    assert body["totals"]["requests"] == 1 and body["paths"] == [["/in", 1]]
    assert old.name not in opened and opened["access.log"] == 1


async def test_after_the_gate_earlier_lines_stay_out_of_7d_and_30d(
    tmp_path: Path,
) -> None:
    now = GATE + 5 * HOUR
    write(
        tmp_path,
        "access.log",
        [line(GATE - 2 * HOUR, "/before"), line(GATE + HOUR, "/after")],
    )
    f = Feeds(tmp_path, now)
    day = await f.get("24h")
    assert day["totals"]["requests"] == 2 and dict(day["paths"]) == {
        "/before": 1,
        "/after": 1,
    }
    for window in ("7d", "30d"):
        body = await f.get(window)
        assert body["totals"]["requests"] == 1 and body["paths"] == [["/after", 1]], (
            window
        )
        assert sum(h["requests"] for h in body["hourly"]) == 1
        assert body["visitors"]["shaped"] == 1 and body["firstTs"] == GATE + HOUR


async def test_window_keys_ride_on_unconfigured_and_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = await Feeds(None, AFTER).get("30d")
    assert (body["state"], body["code"], body["window"]) == (
        "unconfigured",
        "no_file",
        "30d",
    )
    assert body["windows"] == ["24h", "7d", "30d"] and body["gateAt"] == GATE * 1000
    assert body["visitors"] is None and body["totals"] is None
    write(tmp_path, "access.log", [line(AFTER - 5, "/")])
    real = access_cache.open_log

    def slow(path: str) -> Any:
        time.sleep(0.3)
        return real(path)

    monkeypatch.setattr(access_cache, "open_log", slow)
    f = Feeds(tmp_path, AFTER, wait_sec=0.05)
    pending = await f.get("7d")
    assert (pending["state"], pending["window"], pending["gateAt"]) == (
        "pending",
        "7d",
        GATE * 1000,
    )
    assert pending["windows"] == ["24h", "7d", "30d"] and pending["startTs"] is None
    await asyncio.sleep(0.4)  # 뒤에서 마저 돈 채움이 끝난다
    assert (await f.get("7d"))["state"] == "ok"
