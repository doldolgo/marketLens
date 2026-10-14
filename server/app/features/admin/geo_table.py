"""DB-IP Lite 판 하나 — CSV 행 확인·IPv4 구간 적재·/24 조회 (스펙 039 §3.2~§3.5).

외부 계약(2026-10-02 받은 2026-10 판으로 확인): 머리 줄 없는 CSV, 'IP to Country Lite' 는 `시작 IP,끝 IP,나라 두 글자`
(모름은 `ZZ`), 'IP to ASN Lite' 는 `시작 IP,끝 IP,AS 번호,AS 조직`(조직 이름은 따옴표 안에 쉼표가 있을 수 있다).
IPv4·IPv6 행이 한 파일에 섞여 있고 IPv4 가 앞이다. 첫 칸에 `:` 가 든 행(IPv6)은 바로 건너뛴다 — IPv6 는 수만 센다.
IPv4 행은 표준 `array` 에 구간 시작·끝·값 번호(나라 또는 망 종류)로 둔다. ASN 파일은 구간마다 AS 번호·조직 이름 표의
번호도 둔다(069 — 관리자 IP 피드의 망 사업자 표시. 038 요약·067 대역에는 싣지 않는다). 행을 하나씩 흘려 읽고 파일을 쓰지 않는다.
"""

from array import array
from bisect import bisect_right
from collections.abc import Callable, Iterable
from socket import AF_INET, AF_INET6, inet_pton
from typing import NamedTuple

from app.features.admin.geo_kinds import (
    CLOUD,
    OTHER,
    TELECOM,
    TELECOM_KR,
    UNKNOWN,
    network_kind,
)

# 파일마다 IPv4 행 수의 확인 범위 — 2026-10 판은 나라 362,122·ASN 402,389
MIN_ROWS = 10_000
MAX_ROWS = 2_000_000
MAX_COUNTRIES = 255  # 값 번호를 1바이트(`B`)에 둔다
UNKNOWN_COUNTRY = "ZZ"
OTHER_COUNTRY = "(기타)"
IPV6 = "ipv6"  # 짝의 망 칸 표시 — 찾지 않고 수만 센다
MASK_24 = 0xFFFFFF00
# 4바이트 부호 없는 정수 — `I` 가 4바이트가 아닌 플랫폼이면 `L`
IP_TYPE = "I" if array("I").itemsize >= 4 else "L"
ASN_KINDS = (TELECOM, CLOUD, OTHER)


class BadData(Exception):
    """확인을 통과하지 못한 자료 — `bad_data`."""


class Ranges:
    """겹치지 않고 오름차순인 IPv4 구간들과 구간마다 값 번호 하나. ASN 파일은 구간마다 조직 번호도 —
    `orgs[i]` 가 `org_asns`·`org_names` 의 자리다(AS 번호 하나에 이름 하나 — 069)."""

    __slots__ = ("starts", "ends", "codes", "names", "orgs", "org_asns", "org_names")

    def __init__(self, names: tuple[str, ...]) -> None:
        self.starts = array(IP_TYPE)
        self.ends = array(IP_TYPE)
        self.codes = array("B")
        self.names = names
        self.orgs = array(IP_TYPE)
        self.org_asns = array(IP_TYPE)
        self.org_names: list[str] = []

    def __len__(self) -> int:
        return len(self.starts)

    def index(self, ip: int) -> int | None:
        """그 주소가 든 구간의 자리 — 구간 밖이면 None."""
        i = bisect_right(self.starts, ip) - 1
        if i < 0 or self.ends[i] < ip:
            return None
        return i

    def find(self, ip: int) -> str | None:
        """그 주소가 든 구간의 값 — 구간 밖이면 None."""
        i = self.index(ip)
        return None if i is None else self.names[self.codes[i]]

    def org(self, ip: int) -> tuple[int, str] | None:
        """그 주소가 든 구간의 (AS 번호, 조직 이름) — 구간 밖·조직 표 없음은 None."""
        i = self.index(ip)
        if i is None or not self.orgs:
            return None
        at = self.orgs[i]
        return self.org_asns[at], self.org_names[at]


class GeoTable:
    """올린 판 하나 — 갈아 끼운 뒤에는 바꾸지 않는다. `version` 은 로더가 판마다 올리는 번호."""

    __slots__ = ("month", "loaded_at", "version", "country", "asn")

    def __init__(
        self, month: str, loaded_at: int, version: int, country: Ranges, asn: Ranges
    ) -> None:
        self.month = month
        self.loaded_at = loaded_at  # ms
        self.version = version
        self.country = country
        self.asn = asn


def _ip(text: str) -> int:
    try:
        return int.from_bytes(inet_pton(AF_INET, text), "big")
    except (OSError, ValueError) as exc:
        raise BadData from exc


