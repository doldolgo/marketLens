"""접속 요약 — 창·게이트·회전 파일별 메모리 캐시·창 조립 (스펙 038 §3.1~§3.3·§3.6).

읽는 계약(027·035): caddy 가 도메인 요청마다 JSON 한 줄을 `access.log` 에 쓰고, 하루 또는 50MiB 에서 회전해 gzip 된
회전 파일 `access-<UTC 시각>-<size|time>.log.gz` 를 같은 디렉터리에 둔다(압축 중엔 같은 이름 `.log` 가 잠깐 함께 있고
그때는 `.log` 하나만 읽는다). 회전 파일은 압축이 끝나면 바뀌지 않아 확장자를 뗀 이름마다 한 번 세어 두고, `access.log` 는
60초가 지났거나 회전을 본 회차(새 회전 파일 키·바뀐 inode·줄어든 크기)에만 통째로 다시 읽는다 — 회전된 줄을 두 번 세지 않게.
창은 늘 읽는 범위 전체를 채운 캐시에서 시 버킷을 더해 만든다.
게이트(처리방침 v2 시행일 00:00 KST) 전에는 읽는 범위가 24시간 창과 같고 짝을 하나도 만들지 않는다.
DB-IP 판(039)이 바뀌면(처음 올림 포함) 캐시를 통째로 다시 만든다 — 짝 기록의 나라·망 종류는 기록할 때 찾은 값이고 IP 는
남기지 않으므로 새 판으로 다시 찾으려면 줄을 다시 읽어야 한다.
동기 함수뿐이다 — api 가 `asyncio.to_thread` 에서 부르고, 캐시 만들기는 잠금 하나로 프로세스에서 한 번에 하나다.
저장소(Redis·디스크)에는 아무것도 쓰지 않는다.
"""

import gzip
import heapq
import os
import threading
import time
import zlib
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import IO, Any, NamedTuple

from app.features.admin import geo_part
from app.features.admin.access_classes import CLASSES
from app.features.admin.access_hours import AT, COUNTS, LISTS, FileTally
from app.features.admin.access_pairs import PairKeys, before_gate, visitors
from app.features.admin.geo_table import GeoTable

WINDOWS = {"24h": 24, "7d": 168, "30d": 720}  # 창 → 시 수
DEFAULT_WINDOW = "24h"
LOG_NAME = "access.log"
ROTATED_PREFIX = "access-"
# 캐시하는 회전 파일의 압축 풀린 크기 합 — 몰리는 날(50MiB 회전이 여럿) 첫 채움을 줄 수로 묶는다(§3.3·§3.7).
# 압축 크기로 세면 잘 눌리는 몰림이 빠져나간다
BUDGET_BYTES = 100 * 1024 * 1024
CURRENT_EVERY_SEC = 60.0
TOP = 20
# 회전 파일이 깨졌을 때 나는 것 — 잘린 gz(EOFError)·틀린 머리·CRC(BadGzipFile ⊂ OSError)·압축 자료(zlib.error)·권한
BROKEN = (EOFError, OSError, zlib.error)
KST = timezone(timedelta(hours=9))
HOURLY_KEYS = ("requests", "pages", "humanPages", "jsViews", "errors", "wsErrors")
TOTAL_KEYS = ("requests", "pages", "humanPages", "jsViews", "probes", "ws", "skipped")
STATUS_KEYS = ("2xx", "3xx", "4xx", "5xx", "ws5xx")
DURATION_KEYS = ("lt10s", "lt1m", "lt10m", "lt1h", "ge1h")
# 응답 부분의 값 키 — 순서 그대로. 앞의 셋(창)은 상태와 무관하게 늘 싣는다
WINDOW_KEYS = ("window", "windows", "gateAt")
VALUE_KEYS = (
    *WINDOW_KEYS,
    *"startTs endTs firstTs totals hourly status recent5xx ws classes visitors geo".split(),
    *LISTS,
)


class NoLogFile(Exception):
    """디렉터리가 없거나 비었음(env 없음 포함) — `unconfigured`·`no_file`."""


class Entry(NamedTuple):
    size: int
    mtime: float
    raw: int  # 예산에 센 압축 풀린 크기
    tally: FileTally


class Current(NamedTuple):
    """목록에서 본 `access.log` — 회전(바뀐 inode·줄어든 크기)을 알아보는 데 쓴다."""

    path: str
    ino: int
    size: int


