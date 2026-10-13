"""Clarity 날별 기록 — KST 날의 칸 `admin:clarity:day:<YYYYMMDD>` 의 값과 `/admin/clarity/history` 의 행 (스펙 068 §3.2).

칸의 값은 그날 마지막으로 성공한 기본 호출의 정규화 값(`traffic`·`summary`·`countries` — `metrics` 는 주소가 실려 넣지
않는다)과 그날 성공한 차원 값(`dims` — 목록 여섯, 그날 성공이 없는 호출의 목록은 null)과 `fetchedAt`(기본 성공 시각 ms)이다.
뜻은 '그날 마지막으로 받은 직전 24시간 값' 이다 — 달력 하루 합이 아니다. 만료는 그날 끝(다음 날 00:00 KST) + 90일.
"""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.features.admin.clarity import loads

KST_SEC = 9 * 3600
DAY_SEC = 86_400
KEEP_DAYS = 90
# 창 — 오늘(KST)을 넣어 거꾸로 이만큼의 날. 목록 밖·없음은 30
HISTORY_DAYS = {"7": 7, "30": 30, "90": 90}
HISTORY_DEFAULT = 30
HISTORY_REFRESH_SEC = 60
# `days` 는 어느 상태에도 싣는다(고른 창). `rows` 는 값 키라 ok 가 아니면 null
HISTORY_KEYS = ("days", "rows")
VALUE_KEYS = ("traffic", "summary", "countries")


def kst_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000 + KST_SEC, UTC).date()


def day_of(ms: int) -> str:
    """시각(ms)이 든 KST 날 — 칸 이름의 `YYYYMMDD`."""
    return kst_date(ms).strftime("%Y%m%d")


def expire_at(day: str) -> int:
    """그 KST 날의 끝(다음 날 00:00 KST) + 90일, epoch 초 — 덮어써도 같은 값이라 만료가 밀리지 않는다."""
    d = datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC)
    return int(d.timestamp()) - KST_SEC + (1 + KEEP_DAYS) * DAY_SEC


def day_text(fetched_at: int, values: dict[str, Any], dims: dict[str, Any]) -> str:
    """칸 하나의 JSON — 다시 쓸 수 없는 값이면 ValueError(쓰지 않는다)."""
    text = json.dumps(
        {
            "fetchedAt": fetched_at,
            **{k: values.get(k) for k in VALUE_KEYS},
            "dims": dims,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    text.encode("utf-8")
    return text


def window_days(now_ms: int, days: int) -> list[str]:
    """오늘(KST)을 넣어 거꾸로 `days` 날 — 오름차순 `YYYYMMDD`."""
    today = kst_date(now_ms)
    return [
        (today - timedelta(days=back)).strftime("%Y%m%d")
        for back in range(days - 1, -1, -1)
    ]


def history_rows(days: list[str], texts: list[str | None]) -> list[dict[str, Any]]:
    """날 오름차순 행 `{date, fetchedAt, traffic, summary, countries, dims}` — 기록이 없거나 모양이 틀린 날은 빠진다."""
    rows = []
    for day, text in zip(days, texts, strict=True):
        data = _record(text)
        if data is None:
            continue
        fetched = data.get("fetchedAt")
        rows.append(
            {
                "date": datetime.strptime(day, "%Y%m%d").date().isoformat(),
                "fetchedAt": fetched if type(fetched) is int else None,
                "traffic": _kind(data.get("traffic"), dict),
                "summary": _kind(data.get("summary"), dict),
                "countries": _kind(data.get("countries"), list),
                "dims": _kind(data.get("dims"), dict),
            }
        )
    return rows


def _record(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        data = loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _kind(value: Any, kind: type) -> Any:
    return value if isinstance(value, kind) else None
