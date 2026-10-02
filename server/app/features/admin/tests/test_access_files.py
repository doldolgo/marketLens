"""접속 요약 읽기 — 깨진 줄·시각이 아닌 ts·유한하지 않은 duration·5xx 밖 상태·회전 파일(깨진 gz 포함)·파일 없음·
새지 않음·키 상한·메모리(긴 UA 포함) (스펙 035 §3.2·§3.4·§3.5·§4 → 038 §3.3·§3.6)."""

import json
import os
import tracemalloc
from pathlib import Path

import pytest

from app.features.admin.access_cache import AccessLog, NoLogFile
from app.features.admin.tests.access_fakes import (
    CHROME,
    GATE,
    GOOGLEBOT,
    IP,
    IPHONE,
    NOW,
    START_TS,
    at,
    line,
    summary,
    write,
)

BAD_TS = ("NaN", "Infinity", "-Infinity", "1e999", "1e300", "300000000000", "-5")
# 실수로 바꿀 수 없는 큰 정수(OverflowError)·풀 수 없는 긴 정수(ValueError)·NaN·무한
BAD_DURATION = ("1" + "0" * 400, "9" * 5_000, "NaN", "Infinity", "-Infinity", "1e999")


def run(tmp_path: Path, lines: list[str]) -> dict:
    write(tmp_path, "access.log", lines)
    return summary(tmp_path, NOW)


def summarize(directory: str | None, now: float, on_broken: list | None = None) -> dict:
    values, broken = AccessLog(directory, GATE).summary("24h", now)
    if on_broken is not None:
        on_broken += broken
    return values


