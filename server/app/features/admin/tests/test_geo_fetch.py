"""DB-IP 받기 — 두 주소·UTC 달·리다이렉트·지난달·다시 부르기·시한·바이트 상한·한 번에 하나 (스펙 039 §3.3·§4 '받기')."""

import asyncio
import threading
import time
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.core.config import USER_AGENT
from app.features.admin.geo_fetch import MAX_BYTES, GeoLoader
from app.features.admin.tests.access_fakes import AFTER, Feeds, line, write
from app.features.admin.tests.geo_fakes import (
    MONTH,
    Chunks,
    DbIp,
    Jobs,
    bundle,
    inline,
    url,
)

# 2026-10-31T16:00Z = KST 11-01 01:00 — 달은 UTC 로 센다
EDGE = datetime(2026, 10, 31, 16, tzinfo=UTC).timestamp()


class Clock:
    def __init__(self, base: float) -> None:
        self.base, self.t = base, 0.0

    def now(self) -> float:
        return self.base + self.t

    def mono(self) -> float:
        return self.t


def make(
    dbip: DbIp, base: float = AFTER, **kw: object
) -> tuple[GeoLoader, Clock, list]:
    clock, warns = Clock(base), []
    kw.setdefault("start", inline)
    loader = GeoLoader(
        clock=clock.now,
        mono=clock.mono,
        warn=lambda name, code: warns.append((name, code)),
        transport=dbip.transport,
        **kw,  # type: ignore[arg-type]
    )
    return loader, clock, warns


def test_fetches_the_two_urls_of_the_utc_month_with_get_and_the_app_agent() -> None:
    dbip = DbIp().serve()
    loader, _, warns = make(dbip, EDGE)
    loader.ensure()
    assert dbip.urls == [url("country"), url("asn")]
    for request in dbip.requests:
        assert request.method == "GET"
        assert request.headers["user-agent"] == USER_AGENT
    assert loader.table is not None and loader.table.month == MONTH
    assert loader.table.loaded_at == int(EDGE * 1000) and warns == []


def test_a_redirect_is_an_error_and_is_not_followed() -> None:
    dbip = DbIp().serve()
    dbip.files[url("country")] = lambda r: httpx.Response(
        302, headers={"location": url("country", "2026-09")}
    )
    loader, _, warns = make(dbip)
    loader.ensure()
    assert dbip.urls == [url("country")]
    assert loader.table is None and loader.waiting() == ("error", "http_302")
    assert warns == [("geo", "http_302")]


@pytest.mark.parametrize("missing", ["country", "asn"])
def test_this_month_404_without_a_table_falls_back_to_last_month(missing: str) -> None:
    dbip = DbIp().serve("2026-09")
    if missing == "asn":
        dbip.files[url("country")] = bundle()[0]
    loader, _, _ = make(dbip)
    loader.ensure()
    tried = [url("country")] + ([url("asn")] if missing == "asn" else [])
    assert dbip.urls == tried + [url("country", "2026-09"), url("asn", "2026-09")]
    assert loader.table is not None and loader.table.month == "2026-09"


def test_two_months_404_is_an_error_and_retried_once_an_hour_later() -> None:
    dbip = DbIp()
    loader, clock, warns = make(dbip)
    loader.ensure()
    assert dbip.urls == [url("country"), url("country", "2026-09")]
    assert loader.waiting() == ("error", "http_404") and warns == [("geo", "http_404")]
    clock.t = 3599
    loader.ensure()
    assert dbip.count("country") == 1  # 1시간 안 — 부르지 않는다
    clock.t = 3600
    loader.ensure()
    assert dbip.count("country") == 2


def test_an_asn_failure_keeps_the_old_table_and_loads_nothing_without_one() -> None:
    dbip = DbIp().serve()
    dbip.files[url("asn")] = 500
    loader, _, _ = make(dbip)
    loader.ensure()
    assert loader.table is None and loader.waiting() == ("error", "http_500")
    dbip = DbIp().serve()
    loader, clock, _ = make(dbip)
    loader.ensure()
    old = loader.table
    dbip.serve("2026-11")
    dbip.files[url("asn", "2026-11")] = 503
    clock.t = (
        datetime(2026, 11, 2, tzinfo=UTC).timestamp() - AFTER
    )  # 시계 둘이 함께 간다
    loader.ensure()
    assert dbip.count("country", "2026-11") == 1 and loader.table is old


