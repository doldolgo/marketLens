"""수집 상태 조립 — 메모리(실패 이력·스냅샷)만 읽는 순수 계산 (스펙 011 §3.5)."""

from app.core.config import COLLECT_SOURCES, PERP_SOURCES
from app.core.live_store import LiveStore
from app.core.outages import Outage, OutageTracker
from app.features.health.models import (
    CollectHealthResponse,
    ExchangeHealthOut,
    LastErrorOut,
    OutageOut,
)

# 현물 6곳(001·045) 뒤 perp 원천 4개(046 §3.8·047) 고정 순서
EXCHANGES = COLLECT_SOURCES

# state 경계(ms): 마지막 성공 후 경과 < 5초 ok, < 60초 stale, 그 외 down
STALE_MS = 5_000
DOWN_MS = 60_000

WINDOW_MS = 3_600_000  # successRate1h 창

# 080 §3.2 — 이 길이 이하의 network 구간은 '짧은 재연결' 로 접는다. state 의 stale 경계와 같다
RECONNECT_MAX_MS = 5_000
RECONNECT_KIND = "network"


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


def _short_reconnects(outages: list[Outage], now_ms: int) -> set[int]:
    """짧은 재연결(080 §3.2)인 구간의 id() — network·5초 이하·같은 거래소의 다른 구간과 맞닿지 않음.

    맞닿음 = kind 전환(011 §3.3)으로 잘린 조각. 더 긴 장애의 일부라 접지 않는다.
    """
    starts: dict[str, set[int]] = {}
    ends: dict[str, set[int]] = {}
    for o in outages:
        starts.setdefault(o.exchange, set()).add(o.started_at)
        if o.ended_at is not None:
            ends.setdefault(o.exchange, set()).add(o.ended_at)
    short: set[int] = set()
    for o in outages:
        if o.kind != RECONNECT_KIND:
            continue
        end = o.ended_at if o.ended_at is not None else now_ms
        if end - o.started_at > RECONNECT_MAX_MS:
            continue
        if o.started_at in ends.get(o.exchange, ()):
            continue
        if o.ended_at is not None and o.ended_at in starts.get(o.exchange, ()):
            continue
        short.add(id(o))
    return short


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


def _markets(store: LiveStore, ex: str) -> int:
    """현물은 메모리 행 수, perp 원천은 구독 심볼 수 (046 §3.8)."""
    if ex in PERP_SOURCES:
        state = store.stream_state(ex)
        return state.subscribed if state is not None else 0
    return len(store.get_all(exchange=ex))


def build_collect_health(
    store: LiveStore, tracker: OutageTracker, started_at: int, now_ms: int
) -> CollectHealthResponse:
    all_outages = tracker.outages()  # started_at 내림차순
    short = _short_reconnects(all_outages, now_ms)
    # 목록·openOutage·lastError 는 짧은 재연결을 뺀 구간으로, 성공률은 모든 구간으로 (080 §3.2)
    shown = [o for o in all_outages if id(o) not in short]
    exchanges: list[ExchangeHealthOut] = []
    rates: list[float] = []
    for ex in EXCHANGES:
        rate = _success_rate([o for o in all_outages if o.exchange == ex], now_ms)
        rates.append(rate)
        mine = [o for o in shown if o.exchange == ex]
        open_outage = tracker.open_outage(ex)
        if open_outage is not None and id(open_outage) in short:
            open_outage = None  # 5초 안 network — 지나면 보인다
        latest = mine[0] if mine else None  # 24시간 안의 가장 최근 구간
        exchanges.append(
            ExchangeHealthOut(
                exchange=ex,
                state=_state(tracker.last_success_at(ex), now_ms),
                last_success_at=tracker.last_success_at(ex),
                markets=_markets(store, ex),
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
                reconnects_24h=sum(
                    1
                    for o in all_outages
                    if o.exchange == ex and o.ended_at is not None and id(o) in short
                ),
            )
        )
    return CollectHealthResponse(
        server_started_at=started_at,
        fetched_at=now_ms,
        success_rate_1h=round(sum(rates) / len(rates), 1),
        exchanges=exchanges,
        outages=[_outage_out(o) for o in shown],
    )
