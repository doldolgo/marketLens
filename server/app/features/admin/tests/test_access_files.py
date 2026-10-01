"""접속 요약 읽기 — 깨진 줄·회전 파일·파일 없음·새지 않음·키 상한·메모리 (스펙 035 §3.2·§3.4·§3.5·§4)."""

import json
import tracemalloc
from pathlib import Path

import pytest

from app.features.admin.access import NoLogFile, summarize
from app.features.admin.tests.access_fakes import (
    CHROME,
    GOOGLEBOT,
    IP,
    IPHONE,
    NOW,
    START_TS,
    at,
    line,
    write,
)


def run(tmp_path: Path, lines: list[str]) -> dict:
    write(tmp_path, "access.log", lines)
    return summarize(str(tmp_path), NOW)


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
    assert body["totals"] == {"requests": 1, "pages": 1, "ws": 0, "skipped": 5}


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


def test_key_kinds_are_capped_at_5000_and_the_rest_count_as_other(
    tmp_path: Path,
) -> None:
    lines = [line(at(12), f"/?utm_source=s{i}") for i in range(5_002)]
    lines += [line(at(12), f"/scan/{i}") for i in range(5_003)]
    body = run(tmp_path, lines)
    # 경로는 `/` 다음 /scan/ 4,999 종류까지 — 그 뒤 넷은 (기타). 이미 있는 키는 상한 뒤에도 제 칸에 센다
    assert body["paths"][:2] == [["/", 5_002], ["(기타)", 4]]
    assert len(body["paths"]) == 20
    assert body["utmSources"][0] == ["(기타)", 2]
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
