"""api 관리자 피드 — `/admin/access`·`/admin/clarity` 의 부분 상태와 갱신 (스펙 035 §3.1~§3.3·040 §3.2).

공통 규칙은 034 와 같은 문장이다(`parts.py`). 접속 요약은 60초 캐시 한 칸이고 파일 읽기는 `asyncio.to_thread`
(기본 실행기 — api 에는 수집 쓰기가 없다). Clarity 는 요청마다 Redis 기록을 읽어 때를 정한다(`clarity_schedule.py`) —
기록이 Redis 에 있어 재시작이 하루 한도를 쓰지 않고, 키를 지우면 다음 요청이 바로 부른다(런북).
읽기·부르기는 한 번에 하나이고 3초까지 기다린 뒤 늦으면 직전 결과(없으면 pending)를 답한다. 요청이 없으면 아무것도
읽거나 부르지 않는다. 처리기 안 예외도 500 이 아니라 그 부분 `error` 와 WARNING 1줄(부분마다 10분에 1줄)이다 —
라우터가 응답을 JSON 으로 쓰다 실패하지 않게, 쓸 수 없는 값(NaN·짝 없는 서로게이트)이 든 답도 여기서 `error` 로 바꾼다.
JSON 풀기·쓰기는 파일 읽기와 같이 `asyncio.to_thread` 에서 한다.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.features.admin.access import VALUE_KEYS, NoLogFile, summarize
from app.features.admin.clarity import (
    ClarityStore,
    Record,
    check_json,
    fetch,
    load_record,
)
from app.features.admin.clarity_schedule import ClarityKind
from app.features.admin.clarity_values import parse_base
from app.features.admin.parts import (
    UNCONFIGURED,
    WAIT_SEC,
    Result,
    Slot,
    Warner,
    render,
)

ACCESS_REFRESH_SEC = 60
CLARITY_REFRESH_SEC = (
    10_800  # 시도 사이 최소 3시간 — 하루 10회 한도와 신선도의 교환 (§3.4)
)
CLARITY_KEYS = ("nextAt", "numOfDays", "traffic", "summary", "countries", "metrics")
BASE_PARAMS = {"numOfDays": "1"}


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
    ) -> None:
        self._dir = access_dir or None
        self._token = clarity_token or None
        self._transport = transport
        self._clock = clock
        self._access = Slot(ACCESS_REFRESH_SEC, mono, wait_sec)
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
        self._warn = Warner(mono)

    # --- /admin/access ---

    async def access(self) -> dict[str, Any]:
        try:
            result = await self._access.get(self._load_access)
        except Exception as exc:
            result = self._failed("access", exc)
        return self._writable(
            "access", render(result, ACCESS_REFRESH_SEC, VALUE_KEYS), VALUE_KEYS
        )

    async def _load_access(self) -> Result:
        now = self._clock()
        broken: list[str] = []  # 깨진 회전 파일 — 그 파일만 건너뛰고 WARNING 1줄
        try:
            values = await asyncio.to_thread(summarize, self._dir, now, broken.append)
        except NoLogFile:
            return Result("unconfigured", "no_file")
        except Exception as exc:
            return self._failed("access", exc)
        if broken:
            self._warn("access", broken[0])
        return Result("ok", None, int(now * 1000), values)

    # --- /admin/clarity ---

    async def clarity(self, *, bus: ClarityStore | None) -> dict[str, Any]:
        if self._token is None:
            result = UNCONFIGURED  # 토큰 없음 — Redis 도 Clarity 도 부르지 않는다
        else:
            try:
                result = await self._clarity.get(lambda: self._refresh_clarity(bus))
            except Exception as exc:
                result = self._failed("clarity", exc)
        # 값은 state 와 무관하게 마지막 성공 값이다(035 §3.3 — §3.1 의 예외). 경과는 fetchedAt 이 말한다
        body = _body(result, CLARITY_REFRESH_SEC, CLARITY_KEYS)
        return self._writable("clarity", body, CLARITY_KEYS)

    async def _refresh_clarity(self, bus: ClarityStore | None) -> Result:
        now_ms = int(self._clock() * 1000)
        texts: list[str | None] | None = None
        if bus is not None:  # None = lifespan 전 — Redis 자리가 없다
            try:
                texts = [await self._base.load(bus)]
            except Exception:
                texts = None
        if bus is None or texts is None:
            # 시도 시각을 모르면 부르지 않는다 — 한도를 지킨다. 원인 하나라 줄도 하나
            self._warn("clarity", "redis")
            return self._base.part(now_ms, ("error", "redis"))
        try:
            stored = await asyncio.to_thread(lambda: [load_record(t) for t in texts])
        except Exception as exc:
            code = type(exc).__name__
            self._warn("clarity", code)
            return self._base.part(now_ms, ("error", code))
        faults: dict[str, tuple[str, str | None]] = {}
        await self._step(self._base, stored[0], bus, now_ms, True, faults)
        return self._base.part(now_ms, faults.get("clarity"))

    async def _step(
        self,
        kind: ClarityKind,
        stored: Record,
        bus: ClarityStore,
        now_ms: int,
        allowed: bool,
        faults: dict[str, tuple[str, str | None]],
    ) -> None:
        """한 종류 — 때가 됐고 미루지 않으면 부르고, 간격을 따르는 동안 다시 쓸 기록이면 Redis 에 다시 쓴다."""
        try:
            due, direct = kind.settle(stored, now_ms)
            if due and allowed:
                if direct:
                    kind.direct_at = now_ms
                await self._call(kind, bus, now_ms)
            elif not due and kind.dirty:
                await self._save(kind, bus)
        except Exception as exc:
            code = type(exc).__name__
            self._warn(kind.name, code)
            faults[kind.name] = ("error", code)

    async def _call(self, kind: ClarityKind, bus: ClarityStore, now_ms: int) -> None:
        state, code, values = await fetch(
            self._token or "", self._transport, params=kind.params, parse=kind.parse
        )
        kept = kind.record.fresh(now_ms) if kind.record is not None else None
        if state == "ok":
            record = Record(now_ms, state, code, now_ms, values)
        else:
            self._warn(kind.name, code)
            record = Record(
                now_ms,
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

    # --- 공통 ---

    def _failed(self, name: str, exc: BaseException) -> Result:
        code = type(exc).__name__
        self._warn(name, code)
        return Result("error", code)

    def _writable(
        self, name: str, body: dict[str, Any], keys: tuple[str, ...]
    ) -> dict[str, Any]:
        """라우터가 JSON 으로 쓸 수 있는 답인지 — 아니면 그 부분 `error`·예외 이름, 값 키는 모두 null."""
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
            }
        return body


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
