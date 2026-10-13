"""IP 대역별 접속 — 파일 하나의 대역 칸과 창 조립·거르기·정렬·쪽 (스펙 067 §3.1·§3.2).

대역 = 접속 기록 줄의 가린 IP(027 caddy 가 IPv4 /24·IPv6 /48 로 가린다)를 그 길이로 다시 자른 주소에 `/24`·`/48` 을 붙인
글자다. 가린 줄은 글자가 그대로이고, 가려지지 않은 줄(손으로 고친 기록)도 같은 대역으로 접혀 원래 IP 가 응답에 나가지 않는다.
IP 칸이 없는 줄은 `(없음)`, IP 로 풀 수 없는 글자와 파일 하나에서 5만 개를 넘은 새 대역은 `(기타)`.
파일마다 대역 → 시(時) 칸(수 열여섯·처음·마지막 줄 시각·경로·브라우저 정보 수)과 짝(그날 (IP, UA) 의 038 해시 → 그날 시 비트)을
둔다. 창은 038 처럼 창 안 시 칸만 더해 회전 파일이 창 경계를 걸쳐도 수가 창과 같다. 짝 해시·UA 원문은 응답에 없다 — 브라우저
정보는 038 의 기기·OS·브라우저 이름이다. 목록은 두 번에 만든다: 모든 대역을 거르기·정렬 열쇠(요청·페이지·브라우저 종류 요청·
마지막 시각)로만 세고, 고른 쪽(최대 500)만 나머지 값을 모은다 — 대역이 많은 날에도 응답을 만드는 동안의 메모리가 쪽에 묶인다.
"""

import heapq
import ipaddress
import math
from collections import Counter
from collections.abc import Callable, Iterable
from socket import AF_INET, AF_INET6, inet_pton
from typing import Any, NamedTuple

from app.features.admin.access_classes import CLASSES
from app.features.admin.access_pairs import DAY_SEC, KST_SEC, kst_day
from app.features.admin.access_traits import Traits
from app.features.admin.geo_table import IPV6, GeoTable, locate

NO_BAND = "(없음)"
OTHER = "(기타)"
BAND_CAP = 50_000  # 파일 하나의 대역 — 넘는 새 대역은 (기타) (§3.2)
# 시 칸마다 경로 10·브라우저 정보 5 — 이미 있는 이름은 세고 넘는 새 이름은 (기타)
PATH_CAP = 10
AGENT_CAP = 5
PATH_LIMIT = 100
TOP = 3  # 응답의 topPaths·topAgents
LIMIT, LIMIT_MAX = 200, 500
SORTS = ("requests", "pages", "last")
BOT_KINDS = ("scanner", "tool", "ai", "search")  # botPct 의 분자 (§3.2)
STATUS_NAMES = ("2xx", "3xx", "4xx", "5xx")
# 시 칸 — 수 열여섯(요청·사람 모양 페이지·JS 신호·WS 101·종류 여덟·상태 넷), 처음·마지막 줄 시각, 경로·브라우저 정보 수
REQ, PAGES, JS, WS, KIND, STATUS = 0, 1, 2, 3, 4, 12
CELLS = 16
FIRST, LAST, PATHS, AGENTS = 16, 17, 18, 19
KIND_NO = {k: KIND + i for i, k in enumerate(CLASSES)}  # 종류 → 시 칸 번호
BROWSER = KIND_NO["browser"]
_NAMES: dict[Traits, str] = {}  # 기기·OS·브라우저 이름 셋의 조합 — 많아야 수백 가지


def band_of(ip: str) -> str:
    """가린 IP 글자 → 대역 글자. 빈 값은 `(없음)`, IPv4·IPv6 로 풀 수 없으면 `(기타)`."""
    if not ip:
        return NO_BAND
    try:
        n = int.from_bytes(inet_pton(AF_INET, ip), "big") >> 8
        return f"{n >> 16}.{(n >> 8) & 255}.{n & 255}.0/24"
    except (OSError, ValueError):
        pass
    try:
        packed = inet_pton(AF_INET6, ip)
    except (OSError, ValueError):
        return OTHER
    return f"{ipaddress.IPv6Address(packed[:6] + bytes(10))}/48"