def test_broken_lines_are_skipped_and_blank_lines_ignored(tmp_path: Path) -> None:
    good = line(at(10))
    no_request = json.dumps({"level": "info", "ts": at(10), "status": 200})
    bad_status = line(at(10)).replace('"status":200', '"status":"200"')
    body = run(
        tmp_path,
        [
            good,
            good[: len(good) // 2],  # 잘린 줄 — ts 는 창 안
            "not json at all",
            no_request,
            bad_status,
            "",
            "[]",
        ],
    )
    assert body["totals"] == {
        "requests": 1,
        "pages": 1,
        "humanPages": 1,
        "jsViews": 0,
        "probes": 0,
        "ws": 0,
        "skipped": 5,
    }


def test_rotated_files_in_the_window_are_read_and_older_ones_are_not(
    tmp_path: Path,
) -> None:
    # 창 안에 수정된 회전 파일(gz) — 읽는다. 창 앞에 수정된 회전 파일은 줄이 창 안이어도 열지 않는다
    write(
        tmp_path,
        "access-2026-10-01T00-00-00.000-time.log.gz",
        [line(at(0, 1), "/from-gz")],
        mtime=at(0, 2),
    )
    write(
        tmp_path,
        "access-2026-09-29T00-00-00.000-time.log.gz",
        [line(at(1), "/never-read")],
        mtime=START_TS - 1,
    )
    # 압축 중 — 다 쓴 .log 와 덜 쓴 .log.gz 가 함께 있으면 .log 하나만
    write(
        tmp_path,
        "access-2026-10-01T05-00-00.000-size.log",
        [line(at(2), "/compressing")],
        mtime=at(2, 1),
    )
    (tmp_path / "access-2026-10-01T05-00-00.000-size.log.gz").write_bytes(b"\x1f\x8b")
    write(tmp_path, "other.log", [line(at(3), "/not-ours")])
    write(tmp_path, "access.log", [line(at(4), "/current")])
    body = summarize(str(tmp_path), NOW)
    assert sorted(p for p, _ in body["paths"]) == [
        "/compressing",
        "/current",
        "/from-gz",
    ]
    assert body["firstTs"] == int(at(0, 1))


def test_only_rotated_files_without_current_log_still_count(tmp_path: Path) -> None:
    write(
        tmp_path,
        "access-2026-10-01T09-00-00.000-time.log.gz",
        [line(at(23))],
        mtime=at(23, 1),
    )
    assert summarize(str(tmp_path), NOW)["totals"]["pages"] == 1


@pytest.mark.parametrize("directory", [None, "", "missing"])
def test_no_directory_or_no_log_file_is_no_log_file(
    tmp_path: Path, directory: str | None
) -> None:
    target = str(tmp_path / directory) if directory == "missing" else directory
    with pytest.raises(NoLogFile):
        summarize(target, NOW)
    (tmp_path / "unrelated.txt").write_text("x")
    with pytest.raises(NoLogFile):
        summarize(str(tmp_path), NOW)


def test_result_has_no_ip_user_agent_or_query(tmp_path: Path) -> None:
    body = run(
        tmp_path,
        [
            line(
                at(11), "/app/?tab=history&utm_source=x&fbclid=SECRETCLICK", ua=IPHONE
            ),
            line(at(11), "/?gclid=AD123", ua=GOOGLEBOT, status=500),
        ],
    )
    raw = json.dumps(body, ensure_ascii=False)
    for banned in (
        IP,
        IPHONE,
        GOOGLEBOT,
        "Mobile/15E148",
        "fbclid",
        "SECRETCLICK",
        "gclid",
    ):
        assert banned not in raw, banned


def test_key_kinds_are_capped_at_30_per_hour_and_the_rest_count_as_other(
    tmp_path: Path,
) -> None:
    lines = [line(at(12), f"/?utm_source=s{i}") for i in range(5_002)]
    lines += [line(at(12), f"/scan/{i}") for i in range(5_003)]
    body = run(tmp_path, lines)
    # 경로는 `/` 다음 /scan/ 29 종류까지 — 그 뒤는 (기타). 이미 있는 키는 상한 뒤에도 제 칸에 센다(038 — 시마다 30)
    assert body["paths"][:2] == [["/", 5_002], ["(기타)", 5_003 - 29]]
    assert len(body["paths"]) == 20
    assert body["utmSources"][0] == ["(기타)", 5_002 - 30]
    assert body["totals"]["pages"] == 10_005


def test_memory_stays_flat_while_streaming_a_large_log(tmp_path: Path) -> None:
    """t4g.micro(가용 최저 ≈378MB) — 줄을 흘려 읽으므로 파일 크기와 무관하게 집계 상한만큼만 쓴다 (§3.4)."""
    pad = "a" * 300
    lines = [
        line(
            at(i % 24, i % 3600),
            f"/p{i % 7000}?q={pad}",
            ua=CHROME,
            referer=f"https://r{i % 50}.example",
        )
        for i in range(60_000)
    ]
    write(tmp_path, "access.log", lines)
    size = (tmp_path / "access.log").stat().st_size
    assert size > 40 * 1024 * 1024
    tracemalloc.start()
    try:
        body = summarize(str(tmp_path), NOW)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert body["totals"]["requests"] == 60_000
    assert peak < 8 * 1024 * 1024, peak  # 파일(40MB 넘음)을 통째로 올리지 않는다


def test_memory_stays_flat_with_long_distinct_user_agents(tmp_path: Path) -> None:
    """긴 UA·출처(헤더 상한 ≈1MB 안)가 줄마다 달라도 판정 메모가 원문을 붙들지 않는다 — 키는 UA 앞 1,024자·출처는
    512자까지만 (038 §3.4)."""
    tail = "0123456789" * 300
    lines = [
        line(
            at(i % 24, i % 3600),
            "/",
            ua=f"{CHROME} {i:06d} {tail}",
            referer=f"https://r{i % 50}.example/{i:06d}{tail[:1000]}",
        )
        for i in range(4_200)
    ]
    write(tmp_path, "access.log", lines)
    tracemalloc.start()
    try:
        body = summarize(str(tmp_path), NOW)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert body["totals"]["humanPages"] == 4_200
    assert dict(body["referrers"])["https://r0.example"] == 84
    # 메모가 원문을 담으면 4,096 × 4,000자 ≈16MB, 앞 1,024자면 ≈4MB
    assert peak < 8 * 1024 * 1024, peak


def test_a_line_whose_ts_is_not_a_time_is_skipped(tmp_path: Path) -> None:
    # NaN·무한·날짜로 바꿀 수 없는 먼 미래(손상·손으로 고친 줄) — 요약 전체가 아니라 그 줄만 skipped (038 §3.3)
    good = line(at(10), "/")
    bad = [good.replace(f'"ts":{at(10)}', f'"ts":{v}', 1) for v in BAD_TS]
    assert all(b != good for b in bad)
    write(
        tmp_path,
        "access-2026-10-01T09-00-00.000-time.log.gz",
        [good, *bad, good],
        mtime=at(11),
    )
    write(tmp_path, "access.log", [good])
    body = summarize(str(tmp_path), NOW)
    assert body["totals"]["requests"] == 3
    assert body["totals"]["skipped"] == len(BAD_TS)


def test_a_line_whose_duration_is_not_a_finite_number_is_skipped(
    tmp_path: Path,
) -> None:
    # 한 줄이 float 바꾸기에서 요약 전체를 error 로 만들지 않는다 — 그 줄만 skipped (038 §3.3)
    good = line(at(10), "/api/ws/spreads", status=101, duration=5)
    bad = [good.replace('"duration":5', f'"duration":{v}', 1) for v in BAD_DURATION]
    assert all(b != good for b in bad)
    body = run(tmp_path, [good, *bad])
    assert (body["totals"]["requests"], body["totals"]["skipped"]) == (1, 6)
    assert body["ws"]["durations"]["lt10s"] == 1 and body["ws"]["count"] == 1


def test_a_status_outside_5xx_is_not_an_error(tmp_path: Path) -> None:
    huge = 10**30
    body = run(
        tmp_path,
        [line(at(10), "/x", status=s) for s in (599, 600, 999, huge, 0, 101)],
    )
    assert body["totals"]["requests"] == 6 and body["status"]["5xx"] == 1
    assert [e["status"] for e in body["recent5xx"]] == [599]
    assert sum(h["errors"] for h in body["hourly"]) == 1


def test_a_broken_rotated_file_is_skipped_and_the_rest_counted(
    tmp_path: Path,
) -> None:
    # 잘린 gz(앞 절반) — 그 파일은 읽은 데까지, 틀린 머리 gz 는 0줄. 둘 다 skipped 1씩, 요약은 그대로
    whole = write(
        tmp_path,
        "access-2026-10-01T03-00-00.000-size.log.gz",
        [line(at(3, i), "/rotated") for i in range(2_000)],
        mtime=at(4),
    )
    data = whole.read_bytes()
    whole.write_bytes(data[: len(data) // 2])
    os.utime(whole, (at(4), at(4)))
    bad = tmp_path / "access-2026-10-01T05-00-00.000-size.log.gz"
    bad.write_bytes(b"not gzip at all\n")
    os.utime(bad, (at(5), at(5)))
    write(tmp_path, "access.log", [line(at(20), "/current")])
    broken: list[str] = []
    body = summarize(str(tmp_path), NOW, broken)
    assert sorted(broken) == ["BadGzipFile", "EOFError"]
    assert body["totals"]["skipped"] == 2
    rotated = dict(body["paths"])["/rotated"]
    assert 0 < rotated < 2_000 and dict(body["paths"])["/current"] == 1


def test_a_current_log_read_failure_still_fails(tmp_path: Path) -> None:
    (tmp_path / "access.log").mkdir()  # 지금 파일을 못 읽음 — 부분 전체의 error
    broken: list[str] = []
    with pytest.raises(IsADirectoryError):
        summarize(str(tmp_path), NOW, broken)
    assert broken == []
