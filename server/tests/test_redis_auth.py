"""Redis 비밀번호 — 값이 있을 때만 `AUTH default <값>`, 비면 지금처럼 인증 없이 (스펙 021 §3.7).

compose 쪽 계약(`--requirepass ${REDIS_PASSWORD:-}`)은 `test_deploy.py`. 인증 없는 Redis 가 `AUTH default <값>` 을
받아 주는 성질(클라이언트 먼저 켜기의 근거)은 fakeredis 가 실제 Redis 와 달라 여기서 보지 않는다 — redis:7-alpine(7.4)
실측은 021 §5.
"""

from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.redis_bus import RedisBus
from app.core.redis_stream import RedisTickStream, auth_kwargs
from app.main import create_app
from tests.test_raw_archive import _boot

URL = "redis://10.0.0.5:6379/0"


@pytest.mark.parametrize("password", [None, ""])
def test_no_password_sends_no_credentials(password: str | None) -> None:
    assert auth_kwargs(password) == {}
    for cls in (RedisBus, RedisTickStream):
        kwargs = cls.from_url(
            URL, password=password
        )._client.connection_pool.connection_kwargs
        assert kwargs.get("username") is None and kwargs.get("password") is None, cls


def test_password_goes_with_the_default_user_on_every_connection() -> None:
    # 사용자 이름을 함께 보내야 nopass Redis 도 받아 준다 — 2인자 AUTH 는 nopass 서버가 거절한다
    assert auth_kwargs("pw") == {"username": "default", "password": "pw"}
    for cls in (RedisBus, RedisTickStream):
        client = cls.from_url(URL, password="pw")._client
        kwargs = client.connection_pool.connection_kwargs
        assert (kwargs["username"], kwargs["password"]) == ("default", "pw"), cls
        # 주소·제한 시간은 비밀번호와 무관하게 그대로
        assert (kwargs["host"], kwargs["port"], kwargs["db"]) == ("10.0.0.5", 6379, 0)
        assert kwargs["socket_connect_timeout"] == 2.0


def test_settings_read_redis_password_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    assert Settings(_env_file=None).redis_password is None
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    assert Settings(_env_file=None).redis_password == "pw"


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """두 클라이언트의 from_url 을 fakeredis 로 바꾸고 (클래스, password) 를 모은다 — 네트워크 없이 배선만 본다."""
    server = fakeredis.FakeServer()
    seen: list[tuple[str, str | None]] = []

    def fake(cls: Any, url: str, password: str | None = None) -> Any:
        seen.append((cls.__name__, password))
        return cls(fakeredis.aioredis.FakeRedis(server=server))

    for cls in (RedisBus, RedisTickStream):
        monkeypatch.setattr(cls, "from_url", classmethod(fake))
    return seen


def test_collector_gives_the_password_to_both_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture(monkeypatch)
    app, _ = _boot(monkeypatch, redis_password="pw")
    with TestClient(app):
        pass
    assert set(seen) == {("RedisTickStream", "pw"), ("RedisBus", "pw")}


async def test_api_role_gives_the_password_to_its_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture(monkeypatch)
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    get_settings.cache_clear()
    try:
        app = create_app()
        async with app.router.lifespan_context(app):
            pass
    finally:
        get_settings.cache_clear()
    # api 는 스트림 `ticks` 를 안 읽는다 — 버스 하나
    assert seen == [("RedisBus", "pw")]
