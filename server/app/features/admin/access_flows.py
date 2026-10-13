"""접속 흐름 — 짝의 그날 첫 페이지(들어온 곳)·마지막 페이지(나간 곳)·페이지 수 (스펙 062).

페이지 이름은 사람 모양 페이지 줄(038)의 쿼리 뗀 경로로 정하고 052 의 화면 이름과 같은 낱말을 쓴다. 대시보드는 SPA 라
탭 바꿈이 줄에 없어 '들어올 때의 탭'(쿼리 `tab`)이 그 이름이다. 창의 흐름은 038 이 창에서 세는 짝 가운데 그날 페이지
줄이 있는 짝만 세고, 짝마다 [confirmed, shaped] 를 더한다(038 의 날마다 센 방문자와 같은 셈). 짝 기록에는 이름 둘(고정
글자)과 페이지 수(255 에서 멈춤)만 더한다 — 경로·쿼리 원문은 남기지 않는다.
"""

from typing import Any
from urllib.parse import parse_qs

from app.features.admin.access_classes import DEFAULT_TAB, TABS

# 대시보드와 그 아래 SPA 경로 — nginx 가 없는 경로를 index.html 로 준다(`/app` 은 301 이라 페이지 줄이 아니다)
APP_PREFIX = "/app/"
OTHER = "(기타)"
APP_PAGES = {tab: f"app-{tab}" for tab in TABS}
FIXED_PAGES = {
    "/": "landing",
    "/privacy": "privacy",
    "/kimp-chart": "kimp-chart",
    "/kimp-history": "kimp-history",
}
# 응답 `entries`·`exits` 의 이름 — 0 인 이름도 싣는다
PAGES = ("landing", *APP_PAGES.values(), "privacy", "kimp-chart", "kimp-history", OTHER)
NPAGES_CAP = 255  # 짝의 그날 페이지 줄 수는 여기서 멈춘다
FLOW_TOP = 40
# 페이지 수 칸 — (이름, 칸의 끝(이하))
DEPTHS = (("1", 1), ("2", 2), ("3-5", 5), ("6+", NPAGES_CAP))
Row = list[int]  # [confirmed, shaped]


def page_name(path: str, query: str) -> str:
    """페이지 줄의 쿼리 뗀 경로·쿼리 → 페이지 이름. `/app/` 과 그 아래 경로는 첫 `tab` 값이 탭 여섯이면 `app-<tab>`,
    없거나 목록 밖이면 `app-spread`. 그 밖의 경로는 글자가 정확히 같을 때만 이름이고 아니면 `(기타)`."""
    if path.startswith(APP_PREFIX):
        tab = DEFAULT_TAB
        if query:
            tab = parse_qs(query, keep_blank_values=True).get("tab", [DEFAULT_TAB])[0]
        return APP_PAGES.get(tab, APP_PAGES[DEFAULT_TAB])
    return FIXED_PAGES.get(path, OTHER)


def _bump(table: dict[Any, Row], key: Any, confirmed: int) -> None:
    row = table.get(key)
    if row is None:
        row = table[key] = [0, 0]
    row[0] += confirmed
    row[1] += 1


class FlowCounts:
    """창 하나의 흐름 셈 — `visitors` 가 창에서 세는 짝 가운데 페이지 줄이 있는 짝마다 `add` 한다."""

    def __init__(self) -> None:
        self.entries: dict[str, Row] = {}
        self.exits: dict[str, Row] = {}
        self.flows: dict[tuple[str, str, str], Row] = {}
        self.depth: dict[str, Row] = {name: [0, 0] for name, _ in DEPTHS}

    def add(
        self, channel: str, entry: str, exit_: str, npages: int, confirmed: int
    ) -> None:
        _bump(self.entries, entry, confirmed)
        _bump(self.exits, exit_, confirmed)
        _bump(self.flows, (channel, entry, exit_), confirmed)
        _bump(self.depth, next(n for n, top in DEPTHS if npages <= top), confirmed)

    def values(self) -> dict[str, Any]:
        """`flows` 는 shaped 큰 순(같으면 채널·첫·마지막 이름순) 40줄 + 나머지를 합친 `(기타)` 한 줄(나머지가 있을 때만)."""
        ranked = sorted(self.flows.items(), key=lambda kv: (-kv[1][1], kv[0]))
        flows: list[list[Any]] = [[*key, c, s] for key, (c, s) in ranked[:FLOW_TOP]]
        rest = [row for _, row in ranked[FLOW_TOP:]]
        if rest:
            confirmed, shaped = sum(r[0] for r in rest), sum(r[1] for r in rest)
            flows.append([OTHER, OTHER, OTHER, confirmed, shaped])
        return {
            "flows": flows,
            "entries": _pages(self.entries),
            "exits": _pages(self.exits),
            "depthPages": {name: list(row) for name, row in self.depth.items()},
        }


def _pages(table: dict[str, Row]) -> list[list[Any]]:
    """페이지 이름 모두(0 인 이름도) — shaped 내림차순·같으면 이름순."""
    rows = [[name, *table.get(name, (0, 0))] for name in PAGES]
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows
