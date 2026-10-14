"""DB-IP City Lite 흉내 — 가짜 CSV(머리 줄 없음·따옴표 도시·IPv6 꼬리) (스펙 069 §4 '위치').

채움은 100.0.0.0/8 의 /24 구간들(확인 하한 1만 행)이고, 테스트 장소는 그 밖의 문서용 주소다. 채움 위치는 `fill_places` 개를
돌려 쓴다 — 위치 수를 늘려 메모리를 잰다.
"""

import csv
import gzip
import io
import socket
from typing import Any

FILL = 10_000
FILL_START = 100 << 24
MONTH = "2026-10"
# 장소 (시작, 끝) → (대륙, 나라, 지역, 도시, 위도, 경도)
PLACES: dict[tuple[str, str], tuple[str, str, str, str, str, str]] = {
    ("1.0.0.0", "1.0.0.255"): (
        "OC",
        "AU",
        "Queensland",
        "South Brisbane",
        "-27.4767",
        "153.017",
    ),
    # 1.0.1.0 ~ 1.0.1.255 는 비어 있다(구간 사이 빈칸)
    ("1.0.2.0", "1.0.3.255"): ("AS", "CN", "Fujian", "Wenquan", "26.0998", "119.297"),
    ("203.0.113.0", "203.0.113.127"): (
        "AS",
        "KR",
        "Seoul",
        'Gangnam-gu, "Yeoksam"',
        "37.5",
        "127.03",
    ),
    # 같은 위치의 다른 좌표 — 처음 본 좌표가 남는다
    ("203.0.113.128", "203.0.113.255"): (
        "AS",
        "KR",
        "Seoul",
        'Gangnam-gu, "Yeoksam"',
        "37.6",
        "127.1",
    ),
    ("198.51.100.0", "198.51.100.255"): ("NA", "US", "", "", "37.751", "-97.822"),
}


def dotted(n: int) -> str:
    return socket.inet_ntoa(n.to_bytes(4, "big"))


def number(ip: str) -> int:
    return int.from_bytes(socket.inet_aton(ip), "big")


def city_rows(fill: int = FILL, fill_places: int = 50) -> list[list[Any]]:
    rows: list[list[Any]] = [
        [start, end, *value] for (start, end), value in PLACES.items()
    ]
    for i in range(fill):
        start = FILL_START + i * 256
        k = i % fill_places
        rows.append(
            [
                dotted(start),
                dotted(start + 255),
                "NA",
                "US",
                f"Region {k}",
                f"Town {k}",
                "40.0",
                "-75.0",
            ]
        )
    rows.sort(key=lambda r: number(r[0]))
    rows += [
        ["2001:db8::", "2001:db8::ffff", "AS", "JP", "Tokyo", "Tokyo", "35.6", "139.7"]
    ]
    return rows


def text(rows: list[list[Any]]) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def parsed(rows: list[list[Any]]) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text(rows))))


def gz(rows: list[list[Any]]) -> bytes:
    return gzip.compress(text(rows).encode(), compresslevel=1)
