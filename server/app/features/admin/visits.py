"""api 관리자 피드 — `/admin/access`·`/admin/clarity`·`/admin/clarity/history` 의 부분 상태와 갱신 (스펙 035 §3.1~§3.2·
038 §3.1~§3.3·040 §3.2·§3.5·068 §3.1·§3.2), 067 의 IP 대역 목록 `/admin/access/ips`·상세 `/admin/access/ip`.

공통 규칙은 034 와 같은 문장이다(`parts.py`). 접속 요약은 창(24h·7d·30d)마다 따로 60초 칸이고, 파일 캐시는 창 셋이
나눠 쓰며 파일 읽기는 `asyncio.to_thread`(기본 실행기 — api 에는 수집 쓰기가 없다). 마지막 접속 요청에서 1시간이 지나면
파일 캐시·짝 기록·열쇠·창 칸을 통째로 버린다(038 §3.3). Clarity 는 호출이 넷이다 — 기본 요약(6시간)·페이지×기기
묶음(12시간, 하위 부분 `pages`)·기기와 유입 차원(24시간씩, 하위 부분 `dims` 하나에 칸 둘 — 068). 요청마다 Redis 기록 넷을
읽어 때를 정하고(`clarity_schedule.py`), 한 갱신 안에서 기본 → 묶음 → 기기 → 유입 순서로 하나씩 부른다 — Clarity 호출이
동시에 둘 나가지 않는다. 기본이 401·403·429 면 나머지는 미룬다. 기본이 성공할 때마다(그날 차원이 성공할 때도) 그 KST 날의
칸 `admin:clarity:day:<YYYYMMDD>` 를 곧바로 덮어쓰고(`clarity_days.py` — 못 쓰면 다음 갱신에 다시), `/admin/clarity/history`
는 창(7·30·90일)마다 60초 칸으로 그 칸들을 읽는다.
접속 갱신이 게이트 뒤 ok 이면 DB-IP 판(039)이 필요한지 보고 받기를 띄운다 — 갱신은 받기를 기다리지 않고, 판이 없는
동안 하위 부분 `geo` 는 받는 중 `pending`·실패 `error` 다. 판은 캐시 잠금 안에서 하나 집어 그 회차 끝까지 쓴다 — 창
둘의 갱신이 갈아 끼우는 순간에 겹쳐도 옛 판으로 캐시를 다시 만들지 않게.
067 — 대역 목록은 (창, 거르기·정렬·쪽)마다, 상세는 (대역, 창)마다 60초 칸이고 칸은 32개까지(가장 오래 안 부른 것부터
버린다). 목록은 요약과 같은 파일 캐시를 쓰고, 상세는 창 안 파일을 그 대역 줄만 골라 다시 읽는다(한 번에 하나). 둘 다 접속
요청이라 1시간 비움 타이머를 다시 걸고, 비울 때 함께 버린다. 응답은 `{state, code, window, …}` 이고 ok 가 아니면 값 키가 null.
읽기·부르기는 한 번에 하나이고 3초까지 기다린 뒤 늦으면 직전 결과(없으면 pending)를 답한다. 요청이 없으면 아무것도
읽거나 부르지 않는다. 처리기 안 예외도 500 이 아니라 그 부분 `error` 와 WARNING 1줄(부분마다 10분에 1줄, 예외 이름만 —
문장에 가린 IP·UA 가 실릴 수 있다)이다 — 라우터가 응답을 JSON 으로 쓰다 실패하지 않게, 쓸 수 없는 값(NaN·짝 없는
서로게이트)이 든 답도 여기서 `error` 로 바꾼다(`pages` 는 따로 — 바깥을 바꾸지 않는다). JSON 풀기·쓰기는 파일 읽기와 같이
`asyncio.to_thread` 에서 한다.
"""

import asyncio
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import httpx

