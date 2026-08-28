"""GET /spreads 의 공개 동작 — 스펙 003 §3.2·§4 (네트워크 없음, 저장소 직접 시드)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.live_store import LiveStore
from app.features.spreads.tests.helpers import make_client, make_row

NOW = datetime.now(UTC)

# 스펙 §4: 응답 행 키는 정확히 이 18개다
ROW_KEYS = {
    "sym", "dom", "fx", "fwd", "rev", "usd", "spark", "status", "age",
    "liqDom", "liqFx", "rateAsk", "rateBid", "depDom", "wdDom", "depFx", "wdFx", "netDom",
}  # fmt: skip


def seed_basic(store: LiveStore, *, now: datetime = NOW) -> None:
    """upbit BTC + binance BTC + upbit 환율 — 계산 가능한 최소 시드."""
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
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
                price=71_480.0,
                bids=[[71_450.0, 1.5]],
                asks=[[71_500.0, 2.0]],
            )
        ],
        now,
    )
    store.set_rate("upbit", 1400.0, 1390.0, now)
    store.mark_received(1_787_000_000)


def test_empty_memory_is_404_market_data_not_found() -> None:
    resp = make_client(LiveStore()).get("/spreads")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "market_data_not_found"
    assert body["error"]["detail"] == {"exchange": "upbit"}


def test_snapshots_without_base_rate_is_404() -> None:
    store = LiveStore()
    store.replace_exchange("upbit", [make_row("upbit", "BTC")], NOW)
    store.replace_exchange("binance", [make_row("binance", "BTC")], NOW)
    resp = make_client(store).get("/spreads")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "market_data_not_found"
    assert body["error"]["detail"] == {"exchange": "upbit"}


def test_rate_without_foreign_snapshots_is_404_with_exchange_lists() -> None:
    store = LiveStore()
    store.replace_exchange("upbit", [make_row("upbit", "BTC")], NOW)
    store.set_rate("upbit", 1400.0, 1390.0, NOW)
    resp = make_client(store).get("/spreads")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "market_data_not_found"
    assert body["error"]["detail"] == {"domestic": ["upbit"], "foreign": []}


def test_domestic_exchange_without_rate_is_dropped_entirely() -> None:
    store = LiveStore()
    seed_basic(store)
    # bithumb 은 스냅샷은 있지만 환율이 없다 → bithumb 행 전체가 빠진다
    store.replace_exchange("bithumb", [make_row("bithumb", "BTC")], NOW)
    rows = make_client(store).get("/spreads").json()["rows"]
    assert {r["dom"] for r in rows} == {"upbit"}
    assert [r["sym"] for r in rows] == ["BTC"]


def test_one_side_listing_makes_no_row() -> None:
    store = LiveStore()
    seed_basic(store)
    # ETH 는 국내에만, SOL 은 해외에만 상장 → 둘 다 행 없음
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
            ),
            make_row("upbit", "ETH"),
        ],
        NOW,
    )
    store.replace_exchange(
        "binance",
        [
            make_row(
                "binance",
                "BTC",
                price=71_480.0,
                bids=[[71_450.0, 1.5]],
                asks=[[71_500.0, 2.0]],
            ),
            make_row("binance", "SOL"),
        ],
        NOW,
    )
    rows = make_client(store).get("/spreads").json()["rows"]
    assert [r["sym"] for r in rows] == ["BTC"]


def test_fwd_rev_use_directional_quotes_and_rates() -> None:
    store = LiveStore()
    seed_basic(store)
    body = make_client(store).get("/spreads").json()
    [row] = body["rows"]
    # fwd: 해외 ask 에 사서(환율 ask) 국내 bid 에 판다 / rev: 국내 ask 에 사서 해외 bid 에 판다(환율 bid)
    assert row["fwd"] == pytest.approx((100_000_000.0 / (71_500.0 * 1400.0) - 1) * 100)
    assert row["rev"] == pytest.approx((71_450.0 * 1390.0 / 100_100_000.0 - 1) * 100)
    assert row["rateAsk"] == 1400.0
    assert row["rateBid"] == 1390.0
    assert row["usd"] == 71_480.0
    assert row["status"] == "ok"
    # 최상위 값: rate 는 기준 거래소 환율 ask, data_received_at 은 ms
    assert body["rate"] == 1400.0
    assert body["data_received_at"] == 1_787_000_000_000
    assert body["fetched_at"] > 1_700_000_000_000


def test_equal_ask_bid_rate_matches_single_rate_formula() -> None:
    store = LiveStore()
    seed_basic(store)
    store.set_rate("upbit", 1400.0, 1400.0, NOW)
    [row] = make_client(store).get("/spreads").json()["rows"]
    assert row["fwd"] == pytest.approx((100_000_000.0 / (71_500.0 * 1400.0) - 1) * 100)
    assert row["rev"] == pytest.approx((71_450.0 * 1400.0 / 100_100_000.0 - 1) * 100)


def test_each_domestic_exchange_uses_its_own_rate() -> None:
    store = LiveStore()
    seed_basic(store)
    store.replace_exchange("bithumb", [make_row("bithumb", "BTC")], NOW)
    store.set_rate("bithumb", 1410.0, 1405.0, NOW)
    rows = make_client(store).get("/spreads").json()["rows"]
    by_dom = {r["dom"]: r for r in rows}
    assert by_dom["upbit"]["rateAsk"] == 1400.0
    assert by_dom["upbit"]["rateBid"] == 1390.0
    assert by_dom["bithumb"]["rateAsk"] == 1410.0
    assert by_dom["bithumb"]["rateBid"] == 1405.0


def test_empty_orderbook_is_fail_with_zero_numbers_and_kept_io() -> None:
    store = LiveStore()
    seed_basic(store)
    store.replace_exchange(
        "binance",
        [
            make_row(
                "binance", "BTC", asks=[], bids=[[71_450.0, 1.5]], dep=True, wd=False
            )
        ],
        NOW,
    )
    [row] = make_client(store).get("/spreads").json()["rows"]
    assert row["status"] == "fail"
    for key in ("fwd", "rev", "usd", "liqDom", "liqFx", "rateAsk", "rateBid"):
        assert row[key] == 0
    # fail 이어도 입출금 값과 age 는 싣는다
    assert row["depFx"] is True
    assert row["wdFx"] is False
    assert row["age"] >= 0


def test_stale_ok_and_age_follow_older_snapshot() -> None:
    store = LiveStore()
    now = datetime.now(UTC)
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
            )
        ],
        now - timedelta(seconds=6),
    )
    store.replace_exchange(
        "binance",
        [
            make_row(
                "binance",
                "BTC",
                price=71_480.0,
                bids=[[71_450.0, 1.5]],
                asks=[[71_500.0, 2.0]],
            )
        ],
        now - timedelta(seconds=0.5),
    )
    store.set_rate("upbit", 1400.0, 1390.0, now)
    [row] = make_client(store).get("/spreads").json()["rows"]
    # age 는 양측 중 오래된 쪽(6초) 기준 → stale
    assert row["status"] == "stale"
    assert 5.9 <= row["age"] <= 8.0

    # 양쪽 다 0.5초 전이면 ok
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
            )
        ],
        datetime.now(UTC) - timedelta(seconds=0.5),
    )
    [row] = make_client(store).get("/spreads").json()["rows"]
    assert row["status"] == "ok"
    assert 0.4 <= row["age"] <= 2.0


def test_liq_is_min_side_notional_over_rate_ask() -> None:
    store = LiveStore()
    seed_basic(store)
    [row] = make_client(store).get("/spreads").json()["rows"]
    # 국내: min(1억×0.5, 1.001억×0.4)/1400, 해외: min(71450×1.5, 71500×2.0)
    assert row["liqDom"] == pytest.approx(
        min(100_000_000.0 * 0.5, 100_100_000.0 * 0.4) / 1400.0
    )
    assert row["liqFx"] == pytest.approx(min(71_450.0 * 1.5, 71_500.0 * 2.0))


def test_rows_sorted_by_sym_dom_fx() -> None:
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [make_row("upbit", "ETH"), make_row("upbit", "BTC"), make_row("upbit", "ADA")],
        NOW,
    )
    store.replace_exchange(
        "bithumb", [make_row("bithumb", "BTC"), make_row("bithumb", "ADA")], NOW
    )
    store.replace_exchange(
        "binance",
        [
            make_row("binance", "ADA"),
            make_row("binance", "BTC"),
            make_row("binance", "ETH"),
        ],
        NOW,
    )
    store.set_rate("upbit", 1400.0, 1390.0, NOW)
    store.set_rate("bithumb", 1410.0, 1405.0, NOW)
    rows = make_client(store).get("/spreads").json()["rows"]
    keys = [(r["sym"], r["dom"], r["fx"]) for r in rows]
    assert keys == sorted(keys)
    assert keys == [
        ("ADA", "bithumb", "binance"),
        ("ADA", "upbit", "binance"),
        ("BTC", "bithumb", "binance"),
        ("BTC", "upbit", "binance"),
        ("ETH", "upbit", "binance"),
    ]


def test_row_keys_are_exactly_the_18_camel_case_keys() -> None:
    store = LiveStore()
    seed_basic(store)
    [row] = make_client(store).get("/spreads").json()["rows"]
    assert set(row) == ROW_KEYS
    assert row["spark"] == []
    assert row["netDom"] is None
    # 입출금 4개 값은 수집기가 준 코인 단위 값 그대로 — 001 은 전부 null 을 준다
    assert row["depDom"] is None and row["wdDom"] is None
    assert row["depFx"] is None and row["wdFx"] is None


# ---- 리뷰 결함 회귀: 국내 호가 가격 0 은 500 이 아니라 그 행 fail (003 §3.2-4 방어) ----


def test_zero_domestic_ask_price_fails_row_not_500():
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [
            make_row("upbit", "BTC", asks=[[0.0, 1.0]], bids=[[99_000_000.0, 1.0]]),
            make_row(
                "upbit", "ETH", bids=[[5_000_000.0, 1.0]], asks=[[5_010_000.0, 1.0]]
            ),
        ],
        NOW,
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
            ),
            make_row(
                "binance",
                "ETH",
                price=3_550.0,
                bids=[[3_549.0, 1.0]],
                asks=[[3_551.0, 1.0]],
            ),
        ],
        NOW,
    )
    store.set_rate("upbit", 1400.0, 1390.0, NOW)
    store.mark_received(1_787_000_000)
    res = make_client(store).get("/spreads")
    assert res.status_code == 200
    rows = {r["sym"]: r for r in res.json()["rows"]}
    assert rows["BTC"]["status"] == "fail" and rows["BTC"]["rev"] == 0
    assert rows["ETH"]["status"] in ("ok", "stale")
