"""`/history/events`·`/history/candles` 응답 공유 캐시 (013 §3.4·014 §3.6, 2026-09-28 결정).

같은 조회 키의 응답 바이트(원본·gzip)를 TTL 동안 들고, 비었거나 만료된 키에 요청이 몰리면 만들기 태스크 하나를
같이 기다린다 — 같은 분에 같은 조건을 보는 접속자 N명이 조회·빌드·인코딩·압축을 한 번 나눠 쓴다. 오류(400·404·
503)는 담지 않는다 — 다음 요청이 새로 조회한다. 앱마다 하나(`app.state.history_cache`)라 테스트 앱끼리 섞이지 않는다.
"""

import asyncio
import functools
import gzip
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass

from app.core.config import GZIP_LEVEL

EVENTS_TTL_SEC = 60.0  # 사건 목록 — 최대 60초 늦다 (013 §3.4)
# 담아 두는 사건 응답의 합(원본 + gzip) 상한 — 넘으면 가장 오래 안 쓴 키부터 버리고, 혼자 넘는 응답은 담지
# 않는다. 역프 7일 전 코인 한 건이 원본 13.7MB + gzip 1.8MB 라, 키가 분마다 바뀌어도 수집 박스(메모리 2GB)가
# 캐시로 부풀지 않게 둔 보수적인 값이다 (2026-09-28, 권장값이 없어 정함 — §7)
EVENTS_CACHE_MAX_BYTES = 64 * 1024 * 1024
CANDLES_OPEN_TTL_SEC = 30.0  # 끝이 지금 뒤인 청크 — 새 봉이 붙는다 (014 §3.6)
CANDLES_CLOSED_TTL_SEC = 600.0  # 끝이 지난 청크 — 내용이 바뀌지 않는다
CANDLES_CACHE_MAX_KEYS = 256  # 가장 오래 안 쓴 키부터 버린다
# 봉 응답의 합(원본 + gzip) 상한 — 키 수 상한과 함께 걸린다. 웹의 360봉 청크는 ≈147KB 라 다 찬 청크로만 약 220키가
# 들고, 1,440봉 청크(≈590KB)가 몰려도 api 박스(t4g.micro, 메모리 1GB)가 캐시로 부풀지 않게 둔 값이다 (014 §3.6, 2026-09-28)
CANDLES_CACHE_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Encoded:
    """응답 1개의 두 모양 — gzip 을 받는 요청에는 `gz`, 아니면 `raw`."""

    raw: bytes
    gz: bytes

    @property
    def size(self) -> int:
        return len(self.raw) + len(self.gz)


def encode_both(raw: bytes) -> Encoded:
    """원본 바이트 → 두 모양. 라우터가 인코딩과 같은 스레드에서 부른다(압축도 루프 밖에서)."""
    return Encoded(raw=raw, gz=gzip.compress(raw, compresslevel=GZIP_LEVEL))


class ResponseCache:
    """키 → (만료 시각, 응답). 채울 때 만료된 키를 버리고, 키 수·바이트 합 상한을 넘으면 가장 오래 안 쓴 키부터."""

    def __init__(
        self,
        *,
        max_keys: int | None = None,
        max_bytes: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_keys = max_keys
        self._max_bytes = max_bytes
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, Encoded]] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Future[Encoded]] = {}
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def total_bytes(self) -> int:
        return self._bytes

    async def get(
        self,
        key: Hashable,
        ttl_sec: float,
        make: Callable[[], Awaitable[Encoded]],
    ) -> Encoded:
        """살아 있는 값이면 그대로, 아니면 이 키의 만들기 태스크(없으면 새로)를 기다린다. 만들기 오류는 그대로 올라온다."""
        hit = self._entries.get(key)
        if hit is not None and hit[0] > self._clock():
            self._entries.move_to_end(key)
            return hit[1]
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(make())
            self._inflight[key] = task
            # 기다리던 요청이 끊겨도 태스크는 끝까지 돌아 캐시를 채운다 — 끝나는 순간부터 TTL 을 센다
            task.add_done_callback(functools.partial(self._settle, key, ttl_sec))
        return await asyncio.shield(task)

    def _settle(
        self, key: Hashable, ttl_sec: float, task: asyncio.Future[Encoded]
    ) -> None:
        self._inflight.pop(key, None)
        if task.cancelled() or task.exception() is not None:
            return
        self._put(key, self._clock() + ttl_sec, task.result())

    def _put(self, key: Hashable, expires: float, value: Encoded) -> None:
        now = self._clock()
        for old in [k for k, (exp, _) in self._entries.items() if exp <= now]:
            self._drop(old)
        if key in self._entries:
            self._drop(key)
        if self._max_bytes is not None and value.size > self._max_bytes:
            return
        self._entries[key] = (expires, value)
        self._bytes += value.size
        while (self._max_keys is not None and len(self._entries) > self._max_keys) or (
            self._max_bytes is not None and self._bytes > self._max_bytes
        ):
            self._drop(next(iter(self._entries)))

    def _drop(self, key: Hashable) -> None:
        _, value = self._entries.pop(key)
        self._bytes -= value.size


class HistoryCache:
    """앱마다 하나 — 사건·봉 캐시 두 칸."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.events = ResponseCache(max_bytes=EVENTS_CACHE_MAX_BYTES, clock=clock)
        self.candles = ResponseCache(
            max_keys=CANDLES_CACHE_MAX_KEYS,
            max_bytes=CANDLES_CACHE_MAX_BYTES,
            clock=clock,
        )
