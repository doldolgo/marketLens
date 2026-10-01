"""api 관리자 피드 — `/admin/access`·`/admin/clarity` 의 부분 상태와 갱신 (스펙 035 §3.1~§3.3).

공통 규칙은 034 와 같은 문장이다(`parts.py`). 접속 요약은 60초 캐시 한 칸이고 파일 읽기는 `asyncio.to_thread`
(기본 실행기 — api 에는 수집 쓰기가 없다). Clarity 는 요청마다 Redis `admin:clarity` 를 읽어 마지막 시도에서 3시간이
지났을 때만 부른다 — 기록이 Redis 에 있어 재시작이 하루 한도를 쓰지 않고, 키를 지우면 다음 요청이 바로 부른다(런북).
읽기·부르기는 한 번에 하나이고 3초까지 기다린 뒤 늦으면 직전 결과(없으면 pending)를 답한다. 요청이 없으면 아무것도
읽거나 부르지 않는다. 처리기 안 예외도 500 이 아니라 그 부분 `error` 와 WARNING 1줄(부분마다 10분에 1줄)이다 —
라우터가 응답을 JSON 으로 쓰다 실패하지 않게, 쓸 수 없는 값(NaN·짝 없는 서로게이트)이 든 답도 여기서 `error` 로 바꾼다.
JSON 풀기·쓰기(`admin:clarity`)는 파일 읽기와 같이 `asyncio.to_thread` 에서 한다.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.features.admin.access import VALUE_KEYS, NoLogFile, summarize
from app.features.admin.clarity import (
    GAP_MS,
    ClarityStore,
    Record,
    check_json,
    fetch,
    load_record,
)
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
CLARITY_KEYS = ("nextAt", "numOfDays", "traffic", "metrics")


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
        self._record = Record()  # 마지막으로 본 기록 — Redis 에 못 닿을 때 값을 잇는다
        self._unsaved_at: int | None = (
            None  # 불렀는데 Redis 에 못 쓴 시각 — 3시간 동안 다시 부르지 않는다
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
        values = result.values or {}
        # 값은 state 와 무관하게 마지막 성공 값이다(§3.3 — §3.1 의 예외). 경과는 fetchedAt 이 말한다
        body = {
            "state": result.state,
            "code": result.code,
            "fetchedAt": result.fetched_at,
            "refreshSec": CLARITY_REFRESH_SEC,
            **{k: values.get(k) for k in CLARITY_KEYS},
        }
        return self._writable("clarity", body, CLARITY_KEYS)

    async def _refresh_clarity(self, bus: ClarityStore | None) -> Result:
        now_ms = int(self._clock() * 1000)
        try:
            return await self._clarity_step(bus, now_ms)
        except Exception as exc:
            self._warn("clarity", type(exc).__name__)
            return self._from_record(self._record, now_ms, "error", type(exc).__name__)

    async def _clarity_step(self, bus: ClarityStore | None, now_ms: int) -> Result:
        try:
            if bus is None:
                raise ConnectionError("no bus")  # lifespan 전 — Redis 자리가 없다
            text = await bus.clarity_load()
        except Exception:
            # 시도 시각을 모르면 부르지 않는다 — 한도를 지킨다
            self._warn("clarity", "redis")
            return self._from_record(self._record, now_ms, "error", "redis")
        record = await asyncio.to_thread(load_record, text)
        if self._unsaved_at is not None and (record.attempt_at or 0) < self._unsaved_at:
            record = self._record  # Redis 에 못 쓴 마지막 시도가 더 늦다
        if record.attempt_at is not None and now_ms - record.attempt_at < GAP_MS:
            self._record = record
            return self._from_record(record, now_ms)
        state, code, values = await fetch(self._token or "", self._transport)
        kept = record.fresh(now_ms)
        if state == "ok":
            record = Record(now_ms, state, code, now_ms, values)
        else:
            self._warn("clarity", code)
            record = Record(now_ms, state, code, kept.success_at, kept.values)
        self._record = record
        try:
            await bus.clarity_save(await asyncio.to_thread(record.dump))
            self._unsaved_at = None
        except Exception:
            self._unsaved_at = now_ms
            self._warn("clarity", "redis")
        return self._from_record(record, now_ms)

    def _from_record(
        self,
        record: Record,
        now_ms: int,
        state: str | None = None,
        code: str | None = None,
    ) -> Result:
        """기록 → 부분. 값은 마지막 성공에서 7일 안일 때만, `nextAt` = 마지막 시도 + 3시간."""
        kept = record.fresh(now_ms)
        values = dict(kept.values or {})
        values["nextAt"] = None if kept.attempt_at is None else kept.attempt_at + GAP_MS
        if state is None:
            state, code = kept.state or "error", kept.code
        return Result(state, code, kept.success_at, values)

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
