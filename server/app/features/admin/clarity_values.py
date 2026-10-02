"""Clarity 기본 요약 값 — `traffic`·`summary`·`countries`·`metrics` (스펙 040 §3.3).

지표 이름·행 키는 정규화한 꼴(`metric_key`)로 고르므로 실제 CamelCase(`ScrollDepth`)와 문서 철자(`Scroll Depth`)가
같게 읽힌다. 같은 이름의 지표가 둘이면 앞의 것, 값은 그 지표의 첫 행에서 읽는다(`countries` 만 모든 행).
못 알아본 칸은 null 이고 객체 모양은 그대로다. 세션이 있는 응답의 값 꼴을 아직 몰라 받은 그대로(`metrics`)도 남긴다 —
정규화한 지표까지 행 20개(주소 줄인 뒤)로 남겨 짐작을 대조한다.
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
SCROLL = "scrolldepth"
ENGAGEMENT = "engagementtime"
# 불만 신호 여섯 — 응답 키 → 정규화한 지표 이름
SIGNALS = (
    ("deadClick", "deadclickcount"),
    ("rageClick", "rageclickcount"),
    ("excessiveScroll", "excessivescroll"),
    ("quickback", "quickbackclick"),
    ("scriptError", "scripterrorcount"),
    ("errorClick", "errorclickcount"),
)
# 신호 값 — 응답 키 → (정규화한 행 키, 정수 여부)
SIGNAL_KEYS = (
    ("sessions", "sessionscount", True),
    ("sessionPct", "sessionswithmetricpercentage", False),
    ("pageViews", "pagesviews", True),
    ("count", "subtotal", True),
)
COUNTRY_METRICS = frozenset({"country", "countryregion"})
COUNTRY_NAME_KEYS = ("country", "countryregion", "name")
COUNTRY_SESSION_KEYS = ("sessionscount", "totalsessioncount", "sessions", "count")
COUNTRY_LIMIT = 20


def parse_base(content: bytes) -> dict[str, Any]:
    """기본 호출 응답 바이트 → 값 `{numOfDays, traffic, summary, countries, metrics}`. 모양이 틀리면 ValueError."""
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
        "summary": _summary(first),
        "countries": _countries(
            next((first[k] for k in first if k in COUNTRY_METRICS), None)
        ),
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


def _summary(first: dict[str, list[Any]]) -> dict[str, Any]:
    """`{scrollDepth, totalSec, activeSec, signals}` — 늘 이 모양, 실수는 소수 둘째 자리까지."""
    scroll = _read(
        _first_row(first.get(SCROLL)), (("v", "averagescrolldepth", False),), 2
    )
    engagement = _read(
        _first_row(first.get(ENGAGEMENT)),
        (("totalSec", "totaltime", False), ("activeSec", "activetime", False)),
        2,
    )
    return {
        "scrollDepth": scroll["v"],
        "totalSec": engagement["totalSec"],
        "activeSec": engagement["activeSec"],
        "signals": {
            name: _read(_first_row(first.get(key)), SIGNAL_KEYS, 2)
            for name, key in SIGNALS
        },
    }


def _countries(rows: list[Any] | None) -> list[list[Any]] | None:
    """`[[이름, 세션], …]` 세션 내림차순·같으면 이름순 20행. 지표가 없거나 한 행도 못 알아보면 None, 행 0개면 []."""
    if rows is None:
        return None
    if not rows:
        return []
    found: list[tuple[str, int]] = []
    for row in rows:
        if isinstance(row, dict):
            known = _country(row)
            if known is not None:
                found.append(known)
    if not found:
        return None
    found.sort(key=lambda pair: (-pair[1], pair[0]))
    return [
        [utf8(name)[:TEXT_LIMIT], sessions] for name, sessions in found[:COUNTRY_LIMIT]
    ]


def _country(row: dict[str, Any]) -> tuple[str, int] | None:
    """행 → (이름, 세션). 이름은 아는 키의 글자 값, 없으면 행의 글자 값이 하나뿐일 때 그 값 — 숫자로 읽히는 글자
    (`"5"`)는 수라서 글자 값으로 세지 않는다. 세션은 아는 키 중 처음 있는 정수."""
    found = pick(row, frozenset(COUNTRY_NAME_KEYS + COUNTRY_SESSION_KEYS))
    sessions = next(
        (
            n
            for n in (read_int(found.get(k)) for k in COUNTRY_SESSION_KEYS)
            if n is not None
        ),
        None,
    )
    if sessions is None:
        return None
    for key in COUNTRY_NAME_KEYS:
        value = found.get(key)
        if isinstance(value, str):
            return value, sessions
    texts = [v for v in row.values() if isinstance(v, str) and read_number(v) is None]
    return (texts[0], sessions) if len(texts) == 1 else None
