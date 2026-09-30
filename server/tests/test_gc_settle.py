"""기동을 마친 뒤 GC 정리 — 거두고 얼린 뒤 세대 임계를 올린다 (스펙 001 §3.1·016 §3.1, 2026-09-28 결정).

두 역할 모두 lifespan 이 yield 하기 직전에 한다. 프로세스 전역 설정이라 conftest 가 테스트마다 되돌린다.
"""

import gc

import pytest
from fastapi.testclient import TestClient

from app.core.config import GC_THRESHOLDS, get_settings
from app.main import create_app
from tests.test_raw_archive import _boot


def _reset() -> None:
    gc.unfreeze()
    gc.set_threshold(700, 10, 10)


async def test_api_lifespan_freezes_startup_objects_and_raises_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    get_settings.cache_clear()
    try:
        app = create_app()
        _reset()
        assert gc.get_freeze_count() == 0
        async with app.router.lifespan_context(app):
            assert gc.get_threshold() == GC_THRESHOLDS
            assert gc.get_freeze_count() > 0
    finally:
        get_settings.cache_clear()


def test_collector_lifespan_freezes_startup_objects_and_raises_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _boot(monkeypatch)
    _reset()
    with TestClient(app):
        assert gc.get_threshold() == GC_THRESHOLDS
        assert gc.get_freeze_count() > 0
