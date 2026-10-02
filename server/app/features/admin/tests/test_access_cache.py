"""회전 파일별 메모리 캐시 — 다시 열지 않음·access.log 60초·60초 안 회전·사라짐·압축 짝·크기 바뀜·시작점 이동·
한 번에 하나·첫 채움 pending·압축 풀린 100MiB·1시간 비움 (스펙 038 §3.3·§3.8·§4)."""

import asyncio
import gzip
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.features.admin import access_cache, access_hours
from app.features.admin.access_cache import raw_size, read_start
from app.features.admin.access_pairs import pair_hash
from app.features.admin.tests.access_fakes import (
    AFTER,
    DAY,
    GATE,
    HOUR,
    Feeds,
    line,
    rotated,
    watch_opens,
    write,
)

T0 = AFTER - 3 * HOUR


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    return watch_opens(monkeypatch)


async def test_rotated_files_are_read_once_and_access_log_every_60_seconds(
    tmp_path: Path, opened: Counter[str]
) -> None:
    old = rotated(tmp_path, [line(T0, "/a")])
    write(tmp_path, "access.log", [line(T0 + HOUR, "/b")])
    f = Feeds(tmp_path, AFTER)
    assert (await f.get())["totals"]["requests"] == 2
    f.t = 30
    assert (await f.get("7d"))["totals"][
        "requests"
    ] == 2  # 다른 창 — 60초 안이라 access.log 도 다시 안 읽는다
    assert opened == {old.name: 1, "access.log": 1}
    f.t = 61
    write(tmp_path, "access.log", [line(T0 + HOUR, "/b"), line(T0 + HOUR + 1, "/c")])
    assert (await f.get())["totals"]["requests"] == 3
    assert opened == {old.name: 1, "access.log": 2}


@pytest.mark.parametrize("how", ["rename", "copytruncate"])
async def test_a_rotation_inside_60_seconds_never_counts_a_line_twice(
    tmp_path: Path, opened: Counter[str], how: str
) -> None:
    # 24시간 갱신 → caddy 회전 → 30초 뒤 다른 창. 옛 access.log 결과를 새 회전 파일과 함께 세면 안 된다(§3.8)
    lines = [
        line(T0 + i, "/" if i % 2 else "/boom", status=200 if i % 2 else 500)
        for i in range(20)
    ]
    log = write(tmp_path, "access.log", lines)
    f = Feeds(tmp_path, AFTER)
    assert (await f.get())["totals"]["requests"] == 20
    stamp = datetime.fromtimestamp(T0 + 19, UTC).strftime("%Y-%m-%dT%H-%M-%S.%f")
    moved = tmp_path / f"access-{stamp[:-3]}-time.log"
    if how == "rename":  # caddy — 옮기고 새 파일(새 inode)
        log.rename(moved)
        log.write_text("")
    else:  # 같은 inode 를 비움 — 크기가 줄었다
        moved.write_bytes(log.read_bytes())
        log.write_text("")
    os.utime(moved, (T0 + 19, T0 + 19))
    f.t = 30
    week = await f.get("7d")
    assert week["totals"]["requests"] == 20 and week["status"]["5xx"] == 10
    assert sum(h["requests"] for h in week["hourly"]) == 20
    errors = {(e["ts"], e["path"]) for e in week["recent5xx"]}
    assert len(errors) == len(week["recent5xx"]) == 10
    assert opened == {"access.log": 2, moved.name: 1}


async def test_a_vanished_rotated_file_takes_its_counts_with_it(tmp_path: Path) -> None:
    old = rotated(tmp_path, [line(T0, "/a")])
    write(tmp_path, "access.log", [line(T0 + HOUR, "/b")])
    f = Feeds(tmp_path, AFTER)
    assert (await f.get())["totals"]["requests"] == 2
    old.unlink()
    f.t = 61
    assert (await f.get())["paths"] == [["/b", 1]]


