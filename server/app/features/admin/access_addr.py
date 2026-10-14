"""IP 하나(또는 가린 대역 하나)의 접속 — 창 안 기록을 그 행의 줄만 골라 다시 훑기 (스펙 069 §3.6).

067 의 대역 상세(`access_ip.BandScan`)를 그대로 쓰되 줄 고르기를 바꾼다: IP 전체 행은 그 주소 글자가 든 줄 가운데 전체 줄만,
가린 행은 가린 꼴 줄 가운데 전체 저장 시작 시각 앞 줄만. 시작 시각은 목록 캐시가 아는 값(없으면 이 훑기가 읽는 줄에서 처음 본
'끝 옥텟이 0 이 아닌 IPv4' 줄의 시각)이다. 고른 줄은 목록과 같은 IP 칸(`AddrTally`)에도 넣어 목록 행과 같은 값·판정을 만들고,
최근 100줄에 줄마다 판정 표시(probe·404·asset·js·ws)를 붙인다. 위치는 DB-IP 판(나라·망 종류·AS·조직)과 City(지역·도시·
좌표 — 근사), IP 전체 행은 역방향 DNS·검색 로봇 확인(`access_dns.py` — 1초·24시간 캐시)까지. 동기 함수다 — api 가
`asyncio.to_thread` 에서 한 번에 하나 부른다.
"""

import ipaddress
from collections import Counter
from collections.abc import Iterable
from typing import Any

from app.features.admin import access_classes as lines
from app.features.admin.access_addrs import (
    FIRST,
    LAST,
    PEAK,
    SUMS,
    UA_CAP,
    AddrTally,
    classify,
)
from app.features.admin.access_addrs_rows import Rank, row_values, where
from app.features.admin.access_bands import band_of, is_band, spark_cells
from app.features.admin.access_cache import (
    BROKEN,
    DEFAULT_WINDOW,
    bounds,
    listing,
    open_log,
)
from app.features.admin.access_dns import Dns
from app.features.admin.access_ip import BandScan, choose_files
from app.features.admin.geo_city import CityTable
from app.features.admin.geo_table import GeoTable

IP_FIELDS = ('"client_ip":"', '"remote_ip":"')
MEMO_CAP = 4_096


def is_addr(text: str) -> bool:
    """IPv4·IPv6 주소의 정규 글자(대문자·앞 0·줄임 안 한 IPv6 는 아니다)."""
    if len(text) > 64:
        return False
    try:
        return str(ipaddress.ip_address(text)) == text
    except ValueError:
        return False


def is_target(text: str) -> bool:
    return is_addr(text) or is_band(text)


def line_ip(raw: str) -> str:
    """JSON 을 풀지 않고 줄의 IP 칸 글자(client_ip, 없으면 remote_ip) — 없으면 빈 값."""
    for field in IP_FIELDS:
        i = raw.find(field)
        if i >= 0:
            start = i + len(field)
            end = raw.find('"', start)
            return raw[start:end] if end > 0 else ""
    return ""


