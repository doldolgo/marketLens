"""수집 프로세스의 표 게시 — 틱 직후 $1,000 표를 만들어 Redis 로 (스펙 017 §3.1).

틱 루프가 013·014 다음 자리에서 `observe(tick)` 를 부른다(동기·무예외). 표는 `GET /spreads` 와 같은
함수로 만들어 같은 JSON 을 큐에 넣고, 별도 태스크가 PUBLISH + SET 한다 — 009 의 인계기와 같은 모양이라
게시 실패가 틱을 막지 않는다. 표는 접속자 유무와 무관하게 매 틱 만든다(2026-09-26 결정) — 첫 접속자가
`spreads:latest` 로 즉시 snapshot 을 받게 하기 위해서다. `spreads:want` 는 더 이상 읽지 않는다.
표 JSON 의 spark 자리는 009 가 들고 있는 조합별 JSON 조각을 끼운다 — `encode_table` 과 같은 바이트다.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Mapping
from typing import cast

from app.core.live_store import LiveStore, SparkKey
from app.core.models import Tick
from app.core.networks import WalletMemo
from app.core.redis_bus import RedisBus
from app.features.spreads.service import (
    DEFAULT_NOTIONAL,
    MarketDataNotFoundError,
    build_table,
)

logger = logging.getLogger("marketlens.spreads_push")

# 한 회차 표 생성 상한 — 넘으면 틱 루프가 그만큼 멈춘 것 (§3.1)
SLOW_BUILD_WARN_SEC = 0.3
# 같은 원인 경고 간격 — 009 인계 실패 로그와 같은 톤
LOG_SUPPRESS_SEC = 60.0
# 표는 매초 새로 나오므로 밀린 표는 값이 없다 — 오래된 것부터 버린다 (§3.1)
QUEUE_LIMIT = 2
# 표 JSON 에서 행의 spark 자리 표식 — 목록 대신 0 을 넣어 인코딩한 뒤 이 자리마다 조각을 끼운다.
# JSON 문자열 안의 따옴표는 \" 로 나가므로 이 글자는 행의 `spark` 키 자리에만 생긴다
_SPARK_MARK = '"spark":0'


def encode_table(payload: dict[str, object]) -> str:
    """`build_table` 의 dict 를 표 바이트로 — camelCase·공백 없음·NaN 금지 (018 `GET /spreads` 가 그대로 답한다)."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def encode_table_with_spark_json(
    payload: dict[str, object], spark_json: Mapping[SparkKey, str]
) -> str:
    """`encode_table(payload)` 와 같은 바이트 — spark 자리에 009 가 게시한 JSON 조각을 끼운다 (§3.1).

    표 1장에 spark 부동소수가 4만 개 넘게 드는데 조합마다 틱당 끝값 하나만 바뀐다. 그래서 spark 를 뺀 표만
    인코딩하고 조각을 끼운다. 조각이 없는 조합(유한하지 않은 값이 든 조합·조각 없이 게시된 맵)은 그 목록을
    그대로 인코딩한다 — 결과도 오류(NaN 거부)도 `encode_table` 과 같다. `payload` 는 돌려받을 때 그대로다.

    전제: `spark_json` 은 `payload` 를 만든 그 동기 구간에서 읽은 조각 맵이다 — 그 사이 009 가 다시 게시하면
    조각이 표에 든 목록과 다른 틱의 값이 되어 바이트가 갈린다. 그래서 보내기 태스크(await 뒤)로 옮기지 않는다.
    """
    rows = cast(list[dict[str, object]], payload["rows"])
    sparks = [row["spark"] for row in rows]
    for row in rows:
        row["spark"] = 0
    try:
        text = encode_table(payload)
    finally:
        for row, spark in zip(rows, sparks, strict=True):
            row["spark"] = spark
    parts = text.split(_SPARK_MARK)
    out = [parts[0]]
    for row, spark, part in zip(rows, sparks, parts[1:], strict=True):
        fragment = spark_json.get(cast(SparkKey, (row["dom"], row["fx"], row["sym"])))
        if fragment is None:
            fragment = json.dumps(
                spark, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
        out.append('"spark":')
        out.append(fragment)
        out.append(part)
    return "".join(out)


class LogSuppressor:
    """같은 원인의 경고를 60초에 1줄로 — 원인 = 호출자가 주는 문자열(예: 예외 타입 이름)."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}

    def allow(self, cause: str) -> bool:
        now = time.monotonic()
        if now - self._last.get(cause, -LOG_SUPPRESS_SEC) < LOG_SUPPRESS_SEC:
            return False
        self._last[cause] = now
        return True


class SpreadsPublisher:
    def __init__(
        self,
        *,
        store: LiveStore,
        bus: RedisBus,
        day_open: Mapping[tuple[str, str], float] | None = None,
        wallet_memo: WalletMemo | None = None,
    ) -> None:
        self._store = store
        self._bus = bus
        self._day_open = day_open  # 026 — 기준가 장부(같은 틱에서 방금 갱신된 것)
        # 006 §3.7 — 같은 회차의 틱이 채운 망 판정 메모. 없으면 표가 행마다 판정한다
        self._wallet_memo = wallet_memo
        self._queue: deque[str] = deque(maxlen=QUEUE_LIMIT)
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._suppress = LogSuppressor()

    @property
    def pending(self) -> int:
        return len(self._queue)

    def observe(self, tick: Tick) -> None:
        """틱 직후 같은 회차 — 표 1장을 만들어 큐에. 동기·무예외."""
        try:
            started = time.perf_counter()
            try:
                payload = build_table(
                    self._store,
                    notional=DEFAULT_NOTIONAL,
                    day_open=self._day_open,
                    wallet_memo=self._wallet_memo,
                )
            except MarketDataNotFoundError:
                # 환율 없음·국내/해외 스냅샷 없음 — 이 회차는 표를 만들지 않는다, 경고 없음(기동 직후 정상) (018 §3.2)
                return
            self._queue.append(
                encode_table_with_spark_json(payload, self._store.spark_json())
            )
            elapsed = time.perf_counter() - started
            if elapsed > SLOW_BUILD_WARN_SEC:
                logger.warning(
                    "표 생성 %.0fms — 상한 %.0fms 초과",
                    elapsed * 1000,
                    SLOW_BUILD_WARN_SEC * 1000,
                )
            self._wake.set()
        except Exception:
            logger.exception("표 게시 준비 중 예외 — 이 회차는 건너뛴다 ts=%d", tick.ts)

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.run_sender_loop(), name="spreads_publish"),
        ]

    async def run_sender_loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            await self.drain()

    async def drain(self) -> int:
        """큐의 표를 순서대로 게시. 실패한 표는 버리고 같은 원인은 60초에 1줄. 보낸 수를 돌려준다."""
        sent = 0
        while self._queue:
            data = self._queue.popleft()
            try:
                await self._bus.publish_table(data)
                sent += 1
            except Exception as exc:
                if self._suppress.allow(type(exc).__name__):
                    logger.warning("표 게시 실패 — 이 회차는 건너뛴다: %r", exc)
        return sent

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self._queue.clear()  # 종료 시 남은 표는 값이 없다 — 틱과 달리 보내 보지 않는다