async def test_compressing_pair_and_switch_to_gz_never_count_twice(
    tmp_path: Path, opened: Counter[str]
) -> None:
    lines = [line(T0 + i, "/r") for i in range(5)]
    plain = rotated(tmp_path, lines, gz=False)
    (tmp_path / (plain.name + ".gz")).write_bytes(b"\x1f\x8b")  # 덜 쓴 gz
    f = Feeds(tmp_path, AFTER)
    assert (await f.get())["totals"]["requests"] == 5
    plain.unlink()
    rotated(
        tmp_path, lines
    )  # 압축이 끝났다 — 같은 키, 크기·시각이 달라 한 번 다시 만든다
    f.t = 61
    assert (await f.get())["totals"]["requests"] == 5
    f.t = 122
    assert (await f.get())["totals"]["requests"] == 5
    assert opened == {plain.name: 1, plain.name + ".gz": 1}


async def test_a_rotated_file_whose_size_changed_is_read_again(tmp_path: Path) -> None:
    rotated(tmp_path, [line(T0, "/a")])
    f = Feeds(tmp_path, AFTER)
    assert (await f.get())["totals"]["requests"] == 1
    rotated(tmp_path, [line(T0 - 5, "/z"), line(T0, "/a")])
    f.t = 61
    assert (await f.get())["totals"]["requests"] == 2


async def test_a_read_start_moving_into_a_file_rebuilds_its_first_page_line(
    tmp_path: Path, opened: Counter[str]
) -> None:
    now = GATE + 40 * DAY + 10 * HOUR + 1800
    s0 = read_start(now, GATE)
    early, late = s0 + 600, s0 + HOUR + 600
    path = rotated(
        tmp_path,
        [
            line(early, "/", status=304, referer="https://www.google.com"),
            line(late, "/", referer="https://x.com"),
            line(late + 1, "/clarity.js"),
        ],
    )
    f = Feeds(tmp_path, now)
    first = (await f.get("30d"))["visitors"]
    assert (first["channels"], first["returning"]) == ([["search", 1, 1]], 1)
    f.t = HOUR  # 시작점이 한 시간 옮겨 early 를 지난다
    body = await f.get("30d")
    assert (body["visitors"]["channels"], body["visitors"]["returning"]) == (
        [["social", 1, 1]],
        0,
    )
    assert body["firstTs"] == int(late) and opened[path.name] == 2


async def test_two_windows_at_once_open_a_rotated_file_once(
    tmp_path: Path, opened: Counter[str]
) -> None:
    old = rotated(tmp_path, [line(T0, "/a")])
    write(tmp_path, "access.log", [line(T0 + 1, "/b")])
    f = Feeds(tmp_path, AFTER)
    day, week = await asyncio.gather(f.get("24h"), f.get("7d"))
    assert day["totals"]["requests"] == week["totals"]["requests"] == 2
    assert opened == {old.name: 1, "access.log": 1}


async def test_a_slow_first_fill_is_pending_then_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rotated(tmp_path, [line(T0, "/a")])
    real = access_cache.open_log

    def slow(path: str) -> Any:
        time.sleep(0.3)
        return real(path)

    monkeypatch.setattr(access_cache, "open_log", slow)
    f = Feeds(tmp_path, AFTER, wait_sec=0.05)
    assert (await f.get())["state"] == "pending"
    await asyncio.sleep(0.4)
    body = await f.get()
    assert (body["state"], body["totals"]["requests"]) == ("ok", 1)


def _same(directory: Path, ts: float, *, gz: bool = True) -> Path:
    """잘 눌리는 회전 파일 — 같은 줄 200개(압축 풀린 크기 ≈ 압축 크기의 수십 배)."""
    return rotated(directory, [line(ts + i, "/same") for i in range(200)], gz=gz)


def _raw(path: Path) -> int:
    if path.name.endswith(".gz"):
        with gzip.open(path, "rb") as f:
            return len(f.read())
    return path.stat().st_size