def is_band(text: str) -> bool:
    """상세 조회의 `band` — 목록 응답의 글자 그대로인 IPv4 /24·IPv6 /48 꼴만 (§3.3)."""
    if len(text) > 64 or "/" not in text:
        return False
    try:
        net = ipaddress.ip_network(text, strict=True)
    except ValueError:
        return False
    return net.prefixlen == (24 if net.version == 4 else 48) and str(net) == text


def agent_name(traits: Traits) -> str:
    """038 의 기기·OS·브라우저 이름 셋 — UA 원문이 아니다."""
    name = _NAMES.get(traits)
    if name is None:
        name = _NAMES[traits] = f"{traits.device}·{traits.os}·{traits.browser}"
    return name


def geo_of(table: GeoTable | None, band: str) -> tuple[str | None, str | None]:
    """(나라, 망 종류) — 대역의 첫 주소로 039 판을 찾는다. IPv6 는 망 칸만 `ipv6`, 판이 없거나 (없음)·(기타)는 둘 다 None."""
    address, _, length = band.partition("/")
    if length == "48":
        return None, IPV6
    if length != "24" or table is None:
        return None, None
    return locate(table, address)


class Band:
    """파일 하나에서 대역 하나 — 시 시작 → 시 칸, 짝 해시 → (KST 날 번호 << 24) | 그날 시 비트."""

    __slots__ = ("hours", "pairs")

    def __init__(self) -> None:
        self.hours: dict[int, list[Any]] = {}
        self.pairs: dict[bytes, int] = {}


