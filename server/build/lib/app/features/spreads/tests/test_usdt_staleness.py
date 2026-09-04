"""`/spreads` 의 USDT 시세 미갱신 경고 — 스펙 008 §4 (네트워크 없음, 저장소 직접 시드)."""

from datetime import UTC, datetime, timedelta

from app.core.live_store import LiveStore
from app.features.spreads.tests.helpers import make_client, make_row


def seed_pair(store: LiveStore, now: datetime) -> None:
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 1.0]], asks=[[100_100_000.0, 1.0]]
            )
        ],
        now,
    )
    store.replace_exchange(
        "binance",
        [
            make_row(
                "binance",
                "BTC",
                price=71_000.0,
                bids=[[70_990.0, 1.0]],
                asks=[[71_010.0, 1.0]],
            )
        ],
        now,
    )
    store.mark_received(1_787_000_000)


def test_stale_rate_emits_warning_with_seconds() -> None:
    now = datetime.now(UTC)
    store = LiveStore()
    seed_pair(store, now)
    store.set_rate("upbit", 1400.0, 1390.0, now - timedelta(seconds=61))
    body = make_client(store).get("/spreads").json()
    assert len(body["warnings"]) == 1
    w = body["warnings"][0]
    assert w.startswith("upbit USDT 시세가")
    assert "낡은 시세 기준" in w
    seconds = int(w.split("시세가 ")[1].split("초째")[0])
    assert seconds >= 61
    assert "bithumb" not in w  # 시세를 시드하지 않은 거래소는 경고에 없다


def test_fresh_rate_has_no_warning() -> None:
    now = datetime.now(UTC)
    store = LiveStore()
    seed_pair(store, now)
    store.set_rate("upbit", 1400.0, 1390.0, now - timedelta(seconds=59))
    body = make_client(store).get("/spreads").json()
    assert body["warnings"] == []


def test_two_stale_exchanges_sorted_by_id() -> None:
    now = datetime.now(UTC)
    store = LiveStore()
    seed_pair(store, now)
    store.set_rate("upbit", 1400.0, 1390.0, now - timedelta(seconds=61))
    store.set_rate("bithumb", 1401.0, 1391.0, now - timedelta(seconds=90))
    body = make_client(store).get("/spreads").json()
    assert len(body["warnings"]) == 2
    assert body["warnings"][0].startswith("bithumb ")
    assert body["warnings"][1].startswith("upbit ")


def test_warnings_key_always_present_and_top_keys_stable() -> None:
    now = datetime.now(UTC)
    store = LiveStore()
    seed_pair(store, now)
    store.set_rate("upbit", 1400.0, 1390.0, now)
    body = make_client(store).get("/spreads").json()
    assert body["warnings"] == []
    assert {"rate", "rows", "dataReceivedAt", "warnings", "fetchedAt"} <= set(body)
