"""api 관리자 피드 — `/admin/access`·`/admin/clarity` 의 부분 상태와 갱신 (스펙 035 §3.1~§3.2·038 §3.1~§3.3·040 §3.2·§3.5).

공통 규칙은 034 와 같은 문장이다(`parts.py`). 접속 요약은 창(24h·7d·30d)마다 따로 60초 칸이고, 파일 캐시는 창 셋이
나눠 쓰며 파일 읽기는 `asyncio.to_thread`(기본 실행기 — api 에는 수집 쓰기가 없다). 마지막 접속 요청에서 1시간이 지나면
파일 캐시·짝 기록·열쇠·창 칸을 통째로 버린다(038 §3.3). Clarity 는 호출이 둘이다 — 기본 요약(4시간)과 페이지×기기 묶음(12시간,
하위 부분 `pages`). 요청마다 Redis 기록 둘을 읽어 때를 정하고(`clarity_schedule.py`), 한 갱신 안에서 기본 → 묶음
순서로 하나씩 부른다 — Clarity 호출이 동시에 둘 나가지 않는다. 기본이 401·403·429 면 묶음은 미룬다.
접속 갱신이 게이트 뒤 ok 이면 DB-IP 판(039)이 필요한지 보고 받기를 띄운다 — 갱신은 받기를 기다리지 않고, 판이 없는
동안 하위 부분 `geo` 는 받는 중 `pending`·실패 `error` 다. 판은 캐시 잠금 안에서 하나 집어 그 회차 끝까지 쓴다 — 창
둘의 갱신이 갈아 끼우는 순간에 겹쳐도 옛 판으로 캐시를 다시 만들지 않게.
읽기·부르기는 한 번에 하나이고 3초까지 기다린 뒤 늦으면 직전 결과(없으면 pending)를 답한다. 요청이 없으면 아무것도
읽거나 부르지 않는다. 처리기 안 예외도 500 이 아니라 그 부분 `error` 와 WARNING 1줄(부분마다 10분에 1줄, 예외 이름만 —
문장에 가린 IP·UA 가 실릴 수 있다)이다 — 라우터가 응답을 JSON 으로 쓰다 실패하지 않게, 쓸 수 없는 값(NaN·짝 없는
서로게이트)이 든 답도 여기서 `error` 로 바꾼다(`pages` 는 따로 — 바깥을 바꾸지 않는다). JSON 풀기·쓰기는 파일 읽기와 같이
`asyncio.to_thread` 에서 한다.
"""

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.core import config
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
from app.features.admin.clarity import (
    ClarityStore,
    Record,
    check_json,
    fetch,
    load_record,
)
from app.features.admin.clarity_pages import parse_pages
from app.features.admin.clarity_schedule import ClarityKind
from app.features.admin.clarity_values import parse_base
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
# 시도 사이 — 어떤 24시간에도 기본 6 + 묶음 2 = 8회라 하루 10회 한도에서 사람 몫 2회가 남는다 (040 §3.2)
CLARITY_REFRESH_SEC = 14_400
PAGES_REFRESH_SEC = 43_200
CLARITY_KEYS = ("nextAt", "numOfDays", "traffic", "summary", "countries", "metrics")
PAGES_KEYS = ("nextAt", "numOfDays", "rowsIn", "rowLimitHit", "groups")
BASE_PARAMS = {"numOfDays": "1"}
# 동의한 방문자만이라 표본이 작아 72시간 — 차원은 둘(주소·기기)
PAGES_PARAMS = {"numOfDays": "3", "dimension1": "URL", "dimension2": "Device"}


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
        # 주기 0 — Clarity 의 캐시는 Redis 기록이고, 이 칸은 한 번에 하나·3초 기다림·직전 결과만 맡는다
        self._clarity = Slot(0, mono, wait_sec)
        self._base = ClarityKind(
            name="clarity",
            refresh_sec=CLARITY_REFRESH_SEC,
            params=BASE_PARAMS,
            parse=parse_base,
            load=lambda bus: bus.clarity_load(),
            save=lambda bus, data: bus.clarity_save(data),
        )
        self._pages = ClarityKind(
            name="clarity.pages",
            refresh_sec=PAGES_REFRESH_SEC,
            params=PAGES_PARAMS,
            parse=parse_pages,
            load=lambda bus: bus.clarity_pages_load(),
            save=lambda bus, data: bus.clarity_pages_save(data),
        )
        self._warn = Warner(mono)
        # 039 — DB-IP 판. 1시간 비움(§3.3)과 무관하게 프로세스에 남는다(방문자 정보가 아닌 공개 자료)
        self.geo = GeoLoader(
            clock=clock,
            mono=mono,
            warn=self._warn,
            transport=geo_transport,
            start=geo_start,
        )

    # --- /admin/access ---

    def _fresh_access(self) -> tuple[AccessLog, dict[str, Slot]]:
        log = AccessLog(self._dir, self._gate, urandom=self._urandom, mono=self._mono)
        return log, {
            name: Slot(ACCESS_REFRESH_SEC, self._mono, self._wait) for name in WINDOWS
        }

    def _drop_access(self) -> None:
        """1시간 안 부르지 않음 — 요약이 창과 원본보다 오래 남지 않게 통째로 버린다. 다음 요청은 첫 채움부터."""
        self._idle = None
        self._log, self._access = self._fresh_access()

    async def access(self, window: str | None = None) -> dict[str, Any]:
        now = self._clock()
        name = choose(window, now, self._gate)
        if self._idle is not None:
            self._idle.cancel()
        self._idle = self._schedule(ACCESS_IDLE_SEC, self._drop_access)
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

    # --- /admin/clarity ---

    async def clarity(self, *, bus: ClarityStore | None) -> dict[str, Any]:
        if self._token is None:
            outer = inner = (
                UNCONFIGURED  # 토큰 없음 — Redis 도 Clarity 도 부르지 않는다
            )
        else:
            try:
                result = await self._clarity.get(lambda: self._refresh_clarity(bus))
            except Exception as exc:
                result = self._failed("clarity", exc)
            parts = result.values or {}
            outer = parts.get("outer", result)  # pending·처리기 예외면 둘이 같은 상태
            inner = parts.get("pages", result)
        # 값은 state 와 무관하게 마지막 성공 값이다(035 §3.3 — §3.1 의 예외). 경과는 fetchedAt 이 말한다
        pages = self._writable(
            "clarity.pages", _body(inner, PAGES_REFRESH_SEC, PAGES_KEYS), PAGES_KEYS
        )
        body = self._writable(
            "clarity", _body(outer, CLARITY_REFRESH_SEC, CLARITY_KEYS), CLARITY_KEYS
        )
        return {**body, "pages": pages}

    async def _refresh_clarity(self, bus: ClarityStore | None) -> Result:
        now_ms = int(self._clock() * 1000)
        texts: list[str | None] | None = None
        if bus is not None:  # None = lifespan 전 — Redis 자리가 없다
            try:
                texts = [await kind.load(bus) for kind in (self._base, self._pages)]
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
        faults: dict[str, tuple[str, str | None]] = {}
        await self._step(self._base, stored[0], bus, now_ms, True, faults)
        blocked = self._base.blocks_pages()
        await self._step(self._pages, stored[1], bus, now_ms, not blocked, faults)
        base = self._base.record
        if blocked and base is not None and self._pages.record is None:
            # 기록이 없고 미뤘다 — 기본의 state·code 를 그대로 보인다
            faults.setdefault("clarity.pages", (base.state or "error", base.code))
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
            kind.direct_at = sent_ms
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
        return Result("ok", values={"outer": outer, "pages": inner})

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
