"""api 피드 HTTP·공통 규칙 — 항상 200·부분 하나·unconfigured·60초 캐시·처리기 예외·JSON 으로 못 쓰는 값도 200·WARNING 만 (스펙 035 §3.1·§3.2·§4)."""

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.redis_bus import RedisBus
from app.features.admin import visits
from app.features.admin.access import VALUE_KEYS
from app.features.admin.tests.access_fakes import IP, IPHONE, NOW, at, line, write
from app.features.admin.visits import VisitFeeds
from app.main import create_app

TOKEN = "test-clarity-token-http-1a2b"
PART_KEYS = ["state", "code", "fetchedAt", "refreshSec"]


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def feeds(self, directory: str | None) -> VisitFeeds:
        return VisitFeeds(
            access_dir=directory,
            clarity_token=None,
            clock=lambda: NOW + self.t,
            mono=lambda: self.t,
        )


@pytest.mark.parametrize("directory", [None, "missing"])
async def test_access_without_log_files_is_unconfigured_no_file(
    tmp_path: Path, directory: str | None
) -> None:
    target = None if directory is None else str(tmp_path / directory)
    body = await Clock().feeds(target).access()
    assert list(body) == PART_KEYS + list(VALUE_KEYS)
    assert body == {
        "state": "unconfigured",
        "code": "no_file",
        "fetchedAt": None,
        "refreshSec": 60,
        **dict.fromkeys(VALUE_KEYS),
    }


async def test_access_is_cached_for_its_60_second_period(tmp_path: Path) -> None:
    clock = Clock()
    feeds = clock.feeds(str(tmp_path))
    write(tmp_path, "access.log", [line(at(1))])
    body = await feeds.access()
    assert (body["state"], body["code"], body["fetchedAt"]) == (
        "ok",
        None,
        int(NOW * 1000),
    )
    assert body["totals"]["pages"] == 1
    write(tmp_path, "access.log", [line(at(1)), line(at(2))])
    clock.t = 59
    assert (await feeds.access())["totals"][
        "pages"
    ] == 1  # 주기 안 — 파일을 다시 읽지 않는다
    clock.t = 60
    assert (await feeds.access())["totals"]["pages"] == 2


async def test_read_failure_is_error_with_one_warning_and_no_error_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def denied(directory: str | None, now: float, *_: object) -> dict:
        raise PermissionError("/var/log/caddy/access.log 203.0.113.0")

    monkeypatch.setattr(visits, "summarize", denied)
    caplog.set_level(logging.INFO)
    clock = Clock()
    feeds = clock.feeds(str(tmp_path))
    body = await feeds.access()
    assert (body["state"], body["code"], body["totals"]) == (
        "error",
        "PermissionError",
        None,
    )
    clock.t = 61  # 주기가 지나 다시 읽어도 10분 안이라 줄은 하나
    assert (await feeds.access())["state"] == "error"
    records = [r for r in caplog.records if r.name == "marketlens.admin"]
    assert [(r.levelname, r.getMessage()) for r in records] == [
        ("WARNING", "관리자 피드 access 실패 — PermissionError")
    ]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "203.0.113.0" not in caplog.text


async def test_broken_rotated_file_keeps_the_part_ok_with_one_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "access-2026-10-01T05-00-00.000-size.log.gz"
    bad.write_bytes(b"\x1f\x8b\x08\x00")  # 머리만 있는 잘린 gz
    os.utime(bad, (at(5), at(5)))
    write(tmp_path, "access.log", [line(at(6))])
    caplog.set_level(logging.INFO)
    clock = Clock()
    feeds = clock.feeds(str(tmp_path))
    body = await feeds.access()
    assert (body["state"], body["code"]) == ("ok", None)
    assert body["totals"] == {"requests": 1, "pages": 1, "ws": 0, "skipped": 1}
    clock.t = 61  # 다음 회차도 같은 파일 — 10분 안이라 줄은 하나
    assert (await feeds.access())["state"] == "ok"
    records = [r for r in caplog.records if r.name == "marketlens.admin"]
    assert [(r.levelname, r.getMessage()) for r in records] == [
        ("WARNING", "관리자 피드 access 실패 — EOFError")
    ]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.setenv("ACCESS_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    app = create_app()  # lifespan 없음 — Redis 자리만 fakeredis 로 채운다
    app.state.spreads_bus = RedisBus(
        fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer())
    )
    yield TestClient(app)
    get_settings.cache_clear()


