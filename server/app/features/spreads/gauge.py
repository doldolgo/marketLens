"""WebSocket 접속 수 게이지 — StatsD UDP 한 줄 (스펙 027 §3.3).

접속 수 = 허브가 들고 있는 열린 `/ws/spreads` 연결 수(`waiting` 중 포함). 열린 `/app/` 페이지 수이지 사람
수가 아니다. api lifespan 만 띄운다 — collector 의 허브는 배포에서 늘 0 이라 api 값을 덮는다.
받는 쪽은 serve 호스트의 CloudWatch Agent(StatsD :8125)다. 없으면 UDP 가 버려질 뿐 api 는 영향이 없다.

설계 이유:
- 0명도 보낸다 — 안 보내면 "0명" 과 "api 가 죽음" 이 구별되지 않는다.
- 이름 풀기는 스레드에서(getaddrinfo 는 막는다), 풀리면 그 주소를 계속 쓰고 실패하면 다음 회차에 다시 푼다.
- 전송은 non-blocking 한 번, 재시도 없음. 실패 로그는 10분에 1줄 — 에이전트가 없는 동안 10초마다 찍히지 않게.
- 라이브러리 없이 표준 `socket` 만 쓴다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("marketlens.ws_gauge")

METRIC = "marketlens.ws_clients"
INTERVAL_SEC = 10.0  # 기동 즉시 한 번, 이후 이 주기 (§3.3)
WARN_EVERY_SEC = 600.0  # 풀기·전송 실패 WARNING 은 10분에 1줄
TASK_NAME = "ws_clients_gauge"


def parse_addr(value: str) -> tuple[str, int] | None:
    """`host:port` → (host, port). 나눌 수 없거나 포트가 1~65535 밖이면 None. IPv6 는 `[::1]:8125`."""
    host, sep, port = value.strip().rpartition(":")
    host = host.removeprefix("[").removesuffix("]")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        return None
    return host, int(port)


class WsClientsGauge:
    """`count()` 를 주기마다 `marketlens.ws_clients:<정수>|g` 한 줄로 보낸다."""

    def __init__(
        self,
        *,
        count: Callable[[], int],
        host: str,
        port: int,
        interval: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._count = count
        self._host = host
        self._port = port
        # 모듈 상수를 만들 때 읽는다 — 테스트가 주기를 짧게 바꿔 끼울 수 있게
        self._interval = INTERVAL_SEC if interval is None else interval
        self._clock = clock
        self._sock: socket.socket | None = None
        self._dest: Any = None
        self._last_warned: float | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=TASK_NAME)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    async def _run(self) -> None:
        while True:
            await self._send_once()
            await asyncio.sleep(self._interval)

    async def _send_once(self) -> None:
        if self._sock is None and not await self._resolve():
            return
        assert self._sock is not None
        line = f"{METRIC}:{int(self._count())}|g".encode()
        try:
            self._sock.sendto(line, self._dest)
        except OSError as exc:
            self._warn("StatsD 전송 실패 — 이번 회차는 버린다: %r", exc)

    async def _resolve(self) -> bool:
        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo, self._host, self._port, type=socket.SOCK_DGRAM
            )
            family, kind, proto, _, dest = infos[0]
            sock = socket.socket(family, kind, proto)
            sock.setblocking(False)
        except OSError as exc:
            self._warn(
                "StatsD 주소 %s:%d 를 못 풀었다 — 다음 회차에 다시: %r",
                self._host,
                self._port,
                exc,
            )
            return False
        self._sock, self._dest = sock, dest
        return True

    def _warn(self, msg: str, *args: object) -> None:
        now = self._clock()
        if self._last_warned is not None and now - self._last_warned < WARN_EVERY_SEC:
            return
        self._last_warned = now
        logger.warning(msg, *args)


def start_ws_gauge(addr: str | None, count: Callable[[], int]) -> WsClientsGauge | None:
    """`STATSD_ADDR` 가 비었거나 없으면 None(로컬·테스트 기본). `host:port` 가 아니면 WARNING 1줄 뒤 None — 앱은 뜬다."""
    if not addr or not addr.strip():
        return None
    target = parse_addr(addr)
    if target is None:
        logger.warning(
            "STATSD_ADDR 가 host:port 모양이 아니다 — WS 접속 수 게이지를 끈다: %r",
            addr,
        )
        return None
    gauge = WsClientsGauge(count=count, host=target[0], port=target[1])
    gauge.start()
    return gauge
