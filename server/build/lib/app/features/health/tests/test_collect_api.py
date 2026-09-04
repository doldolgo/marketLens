"""GET /health/collect — 순서·state 경계·성공률·정렬·불변 계약 (스펙 011 §3.5, §4). 네트워크 없음."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.outages import OutageTracker
from app.features.health.service import build_collect_health
from app.main import create_app

T0 = 1_756_900_000_000  # 기동 시각(epoch ms)
SEC = 1_000
HOUR = 3_600 * SEC


def row(exchange: str, base: str) -> Row:
    quote = "USDT" if exchange == "binance" else "KRW"
    return Row(
        exchange=exchange,
        base=base,
        quote=quote,
        native_symbol=f"{base}-{quote}",
        price=1.0,
        asks=[[1.0, 1.0]],
        bids=[[1.0, 1.0]],
        price_timestamp=T0,
    )


def fail(
    t: OutageTracker,
    ex: str,
    at: int,
    kind: str = "timeout",
    status: int | None = None,
    retry: int | None = None,
) -> None:
    t.record_failure(
        ex,
        at,
        kind=kind,
        message=f"{ex} 실패",
        status_code=status,
        url="https://x/y",
        retry_after_sec=retry,
    )


def close(t: OutageTracker, ex: str, first_success: int) -> None:
    for i in range(3):
        t.record_success(ex, first_success + i * SEC)


def make_client(
    store: LiveStore,
    tracker: OutageTracker,
    now_ms: int,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """lifespan 없이 상태를 직접 채운다 — 수집 루프·네트워크·Influx 없음."""
    app = create_app()
    app.state.live_store = store
    app.state.outages = tracker
    app.state.started_at = T0
    app.state.settings = SimpleNamespace(refresh_token=None)
    monkeypatch.setattr("app.features.health.router.time.time", lambda: now_ms / 1000)
    return TestClient(app)


def test_response_shape_fixed_exchange_order_and_open_outage_in_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LiveStore()
    now = datetime.now(UTC)
    store.replace_exchange("upbit", [row("upbit", "BTC"), row("upbit", "ETH")], now)
    store.replace_exchange("binance", [row("binance", "BTC")], now)
    t = OutageTracker()
    now_ms = T0 + 100 * SEC
    for ex in ("upbit", "bithumb"):
        t.record_success(ex, now_ms - SEC)
    fail(t, "binance", now_ms - 12 * SEC, kind="rate_limit", status=429, retry=10)
    fail(t, "binance", now_ms - SEC, kind="rate_limit", status=429, retry=10)

    body = make_client(store, t, now_ms, monkeypatch).get("/health/collect").json()
    assert body["serverStartedAt"] == T0 and body["fetchedAt"] == now_ms
    assert [e["exchange"] for e in body["exchanges"]] == ["upbit", "bithumb", "binance"]
    up, bt, bn = body["exchanges"]
    assert up["successRate1h"] == 100.0 and bn["successRate1h"] == 99.7
    assert body["successRate1h"] == 99.9  # (100 + 100 + 99.7) / 3
    assert (up["state"], up["markets"], up["openOutage"], up["lastError"]) == (
        "ok",
        2,
        None,
        None,
    )
    assert bt["markets"] == 0
    assert bn["state"] == "down"  # 성공 0회
    assert bn["openOutage"]["count"] == 2 and bn["openOutage"]["endedAt"] is None
    assert bn["openOutage"]["retryAfterSec"] == 10
    assert bn["lastError"] == {
        "at": now_ms - SEC,
        "kind": "rate_limit",
        "statusCode": 429,
        "message": "binance 실패",
    }
    assert body["outages"] == [bn["openOutage"]]
    assert set(body["outages"][0]) == {
        "exchange",
        "kind",
        "startedAt",
        "endedAt",
        "lastFailedAt",
        "count",
        "statusCode",
        "message",
        "url",
        "retryAfterSec",
    }


@pytest.mark.parametrize(
    ("elapsed_ms", "state"),
    [(4_900, "ok"), (5_000, "stale"), (59_999, "stale"), (60_000, "down")],
)
def test_state_boundaries(elapsed_ms: int, state: str) -> None:
    t = OutageTracker()
    now_ms = T0 + HOUR
    t.record_success("upbit", now_ms - elapsed_ms)
    out = build_collect_health(LiveStore(), t, T0, now_ms)
    assert out.exchanges[0].state == state


def test_zero_success_is_down() -> None:
    out = build_collect_health(LiveStore(), OutageTracker(), T0, T0 + SEC)
    assert [e.state for e in out.exchanges] == ["down", "down", "down"]
    assert out.success_rate_1h == 100.0 and out.outages == []


def test_success_rate_counts_only_overlap_with_1h_window() -> None:
    t = OutageTracker()
    now_ms = T0 + 10 * HOUR
    # 창 밖(2시간 전, 60초) — 무시
    fail(t, "upbit", now_ms - 2 * HOUR)
    close(t, "upbit", now_ms - 2 * HOUR + 60 * SEC)
    # 창 경계에 걸침: 70분 전 시작, 50분 전 종료 → 겹침 10분 = 600초
    fail(t, "upbit", now_ms - 70 * 60 * SEC)
    close(t, "upbit", now_ms - 50 * 60 * SEC)
    # 진행 중: 36초 전 시작 → now 까지 36초
    fail(t, "bithumb", now_ms - 36 * SEC)
    out = build_collect_health(LiveStore(), t, T0, now_ms)
    up, bt, bn = out.exchanges
    assert up.success_rate_1h == round((1 - 600 / 3600) * 100, 1)  # 83.3
    assert bt.success_rate_1h == 99.0
    assert bn.success_rate_1h == 100.0
    assert out.success_rate_1h == round((up.success_rate_1h + 99.0 + 100.0) / 3, 1)


def test_outages_sorted_by_started_at_desc_and_last_error_is_latest() -> None:
    t = OutageTracker()
    now_ms = T0 + HOUR
    fail(t, "upbit", now_ms - 300 * SEC, kind="timeout")
    close(t, "upbit", now_ms - 290 * SEC)
    fail(t, "upbit", now_ms - 100 * SEC, kind="unavailable", status=503)
    close(t, "upbit", now_ms - 90 * SEC)
    fail(t, "binance", now_ms - 200 * SEC, kind="network")
    close(t, "binance", now_ms - 199 * SEC)
    out = build_collect_health(LiveStore(), t, T0, now_ms)
    assert [(o.exchange, o.kind) for o in out.outages] == [
        ("upbit", "unavailable"),
        ("binance", "network"),
        ("upbit", "timeout"),
    ]
    up = out.exchanges[0]
    assert up.open_outage is None
    assert up.last_error is not None and (
        up.last_error.kind,
        up.last_error.status_code,
    ) == ("unavailable", 503)
    assert up.last_error.at == now_ms - 100 * SEC


def test_health_contract_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(LiveStore(), OutageTracker(), T0, monkeypatch)
    assert client.get("/health").json() == {"status": "ok", "version": "0.1.0"}
