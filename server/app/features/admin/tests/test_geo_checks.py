"""DB-IP 확인 — 행 수·순서·겹침·나라 값·열 수·AS 번호·깨진 gzip 은 `bad_data`, 흘려 읽기 메모리·읽기 단위 (스펙 039 §3.3·§4 '확인'·'흘려 읽기')."""

import gzip
import tracemalloc
from collections.abc import Callable
from typing import Any

import pytest

from app.features.admin.geo_fetch import TEXT_CHUNK, GeoLoader
from app.features.admin.tests.access_fakes import AFTER
from app.features.admin.tests.geo_fakes import (
    FILL,
    PLACES,
    DbIp,
    asn_rows,
    country_rows,
    dotted,
    fill_rows,
    gz,
    inline,
    ipv6_rows,
    number,
    url,
)

Rows = list[list[Any]]


def load(country: bytes | None = None, asn: bytes | None = None) -> GeoLoader:
    dbip = DbIp().serve()
    if country is not None:
        dbip.files[url("country")] = country
    if asn is not None:
        dbip.files[url("asn")] = asn
    loader = GeoLoader(
        clock=lambda: AFTER,
        mono=lambda: 0.0,
        warn=lambda name, code: None,
        transport=dbip.transport,
        start=inline,
    )
    loader.ensure()
    return loader


def swapped(rows: Rows) -> Rows:
    rows[100], rows[101] = rows[101], rows[100]
    return rows


def overlapping(rows: Rows) -> Rows:
    """앞 구간의 끝에서 시작하는 구간 하나를 끼운다."""
    end = rows[50][1]
    rows.insert(51, [end, dotted(number(end) + 9), "US"])
    return rows


def reversed_range(rows: Rows) -> Rows:
    rows[7] = [rows[7][1], rows[7][0], *rows[7][2:]]
    return rows


def countries(n: int) -> Rows:
    """나라 값이 정확히 n 가지인 채움 행."""
    codes = [a + b for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" for b in "ABCDEFGHIJ"][:n]
    rows = fill_rows(FILL, [""])
    for i, row in enumerate(rows):
        row[2] = codes[i % n]
    return rows


def with_cell(rows: Rows, at: int, value: Any) -> Rows:
    rows[5][at] = value
    return rows


def with_row(rows: Rows, row: list[Any]) -> Rows:
    rows[5] = row
    return rows


def many_rows(n: int) -> bytes:
    """IPv4 /24 n 개 — 1.0.0.0 부터."""
    lines = (
        f"{a}.{b}.{c}.0,{a}.{b}.{c}.255,US\n"
        for a in range(1, 224)
        for b in range(256)
        for c in range(256)
    )
    text = "".join(line for _, line in zip(range(n), lines, strict=False))
    return gzip.compress(text.encode(), compresslevel=1)


CASES: dict[str, Callable[[], tuple[bytes | None, bytes | None]]] = {
    "IPv4 9,999 + IPv6 10,000": lambda: (
        gz(fill_rows(9_999, ["US"]) + ipv6_rows(["JP"], 10_000)),
        None,
    ),
    "IPv4 2,000,001": lambda: (many_rows(2_000_001), None),
    "start goes down": lambda: (gz(swapped(country_rows(PLACES))), None),
    "overlap": lambda: (gz(overlapping(country_rows(PLACES))), None),
    "start after end": lambda: (gz(reversed_range(country_rows(PLACES))), None),
    "lower case country": lambda: (gz(with_cell(country_rows(PLACES), 2, "kr")), None),
    "three letter country": lambda: (
        gz(with_cell(country_rows(PLACES), 2, "KOR")),
        None,
    ),
    "256 countries": lambda: (gz(countries(256)), None),
    "country columns": lambda: (
        gz(with_row(country_rows(PLACES), ["100.0.5.0", "100.0.5.255", "US", "x"])),
        None,
    ),
    "asn columns": lambda: (
        None,
        gz(with_row(asn_rows(PLACES), ["100.0.5.0", "100.0.5.255", "64512"])),
    ),
    "negative AS number": lambda: (None, gz(with_cell(asn_rows(PLACES), 2, "-1"))),
    "AS number with letters": lambda: (
        None,
        gz(with_cell(asn_rows(PLACES), 2, "AS64512")),
    ),
    "cut gzip": lambda: (gz(country_rows(PLACES))[:-200], None),
    "not gzip": lambda: (b"0.0.0.0,0.255.255.255,ZZ\n" * 10, None),
    "not UTF-8": lambda: (gzip.compress(b"\xff\xfe,\xff\n" * 10), None),
}


@pytest.mark.parametrize("case", list(CASES))
def test_files_that_fail_the_checks_are_bad_data(case: str) -> None:
    loader = load(*CASES[case]())
    assert loader.table is None and loader.waiting() == ("error", "bad_data"), case


def test_the_edges_pass_10000_rows_and_255_countries_and_quoted_names() -> None:
    loader = load(gz(countries(255)), gz(fill_rows(FILL, [64_512, 'A, "B" Hosting'])))
    table = loader.table
    assert table is not None
    assert (len(table.country), len(table.asn)) == (FILL, FILL)
    assert len(table.country.names) == 255


def test_loading_300k_country_rows_stays_under_8mb() -> None:
    # 합성 나라 IPv4 30만 행 + IPv6 10만 행 — 풀면 ≈11MB. 본문은 흘려 읽어 통째로 올리지 않는다
    big = gz(fill_rows(300_000, ["US"], start=1 << 24) + ipv6_rows(["JP"], 100_000))
    tracemalloc.start()
    try:
        loader = load(big)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert loader.table is not None and len(loader.table.country) == 300_000
    assert peak <= 8 * 1024 * 1024, peak


def test_the_text_reader_asks_for_1mb_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 풀기는 부를 때마다 GIL 을 놓았다 다시 잡는다 — 같은 프로세스에 CPU 를 쓰는 스레드가 있으면 그때마다 전환 간격을
    # 기다리므로 부르는 수가 적어야 한다. 한 번에 풀리는 양은 받은 조각(여기선 64KB 압축)이 정해 1MB 보다 작다.
    # 기본 8KB 단위면 이 본문(풀면 ≈12MB)에 ≈1,500번이다
    big = gz(fill_rows(300_000, ["US"], start=1 << 24) + ipv6_rows(["JP"], 100_000))
    calls: list[int] = []
    real = gzip.GzipFile.read1

    def counted(self: gzip.GzipFile, size: int = -1) -> bytes:
        calls.append(size)
        return real(self, size)

    monkeypatch.setattr(gzip.GzipFile, "read1", counted)
    loader = load(big)
    assert loader.table is not None and len(loader.table.country) == 300_000
    assert set(calls) == {TEXT_CHUNK}
    assert len(calls) * 64 * 1024 <= len(gzip.decompress(big)), len(calls)