async def test_rotated_files_past_the_uncompressed_budget_are_neither_read_nor_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opened: Counter[str]
) -> None:
    assert access_cache.BUDGET_BYTES == 104_857_600
    oldest = _same(tmp_path, T0 - 3 * HOUR)
    middle = _same(tmp_path, T0 - 2 * HOUR)
    raw = _raw(middle)
    monkeypatch.setattr(access_cache, "BUDGET_BYTES", 2 * raw + raw // 2)
    f = Feeds(tmp_path, AFTER)
    assert (await f.get())["totals"]["requests"] == 400
    newest = _same(tmp_path, T0 - HOUR)
    # 압축 크기로 세면 셋 다 들어간다 — 잘 눌리는 몰림도 줄 수로 묶는다
    assert sum(p.stat().st_size for p in (oldest, middle, newest)) < raw
    f.t = 61
    body = await f.get()
    assert body["totals"]["requests"] == 400 and body["firstTs"] == int(T0 - 2 * HOUR)
    assert opened == {oldest.name: 1, middle.name: 1, newest.name: 1}


async def test_a_plain_rotated_log_counts_its_size_even_while_compressing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opened: Counter[str]
) -> None:
    oldest = _same(tmp_path, T0 - 3 * HOUR)
    _same(tmp_path, T0 - 2 * HOUR)
    plain = _same(tmp_path, T0 - HOUR, gz=False)
    # 압축 중 — 덜 쓴 gz 의 꼬리(ISIZE 0)가 아니라 `.log` 의 크기로 센다
    (tmp_path / (plain.name + ".gz")).write_bytes(b"\x1f\x8b\x08\x00\x00\x00\x00\x00")
    raw = _raw(plain)
    monkeypatch.setattr(access_cache, "BUDGET_BYTES", 2 * raw + raw // 2)
    body = await Feeds(tmp_path, AFTER).get()
    assert body["totals"]["requests"] == 400 and body["firstTs"] == int(T0 - 2 * HOUR)
    assert oldest.name not in opened and opened[plain.name] == 1


def test_raw_size_reads_the_gzip_tail_and_calls_a_bad_tail_broken(
    tmp_path: Path,
) -> None:
    gz = _same(tmp_path, T0)
    assert raw_size(str(gz), gz.stat().st_size) == _raw(gz) > gz.stat().st_size
    plain = _same(tmp_path, T0 + HOUR, gz=False)
    assert raw_size(str(plain), plain.stat().st_size) == plain.stat().st_size
    # 꼬리 4바이트 미만·ISIZE 가 100MiB 넘음(잘린 gz 의 아무 값) → 깨진 파일로 0
    short = tmp_path / "access-a-time.log.gz"
    short.write_bytes(b"\x1f\x8b")
    huge = tmp_path / "access-b-time.log.gz"
    huge.write_bytes(b"\x1f\x8b" + (104_857_601).to_bytes(4, "little"))
    assert raw_size(str(short), 2) == raw_size(str(huge), 6) == 0
    assert raw_size(str(tmp_path / "access-gone-time.log.gz"), 99) is None


async def test_an_hour_without_requests_drops_caches_pairs_and_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opened: Counter[str]
) -> None:
    keys: list[bytes] = []

    def watched(key: bytes, ip: str, ua: str) -> bytes:
        keys.append(key)
        return pair_hash(key, ip, ua)

    monkeypatch.setattr(access_hours, "pair_hash", watched)
    old = rotated(tmp_path, [line(T0, "/")])
    f = Feeds(tmp_path, AFTER, urandom=os.urandom)
    assert (await f.get())["visitors"]["shaped"] == 1
    assert [t.delay for t in f.timers] == [3600.0]
    await f.get()  # 다음 요청이 타이머를 다시 건다
    assert f.timers[0].cancelled and not f.timers[1].cancelled
    f.timers[1].callback()  # 요청 없이 1시간
    assert (await f.get())["visitors"]["shaped"] == 1  # 60초 안이어도 첫 채움부터
    assert opened[old.name] == 2 and len(keys) == 2 and keys[0] != keys[1]
