"""snapshot 루프 — 60초마다 `/spreads` 행 전체를 S3 에 `.jsonl.gz` 한 객체로 (스펙 010 §3.4~3.5).

앱 기동(main.py lifespan)이 이 루프를 관리한다. 005 persist 루프와 독립이며 같은 규칙을
따른다 — 실패는 로그 후 다음 회차 재시도, 놓친 회차는 구멍으로 남는다(소급 안 함).
표 계산은 003 의 공개 함수(build_spreads) 하나를 그대로 쓴다 — 김프 재정의 금지.
"""

import asyncio
import gzip
import json
import logging
from datetime import UTC, datetime
from typing import Protocol

from app.core.collector import Collector
from app.core.live_store import LiveStore
from app.core.s3 import S3UnavailableError
from app.core.serialization import camelize_json
from app.features.spreads.models import SpreadsResponse
from app.features.spreads.service import (
    MarketDataNotFoundError,
    build_spreads,
)

logger = logging.getLogger("marketlens.snapshot")

# 저장 주기(초)는 코드 상수다 — 005 와 같다 (스펙 010 §3.2)
SNAPSHOT_INTERVAL = 60.0


class ObjectPutter(Protocol):
    """snapshot 루프가 쓰는 최소 인터페이스 — 실물은 core.s3.S3Uploader, 테스트는 fake."""

    def put(self, key: str, body: bytes) -> None: ...


def build_object(payload: SpreadsResponse) -> tuple[str, bytes]:
    """한 회차의 (키, gzip 본문) — 순수 계산·결정적 (§3.4).

    키: `spreads/dt=YYYY-MM-DD/hh=HH/YYYYMMDDTHHMMSSZ.jsonl.gz`, 전부 UTC,
    시각은 dataReceivedAt(수집 시각). 한 줄 = 행 18키 + 최상위 `rate`·`dataReceivedAt`·
    `warnings` 를 붙인 21키 — 줄 하나만 읽어도 맥락이 완결된다. `fetchedAt` 은 응답
    시각이지 데이터 시각이 아니라 싣지 않는다.
    """
    assert payload.data_received_at is not None  # 호출자가 수집 여부를 먼저 확인한다
    # dataReceivedAt 은 epoch ms 지만 수집 시각의 정밀도는 초다 (001 계약)
    dt = datetime.fromtimestamp(payload.data_received_at // 1000, tz=UTC)
    key = f"spreads/dt={dt:%Y-%m-%d}/hh={dt:%H}/{dt:%Y%m%dT%H%M%SZ}.jsonl.gz"

    # 같은 객체 안 모든 줄에 같은 최상위 세 값 — camelCase, 값·순서는 API 와 동일
    context = {
        "rate": payload.rate,
        "dataReceivedAt": payload.data_received_at,
        "warnings": payload.warnings,
    }
    lines: list[str] = []
    for row in payload.rows:
        record = camelize_json(row.model_dump())
        assert isinstance(record, dict)
        record.update(context)
        # 키 순서·구분자를 고정해 같은 스냅샷은 바이트까지 같게 만든다 (§3.4 결정적)
        lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    data = ("\n".join(lines) + "\n").encode("utf-8")
    # gzip 헤더의 mtime 이 현재 시각으로 박히면 바이트가 매번 달라진다 — 0 고정
    return key, gzip.compress(data, mtime=0)


class SnapshotLoop:
    """기동 후 먼저 interval 만큼 잔 뒤(직후엔 메모리가 비어 있다) 회차를 반복한다."""

    def __init__(
        self,
        *,
        store: LiveStore,
        collector: Collector,
        s3: ObjectPutter,
        interval: float = SNAPSHOT_INTERVAL,
    ) -> None:
        self._store = store
        self._collector = collector
        self._s3 = s3
        self._interval = interval
        self._consecutive_failures = 0
        # 직전에 올린 객체의 dataReceivedAt — 수집이 멈춘 동안 같은 표를 다시 올리지 않는다.
        # 실패한 회차는 기록하지 않는다(다음 회차에 같은 시각이라도 다시 시도, §3.5).
        self._last_uploaded_at: int | None = None

    async def run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.snapshot_once()
            except Exception:
                # 회차는 예외를 던지지 않는 계약이지만, 버그 하나로 루프가 영구 정지하면 안 된다
                logger.exception("snapshot 회차가 예외로 끝났다")

    async def snapshot_once(self) -> int:
        """한 회차 — 올린 객체 수를 돌려준다(생략·실패는 0)."""
        # 수집이 메모리를 통째 교체하는 도중 읽으면 반쪽이 남는다 — 수집과 같은 락 (§3.5)
        async with self._collector.lock:
            if self._store.received_at is None:
                # 수집이 아직 한 번도 안 돌았다 — 올릴 것이 없다
                return 0
            try:
                payload = build_spreads(self._store)
            except MarketDataNotFoundError as exc:
                logger.warning(
                    "시장 데이터가 없어 이번 회차 snapshot 을 생략한다: %s", exc.message
                )
                return 0
        if payload.data_received_at == self._last_uploaded_at:
            return 0  # 수집이 멈춘 동안 같은 표를 매 분 다시 올리지 않는다 (§3.5)
        key, body = build_object(payload)
        try:
            # 네트워크 I/O 는 락 밖에서 — 동기 SDK 라 스레드로 (§3.5)
            await asyncio.to_thread(self._s3.put, key, body)
        except S3UnavailableError as exc:
            self._consecutive_failures += 1
            logger.error(
                "S3 저장 실패 (연속 %d회): %s", self._consecutive_failures, exc
            )
            return 0
        self._consecutive_failures = 0
        self._last_uploaded_at = payload.data_received_at
        return 1
