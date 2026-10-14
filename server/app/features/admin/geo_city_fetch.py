"""DB-IP City Lite 월판 — 볼륨의 캐시 파일 읽기·받아 만들기·갈아 끼우기 (스펙 069 §3.3).

외부 계약: `https://download.db-ip.com/free/dbip-city-lite-YYYY-MM.csv.gz` — 계정·키 없음, 매월 새 판, CC BY 4.0, 2026-10 판
gz 85,827,400B. 가는 요청은 이 주소뿐이라 방문자 정보가 없다.
캐시 디렉터리(`GEO_CACHE_DIR` — compose 가 serve 의 api 에만 named volume `geo-cache` 로 준다)가 없으면 아무것도 하지 않는다
(위치 칸 null). 기동 때(api lifespan)는 볼륨에 파일이 있으면 그 파일만 읽어 올리고 받지 않는다. 받기는 IP 피드
(`/admin/access/addrs`·`addr`)가 판을 챙길 때 — 판이 없거나 지난달 판이면 이번 달을 받는다.
받기·만들기·파일 쓰기·읽기는 이벤트 루프 밖의 데몬 스레드 하나에서 한 번에 하나만 돈다(039 와 같은 규칙 — 종료를 막지 않는다).
받기는 300초·압축 150MB 상한, 확인은 `geo_city.read_city`. 만든 판은 볼륨에 파일 하나로 쓰고(옛 달 파일은 지운다) 다 만든 뒤
한 번에 갈아 끼운다 — 실패하면 옛 판 그대로, 판이 없으면 같은 시도에서 지난달 판을 받는다. 다시 받는 때는 039 와 같다(이번 달
실패 뒤 6시간, 이번 달을 올린 뒤 24시간). 파일 쓰기가 실패해도 만든 판은 메모리에 올린다(다음 기동이 다시 받는다).
예외는 종류 이름만 남긴다.
"""

import csv
import gzip
import io
import logging
import threading
import time
import zlib
from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import USER_AGENT
from app.features.admin import geo_city
from app.features.admin.geo_city import CityTable
from app.features.admin.geo_fetch import (
    MONTH_RETRY_SEC,
    READ_TIMEOUT_SEC,
    RETRY_SEC,
    TEXT_CHUNK,
    Failed,
    _Body,
    previous_month,
    utc_month,
)
from app.features.admin.geo_table import BadData

logger = logging.getLogger("marketlens.admin")

CITY_URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.csv.gz"
LIMIT_SEC = 300.0  # 받기·풀기·만들기 합
MAX_BYTES = 150_000_000  # 받은 압축 바이트
PART = "geo.city"


def _code(exc: Exception) -> str:
    return exc.code if isinstance(exc, Failed) else type(exc).__name__


def _daemon(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="admin-geo-city", daemon=True).start()


