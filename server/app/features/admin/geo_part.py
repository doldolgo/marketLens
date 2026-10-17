"""접속 요약의 `geo` 하위 부분 — 나라·망 종류별 날마다 센 방문자 (스펙 039 §3.6).

셈은 038 `visitors` 와 같다(`access_pairs.visitors` 가 짝마다 나라·망 종류 칸을 더해 넘긴다). 나라와 망 종류는 모두 이름으로
낸다 — 관리자가 IP 하나마다 나라·도시·망 사업자까지 보는 지금(069·070) 적은 나라를 `(기타)` 로 묶어도 숨기는 것이 없다
(사람 요청 2026-10-17). `(기타)` 는 DB-IP 에서 나라를 찾지 못한 주소만 — 끝 한 행. 응답에는 나라 두 글자·종류 이름·수만
있다(IP·AS 번호·조직 이름 없음).
"""

from typing import Any

from app.features.admin.geo_table import OTHER_COUNTRY, GeoTable

VALUE_KEYS = ("month", "loadedAt", "sinceTs", "countries", "networks", "ipv6")
Table = dict[str, list[int]]  # 이름 → [confirmed, shaped]


def empty(state: str, code: str | None) -> dict[str, Any]:
    """값 없는 하위 부분 — 게이트 전(`unconfigured`·`before_gate`)·첫 판을 받는 중(`pending`)·올린 판 없음(`error`)."""
    return {"state": state, "code": code, **dict.fromkeys(VALUE_KEYS)}


def before_gate() -> dict[str, Any]:
    return empty("unconfigured", "before_gate")


def _sorted(table: Table) -> list[list[Any]]:
    rows = [[name, c, s] for name, (c, s) in table.items() if s]
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows


def countries_rows(table: Table) -> list[list[Any]]:
    """shaped 내림차순·같으면 글자순으로 모든 나라, 나라를 찾지 못한 주소는 끝의 `(기타)` 하나(합 0 이면 뺌)."""
    rows = _sorted({k: v for k, v in table.items() if k != OTHER_COUNTRY})
    rest_c, rest_s = table.get(OTHER_COUNTRY, [0, 0])
    if rest_s:
        rows.append([OTHER_COUNTRY, rest_c, rest_s])
    return rows


def networks_rows(table: Table) -> list[list[Any]]:
    """모든 망 종류 — 같은 정렬, 0 행은 뺀다. 국내 통신사(`telecom_kr`)와 해외 통신사(`telecom`)는 늘 따로다."""
    return _sorted(table)


def ok(
    table: GeoTable,
    countries: Table,
    networks: Table,
    ipv6: int,
    since_ts: int,
) -> dict[str, Any]:
    return {
        "state": "ok",
        "code": None,
        "month": table.month,
        "loadedAt": table.loaded_at,
        "sinceTs": since_ts,
        "countries": countries_rows(countries),
        "networks": networks_rows(networks),
        "ipv6": ipv6,
    }
