"""수집 상태 조립 — 메모리(실패 이력·스냅샷)만 읽는 순수 계산 (스펙 011 §3.5)."""

from app.core.live_store import LiveStore
from app.core.outages import Outage, OutageTracker
from app.features.health.models import (
    CollectHealthResponse,
    ExchangeHealthOut,
    LastErrorOut,
    OutageOut,
)

# 001 의 거래소 3곳 고정 순서
EXCHANGES = ("upbit", "bithumb", "binance")

# state 경계(ms): 마지막 성공 후 경과 < 5초 ok, < 60초 stale, 그 외 down
STALE_MS = 5_000
DOWN_MS = 60_000

WINDOW_MS = 3_600_000  # successRate1h 창


def _state(last_success_at: int | None, now_ms: int) -> str:
    if last_success_at is None:
        return "down"  # 기동 후 성공 0회
    elapsed = now_ms - last_success_at
    if elapsed < STALE_MS:
        return "ok"
    if elapsed < DOWN_MS:
        return "stale"
    return "down"


def _success_rate(outages: list[Outage], now_ms: int) -> float:
    """(1 − 최근 3600초 창과 겹친 초 합 / 3600) × 100, 소수 1자리. 진행 중 구간은 now 까지."""
    window_start = now_ms - WINDOW_MS
    overlap_ms = 0
    for o in outages:
        end = o.ended_at if o.ended_at is not None else now_ms
        lo = max(o.started_at, window_start)
        hi = min(end, now_ms)
        if hi > lo:
            overlap_ms += hi - lo
    return round((1 - overlap_ms / WINDOW_MS) * 100, 1)


def _outage_out(o: Outage) -> OutageOut:
    return OutageOut(
        exchange=o.exchange,
        kind=o.kind,
        started_at=o.started_at,
        ended_at=o.ended_at,
        last_failed_at=o.last_failed_at,
        count=o.count,
        status_code=o.status_code,
        message=o.message,
        url=o.url,
        retry_after_sec=o.retry_after_sec,
    )


def build_collect_health(
    store: LiveStore, tracker: OutageTracker, started_at: int, now_ms: int
) -> CollectHealthResponse:
    all_outages = tracker.outages()  # started_at 내림차순
    exchanges: list[ExchangeHealthOut] = []
    rates: list[float] = []
    for ex in EXCHANGES:
        mine = [o for o in all_outages if o.exchange == ex]
        rate = _success_rate(mine, now_ms)
        rates.append(rate)
        open_outage = tracker.open_outage(ex)
        latest = mine[0] if mine else None  # 24시간 안의 가장 최근 구간
        exchanges.append(
            ExchangeHealthOut(
                exchange=ex,
                state=_state(tracker.last_success_at(ex), now_ms),
                last_success_at=tracker.last_success_at(ex),
                markets=len(store.get_all(exchange=ex)),
                success_rate_1h=rate,
                open_outage=_outage_out(open_outage) if open_outage else None,
                last_error=LastErrorOut(
                    at=latest.last_failed_at,
                    kind=latest.kind,
                    status_code=latest.status_code,
                    message=latest.message,
                )
                if latest
                else None,
            )
        )
    return CollectHealthResponse(
        server_started_at=started_at,
        fetched_at=now_ms,
        success_rate_1h=round(sum(rates) / len(rates), 1),
        exchanges=exchanges,
        outages=[_outage_out(o) for o in all_outages],
    )
