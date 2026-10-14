"""DB-IP City Lite 판 하나 — CSV 확인·간결한 표·이진 파일 쓰기/읽기·주소 조회 (스펙 069 §3.3).

외부 계약(2026-10 판으로 확인): 머리 줄 없는 CSV `시작,끝,대륙,나라,지역,도시,위도,경도`(도시에 따옴표가 있을 수 있다),
IPv4 3,620,434행 뒤에 IPv6 행. 첫 칸에 `:` 가 든 행(IPv6)은 건너뛴다 — 조회는 IPv4 만.
표는 구간 시작·끝·위치 번호(4바이트 정수 배열 셋 — 행마다 12바이트)와 위치 표(`나라\\t지역\\t도시` UTF-8 글자를 이은 바이트·
자리 배열·위도·경도 float32)다. 위치 = (나라, 지역, 도시) 하나 — 2026-10 판 166,890개. 같은 위치의 행들이 위도·경도를 달리
적어도 처음 본 값 하나만 둔다(위치는 근사다 — 행마다 좌표를 두면 +29MB). 2026-10 판이면 유지 메모리 ≈49MB.
파일은 머리(판·개수·바이트 순서·CRC32) 뒤에 배열을 그대로 잇는다 — 읽기는 배열에 바로 붓고 CRC 로 깨짐을 본다.
"""

import math
import os
import struct
import sys
import zlib
from array import array
from bisect import bisect_right
from collections.abc import Callable, Iterable
from pathlib import Path
from socket import AF_INET, inet_pton
from typing import NamedTuple

from app.features.admin.geo_table import IP_TYPE, BadData

MIN_ROWS = 10_000
MAX_ROWS = 10_000_000
COLUMNS = 8
MAGIC = b"KTCITY01"
# 머리: 표지 8 · 판 7(YYYY-MM) · 바이트 순서 1(l·b) · 정수 크기 1 · 행 수 · 위치 수 · 위치 글자 바이트 · 몸통 CRC32
HEAD = struct.Struct("<8s7sccIIII")
PREFIX = "city-"
SUFFIX = ".bin"


class City(NamedTuple):
    country: str
    region: str | None
    city: str | None
    lat: float
    lon: float


class CityTable:
    """올린 판 하나 — 만든 뒤에는 바꾸지 않는다."""

    __slots__ = ("month", "starts", "ends", "locs", "offsets", "blob", "lats", "lons")

    def __init__(self, month: str) -> None:
        self.month = month
        self.starts = array(IP_TYPE)
        self.ends = array(IP_TYPE)
        self.locs = array(IP_TYPE)
        self.offsets = array(
            IP_TYPE, [0]
        )  # 위치 i 의 글자 = blob[offsets[i]:offsets[i + 1]]
        self.blob = b""
        self.lats = array("f")
        self.lons = array("f")

    def __len__(self) -> int:
        return len(self.starts)

    @property
    def places(self) -> int:
        return len(self.lats)

    def nbytes(self) -> int:
        """배열·위치 글자의 바이트 합 — 유지 메모리의 거의 전부."""
        arrays = (self.starts, self.ends, self.locs, self.offsets, self.lats, self.lons)
        return sum(a.itemsize * len(a) for a in arrays) + len(self.blob)

    def find(self, ip: str) -> City | None:
        """IPv4 글자 → 그 주소가 든 구간의 위치. 구간 밖·구간 사이 빈칸·IPv6·풀 수 없는 글자는 None."""
        try:
            n = int.from_bytes(inet_pton(AF_INET, ip), "big")
        except (OSError, ValueError):
            return None
        i = bisect_right(self.starts, n) - 1
        if i < 0 or self.ends[i] < n:
            return None
        at = self.locs[i]
        text = self.blob[self.offsets[at] : self.offsets[at + 1]].decode("utf-8")
        country, region, city = text.split("\t")
        return City(
            country,
            region or None,
            city or None,
            round(self.lats[at], 4),
            round(self.lons[at], 4),
        )


def _ip(text: str) -> int:
    try:
        return int.from_bytes(inet_pton(AF_INET, text), "big")
    except (OSError, ValueError) as exc:
        raise BadData from exc


def _coord(text: str, limit: float) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise BadData from exc
    if not (math.isfinite(value) and -limit <= value <= limit):
        raise BadData
    return value


