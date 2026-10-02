"""게이트 — 처리방침 v2 시행일 전에는 DB-IP 를 받지도 찾지도 않고, 뒤 첫 요청은 `pending` 뒤 `ok` (스펙 039 §3.1·§3.3·§3.6·§4 '게이트')."""

from pathlib import Path
from typing import Any

import pytest

from app.core import config
from app.features.admin import access_hours
from app.features.admin.tests.access_fakes import (
    AFTER,
    GATE,
    NOW,
    Feeds,
    line,
    write,
)
from app.features.admin.tests.geo_fakes import MONTH, DbIp, Jobs, inline
from app.features.admin.visits import VisitFeeds

BEFORE_GATE = {
    "state": "unconfigured",
    "code": "before_gate",
    **dict.fromkeys("month loadedAt sinceTs countries networks ipv6".split()),
}


@pytest.fixture
def looked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """짝 기록이 나라·망 종류를 찾은 IP 들."""
    calls: list[str] = []
    real = access_hours.locate

    def watched(table: Any, ip: str) -> tuple[str | None, str]:
        calls.append(ip)
        return real(table, ip)

    monkeypatch.setattr(access_hours, "locate", watched)
    return calls


@pytest.mark.parametrize("window", ["24h", "7d", "30d"])
async def test_before_the_gate_nothing_is_fetched_or_looked_up(
    tmp_path: Path, looked: list[str], window: str
) -> None:
    write(tmp_path, "access.log", [line(NOW - 60, "/"), line(NOW - 50, "/clarity.js")])
    dbip, jobs = DbIp().serve(), Jobs()
    f = Feeds(tmp_path, NOW, geo_transport=dbip.transport, geo_start=jobs)
    for step in range(3):
        f.t = step * 60
        body = await f.get(window)
        assert (body["state"], body["window"]) == ("ok", "24h")
        assert body["geo"] == BEFORE_GATE
        assert body["visitors"]["code"] == "before_gate"
    assert jobs.jobs == [] and dbip.requests == [] and looked == []
    assert f.feeds.geo.table is None


async def test_gate_at_follows_the_one_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "access.log", [line(NOW, "/")])
    expected = 1_791_730_800_000  # 2026-10-11T15:00Z = 2026-10-12 00:00 KST
    named = VisitFeeds(
        access_dir=str(tmp_path),
        clarity_token=None,
        clock=lambda: NOW,
        privacy_effective="2026-10-12",
        geo_start=Jobs(),
    )
    assert (await named.access())["gateAt"] == expected
    monkeypatch.setattr(config, "PRIVACY_V2_EFFECTIVE", "2026-10-12")
    default = VisitFeeds(
        access_dir=str(tmp_path),
        clarity_token=None,
        clock=lambda: NOW,
        geo_start=Jobs(),
    )
    body = await default.access()
    assert (body["gateAt"], body["geo"]) == (expected, BEFORE_GATE)


async def test_the_first_request_after_the_gate_is_pending_then_ok(
    tmp_path: Path, looked: list[str]
) -> None:
    write(
        tmp_path, "access.log", [line(AFTER - 60, "/"), line(AFTER - 50, "/clarity.js")]
    )
    dbip, jobs = DbIp().serve(), Jobs()
    f = Feeds(tmp_path, AFTER, geo_transport=dbip.transport, geo_start=jobs)
    first = await f.get("7d")
    assert (first["state"], first["visitors"]["state"]) == ("ok", "ok")
    assert first["geo"] == {**BEFORE_GATE, "state": "pending", "code": None}
    assert len(jobs.jobs) == 1 and looked == []  # 판이 없을 때 파싱한 짝은 찾지 않는다
    jobs.run()
    f.t = 60
    geo = (await f.get("7d"))["geo"]
    assert (geo["state"], geo["code"], geo["month"]) == ("ok", None, MONTH)
    assert looked == ["203.0.113.0"]  # 판이 바뀌어 다시 읽은 짝


async def test_crossing_the_gate_starts_the_fetch_on_the_next_refresh(
    tmp_path: Path,
) -> None:
    write(tmp_path, "access.log", [line(GATE - 90, "/"), line(GATE + 10, "/")])
    dbip = DbIp().serve()
    f = Feeds(tmp_path, GATE - 30, geo_transport=dbip.transport, geo_start=inline)
    assert (await f.get())["geo"]["code"] == "before_gate" and dbip.requests == []
    f.t = 60  # 게이트 30초 뒤
    assert (await f.get())["geo"]["state"] == "pending"
    assert (dbip.count("country"), dbip.count("asn")) == (1, 1)
    f.t = 120
    geo = (await f.get())["geo"]
    assert (geo["state"], geo["sinceTs"]) == ("ok", GATE)
    # 한 명 — KR 이 이름으로 남지 않아 국내 통신사 칸도 통신사 칸에 들고, 그 칸도 하루 1 이라 (기타)
    assert (geo["countries"], geo["networks"]) == (
        [["(기타)", 0, 1]],
        [["(기타)", 0, 1]],
    )
