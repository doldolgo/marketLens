"""Clarity 페이지×기기 묶음 — `URL`·`Device` 차원 응답을 (페이지 종류, 기기) 칸으로 (스펙 040 §3.4).

주소는 페이지 종류를 정하는 데만 메모리에서 쓰고 남기지 않는다 — 값은 칸 40개 이하의 수뿐이다.
묶는 지표는 Traffic·ScrollDepth·EngagementTime·불만 신호 여섯이고, 그 밖 지표는 행 수만 센다.
칸의 세션은 Traffic 행 세션의 합, 나머지는 칸 안 행들의 세션 가중 평균이다 — 행의 가중치는 같은 (주소, 기기)
글자의 Traffic 세션, 없으면 그 행의 `sessionsCount`, 그것도 없으면 1. 차원 행 키의 철자는 아직 확인 전이라
정규화한 이름(`url`·`device`)으로 찾고, 행이 있는데 둘을 함께 가진 행이 하나도 없으면 받은 자료가 틀린 것(ValueError)이다.
"""

from typing import Any

from app.features.admin.clarity import (
    TAB_IDS,
    address_parts,
    first_tab,
    is_ours,
    loads,
    metric_items,
    metric_key,
    pick,
    read_int,
    read_number,
    split_path,
)

NUM_OF_DAYS = 3
ROW_CAP = 1000  # Clarity 가 지표마다 주는 행 상한 — 닿으면 그 지표는 잘렸을 수 있다
PAGES = (
    ("landing",)
    + tuple(f"app:{tab}" for tab in TAB_IDS)
    + ("app:other", "privacy", "other")
)
DEVICES = ("mobile", "tablet", "desktop", "other")
DEVICE_OF = {
    "mobile": "mobile",
    "tablet": "tablet",
    "pc": "desktop",
    "desktop": "desktop",
}
TRAFFIC = "traffic"
# 평균 칸 — 칸 키 → (정규화한 지표 이름, 정규화한 행 키)
AVERAGES = (
    ("scrollDepth", "scrolldepth", "averagescrolldepth"),
    ("totalSec", "engagementtime", "totaltime"),
    ("activeSec", "engagementtime", "activetime"),
    ("deadClickPct", "deadclickcount", "sessionswithmetricpercentage"),
    ("rageClickPct", "rageclickcount", "sessionswithmetricpercentage"),
    ("excessiveScrollPct", "excessivescroll", "sessionswithmetricpercentage"),
    ("quickbackPct", "quickbackclick", "sessionswithmetricpercentage"),
    ("scriptErrorPct", "scripterrorcount", "sessionswithmetricpercentage"),
    ("errorClickPct", "errorclickcount", "sessionswithmetricpercentage"),
)
GROUPED = frozenset({TRAFFIC} | {metric for _, metric, _ in AVERAGES})
_DIMENSIONS = frozenset({"url", "device"})
_TRAFFIC_KEY = frozenset({"totalsessioncount"})


def page_kind(url: str) -> str:
    """주소 → 페이지 종류. 우리 호스트(스킴 없는 꼴 포함)이거나 호스트 없이 `/` 로 시작하는 주소에서
    `/` → landing, `/app/` → `app:<탭>`, `/privacy` → privacy, 그 밖은 other."""
    parts = address_parts(url)
    if parts is None:
        return "other"
    authority, rest = parts
    if authority is not None and not is_ours(authority):
        return "other"
    path, query = split_path(rest)
    if path == "" and authority is not None:
        path = (
            "/"  # `https://kimptrack.com`·`kimptrack.com?x` — 호스트 뒤 빈 경로는 `/`
        )
    if path == "/":
        return "landing"
    if path == "/privacy":
        return "privacy"
    if path != "/app/":
        return "other"
    tab = first_tab(query)
    if tab is None:
        return "app:spread"  # 기본 탭 — 002 는 기본값이면 키를 지운다
    return f"app:{tab}" if tab in TAB_IDS else "app:other"