def read_city(
    rows: Iterable[list[str]],
    month: str,
    check: Callable[[], None] = lambda: None,
) -> CityTable:
    """City CSV 행 → 표. 확인(039 꼴): IPv4 행 1만~1,000만·오름차순·겹침 없음·열 여덟·나라 영문 대문자 두 글자·좌표 범위."""
    out = CityTable(month)
    starts, ends, locs = out.starts, out.ends, out.locs
    offsets, lats, lons = out.offsets, out.lats, out.lons
    # 위치 글자 → 번호 — 적재 동안만(166,890개 ≈25MB). 글자는 이어 붙일 조각 목록에 한 번씩
    seen: dict[str, int] = {}
    pieces: list[bytes] = []
    size = 0
    prev = -1
    count = 0
    for row in rows:
        if row and ":" in row[0]:
            continue  # IPv6
        if len(row) != COLUMNS:
            raise BadData
        start, end = _ip(row[0]), _ip(row[1])
        if start > end or start <= prev:
            raise BadData  # 뒤섞임·겹침
        prev = end
        count += 1
        if count > MAX_ROWS:
            raise BadData
        if count & 0xFFFF == 0:
            check()
        key = f"{row[3]}\t{row[4]}\t{row[5]}"
        at = seen.get(key)
        if at is None:
            cc = row[3]
            if not (len(cc) == 2 and cc.isascii() and cc.isalpha() and cc.isupper()):
                raise BadData
            if "\t" in row[4] or "\t" in row[5]:
                raise BadData
            at = seen[key] = len(lats)
            lats.append(_coord(row[6], 90.0))
            lons.append(_coord(row[7], 180.0))
            data = key.encode("utf-8")
            pieces.append(data)
            size += len(data)
            offsets.append(size)
        starts.append(start)
        ends.append(end)
        locs.append(at)
    if count < MIN_ROWS:
        raise BadData
    out.blob = b"".join(pieces)
    return out


def _arrays(table: CityTable) -> tuple[array, ...]:
    return (table.starts, table.ends, table.locs, table.offsets, table.lats, table.lons)


def file_name(month: str) -> str:
    return f"{PREFIX}{month}{SUFFIX}"


def write(table: CityTable, directory: str) -> Path:
    """`city-YYYY-MM.bin` 하나로 — 임시 이름에 다 쓴 뒤 바꿔 달고, 이 표지의 다른 파일(옛 달·남은 임시)은 지운다."""
    root = Path(directory)
    crc = 0
    for part in _arrays(table):
        crc = zlib.crc32(memoryview(part).cast("B"), crc)
    crc = zlib.crc32(table.blob, crc)
    head = HEAD.pack(
        MAGIC,
        table.month.encode("ascii"),
        sys.byteorder[0].encode("ascii"),
        bytes([table.starts.itemsize]),
        len(table),
        table.places,
        len(table.blob),
        crc,
    )
    final = root / file_name(table.month)
    temp = root / f".{final.name}.tmp"
    with open(temp, "wb") as handle:
        handle.write(head)
        for part in _arrays(table):
            part.tofile(handle)
        handle.write(table.blob)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, final)
    for other in root.iterdir():
        if other != final and _ours(other.name):
            other.unlink(missing_ok=True)
    return final


def _ours(name: str) -> bool:
    plain = name.removeprefix(".").removesuffix(".tmp")
    return plain.startswith(PREFIX) and plain.endswith(SUFFIX)


def latest(directory: str) -> Path | None:
    """볼륨의 가장 늦은 달 파일(임시 파일은 빼고)."""
    try:
        names = sorted(
            p.name
            for p in Path(directory).iterdir()
            if p.name.startswith(PREFIX) and p.name.endswith(SUFFIX)
        )
    except FileNotFoundError:
        return None
    return Path(directory) / names[-1] if names else None


def read(path: Path) -> CityTable:
    """파일 → 표. 머리·크기·CRC 가 맞지 않으면 `BadData`(바이트 순서·정수 크기가 다른 상자의 파일도)."""
    with open(path, "rb") as handle:
        raw = handle.read(HEAD.size)
        if len(raw) != HEAD.size:
            raise BadData
        magic, month, order, width, rows, places, blob, crc = HEAD.unpack(raw)
        if (
            magic != MAGIC
            or order != sys.byteorder[0].encode("ascii")
            or width[0] != array(IP_TYPE).itemsize
            or not MIN_ROWS <= rows <= MAX_ROWS
            or places > rows
        ):
            raise BadData
        table = CityTable(month.decode("ascii"))
        table.offsets = array(IP_TYPE)
        counts = (rows, rows, rows, places + 1, places, places)
        check = 0
        try:
            for part, n in zip(_arrays(table), counts, strict=True):
                part.fromfile(handle, n)
                check = zlib.crc32(memoryview(part).cast("B"), check)
        except EOFError as exc:
            raise BadData from exc
        table.blob = handle.read(blob)
        if len(table.blob) != blob or handle.read(1):
            raise BadData
    if zlib.crc32(table.blob, check) != crc or table.offsets[-1] != blob:
        raise BadData
    return table