def test_http_access_reads_the_env_directory_and_leaks_no_ip_or_ua(
    api: TestClient, tmp_path: Path
) -> None:
    # 앱의 시계는 실제 시각 — 1분 전 줄은 늘 창 안이다
    write(
        tmp_path,
        "access.log",
        [line(time.time() - 60, "/app/?tab=history&gclid=X", ua=IPHONE)],
    )
    resp = api.get("/admin/access")
    assert (
        resp.status_code == 200 and resp.headers["content-type"] == "application/json"
    )
    body = resp.json()
    assert body["state"] == "ok" and body["tabs"] == [["history", 1]]
    for leak in (IP, IPHONE, "gclid"):
        assert leak not in resp.text, leak


def test_http_clarity_answers_one_part_without_the_token(api: TestClient) -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[{"metricName": "Traffic", "information": []}])

    api.app.state.admin_visits = VisitFeeds(
        access_dir=None, clarity_token=TOKEN, transport=httpx.MockTransport(handle)
    )
    resp = api.get("/admin/clarity")
    assert resp.status_code == 200
    body = resp.json()
    assert list(body)[:4] == PART_KEYS
    assert (body["state"], body["traffic"], body["metrics"]) == ("ok", None, [])
    assert len(calls) == 1 and TOKEN not in resp.text
    assert api.get("/admin/clarity").json()["state"] == "ok" and len(calls) == 1


def test_handler_exceptions_become_error_parts_not_500(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*args: object) -> object:
        raise TypeError("arn:aws:iam::123456789012:x secret")

    monkeypatch.setattr(visits, "summarize", boom)
    monkeypatch.setattr(visits, "load_record", boom)
    api.app.state.admin_visits = VisitFeeds(access_dir="/nowhere", clarity_token=TOKEN)
    caplog.set_level(logging.INFO)
    for path in ("/admin/access", "/admin/clarity"):
        resp = api.get(path)
        assert resp.status_code == 200, path
        assert (resp.json()["state"], resp.json()["code"]) == ("error", "TypeError")
        assert "secret" not in resp.text and "arn:aws" not in resp.text
    warnings = [r.getMessage() for r in caplog.records if r.name == "marketlens.admin"]
    assert warnings == [
        "관리자 피드 access 실패 — TypeError",
        "관리자 피드 clarity 실패 — TypeError",
    ]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_http_clarity_with_nan_infinity_and_surrogates_stays_200(
    api: TestClient,
) -> None:
    body = (
        b'[{"metricName":"Traffic","information":[{"totalSessionCount":"5"}]},'
        b'{"metricName":"Scroll Depth","information":[{"avg":NaN,"x":Infinity}]},'
        b'{"metricName":"Page Title","information":[{"title":"BTC \\ud83d"}]}]'
    )
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, content=body, headers={"content-type": "application/json"}
        )
    )
    api.app.state.admin_visits = VisitFeeds(
        access_dir=None, clarity_token=TOKEN, transport=transport
    )
    for _ in range(2):
        resp = api.get("/admin/clarity")
        assert resp.status_code == 200 and resp.json()["state"] == "ok"
    depth, title = resp.json()["metrics"]
    assert depth["rows"] == [{"avg": None, "x": None}]
    assert title["rows"] == [{"title": "BTC ?"}]


def test_http_unwritable_record_in_redis_is_an_error_part_not_500(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # 손으로 넣은 기록 — 짝 없는 서로게이트는 응답으로 쓸 수 없다. 3시간 안이라 부르지도 않는다
    now_ms = int(time.time() * 1000)
    record = (
        f'{{"attemptAt":{now_ms},"state":"ok","code":null,"successAt":{now_ms},'
        '"values":{"numOfDays":1,"traffic":null,"metrics":[{"name":"\\ud83d","rows":[]}]}}'
    )
    server = fakeredis.FakeServer()
    fakeredis.FakeRedis(server=server).set("admin:clarity", record)
    api.app.state.spreads_bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    api.app.state.admin_visits = VisitFeeds(
        access_dir=None, clarity_token=TOKEN, transport=httpx.MockTransport(handle)
    )
    caplog.set_level(logging.INFO)
    resp = api.get("/admin/clarity")
    assert resp.status_code == 200
    assert (resp.json()["state"], resp.json()["code"]) == (
        "error",
        "UnicodeEncodeError",
    )
    assert resp.json()["metrics"] is None and calls == []
    assert [r.getMessage() for r in caplog.records if r.name == "marketlens.admin"] == [
        "관리자 피드 clarity 실패 — UnicodeEncodeError"
    ]
