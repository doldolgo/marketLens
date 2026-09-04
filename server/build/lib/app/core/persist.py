"""persist 루프 — 60초마다 메모리 김프를 InfluxDB `premium` 에 한 점씩 (스펙 005 §3.3).

앱 기동(main.py lifespan)이 이 루프를 관리한다. 수집·조회는 저장소 장애에 볼모 잡히지
않는다 — 실패는 로그 후 다음 회차 재시도, 놓친 회차는 구멍으로 남는다(소급 안 함).
"""

import asyncio
import logging
from typing import Protocol

from app.core.collector import Collector
from app.core.influx import (
    InfluxPoint,
    InfluxUnavailableError,
    dw_fail_point,
    premium_point,
)
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.premium import premium_percent

logger = logging.getLogger("marketlens.persist")

# 저장 주기(초)는 코드 상수다 (스펙 005 §3.1)
PERSIST_INTERVAL = 60.0

# 페어 분류 기준은 003 과 같다 — KRW 호가면 국내, USDT 호가면 해외
_DOMESTIC_QUOTE = "KRW"
_FOREIGN_QUOTE = "USDT"


class PointWriter(Protocol):
    """저장 루프가 쓰는 최소 인터페이스 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def write(self, points: list[InfluxPoint]) -> None: ...


def build_points(
    store: LiveStore, dw_failed: list[str]
) -> tuple[list[InfluxPoint], str | None]:
    """한 회차의 점 목록 — 순수 계산. (점들, 생략 사유) 를 돌려준다.

    김프 점 규칙(§3.3): base 마다 (국내 × 해외) 조합. 국내 행은 호가 + 그 거래소의
    환율 필수(남의 환율을 빌리지 않는다), 해외 행도 호가 필수. 여섯 값 중 하나라도
    ≤ 0 이면 그 점은 건너뛴다.
    """
    ts = store.received_at
    if ts is None:
        # 수집이 아직 한 번도 안 돌았다 — 쓸 것이 없다
        return [], None

    # 환율이 하나도 없으면 이번 회차 생략 (경고 로그는 호출자 몫)
    rates = {ex: r for ex, r in store.rates().items() if r.ask > 0 and r.bid > 0}
    if not rates:
        return [], "환율이 하나도 없어 이번 회차 저장을 생략한다"

    domestic: dict[str, dict[str, Row]] = {}
    foreign: dict[str, dict[str, Row]] = {}
    for row in store.get_all():
        if row.quote == _DOMESTIC_QUOTE:
            domestic.setdefault(row.exchange, {})[row.base.upper()] = row
        elif row.quote == _FOREIGN_QUOTE:
            foreign.setdefault(row.exchange, {})[row.base.upper()] = row

    points: list[InfluxPoint] = []
    for dom_ex, dom_table in sorted(domestic.items()):
        rate = rates.get(dom_ex)
        if rate is None:
            continue  # 환율 없는 국내 거래소는 이 회차에서 빠진다
        for fx_ex, fx_table in sorted(foreign.items()):
            if fx_ex == dom_ex:
                continue
            for base in sorted(dom_table.keys() & fx_table.keys()):
                dom_row = dom_table[base]
                fx_row = fx_table[base]
                dom_bid = dom_row.bids[0][0] if dom_row.bids else 0.0
                dom_ask = dom_row.asks[0][0] if dom_row.asks else 0.0
                fx_bid = fx_row.bids[0][0] if fx_row.bids else 0.0
                fx_ask = fx_row.asks[0][0] if fx_row.asks else 0.0
                six = (dom_bid, dom_ask, fx_bid, fx_ask, rate.ask, rate.bid)
                if any(v <= 0 for v in six):
                    continue
                # /spreads 와 같은 식 — core.premium 재정의 금지 (§3.3)
                fwd = premium_percent(buy_krw=fx_ask * rate.ask, sell_krw=dom_bid)
                rev = premium_percent(buy_krw=dom_ask, sell_krw=fx_bid * rate.bid)
                points.append(
                    premium_point(
                        dom=dom_ex, fx=fx_ex, base=base, ts=ts, fwd=fwd, rev=rev
                    )
                )

    for exchange in dw_failed:
        points.append(dw_fail_point(exchange=exchange, ts=ts))
    return points, None


class PersistLoop:
    """기동 후 먼저 interval 만큼 잔 뒤(직후엔 메모리가 비어 쓸 것이 없다) 회차를 반복한다."""

    def __init__(
        self,
        *,
        store: LiveStore,
        collector: Collector,
        influx: PointWriter,
        interval: float = PERSIST_INTERVAL,
    ) -> None:
        self._store = store
        self._collector = collector
        self._influx = influx
        self._interval = interval
        self._consecutive_failures = 0

    async def run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.persist_once()
            except Exception:
                # 회차는 예외를 던지지 않는 계약이지만, 버그 하나로 저장이 영구 중단되는 것을 막는다
                logger.exception("persist 회차가 예외로 끝났다")

    async def persist_once(self) -> int:
        """한 회차 — 쓴 점 수를 돌려준다(생략·실패는 0)."""
        # 수집이 메모리를 통째 교체하는 도중 읽으면 반쪽이 남는다 — 수집과 같은 락 (§3.3)
        async with self._collector.lock:
            points, skip_reason = build_points(self._store, self._collector.dw_failed)
        if skip_reason is not None:
            logger.warning(skip_reason)
            return 0
        if not points:
            return 0
        try:
            # 한 회차의 점은 쓰기 1번 — 전부 성공 또는 전부 없음. 동기 클라이언트라 스레드로.
            await asyncio.to_thread(self._influx.write, points)
        except InfluxUnavailableError as exc:
            self._consecutive_failures += 1
            logger.error(
                "DB 저장 실패 (연속 %d회): %s", self._consecutive_failures, exc
            )
            return 0
        self._consecutive_failures = 0
        return len(points)
