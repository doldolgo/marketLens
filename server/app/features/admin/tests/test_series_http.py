"""`GET /admin/aws/series` HTTP 계약 — 항상 200 JSON·부분 하나·창 고르기·설정 없으면 unconfigured·처리기 예외도 200
(스펙 063 §3.1·§3.3·§4). 역할(api 404·OpenAPI)은 tests/test_role.py, 관리자·공개 nginx 는 tests/test_admin.py."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.features.admin.feeds import AdminFeeds
from app.main import create_app

KEYS = [
    "state",
    "code",
    "fetchedAt",
    "refreshSec",
    "range",
    "periodSec",
    "startTs",
    "endTs",
    "boxes",
    "wsClients",
    "canary",
]


@pytest.fixture
def collector(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("ROLE", raising=False)
    monkeypatch.delenv("ADMIN_AWS_REGION", raising=False)
    monkeypatch.setenv("INFLUX_TOKEN", "")
    get_settings.cache_clear()
    yield TestClient(create_app())  # lifespan 없음 — 수집·AWS 를 띄우지 않는다
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("query", "name", "refresh"),
    [
        ("", "24h", 300),
        ("?range=6h", "6h", 300),
        ("?range=24h", "24h", 300),
        ("?range=7d", "7d", 1_800),
        ("?range=30d", "30d", 3_600),
        ("?range=1y", "24h", 300),  # 목록 밖은 24h
        ("?range=", "24h", 300),
    ],
)
def test_series_without_region_answers_one_unconfigured_part_for_the_chosen_range(
    collector: TestClient, query: str, name: str, refresh: int
) -> None:
    resp = collector.get(f"/admin/aws/series{query}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert list(resp.json()) == KEYS
    assert resp.json() == {
        **dict.fromkeys(KEYS),
        "state": "unconfigured",
        "refreshSec": refresh,
        "range": name,
    }


def test_unexpected_failure_inside_the_series_is_still_200(
    collector: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    feeds = AdminFeeds(region="ap-northeast-2", client=lambda s: None, wait_sec=5.0)
    collector.app.state.admin_feeds = feeds

    def boom(self: object, read: object) -> object:
        raise TypeError("arn:aws:iam::123456789012:x")

    monkeypatch.setattr(AdminFeeds, "_run", boom)
    resp = collector.get("/admin/aws/series?range=30d")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["state"], body["code"], body["range"]) == ("error", "TypeError", "30d")
    assert "arn:aws:" not in resp.text and "123456789012" not in resp.text