def _load(
    rows: Iterable[list[str]],
    columns: int,
    value_of: Callable[[list[str]], int],
    names: tuple[str, ...],
    check: Callable[[], None],
    out: Ranges | None = None,
) -> Ranges:
    if out is None:
        out = Ranges(names)
    starts, ends, codes = out.starts, out.ends, out.codes
    prev = -1
    count = 0
    for row in rows:
        if row and ":" in row[0]:
            continue  # IPv6
        if len(row) != columns:
            raise BadData
        start, end = _ip(row[0]), _ip(row[1])
        if start > end or start <= prev:
            raise BadData  # 뒤섞임·겹침
        prev = end
        count += 1
        if count > MAX_ROWS:
            raise BadData
        if count & 0xFFFF == 0:
            check()  # 6만여 행마다 — 파일마다 60초
        starts.append(start)
        ends.append(end)
        codes.append(value_of(row))
    if count < MIN_ROWS:
        raise BadData
    return out


def read_country(
    rows: Iterable[list[str]], check: Callable[[], None] = lambda: None
) -> Ranges:
    """나라 파일 — 나라 값은 영문 대문자 두 글자, 서로 다른 값 255개까지."""
    seen: dict[str, int] = {}
    names: list[str] = []

    def value_of(row: list[str]) -> int:
        code = seen.get(row[2])
        if code is None:
            cc = row[2]
            if not (len(cc) == 2 and cc.isascii() and cc.isalpha() and cc.isupper()):
                raise BadData
            if len(names) >= MAX_COUNTRIES:
                raise BadData
            code = seen[cc] = len(names)
            names.append(cc)
        return code

    out = _load(rows, 3, value_of, (), check)
    out.names = tuple(names)
    return out


def read_asn(
    rows: Iterable[list[str]], check: Callable[[], None] = lambda: None
) -> Ranges:
    """ASN 파일 — AS 번호는 0 이상 4바이트 정수. 조직 이름은 그 행의 망 종류를 정하고, AS 번호마다 처음 본 이름을 조직
    표에 남긴다(069 — 관리자 IP 피드의 망 사업자)."""
    at = {kind: i for i, kind in enumerate(ASN_KINDS)}
    out = Ranges(ASN_KINDS)
    orgs, org_asns, org_names = out.orgs, out.org_asns, out.org_names
    # AS 번호 글자 → (종류 번호, 조직 표 자리) — 한 망이 여러 구간에 나와 판정은 AS 번호마다 한 번이다. 한 판 안에서 AS 번호
    # 하나의 조직 이름은 하나라고 본다(2026-10 판 79,109개 모두). 메모는 적재가 끝나면 버린다
    memo: dict[str, tuple[int, int]] = {}

    def value_of(row: list[str]) -> int:
        asn = row[2]
        found = memo.get(asn)
        if found is None:
            if not (asn.isascii() and asn.isdigit()) or int(asn) > 0xFFFFFFFF:
                raise BadData
            number = int(asn)
            found = memo[asn] = (at[network_kind(number, row[3])], len(org_names))
            org_asns.append(number)
            org_names.append(row[3])
        orgs.append(found[1])
        return found[0]

    return _load(rows, 4, value_of, ASN_KINDS, check, out)


def locate(table: GeoTable, ip: str) -> tuple[str | None, str]:
    """가린 IP 글자 → (나라 두 글자 또는 `(기타)`, 망 종류) — IPv4 는 x.y.z.0 으로 다시 잘라 찾는다.
    IPv6 는 (None, `ipv6`) 이고 찾지 않는다. IPv4·IPv6 어느 쪽으로도 풀 수 없으면 (`(기타)`, `unknown`)."""
    try:
        n = int.from_bytes(inet_pton(AF_INET, ip), "big") & MASK_24
    except (OSError, ValueError):
        try:
            inet_pton(AF_INET6, ip)
        except (OSError, ValueError):
            return OTHER_COUNTRY, UNKNOWN
        return None, IPV6
    country = table.country.find(n)
    kind = table.asn.find(n) or UNKNOWN
    if kind == TELECOM and country == "KR":
        kind = TELECOM_KR
    if country is None or country == UNKNOWN_COUNTRY:
        country = OTHER_COUNTRY
    return country, kind


class Place(NamedTuple):
    """주소 하나의 나라·망 종류·AS 번호·조직 이름 (069)."""

    country: str | None
    net: str
    asn: int | None
    org: str | None


def place(table: GeoTable, ip: str) -> Place:
    """IP 글자 → 그 주소 그대로 찾은 값(069 — IP 전체 행). `locate` 와 같은 규칙(KR + telecom 은 telecom_kr, 모름·구간 밖은
    `(기타)`)이되 /24 로 자르지 않는다. IPv6 는 (None, `ipv6`), 풀 수 없으면 (`(기타)`, `unknown`)."""
    try:
        n = int.from_bytes(inet_pton(AF_INET, ip), "big")
    except (OSError, ValueError):
        country, net = locate(table, ip)
        return Place(country, net, None, None)
    country = table.country.find(n)
    kind = table.asn.find(n) or UNKNOWN
    if kind == TELECOM and country == "KR":
        kind = TELECOM_KR
    if country is None or country == UNKNOWN_COUNTRY:
        country = OTHER_COUNTRY
    org = table.asn.org(n)
    return Place(country, kind, *(org or (None, None)))