def device_kind(device: str) -> str:
    """`Mobile`·`Tablet`·`PC`(·`Desktop`) → mobile·tablet·desktop, 그 밖(`Email`·`Other`·빈 값)은 other."""
    return DEVICE_OF.get(metric_key(device), "other")


def parse_pages(content: bytes) -> dict[str, Any]:
    """묶음 호출 응답 바이트 → 값 `{numOfDays, rowsIn, rowLimitHit, groups}`. 모양이 틀리면 ValueError."""
    items = metric_items(loads(content))
    rows_in = sum(len(rows) for _, _, rows in items)
    row_limit_hit = any(len(rows) >= ROW_CAP for _, _, rows in items)
    first: dict[str, list[Any]] = {}  # 묶는 지표 — 같은 이름이면 앞의 것
    for _, key, rows in items:
        if key in GROUPED:
            first.setdefault(key, rows)
    dimensioned = False  # url·device 를 함께 가진 행이 하나라도 있는가 — 모든 지표에서
    for _, _, rows in items:
        if any(_dims(row) is not None for row in rows):
            dimensioned = True
            break
    if rows_in and not dimensioned:
        raise ValueError("url·device 를 가진 행이 없다")  # 차원 키 철자가 짐작과 다르다
    return {
        "numOfDays": NUM_OF_DAYS,
        "rowsIn": rows_in,
        "rowLimitHit": row_limit_hit,
        "groups": _groups(first),
    }


def _dims(row: Any) -> tuple[str, str] | None:
    """행 → (주소, 기기) 글자. 둘 중 하나라도 글자 값이 없으면 None."""
    if not isinstance(row, dict):
        return None
    found = pick(row, _DIMENSIONS)
    url, device = found.get("url"), found.get("device")
    if isinstance(url, str) and isinstance(device, str):
        return url, device
    return None


def _groups(first: dict[str, list[Any]]) -> list[dict[str, Any]]:
    kinds: dict[
        tuple[str, str], tuple[str, str]
    ] = {}  # (주소, 기기) 글자 → 칸 — 주소마다 한 번만 판정

    def cell_of(dims: tuple[str, str]) -> tuple[str, str]:
        cell = kinds.get(dims)
        if cell is None:
            cell = kinds[dims] = (page_kind(dims[0]), device_kind(dims[1]))
        return cell

    # 같은 (주소, 기기) 의 Traffic 세션 — 칸의 세션 합이자 다른 지표 행의 가중치
    traffic: dict[tuple[str, str], int] = {}
    sessions: dict[tuple[str, str], int] = {}
    for row in first.get(TRAFFIC, ()):
        dims = _dims(row)
        if dims is None:
            continue
        count = read_int(pick(row, _TRAFFIC_KEY).get("totalsessioncount"))
        if count is None:
            continue
        traffic[dims] = traffic.get(dims, 0) + count
        cell = cell_of(dims)
        sessions[cell] = sessions.get(cell, 0) + count
    # 평균 칸 — (칸, 칸 키) → [가중 합, 가중치 합]
    sums: dict[tuple[tuple[str, str], str], list[float]] = {}
    for name, metric, value_key in AVERAGES:
        wanted = frozenset({value_key, "sessionscount"})
        for row in first.get(metric, ()):
            dims = _dims(row)
            if dims is None:
                continue
            found = pick(row, wanted)
            value = read_number(found.get(value_key))
            if value is None:
                continue
            weight = traffic.get(dims)
            if weight is None:
                weight = read_int(found.get("sessionscount"))
            if weight is None:
                weight = 1
            acc = sums.setdefault((cell_of(dims), name), [0.0, 0.0])
            acc[0] += value * weight
            acc[1] += weight
    groups = []
    for page in PAGES:
        for device in DEVICES:
            cell = (page, device)
            values: dict[str, Any] = {"sessions": sessions.get(cell)}
            for name, _, _ in AVERAGES:
                acc = sums.get((cell, name))
                values[name] = (
                    None if acc is None or acc[1] == 0 else round(acc[0] / acc[1], 2)
                )
            if any(v is not None for v in values.values()):
                groups.append({"page": page, "device": device, **values})
    return groups
