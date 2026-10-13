"""IP 대역별 접속 — 파일 하나의 대역 칸과 창 조립·거르기·정렬·쪽 (스펙 067 §3.1·§3.2).

대역 = 접속 기록 줄의 가린 IP(027 caddy 가 IPv4 /24·IPv6 /48 로 가린다)를 그 길이로 다시 자른 주소에 `/24`·`/48` 을 붙인
글자다. 가린 줄은 글자가 그대로이고, 가려지지 않은 줄(손으로 고친 기록)도 같은 대역으로 접혀 원래 IP 가 응답에 나가지 않는다.
IP 칸이 없는 줄은 `(없음)`, IP 로 풀 수 없는 글자와 파일 하나에서 5만 개를 넘은 새 대역은 `(기타)`.
파일마다 대역 → 시(時) 칸(수 열여섯·처음·마지막 줄 시각·경로·브라우저 정보 수)과 짝(그날 (IP, UA) 의 038 해시 → 그날 시 비트)을
둔다. 창은 038 처럼 창 안 시 칸만 더해 회전 파일이 창 경계를 걸쳐도 수가 창과 같다. 짝 해시·UA 원문은 응답에 없다 — 브라우저
정보는 038 의 기기·OS·브라우저 이름이다. 목록은 두 번에 만든다: 모든 대역을 거르기·정렬 열쇠(요청·페이지·브라우저 종류 요청·
마지막 시각)로만 세고, 고른 쪽(최대 500)만 나머지 값을 모은다 — 대역이 많은 날에도 응답을 만드는 동안의 메모리가 쪽에 묶인다.
"""

import ipaddress
from socket import AF_INET, AF_INET6, inet_pton
from typing import Any

from app.features.admin.access_classes import CLASSES
from app.features.admin.access_pairs import DAY_SEC, KST_SEC
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
