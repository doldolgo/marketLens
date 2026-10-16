"""관리자 피드 셋 — `live`·`sessions`·`session` (스펙 073 §3.6).

항상 200 상태 응답(034 규칙) — Redis 가 없거나 실패하면 `state` `error`·`code` `redis`. 캐시하지 않는다(따라 보기가 2초마다
새 묶음을 받는다). 행은 조각 머리 해시에서 만들고, 머리가 만료된 sid(집합에만 남은 것)는 뺀다. `session` 의 묶음은 저장한
`{"at","seq","ev"}` JSON 을 풀어 그대로 잇는다 — 다시 파싱·직렬화하지 않고, 푸는 일과 잇는 일은 스레드에서 한다(요청 경로의 큰
JSON 은 이벤트루프에서 만들지 않는다 — architecture.md 성능 원칙).
"""

import asyncio
import json
import logging
import re
import zlib
from collections.abc import Callable, Sequence
from typing import Any

from app.core.redis_bus import RedisBus

logger = logging.getLogger("marketlens.replay")

LIVE_MS = 60_000
LIVE_ROWS = 50
SESSION_ROWS = 200
PAGE_BATCHES = 600
# `after` 는 0 이상의 정수 글자 — 그 밖은 0
_AFTER = re.compile(r"[0-9]{1,9}")


def row(sid: str, meta: dict[str, str]) -> dict[str, Any] | None:
    """조각 머리 → 피드 행. 머리가 없으면(만료) None."""
    if "first" not in meta:
        return None
    return {
        "sid": sid,
        "ip": meta["ip"],
        "dev": meta["dev"],
        "page": meta["page"],
        "pages": meta["pages"].split(","),
        "now": meta["now"],
        "first": int(meta["first"]),
        "last": int(meta["last"]),
        "n": int(meta["n"]),
        "clicks": int(meta["clicks"]),
        "w": int(meta["w"]),
        "h": int(meta["h"]),
        "cut": meta.get("cut") == "1",
    }


def _dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def page_body(
    head: dict[str, Any] | None,
    lines: Sequence[bytes],
    start: int,
    total: int,
    archived: bool,
) -> bytes:
    """`{state, code, meta, batches, next, more, archived}` 바이트 — 묶음은 이미 푼 JSON 줄을 쉼표로 잇는다(다시 파싱하지 않는다).
    078 의 수집기 archive 피드도 같은 꼴로 쓴다."""
    nxt = start + len(lines)
    return b"".join(
        (
            b'{"state":"ok","code":null,"meta":',
            _dumps(head).encode(),
            b',"batches":[',
            b",".join(lines),
            b'],"next":',
            str(nxt).encode(),
            b',"more":',
            b"true" if nxt < total else b"false",
            b',"archived":',
            b"true" if archived else b"false",
            b"}",
        )
    )


def session_body(
    meta: dict[str, str], sid: str, blobs: Sequence[bytes], start: int, total: int
) -> bytes:
    """`{state, code, meta, batches, next, more, archived}` 바이트 — 묶음은 푼 JSON 을 쉼표로 잇는다. 머리가 없으면 묶음도 없다.
    머리에 `arch` 가 있고 Redis 묶음이 비었으면(14일이 지나 S3 에만 있다) `archived` true — 관리자는 수집기 archive 로 다시 받는다(078 §3.3)."""
    head = row(sid, meta)
    if head is None:
        blobs, total = [], 0
    archived = head is not None and "arch" in meta and total == 0
    return page_body(
        head, [zlib.decompress(blob) for blob in blobs], start, total, archived
    )


class ReplayFeed:
    """앱 하나에 하나 — 시계만 든다."""

    def __init__(self, *, clock: Callable[[], float]) -> None:
        self._clock = clock

    async def _rows(self, bus: RedisBus, sids: list[str]) -> list[dict[str, Any]]:
        metas = await bus.replay_metas(sids)
        rows = (row(sid, meta) for sid, meta in zip(sids, metas, strict=True))
        return [r for r in rows if r is not None]

    async def live(self, bus: RedisBus | None) -> dict[str, Any]:
        """last 가 60초 안인 조각, 최근 순 50개 + 서버 시각 `now`(ms)."""
        now_ms = int(self._clock() * 1000)
        body: dict[str, Any] = {"state": "ok", "code": None, "now": now_ms, "rows": []}
        try:
            if bus is None:
                raise ConnectionError("버스 없음")
            sids = await bus.replay_recent(now_ms - LIVE_MS, LIVE_ROWS)
            body["rows"] = await self._rows(bus, sids)
        except Exception as exc:  # 관리자 피드는 항상 200 상태 응답(034 규칙)
            body["state"], body["code"] = "error", "redis"
            logger.warning("화면 이동 기록 읽기 실패: %s", type(exc).__name__)
        return body

    async def sessions(self, ip: str | None, bus: RedisBus | None) -> dict[str, Any]:
        """ip 가 있으면 그 IP 의 조각을 first 늦은 순, 없으면 모든 IP 의 조각을 last 늦은 순 — 200개까지.
        ip 꼴 검사(400)는 경로가 먼저 한다."""
        body: dict[str, Any] = {"state": "ok", "code": None, "ip": ip, "rows": []}
        try:
            if bus is None:
                raise ConnectionError("버스 없음")
            if ip is None:
                sids = await bus.replay_recent(None, SESSION_ROWS)
            else:
                sids = await bus.replay_ip(ip, SESSION_ROWS)
            body["rows"] = await self._rows(bus, sids)
        except Exception as exc:
            body["state"], body["code"] = "error", "redis"
            logger.warning("화면 이동 기록 읽기 실패: %s", type(exc).__name__)
        return body

    async def session(self, sid: str, after: str | None, bus: RedisBus | None) -> bytes:
        """after(기본 0)번째 묶음부터 600개. sid 꼴 검사(400)는 경로가 먼저 한다."""
        start = int(after) if after is not None and _AFTER.fullmatch(after) else 0
        try:
            if bus is None:
                raise ConnectionError("버스 없음")
            meta, blobs, total = await bus.replay_batches(sid, start, PAGE_BATCHES)
            return await asyncio.to_thread(session_body, meta, sid, blobs, start, total)
        except Exception as exc:
            logger.warning("화면 이동 기록 읽기 실패: %s", type(exc).__name__)
        failed = {
            "state": "error",
            "code": "redis",
            "meta": None,
            "batches": [],
            "next": start,
            "more": False,
            "archived": False,
        }
        return _dumps(failed).encode()
