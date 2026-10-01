"""수집기 피드 HTTP 계약 — 항상 200 JSON·camelCase·설정 없으면 unconfigured·처리기 예외도 200 (스펙 034 §3.1·§4)."""

from collections.abc import Iterator

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.redis_bus import RedisBus
from app.features.admin.feeds import AdminFeeds
from app.main import create_app


@pytest.fixture
def collector(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("ROLE", raising=False)
    monkeypatch.delenv("ADMIN_AWS_REGION", raising=False)
    monkeypatch.setenv("INFLUX_TOKEN", "")
    get_settings.cache_clear()
    yield TestClient(create_app())  # lifespan 없음 — 수집·AWS 를 띄우지 않는다
    get_settings.cache_clear()


def test_admin_aws_without_region_answers_four_unconfigured_parts(
    collector: TestClient,
) -> None:
    resp = collector.get("/admin/aws")
    assert (
        resp.status_code == 200 and resp.headers["content-type"] == "application/json"
    )
    body = resp.json()
    assert list(body) == ["alarms", "metrics", "canary", "budget"]
    assert {p["state"] for p in body.values()} == {"unconfigured"}
    assert set(body["metrics"]) == {
        "state",
        "code",
        "fetchedAt",
        "refreshSec",
        "endTs",
        "startTs",
        "periodSec",
        "boxes",
        "wsClients",
        "canary",
    }


def test_admin_alerts_reads_the_bus_only_when_this_process_has_a_webhook(
    collector: TestClient,
) -> None:
    app = collector.app
    app.state.spreads_bus = RedisBus(
        fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer())
    )
    body = collector.get("/admin/alerts").json()
    assert body["slack"]["state"] == "unconfigured"  # 웹훅 없음 — 알림기가 없다
    assert body["alarms"]["state"] == "unconfigured"
    assert body["items"] == []
    app.state.notifier = object()  # 알림기가 있는 프로세스
    assert collector.get("/admin/alerts").json()["slack"]["state"] == "ok"


def test_unexpected_failure_inside_a_part_is_still_200(
    collector: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    feeds = AdminFeeds(region="ap-northeast-2", client=lambda s: None, wait_sec=5.0)
    collector.app.state.admin_feeds = feeds

    def boom(self: object, read: object) -> object:
        raise TypeError("arn:aws:iam::123456789012:x")

    monkeypatch.setattr(AdminFeeds, "_run", boom)
    resp = collector.get("/admin/aws")
    assert resp.status_code == 200
    assert {(p["state"], p["code"]) for p in resp.json().values()} == {
        ("error", "TypeError")
    }
    assert "arn:aws:" not in resp.text
