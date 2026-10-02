"""DB-IP Lite 월판 받기·갈아 끼우기·다시 받는 때 (스펙 039 §3.2·§3.3·§3.8).

외부 계약: `https://download.db-ip.com/free/dbip-{country,asn}-lite-YYYY-MM.csv.gz` — 계정·키 없음, 매월 새 판, CC BY 4.0.
가는 요청은 이 두 주소뿐이라 방문자 정보가 없다. 처리방침 v2 시행일(게이트) 뒤 접속 갱신이 ok 일 때만 부른다(`visits.py`).
받기·적재는 이벤트 루프 밖의 데몬 스레드 하나에서 한 번에 하나만 돈다 — 기본 실행기(to_thread)는 앱 종료 때 끝까지
기다리므로 따로 띄우고, 끝나면 스레드도 사라진다. 본문은 흘려 받으며 gunzip·CSV 로 한 줄씩 읽고 파일을 쓰지 않는다.
두 파일은 같은 달 한 묶음이다 — 둘 다 확인을 통과해야 갈아 끼우고, 그 전까지 조회는 옛 판으로 한다. 판이 없을 때 이번 달
묶음이 어떤 이유로든 실패하면 같은 시도에서 지난달 묶음을 받는다. 이번 달이 실패한 시도 뒤에는 6시간, 이번 달을 올린 시도
뒤에는(달이 바뀌어야 뜻이 있다) 24시간 뒤에 이번 달을 다시 받는다.
판·시도 시각·결과는 프로세스 메모리에만 둔다. 예외는 종류 이름만 남긴다(문장·traceback 없음).
"""

import csv
import gzip
import io
import logging
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import USER_AGENT
from app.features.admin.geo_table import (
    BadData,
    GeoTable,
    Ranges,
    read_asn,
    read_country,
)

logger = logging.getLogger("marketlens.admin")

COUNTRY_URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.csv.gz"
ASN_URL = "https://download.db-ip.com/free/dbip-asn-lite-{month}.csv.gz"
FILE_LIMIT_SEC = 60.0  # 파일마다 받기·풀기·적재 합
MAX_BYTES = 20_000_000  # 받은 압축 바이트 — 2026-10 판은 4.5MB·7.0MB
READ_TIMEOUT_SEC = 10.0  # 한 번 기다림 — 멈춘 연결이 60초 검사를 오래 비켜 가지 않게
# 글자 읽기 단위(기본 8KB). 풀기는 부를 때마다 GIL 을 놓았다 다시 잡는데, 같은 프로세스에 CPU 를 쓰는 스레드가 있으면 다시
# 잡을 때마다 전환 간격(5ms)을 기다린다 — 8KB 면 두 파일에 ≈7,400번이라 적재가 ≈40초로 늘었다(1MB 면 ≈60번·≈3초)
TEXT_CHUNK = 1 << 20
# 이번 달 묶음이 실패한 시도 뒤(지난달을 올렸든, 옛 판을 두든, 판이 없든) — 달 첫날 게시 전 404·바깥 장애는 대개 몇
# 시간이라 적재(serve ≈4초 GIL)와 실패를 1시간마다 되풀이하지 않게
RETRY_SEC = 21_600.0
MONTH_RETRY_SEC = 86_400.0  # 이번 달 묶음을 올린 시도 뒤 — 그 뒤 달이 바뀌었을 때
PART = "geo"


