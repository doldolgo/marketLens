"""027 §3.3·§4 — WS 접속 수 StatsD 게이지. 받는 쪽은 가짜 UDP 수신기(127.0.0.1 임의 포트)다."""

import asyncio
import logging
import socket
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.features.spreads import gauge as gauge_mod
from app.features.spreads.gauge import WsClientsGauge
from app.main import create_app

LINE = "marketlens.ws_clients:{}|g"


class Receiver(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.lines: asyncio.Queue[str] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: object) -> None:
        self.lines.put_nowait(data.decode())


async def _receiver() -> tuple[asyncio.DatagramTransport, Receiver, int]:
    transport, proto = await asyncio.get_running_loop().create_datagram_endpoint(
        Receiver, local_addr=("127.0.0.1", 0)
    )
    return transport, proto, transport.get_extra_info("sockname")[1]


async def test_sends_immediately_then_every_interval_including_zero() -> None:
    transport, rx, port = await _receiver()
    clients = [0]
    gauge = WsClientsGauge(
        count=lambda: clients[0], host="127.0.0.1", port=port, interval=0.05
    )
    gauge.start()
    try:
        assert await asyncio.wait_for(rx.lines.get(), 1) == LINE.format(0)
        clients[0] = 2
        while (line := await asyncio.wait_for(rx.lines.get(), 1)) == LINE.format(0):
            pass
        assert line == LINE.format(2)
    finally:
        await gauge.aclose()
        transport.close()


async def test_resolve_failure_warns_once_per_10_minutes_and_retries_next_round(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    transport, rx, port = await _receiver()
    real = socket.getaddrinfo
    failures = [2]

    def flaky(*args: object, **kwargs: object) -> object:
        if failures[0] > 0:
            failures[0] -= 1
            raise socket.gaierror("temporary failure in name resolution")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(gauge_mod.socket, "getaddrinfo", flaky)
    caplog.set_level(logging.WARNING, logger="marketlens.ws_gauge")
    gauge = WsClientsGauge(count=lambda: 1, host="127.0.0.1", port=port, interval=0.02)
    gauge.start()
    try:
        # 두 회차 실패 뒤 세 번째 회차에 풀려서 보낸다
        assert await asyncio.wait_for(rx.lines.get(), 1) == LINE.format(1)
    finally:
        await gauge.aclose()
        transport.close()
    warnings = [r for r in caplog.records if r.name == "marketlens.ws_gauge"]
    assert len(warnings) == 1 and "다음 회차" in warnings[0].getMessage()


# --- 앱 배선: api 역할만, STATSD_ADDR 가 있을 때만 (test_role.py 방식) ------------


@pytest.fixture
def api_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    def _set(role: str, statsd: str | None) -> None:
        monkeypatch.setenv("ROLE", role)
        monkeypatch.setenv("INFLUX_TOKEN", "")  # Influx ping 없음 — 네트워크 없이
        monkeypatch.setenv("S3_BUCKET", "")
        monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # 즉시 거절
        if statsd is None:
            monkeypatch.delenv("STATSD_ADDR", raising=False)
        else:
            monkeypatch.setenv("STATSD_ADDR", statsd)
        get_settings.cache_clear()

    monkeypatch.setattr(gauge_mod, "INTERVAL_SEC", 0.05)
    yield _set
    get_settings.cache_clear()


def _udp() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    return sock, sock.getsockname()[1]


def _until(sock: socket.socket, value: int) -> None:
    """주기마다 오는 줄 중 `value` 가 나올 때까지 읽는다(2초 안에 안 오면 timeout)."""
    while sock.recv(64).decode() != LINE.format(value):
        pass


def test_api_app_reports_open_connections(api_env) -> None:  # noqa: ANN001
    """접속 0명이면 0, 연결 2개면 2, 닫으면 다시 준다 — 허브가 든 열린 연결 수(`waiting` 포함)."""
    sock, port = _udp()
    api_env("api", f"127.0.0.1:{port}")
    try:
        with TestClient(create_app()) as client:
            assert sock.recv(64).decode() == LINE.format(0)  # 기동 즉시 한 번
            with (
                client.websocket_connect("/ws/spreads"),
                client.websocket_connect("/ws/spreads"),
            ):
                _until(sock, 2)
            _until(sock, 0)
    finally:
        sock.close()


async def test_api_lifespan_has_gauge_task_only_with_statsd_addr(api_env) -> None:  # noqa: ANN001
    sock, port = _udp()
    try:
        for statsd, expected in (
            (None, ["spreads_hub"]),
            ("", ["spreads_hub"]),
            (f"127.0.0.1:{port}", ["spreads_hub", "ws_clients_gauge"]),
        ):
            api_env("api", statsd)
            app = create_app()
            async with app.router.lifespan_context(app):
                others = [
                    t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                ]
                assert sorted(t.get_name() for t in others) == expected, statsd
                await asyncio.sleep(0.1)
        # 주소가 있던 회차만 보냈다 — 없거나 빈 값이면 아무것도 오지 않는다
        sock.settimeout(0.3)
        lines = []
        with pytest.raises(TimeoutError):
            while True:
                lines.append(sock.recv(64).decode())
        assert lines and set(lines) == {LINE.format(0)}
    finally:
        sock.close()


@pytest.mark.parametrize(
    "bad", ["8125", "localhost", "localhost:", ":8125", "h:0", "h:70000", "h:x"]
)
async def test_bad_statsd_addr_warns_and_app_still_starts(
    api_env,  # noqa: ANN001
    bad: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="marketlens.ws_gauge")
    api_env("api", bad)
    app = create_app()
    async with app.router.lifespan_context(app):
        names = [t.get_name() for t in asyncio.all_tasks()]
        assert "ws_clients_gauge" not in names
        assert "spreads_hub" in names
    warnings = [r for r in caplog.records if r.name == "marketlens.ws_gauge"]
    assert len(warnings) == 1 and "host:port" in warnings[0].getMessage()


async def test_collector_never_starts_the_gauge(
    api_env,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """collector 의 허브는 배포에서 늘 0 이라 api 값을 덮는다 — STATSD_ADDR 가 있어도 띄우지 않는다."""
    import app.main as main

    # 거래소·틱 루프는 띄우지 않는다(네트워크 없음) — 나머지 lifespan 은 그대로 돈다
    for cls in (
        main.UniverseRefresher,
        main.UpbitStream,
        main.BithumbStream,
        main.BinanceStream,
        main.BybitStream,
        main.BitgetStream,
        main.TickLoop,
    ):
        monkeypatch.setattr(cls, "start", lambda self: None)
    sock, port = _udp()
    api_env("collector", f"127.0.0.1:{port}")
    try:
        app = create_app()
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.2)
            names = [t.get_name() for t in asyncio.all_tasks()]
            assert "spreads_hub" in names and "ws_clients_gauge" not in names
        sock.settimeout(0.2)
        with pytest.raises(TimeoutError):
            sock.recv(64)
    finally:
        sock.close()
