"""화면 영역 이용 통계 — 비콘 받기·IP 10분 제한·10초 묶음 쓰기 (스펙 052 §3.4·§3.5). 관리자 피드는 `feed.py`.

api 역할 하나에 서비스 하나(`app.state.attention`). 받은 비콘은 메모리의 (KST 날, 필드) 합에 더해 두고 10초마다 한 번
Redis 해시 `attn:d:<YYYYMMDD>` 에 파이프라인으로 보낸다 — 비콘마다 Redis 를 부르지 않아 방문이 몰려도 Redis 왕복은 10초에
한 번이다. 실패한 묶음은 메모리에 남겨 다음 회차에 함께 보내고, 쌓인 필드가 2만을 넘으면 버린다(통계 빈칸이지 서비스 장애가
아니다). IP 는 10분 고정 창의 셈 표에만 있고 로그·Redis·응답에 쓰지 않는다.

처리방침 v3 시행일(`PRIVACY_V3_EFFECTIVE`) 00:00 Asia/Seoul 전에 온 비콘은 검사만 하고 버린다 — 개정 전에 새 항목을 모으지
않으려는 날짜 게이트다(시각 계산은 `kst.py`).
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from app.core import config
from app.core.redis_bus import RedisBus
from app.features.attention.feed import AttentionFeed
from app.features.attention.kst import day_key, expire_at, gate_ts, kst_date
from app.features.attention.models import (
    AREA_ID,
    DEVICES,
    MAX_AREAS,
    MAX_BODY_BYTES,
    MAX_CLICKS,
    MAX_MS,
    METRICS,
    PAGES,
    Beacon,
)

logger = logging.getLogger("marketlens.attention")

FLUSH_SEC = 10.0
# 실패가 이어져 메모리에 쌓인 필드가 이만큼을 넘으면 묶음을 버린다(§3.5) — 하루 ≈900 필드라 20일치 남짓
MAX_PENDING_FIELDS = 20_000
RATE_WINDOW_SEC = 600
RATE_LIMIT = 120
RATE_MAX_KEYS = 50_000

Outcome = Literal["added", "dropped", "cross_site", "too_large", "bad_beacon"]


def _int_in(value: object, low: int, high: int) -> bool:
    # bool 은 int 의 하위형이라 따로 막는다 — JSON 의 true 는 1 이 아니다
    return type(value) is int and low <= value <= high


def parse_beacon(body: bytes) -> Beacon | None:
    """§3.4 ③ — 어긋나면 None(400). 키는 다섯 개 그대로만 받는다."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        # 깨진 UTF-8·JSON, 4KB 안의 깊은 중첩(파이썬 재귀 한도) 모두 400
        return None
    if not isinstance(data, dict) or set(data) != {"v", "page", "device", "pv", "a"}:
        return None
    if type(data["v"]) is not int or data["v"] != 1:
        return None
    page, device, pv, raw = data["page"], data["device"], data["pv"], data["a"]
    if page not in PAGES or device not in DEVICES or not _int_in(pv, 0, 1):
        return None
    if not isinstance(raw, dict) or len(raw) > MAX_AREAS:
        return None
    areas: dict[str, tuple[int, int, int]] = {}
    for area, value in raw.items():
        if not AREA_ID.fullmatch(area):
            return None
        if not isinstance(value, list) or len(value) != 3:
            return None
        ms, clicks, seen = value
        if not (
            _int_in(ms, 0, MAX_MS)
            and _int_in(clicks, 0, MAX_CLICKS)
            and _int_in(seen, 0, 1)
        ):
            return None
        areas[area] = (ms, clicks, seen)
    return Beacon(page=page, device=device, pv=pv, areas=areas)


def beacon_counts(beacon: Beacon) -> dict[str, int]:
    """비콘 → 해시 필드별 더할 값(0 은 뺀다)."""
    head = f"{beacon.page}|{beacon.device}"
    counts: dict[str, int] = {}
    if beacon.pv:
        counts[f"{head}||pv"] = beacon.pv
    for area, values in beacon.areas.items():
        for metric, n in zip(METRICS, values, strict=True):
            if n:
                counts[f"{head}|{area}|{metric}"] = n
    return counts


