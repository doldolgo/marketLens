"""관리자 피드 테스트 공통 — 가짜 주입이 빠져 실제 transport 로 Clarity 를 부르면 그 테스트를 실패시킨다 (스펙 040 §4)."""

from collections.abc import Iterator

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    used: list[str] = []

    async def refuse(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        used.append(request.url.host)
        raise httpx.ConnectError("테스트는 네트워크를 쓰지 않는다", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    yield
    assert not used, f"가짜 transport 없이 실제 transport 를 썼다: {used}"
