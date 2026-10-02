"""Clarity 기본 요약 값 — `traffic`·`metrics` (스펙 035 §3.3·040 §3.3).

지표 이름·행 키는 정규화한 꼴(`metric_key`)로 고르므로 실제 CamelCase 와 문서 철자가 같게 읽힌다.
같은 이름의 지표가 둘이면 앞의 것, 값은 그 지표의 첫 행에서 읽는다. 못 알아본 칸은 null 이다.
"""

from typing import Any

from app.features.admin.clarity import (
    ROW_LIMIT,
    TEXT_LIMIT,
    is_referrer,
    loads,
    metric_items,
    pick,
    read_int,
    read_number,
    reduce_value,
    utf8,
)

NUM_OF_DAYS = 1
TRAFFIC = "traffic"  # 실제·문서 모두 `Traffic`
# Traffic 첫 행 — 응답 키 → (정규화한 행 키, 정수 여부). 035 그대로(반올림 없음)
TRAFFIC_KEYS = (
    ("sessions", "totalsessioncount", True),
    ("botSessions", "totalbotsessioncount", True),
    ("users", "distantusercount", True),
    (
        "pagesPerSession",
        "pagespersessionpercentage",
        False,
    ),  # 이름과 달리 세션당 페이지 수
)


def parse_base(content: bytes) -> dict[str, Any]:
    """기본 호출 응답 바이트 → 값 `{numOfDays, traffic, metrics}`. 모양이 틀리면 ValueError."""
    items = metric_items(loads(content))
    first: dict[str, list[Any]] = {}  # 정규화한 이름 → 그 이름의 첫 지표 행
    metrics: list[dict[str, Any]] = []
    for name, key, rows in items:
        first.setdefault(key, rows)
        if key == TRAFFIC:
            continue  # Traffic 은 이름 붙은 값으로만 (035)
        referrer = is_referrer(key)
        metrics.append(
            {
                "name": utf8(name)[:TEXT_LIMIT],
                "rows": [reduce_value(row, referrer) for row in rows[:ROW_LIMIT]],
            }
        )
    traffic_rows = first.get(TRAFFIC)
    traffic_row = _first_row(traffic_rows)
    return {
        "numOfDays": NUM_OF_DAYS,
        "traffic": None if traffic_row is None else _read(traffic_row, TRAFFIC_KEYS),
        "metrics": metrics,
    }


def _first_row(rows: list[Any] | None) -> dict[str, Any] | None:
    if rows and isinstance(rows[0], dict):
        return rows[0]
    return None


def _read(
    row: dict[str, Any] | None,
    keys: tuple[tuple[str, str, bool], ...],
    digits: int | None = None,
) -> dict[str, Any]:
    """행 → 이름 붙은 수. 정수 칸은 정수일 때만, 실수 칸은 유한할 때만(`digits` 면 그 자리까지 반올림)."""
    found = {} if row is None else pick(row, frozenset(k for _, k, _ in keys))
    out: dict[str, Any] = {}
    for name, key, integer in keys:
        if integer:
            out[name] = read_int(found.get(key))
            continue
        number = read_number(found.get(key))
        out[name] = (
            number if number is None or digits is None else round(number, digits)
        )
    return out