class CityLoader:
    """api 앱 하나에 하나. 시계·전송·스레드 띄우기는 테스트가 바꿀 수 있게 주입한다."""

    def __init__(
        self,
        directory: str | None,
        *,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        warn: Callable[[str, str | None], None],
        transport: httpx.BaseTransport | None = None,
        start: Callable[[Callable[[], None]], Any] | None = None,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self._dir = directory or None
        self._clock = clock
        self._mono = mono
        self._warn = warn
        self._transport = transport
        self._start = start or _daemon
        self._max = max_bytes
        self._lock = threading.Lock()
        self.table: CityTable | None = None
        self._running = False
        self._read_file = False  # 볼륨 파일을 한 번 읽어 봤다
        self._attempt: float | None = None  # 마지막 받기 시작(mono)
        self._wait = RETRY_SEC

    def boot(self) -> None:
        """기동 때 — 볼륨에 파일이 있으면 그 파일만 읽어 올린다(받지 않는다). 기다리지 않는다."""
        if self._dir is None or geo_city.latest(self._dir) is None:
            return
        with self._lock:
            if self._running or self._read_file:
                return
            self._running = True
        self._spawn(lambda: self._run(None))

    def ensure(self) -> None:
        """IP 피드가 판을 챙길 때(루프 스레드) — 때가 됐고 도는 일이 없으면 하나 띄운다. 기다리지 않는다."""
        if self._dir is None:
            return
        month = utc_month(self._clock())
        with self._lock:
            if self._running or not self._due(month, self._mono()):
                return
            self._running = True
        self._spawn(lambda: self._run(month))

    def _spawn(self, job: Callable[[], None]) -> None:
        try:
            self._start(job)
        except Exception as exc:  # 스레드를 못 띄움 — 6시간 뒤 다시
            with self._lock:
                self._running = False
                self._attempt, self._wait = self._mono(), RETRY_SEC
            self._warn(PART, type(exc).__name__)

    def _due(self, month: str, mono: float) -> bool:
        if not self._read_file:
            return True  # 볼륨 파일부터
        table = self.table
        if table is not None and table.month >= month:
            return False
        return self._attempt is None or mono - self._attempt >= self._wait

    def _run(self, month: str | None) -> None:
        """`month` None 이면 볼륨 파일만, 아니면 파일을 아직 안 읽었으면 읽고 그 달보다 낡았으면 받는다."""
        try:
            if not self._read_file:
                self._load_file()
            table = self.table
            if month is not None and (table is None or table.month < month):
                with self._lock:
                    self._attempt = self._mono()
                self._fetch(month, fallback=table is None)
        finally:
            with self._lock:
                self._running = False

    def _load_file(self) -> None:
        self._read_file = True
        path = geo_city.latest(self._dir or "")
        if path is None:
            return
        began = time.perf_counter()
        try:
            table = geo_city.read(path)
        except (BadData, OSError, ValueError) as exc:
            # 깨진·다른 상자의 파일 — 판 없음으로 두고 받기가 새 파일로 바꾼다
            self._warn(PART, f"bad_file_{type(exc).__name__}")
            return
        with self._lock:
            self.table = table
        logger.info(
            "DB-IP City %s 판 올림(파일) — 구간 %d·위치 %d, %.2f초",
            table.month,
            len(table),
            table.places,
            time.perf_counter() - began,
        )

    def _fetch(self, month: str, *, fallback: bool) -> None:
        began = time.perf_counter()
        missed: str | None = None
        table: CityTable | None = None
        try:
            try:
                table = self._file(month)
            except Exception as exc:
                missed = _code(exc)
                if not fallback:
                    raise
                table = self._file(previous_month(month))
        except Exception as exc:
            missed = missed or _code(exc)
        with self._lock:
            self._wait = MONTH_RETRY_SEC if missed is None else RETRY_SEC
        if table is not None:
            try:
                geo_city.write(table, self._dir or "")
            except OSError as exc:
                self._warn(
                    PART, type(exc).__name__
                )  # 메모리에는 올린다 — 다음 기동이 다시 받는다
            with self._lock:
                self.table = table
            logger.info(
                "DB-IP City %s 판 올림(받기) — 구간 %d·위치 %d, %.2f초",
                table.month,
                len(table),
                table.places,
                time.perf_counter() - began,
            )
        if missed is not None:
            self._warn(PART, missed)

    def _file(self, month: str) -> CityTable:
        deadline = self._mono() + LIMIT_SEC

        def check() -> None:
            if self._mono() > deadline:
                raise Failed("timeout")

        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        try:
            with (
                httpx.Client(
                    transport=self._transport, timeout=READ_TIMEOUT_SEC
                ) as client,
                client.stream(
                    "GET", CITY_URL.format(month=month), headers=headers
                ) as resp,
            ):
                if resp.status_code != 200:  # 3xx 도 — 리다이렉트를 따르지 않는다
                    raise Failed(f"http_{resp.status_code}")
                body = _Body(resp.iter_raw(), check, self._max)
                with (
                    gzip.GzipFile(fileobj=body) as gz,
                    io.TextIOWrapper(gz, encoding="utf-8", newline="") as text,
                ):
                    text._CHUNK_SIZE = TEXT_CHUNK  # type: ignore[attr-defined]
                    return geo_city.read_city(csv.reader(text), month, check)
        except Failed:
            raise
        except httpx.TimeoutException as exc:
            raise Failed("timeout") from exc
        except httpx.HTTPError as exc:
            raise Failed(type(exc).__name__) from exc
        except (BadData, EOFError, OSError, ValueError, csv.Error, zlib.error) as exc:
            raise Failed("bad_data") from exc
