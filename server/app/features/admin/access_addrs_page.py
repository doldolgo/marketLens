"""IP 하나 단위 목록 — 창 조립·가린/전체 가르기·판정·거르기·정렬·쪽 (스펙 069 §3.2·§3.4·§3.5).

전체 저장 시작 시각 `since` = 캐시한 파일(읽는 범위 30일 — 창보다 넓다) 가운데 끝 옥텟이 0 이 아닌 IPv4 줄의 가장 이른 시각.
파일의 '앞 줄' 행(가린 꼴 글자)은 그 행의 첫 줄이 `since` 뒤면 전체 IP 행, 아니면 가린 대역 행(`1.2.3.0/24`·`…/48`)이다.
목록은 067 처럼 두 번에 만든다 — 모든 행을 판정에 드는 수로만 모아 점수·판정·거르기·정렬을 하고, 고른 쪽(최대 500)만
spark·경로·브라우저 정보·위치를 모은다.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

from app.features.admin.access_addrs import (
    FIRST,
    LAST,
    PEAK,
    REQ,
    SUMS,
    AddrTally,
    merge,
)
from app.features.admin.access_addrs_rows import Rank, Where, row_values, signals, where
from app.features.admin.access_bands import band_of
from app.features.admin.access_verdict import judge
from app.features.admin.geo_city import CityTable
from app.features.admin.geo_table import GeoTable

LIMIT, LIMIT_MAX = 200, 500
SORTS = ("requests", "pages", "last", "score")
VERDICTS = ("bot", "suspect", "human", "unknown", "operator")


class AddrsQuery(NamedTuple):
    """거르기·정렬·쪽 — 모양이 틀린 값은 422·400 없이 기본값(067 과 같은 규칙)."""

    net: str | None = None
    country: str | None = None
    verdicts: frozenset[str] | None = None
    q: str = ""
    full_only: bool = False
    sort: str = "requests"
    limit: int = LIMIT
    offset: int = 0


def _int(text: str | None, default: int) -> int:
    try:
        return int(text) if text is not None else default
    except ValueError:
        return default


def addrs_query(
    net: str | None = None,
    country: str | None = None,
    verdict: str | None = None,
    q: str | None = None,
    masked: str | None = None,
    sort: str | None = None,
    limit: str | None = None,
    offset: str | None = None,
) -> AddrsQuery:
    chosen = frozenset(
        v.strip().lower() for v in (verdict or "").split(",")
    ) & frozenset(VERDICTS)
    return AddrsQuery(
        net=(net or "").strip().lower() or None,
        country=(country or "").strip().upper() or None,
        verdicts=chosen or None,
        q=(q or "").strip().lower()[:64],
        full_only=masked == "0",
        sort=sort if sort in SORTS else "requests",
        limit=min(max(_int(limit, LIMIT), 1), LIMIT_MAX),
        offset=max(_int(offset, 0), 0),
    )


def since_of(tallies: Iterable[AddrTally]) -> float | None:
    found = [t.since for t in tallies if t.since is not None]
    return min(found) if found else None


def resolve(text: str, pre: bool, first: float, since: float | None) -> str:
    """파일 행 → 응답 행 글자 — 전체 IP 그대로, 가린 대역은 `/24`·`/48` 을 붙인 글자."""
    if not pre or (since is not None and first >= since):
        return text
    return band_of(text)


def _rank(
    tallies: list[AddrTally], start: int, last: int, since: float | None
) -> dict[str, Rank]:
    out: dict[str, Rank] = {}
    for tally in tallies:
        for (text, pre), row in tally.rows.items():
            acc: Rank | None = None
            for hour, rec in row.hours.items():
                if not start <= hour <= last:
                    continue
                if acc is None:
                    key = resolve(text, pre, row.first, since)
                    acc = out.get(key)
                    if acc is None:
                        acc = out[key] = Rank()
                cells = acc.cells
                for i in range(SUMS):
                    cells[i] += rec[i]
                cells[PEAK] = max(cells[PEAK], rec[PEAK])
                cells[FIRST] = min(cells[FIRST], rec[FIRST])
                cells[LAST] = max(cells[LAST], rec[LAST])
            if acc is not None:
                merge(acc, row)
    return out


def _keep(ip: str, rank: Rank, w: Where, verdict: str, query: AddrsQuery) -> bool:
    if query.q and not ip.startswith(query.q):
        return False
    if query.full_only and "/" in ip:
        return False
    if query.net is not None and w.net != query.net:
        return False
    if query.country is not None and w.country != query.country:
        return False
    return query.verdicts is None or verdict in query.verdicts


SORT_KEYS: dict[str, Callable[[tuple[str, Rank, Any]], tuple[Any, ...]]] = {
    "requests": lambda r: (-r[1].cells[REQ], r[0]),
    "pages": lambda r: (-r[1].cells[1], r[0]),
    "last": lambda r: (-r[1].cells[LAST], r[0]),
    "score": lambda r: (
        -(r[2]["score"] if r[2]["score"] is not None else -(10**9)),
        r[0],
    ),
}


def _gather(
    tallies: list[AddrTally],
    keys: set[str],
    start: int,
    last: int,
    cells: tuple[int, int, int],
    since: float | None,
) -> dict[str, tuple[list[int], Counter[str], Counter[str]]]:
    origin, size, n = cells
    out = {k: ([0] * n, Counter[str](), Counter[str]()) for k in keys}
    for tally in tallies:
        for (text, pre), row in tally.rows.items():
            key = resolve(text, pre, row.first, since)
            if key not in out:
                continue
            spark, paths, agents = out[key]
            seen = False
            for hour, rec in row.hours.items():
                if start <= hour <= last:
                    spark[(hour - origin) // size] += rec[REQ]
                    seen = True
            if seen:  # 상위 목록은 파일 단위 — 창 안 줄이 있는 파일의 것을 더한다(067 과 같은 근사)
                paths.update(row.paths)
                agents.update(row.agents)
    return out


def page(
    tallies: list[AddrTally],
    since: float | None,
    start: int,
    end: int,
    cells: tuple[int, int, int],
    query: AddrsQuery,
    table: GeoTable | None,
    city: CityTable | None,
) -> dict[str, Any]:
    """창 [start, end] 의 IP 목록 — `total`(거른 뒤 행 수)·`since`(초, 없으면 null)·쪽의 행."""
    last = end - end % 3600
    ranks = _rank(tallies, start, last, since)
    rows = []
    places: dict[str, Where] = {}
    for ip, rank in ranks.items():
        w = places[ip] = where(table, ip)
        verdict = judge(signals(rank.cells, rank, w), ip=ip, masked="/" in ip)
        if _keep(ip, rank, w, verdict["verdict"], query):
            rows.append((ip, rank, verdict))
    rows.sort(key=SORT_KEYS[query.sort])
    chosen = [ip for ip, _, _ in rows[query.offset : query.offset + query.limit]]
    extra = _gather(tallies, set(chosen), start, last, cells, since)
    return {
        "total": len(rows),
        "since": None if since is None else int(since),
        "rows": [
            row_values(ip, ranks[ip], places[ip], city, *extra[ip]) for ip in chosen
        ],
    }
