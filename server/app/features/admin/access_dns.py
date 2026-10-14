"""IP 상세의 역방향 DNS·검색 로봇 주소 확인 (스펙 069 §3.3·§3.4·§3.8).

상세(`/admin/access/addr`)에서만, IP 하나에 한 번 부른다 — 목록에서는 하지 않는다. 묻기마다 1초 시간 초과: 막히는 시스템
해석기를 데몬 스레드에서 부르고 1초만 기다린다(늦은 스레드는 혼자 끝난다 — 종료를 막지 않는다, 동시에 넷까지). 결과(이름 또는
없음)와 정방향 확인을 24시간 메모리에 둔다(5,000개 — 넘으면 가장 오래된 것부터). 시간 초과·해석기 오류는 담지 않고 null 이다.
검색 로봇 확인: PTR 이름이 그 로봇의 도메인으로 끝나고 그 이름의 정방향 주소에 같은 IP 가 있으면 true, PTR 이 없거나 이름·주소가
안 맞으면 false, 묻기가 실패하면 null.
"""

import socket
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import Any, Protocol

TIMEOUT_SEC = 1.0
CACHE_SEC = 86_400.0
CACHE_MAX = 5_000
THREADS_MAX = 4
# 038 종류 search 의 이름(`access_addrs.CRAWLERS`) → PTR 이름이 끝나야 하는 도메인
DOMAINS = {
    "google": ("googlebot.com", "google.com"),
    "bing": ("search.msn.com",),
    "naver": ("naver.com",),
    "daum": ("daum.net",),
}


class Failed(Exception):
    """시간 초과·해석기 오류 — 결과를 모른다(null)."""


class Resolver(Protocol):
    def ptr(self, ip: str) -> str | None:
        """PTR 이름 — 레코드가 없으면 None, 묻기 실패는 예외."""

    def forward(self, name: str) -> set[str]:
        """정방향 주소들 — 묻기 실패는 예외."""


class SystemResolver:
    def ptr(self, ip: str) -> str | None:
        try:
            return socket.gethostbyaddr(ip)[0].rstrip(".").lower() or None
        except socket.herror:
            return None  # 레코드 없음

    def forward(self, name: str) -> set[str]:
        try:
            return {info[4][0] for info in socket.getaddrinfo(name, None)}
        except socket.gaierror as exc:
            if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -5)):
                return set()
            raise


def _daemon(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="admin-dns", daemon=True).start()


class Dns:
    """api 앱 하나에 하나. 해석기·시계·스레드 띄우기는 테스트가 바꾼다."""

    def __init__(
        self,
        resolver: Resolver | None = None,
        *,
        mono: Callable[[], float] = time.monotonic,
        timeout: float = TIMEOUT_SEC,
        start: Callable[[Callable[[], None]], Any] = _daemon,
    ) -> None:
        self._resolver = resolver or SystemResolver()
        self._mono = mono
        self._timeout = timeout
        self._start = start
        self._lock = threading.Lock()
        self._busy = 0
        # IP → (만료(mono), PTR 이름 또는 None, 정방향에 같은 IP 가 있나 — 이름이 로봇 도메인일 때만, 아니면 None)
        self._cache: OrderedDict[str, tuple[float, str | None, bool | None]] = (
            OrderedDict()
        )

    def _ask(self, call: Callable[[], Any]) -> Any:
        """막히는 묻기를 데몬 스레드에서 — 1초 안에 끝나지 않으면 `Failed`."""
        with self._lock:
            if self._busy >= THREADS_MAX:
                raise Failed
            self._busy += 1
        done = threading.Event()
        box: dict[str, Any] = {}

        def job() -> None:
            try:
                box["value"] = call()
            except Exception as exc:  # 해석기 오류 — 종류만
                box["error"] = exc
            finally:
                with self._lock:
                    self._busy -= 1
                done.set()

        try:
            self._start(job)
        except Exception as exc:
            with self._lock:
                self._busy -= 1
            raise Failed from exc
        if not done.wait(self._timeout) or "error" in box:
            raise Failed
        return box["value"]

    def lookup(self, ip: str) -> tuple[str | None, bool | None] | None:
        """(PTR 이름 또는 None, 로봇 도메인 이름의 정방향 확인) — 묻기 실패면 None(담지 않는다)."""
        now = self._mono()
        with self._lock:
            hit = self._cache.get(ip)
            if hit is not None and hit[0] > now:
                return hit[1], hit[2]
        try:
            name = self._ask(lambda: self._resolver.ptr(ip))
            forward = None
            if name is not None and _robot_domain(name):
                forward = ip in self._ask(lambda: self._resolver.forward(name))
        except Failed:
            return None
        with self._lock:
            self._cache[ip] = (now + CACHE_SEC, name, forward)
            self._cache.move_to_end(ip)
            while len(self._cache) > CACHE_MAX:
                self._cache.popitem(last=False)
        return name, forward

    def check(self, ip: str, claims: Iterable[str]) -> tuple[str | None, bool | None]:
        """(PTR 이름, verifiedCrawler) — 주장한 로봇이 없으면 확인은 None."""
        found = self.lookup(ip)
        claimed = [c for c in claims if c in DOMAINS]
        if found is None:
            return None, None
        name, forward = found
        if not claimed:
            return name, None
        if name is None:
            return None, False
        if not any(_ends(name, DOMAINS[c]) for c in claimed):
            return name, False
        return name, forward


def _ends(name: str, domains: Iterable[str]) -> bool:
    return any(name == d or name.endswith("." + d) for d in domains)


def _robot_domain(name: str) -> bool:
    return any(_ends(name, domains) for domains in DOMAINS.values())