def gate_ts(effective: str) -> int:
    """처리방침 v2 시행일(KST 날짜) 00:00 Asia/Seoul 의 epoch 초."""
    d = date.fromisoformat(effective)
    return int(datetime(d.year, d.month, d.day, tzinfo=KST).timestamp())


def open_windows(now: float, gate: int) -> list[str]:
    return list(WINDOWS) if now >= gate else [DEFAULT_WINDOW]


def choose(window: str | None, now: float, gate: int) -> str:
    """글자가 정확히 같고 지금 고를 수 있는 창만 — 아니면 24시간(422·400 없음)."""
    return window if window in open_windows(now, gate) else DEFAULT_WINDOW


def hour_start(ts: float) -> int:
    return int(ts) - int(ts) % 3600


def bounds(name: str, now: float, gate: int) -> tuple[int, int]:
    """(startTs, endTs) — 지금이 든 시의 시작 − (N−1)시간부터 지금까지. 7일·30일은 게이트로 자른다."""
    start = hour_start(now) - (WINDOWS[name] - 1) * 3600
    if name != DEFAULT_WINDOW:
        start = max(start, gate)
    return start, int(now)


def read_start(now: float, gate: int) -> int:
    """이 앞 줄은 어떤 값에도 들지 않는다 — 게이트 전에는 24시간 창 시작, 뒤에는 30일 창(게이트로 자름)까지."""
    day = bounds(DEFAULT_WINDOW, now, gate)[0]
    return min(day, max(hour_start(now) - (WINDOWS["30d"] - 1) * 3600, gate))


def raw_size(path: str, size: int) -> int | None:
    """회전 파일의 압축 풀린 크기 — `.log` 는 파일 크기, `.gz` 는 gzip 꼬리의 ISIZE(마지막 4바이트, 2^32 나머지 —
    50MiB 에서 회전하므로 넘칠 일이 없다). 꼬리가 4바이트 미만이거나 ISIZE 가 예산보다 크면 깨진 파일이라(잘린 gz 의 꼬리는
    아무 값이다) 0 — 읽으면 읽은 데까지 세고 `skipped` 에 1 을 더한다. 파일이 사라졌으면 None."""
    if not path.endswith(".gz"):
        return size
    if size < 4:
        return 0
    try:
        with open(path, "rb") as handle:
            handle.seek(-4, os.SEEK_END)
            isize = int.from_bytes(handle.read(4), "little")
    except FileNotFoundError:
        return None
    except OSError:
        return 0  # 권한 등 — 읽기도 실패해 깨진 회전 파일로 센다
    return 0 if isize > BUDGET_BYTES else isize


def open_log(path: str) -> IO[str]:
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