class AddrScan(BandScan):
    """행 하나의 창 — 줄 고르기만 바꾸고 고른 줄을 IP 칸에도 센다."""

    def __init__(
        self, target: str, start: int, end: int, gate: int, since: float | None
    ) -> None:
        self.masked = "/" in target
        super().__init__(target if self.masked else band_of(target), start, end, gate)
        self.target = target
        if not self.masked:
            self.needle = f'"{target}"'
        self.since = since
        self.tally = AddrTally()
        self._ips: dict[str, tuple[str, bool, bool]] = {}

    def read(self, handle: Iterable[str]) -> None:
        try:
            for raw in handle:
                if self.since is None:
                    self._discover(raw)
                if self.needle in raw:
                    self._line(raw)
        finally:
            self._kinds.clear()
            self._traits.clear()
            self._ips.clear()

    def _classify(self, ip: str) -> tuple[str, bool, bool]:
        found = self._ips.get(ip)
        if found is None:
            found = classify(ip)
            if len(self._ips) < MEMO_CAP:
                self._ips[ip] = found
        return found

    def _discover(self, raw: str) -> None:
        if self._classify(line_ip(raw))[2]:
            self.since = lines.line_ts(raw)

    def _match(self, ip: str, ts: float) -> bool:
        text, shape, _ = self._classify(ip)
        full = not shape or (self.since is not None and ts >= self.since)
        if self.masked:
            return shape and not full and band_of(text) == self.target
        return full and text == self.target

    def _seen(
        self, ts: float, ip: str, line: tuple[Any, ...], flags: tuple[bool, ...]
    ) -> None:
        its = int(ts)
        self.tally.add(ts, its - its % 3600, ip, line, flags)  # type: ignore[arg-type]

    def rank(self, cells: tuple[int, int, int]) -> tuple[Rank, list[int]]:
        """창 안 IP 칸을 행 하나로 — 목록의 `Rank` 와 같은 값과 spark."""
        origin, size, n = cells
        acc, spark = Rank(), [0] * n
        c = acc.cells
        for row in self.tally.rows.values():
            for hour, rec in row.hours.items():
                if not self.start <= hour <= self.last:
                    continue
                for i in range(SUMS):
                    c[i] += rec[i]
                c[PEAK] = max(c[PEAK], rec[PEAK])
                c[FIRST] = min(c[FIRST], rec[FIRST])
                c[LAST] = max(c[LAST], rec[LAST])
                spark[(hour - origin) // size] += rec[0]
            if len(acc.uas) < UA_CAP:
                acc.uas.update(row.uas)
            acc.names.update(row.names)
            acc.claims.update(row.claims)
        return acc, spark

    def values(self) -> dict[str, Any]:
        out = super().values()
        recent = sorted(self.recent, reverse=True)
        for item, marks in zip(out["recent"], recent, strict=True):
            item["flags"] = list(marks[-1])
        return out


def scan(
    directory: str | None,
    target: str,
    name: str,
    now: float,
    gate: int,
    since: float | None,
    table: GeoTable | None,
    city: CityTable | None,
    dns: Dns,
) -> tuple[dict[str, Any], list[str]]:
    """창 `name` 의 행 `target` 값과 깨진 회전 파일의 예외 이름들. 파일이 없으면 `NoLogFile`, `access.log` 읽기 실패는 예외."""
    start, end = bounds(name, now, gate)
    current, rotated = listing(directory)
    files, partial = choose_files(current, rotated, start)
    tally = AddrScan(target, start, end, gate, since)
    broken: list[str] = []
    for path in files:
        try:
            with open_log(path) as handle:
                tally.read(handle)
        except FileNotFoundError:
            partial = True
        except BROKEN as exc:
            if current is not None and path == current.path:
                raise
            partial = True
            broken.append(type(exc).__name__)
    acc, spark = tally.rank(spark_cells(name == DEFAULT_WINDOW, start, end))
    w = where(table, target)
    ptr, verified = (None, None) if tally.masked else dns.check(target, acc.claims)
    row = row_values(
        target,
        acc,
        w,
        city,
        spark,
        Counter(tally.paths),
        Counter(tally.agents),
        fake_crawler=verified is False,
    )
    found = (
        None if city is None or ":" in target else city.find(target.partition("/")[0])
    )
    geo = {
        "country": w.country,
        "region": None if found is None else found.region,
        "city": None if found is None else found.city,
        "lat": None if found is None else found.lat,
        "lon": None if found is None else found.lon,
        "asn": w.asn,
        "org": w.org,
        "net": w.net,
        "ptr": ptr,
        "approx": True,
    }
    detail = tally.values()
    return {
        **row,
        "geo": geo,
        "verifiedCrawler": verified,
        **detail,
        "partial": partial,
    }, broken
