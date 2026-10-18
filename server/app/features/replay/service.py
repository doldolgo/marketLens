"""화면 이동 기록 받기 — 검사·게이트·IP 10분 제한·묶음마다 곧바로 Redis (스펙 073 §3.4·§3.5). 관리자 피드는 `feed.py`.

api 역할 하나에 서비스 하나(`app.state.replay`). 052 처럼 메모리에 모았다 10초마다 보내지 않고 묶음마다 곧바로 한 MULTI 로
쓴다 — 관리자의 '지금 보는 중' 이 2~4초 늦게 따라오게. 쓰기 전에 조각 머리와 그날 바이트 합을 한 왕복으로 읽어 상한(그날 8MB·
조각 1MB·묶음 3,600)과 `seq` 차례를 본다. 같은 조각의 두 묶음이 동시에 와도(숨길 때의 비콘과 2초 묶음) 읽고 쓰는 사이에 섞이지
않게 조각마다 메모리 잠금을 건다(워커 1개). Redis 가 실패하면 그 묶음은 버리고(204) WARNING 은 30초에 한 번 — 재생 기록이
빈칸이 될 뿐 서비스 장애가 아니다.

처리방침 v7 시행일(`PRIVACY_V7_EFFECTIVE`) 00:00 Asia/Seoul 전에 온 묶음은 검사만 하고 버린다. IP 는 10분 고정 창의 셈 표
(메모리)와 조각 머리·`rp:ip:<ip>` 에 쓴다 — 관리자가 IP 별로 찾는 기록이라서다(076 §3.2).

보관(078 §3.1): 조각 머리를 처음 만들 때 v8 시행일(`PRIVACY_V8_EFFECTIVE`) 00:00 KST 이후면 `keep=1` — 머리는 만료 없이 두고
수집기가 S3 로 옮긴다(`rp:todo`). 묶음 리스트는 keep 이어도 마지막 쓰기 + 14일(최근 재생용 Redis 몫).
"""

import asyncio
import contextlib
import json
import logging
import time
import zlib
from collections.abc import AsyncIterator, Callable
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.core import config
from app.core.redis_bus import RedisBus
from app.features.replay.feed import ReplayFeed
from app.features.replay.models import MAX_BODY_BYTES, Batch, client_ip, parse_batch

logger = logging.getLogger("marketlens.replay")

KST = ZoneInfo("Asia/Seoul")
RATE_WINDOW_SEC = 600
RATE_LIMIT = 600
RATE_MAX_KEYS = 50_000
# 보관 — 묶음·그날 키는 마지막 쓰기 + 14일, keep 이 아닌 조각(v8 전)의 머리도 14일. keep 머리는 만료 없음(078 §3.1)
KEEP_SEC = 14 * 86_400
# 상한은 누른(zlib) 바이트 — 14일 × 8MB = 112MB 가 최악(Redis maxmemory 600MB 의 시세 키를 밀어내지 않는 몫)
DAY_CAP_BYTES = 8 * 1024 * 1024
SID_CAP_BYTES = 1024 * 1024
SID_CAP_BATCHES = 3_600
WARN_EVERY_SEC = 30.0
# HTTP·인계 gzip 과 같은 레벨 6(architecture.md 성능 원칙)
ZLIB_LEVEL = 6

Outcome = Literal["stored", "dropped", "cross_site", "too_large", "bad_replay"]


def gate_ts(effective: str) -> int:
    """시행일(KST 날짜) 00:00 Asia/Seoul 의 epoch 초 — admin·attention 의 같은 함수를 쓰지 않는다(기능 간 import 금지)."""
    d = date.fromisoformat(effective)
    return int(datetime(d.year, d.month, d.day, tzinfo=KST).timestamp())


def day_key(ts: float) -> str:
    """받은 시각의 KST 날짜 — `rp:b:<YYYYMMDD>`."""
    return datetime.fromtimestamp(ts, KST).strftime("%Y%m%d")


def encode_batch(batch: Batch, at_ms: int) -> bytes:
    """리스트에 넣을 묶음 — `{"at","seq","ev"}` JSON 을 zlib 로. 피드는 풀어서 그대로 잇는다(다시 파싱하지 않는다)."""
    raw = json.dumps(
        {"at": at_ms, "seq": batch.seq, "ev": batch.ev}, separators=(",", ":")
    )
    return zlib.compress(raw.encode(), ZLIB_LEVEL)


def next_meta(
    meta: dict[str, str],
    batch: Batch,
    ip: str,
    now_ms: int,
    size: int,
    keep: bool = False,
) -> dict[str, str]:
    """이 묶음을 더한 조각 머리 전체. 첫 묶음이면 ip·dev·w·h·page·first·첫 동의 상태 `cs0` 를 이 묶음에서 정하고(keep 이면
    `keep=1` — 078 §3.1, 머리를 만든 뒤에는 바꾸지 않는다), `p` 사건은 `pages`(차례대로·중복 없음)와 지금 화면 `now` 를, `c` 사건은
    `clicks` 를 늘린다. `seq` 는 마지막으로 받은 묶음 번호, `cs` 는 마지막 묶음의 동의 상태."""
    if "first" in meta:
        base = meta
    else:
        base = {
            "ip": ip,
            "dev": batch.dev,
            "w": str(batch.w),
            "h": str(batch.h),
            "page": batch.page,
            "pages": batch.page,
            "now": batch.page,
            "first": str(now_ms),
            "n": "0",
            "bytes": "0",
            "clicks": "0",
            "cs0": batch.consent,
            **({"keep": "1"} if keep else {}),
        }
    pages = base["pages"].split(",")
    current = base["now"]
    clicks = 0
    for ev in batch.ev:
        if ev[1] == "p":
            current = str(ev[2])
            if current not in pages:
                pages.append(current)
        elif ev[1] == "c":
            clicks += 1
    return {
        **base,
        "pages": ",".join(pages),
        "now": current,
        "last": str(now_ms),
        "seq": str(batch.seq),
        "n": str(int(base["n"]) + 1),
        "bytes": str(int(base["bytes"]) + size),
        "clicks": str(int(base["clicks"]) + clicks),
        "cs": batch.consent,
    }


