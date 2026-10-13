"""관리자 피드 `GET /admin/attention` — 창 안 날의 하루 합계를 (화면, 기기) 행으로 (스펙 052 §3.6, 061 §3.6).

창은 오늘(KST)을 넣어 거꾸로 days 날이고, 시작이 시행일보다 앞이면 시행일로 자른다. 행마다 영역(052)·떠난 곳·깊이(061)를,
`page` 를 주면 그 화면 행만 요소별 값과 함께 준다. 같은 (days, page) 결과는 60초 메모리 캐시 — 관리자 화면이 자주 불러도
Redis 읽기는 (days, page) 마다 1분에 한 번이다. 실패는 500 이 아니라 상태 응답(034 규칙).
"""

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from app.core.redis_bus import RedisBus
from app.features.attention.kst import day_key, gate_ts, kst_date
from app.features.attention.models import (
    DEPTH_MAX,
    DEPTH_PAGES,
    DEVICES,
    ELEMENT_METRICS,
    METRICS,
    PAGES,
)

logger = logging.getLogger("marketlens.attention")

FEED_DAYS = {"1": 1, "7": 7, "30": 30, "90": 90}
DEFAULT_DAYS = 7
FEED_CACHE_SEC = 60.0


# 요소 목록은 행마다 (클릭, 보인 시간) 큰 순 이만큼까지
MAX_FEED_ELEMENTS = 300
_DEPTH = {f"d{n}": n for n in range(DEPTH_MAX + 1)}
_ELEMENT_KEYS = dict(zip(ELEMENT_METRICS, ("clicks", "hoverMs", "visMs"), strict=True))


class _Row:
    """(화면, 기기) 하나의 창 합계."""

    def __init__(self) -> None:
        self.pv = 0
        self.areas: dict[str, dict[str, int]] = {}
        self.exits: dict[str, int] = {}
        self.depth = [0] * (DEPTH_MAX + 1)
        self.elements: dict[tuple[str, str], dict[str, int]] = {}

    def add(self, area: str, metric: str, n: int) -> None:
        if area == "" and metric == "pv":
            self.pv += n
        elif area == "" and metric in _DEPTH:
            self.depth[_DEPTH[metric]] += n
        elif area and metric in METRICS:
            slot = self.areas.setdefault(area, {"ms": 0, "clicks": 0, "seen": 0})
            slot[metric] += n
        elif area and metric == "x":
            self.exits[area] = self.exits.get(area, 0) + n

    def add_element(self, area: str, key: str, metric: str, n: int) -> None:
        slot = self.elements.setdefault(
            (area, key), {"clicks": 0, "hoverMs": 0, "visMs": 0}
        )
        slot[_ELEMENT_KEYS[metric]] += n


def build_rows(
    days: dict[str, dict[str, int]], page: str | None = None
) -> list[dict[str, Any]]:
    """창 안 날의 해시를 더해 (화면, 기기) 행으로 — pv 0 인 행은 없고 영역은 ms 큰 순(같으면 id 순), 떠난 곳은 n 큰 순
    (같으면 id 순), 깊이는 정적 페이지만 11칸(대시보드는 null). page 를 주면 그 화면 행만, 행마다 요소를 (클릭, 보인 시간)
    큰 순 300개까지(같으면 (영역, 열쇠) 순)."""
    rows_by: dict[tuple[str, str], _Row] = {}
    for fields in days.values():
        for field, n in fields.items():
            parts = field.split("|")
            if parts[0] not in PAGES or (page is not None and parts[0] != page):
                continue
            if len(parts) < 4 or parts[1] not in DEVICES:
                continue
            row = rows_by.setdefault((parts[0], parts[1]), _Row())
            if len(parts) == 4:
                row.add(parts[2], parts[3], n)
            elif (
                page is not None
                and len(parts) == 6
                and parts[2]
                and parts[3] == "e"
                and parts[4]
                and parts[5] in _ELEMENT_KEYS
            ):
                row.add_element(parts[2], parts[4], parts[5], n)
    rows = []
    for name in PAGES:
        for device in DEVICES:
            row = rows_by.get((name, device))
            if row is None or row.pv <= 0:
                continue
            areas = sorted(
                row.areas.items(), key=lambda item: (-item[1]["ms"], item[0])
            )
            exits = sorted(row.exits.items(), key=lambda item: (-item[1], item[0]))
            out: dict[str, Any] = {
                "page": name,
                "device": device,
                "pv": row.pv,
                "areas": [{"id": area, **values} for area, values in areas],
                "exits": [[area, n] for area, n in exits],
                "depth": row.depth if name in DEPTH_PAGES else None,
            }
            if page is not None:
                ranked = sorted(
                    row.elements.items(),
                    key=lambda item: (-item[1]["clicks"], -item[1]["visMs"], item[0]),
                )[:MAX_FEED_ELEMENTS]
                out["elements"] = [
                    {"area": area, "key": key, **values}
                    for (area, key), values in ranked
                ]
            rows.append(out)
    return rows


class AttentionFeed:
    """앱 하나에 하나 — (days, page) 마다 60초 칸."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        mono: Callable[[], float],
        effective: str,
    ) -> None:
        self._clock = clock
        self._mono = mono
        self._effective = effective
        self._gate = gate_ts(effective)
        self._cache: dict[tuple[int, str | None], tuple[float, dict[str, Any]]] = {}

    async def read(
        self, days: str | None, page: str | None, bus: RedisBus | None
    ) -> dict[str, Any]:
        """창 = 오늘(KST)을 넣어 거꾸로 days 날, 시작은 시행일로 자른다. page 가 목록 밖이면 없는 것과 같다.
        같은 (days, page) 결과는 60초 메모리 캐시."""
        n = FEED_DAYS.get(days or "", DEFAULT_DAYS)
        screen = page if page in PAGES else None
        cached = self._cache.get((n, screen))
        mono = self._mono()
        if cached is not None and mono < cached[0]:
            return cached[1]
        now = self._clock()
        today = kst_date(now)
        gate_day = date.fromisoformat(self._effective)
        start = max(today - timedelta(days=n - 1), gate_day)
        body: dict[str, Any] = {
            "state": "ok",
            "code": None,
            "gateAt": self._gate * 1000,
            "days": n,
            "from": start.isoformat(),
            "to": today.isoformat(),
            "fetchedAt": int(now * 1000),
            "rows": [],
        }
        if today < gate_day:
            body["state"] = "before_gate"
        else:
            keys = [
                day_key(start + timedelta(days=i))
                for i in range((today - start).days + 1)
            ]
            try:
                if bus is None:
                    raise ConnectionError("버스 없음")
                body["rows"] = build_rows(await bus.attention_days(keys), screen)
            except Exception as exc:  # 관리자 피드는 항상 200 상태 응답(034 규칙)
                body["state"], body["code"] = "error", "redis"
                logger.warning("화면 영역 통계 읽기 실패: %s", type(exc).__name__)
        self._cache[(n, screen)] = (mono + FEED_CACHE_SEC, body)
        return body
