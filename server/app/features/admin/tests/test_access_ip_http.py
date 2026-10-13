"""IP 대역 상세 경로 — band 검사 400(목록의 글자 그대로인 /24·/48 만)·로그 없음·no-store (스펙 067 §3.3·§4)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

BAND = "203.0.113.0/24"
VALUE_KEYS = (
    "partial",
    "country",
    "net",
    "hourly",
    "kinds",
    "status",
    "agents",
    "paths",
    "pairs",
    "recent",
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("ACCESS_LOG_DIR", raising=False)
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "band",
    [
        None,
        "",
        "203.0.113.5/24",
        "203.0.113.0",
        "203.0.113.0/25",
        "(없음)",
        "(기타)",
        "2001:DB8:1::/48",
        "2001:db8:1:0::/48",
        "2001:db8:1::/64",
        "1.2.3.0/24x",
        "9" * 100,
    ],
)
def test_band_that_is_not_a_listed_24_or_48_is_a_400(
    client: TestClient, band: str | None
) -> None:
    params = {} if band is None else {"band": band}
    resp = client.get("/admin/access/ip", params=params)
    assert resp.status_code == 400 and resp.headers["cache-control"] == "no-store"
    assert resp.json()["error"]["code"] == "bad_band"
    assert resp.json()["error"]["detail"] is None


@pytest.mark.parametrize("band", [BAND, "2001:db8:1::/48"])
def test_a_listed_band_without_logs_is_unconfigured(
    client: TestClient, band: str
) -> None:
    resp = client.get("/admin/access/ip", params={"band": band, "window": "1y"})
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    assert resp.json() == {
        "state": "unconfigured",
        "code": "no_file",
        "window": "24h",
        "band": band,
        **dict.fromkeys(VALUE_KEYS),
    }
