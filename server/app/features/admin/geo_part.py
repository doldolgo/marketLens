"""접속 요약의 `geo` 하위 부분 — 나라·망 종류별 날마다 센 방문자와 적은 나라 묶기 (스펙 039 §3.6).

셈은 038 `visitors` 와 같다(`access_pairs.visitors` 가 짝마다 나라·망 종류 칸을 더해 넘긴다). 창 안 어느 KST 하루에도
그 나라의 짝(shaped)이 3 에 못 미친 나라는 이름으로 내지 않고 `(기타)` 에 합친다 — 짝 열쇠가 날마다 바뀌어 날을 넘어
같은 사람인지 알 수 없으므로, 창 합으로 재면 한 사람이 사흘 와도 나라가 드러난다. 판정은 모양(상한)의 하루 최대 하나라
한 나라가 confirmed·shaped 가운데 한 값에서만 보이는 일이 없다. 그 창에서 KR 이 이름으로 남지 않으면 `telecom_kr` 을
`telecom` 에 합친다 — 숨긴 나라를 망 종류로 비켜 보지 않게. 망 종류 행도 같은 기준(하루 최대 짝 3)으로 `(기타)` 에 합친다.
응답에는 나라 두 글자·종류 이름·수만 있다(IP·AS 번호·조직 이름 없음).
"""

from typing import Any

from app.features.admin.geo_kinds import NET_TELECOM_ALL, TELECOM, TELECOM_KR
from app.features.admin.geo_table import OTHER_COUNTRY, GeoTable

K = 3  # 어느 하루에도 짝이 이 수보다 적었던 나라는 이름으로 내지 않는다
TOP = 20
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


def countries_rows(table: Table, peaks: dict[str, int]) -> list[list[Any]]:
    """shaped 내림차순·같으면 글자순, 이름으로 두는 것은 하루 최대 짝 수(`peaks`)가 3 이상인 앞 20행 — 나머지는 끝의
    `(기타)` 하나(합 0 이면 뺌)."""
    rest = table.get(OTHER_COUNTRY, [0, 0])
    rest_c, rest_s = rest
    named: list[list[Any]] = []
    for name, c, s in _sorted({k: v for k, v in table.items() if k != OTHER_COUNTRY}):
        if len(named) < TOP and peaks.get(name, 0) >= K:
            named.append([name, c, s])
        else:
            rest_c, rest_s = rest_c + c, rest_s + s
    if rest_s:
        named.append([OTHER_COUNTRY, rest_c, rest_s])
    return named


def networks_rows(
    table: Table, kr_named: bool, peaks: dict[str, int]
) -> list[list[Any]]:
    """망 종류 행 — KR 이 이름으로 남지 않으면 telecom_kr 을 telecom 에 접고, 그 뒤 어느 하루에도 짝이 3 에 못 미친
    종류는 끝의 `(기타)` 하나에 합친다(나라와 같은 기준 — 망 종류 한 행으로 한 사람이 드러나지 않게)."""
    nets = {name: list(v) for name, v in table.items()}
    peak = dict(peaks)
    kr = nets.pop(TELECOM_KR, None)
    if kr is not None:
        if kr_named:
            nets[TELECOM_KR] = kr
        else:
            row = nets.setdefault(TELECOM, [0, 0])
            row[0] += kr[0]
            row[1] += kr[1]
            peak[TELECOM] = peaks.get(NET_TELECOM_ALL, 0)
    rest_c = rest_s = 0
    shown: Table = {}
    for name, (c, s) in nets.items():
        if peak.get(name, 0) >= K:
            shown[name] = [c, s]
        else:
            rest_c, rest_s = rest_c + c, rest_s + s
    rows = _sorted(shown)
    if rest_s:
        rows.append([OTHER_COUNTRY, rest_c, rest_s])
    return rows


def ok(
    table: GeoTable,
    countries: Table,
    networks: Table,
    ipv6: int,
    peaks: dict[str, int],
    net_peaks: dict[str, int],
    since_ts: int,
) -> dict[str, Any]:
    rows = countries_rows(countries, peaks)
    kr_named = any(row[0] == "KR" for row in rows)
    return {
        "state": "ok",
        "code": None,
        "month": table.month,
        "loadedAt": table.loaded_at,
        "sinceTs": since_ts,
        "countries": rows,
        "networks": networks_rows(networks, kr_named, net_peaks),
        "ipv6": ipv6,
    }