class RateLimiter:
    """같은 IP 의 10분 고정 창 안 600묶음까지(052 꼴) — 창이 바뀌면 표를 비우고, 키가 5만이면 새 IP 는 세지 않고 받는다.
    IP 를 읽을 수 없는 묶음은 `(없음)` 하나로 센다."""

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


class SidLocks:
    """조각마다 잠금 하나 — 기다리는 사람이 없어지면 지운다(표가 조각 수만큼 자라지 않게)."""

    def __init__(self) -> None:
        self._slots: dict[str, tuple[asyncio.Lock, list[int]]] = {}

    @contextlib.asynccontextmanager
    async def hold(self, sid: str) -> AsyncIterator[None]:
        slot = self._slots.get(sid)
        if slot is None:
            slot = self._slots[sid] = (asyncio.Lock(), [0])
        lock, users = slot
        users[0] += 1
        try:
            async with lock:
                yield
        finally:
            users[0] -= 1
            if users[0] == 0:
                del self._slots[sid]


class ReplayService:
    """시계·시행일은 테스트가 바꿀 수 있게 주입한다."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        privacy_effective: str | None = None,
        keep_effective: str | None = None,
    ) -> None:
        self._clock = clock
        self._mono = mono
        self._gate = gate_ts(privacy_effective or config.PRIVACY_V7_EFFECTIVE)
        # 078 §3.1 — 이 시각 이후 처음 만든 조각은 서비스를 끝낼 때까지 둔다
        self._keep_from = gate_ts(keep_effective or config.PRIVACY_V8_EFFECTIVE)
        self._limiter = RateLimiter()
        self._locks = SidLocks()
        self._warned_at: float | None = None
        self._feed = ReplayFeed(clock=clock)

    # --- 받기 (§3.4) ---

    async def accept(
        self,
        body: bytes | None,
        sec_fetch_site: str | None,
        client_ip_header: str | None,
        bus: RedisBus | None,
    ) -> Outcome:
        """① 교차 사이트 ② 크기(body None = 16,384바이트 초과) ③ 검사 ④ 게이트 ⑤ IP 제한 ⑥ 상한·차례를 보고 쓰기."""
        if sec_fetch_site is not None and sec_fetch_site != "same-origin":
            return "cross_site"
        if body is None or len(body) > MAX_BODY_BYTES:
            return "too_large"
        batch = parse_batch(body)
        if batch is None:
            return "bad_replay"
        now = self._clock()
        if now < self._gate:
            return "dropped"
        ip = client_ip(client_ip_header)
        if not self._limiter.allow(ip, now):
            return "dropped"
        try:
            if bus is None:
                raise ConnectionError("버스 없음")
            async with self._locks.hold(batch.sid):
                stored = await self._store(bus, batch, ip, now)
        except Exception as exc:  # Redis 가 죽어도 받기는 계속된다 — 그 묶음만 버린다
            self._warn(exc)
            return "dropped"
        return "stored" if stored else "dropped"

    async def _store(self, bus: RedisBus, batch: Batch, ip: str, now: float) -> bool:
        """§3.5 — 같거나 앞선 seq(다시 온 비콘)·조각 상한·그날 상한이면 쓰지 않는다. 조각 상한으로 버리면 머리에 `cut`."""
        day = day_key(now)
        now_ms = int(now * 1000)
        meta, used = await bus.replay_load(batch.sid, day)
        if "first" in meta and batch.seq <= int(meta["seq"]):
            return False
        blob = encode_batch(batch, now_ms)
        if "first" in meta:
            n, size = int(meta["n"]), int(meta["bytes"])
            if n >= SID_CAP_BATCHES or size + len(blob) > SID_CAP_BYTES:
                if meta.get("cut") != "1":
                    ttl = None if meta.get("keep") == "1" else KEEP_SEC
                    await bus.replay_mark_cut(batch.sid, ttl)
                return False
        if used + len(blob) > DAY_CAP_BYTES:
            return False
        keep = now >= self._keep_from
        new = next_meta(meta, batch, ip, now_ms, len(blob), keep)
        await bus.replay_append(batch.sid, new, blob, day, KEEP_SEC)
        return True

    def _warn(self, exc: Exception) -> None:
        mono = self._mono()
        if self._warned_at is not None and mono - self._warned_at < WARN_EVERY_SEC:
            return
        self._warned_at = mono
        logger.warning(
            "화면 이동 기록 묶음을 버렸다 — Redis 쓰기 실패: %s", type(exc).__name__
        )

    # --- 관리자 피드 (§3.6) ---

    async def live(self, bus: RedisBus | None) -> dict[str, Any]:
        return await self._feed.live(bus)

    async def sessions(self, ip: str | None, bus: RedisBus | None) -> dict[str, Any]:
        return await self._feed.sessions(ip, bus)

    async def session(self, sid: str, after: str | None, bus: RedisBus | None) -> bytes:
        return await self._feed.session(sid, after, bus)