from app.core import config
from app.features.admin import access_addr, access_ip
from app.features.admin import clarity_recordings as recordings
from app.features.admin.access_addrs_page import AddrsQuery
from app.features.admin.access_bands import IpsQuery
from app.features.admin.access_cache import (
    VALUE_KEYS,
    WINDOW_KEYS,
    WINDOWS,
    AccessLog,
    NoLogFile,
    choose,
    gate_ts,
    window_values,
)
from app.features.admin.access_dns import Dns
from app.features.admin.clarity import (
    ClarityStore,
    Record,
    check_json,
    fetch,
    load_record,
)
from app.features.admin.clarity_days import (
    HISTORY_DAYS,
    HISTORY_DEFAULT,
    HISTORY_KEYS,
    HISTORY_REFRESH_SEC,
    day_of,
    day_text,
    expire_at,
    history_rows,
    window_days,
)
from app.features.admin.clarity_dims import DIMS_CALLS, DIMS_KEYS, parse_dims
from app.features.admin.clarity_pages import parse_pages
from app.features.admin.clarity_schedule import ClarityKind, DirectBudget
from app.features.admin.clarity_values import parse_base
from app.features.admin.geo_city_fetch import CityLoader
from app.features.admin.geo_fetch import GeoLoader
from app.features.admin.parts import (
    UNCONFIGURED,
    WAIT_SEC,
    Result,
    Slot,
    Warner,
    render,
)

ACCESS_REFRESH_SEC = 60
# 마지막 접속 요청에서 이만큼 지나면 캐시·짝 기록·열쇠를 버린다(038 §3.3)
ACCESS_IDLE_SEC = 3600.0
# 시도 사이 — 어떤 24시간에도 기본 4 + 묶음 2 + 기기 1 + 유입 1 = 8회라 하루 10회 한도에서 사람 몫 2회가 남는다
# (040 §3.2·068 §3.1)
CLARITY_REFRESH_SEC = 21_600
PAGES_REFRESH_SEC = 43_200
DIMS_REFRESH_SEC = 86_400
CLARITY_KEYS = ("nextAt", "numOfDays", "traffic", "summary", "countries", "metrics")
PAGES_KEYS = ("nextAt", "numOfDays", "rowsIn", "rowLimitHit", "groups")
BASE_PARAMS = {"numOfDays": "1"}
# 동의한 방문자만이라 표본이 작아 72시간 — 차원은 둘(주소·기기)
PAGES_PARAMS = {"numOfDays": "3", "dimension1": "URL", "dimension2": "Device"}
# 067 — 대역 목록(창·거르기·정렬·쪽마다)·상세(대역·창마다) 60초 칸. 칸이 이만큼 차면 가장 오래 안 부른 것부터 버린다
BAND_SLOTS = 32
IPS_KEYS = ("total", "bands")
ADDRS_KEYS = ("total", "since", "rows")  # 069 §3.5
# 069 §3.6 — 목록 행의 값(ip 는 머리에) + 위치·확인 + 067 상세 값 + partial
ADDR_KEYS = (
    "masked",
    "country",
    "region",
    "city",
    "asn",
    "org",
    "net",
    "requests",
    "pages",
    "js",
    "ws",
    "kinds",
    "status",
    "first",
    "last",
    "spark",
    "topPaths",
    "topAgents",
    "score",
    "verdict",
    "reasons",
    "geo",
    "verifiedCrawler",
    "hourly",
    "agents",
    "paths",
    "pairs",
    "recent",
    "partial",
)
IP_KEYS = (
    "partial",
    "country",
    "net",
    "hourly",
    "kinds",
    "status",
    "agents",
    "paths",
    "pairs",
    "recent",
)


