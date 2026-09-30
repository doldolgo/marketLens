"""수집기 관리자 피드 — `/admin/aws`·`/admin/alerts` 의 부분별 캐시·갱신·상태 (스펙 034 §3.1~§3.3).

응답은 부분들이고 부분은 `{state, code, fetchedAt, refreshSec, …값 키}` 다. 갱신 규칙은 022 랜딩과 같은 모양이다 —
요청이 왔을 때 비었거나 주기가 지났으면 갱신을 하나만 띄우고 3초까지 기다린 뒤, 늦으면 직전 결과(없으면 pending)를
답하고 갱신은 뒤에서 마저 돈다. 실패도 주기만큼 캐시한다. 요청이 없으면 아무것도 부르지 않는다.
AWS 호출은 전용 실행기(스레드 1개)에서 한 번에 하나씩 — 기본 실행기(`asyncio.to_thread`)는 수집 쓰기가 쓴다(§3.1).
실패는 `marketlens.admin` 에 WARNING 으로 부분 이름과 code 만, 부분마다 10분에 1줄. ERROR 로 남기지 않는다 —
025 로그 핸들러가 `marketlens.*` 의 ERROR 를 예외 문장과 함께 Slack 으로 보낸다.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.redact import redact
from app.features.admin.aws import AwsReader, classify, client_factory

logger = logging.getLogger("marketlens.admin")

WAIT_SEC = 3.0
WARN_EVERY_SEC = 600.0
ALERTS_WINDOW_MS = 7 * 86_400 * 1000
ALERTS_LIMIT = 200
# 부분 → (주기 초, 값 키). 주기는 요금과 신선도의 교환 — 사람 확인 대상의 기본값 (§3.1)
AWS_PARTS: dict[str, tuple[int, tuple[str, ...]]] = {
    "alarms": (60, ("items", "counts")),
    "metrics": (300, ("endTs", "startTs", "periodSec", "boxes", "wsClients", "canary")),
    "canary": (60, ("lastRunAt", "durationMs", "ok", "lines")),
    "budget": (21_600, ("items",)),
}
HISTORY_REFRESH_SEC = AWS_PARTS["alarms"][0]  # 경보 이력은 alarms 주기를 따른다 (§3.3)
_ITEM_KEYS = (
    "at",
    "source",
    "text",
    "role",
    "key",
    "delivered",
    "alarm",
    "fromState",
    "toState",
)


class AlertLog(Protocol):
    """core `RedisBus.alert_log_recent` 시그니처 — 최신순 JSON 줄."""

    async def alert_log_recent(self, limit: int) -> list[str]: ...


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


class _Slot:
    """부분 하나의 캐시 — 결과(실패 포함)를 주기 동안 들고, 갱신은 한 번에 하나(같은 태스크를 같이 기다린다)."""

    def __init__(
        self, refresh_sec: int, mono: Callable[[], float], wait_sec: float
    ) -> None:
        self._refresh = refresh_sec
        self._mono = mono
        self._wait = wait_sec
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
                state, code = classify(exc)
                result = Result(state, code)
            self._result = result
            self._stored = self._mono()
            return result
        finally:
            self._task = None


class AdminFeeds:
    """수집기 앱 하나에 하나(`app.state.admin_feeds`). AWS 설정·시계·기다림·클라이언트는 테스트가 바꿀 수 있게 주입한다."""

    def __init__(
        self,
        *,
        region: str | None,
        client: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        wait_sec: float = WAIT_SEC,
    ) -> None:
        self._region = region or None
        self._clock = clock
        self._mono = mono
        make = client if client is not None else client_factory(region or "", mono)
        self._clients: dict[str, Any] = {}

        def cached(service: str) -> Any:
            # 스레드 안에서 처음 쓸 때 만든다 — 기동에 넣지 않는다(§3.2). 만들기 실패는 그 부분의 실패다
            if service not in self._clients:
                self._clients[service] = make(service)
            return self._clients[service]

        self._reader = AwsReader(cached, clock=clock, mono=mono)
        # 스레드는 첫 제출 때 생긴다 — 설정이 없거나 페이지를 안 보면 스레드도 없다
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="admin-aws"
        )
        self._slots = {
            name: _Slot(refresh, mono, wait_sec)
            for name, (refresh, _) in AWS_PARTS.items()
        }
        self._history = _Slot(HISTORY_REFRESH_SEC, mono, wait_sec)
        self._warned: dict[str, float] = {}

    # --- /admin/aws ---

    async def aws(self) -> dict[str, Any]:
        readers = {
            "alarms": self._reader.alarms,
            "metrics": self._reader.metrics,
            "canary": self._reader.canary,
            "budget": self._reader.budget,
        }
        results = await asyncio.gather(
            *(
                self._aws_part(f"aws.{name}", self._slots[name], readers[name])
                for name in AWS_PARTS
            )
        )
        return {
            name: render(result, *AWS_PARTS[name])
            for name, result in zip(AWS_PARTS, results, strict=True)
        }

    # --- /admin/alerts ---

    async def alerts(
        self, *, bus: AlertLog | None, slack_configured: bool
    ) -> dict[str, Any]:
        (slack, slack_items), history = await asyncio.gather(
            self._slack(bus, slack_configured),
            self._aws_part("alerts.alarms", self._history, self._reader.alarm_history),
        )
        now_ms = int(self._clock() * 1000)
        items = list(slack_items)
        if history.state == "ok" and history.values:
            items.extend(
                {**dict.fromkeys(_ITEM_KEYS), **item, "source": "alarm"}
                for item in history.values.get("items", [])
                if isinstance(item.get("at"), int)
            )
        # 두 출처를 시각으로 합친 최근 7일·최신순·200건 — 캐시된 이력도 지금 기준으로 7일을 다시 자른다
        items = [i for i in items if i["at"] >= now_ms - ALERTS_WINDOW_MS]
        items.sort(key=lambda i: i["at"], reverse=True)
        return {
            "items": items[:ALERTS_LIMIT],
            "slack": render(slack, 0, ()),
            "alarms": render(history, HISTORY_REFRESH_SEC, ()),
        }

    async def _slack(
        self, bus: AlertLog | None, configured: bool
    ) -> tuple[Result, list[dict[str, Any]]]:
        """요청마다 Redis 를 읽는다(refreshSec 0). 웹훅이 없는 프로세스는 unconfigured — 읽지 않는다."""
        if not configured:
            return UNCONFIGURED, []
        try:
            if bus is None:
                raise ConnectionError("no bus")  # lifespan 전 — Redis 자리가 없다
            lines = await bus.alert_log_recent(ALERTS_LIMIT)
        except Exception:
            self._warn("alerts.slack", "redis")
            return Result("error", "redis"), []
        now_ms = int(self._clock() * 1000)
        items = [item for item in map(_slack_item, lines) if item is not None]
        return Result("ok", None, now_ms, {}), items

    # --- 공통 ---

    async def _aws_part(
        self, name: str, slot: _Slot, read: Callable[[], dict[str, Any]]
    ) -> Result:
        if self._region is None:
            return UNCONFIGURED  # 설정 없음 — AWS 를 부르지 않는다(로컬·테스트 기본)

        async def load() -> Result:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._executor, self._run, read)
            if result.state != "ok":
                self._warn(name, result.code)
            return result

        try:
            return await slot.get(load)
        except Exception as exc:
            # 처리기 안 예외도 500 이 아니라 그 부분 error (§3.1)
            return Result("error", type(exc).__name__)

    def _run(self, read: Callable[[], dict[str, Any]]) -> Result:
        """전용 스레드 — 읽기·풀기를 한 번에. 예외는 여기서 상태로 바꾼다(문장은 버린다)."""
        try:
            values = read()
        except Exception as exc:
            state, code = classify(exc)
            if code == "no_credentials":
                # 자격증명 없이 만든 클라이언트는 나중에 역할이 붙어도 계속 없다 — 버려 다음 갱신이 다시 찾게 한다
                # (`client_factory` 는 자격증명이 없으면 만들지 않는다 — 주입한 클라이언트를 위한 자리)
                self._clients.clear()
            return Result(state, code)
        return Result("ok", None, int(self._clock() * 1000), values)

    def _warn(self, name: str, code: str | None) -> None:
        now = self._mono()
        last = self._warned.get(name)
        if last is not None and now - last < WARN_EVERY_SEC:
            return
        self._warned[name] = now
        logger.warning("관리자 피드 %s 실패 — %s", name, code)

    def close(self) -> None:
        """수집기 종료 때 — 대기 중인 호출은 버리고 도는 호출(최대 7초)은 기다리지 않는다."""
        self._executor.shutdown(wait=False, cancel_futures=True)


def _slack_item(line: str) -> dict[str, Any] | None:
    """`alerts:log` 한 줄 → 타임라인 항목. 깨진 줄은 None(뺀다). 넣을 때 가렸지만 읽을 때도 한 번 더 가린다(`key` 도)."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    at, text, role, key, delivered = (
        data.get(k) for k in ("at", "text", "role", "key", "delivered")
    )
    if type(at) is not int or not isinstance(delivered, bool):
        return None
    if not (isinstance(text, str) and isinstance(role, str) and isinstance(key, str)):
        return None
    return {
        **dict.fromkeys(_ITEM_KEYS),
        "at": at,
        "source": "slack",
        "text": redact(text),
        "role": role,
        "key": redact(key),
        "delivered": delivered,
    }