class BandTally:
    """파일 하나의 대역 칸 — `FileTally` 가 줄마다 `add` 하고, 다 읽으면(깨진 파일도) `finish` 가 메모를 버린다."""

    __slots__ = ("bands", "_memo", "_paths")

    def __init__(self) -> None:
        self.bands: dict[str, Band] = {}
        # 읽는 동안만 — 가린 IP 글자 → 대역, 같은 경로 글자를 대역마다 따로 들지 않게 하나로
        self._memo: dict[str, str] = {}
        self._paths: dict[str, str] = {}

    def add(
        self,
        ts: float,
        hour: int,
        ip: str,
        path: str,
        agent: str,
        kind: str,
        status: int,
        flags: tuple[bool, bool, bool, bool],
        pair: bytes | None,
        mark: int,
    ) -> None:
        """줄 하나 — `kind` 는 038 의 종류, `flags` 는 (사람 모양 페이지, JS 신호, WS 101, WS 경로), `pair` 는 038 의 짝 해시
        (IP 가 없거나 게이트 앞 줄이면 None)와 그 `mark`. 상태 칸은 038 과 같다 — 200~599 만, WS 경로의 5xx 는 넣지 않는다."""
        band = self._memo.get(ip)
        if band is None:
            band = self._memo[ip] = band_of(ip)
        b = self.bands.get(band)
        if b is None:
            if len(self.bands) >= BAND_CAP:
                band = OTHER
                b = self.bands.get(OTHER)
            if b is None:
                b = self.bands[band] = Band()
        rec = b.hours.get(hour)
        if rec is None:
            rec = b.hours[hour] = [0] * CELLS + [ts, ts, {}, {}]
        elif ts < rec[FIRST]:
            rec[FIRST] = ts
        elif ts > rec[LAST]:
            rec[LAST] = ts
        rec[REQ] += 1
        rec[KIND_NO[kind]] += 1
        page, js, ws, ws_path = flags
        if 200 <= status < 600 and not (status >= 500 and ws_path):
            rec[STATUS + status // 100 - 2] += 1
        if page:
            rec[PAGES] += 1
        if js:
            rec[JS] += 1
        if ws:
            rec[WS] += 1
        path = path[:PATH_LIMIT]
        paths = rec[PATHS]
        if path in paths:
            paths[path] += 1
        elif len(paths) < PATH_CAP:
            paths[self._paths.setdefault(path, path)] = 1
        else:
            paths[OTHER] = paths.get(OTHER, 0) + 1
        agents = rec[AGENTS]
        if agent in agents:
            agents[agent] += 1
        elif len(agents) < AGENT_CAP:
            agents[agent] = 1
        else:
            agents[OTHER] = agents.get(OTHER, 0) + 1
        if pair is not None:
            pairs = b.pairs
            pairs[pair] = pairs.get(pair, 0) | mark

    def finish(self) -> None:
        self._memo.clear()
        self._paths.clear()


def pair_mark(day: int, its: int) -> int:
    """짝 값 — KST 날 번호와 그날 시 비트(`day` 는 그 줄의 KST 날 00:00). 열쇠가 날마다 달라 같은 해시는 다른 날에 나오지 않는다."""
    return ((day + KST_SEC) // DAY_SEC) << 24 | 1 << ((its - day) // 3600)


def spark_cells(hourly: bool, start: int, end: int) -> tuple[int, int, int]:
    """(첫 칸의 시작, 칸 길이, 칸 수) — 24시간 창은 시 칸(038 `hourly` 와 같은 칸), 7일·30일은 창과 겹치는 KST 날마다 한 칸
    (038 `visitors.days` 와 같은 날 — 창 시작이 KST 자정이 아니면 7일 창은 8칸·30일 창은 31칸)."""
    last = end - end % 3600
    if hourly:
        return start, 3600, (last - start) // 3600 + 1
    first = kst_day(start)
    return first, DAY_SEC, (kst_day(end) - first) // DAY_SEC + 1


class IpsQuery(NamedTuple):
    """목록의 거르기·정렬·쪽 — 모양이 틀린 값은 422·400 없이 기본값(038 의 창과 같은 규칙)."""

    net: str | None = None
    country: str | None = None
    q: str = ""
    bots: bool = True
    sort: str = "requests"
    limit: int = LIMIT
    offset: int = 0


def _int(text: str | None, default: int) -> int:
    try:
        return int(text) if text is not None else default
    except ValueError:
        return default


def ips_query(
    net: str | None = None,
    country: str | None = None,
    q: str | None = None,
    bots: str | None = None,
    sort: str | None = None,
    limit: str | None = None,
    offset: str | None = None,
) -> IpsQuery:
    return IpsQuery(
        net=(net or "").strip().lower() or None,
        country=(country or "").strip().upper() or None,
        q=(q or "").strip().lower()[:64],
        bots=bots != "0",
        sort=sort if sort in SORTS else "requests",
        limit=min(max(_int(limit, LIMIT), 1), LIMIT_MAX),
        offset=max(_int(offset, 0), 0),
    )


# 큰 순, 같으면 대역 글자순
SORT_KEYS: dict[str, Callable[[tuple[str, list[Any]]], tuple[Any, ...]]] = {
    "requests": lambda r: (-r[1][0], r[0]),
    "pages": lambda r: (-r[1][1], r[0]),
    "last": lambda r: (-r[1][3], r[0]),
}


def top(counter: Counter[str], n: int) -> list[list[Any]]:
    """수 내림차순(같으면 이름순) n 개 — `[[이름, 수], …]`."""
    best = heapq.nsmallest(n, counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [[name, count] for name, count in best]


def day_mask(day: int, start: int, last: int) -> int:
    """KST 날(00:00 epoch 초)의 시 가운데 창 [start, last(마지막 시의 시작)] 에 드는 것의 비트."""
    mask = 0
    for h in range(24):
        if start <= day + h * 3600 <= last:
            mask |= 1 << h
    return mask


class _Acc:
    """쪽에 든 대역 하나의 창 값 — 파일마다 창 안 시 칸을 더한다."""

    __slots__ = ("cells", "first", "last", "spark", "paths", "agents", "pairs")

    def __init__(self, n: int) -> None:
        self.cells = [0] * CELLS
        self.first = math.inf
        self.last = 0.0
        self.spark = [0] * n
        self.paths: Counter[str] = Counter()
        self.agents: Counter[str] = Counter()
        self.pairs: set[bytes] = set()


def _rank(tallies: Iterable[BandTally], start: int, last: int) -> dict[str, list[Any]]:
    """대역 → [요청, 사람 모양 페이지, 브라우저 종류 요청, 마지막 줄 시각] — 창 안 시 칸만, 줄이 없는 대역은 없다."""
    keys: dict[str, list[Any]] = {}
    for tally in tallies:
        for band, b in tally.bands.items():
            row = None
            for hour, rec in b.hours.items():
                if start <= hour <= last:
                    if row is None:
                        row = keys.get(band)
                        if row is None:
                            row = keys[band] = [0, 0, 0, 0.0]
                    row[0] += rec[REQ]
                    row[1] += rec[PAGES]
                    row[2] += rec[BROWSER]
                    if rec[LAST] > row[3]:
                        row[3] = rec[LAST]
    return keys


def _keep(
    row: tuple[str, list[Any]],
    query: IpsQuery,
    where: Callable[[str], tuple[str | None, str | None]],
) -> bool:
    band, (_, _, browser, _) = row
    if query.q and not band.startswith(query.q):
        return False
    if not query.bots and not browser:
        return False  # 사람 브라우저 모양 요청이 하나도 없는 대역
    if query.net is not None or query.country is not None:
        country, net = where(band)
        if query.net is not None and net != query.net:
            return False
        if query.country is not None and country != query.country:
            return False
    return True


def _gather(
    tallies: Iterable[BandTally],
    bands: list[str],
    start: int,
    last: int,
    cells: tuple[int, int, int],
) -> dict[str, _Acc]:
    origin, size, n = cells
    accs = {band: _Acc(n) for band in bands}
    masks: dict[int, int] = {}  # KST 날 번호 → 창 안 시 비트
    for tally in tallies:
        found = tally.bands
        for band, acc in accs.items():
            b = found.get(band)
            if b is None:
                continue
            for hour, rec in b.hours.items():
                if not start <= hour <= last:
                    continue
                total = acc.cells
                for i in range(CELLS):
                    total[i] += rec[i]
                acc.first = min(acc.first, rec[FIRST])
                acc.last = max(acc.last, rec[LAST])
                acc.spark[(hour - origin) // size] += rec[REQ]
                acc.paths.update(rec[PATHS])
                acc.agents.update(rec[AGENTS])
            # 짝 — 같은 날을 두 파일이 나눠도 해시가 같아 한 번만 센다(038 의 열쇠), 다른 날은 해시가 달라 날마다 따로
            for pair, mark in b.pairs.items():
                dayno = mark >> 24
                mask = masks.get(dayno)
                if mask is None:
                    mask = masks[dayno] = day_mask(
                        dayno * DAY_SEC - KST_SEC, start, last
                    )
                if mark & mask:
                    acc.pairs.add(pair)
    return accs


def _row(band: str, acc: _Acc, geo: tuple[str | None, str | None]) -> dict[str, Any]:
    c = acc.cells
    requests = c[REQ]
    bots = sum(c[KIND_NO[k]] for k in BOT_KINDS)
    return {
        "band": band,
        "country": geo[0],
        "net": geo[1],
        "requests": requests,
        "pages": c[PAGES],
        "js": c[JS],
        "ws": c[WS],
        "pairs": len(acc.pairs),
        "kinds": {k: c[KIND + i] for i, k in enumerate(CLASSES)},
        "status": {s: c[STATUS + i] for i, s in enumerate(STATUS_NAMES)},
        "botPct": round(bots * 100 / requests, 1) if requests else 0.0,
        "first": int(acc.first),
        "last": int(acc.last),
        "spark": acc.spark,
        "topPaths": top(acc.paths, TOP),
        "topAgents": top(acc.agents, TOP),
    }


def page(
    tallies: list[BandTally],
    start: int,
    end: int,
    cells: tuple[int, int, int],
    query: IpsQuery,
    table: GeoTable | None,
) -> dict[str, Any]:
    """창 [start, end] 의 대역 목록 — `total`(거른 뒤 대역 수)과 쪽의 행. `cells` = (첫 칸의 시작, 칸 길이, 칸 수)."""
    last = end - end % 3600
    geo: dict[str, tuple[str | None, str | None]] = {}

    def where(band: str) -> tuple[str | None, str | None]:
        found = geo.get(band)
        if found is None:
            found = geo[band] = geo_of(table, band)
        return found

    rows = [
        row for row in _rank(tallies, start, last).items() if _keep(row, query, where)
    ]
    rows.sort(key=SORT_KEYS[query.sort])
    chosen = [band for band, _ in rows[query.offset : query.offset + query.limit]]
    accs = _gather(tallies, chosen, start, last, cells)
    return {
        "total": len(rows),
        "bands": [_row(band, accs[band], where(band)) for band in chosen],
    }
