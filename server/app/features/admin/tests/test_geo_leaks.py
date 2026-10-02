"""DB-IP 가 내지 않는 것 — 응답 바이트와 로그에 조직 이름·AS 번호·IP 주소 글자가 없다 (스펙 039 §3.3·§3.6·§4 '새지 않음')."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    HOUR,
    Feeds,
    line,
    write,
)
from app.features.admin.tests.geo_fakes import FILL_ASN, FILL_ORG, DbIp, inline, url
from app.main import create_app

T0 = AFTER - 2 * HOUR
PLACES = {
    "203.0.113.0": ("KR", 4_199_999_377, "Leakcheck Telecom Org"),
    "198.51.100.0": ("JP", 4_199_999_388, "Leakcheck Hosting Org"),
    "192.0.2.0": ("DE", None, ""),
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def test_response_and_logs_hold_no_org_name_as_number_or_ip(
    client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    # 마지막 마디가 0 이 아닌 IP — /24 로 다시 잘라 찾는다
    lines = [
        line(T0 + i, "/", ua=f"{CHROME} {n} {i}", ip=f"{ip[:-1]}77")
        for n, ip in enumerate(PLACES)
        for i in range(3)
    ]
    lines += [line(T0, "/", ua=f"{CHROME} six", ip="2001:db8:77::")]
    write(tmp_path, "access.log", lines)
    dbip = DbIp().serve("2026-10", PLACES)
    feeds = Feeds(tmp_path, AFTER, geo_transport=dbip.transport, geo_start=inline)
    client.app.state.admin_visits = feeds.feeds
    texts = []
    for step, window in enumerate(("24h", "7d", "30d", "24h")):
        feeds.t = step * 60
        resp = client.get(f"/admin/access?window={window}")
        assert resp.status_code == 200
        texts.append(resp.text)
    assert '"month":"2026-10"' in texts[-1] and '"ipv6":1' in texts[-1]
    assert '"countries":[["DE",0,3],["JP",0,3],["KR",0,3]]' in texts[-1]
    # 실패도 — 다음 달 묶음이 404 면 WARNING 은 부분 이름과 code 뿐
    dbip.files[url("country", "2026-11")] = 404
    feeds.t = datetime(2026, 11, 2, tzinfo=UTC).timestamp() - AFTER
    feeds.feeds.geo.ensure()
    banned = [
        "Leakcheck",
        "4199999377",
        "4199999388",
        FILL_ORG,
        str(FILL_ASN),
        "Quoted",
    ]
    banned += ["203.0.113", "198.51.100", "192.0.2.", "2001:db8"]
    for text in (*texts, caplog.text):
        for word in banned:
            assert word not in text, word
    messages = [r.getMessage() for r in caplog.records if r.name == "marketlens.admin"]
    assert any(m.startswith("DB-IP 2026-10 판 올림") for m in messages)
    assert "관리자 피드 geo 실패 — http_404" in messages