class Failed(Exception):
    """파일 하나 실패 — 응답 code(`http_<n>`·`timeout`·`bad_data`·예외 이름)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _code(exc: Exception) -> str:
    return exc.code if isinstance(exc, Failed) else type(exc).__name__


def utc_month(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m")


def previous_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:])
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


class _Body(io.RawIOBase):
    """받은 압축 조각을 gzip 에 읽히는 파일 — 조각마다 바이트 상한과 파일 시한을 본다."""

    def __init__(
        self, chunks: Iterator[bytes], check: Callable[[], None], max_bytes: int
    ) -> None:
        self._chunks = chunks
        self._check = check
        self._max = max_bytes
        self._buf = b""
        self._pos = 0
        self.total = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        while self._pos >= len(self._buf):
            self._check()
            chunk = next(self._chunks, None)
            if chunk is None:
                return 0
            self.total += len(chunk)
            if self.total > self._max:
                raise Failed("bad_data")  # 그 자리에서 끊는다 — 더 읽지 않는다
            self._buf, self._pos = chunk, 0
        n = min(len(b), len(self._buf) - self._pos)
        b[:n] = self._buf[self._pos : self._pos + n]
        self._pos += n
        return n


def _daemon(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="admin-geo", daemon=True).start()


class GeoLoader:
    """api 앱 하나에 하나. 시계·전송·스레드 띄우기는 테스트가 바꿀 수 있게 주입한다."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        warn: Callable[[str, str | None], None],
        transport: httpx.BaseTransport | None = None,
        start: Callable[[Callable[[], None]], Any] | None = None,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self._clock = clock
        self._mono = mono
        self._warn = warn
        self._transport = transport
        self._start = start or _daemon
        self._max = max_bytes
        self._lock = threading.Lock()
        self.table: GeoTable | None = None
        self._version = 0
        self._running = False
        self._attempt: float | None = None  # 마지막 시도 시작(mono)
        self._wait = RETRY_SEC  # 마지막 시도 뒤 이번 달을 다시 받기까지
        # 판이 없을 때 error 의 code — 마지막 시도에서 이번 달 묶음의 실패. 판을 올렸으면(지난달 포함) None
        self._code: str | None = None

    def ensure(self) -> None:
        """게이트 뒤 접속 갱신이 ok 일 때(루프 스레드) — 때가 됐고 도는 받기가 없으면 하나 띄운다. 기다리지 않는다."""
        month = utc_month(self._clock())
        with self._lock:
            if self._running or not self._due(month, self._mono()):
                return
            self._running, self._attempt = True, self._mono()
            fallback = self.table is None
        try:
            self._start(lambda: self._run(month, fallback))
        except Exception as exc:  # 스레드를 못 띄움 — 6시간 뒤 다시
            self._finish(None, type(exc).__name__)

    def _due(self, month: str, mono: float) -> bool:
        table = self.table
        if table is not None and table.month >= month:
            return False  # 이번 달 판
        if self._attempt is None:
            return True
        return mono - self._attempt >= self._wait

    def waiting(self) -> tuple[str, str | None]:
        """판이 없을 때의 (state, code) — 받는 중·막 올림은 `pending`, 마지막 시도가 실패면 `error`."""
        with self._lock:
            if self._running or self._code is None:
                return "pending", None
            return "error", self._code

    def _run(self, month: str, fallback: bool) -> None:
        began = time.perf_counter()
        table: GeoTable | None = None
        missed: str | None = None  # 이번 달 묶음의 실패 code
        try:
            try:
                country, asn = self._bundle(month)
            except Exception as exc:
                # 올린 판이 없으면 이번 달이 어떤 이유로 실패했든 지난달 묶음
                missed = _code(exc)
                if not fallback:
                    raise
                month = previous_month(month)
                country, asn = self._bundle(month)
            with self._lock:
                version = self._version + 1
            table = GeoTable(month, int(self._clock() * 1000), version, country, asn)
        except Exception as exc:
            # 지난달 받기도 실패했어도 code 는 이번 달의 실패다
            missed = missed or _code(exc)
        self._finish(table, missed)
        if table is not None:
            logger.info(
                "DB-IP %s 판 올림 — 나라 %d·ASN %d 구간, %.2f초",
                month,
                len(table.country),
                len(table.asn),
                time.perf_counter() - began,
            )

    def _finish(self, table: GeoTable | None, missed: str | None) -> None:
        """시도 하나의 끝 — `missed` 는 이번 달 묶음의 실패 code(올렸으면 None). 시도마다 WARNING 은 많아야 한 줄."""
        with self._lock:
            if table is not None:
                self._version = table.version
                self.table = table  # 다 올린 뒤 한 번에 — 그 전까지 조회는 옛 판
            self._code = None if table is not None else missed
            self._wait = MONTH_RETRY_SEC if missed is None else RETRY_SEC
            self._running = False
        if missed is not None:
            self._warn(PART, missed)

    def _bundle(self, month: str) -> tuple[Ranges, Ranges]:
        country = self._file(COUNTRY_URL.format(month=month), read_country)
        asn = self._file(ASN_URL.format(month=month), read_asn)
        return country, asn

    def _file(self, url: str, reader: Callable[..., Ranges]) -> Ranges:
        deadline = self._mono() + FILE_LIMIT_SEC

        def check() -> None:
            if self._mono() > deadline:
                raise Failed("timeout")

        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        try:
            with (
                httpx.Client(
                    transport=self._transport, timeout=READ_TIMEOUT_SEC
                ) as client,
                client.stream("GET", url, headers=headers) as resp,
            ):
                if resp.status_code != 200:  # 3xx 도 — 리다이렉트를 따르지 않는다
                    raise Failed(f"http_{resp.status_code}")
                body = _Body(resp.iter_raw(), check, self._max)
                with (
                    gzip.GzipFile(fileobj=body) as gz,
                    io.TextIOWrapper(gz, encoding="utf-8", newline="") as text,
                ):
                    text._CHUNK_SIZE = TEXT_CHUNK  # type: ignore[attr-defined]
                    return reader(csv.reader(text), check)
        except Failed:
            raise
        except httpx.TimeoutException as exc:
            raise Failed("timeout") from exc
        except httpx.HTTPError as exc:
            raise Failed(type(exc).__name__) from exc
        except (BadData, EOFError, OSError, ValueError, csv.Error, zlib.error) as exc:
            # 잘린·깨진 gzip(EOFError·BadGzipFile ⊂ OSError·zlib.error)·UTF-8 아님·깨진 CSV·틀린 행
            raise Failed("bad_data") from exc
