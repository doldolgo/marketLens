"""DB-IP City 받기·볼륨 캐시 파일 — 기동 때 파일만 읽기·이번 달 받기·지난달 대신·실패 시 옛 판·상한·볼륨 안 한 파일
(스펙 069 §3.3·§4 '위치')."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.features.admin import geo_city
from app.features.admin.geo_city_fetch import CityLoader
from app.features.admin.tests.city_fakes import city_rows, gz, parsed
from app.features.admin.tests.geo_fakes import BASE, Chunks, DbIp, Jobs, inline

OCT = datetime(2026, 10, 15, tzinfo=UTC).timestamp()
NOV = datetime(2026, 11, 2, tzinfo=UTC).timestamp()
HOURS6 = 6 * 3600


def url(month: str) -> str:
    return f"{BASE}dbip-city-lite-{month}.csv.gz"


class Rig:
    """손으로 움직이는 시계·가짜 DB-IP·경고 기록."""

    def __init__(self, directory: Path | None, start=inline, **kw: object) -> None:  # noqa: ANN001
        self.now = OCT
        self.mono = 0.0
        self.warned: list[tuple[str, str | None]] = []
        self.dbip = DbIp()
        self.loader = CityLoader(
            None if directory is None else str(directory),
            clock=lambda: self.now,
            mono=lambda: self.mono,
            warn=lambda name, code: self.warned.append((name, code)),
            transport=self.dbip.transport,
            start=start,
            **kw,  # type: ignore[arg-type]
        )

    def serve(self, month: str, data: bytes | int | None = None) -> None:
        self.dbip.files[url(month)] = gz(city_rows()) if data is None else data


def _rows() -> list[list[str]]:
    return parsed(city_rows())


def months(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def test_without_a_cache_directory_nothing_is_read_or_fetched() -> None:
    rig = Rig(None)
    rig.serve("2026-10")
    rig.loader.boot()
    rig.loader.ensure()
    assert rig.loader.table is None and rig.dbip.requests == []


def test_boot_reads_the_volume_file_and_fetches_nothing(tmp_path: Path) -> None:
    geo_city.write(geo_city.read_city(_rows(), "2026-10"), str(tmp_path))
    rig = Rig(tmp_path)
    rig.loader.boot()
    assert rig.loader.table is not None and rig.loader.table.month == "2026-10"
    rig.loader.ensure()  # 이번 달 판 — 받지 않는다
    assert rig.dbip.requests == []


def test_boot_without_a_file_waits_for_the_feeds(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.serve("2026-10")
    rig.loader.boot()
    assert rig.loader.table is None and rig.dbip.requests == []
    rig.loader.ensure()
    assert rig.loader.table is not None and rig.dbip.urls == [url("2026-10")]
    assert months(tmp_path) == ["city-2026-10.bin"]
    assert (
        geo_city.read(tmp_path / "city-2026-10.bin").find("1.0.0.7").city
        == "South Brisbane"
    )
    rig.loader.ensure()
    assert len(rig.dbip.requests) == 1


def test_a_new_month_replaces_the_file_and_keeps_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.serve("2026-10")
    rig.loader.ensure()
    rig.now = NOV
    rig.mono += 86_400
    rig.serve("2026-11")
    rig.loader.ensure()
    assert rig.loader.table.month == "2026-11"  # type: ignore[union-attr]
    assert months(tmp_path) == ["city-2026-11.bin"]


def test_a_failed_month_keeps_the_old_table_and_retries_after_six_hours(
    tmp_path: Path,
) -> None:
    geo_city.write(geo_city.read_city(_rows(), "2026-10"), str(tmp_path))
    rig = Rig(tmp_path)
    rig.now = NOV
    rig.serve("2026-11", 404)
    rig.loader.ensure()
    assert rig.loader.table.month == "2026-10"  # type: ignore[union-attr]
    assert rig.warned == [("geo.city", "http_404")]
    assert rig.dbip.urls == [url("2026-11")]  # 판이 있으면 지난달을 받지 않는다
    rig.mono += HOURS6 - 1
    rig.loader.ensure()
    assert len(rig.dbip.requests) == 1
    rig.mono += 1
    rig.serve("2026-11")
    rig.loader.ensure()
    assert rig.loader.table.month == "2026-11"  # type: ignore[union-attr]
    assert months(tmp_path) == ["city-2026-11.bin"]


def test_with_no_table_a_failed_month_falls_back_to_the_previous(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.now = NOV
    rig.serve("2026-11", 404)
    rig.serve("2026-10")
    rig.loader.ensure()
    assert rig.loader.table.month == "2026-10"  # type: ignore[union-attr]
    assert rig.dbip.urls == [url("2026-11"), url("2026-10")]
    assert rig.warned == [("geo.city", "http_404")]
    assert months(tmp_path) == ["city-2026-10.bin"]


@pytest.mark.parametrize("case", ["bad_rows", "cap", "timeout", "gzip"])
def test_bad_downloads_fail_without_a_table(tmp_path: Path, case: str) -> None:
    rig = Rig(tmp_path, max_bytes=50_000 if case == "cap" else 150_000_000)
    rows = city_rows()
    data = gz(rows)
    if case == "bad_rows":
        rows[3], rows[4] = rows[4], rows[3]
        data = gz(rows)
    elif case == "gzip":
        data = data[: len(data) // 2]
    if case == "timeout":

        def tick(_: bytes) -> None:
            rig.mono += 400  # 첫 조각 뒤 300초 상한을 넘는다

        rig.dbip.files[url("2026-10")] = lambda request: httpx.Response(
            200, stream=Chunks(data, size=4096, each=tick)
        )
        rig.dbip.files[url("2026-09")] = 404
    else:
        rig.serve("2026-10", data)
    rig.loader.ensure()
    assert rig.loader.table is None
    assert rig.warned[0] == ("geo.city", "timeout" if case == "timeout" else "bad_data")
    assert months(tmp_path) == []


def test_a_broken_volume_file_is_replaced_by_a_download(tmp_path: Path) -> None:
    (tmp_path / "city-2026-10.bin").write_bytes(b"broken")
    rig = Rig(tmp_path)
    rig.loader.boot()
    assert rig.loader.table is None
    assert rig.warned == [("geo.city", "bad_file_BadData")]
    rig.serve("2026-10")
    rig.loader.ensure()
    assert rig.loader.table is not None
    assert geo_city.read(tmp_path / "city-2026-10.bin").month == "2026-10"


def test_a_failed_write_still_loads_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise PermissionError

    monkeypatch.setattr(geo_city, "write", fail)
    rig = Rig(tmp_path)
    rig.serve("2026-10")
    rig.loader.ensure()
    assert rig.loader.table is not None
    assert rig.warned == [("geo.city", "PermissionError")]


def test_one_job_at_a_time_and_the_old_table_serves_meanwhile(tmp_path: Path) -> None:
    geo_city.write(geo_city.read_city(_rows(), "2026-10"), str(tmp_path))
    jobs = Jobs()
    rig = Rig(tmp_path, start=jobs)
    rig.loader.boot()
    rig.loader.ensure()  # 읽는 중 — 둘째 일을 띄우지 않는다
    assert len(jobs.jobs) == 1
    jobs.run()
    rig.now = NOV
    rig.serve("2026-11")
    rig.loader.ensure()
    rig.loader.ensure()
    assert len(jobs.jobs) == 1
    assert rig.loader.table.month == "2026-10"  # type: ignore[union-attr]
    jobs.run()
    assert rig.loader.table.month == "2026-11"  # type: ignore[union-attr]
