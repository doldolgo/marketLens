"""접속 요약 — caddy 접속 로그의 최근 24개 시(時) (스펙 035 §3.2).

동기 함수 `summarize` 하나가 파일을 흘려 읽어 센다 — api 의 `asyncio.to_thread`(기본 실행기)에서 돈다. 통째로 올리지
않고 줄 단위로 읽으며, 창 밖 줄은 JSON 을 풀기 전에 `ts` 만 보고 버린다. 메모리는 파일 크기와 무관하게 집계 키 상한
(경로·출처·utm 각 5,000)과 최근 5xx 20줄로 묶인다. 새로 저장하는 것은 없고, IP·UA 원문·쿼리는 결과에 싣지 않는다.

읽는 계약(027·032): caddy 가 도메인 요청마다 JSON 한 줄을 `access.log` 에 쓰고, 하루 또는 50MiB 에서 회전해
gzip 된 회전 파일을 같은 디렉터리에 둔다. 회전 파일 이름은 `access-<UTC 시각>-<size|time>.log.gz`
(caddy 2.11.4 로컬 확인 — 압축 중에는 같은 이름의 `.log` 가 잠깐 함께 있다).
"""

import gzip
import os
from typing import Any

from app.features.admin.access_tally import HOURS, Tally

LOG_NAME = "access.log"
ROTATED_PREFIX = "access-"
# 응답 부분의 값 키 — 순서 그대로
VALUE_KEYS = tuple(
    "startTs endTs firstTs totals hourly paths tabs referrers utmSources devices browsers status recent5xx ws".split()
)


class NoLogFile(Exception):
    """디렉터리가 없거나 비었음(env 없음 포함) — `unconfigured`·`no_file`."""


def summarize(directory: str | None, now: float) -> dict[str, Any]:
    """`now`(epoch 초)가 든 시의 시작 − 23시간부터 지금까지를 센다. 디렉터리·파일이 없으면 `NoLogFile`."""
    if not directory:
        raise NoLogFile
    end_ts = int(now)
    start_ts = end_ts - end_ts % 3600 - (HOURS - 1) * 3600
    tally = Tally(start_ts)
    for path in _files(directory, start_ts):
        try:
            handle = (
                gzip.open(path, "rt", encoding="utf-8", errors="replace")
                if path.endswith(".gz")
                else open(path, encoding="utf-8", errors="replace")
            )
        except FileNotFoundError:
            continue  # 목록을 본 뒤 회전·보관 삭제로 사라졌다 — 다음 회차가 새 이름으로 읽는다
        with handle:
            for line in handle:
                tally.line(line)
    return tally.result(end_ts)


def _files(directory: str, start_ts: int) -> list[str]:
    """`access.log` 와 수정 시각이 창 안인 회전 파일 — 오래된 것부터. 회전 파일의 수정 시각은 회전(=마지막 줄) 무렵이다."""
    try:
        entries = list(os.scandir(directory))
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise NoLogFile from exc
    current: str | None = None
    rotated: dict[str, tuple[float, str]] = {}
    for entry in entries:
        name = entry.name
        if name == LOG_NAME:
            current = entry.path
        elif name.startswith(ROTATED_PREFIX) and name.endswith((".log", ".log.gz")):
            stem = name.removesuffix(".gz")
            # 압축 중이면 같은 줄을 담은 `.log` 와 덜 쓴 `.log.gz` 가 함께 있다 — 다 쓴 `.log` 를 읽는다
            if stem in rotated and not name.endswith(".log"):
                continue
            try:
                mtime = entry.stat().st_mtime
            except FileNotFoundError:
                continue  # 목록을 본 뒤 압축·보관 삭제로 사라졌다
            rotated[stem] = (mtime, entry.path)
    if current is None and not rotated:
        raise NoLogFile
    files = [p for mtime, p in sorted(rotated.values()) if mtime >= start_ts]
    if current is not None:
        files.append(current)
    return files