def test_a_file_over_60_seconds_is_a_timeout() -> None:
    dbip = DbIp().serve()
    loader, clock, _ = make(dbip)

    def slow(piece: bytes) -> None:
        clock.t += 61  # 가짜 시계 — 조각 하나에 61초

    country = dbip.files[url("country")]
    dbip.files[url("country")] = lambda r: httpx.Response(
        200, stream=Chunks(country, 4096, slow)
    )
    loader.ensure()
    assert loader.waiting() == ("error", "timeout") and loader.table is None


def test_a_read_timeout_and_a_connection_failure_name_their_codes() -> None:
    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    for handler, code in ((hang, "timeout"), (refuse, "ConnectError")):
        dbip = DbIp()
        dbip.files[url("country")] = handler
        loader, _, warns = make(dbip)
        loader.ensure()
        assert loader.waiting() == ("error", code) and warns == [("geo", code)]


def test_a_body_over_20mb_is_bad_data_and_not_read_on() -> None:
    piece = 1_000_000
    # 압축하지 않은(stored) gzip — 받은 바이트가 곧 본문 크기다. IPv6 행이라 확인에 걸리기 전에 크기에 걸린다
    row = b"2001:db8::," + b"f" * 1000 + b",JP\n"

    def body() -> Iterator[bytes]:
        packer = zlib.compressobj(0, zlib.DEFLATED, 31)
        while True:
            yield packer.compress(row * (piece // len(row)))

    dbip = DbIp()
    stream = Chunks(body())
    dbip.files[url("country")] = lambda r: httpx.Response(200, stream=stream)
    loader, _, _ = make(dbip)
    loader.ensure()
    assert loader.waiting() == ("error", "bad_data")
    assert MAX_BYTES < stream.sent <= MAX_BYTES + piece + 1024


async def test_a_second_refresh_during_a_fetch_starts_nothing(tmp_path: Path) -> None:
    write(tmp_path, "access.log", [line(AFTER - 60, "/")])
    dbip, jobs = DbIp().serve(), Jobs()
    f = Feeds(tmp_path, AFTER, geo_transport=dbip.transport, geo_start=jobs)
    first = await f.get()
    assert (first["state"], first["geo"]["state"]) == ("ok", "pending")
    f.t = 60
    assert (await f.get())["geo"]["state"] == "pending" and len(jobs.jobs) == 1
    jobs.run()
    assert (dbip.count("country"), dbip.count("asn")) == (1, 1)
    f.t = 120
    geo = (await f.get())["geo"]
    assert (geo["state"], geo["month"]) == ("ok", MONTH)


async def test_access_answers_within_3_seconds_while_the_fetch_is_held(
    tmp_path: Path,
) -> None:
    write(tmp_path, "access.log", [line(AFTER - 60, "/")])
    release, threads = threading.Event(), []
    dbip = DbIp().serve()
    country = dbip.files[url("country")]

    def held(request: httpx.Request) -> httpx.Response:
        release.wait(10)
        return httpx.Response(200, stream=Chunks(country))

    def start(job: object) -> None:
        threads.append(threading.Thread(target=job, daemon=True))  # type: ignore[arg-type]
        threads[-1].start()

    dbip.files[url("country")] = held
    f = Feeds(tmp_path, AFTER, geo_transport=dbip.transport, geo_start=start)
    began = time.perf_counter()
    body = await asyncio.wait_for(f.get(), 3)
    assert time.perf_counter() - began < 3
    assert (body["state"], body["geo"]["state"], body["geo"]["code"]) == (
        "ok",
        "pending",
        None,
    )
    release.set()
    threads[0].join(10)
    assert f.feeds.geo.table is not None
