"""일중 기준가 장부 — 스펙 026 §3.1.

국내 거래소·코인별 **KST 00:00 이후 첫 체결가**를 잡아 두고 Redis 해시 `dayopen:<날짜>` 에 보존한다.
틱 루프가 매초 observe 로 넘기고(동기·무예외), 표 게시기가 `prices` 를 읽어 행의 `dayChg` 를 만든다.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, timezone

from app.core.models import Tick
from app.core.redis_bus import RedisBus

logger = logging.getLogger("marketlens.day_open")

KST = timezone(timedelta(hours=9))
WARN_SUPPRESS_SEC = 600.0


def kst_date(ts: int) -> str:
    """epoch 초 → KST 달력일 `YYYY-MM-DD` — 하루의 경계는 KST 자정이다 (§3.1)."""
    return datetime.fromtimestamp(ts, tz=UTC).astimezone(KST).date().isoformat()


class DayOpenBook:
    def __init__(
        self, *, bus: RedisBus | None, clock: Callable[[], float] = time.time
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._date: str | None = None
        self._prices: dict[tuple[str, str], float] = {}
        # 읽기가 끝나기 전엔 못 박지 않는다 — 재기동 직후 가격이 자정 가격을 덮어쓰면 안 된다 (§3.1)
        self._loaded = False
        self._pending: dict[
            str, float
        ] = {}  # 아직 Redis 에 못 쓴 항목 — 다음 틱에 다시
        self._task: asyncio.Task[None] | None = None
        self._last_warned: float | None = None

    @property
    def date(self) -> str | None:
        return self._date

    @property
    def prices(self) -> Mapping[tuple[str, str], float]:
        """(거래소, 코인) → 오늘 기준가. 표 게시기가 매초 읽는다."""
        return self._prices

    def observe(self, tick: Tick) -> None:
        date = kst_date(tick.ts)
        if date != self._date:
            # 자정(또는 기동) — 장부를 비우고 그 날짜의 해시부터 읽는다
            self._date = date
            # 게시기가 `prices` 의 참조를 들고 있다 — 새 dict 로 갈아끼우면 게시기는 빈 옛 dict 를 본다
            self._prices.clear()
            self._pending = {}
            self._loaded = False
            self._spawn(self._load(date))
            return
        if not self._loaded:
            return
        for row in tick.rows:
            key = (row.dom, row.base)
            if key in self._prices or row.dom_price <= 0:
                continue
            self._prices[key] = row.dom_price
            self._pending[f"{row.dom}:{row.base}"] = row.dom_price
        if self._pending and (self._task is None or self._task.done()):
            # 배치는 지금 떼어 둔다 — 태스크가 도는 사이 자정 비우기가 pending 을 지워도 이 배치는 남는다
            batch, self._pending = self._pending, {}
            self._spawn(self._save(date, batch))

    def _spawn(self, coro) -> None:  # noqa: ANN001 — 코루틴 객체
        self._task = asyncio.create_task(coro, name="day_open")

    async def _load(self, date: str) -> None:
        stored: dict[str, float] = {}
        if self._bus is not None:
            try:
                stored = await self._bus.day_open_load(date)
            except Exception as exc:
                self._warn(
                    "기준가 읽기 실패 — 오늘은 메모리만으로 진행 %s: %r", date, exc
                )
        if self._date != date:
            return  # 읽는 사이 날짜가 또 바뀜 — 그 날짜의 읽기가 따로 돈다
        for field, price in stored.items():
            exchange, _, base = field.partition(":")
            self._prices.setdefault((exchange, base), price)
        self._loaded = True

    async def _save(self, date: str, batch: dict[str, float]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.day_open_save(date, batch)
        except Exception as exc:
            self._warn("기준가 쓰기 실패 — 다음 틱에 다시: %r", exc)
            if self._date == date:
                # 다음 틱의 observe 가 다시 보낸다 — 그 사이 새로 잡힌 값이 있으면 그쪽이 우선
                self._pending = {**batch, **self._pending}

    def _warn(self, msg: str, *args: object) -> None:
        now = self._clock()
        if (
            self._last_warned is not None
            and now - self._last_warned < WARN_SUPPRESS_SEC
        ):
            return
        self._last_warned = now
        logger.warning(msg, *args)

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
