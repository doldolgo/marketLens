"""DB-IP Lite 흉내 — 가짜 나라·ASN CSV.gz·MockTransport·받기 띄우기 (스펙 039 §4).

판은 테스트가 정한 /24 몇 개(`places`)와 채움 구간으로 만든다 — 채움은 100.0.0.0/8 의 /24 1만 개(확인 하한)라 테스트
IP(문서용 192.0.2.0·198.51.100.0·203.0.113.0 과 10.0.i.0)와 겹치지 않는다. 두 파일 끝에 IPv6 행을, ASN 채움에는
따옴표와 쉼표가 든 조직 이름을 둔다. 실제 자료처럼 머리 줄이 없다.
"""

import csv
import gzip
import io
import socket
from collections.abc import Callable, Iterator
from typing import Any

import httpx

BASE = "https://download.db-ip.com/free/"
MONTH = "2026-10"
FILL = 10_000
FILL_START = 100 << 24
FILL_ORG = 'Filler Net, "Quoted" Org'  # 망 종류 other
FILL_ASN = 64_512
# 장소 → (나라, AS 번호, 조직 이름) — AS 번호 None 이면 ASN 자료 밖
KR_TELECOM = "203.0.113.0"  # KR · KT
KR_CLOUD = "198.51.100.0"  # KR · Amazon
DE_TELECOM = "192.0.2.0"  # DE · 이름 낱말
PLACES: dict[str, tuple[str, int | None, str]] = {
    KR_TELECOM: ("KR", 4766, "Korea Telecom"),
    KR_CLOUD: ("KR", 16509, "Amazon.com, Inc."),
    DE_TELECOM: ("DE", 3320, "Deutsche Telekom AG"),
}


def url(kind: str, month: str = MONTH) -> str:
    return f"{BASE}dbip-{kind}-lite-{month}.csv.gz"


def dotted(n: int) -> str:
    return socket.inet_ntoa(n.to_bytes(4, "big"))


def number(ip: str) -> int:
    return int.from_bytes(socket.inet_aton(ip), "big")


def gz(rows: list[list[Any]], level: int = 1) -> bytes:
    text = io.StringIO()
    csv.writer(text, lineterminator="\n").writerows(rows)
    return gzip.compress(text.getvalue().encode(), compresslevel=level)


def fill_rows(count: int, value: list[Any], start: int = FILL_START) -> list[list[Any]]:
    return [
        [dotted(start + i * 256), dotted(start + i * 256 + 255), *value]
        for i in range(count)
    ]


def ipv6_rows(value: list[Any], count: int = 3) -> list[list[Any]]:
    return [
        [f"2001:db8:{i:x}::", f"2001:db8:{i:x}:ffff::", *value] for i in range(count)
    ]


def country_rows(
    places: dict[str, tuple[str, int | None, str]], fill: int = FILL
) -> list[list[Any]]:
    rows = fill_rows(fill, ["US"])
    rows += [[ip, dotted(number(ip) + 255), cc] for ip, (cc, _, _) in places.items()]
    rows.sort(key=lambda r: number(r[0]))
    return rows + ipv6_rows(["JP"])


def asn_rows(
    places: dict[str, tuple[str, int | None, str]], fill: int = FILL
) -> list[list[Any]]:
    rows = fill_rows(fill, [FILL_ASN, FILL_ORG])
    rows += [
        [ip, dotted(number(ip) + 255), asn, org]
        for ip, (_, asn, org) in places.items()
        if asn is not None
    ]
    rows.sort(key=lambda r: number(r[0]))
    return rows + ipv6_rows([13335, "Cloudflare, Inc."])


def bundle(
    places: dict[str, tuple[str, int | None, str]] | None = None,
) -> tuple[bytes, bytes]:
    places = PLACES if places is None else places
    return gz(country_rows(places)), gz(asn_rows(places))


class Chunks(httpx.SyncByteStream):
    """본문을 조각으로 — 조각을 내줄 때마다 `each(조각)` 을 부른다(시계 움직이기·멈추기·세기)."""

    def __init__(
        self,
        data: bytes | Iterator[bytes],
        size: int = 65_536,
        each: Callable[[bytes], None] | None = None,
    ) -> None:
        self._data = data
        self._size = size
        self._each = each
        self.sent = 0

    def __iter__(self) -> Iterator[bytes]:
        data = self._data
        pieces = (
            (data[i : i + self._size] for i in range(0, len(data), self._size))
            if isinstance(data, bytes)
            else data
        )
        for piece in pieces:
            if self._each is not None:
                self._each(piece)
            self.sent += len(piece)
            yield piece


class DbIp:
    """download.db-ip.com 흉내 — 주소마다 본문(bytes)·상태 코드(int)·처리 함수. 없는 주소는 404."""

    def __init__(self) -> None:
        self.files: dict[str, Any] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self.handle)

    def serve(
        self,
        month: str = MONTH,
        places: dict[str, tuple[str, int | None, str]] | None = None,
    ) -> "DbIp":
        country, asn = bundle(places)
        self.files[url("country", month)] = country
        self.files[url("asn", month)] = asn
        return self

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        got = self.files.get(str(request.url), 404)
        if callable(got):
            return got(request)
        if isinstance(got, int):
            return httpx.Response(got)
        return httpx.Response(200, stream=Chunks(got))

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def count(self, kind: str, month: str = MONTH) -> int:
        return self.urls.count(url(kind, month))


def inline(job: Callable[[], None]) -> None:
    """받기를 그 자리에서 끝까지 — 스레드 없이."""
    job()


class Jobs:
    """띄운 받기를 쥐고 있다가 테스트가 돌린다 — '받는 중' 을 스레드 없이 흉내."""

    def __init__(self) -> None:
        self.jobs: list[Callable[[], None]] = []

    def __call__(self, job: Callable[[], None]) -> None:
        self.jobs.append(job)

    def run(self) -> None:
        while self.jobs:
            self.jobs.pop(0)()
