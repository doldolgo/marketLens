"""IP 칸의 메모리 — 지금 규모(하루 700줄·IP 150, 30일)에서 30일 창 캐시에 더해지는 것이 20MiB 아래, 읽은 뒤 메모 비움
(스펙 069 §3.2 '메모리')."""

import tracemalloc
from pathlib import Path

from app.features.admin.access_addrs_page import addrs_query
from app.features.admin.access_cache import AccessLog
from app.features.admin.tests.access_fakes import (
    CHROME,
    GATE,
    HOUR,
    IPHONE,
    line,
    rotated,
    write,
)

DAY = 86_400
NOW = GATE + 31 * DAY + 10 * HOUR


def month(directory: Path) -> None:
    for d in range(30):
        start = NOW - (30 - d) * DAY
        lines = [
            line(
                start + i * 120,
                ("/", "/app/", "/clarity.js", "/assets/a.js", "/.env", "/robots.txt")[
                    i % 6
                ],
                ua=(CHROME, IPHONE, "curl/8.7.1")[i % 3] + f" {i % 40}",
                ip=f"10.{d}.{i % 150 // 250}.{i % 150 + 1}",
            )
            for i in range(700)
        ]
        rotated(directory, lines)
    write(directory, "access.log", [line(NOW - 60, "/", ip="10.99.0.1")])


def test_thirty_days_of_ip_cells_stay_under_20mib(tmp_path: Path) -> None:
    month(tmp_path)
    log = AccessLog(str(tmp_path), GATE)
    tracemalloc.start(1)
    try:
        log.addrs("30d", NOW, addrs_query())
        snap = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
    kept = snap.filter_traces([tracemalloc.Filter(True, "*access_addrs.py")])
    size = sum(stat.size for stat in kept.statistics("filename"))
    assert size < 20 * 1024 * 1024, size
    body = log.addrs("30d", NOW, addrs_query(limit="500"))[0]
    assert body["total"] == 30 * 150 + 1
    for entry in log._files.values():
        addrs = entry.tally.addrs
        assert not (
            addrs._ips or addrs._uas or addrs._refs or addrs._paths
        )  # 다 읽으면 메모를 버린다
        assert all(
            len(row.paths) <= 11 and len(row.agents) <= 6 for row in addrs.rows.values()
        )