class RateLimiter:
    """같은 IP 의 10분 고정 창 안 120번까지 — 창이 바뀌면 표를 비우고, 키가 5만이면 새 IP 는 세지 않고 받는다."""

    def __init__(self) -> None:
        self._window = -1
        self._seen: dict[str, int] = {}

    def allow(self, ip: str, now: float) -> bool:
        window = int(now // RATE_WINDOW_SEC)
        if window != self._window:
            self._window = window
            self._seen = {}
        count = self._seen.get(ip)
        if count is None:
            if len(self._seen) >= RATE_MAX_KEYS:
                return True
            count = 0
        count += 1
        self._seen[ip] = count
        return count <= RATE_LIMIT


def _merge(into: dict[str, dict[str, int]], day: str, counts: dict[str, int]) -> None:
    bucket = into.setdefault(day, {})
    for field, n in counts.items():
        bucket[field] = bucket.get(field, 0) + n


class AttentionService:
    """시계·잠·시행일은 테스트가 바꿀 수 있게 주입한다."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        privacy_effective: str | None = None,
    ) -> None:
        self._clock = clock
        self._mono = mono
        self._sleep = sleep
        effective = privacy_effective or config.PRIVACY_V3_EFFECTIVE
        self._gate = gate_ts(effective)
        self._limiter = RateLimiter()
        self._pending: dict[str, dict[str, int]] = {}
        self._bus: RedisBus | None = None
        self._task: asyncio.Task[None] | None = None
        self._feed = AttentionFeed(clock=clock, mono=mono, effective=effective)

    # --- 받기 (§3.4) ---

    def accept(
        self, body: bytes | None, sec_fetch_site: str | None, client_ip: str | None
    ) -> Outcome:
        """① 교차 사이트 ② 크기(body None = 4,096바이트 초과) ③ 검사 ④ 게이트 ⑤ IP 제한 ⑥ 더하기."""
        if sec_fetch_site is not None and sec_fetch_site != "same-origin":
            return "cross_site"
        if body is None or len(body) > MAX_BODY_BYTES:
            return "too_large"
        beacon = parse_beacon(body)
        if beacon is None:
            return "bad_beacon"
        now = self._clock()
        if now < self._gate:
            return "dropped"
        if client_ip and not self._limiter.allow(client_ip, now):
            return "dropped"
        counts = beacon_counts(beacon)
        if counts:
            _merge(self._pending, day_key(kst_date(now)), counts)
        return "added"

    # --- 10초 묶음 쓰기 (§3.5) ---

    def start(self, bus: RedisBus) -> None:
        """api lifespan 이 버스를 만든 뒤 한 번 — 주기 작업 하나를 띄운다."""
        self._bus = bus
        self._task = asyncio.create_task(self._run(), name="attention_flush")

    async def _run(self) -> None:
        while True:
            await self._sleep(FLUSH_SEC)
            await self.flush()

    async def aclose(self) -> None:
        """끌 때 — 주기 작업을 멈추고 남은 묶음을 한 번 보낸다."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.flush()

    async def flush(self) -> None:
        """쌓인 날마다 한 파이프라인. 실패한 날은 메모리로 되돌리고, 쌓인 필드가 2만을 넘으면 통째로 버린다."""
        bus = self._bus
        if bus is None or not self._pending:
            return
        batch, self._pending = self._pending, {}
        failed: dict[str, dict[str, int]] = {}
        for day, counts in batch.items():
            try:
                await bus.attention_add(day, counts, expire_at(day))
            except (
                Exception
            ):  # Redis 가 죽어도 받기는 계속된다. 다음 회차에 다시 보낸다
                failed[day] = counts
        if not failed:
            return
        # 보내는 동안 새로 받은 몫과 합친다
        for day, counts in failed.items():
            _merge(self._pending, day, counts)
        fields = sum(len(counts) for counts in self._pending.values())
        if fields > MAX_PENDING_FIELDS:
            self._pending = {}
            logger.warning(
                "화면 영역 통계 %d 필드를 버렸다 — Redis 쓰기 실패가 이어졌다", fields
            )

    # --- 관리자 피드 (§3.6) ---

    async def feed(self, days: str | None, bus: RedisBus | None) -> dict[str, Any]:
        return await self._feed.read(days, bus)
