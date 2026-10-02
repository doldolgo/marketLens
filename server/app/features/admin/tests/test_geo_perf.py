"""DB-IP 성능 — 실제 두 파일로 ①적재 ②유지 메모리 ③조회 ④적재 중 루프 지연 ⑤CPU 스레드와 겹친 적재
(스펙 039 §3.7·§4 '성능').

실제 파일은 레포에 두지 않는다(CC BY 4.0 이지만 달마다 수 MB). `DBIP_DIR` 에 그 달의 `dbip-country-lite-YYYY-MM.csv.gz`·
`dbip-asn-lite-YYYY-MM.csv.gz` 를 두고 `DBIP_MONTH`(기본 2026-10)와 함께 돌릴 때만 돈다 — CI 에서는 건너뛴다.
  DBIP_DIR=<디렉터리> .venv/bin/pytest -q -s app/features/admin/tests/test_geo_perf.py
①·③·④ 는 tracemalloc 없이, ② 는 따로 잰다(tracemalloc 이 적재를 몇 배 늦춘다). 본문은 64KB 조각으로 흘려 넣는다.
⑤ 는 같은 프로세스에 CPU 를 쓰는 스레드(038 요약·040 묶기 흉내)가 겹친 때다 — 풀기가 GIL 을 놓았다 다시 잡을 때마다
전환 간격을 기다려, 읽기 단위가 작으면 적재가 수십 초로 는다(8KB 단위로 ≈40초 쟀다).
"""

import asyncio
import gc
import json
import os
import random
import statistics
import threading
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.features.admin.geo_fetch import GeoLoader
from app.features.admin.geo_table import GeoTable, locate
from app.features.admin.tests.geo_fakes import Chunks

DIR = os.environ.get("DBIP_DIR")
MONTH = os.environ.get("DBIP_MONTH", "2026-10")
pytestmark = pytest.mark.skipif(
    not DIR, reason="DBIP_DIR 없음 — 실제 DB-IP 파일로만 잰다"
)


def files() -> dict[str, bytes]:
    root = Path(DIR or ".")
    names = [f"dbip-{kind}-lite-{MONTH}.csv.gz" for kind in ("country", "asn")]
    return {name: (root / name).read_bytes() for name in names}


def loader(data: dict[str, bytes], start: object = None) -> GeoLoader:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, stream=Chunks(data[request.url.path.rsplit("/", 1)[1]])
        )

    year, mon = map(int, MONTH.split("-"))
    return GeoLoader(
        clock=lambda: datetime(year, mon, 15, tzinfo=UTC).timestamp(),
        warn=lambda name, code: pytest.fail(f"{name} {code}"),
        transport=httpx.MockTransport(handle),
        start=start or (lambda job: job()),  # type: ignore[arg-type]
    )


def load(data: dict[str, bytes]) -> GeoTable:
    one = loader(data)
    one.ensure()
    assert one.table is not None
    return one.table


def test_load_memory_lookup_and_loop_lag() -> None:
    data = files()
    times = []
    for _ in range(5):
        gc.collect()
        began = time.perf_counter()
        table = load(data)
        times.append(time.perf_counter() - began)
    took = statistics.median(times)

    rng = random.Random(39)
    ips = [
        f"{rng.randrange(1, 224)}.{rng.randrange(256)}.{rng.randrange(256)}.0"
        for _ in range(10_000)
    ]
    best = float("inf")
    for _ in range(5):
        began = time.perf_counter()
        for ip in ips:
            locate(table, ip)
        best = min(best, time.perf_counter() - began)
    del table

    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        table = load(data)
        gc.collect()
        kept = tracemalloc.get_traced_memory()[0] - base
    finally:
        tracemalloc.stop()

    async def lag() -> float:
        threads: list[threading.Thread] = []

        def start(job: object) -> None:
            threads.append(threading.Thread(target=job, daemon=True))  # type: ignore[arg-type]
            threads[-1].start()

        one = loader(data, start)
        one.ensure()
        worst = 0.0
        while one.table is None:
            began = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - began - 0.01)
        threads[0].join()
        return worst

    worst = max(asyncio.run(lag()) for _ in range(3))

    stop = threading.Event()
    blob = json.dumps({"k": list(range(2_000))})

    def spin() -> None:
        while not stop.is_set():
            json.loads(blob)

    spinner = threading.Thread(target=spin, daemon=True)
    spinner.start()
    try:
        began = time.perf_counter()
        load(data)
        busy = time.perf_counter() - began
    finally:
        stop.set()
        spinner.join()
    print(
        f"\n① 두 파일 적재 {took:.3f}초(중앙값 5회, serve ×6 짐작 {took * 6:.1f}초)"
        f" · 구간 나라 {len(table.country):,}·ASN {len(table.asn):,}"
        f"\n② 유지 메모리 {kept / 1e6:.2f}MB\n③ 조회 1만 건 {best * 1000:.1f}ms"
        f"\n④ 적재 중 루프 지연 최댓값 {worst * 1000:.1f}ms(3회)"
        f"\n⑤ CPU 스레드 하나와 겹친 적재 {busy:.2f}초"
    )
    assert took <= 1.0
    assert kept <= 10 * 1024 * 1024
    assert best <= 0.050
    assert worst <= 0.050
    assert busy <= 10 * took  # 혼자의 10배 안 — 8KB 단위면 ≈58배였다
