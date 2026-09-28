"""027 §3.3·§4 — WS 접속 수 StatsD 게이지. 받는 쪽은 가짜 UDP 수신기(127.0.0.1 임의 포트)다."""

import asyncio
import logging
import socket

import pytest

from app.features.spreads import gauge as gauge_mod
from app.features.spreads.gauge import WsClientsGauge

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