class VisitFeeds:
    """api 앱 하나에 하나(`app.state.admin_visits`). 시계·기다림·Clarity 전송은 테스트가 바꿀 수 있게 주입한다."""

    def __init__(
        self,
        *,
        access_dir: str | None,
        clarity_token: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        wait_sec: float = WAIT_SEC,
        privacy_effective: str | None = None,
        urandom: Callable[[int], bytes] = os.urandom,
        schedule: Callable[[float, Callable[[], None]], Any] | None = None,
        geo_transport: httpx.BaseTransport | None = None,
        geo_start: Callable[[Callable[[], None]], Any] | None = None,
        geo_cache_dir: str | None = None,
        city_transport: httpx.BaseTransport | None = None,
        city_start: Callable[[Callable[[], None]], Any] | None = None,
        dns: Dns | None = None,
    ) -> None:
        self._dir = access_dir or None
        self._token = clarity_token or None
        self._transport = transport
        self._clock = clock
        self._mono = mono
        self._wait = wait_sec
        # 시행일 한 곳(037) — 테스트가 바꿔 끼운다
        self._gate = gate_ts(privacy_effective or config.PRIVACY_V2_EFFECTIVE)
        self._urandom = urandom
        self._schedule = schedule or _call_later
        self._idle: Any = None
        self._log, self._access = self._fresh_access()
        self._ips: OrderedDict[tuple[str, IpsQuery], Slot] = OrderedDict()
        self._ip: OrderedDict[tuple[str, str], Slot] = OrderedDict()
        # 069 — IP 하나 단위 목록·상세 칸(대역 칸과 같은 규칙)
        self._addrs: OrderedDict[tuple[str, AddrsQuery], Slot] = OrderedDict()
        self._addr: OrderedDict[tuple[str, str], Slot] = OrderedDict()
        self._scan_lock = threading.Lock()  # 상세 다시 읽기는 프로세스에서 한 번에 하나
        # 주기 0 — Clarity 의 캐시는 Redis 기록이고, 이 칸은 한 번에 하나·3초 기다림·직전 결과만 맡는다
        self._clarity = Slot(0, mono, wait_sec)
        budget = (
            DirectBudget()
        )  # 바로 부르기 — 종류 넷이 같이 쓴다(어떤 24시간에도 두 번)
        self._base = ClarityKind(
            name="clarity",
            refresh_sec=CLARITY_REFRESH_SEC,
            params=BASE_PARAMS,
            parse=parse_base,
            load=lambda bus: bus.clarity_load(),
            save=lambda bus, data: bus.clarity_save(data),
            budget=budget,
        )
        self._pages = ClarityKind(
            name="clarity.pages",
            refresh_sec=PAGES_REFRESH_SEC,
            params=PAGES_PARAMS,
            parse=parse_pages,
            load=lambda bus: bus.clarity_pages_load(),
            save=lambda bus, data: bus.clarity_pages_save(data),
            budget=budget,
        )
        # 068 — 차원 호출 둘(기기·유입). 기록은 해시 `admin:clarity:dims` 의 칸 하나씩, 목록 이름은 응답 `dims` 의 키
        self._dims = tuple(
            (
                ClarityKind(
                    name=f"clarity.dims.{field}",
                    refresh_sec=DIMS_REFRESH_SEC,
                    params=params,
                    parse=parse_dims(dims),
                    load=lambda bus, f=field: bus.clarity_dims_load(f),
                    save=lambda bus, data, f=field: bus.clarity_dims_save(f, data),
                    budget=budget,
                ),
                tuple(name for name, _ in dims),
            )
            for field, params, dims in DIMS_CALLS
        )
        self._kinds = (self._base, self._pages, *(kind for kind, _ in self._dims))
        # 못 쓴 날별 기록(다음 갱신이 다시 쓴다)·이번 갱신에 Redis 에서 읽은 기록
        self._day_pending = False
        self._stored: dict[ClarityKind, Record] = {}
        self._history = {
            n: Slot(HISTORY_REFRESH_SEC, mono, wait_sec) for n in HISTORY_DAYS.values()
        }
        # 077 — Clarity 녹화 목록, 창(1·3·7·30일)마다 30분 칸(요청이 있을 때만 부른다)
        self._recordings = {
            n: Slot(recordings.REFRESH_SEC, mono, wait_sec) for n in recordings.DAYS
        }
        self._warn = Warner(mono)
        # 039 — DB-IP 판. 1시간 비움(§3.3)과 무관하게 프로세스에 남는다(방문자 정보가 아닌 공개 자료)
        self.geo = GeoLoader(
            clock=clock,
            mono=mono,
            warn=self._warn,
            transport=geo_transport,
            start=geo_start,
        )
        # 069 — IP 상세의 역방향 DNS·검색 로봇 확인(24시간 메모리 캐시 — 1시간 비움과 무관, 방문 기록이 아닌 공개 이름)
        self.dns = dns or Dns()
        # 069 — DB-IP City 판(도시). 볼륨 파일·IP 피드가 챙길 때만 받는다. 039 판처럼 프로세스에 남는다
        self.city = CityLoader(
            geo_cache_dir,
            clock=clock,
            mono=mono,
            warn=self._warn,
            transport=city_transport,
            start=city_start,
        )

    # --- /admin/access ---

    def _fresh_access(self) -> tuple[AccessLog, dict[str, Slot]]:
        log = AccessLog(self._dir, self._gate, urandom=self._urandom, mono=self._mono)
        return log, {
            name: Slot(ACCESS_REFRESH_SEC, self._mono, self._wait) for name in WINDOWS
        }

    def _drop_access(self) -> None:
        """1시간 안 부르지 않음 — 요약이 창과 원본보다 오래 남지 않게 통째로 버린다(067 의 대역 칸·상세도). 다음 요청은
        첫 채움부터."""
        self._idle = None
        self._log, self._access = self._fresh_access()
        self._ips, self._ip = OrderedDict(), OrderedDict()
        self._addrs, self._addr = OrderedDict(), OrderedDict()

    def _touch(self) -> None:
        """접속 요청(요약·대역 목록·상세)마다 1시간 비움 타이머를 다시 건다."""
        if self._idle is not None:
            self._idle.cancel()
        self._idle = self._schedule(ACCESS_IDLE_SEC, self._drop_access)

    async def access(self, window: str | None = None) -> dict[str, Any]:
        now = self._clock()
        name = choose(window, now, self._gate)
        self._touch()
        log = self._log
        try:
            result = await self._access[name].get(lambda: self._load_access(log, name))
        except Exception as exc:
            result = self._failed("access", exc)
        body = render(result, ACCESS_REFRESH_SEC, VALUE_KEYS)
        if result.state != "ok" or not result.values:
            body.update(window_values(name, now, self._gate))
        return self._writable("access", body, VALUE_KEYS, keep=WINDOW_KEYS)

    async def _load_access(self, log: AccessLog, name: str) -> Result:
        now = self._clock()
        try:
            values, broken = await asyncio.to_thread(
                log.summary, name, now, lambda: self.geo.table
            )
        except NoLogFile:
            return Result("unconfigured", "no_file")
        except Exception as exc:
            return self._failed("access", exc)
        if broken:  # 깨진 회전 파일 — 그 파일만 건너뛰고 WARNING 1줄
            self._warn("access", broken[0])
        if now >= self._gate:
            self.geo.ensure()  # 판이 없거나 낡았으면 받기를 띄운다 — 기다리지 않는다
            geo = values["geo"]
            # 이 회차는 판 없이 셌다 — 받는 중·실패를 싣는다
            if geo["state"] == "pending":
                geo["state"], geo["code"] = self.geo.waiting()
        return Result("ok", None, int(now * 1000), values)

    # --- /admin/access/ips·/admin/access/ip (067) ---

    def _band_slot(self, slots: OrderedDict[Any, Slot], key: Any) -> Slot:
        slot = slots.get(key)
        if slot is None:
            if len(slots) >= BAND_SLOTS:
                slots.popitem(last=False)
            slot = slots[key] = Slot(ACCESS_REFRESH_SEC, self._mono, self._wait)
        else:
            slots.move_to_end(key)
        return slot

    async def ips(self, window: str | None, query: IpsQuery) -> dict[str, Any]:
        """대역 목록 — `{state, code, window, total, bands}`. 파일 캐시는 요약(038)과 나눠 쓴다."""
        now = self._clock()
        name = choose(window, now, self._gate)
        self._touch()
        log = self._log
        try:
            slot = self._band_slot(self._ips, (name, query))
            result = await slot.get(
                lambda: self._load_band("access.ips", log.ips, name, query)
            )
        except Exception as exc:
            result = self._failed("access.ips", exc)
        head = {"state": result.state, "code": result.code, "window": name}
        return self._checked("access.ips", head, result, IPS_KEYS)

    async def ip(self, band: str, window: str | None) -> dict[str, Any]:
        """대역 하나의 상세 — 창 안 파일을 그 대역 줄만 골라 다시 훑는다(요청이 있을 때만·한 번에 하나)."""
        now = self._clock()
        name = choose(window, now, self._gate)
        self._touch()
        try:
            slot = self._band_slot(self._ip, (band, name))
            result = await slot.get(
                lambda: self._load_band("access.ip", self._scan, name, band)
            )
        except Exception as exc:
            result = self._failed("access.ip", exc)
        head = {
            "state": result.state,
            "code": result.code,
            "window": name,
            "band": band,
        }
        return self._checked("access.ip", head, result, IP_KEYS)

    # --- /admin/access/addrs (069) ---

    async def addrs(self, window: str | None, query: AddrsQuery) -> dict[str, Any]:
        """IP 하나 단위 목록 — `{state, code, window, total, since, rows}`. 파일 캐시는 요약(038)·대역(067)과 나눠 쓴다."""
        now = self._clock()
        name = choose(window, now, self._gate)
        self._touch()
        log = self._log
        try:
            slot = self._band_slot(self._addrs, (name, query))
            result = await slot.get(lambda: self._load_addrs(log, name, query))
        except Exception as exc:
            result = self._failed("access.addrs", exc)
        head = {"state": result.state, "code": result.code, "window": name}
        return self._checked("access.addrs", head, result, ADDRS_KEYS)

    async def _load_addrs(self, log: AccessLog, name: str, query: AddrsQuery) -> Result:
        now = self._clock()
        try:
            values, broken = await asyncio.to_thread(
                log.addrs,
                name,
                now,
                query,
                lambda: self.geo.table,
                lambda: self.city.table,
            )
        except NoLogFile:
            return Result("unconfigured", "no_file")
        except Exception as exc:
            return self._failed("access.addrs", exc)
        if broken:
            self._warn("access.addrs", broken[0])
        self._ensure_places(now)
        return Result("ok", None, int(now * 1000), values)

    async def addr(self, ip: str, window: str | None) -> dict[str, Any]:
        """IP 하나(또는 가린 대역)의 상세 — 창 안 파일을 그 행의 줄만 골라 다시 훑는다(요청이 있을 때만·한 번에 하나)."""
        now = self._clock()
        name = choose(window, now, self._gate)
        self._touch()
        log = self._log
        try:
            slot = self._band_slot(self._addr, (ip, name))
            result = await slot.get(lambda: self._load_addr(log, ip, name))
        except Exception as exc:
            result = self._failed("access.addr", exc)
        head = {"state": result.state, "code": result.code, "window": name, "ip": ip}
        return self._checked("access.addr", head, result, ADDR_KEYS)

    async def _load_addr(self, log: AccessLog, ip: str, name: str) -> Result:
        now = self._clock()

        def read() -> tuple[dict[str, Any], list[str]]:
            since = (
                log.since()
            )  # 목록 캐시가 아는 전체 저장 시작 시각(비었으면 None — 훑기가 찾는다)
            with self._scan_lock:
                return access_addr.scan(
                    self._dir,
                    ip,
                    name,
                    now,
                    self._gate,
                    since,
                    self.geo.table,
                    self.city.table,
                    self.dns,
                )

        try:
            values, broken = await asyncio.to_thread(read)
        except NoLogFile:
            return Result("unconfigured", "no_file")
        except Exception as exc:
            return self._failed("access.addr", exc)
        if broken:
            self._warn("access.addr", broken[0])
        self._ensure_places(now)
        return Result("ok", None, int(now * 1000), values)

    def _ensure_places(self, now: float) -> None:
        """나라·망 종류(039)·City(069) 판 — 없거나 낡았으면 받기를 띄운다, 기다리지 않는다."""
        if now >= self._gate:
            self.geo.ensure()
        self.city.ensure()

    def _scan(
        self, name: str, now: float, band: str, geo: Callable[[], Any]
    ) -> tuple[dict[str, Any], list[str]]:
        with self._scan_lock:
            return access_ip.scan(self._dir, band, name, now, self._gate, geo())

    async def _load_band(
        self, part: str, read: Callable[..., Any], name: str, arg: Any
    ) -> Result:
        """`read(name, now, arg, geo)` 를 스레드에서 — 깨진 회전 파일은 WARNING 1줄, 게이트 뒤 ok 면 DB-IP 판을 챙긴다."""
        now = self._clock()
        try:
            values, broken = await asyncio.to_thread(
                read, name, now, arg, lambda: self.geo.table
            )
        except NoLogFile:
            return Result("unconfigured", "no_file")
        except Exception as exc:
            return self._failed(part, exc)
        if broken:
            self._warn(part, broken[0])
        if now >= self._gate:
            self.geo.ensure()  # 나라·망 종류(039) — 판이 없으면 받기를 띄운다, 기다리지 않는다
        return Result("ok", None, int(now * 1000), values)

    def _checked(
        self,
        name: str,
        head: dict[str, Any],
        result: Result,
        keys: tuple[str, ...],
    ) -> dict[str, Any]:
        """ok 가 아니면 값 키는 null. JSON 으로 쓸 수 없는 값이 든 답은 그 부분 `error`·예외 이름(값 키 null)."""
        values = result.values if result.state == "ok" and result.values else {}
        body = {**head, **{k: values.get(k) for k in keys}}
        try:
            check_json(body)
        except (TypeError, ValueError) as exc:
            code = type(exc).__name__
            self._warn(name, code)
            return {**head, "state": "error", "code": code, **dict.fromkeys(keys)}
        return body

    # --- /admin/clarity ---

    async def clarity(self, *, bus: ClarityStore | None) -> dict[str, Any]:
        if self._token is None:
            # 토큰 없음 — Redis 도 Clarity 도 부르지 않는다
            outer = inner = dims = UNCONFIGURED
        else:
            try:
                result = await self._clarity.get(lambda: self._refresh_clarity(bus))
            except Exception as exc:
                result = self._failed("clarity", exc)
            parts = result.values or {}
            # pending·처리기 예외면 셋이 같은 상태
            outer = parts.get("outer", result)
            inner = parts.get("pages", result)
            dims = parts.get("dims", result)
        # 값은 state 와 무관하게 마지막 성공 값이다(035 §3.3 — §3.1 의 예외). 경과는 fetchedAt 이 말한다
        pages = self._writable(
            "clarity.pages", _body(inner, PAGES_REFRESH_SEC, PAGES_KEYS), PAGES_KEYS
        )
        dims_part = self._writable(
            "clarity.dims", _body(dims, DIMS_REFRESH_SEC, DIMS_KEYS), DIMS_KEYS
        )
        body = self._writable(
            "clarity", _body(outer, CLARITY_REFRESH_SEC, CLARITY_KEYS), CLARITY_KEYS
        )
        return {**body, "pages": pages, "dims": dims_part}

    async def _refresh_clarity(self, bus: ClarityStore | None) -> Result:
        now_ms = int(self._clock() * 1000)
        texts: list[str | None] | None = None
        if bus is not None:  # None = lifespan 전 — Redis 자리가 없다
            try:
                texts = [await kind.load(bus) for kind in self._kinds]
            except Exception:
                texts = None
        if bus is None or texts is None:
            # 시도 시각을 모르면 부르지 않는다 — 한도를 지킨다. 원인 하나라 줄도 하나
            self._warn("clarity", "redis")
            return self._clarity_parts(now_ms, ("error", "redis"))
        try:
            stored = await asyncio.to_thread(lambda: [load_record(t) for t in texts])
        except Exception as exc:
            code = type(exc).__name__
            self._warn("clarity", code)
            return self._clarity_parts(now_ms, ("error", code))
        self._stored = dict(zip(self._kinds, stored, strict=True))
        if self._day_pending:  # 앞 갱신에서 못 쓴 날별 기록
            await self._save_day(bus)
        faults: dict[str, tuple[str, str | None]] = {}
        await self._step(self._base, stored[0], bus, now_ms, True, faults)
        blocked = self._base.blocks_pages()
        for kind, record in zip(self._kinds[1:], stored[1:], strict=True):
            await self._step(kind, record, bus, now_ms, not blocked, faults)
        base = self._base.record
        if blocked and base is not None:
            for kind in self._kinds[1:]:
                if kind.record is None:
                    # 기록이 없고 미뤘다 — 기본의 state·code 를 그대로 보인다
                    faults.setdefault(kind.name, (base.state or "error", base.code))
        return self._clarity_parts(now_ms, None, faults)

    async def _step(
        self,
        kind: ClarityKind,
        stored: Record,
        bus: ClarityStore,
        now_ms: int,
        allowed: bool,
        faults: dict[str, tuple[str, str | None]],
    ) -> None:
        """한 종류 — 때가 됐고 미루지 않으면 부르고, 간격을 따르는 동안 다시 쓸 기록이면 Redis 에 다시 쓴다.
        부르지 않는 요청(간격 안·미룸)이라도 Redis 에 있는 기록의 값이 7일 지났으면 값을 뺀 기록으로 다시 쓴다 —
        미룸이 길어져도(토큰 만료를 아무도 안 바꿈) Redis 에 7일 넘은 값이 남지 않게. 지운 키는 되살리지 않는다."""
        try:
            due, direct = kind.settle(stored, now_ms)
            if due and allowed:
                await self._call(kind, bus, direct)
                return
            expired = kind.expire(now_ms) and stored.attempt_at is not None
            if expired or (not due and kind.dirty):
                await self._save(kind, bus)
        except Exception as exc:
            code = type(exc).__name__
            self._warn(kind.name, code)
            faults[kind.name] = ("error", code)

    async def _call(self, kind: ClarityKind, bus: ClarityStore, direct: bool) -> None:
        # 시도 시각 = 보내는 때 — 갱신 시작이 아니다. 묶음은 기본(제한 10초) 뒤에 나가므로 시작 시각을 적으면
        # 다음 묶음이 보낸 때로부터 12시간보다 일찍 나가 어떤 24시간에 3회가 들 수 있다 (§3.2)
        sent_ms = int(self._clock() * 1000)
        if direct:
            kind.spend_direct(sent_ms)
        state, code, values = await fetch(
            self._token or "", self._transport, params=kind.params, parse=kind.parse
        )
        kept = kind.record.fresh(sent_ms) if kind.record is not None else None
        if state == "ok":
            record = Record(sent_ms, state, code, sent_ms, values)
        else:
            self._warn(kind.name, code)
            record = Record(
                sent_ms,
                state,
                code,
                kept.success_at if kept else None,
                kept.values if kept else None,
            )
        kind.record, kind.saved, kind.dirty = record, False, True
        await self._save(kind, bus)
        if state == "ok" and self._writes_day(kind, sent_ms):
            await self._save_day(bus)

    def _writes_day(self, kind: ClarityKind, sent_ms: int) -> bool:
        """날별 기록을 덮어쓸 성공인가 — 기본, 또는 기본의 마지막 성공과 같은 KST 날의 차원 성공(068 §3.2).
        차원이 다음 날에 성공하면 쓰지 않는다 — 기본의 날 칸에 든 그날 차원 값을 null 로 덮지 않게."""
        if kind is self._base:
            return True
        base = self._base.record
        return (
            kind is not self._pages
            and base is not None
            and base.success_at is not None
            and day_of(base.success_at) == day_of(sent_ms)
        )

    async def _save_day(self, bus: ClarityStore) -> None:
        """기본의 마지막 성공 KST 날 칸을 덮어쓴다 — 기본의 정규화 값 + 그날 성공한 차원의 목록(아니면 null).
        Redis 에 못 쓰면 다음 갱신이 다시 쓴다. 값을 다시 쓸 수 없으면(손으로 넣은 기록) 버린다."""
        self._day_pending = False
        base = self._base.record
        if base is None or base.success_at is None or base.values is None:
            return
        day = day_of(base.success_at)
        dims: dict[str, Any] = {}
        for kind, names in self._dims:
            # 메모리와 이번 갱신에 읽은 Redis 기록 중 늦은 성공 — 다시 뜬 앱의 첫 갱신은 기본이 차원보다 먼저 불려
            # 메모리가 비어 있다
            values = _day_values(day, kind.record, self._stored.get(kind))
            for name in names:
                dims[name] = None if values is None else values.get(name)
        try:
            text = await asyncio.to_thread(day_text, base.success_at, base.values, dims)
        except Exception as exc:
            self._warn("clarity.day", type(exc).__name__)
            return
        try:
            await bus.clarity_day_save(day, text, expire_at(day))
        except Exception:
            self._warn("clarity.day", "redis")
            self._day_pending = True

    async def _save(self, kind: ClarityKind, bus: ClarityStore) -> None:
        """기억한 기록을 Redis 에 — 실패하면 쓰이지 않은 채로 두고(다음 요청이 다시 쓴다) WARNING."""
        record = kind.record
        if record is None:
            return
        try:
            await kind.save(bus, await asyncio.to_thread(record.dump))
        except Exception:
            self._warn(kind.name, "redis")
            return
        if kind.record is record:
            kind.saved, kind.dirty = True, False

    def _clarity_parts(
        self,
        now_ms: int,
        fault: tuple[str, str | None] | None,
        faults: dict[str, tuple[str, str | None]] | None = None,
    ) -> Result:
        faults = faults or {}
        outer = self._base.part(now_ms, fault or faults.get("clarity"))
        inner = self._pages.part(now_ms, fault or faults.get("clarity.pages"))
        dims = _merged(
            [
                kind.part(now_ms, fault or faults.get(kind.name))
                for kind, _ in self._dims
            ]
        )
        return Result("ok", values={"outer": outer, "pages": inner, "dims": dims})

    # --- /admin/clarity/history (068) ---

    async def clarity_history(
        self, days: str | None, *, bus: ClarityStore | None
    ) -> dict[str, Any]:
        """날별 기록 — 창(7·30·90일, 목록 밖·없음은 30)마다 60초 칸. 토큰이 없으면 Redis 도 읽지 않는다(040 과 같다)."""
        n = HISTORY_DAYS.get(days or "", HISTORY_DEFAULT)
        if self._token is None:
            result = UNCONFIGURED
        else:
            try:
                result = await self._history[n].get(lambda: self._load_history(n, bus))
            except Exception as exc:
                result = self._failed("clarity.history", exc)
        body = render(result, HISTORY_REFRESH_SEC, HISTORY_KEYS)
        body["days"] = n  # 고른 창 — 어느 상태에도
        return self._writable("clarity.history", body, HISTORY_KEYS, keep=("days",))

    async def _load_history(self, n: int, bus: ClarityStore | None) -> Result:
        now_ms = int(self._clock() * 1000)
        days = window_days(now_ms, n)
        try:
            if bus is None:  # lifespan 전 — Redis 자리가 없다
                raise ConnectionError("no bus")
            texts = await bus.clarity_days_load(days)
        except Exception:
            self._warn("clarity.history", "redis")
            return Result("error", "redis")
        try:
            rows = await asyncio.to_thread(history_rows, days, texts)
        except Exception as exc:
            return self._failed("clarity.history", exc)
        return Result("ok", None, now_ms, {"rows": rows})

    async def clarity_recordings(self, days: str | None) -> dict[str, Any]:
        """Clarity 녹화 목록(077 §3.2) — 창(1·3·7·30일, 목록 밖·없음은 3)마다 30분 칸. 토큰이 없으면 부르지 않는다."""
        n = recordings.days_of(days)
        if self._token is None:
            result = UNCONFIGURED
        else:
            try:
                result = await self._recordings[n].get(lambda: self._load_recordings(n))
            except Exception as exc:
                result = self._failed("clarity.recordings", exc)
        body = render(result, recordings.REFRESH_SEC, recordings.KEYS)
        body["days"] = n  # 고른 창 — 어느 상태에도
        return self._writable(
            "clarity.recordings", body, recordings.KEYS, keep=("days",)
        )

    async def _load_recordings(self, n: int) -> Result:
        now_ms = int(self._clock() * 1000)
        state, code, values = await recordings.fetch(
            self._token or "", self._transport, recordings.request_body(now_ms, n)
        )
        if state != "ok":
            self._warn("clarity.recordings", code or state)
            return Result(state, code)
        return Result("ok", None, now_ms, values)

    # --- 공통 ---

    def _failed(self, name: str, exc: BaseException) -> Result:
        code = type(exc).__name__
        self._warn(name, code)
        return Result("error", code)

    def _writable(
        self,
        name: str,
        body: dict[str, Any],
        keys: tuple[str, ...],
        keep: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """라우터가 JSON 으로 쓸 수 있는 답인지 — 아니면 그 부분 `error`·예외 이름, 값 키는 `keep` 밖 모두 null."""
        try:
            check_json(body)
        except (TypeError, ValueError) as exc:
            code = type(exc).__name__
            self._warn(name, code)
            return {
                "state": "error",
                "code": code,
                "fetchedAt": None,
                "refreshSec": body["refreshSec"],
                **dict.fromkeys(keys),
                **{k: body[k] for k in keep},
            }
        return body


def _day_values(day: str, *records: Record | None) -> dict[str, Any] | None:
    """기록들 가운데 마지막 성공의 값 — 그 성공이 그 KST 날이 아니면 None."""
    done = [r for r in records if r is not None and r.success_at is not None]
    best = max(done, key=lambda r: r.success_at or 0, default=None)
    if best is None or best.success_at is None or day_of(best.success_at) != day:
        return None
    return best.values


def _merged(parts: list[Result]) -> Result:
    """차원 칸 둘 → 하위 부분 `dims` 하나. state·code 는 처음으로 ok 가 아닌 칸(기기 → 유입), `fetchedAt` 은 두 칸의
    마지막 성공 가운데 이른 쪽(경과를 넉넉히 보이게), 값은 칸마다 그 칸의 마지막 성공."""
    bad = next((part for part in parts if part.state != "ok"), None)
    fetched = [part.fetched_at for part in parts if part.fetched_at is not None]
    values: dict[str, Any] = {}
    for part in parts:
        values.update(part.values or {})
    return Result(
        bad.state if bad else "ok",
        bad.code if bad else None,
        min(fetched) if fetched else None,
        values,
    )


def _call_later(delay: float, callback: Callable[[], None]) -> asyncio.TimerHandle:
    return asyncio.get_running_loop().call_later(delay, callback)


def _body(result: Result, refresh_sec: int, keys: tuple[str, ...]) -> dict[str, Any]:
    """부분 → 응답 모양 `{state, code, fetchedAt, refreshSec, …값 키}` — 값은 state 와 무관하게 기억한 값."""
    values = result.values or {}
    return {
        "state": result.state,
        "code": result.code,
        "fetchedAt": result.fetched_at,
        "refreshSec": refresh_sec,
        **{k: values.get(k) for k in keys},
    }
