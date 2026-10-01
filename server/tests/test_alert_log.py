"""보낸 알림 기록 리스트 `alerts:log` — 넣기·자르기 한 왕복, 최신순 읽기, 가림, 두 역할의 배선 (스펙 034 §3.3·§4)."""

import asyncio
import json
import logging
from collections.abc import Iterator

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.redact import MASK, redact
from app.core.redis_bus import ALERT_LOG_KEY, ALERT_LOG_MAX, RedisBus
from app.main import create_app
from tests.test_raw_archive import _boot


def _bus() -> tuple[RedisBus, fakeredis.FakeServer]:
    server = fakeredis.FakeServer()
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server)), server


async def test_push_keeps_newest_first_and_only_the_last_thousand() -> None:
    bus, server = _bus()
    for i in range(ALERT_LOG_MAX + 1):
        await bus.alert_log_push(f'{{"n":{i}}}')
    sync = fakeredis.FakeRedis(server=server)
    assert sync.llen(ALERT_LOG_KEY) == ALERT_LOG_MAX == 1000
    assert sync.ttl(ALERT_LOG_KEY) == -1  # 만료 없음
    recent = await bus.alert_log_recent(3)
    assert recent == ['{"n":1000}', '{"n":999}', '{"n":998}']
    # 가장 오래된 한 줄(0번)이 잘렸다
    assert (await bus.alert_log_recent(ALERT_LOG_MAX))[-1] == '{"n":1}'


async def test_recent_on_missing_key_is_empty() -> None:
    bus, _ = _bus()
    assert await bus.alert_log_recent(200) == []


def test_redact_masks_arn_chunks_and_bare_account_ids_only() -> None:
    text = (
        "User: arn:aws:sts::123456789012:assumed-role/marketlens-s3-snapshot/i-0abc "
        "is not authorized on arn:aws:budgets::123456789012:budget/x; acct=123456789012, "
        "ms=1759300000000 ts=1759300000 id=i-0123456789abcdef0"
    )
    out = redact(text)
    assert "arn:aws:" not in out
    assert out.count(MASK) == 3
    # 13자리 ms·10자리 초는 계정 ID 모양이 아니다 — 그대로
    assert "ms=1759300000000" in out and "ts=1759300000" in out
    assert redact("평범한 문장") == "평범한 문장"


# --- 배선: 두 역할 모두 버스가 생긴 뒤 알림기에 기록 함수를 꽂는다 (§3.3) -------------------

HOOK = "https://hooks.slack.com/services/T/B/x"


@pytest.fixture
def fake_bus(monkeypatch: pytest.MonkeyPatch) -> Iterator[fakeredis.FakeServer]:
    """lifespan 이 만드는 버스를 fakeredis 로 — 네트워크 없이 기록을 읽는다."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        RedisBus,
        "from_url",
        classmethod(lambda cls, url: cls(fakeredis.aioredis.FakeRedis(server=server))),
    )
    yield server
    get_settings.cache_clear()
    root = logging.getLogger()
    for h in [h for h in root.handlers if type(h).__name__ == "SlackLogHandler"]:
        root.removeHandler(h)


def _hook_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


async def test_api_role_records_the_startup_alert(
    monkeypatch: pytest.MonkeyPatch, fake_bus: fakeredis.FakeServer
) -> None:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", HOOK)
    monkeypatch.setenv("INFLUX_TOKEN", "")
    get_settings.cache_clear()
    app = create_app()
    app.state.notifier._client = httpx.AsyncClient(
        transport=httpx.MockTransport(_hook_ok)
    )
    async with app.router.lifespan_context(app):
        assert app.state.notifier.record.__self__ is app.state.spreads_bus
        await asyncio.wait_for(app.state.notifier._queue.join(), 1.0)
    (line,) = fakeredis.FakeRedis(server=fake_bus).lrange(ALERT_LOG_KEY, 0, -1)
    assert json.loads(line) | {"at": 0} == {
        "at": 0,
        "role": "api",
        "key": "startup",
        "text": "🟢 api 기동 (v0.1.0)",
        "delivered": True,
    }


async def test_no_webhook_means_no_notifier_and_nothing_recorded(
    monkeypatch: pytest.MonkeyPatch, fake_bus: fakeredis.FakeServer
) -> None:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("INFLUX_TOKEN", "")
    get_settings.cache_clear()
    app = create_app()
    assert app.state.notifier is None
    async with app.router.lifespan_context(app):
        logging.getLogger("marketlens.x").error(
            "기동 뒤 ERROR"
        )  # 웹훅이 있었다면 알림·기록 한 줄
        await asyncio.sleep(0.05)
    assert fakeredis.FakeRedis(server=fake_bus).llen(ALERT_LOG_KEY) == 0


def test_collector_plugs_the_same_bus_into_the_notifier(
    monkeypatch: pytest.MonkeyPatch, fake_bus: fakeredis.FakeServer
) -> None:
    app, _ = _boot(monkeypatch, slack_webhook_url=HOOK)
    with TestClient(app):
        assert app.state.notifier.record.__self__ is app.state.spreads_bus
