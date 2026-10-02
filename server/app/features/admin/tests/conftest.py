"""관리자 피드 테스트 공통 — 가짜 주입이 빠져 실제 transport 로 Clarity·DB-IP 를 부르거나 DB-IP 받기 스레드를
그대로 띄우면 그 테스트를 실패시킨다 (스펙 040 §4·039 §4)."""

from collections.abc import Callable, Iterator

import httpx
import pytest

from app.features.admin import geo_fetch


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    used: list[str] = []

    async def refuse(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        used.append(request.url.host)
        raise httpx.ConnectError("테스트는 네트워크를 쓰지 않는다", request=request)

    def refuse_sync(
        self: httpx.HTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        used.append(request.url.host)
        raise httpx.ConnectError("테스트는 네트워크를 쓰지 않는다", request=request)

    def no_thread(job: Callable[[], None]) -> None:
        # 띄우기를 주입하지 않은 받기 — 뒤에서 돌면 테스트가 끝난 뒤 실제 transport 를 쓸 수 있다
        used.append("DB-IP 받기 스레드")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_sync)
    monkeypatch.setattr(geo_fetch, "_daemon", no_thread)
    yield
    assert not used, f"가짜 transport 없이 실제 transport 를 썼다: {used}"
