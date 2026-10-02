"""관리자 피드 공통 — 부분 결과·응답 모양·캐시 한 칸·경고 억제 (스펙 034 §3.1·035 §3.1 — 두 스펙이 같은 문장으로 나눠 가진다).

부분은 `{state, code, fetchedAt, refreshSec, …값 키}` 다. 갱신 규칙은 022 랜딩과 같은 모양이다 —
요청이 왔을 때 비었거나 주기가 지났으면 갱신을 하나만 띄우고 3초까지 기다린 뒤, 늦으면 직전 결과(없으면 pending)를
답하고 갱신은 뒤에서 마저 돈다. 실패도 주기만큼 캐시한다. 요청이 없으면 아무것도 부르지 않는다.
실패는 `marketlens.admin` 에 WARNING 으로 부분 이름과 code 만, 부분마다 10분에 1줄. ERROR 로 남기지 않는다 —
025 로그 핸들러가 `marketlens.*` 의 ERROR 를 예외 문장과 함께 Slack 으로 보낸다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("marketlens.admin")

WAIT_SEC = 3.0
WARN_EVERY_SEC = 600.0


@dataclass(frozen=True)
class Result:
    state: str
    code: str | None = None
    fetched_at: int | None = None  # ms — ok 일 때만
    values: dict[str, Any] | None = None  # ok 일 때만


PENDING = Result("pending")
UNCONFIGURED = Result("unconfigured")


def render(result: Result, refresh_sec: int, keys: tuple[str, ...]) -> dict[str, Any]:
    """ok 가 아니면 값 키는 모두 null — 직전 값을 정상처럼 보이지 않게 (029 §3.3 원칙)."""
    values = result.values if result.state == "ok" and result.values else {}
    return {
        "state": result.state,
        "code": result.code,
        "fetchedAt": result.fetched_at,
        "refreshSec": refresh_sec,
        **{k: values.get(k) for k in keys},
    }


def exception_name(exc: BaseException) -> tuple[str, str]:
    """예외 → (`error`, 예외 이름). 오류 문장은 버린다."""
    return "error", type(exc).__name__


class Slot:
    """부분 하나의 캐시 — 결과(실패 포함)를 주기 동안 들고, 갱신은 한 번에 하나(같은 태스크를 같이 기다린다)."""

    def __init__(
        self,
        refresh_sec: int,
        mono: Callable[[], float],
        wait_sec: float,
        classify: Callable[[BaseException], tuple[str, str]] = exception_name,
    ) -> None:
        self._refresh = refresh_sec
        self._mono = mono
        self._wait = wait_sec
        self._classify = classify
        self._result: Result | None = None
        self._stored = 0.0
        self._task: asyncio.Task[Result] | None = None

    async def get(self, load: Callable[[], Awaitable[Result]]) -> Result:
        if self._result is not None and self._mono() - self._stored < self._refresh:
            return self._result
        if self._task is None:
            self._task = asyncio.create_task(self._fill(load))
        try:
            # shield — 기다림이 끝나거나 요청이 끊겨도 갱신은 끝까지 돌아 캐시를 채운다
            return await asyncio.wait_for(asyncio.shield(self._task), self._wait)
        except TimeoutError:
            return self._result or PENDING

    async def _fill(self, load: Callable[[], Awaitable[Result]]) -> Result:
        try:
            try:
                result = await load()
            except Exception as exc:
                state, code = self._classify(exc)
                result = Result(state, code)
            self._result = result
            self._stored = self._mono()
            return result
        finally:
            self._task = None


class Warner:
    """부분마다 10분에 1줄 — 이름과 code 만 (오류 문장·헤더·토큰 없이)."""

    def __init__(self, mono: Callable[[], float]) -> None:
        self._mono = mono
        self._warned: dict[str, float] = {}

    def __call__(self, name: str, code: str | None) -> None:
        now = self._mono()
        last = self._warned.get(name)
        if last is not None and now - last < WARN_EVERY_SEC:
            return
        self._warned[name] = now
        logger.warning("관리자 피드 %s 실패 — %s", name, code)
