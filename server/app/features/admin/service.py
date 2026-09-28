"""관리자 상태 — WS 접속 수·Redis·Influx 확인 (스펙 029 §3.4).

허브·버스·Influx 는 api lifespan 이 `app.state` 에 둔 것을 라우터가 넘긴다. 허브는 spreads 기능의
객체지만 import 하지 않고 `connections` 만 구조적 타입으로 안다(기능 간 import 금지).

두 확인은 동시에 돌고 각각 2초 제한이다. Influx 클라이언트는 동기라 스레드에서 부르는데 자체 타임아웃이
60초라, 제한을 넘긴 ping 은 스레드에서 계속 돈다 — 그 ping 이 끝나기 전에는 새로 띄우지 않고 down 을
답한다. 매달린 Influx 에서 10초 폴링이 스레드를 쌓아 `/history`·`/landing` 조회를 밀어내지 않게.
"""

import asyncio
from typing import Protocol

from app.core.config import APP_VERSION
from app.features.admin.models import AdminStatusOut, Health

CHECK_TIMEOUT_SEC = 2.0


class Hub(Protocol):
    @property
    def connections(self) -> int: ...


class Bus(Protocol):
    async def ping(self) -> None: ...


class Influx(Protocol):
    def ping(self) -> bool: ...


class AdminStatusService:
    def __init__(self, *, timeout_sec: float = CHECK_TIMEOUT_SEC) -> None:
        self._timeout_sec = timeout_sec
        self._influx_ping: asyncio.Future[bool] | None = None

    async def status(
        self, *, hub: Hub | None, bus: Bus | None, influx: Influx | None
    ) -> AdminStatusOut:
        redis, influx_health = await asyncio.gather(
            self._check_redis(bus), self._check_influx(influx)
        )
        return AdminStatusOut(
            ws_connections=hub.connections if hub is not None else 0,
            redis=redis,
            influx=influx_health,
            version=APP_VERSION,
        )

    async def _check_redis(self, bus: Bus | None) -> Health:
        if bus is None:
            return "down"
        try:
            await asyncio.wait_for(bus.ping(), self._timeout_sec)
        except Exception:
            return "down"
        return "ok"

    async def _check_influx(self, influx: Influx | None) -> Health:
        if influx is None:
            return "down"  # 토큰 없음 — 클라이언트를 만들지 않았다
        pending = self._influx_ping
        if pending is not None and not pending.done():
            return "down"  # 앞선 ping 이 스레드에서 아직 돈다 — 쌓지 않는다
        task = asyncio.ensure_future(asyncio.to_thread(influx.ping))
        task.add_done_callback(_consume)
        self._influx_ping = task
        try:
            # shield — 제한을 넘겨도 ping 태스크는 스레드가 끝날 때까지 살아 있어야 "진행 중" 을 안다
            ok = await asyncio.wait_for(asyncio.shield(task), self._timeout_sec)
        except Exception:
            return "down"
        return "ok" if ok else "down"


def _consume(task: asyncio.Future[bool]) -> None:
    """제한을 넘긴 뒤 끝난 ping 의 예외를 거둔다 — 안 거두면 루프가 "never retrieved" 경고를 낸다."""
    if not task.cancelled():
        task.exception()
