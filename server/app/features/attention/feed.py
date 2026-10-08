"""관리자 피드 `GET /admin/attention` — 창 안 날의 하루 합계를 (화면, 기기) 행으로 (스펙 052 §3.6).

창은 오늘(KST)을 넣어 거꾸로 days 날이고, 시작이 시행일보다 앞이면 시행일로 자른다. 같은 days 결과는 60초 메모리
캐시 — 관리자 화면이 자주 불러도 Redis 읽기는 days 마다 1분에 한 번이다. 실패는 500 이 아니라 상태 응답(034 규칙).
"""

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from app.core.redis_bus import RedisBus
from app.features.attention.kst import day_key, gate_ts, kst_date
from app.features.attention.models import DEVICES, METRICS, PAGES

logger = logging.getLogger("marketlens.attention")

FEED_DAYS = {"1": 1, "7": 7, "30": 30, "90": 90}
DEFAULT_DAYS = 7
FEED_CACHE_SEC = 60.0


def build_rows(days: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    """창 안 날의 해시를 더해 (화면, 기기) 행으로 — pv 0 인 행은 없고 영역은 ms 큰 순(같으면 id 순)."""
    pv: dict[tuple[str, str], int] = {}
    areas: dict[tuple[str, str], dict[str, dict[str, int]]] = {}
    for fields in days.values():
        for field, n in fields.items():
            parts = field.split("|")
            if len(parts) != 4 or parts[0] not in PAGES or parts[1] not in DEVICES:
                continue
            page, device, area, metric = parts
            key = (page, device)
            if area == "" and metric == "pv":
                pv[key] = pv.get(key, 0) + n
            elif area and metric in METRICS:
                slot = areas.setdefault(key, {}).setdefault(
                    area, {"ms": 0, "clicks": 0, "seen": 0}
                )
                slot[metric] += n
    rows = []
    for page in PAGES:
        for device in DEVICES:
            views = pv.get((page, device), 0)
            if views <= 0:
                continue
            found = areas.get((page, device), {})
            ordered = sorted(found.items(), key=lambda item: (-item[1]["ms"], item[0]))
            rows.append(
                {
                    "page": page,
                    "device": device,
                    "pv": views,
                    "areas": [{"id": area, **values} for area, values in ordered],
                }
            )
    return rows


class AttentionFeed:
    """앱 하나에 하나 — days 마다 60초 칸."""

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
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}

    async def read(self, days: str | None, bus: RedisBus | None) -> dict[str, Any]:
        """창 = 오늘(KST)을 넣어 거꾸로 days 날, 시작은 시행일로 자른다. 같은 days 결과는 60초 메모리 캐시."""
        n = FEED_DAYS.get(days or "", DEFAULT_DAYS)
        cached = self._cache.get(n)
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
                body["rows"] = build_rows(await bus.attention_days(keys))
            except Exception as exc:  # 관리자 피드는 항상 200 상태 응답(034 규칙)
                body["state"], body["code"] = "error", "redis"
                logger.warning("화면 영역 통계 읽기 실패: %s", type(exc).__name__)
        self._cache[n] = (mono + FEED_CACHE_SEC, body)
        return body
