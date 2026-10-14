"""IP 행 하나의 값 — 창 안 수·위치(DB-IP 나라·망 종류·AS·조직·City)·판정 (스펙 069 §3.3·§3.4·§3.5).

목록(`access_addrs_page.py`)과 상세(`access_addr.py`)가 같은 모양의 행을 만든다. 전체 IP 는 그 주소, 가린 대역은 첫 주소로
찾는다(067). IPv6 는 망 칸만 `ipv6`, City·ASN 은 IPv4 만.
"""

import heapq
from collections import Counter
from typing import Any, NamedTuple

from app.features.admin.access_addrs import (
    FIRST,
    KIND,
    LAST,
    NO_IP,
    OTHER,
    PEAK,
    REQ,
    STATUS,
    SUMS,
)
from app.features.admin.access_bands import STATUS_NAMES, geo_of
from app.features.admin.access_classes import CLASSES, PROBE
from app.features.admin.access_verdict import Signals, judge
from app.features.admin.geo_city import CityTable
from app.features.admin.geo_table import IPV6, GeoTable, place

TOP = 3


class Rank:
    """창 안 값 — 수 스물둘의 합·1분 최대·처음·마지막·UA 해시·봇 이름·검색 로봇 주장."""

    __slots__ = ("cells", "uas", "names", "claims")

    def __init__(self) -> None:
        self.cells: list[Any] = [0] * SUMS + [0, float("inf"), 0.0]
        self.uas: set[int] = set()
        self.names: Counter[str] = Counter()
        self.claims: set[str] = set()


class Where(NamedTuple):
    country: str | None
    net: str | None
    asn: int | None
    org: str | None


def where(table: GeoTable | None, ip: str) -> Where:
    """나라·망 종류·AS 번호·조직 이름 — 전체 IP 는 그 주소, 가린 대역은 첫 주소(067). IPv6 는 망 칸만 `ipv6`."""
    if ip in (NO_IP, OTHER):
        return Where(None, None, None, None)
    if "/" in ip:
        country, net = geo_of(table, ip)
        if table is None or net == IPV6:
            return Where(country, net, None, None)
        found = place(table, ip.partition("/")[0])
        return Where(country, net, found.asn, found.org)
    if ":" in ip:
        return Where(None, IPV6, None, None)
    if table is None:
        return Where(None, None, None, None)
    return Where(*place(table, ip))


def signals(
    acc_cells: list[Any], rank: Rank, w: Where, probe_paths: tuple[str, ...] = ()
) -> Signals:
    c = acc_cells
    return Signals(
        requests=c[REQ],
        pages=c[1],
        js=c[2],
        ws=c[3],
        kinds={k: c[KIND + i] for i, k in enumerate(CLASSES)},
        probes=c[16],
        e404=c[17],
        assets=c[18],
        robots=c[19],
        search=c[20],
        peak=c[PEAK],
        ua_bot=c[21],
        agents=len(rank.uas),
        net=w.net,
        names=tuple(name for name, _ in rank.names.most_common()),
        probe_paths=probe_paths,
        org=w.org,
    )


def top(counter: Counter[str], n: int) -> list[list[Any]]:
    best = heapq.nsmallest(n, counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [[name, count] for name, count in best]


def probe_paths(paths: Counter[str]) -> tuple[str, ...]:
    found = [kv for kv in paths.items() if PROBE.search(kv[0].lower()) is not None]
    return tuple(
        p for p, _ in heapq.nsmallest(3, found, key=lambda kv: (-kv[1], kv[0]))
    )


def row_values(
    ip: str,
    rank: Rank,
    w: Where,
    city: CityTable | None,
    spark: list[int],
    paths: Counter[str],
    agents: Counter[str],
    *,
    fake_crawler: bool = False,
) -> dict[str, Any]:
    """응답 행 하나 — §3.5 키 순서."""
    c = rank.cells
    masked = "/" in ip
    found = None if city is None or ":" in ip else city.find(ip.partition("/")[0])
    verdict = judge(
        signals(c, rank, w, probe_paths(paths)),
        ip=ip,
        masked=masked,
        fake_crawler=fake_crawler,
    )
    return {
        "ip": ip,
        "masked": masked,
        "country": w.country,
        "region": None if found is None else found.region,
        "city": None if found is None else found.city,
        "asn": w.asn,
        "org": w.org,
        "net": w.net,
        "requests": c[REQ],
        "pages": c[1],
        "js": c[2],
        "ws": c[3],
        "kinds": {k: c[KIND + i] for i, k in enumerate(CLASSES)},
        "status": {s: c[STATUS + i] for i, s in enumerate(STATUS_NAMES)},
        "first": None if c[FIRST] == float("inf") else int(c[FIRST]),
        "last": None if not c[LAST] else int(c[LAST]),
        "spark": spark,
        "topPaths": top(paths, TOP),
        "topAgents": top(agents, TOP),
        **verdict,
    }
