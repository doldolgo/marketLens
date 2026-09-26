"""수집기 심장박동 + 기동 알림 (스펙 025 §3.3·§3.4, §4)."""

import asyncio
import json
import logging

import fakeredis
import httpx
import pytest

from app.core.config import get_settings
from app.core.heartbeat import HeartbeatSink
from app.core.redis_bus import RedisBus
from app.core.ticks import TickLoop
from app.main import create_app
from tests.test_ticks import _client, seeded

T0 = 1_700_000_000


async def test_tick_writes_heartbeat_key_with_30s_ttl() -> None:
    server = fakeredis.FakeServer()
    bus = RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    hb = HeartbeatSink(bus=bus)
    loop = TickLoop(store=seeded(), streams=[], client=_client(), heartbeat=hb)
    loop.tick(T0)
    await asyncio.sleep(0)  # 쓰기 태스크 1회전
    await hb.aclose()
    raw = fakeredis.FakeRedis(server=server)
    assert raw.get("collect:heartbeat") == str(T0 * 1000).encode()
    assert 0 < raw.ttl("collect:heartbeat") <= 30
    assert await bus.heartbeat() == T0 * 1000


async def test_heartbeat_failure_does_not_break_tick_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken:
        async def set_heartbeat(self, ts_ms: int) -> None:
            raise RuntimeError("refused")

    caplog.set_level(logging.WARNING, logger="marketlens.heartbeat")
    hb = HeartbeatSink(bus=Broken())  # type: ignore[arg-type]
    loop = TickLoop(store=seeded(), streams=[], client=_client(), heartbeat=hb)
    for i in range(3):
        loop.tick(T0 + i)  # 예외 없이 돈다
        await asyncio.sleep(0)
    await hb.aclose()
    assert len([r for r in caplog.records if r.name == "marketlens.heartbeat"]) == 1


@pytest.fixture
def webhook_env(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    root = logging.getLogger()
    for h in list(root.handlers):
        if type(h).__name__ == "SlackLogHandler":
            root.removeHandler(h)


async def test_startup_alert_is_sent_once_from_lifespan(webhook_env) -> None:  # noqa: ANN001
    bodies: list[dict] = []

    def hook(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200)

    app = create_app()
    assert app.state.notifier is not None
    app.state.notifier._client = httpx.AsyncClient(transport=httpx.MockTransport(hook))
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(app.state.notifier._queue.join(), 1.0)
    assert bodies == [{"text": "[api] 🟢 api 기동 (v0.1.0)"}]


def test_no_webhook_means_no_notifier_and_no_handler() -> None:
    app = create_app()
    assert app.state.notifier is None
    assert not any(
        type(h).__name__ == "SlackLogHandler" for h in logging.getLogger().handlers
    )
