"""spark — `/spreads` 행의 김프(fwd 원값) 추이 링버퍼 (스펙 009 §3.6).

조합 (dom, fx, base) 마다 벽시계 1분 버킷(`ts // 60`)의 마지막 값 30개를 든다. 인계기가 틱마다
갱신하고 스냅샷을 LiveStore 에 게시한다 — Redis·Influx 성공과 무관한 메모리 계산이다.
기동 시 Influx 의 최근 30분 집계로 채운다(10초 상한, 실패면 빈 채로).
"""

import asyncio
import logging
from collections import deque
from collections.abc import Iterable
from typing import Protocol

from app.core.influx import SparkBucketRow
from app.core.live_store import LiveStore, SparkKey
from app.core.models import Tick

logger = logging.getLogger("marketlens.spark")

SPARK_LEN = 30  # 버킷 수 = 최근 30분
BUCKET_SEC = 60
RESTORE_TIMEOUT_SEC = 10.0


class SparkReader(Protocol):
    """기동 복원 조회 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def query_spark(self, *, start: int, stop: int) -> list[SparkBucketRow]: ...


class SparkBuffer:
    def __init__(self) -> None:
        # 조합 → (버킷, fwd) 오래된 → 최신. 같은 버킷은 마지막 값으로 덮인다.
        self._buf: dict[SparkKey, deque[tuple[int, float]]] = {}

    def update(self, tick: Tick) -> None:
        bucket = tick.ts // BUCKET_SEC
        for row in tick.rows:
            self._put((row.dom, row.fx, row.base.upper()), bucket, row.fwd)

    def seed(self, rows: Iterable[SparkBucketRow]) -> None:
        """복원 — 버킷 오름차순으로 넣는다(조회 결과 순서에 기대지 않는다)."""
        for r in sorted(rows, key=lambda r: r.bucket_ts):
            self._put((r.dom, r.fx, r.base.upper()), r.bucket_ts // BUCKET_SEC, r.fwd)

    def _put(self, key: SparkKey, bucket: int, value: float) -> None:
        buf = self._buf.get(key)
        if buf is None:
            buf = deque(maxlen=SPARK_LEN)
            self._buf[key] = buf
        if buf and buf[-1][0] == bucket:
            buf[-1] = (bucket, value)  # 같은 분 — 마지막 값
        elif not buf or buf[-1][0] < bucket:
            buf.append((bucket, value))
        # 이미 지난 버킷의 값은 무시한다 — 틱은 시각 순으로 오므로 정상 경로엔 없다

    def snapshot(self) -> dict[SparkKey, list[float]]:
        """LiveStore 에 게시할 맵 — 매번 새로 만든다(≈490 × ≤30 값)."""
        return {key: [v for _, v in buf] for key, buf in self._buf.items()}


async def restore_spark(
    reader: SparkReader | None, buffer: SparkBuffer, store: LiveStore, now_sec: int
) -> int:
    """기동 시 1회 — 기동 분을 포함한 30개 버킷을 Influx 에서 읽어 채우고 게시한다.

    Influx 가 없거나 실패·10초 초과면 빈 채로 시작해 회차마다 찬다(경고 1줄). 채운 행 수를 돌려준다.
    """
    if reader is None:
        logger.warning("Influx 가 없어 spark 를 복원하지 않는다 — 빈 채로 시작")
        return 0
    start = (now_sec // BUCKET_SEC - (SPARK_LEN - 1)) * BUCKET_SEC
    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(reader.query_spark, start=start, stop=now_sec),
            timeout=RESTORE_TIMEOUT_SEC,
        )
    except Exception as exc:
        logger.warning("spark 복원 실패 — 빈 채로 시작: %r", exc)
        return 0
    buffer.seed(rows)
    store.set_spark(buffer.snapshot())
    logger.info("spark 복원: %d점 (조합 %d개)", len(rows), len(buffer.snapshot()))
    return len(rows)
