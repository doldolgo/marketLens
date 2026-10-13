"""Clarity 기기·유입 차원 호출 — `Device`·`Browser`·`OS` 와 `Channel`·`Source`·`Country/Region` 세션 목록 (스펙 068 §3.1·§3.2).

호출마다 차원 셋을 주고(`numOfDays=1` — 부른 때 직전 24시간) 받은 Traffic 행(차원 조합마다 한 행)의 세션을 차원 값마다
더해 `[[이름, 세션], …]` 목록 셋을 만든다. 040 의 나라 목록과 같은 규칙이다 — 이름은 글자 값(주소 꼴은 이름이 아니라
버린다), 세션은 정수, 세션 내림차순·같으면 이름순 20행, 이름은 200자까지. Traffic 지표가 없거나 한 행도 못 알아보면
null, 행이 0개면 `[]`. 차원 행 키의 철자는 아직 확인 전이라 정규화한 이름으로 찾고(`Country/Region` → `countryregion`,
`country` 도 받는다), 행이 있는데 차원 키를 가진 행이 하나도 없으면 받은 자료가 틀린 것(ValueError — `bad_data`)이다.
"""

from collections.abc import Callable
from typing import Any

from app.features.admin.clarity import (
    TEXT_LIMIT,
    is_name,
    loads,
    metric_items,
    pick,
    read_int,
    utf8,
)

TRAFFIC = "traffic"
LIST_LIMIT = 20
SESSIONS = "totalsessioncount"
# 목록 이름 → 정규화한 차원 행 키 후보(앞의 것부터)
DEVICE_DIMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("device", ("device",)),
    ("browser", ("browser",)),
    ("os", ("os",)),
)
CHANNEL_DIMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("channel", ("channel",)),
    ("source", ("source",)),
    ("country", ("countryregion", "country")),
)
DEVICE_PARAMS = {
    "numOfDays": "1",
    "dimension1": "Device",
    "dimension2": "Browser",
    "dimension3": "OS",
}
CHANNEL_PARAMS = {
    "numOfDays": "1",
    "dimension1": "Channel",
    "dimension2": "Source",
    "dimension3": "Country/Region",
}
# 호출 둘 — (Redis `admin:clarity:dims` 의 칸, 인자, 목록 셋)
DIMS_CALLS = (
    ("device", DEVICE_PARAMS, DEVICE_DIMS),
    ("channel", CHANNEL_PARAMS, CHANNEL_DIMS),
)
# `dims` 하위 부분·날별 기록의 목록 여섯 — 기기 호출 셋 뒤 유입 호출 셋
DIMS_KEYS = tuple(name for name, _ in DEVICE_DIMS + CHANNEL_DIMS)


def parse_dims(
    dims: tuple[tuple[str, tuple[str, ...]], ...],
) -> Callable[[bytes], dict[str, Any]]:
    """차원 셋 → 응답 바이트를 값 `{목록 이름: [[이름, 세션], …] | [] | None}` 으로 바꾸는 함수. 모양이 틀리면 ValueError."""
    wanted = frozenset(key for _, keys in dims for key in keys)

    def parse(content: bytes) -> dict[str, Any]:
        items = metric_items(loads(content))
        rows_in = sum(len(rows) for _, _, rows in items)
        if rows_in and not any(
            _has_dim(row, wanted) for _, _, rows in items for row in rows
        ):
            raise ValueError("차원 키를 가진 행이 없다")  # 차원 키 철자가 짐작과 다르다
        traffic = next((rows for _, key, rows in items if key == TRAFFIC), None)
        return {name: _sessions(traffic, keys) for name, keys in dims}

    return parse


def _has_dim(row: Any, wanted: frozenset[str]) -> bool:
    return isinstance(row, dict) and any(
        isinstance(v, str) for v in pick(row, wanted).values()
    )


def _sessions(rows: list[Any] | None, keys: tuple[str, ...]) -> list[list[Any]] | None:
    """Traffic 행 → 차원 값마다 세션 합 20행. 지표가 없거나 한 행도 못 알아보면 None, 행 0개면 []."""
    if rows is None:
        return None
    if not rows:
        return []
    wanted = frozenset((*keys, SESSIONS))
    sums: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        found = pick(row, wanted)
        sessions = read_int(found.get(SESSIONS))
        name = next((v for v in (found.get(k) for k in keys) if is_name(v)), None)
        if sessions is None or name is None:
            continue
        sums[name] = sums.get(name, 0) + sessions
    if not sums:
        return None
    ordered = sorted(sums.items(), key=lambda pair: (-pair[1], pair[0]))
    return [[utf8(name)[:TEXT_LIMIT], n] for name, n in ordered[:LIST_LIMIT]]