class AccessLog:
    """api 프로세스 메모리의 파일 캐시·짝 기록·열쇠 — 1시간 안 부르면 부르는 쪽이 이 객체째 버린다."""

    def __init__(
        self,
        directory: str | None,
        gate: int,
        *,
        urandom: Callable[[int], bytes] = os.urandom,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dir = directory or None
        self._gate = gate
        self._keys = PairKeys(urandom)
        self._mono = mono
        self._lock = threading.Lock()
        self._files: dict[str, Entry] = {}  # 확장자를 뗀 이름 → 회전 파일 하나의 세기
        self._current: FileTally | None = None
        self._current_at: float | None = None
        # 지금 `_current` 를 읽은 회차의 목록 값
        self._current_seen: Current | None = None
        self._stems: set[str] = set()  # 직전 회차 목록의 회전 파일 키
        self._geo_version = 0  # 캐시를 만든 DB-IP 판 번호(0 = 판 없음)

    def summary(
        self, name: str, now: float, table: GeoTable | None = None
    ) -> tuple[dict[str, Any], list[str]]:
        """창 하나의 값과 이번 회차에 깨진 것으로 본 회전 파일의 예외 이름들. 디렉터리·파일이 없으면 `NoLogFile`.
        지금 고를 수 없는 창은 24시간이다(§3.1). `table` 은 이 회차가 쓰는 DB-IP 판(039, 없으면 None)."""
        with self._lock:
            broken = self._sync(now, table)
            return self._assemble(choose(name, now, self._gate), now, table), broken

    def _sync(self, now: float, table: GeoTable | None) -> list[str]:
        version = 0 if table is None else table.version
        if version != self._geo_version:
            # 판이 바뀌었다 — 옛 판으로 찾은 짝 기록을 버리고 이번 회차에 다시 읽는다(039 §3.5)
            self._files.clear()
            self._current = self._current_at = self._current_seen = None
            self._geo_version = version
        start = read_start(now, self._gate)
        try:
            current, rotated = self._listing()
        except NoLogFile:
            self._files.clear()  # 원본이 모두 사라졌다 — 요약도 같은 회차에 버린다
            self._current = self._current_at = self._current_seen = None
            self._stems = set()
            raise
        # 새 회전 파일 키가 나타났다 = 회전 — 그 줄은 이번 회차에 회전 파일로 센다
        rotated_now = not rotated.keys() <= self._stems
        self._stems = set(rotated)
        chosen: dict[str, tuple[str, int, float, int]] = {}
        spent = 0
        for stem, (path, size, mtime) in sorted(
            rotated.items(), key=lambda kv: kv[1][2], reverse=True
        ):
            if mtime < start:
                break  # 이 뒤는 더 오래됐다
            entry = self._files.get(stem)
            if entry is not None and (entry.size, entry.mtime) == (size, mtime):
                cost = entry.raw  # 바뀌지 않은 파일 — 꼬리를 다시 읽지 않는다
            else:
                cost = raw_size(path, size)
                if cost is None:
                    continue  # 목록을 본 뒤 압축·보관 삭제로 사라졌다
            if spent + cost > BUDGET_BYTES:
                break
            spent += cost
            chosen[stem] = (path, size, mtime, cost)
        # 보관 삭제·시작점 앞·100MiB 밖 — 같은 회차에 버려 요약이 원본보다 오래 남지 않게
        for stem in [s for s in self._files if s not in chosen]:
            del self._files[stem]
        broken: list[str] = []
        for stem, (path, size, mtime, cost) in chosen.items():
            entry = self._files.get(stem)
            if entry is not None and (entry.size, entry.mtime) == (size, mtime):
                # 읽기 시작점이 파일 안으로 옮겨 오면 그 파일만 다시 — 앞 줄이 짝의 첫 페이지 줄에 남지 않게
                if not entry.tally.earliest or entry.tally.earliest >= start:
                    continue
            tally = FileTally(start, self._gate, self._keys, table)
            try:
                with open_log(path) as handle:
                    tally.read(handle)
            except FileNotFoundError:
                self._files.pop(stem, None)
                continue  # 목록을 본 뒤 압축·보관 삭제로 사라졌다 — 다음 회차가 새 이름으로 읽는다
            except BROKEN as exc:
                tally.broken = 1  # 읽은 줄은 두고 파일 하나를 1로 센다 — 크기·시각이 바뀌기 전엔 다시 읽지 않는다
                broken.append(type(exc).__name__)
            self._files[stem] = Entry(size, mtime, cost, tally)
        mono = self._mono()
        seen = self._current_seen
        if current is None:
            self._current = self._current_at = self._current_seen = None
        elif (
            self._current is None
            or self._current_at is None
            or seen is None
            or mono - self._current_at >= CURRENT_EVERY_SEC
            or self._current.read_start != start
            # 회전을 본 회차 — 60초와 무관하게 통째로 바꾼다. 옛 `access.log` 의 줄을 새 회전 파일과 두 번 세지 않게(§3.8)
            or rotated_now
            or current.ino != seen.ino
            or current.size < seen.size
        ):
            tally = FileTally(start, self._gate, self._keys, table)
            try:
                with open_log(current.path) as handle:
                    tally.read(handle)  # 읽기 실패는 부분 전체의 error
            except FileNotFoundError:
                pass  # 회전 직후 — 빈 파일로 본다
            self._current, self._current_at, self._current_seen = tally, mono, current
        return broken

    def _listing(self) -> tuple[Current | None, dict[str, tuple[str, int, float]]]:
        if not self._dir:
            raise NoLogFile
        try:
            entries = list(os.scandir(self._dir))
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise NoLogFile from exc
        current: Current | None = None
        rotated: dict[str, tuple[str, int, float]] = {}
        for entry in entries:
            name = entry.name
            if name == LOG_NAME:
                try:
                    st = entry.stat()
                except FileNotFoundError:
                    st = None  # 회전 중 — 열기도 빈 파일로 본다
                # 크기를 모르면 -1 — 다음 회차의 어떤 크기도 '줄어듦' 이 아니다
                ino, size = (0, -1) if st is None else (st.st_ino, st.st_size)
                current = Current(entry.path, ino, size)
            elif name.startswith(ROTATED_PREFIX) and name.endswith((".log", ".log.gz")):
                stem = name.removesuffix(".gz").removesuffix(".log")
                # 압축 중이면 같은 줄을 담은 `.log` 와 덜 쓴 `.log.gz` 가 함께 있다 — 다 쓴 `.log` 를 읽는다
                if stem in rotated and name.endswith(".gz"):
                    continue
                try:
                    st = entry.stat()
                except FileNotFoundError:
                    continue
                rotated[stem] = (entry.path, st.st_size, st.st_mtime)
        if current is None and not rotated:
            raise NoLogFile
        return current, rotated

    def _assemble(
        self, name: str, now: float, table: GeoTable | None
    ) -> dict[str, Any]:
        gate = self._gate
        start, end = bounds(name, now, gate)
        last = hour_start(end)
        files = [(e.tally, e.mtime >= start) for e in self._files.values()]
        if self._current is not None:
            files.append((self._current, True))
        rows: dict[int, list[int]] = {}
        lists = [Counter[str]() for _ in LISTS]
        first: float | None = None
        skipped = 0
        recent = []
        for tally, overlaps in files:
            if overlaps:
                skipped += tally.no_ts + tally.broken
            for ts, hour in tally.hours.items():
                if not start <= ts <= last:
                    continue
                row = rows.get(ts)
                if row is None:
                    rows[ts] = list(hour.counts)
                else:
                    for i, v in enumerate(hour.counts):
                        row[i] += v
                if hour.first is not None and (first is None or hour.first < first):
                    first = hour.first
                for counter, counted in zip(lists, hour.lists or (), strict=False):
                    counter.update(counted)
            recent += [e for e in tally.recent if start <= e.ts < last + 3600]
        total = [0] * len(COUNTS)
        hourly = []
        for ts in range(start, last + 1, 3600):
            row = rows.get(ts) or [0] * len(COUNTS)
            for i, v in enumerate(row):
                total[i] += v
            hourly.append({"ts": ts, **{k: row[AT[k]] for k in HOURLY_KEYS}})
        totals = {k: total[AT[k]] for k in TOTAL_KEYS}
        totals["skipped"] += skipped
        if now < gate:
            people, ws_pairs, geo = before_gate(), None, geo_part.before_gate()
        else:
            since = max(start, gate)
            people, ws_pairs, counts = visitors((t.days for t, _ in files), since, end)
            # 판이 없으면 pending — 부르는 쪽이 받기 상태(받는 중·실패)로 바꾼다
            geo = (
                geo_part.empty("pending", None)
                if table is None
                else geo_part.ok(table, *counts, since)
            )
        return {
            **window_values(name, now, gate),
            "startTs": start,
            "endTs": end,
            "firstTs": None if first is None else int(first),
            "totals": totals,
            "hourly": hourly,
            "status": {k: total[AT[k]] for k in STATUS_KEYS},
            "recent5xx": [
                {"ts": int(e.ts), "path": e.path, "status": e.status}
                for e in heapq.nlargest(TOP, recent)
            ],
            "ws": {
                "count": total[AT["ws"]],
                "pairs": ws_pairs,
                "errors": total[AT["ws5xx"]],
                "durations": {k: total[AT[k]] for k in DURATION_KEYS},
            },
            "classes": {
                k: {
                    "requests": total[AT[f"{k}:requests"]],
                    "pages": total[AT[f"{k}:pages"]],
                }
                for k in CLASSES
            },
            "visitors": people,
            "geo": geo,
            **{key: _top(counter) for key, counter in zip(LISTS, lists, strict=True)},
        }


def _top(counter: Counter[str]) -> list[list[Any]]:
    """수 내림차순(같으면 이름순) 20개 — `[[이름, 수], …]`."""
    best = heapq.nsmallest(TOP, counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [[name, n] for name, n in best]


def window_values(name: str, now: float, gate: int) -> dict[str, Any]:
    """상태와 무관하게 늘 싣는 셋 — `unconfigured`·`pending`·`error` 에도 화면이 창 버튼을 그리게."""
    return {"window": name, "windows": open_windows(now, gate), "gateAt": gate * 1000}
