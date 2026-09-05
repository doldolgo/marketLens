"""Redis Stream `ticks` 클라이언트 — 연결·XADD·XRANGE·XDEL (스펙 009 §3.4).

redis 라이브러리를 import 하는 곳은 이 모듈뿐이다. 인계기·flusher 는 아래 메서드에만 의존하고,
테스트는 fakeredis 의 asyncio 클라이언트를 같은 자리에 꽂는다(Redis 를 띄우지 않는다).
명령 자동 재시도는 끈다 — 실패는 그 자리에서 호출자(§3.3 버림·§3.5 회차 실패)가 처리한다.
"""

import logging
from dataclasses import dataclass

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

logger = logging.getLogger("marketlens.redis")

STREAM_KEY = "ticks"
MAXLEN = 86_400  # 24시간 안전 상한 — 평상시 60건 안팎, Influx 가 하루 넘게 막혔을 때만 잘린다
PAGE = 1_000  # XRANGE 페이지·XDEL 묶음 크기
CONNECT_TIMEOUT_SEC = 2.0
SOCKET_TIMEOUT_SEC = 5.0


@dataclass(frozen=True)
class StreamEntry:
    """스트림 엔트리 1건 = 틱 1개. `data` 는 gzip JSON 그대로."""

    id: str
    ts: int
    data: bytes


class RedisTickStream:
    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> "RedisTickStream":
        """생성은 연결하지 않는다(lazy). 명령마다 필요하면 다시 연결한다."""
        return cls(
            aioredis.from_url(
                url,
                socket_connect_timeout=CONNECT_TIMEOUT_SEC,
                socket_timeout=SOCKET_TIMEOUT_SEC,
                retry=Retry(NoBackoff(), 0),
            )
        )

    async def ping(self) -> bool:
        """연결 확인 — 실패해도 예외 없이 False (기동 시 경고 1줄용)."""
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def add(self, ts: int, data: bytes) -> str:
        """`XADD ticks MAXLEN ~ 86400 * ts <ts> data <gzip JSON>` — 실패는 예외."""
        entry_id = await self._client.xadd(
            STREAM_KEY, {"ts": ts, "data": data}, maxlen=MAXLEN, approximate=True
        )
        return _text(entry_id)

    async def read_all(self) -> list[StreamEntry]:
        """`XRANGE ticks - +` 전량 — PAGE 건씩 페이지를 넘겨 읽는다. 실패는 예외."""
        out: list[StreamEntry] = []
        start = "-"
        while True:
            page = await self._client.xrange(STREAM_KEY, start, "+", count=PAGE)
            for raw_id, fields in page:
                out.append(
                    StreamEntry(
                        id=_text(raw_id),
                        ts=int(fields[b"ts"]),
                        data=bytes(fields[b"data"]),
                    )
                )
            if len(page) < PAGE:
                return out
            start = "(" + out[-1].id  # 마지막 ID 제외(exclusive) 다음 페이지

    async def delete(self, ids: list[str]) -> int:
        """읽은 ID 만 지운다 — PAGE 개씩 XDEL. 지운 수를 돌려주고 실패는 예외."""
        removed = 0
        for i in range(0, len(ids), PAGE):
            removed += int(await self._client.xdel(STREAM_KEY, *ids[i : i + PAGE]))
        return removed

    async def length(self) -> int:
        return int(await self._client.xlen(STREAM_KEY))

    async def aclose(self) -> None:
        await self._client.aclose()


def _text(v: bytes | str) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)
