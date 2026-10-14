"""DB-IP City 간결한 표 — 확인·조회 경계·이진 파일 쓰기/읽기·볼륨 안 한 파일·메모리 상한 (스펙 069 §3.3·§4 '위치')."""

import tracemalloc
from pathlib import Path

import pytest

from app.features.admin import geo_city
from app.features.admin.geo_city import City, read_city
from app.features.admin.geo_table import BadData
from app.features.admin.tests.city_fakes import MONTH, city_rows, parsed

GANGNAM = 'Gangnam-gu, "Yeoksam"'


def table() -> geo_city.CityTable:
    return read_city(parsed(city_rows()), MONTH)


def test_lookup_finds_the_range_from_its_first_to_its_last_address() -> None:
    t = table()
    brisbane = City("AU", "Queensland", "South Brisbane", -27.4767, 153.017)
    assert t.find("1.0.0.0") == brisbane
    assert t.find("1.0.0.255") == brisbane
    assert t.find("1.0.2.0").city == "Wenquan"
    assert t.find("1.0.3.255").city == "Wenquan"


def test_gaps_and_addresses_outside_every_range_have_no_place() -> None:
    t = table()
    assert t.find("1.0.1.0") is None  # 구간 사이 빈칸
    assert t.find("1.0.1.255") is None
    assert t.find("0.255.255.255") is None  # 첫 구간 앞
    assert t.find("255.255.255.255") is None  # 마지막 구간 뒤
    assert t.find("2001:db8::1") is None  # IPv6 는 찾지 않는다
    assert t.find("not-an-ip") is None


def test_one_place_keeps_its_first_coordinates_and_quoted_names() -> None:
    t = table()
    first = City("KR", "Seoul", GANGNAM, 37.5, 127.03)
    assert t.find("203.0.113.5") == first
    assert t.find("203.0.113.200") == first
    assert t.places == 50 + 4  # 채움 50 + 장소 넷(강남 하나)


def test_empty_region_and_city_are_none() -> None:
    assert table().find("198.51.100.7") == City("US", None, None, 37.751, -97.822)


@pytest.mark.parametrize(
    "case",
    ["few", "unsorted", "overlap", "columns", "country", "lat", "lon", "ip", "tab"],
)
def test_files_that_fail_the_checks_are_bad_data(case: str) -> None:
    rows = city_rows()
    if case == "few":
        rows = city_rows(fill=10_000 - 6)  # IPv4 9,999행
    elif case == "unsorted":
        rows[3], rows[4] = rows[4], rows[3]
    elif case == "overlap":
        rows[1][0] = rows[0][1]
    elif case == "columns":
        rows[2] = rows[2][:7]
    elif case == "country":
        rows[5][3] = "kr"
    elif case == "lat":
        rows[5][6] = "91"
    elif case == "lon":
        rows[5][7] = "nan"
    elif case == "ip":
        rows[5][0] = "1.2.3"
    else:
        rows[5][5] = "a\tb"
    with pytest.raises(BadData):
        read_city(parsed(rows), MONTH)


def test_the_file_round_trips_and_is_the_only_city_file_in_the_volume(
    tmp_path: Path,
) -> None:
    (tmp_path / "city-2026-09.bin").write_bytes(b"old month")
    (tmp_path / ".city-2026-08.bin.tmp").write_bytes(b"left over")
    (tmp_path / "other.txt").write_text("not ours")
    t = table()
    path = geo_city.write(t, str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "city-2026-10.bin",
        "other.txt",
    ]
    assert geo_city.latest(str(tmp_path)) == path
    back = geo_city.read(path)
    assert back.month == MONTH and len(back) == len(t) and back.places == t.places
    for ip in ("1.0.0.0", "1.0.1.0", "203.0.113.200", "198.51.100.7", "100.0.3.4"):
        assert back.find(ip) == t.find(ip), ip


@pytest.mark.parametrize("damage", ["flip", "cut", "extra", "magic", "empty"])
def test_a_damaged_file_is_bad_data(tmp_path: Path, damage: str) -> None:
    path = geo_city.write(table(), str(tmp_path))
    data = bytearray(path.read_bytes())
    if damage == "flip":
        data[len(data) // 2] ^= 0xFF
    elif damage == "cut":
        data = data[:-10]
    elif damage == "extra":
        data += b"x"
    elif damage == "magic":
        data[:8] = b"NOTCITY!"
    else:
        data = bytearray()
    path.write_bytes(bytes(data))
    with pytest.raises(BadData):
        geo_city.read(path)


def test_no_file_and_no_directory_mean_no_latest(tmp_path: Path) -> None:
    assert geo_city.latest(str(tmp_path)) is None
    assert geo_city.latest(str(tmp_path / "missing")) is None


def test_memory_stays_under_60mb_at_the_real_size() -> None:
    """2026-10 판 크기(IPv4 3,620,434행·위치 166,890개)로 늘린 근사 — 행마다 12바이트 + 위치마다 글자·좌표·자리.
    표 밖에 남는 파이썬 객체가 없는지 tracemalloc 으로 같이 본다."""
    rows = parsed(city_rows(fill=60_000, fill_places=3_000))
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        t = read_city(rows, MONTH)
        kept = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert kept < t.nbytes() * 1.3 + 64_000  # 배열 여유분(늘릴 때 미리 잡는 몫)만
    per_row = (t.starts.itemsize + t.ends.itemsize + t.locs.itemsize) * 1.15
    per_place = (len(t.blob) + 4 * (t.places + 1)) / t.places + 8
    real_place = (
        per_place + 15
    )  # 실제 지역·도시 이름은 가짜('Region k\tTown k')보다 길다(평균 28바이트)
    assert 3_620_434 * per_row + 166_890 * real_place < 60_000_000
