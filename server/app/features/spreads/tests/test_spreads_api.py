"""GET /spreads 의 공개 동작 — HTTP 서빙 계약은 스펙 018 §3.1·§4, 표 계산 규칙은 003 §3.2·§4.

018 부터 HTTP 는 Redis 키 `spreads:latest` 를 그대로 답하므로(메모리를 읽지 않는다) 계산 규칙은
`spreads_json`(게시기와 같은 함수·직렬화)으로 본다. 네트워크 없음, 저장소 직접 시드, Redis 는 fakeredis.
"""

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from app.core.live_store import LiveStore
from app.core.redis_bus import RedisBus
from app.features.spreads.tests.helpers import (
    make_bus,
    make_client,
    make_row,
    seed_rows,
    spreads_json,
)


def _now() -> datetime:
    """호출 시점의 시계 — import 시각을 상수로 잡으면 느린 CI 에서 수집 뒤 실행까지 STALE_SEC 를 넘겨 행이 낡은 것으로 판정된다."""
    return datetime.now(UTC)


# 스펙 §4: 응답 행 키는 정확히 이 17개다
ROW_KEYS = {
    "sym", "dom", "fx", "fwd", "rev", "usd", "spark", "status", "age",
    "slipFwd", "slipRev", "krw", "netDom", "depDom", "wdDom", "depFx", "wdFx",
}  # fmt: skip

# 최상위 6키 (§4)
TOP_KEYS = {"rate", "notional", "rows", "warnings", "dataReceivedAt", "fetchedAt"}


def seed_basic(store: LiveStore, *, now: datetime | None = None) -> None:
    """upbit BTC + binance BTC + upbit 환율 — 계산 가능한 최소 시드."""
    now = now if now is not None else _now()
    seed_rows(
        store,
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
            )
        ],
        now,
    )
    seed_rows(
        store,
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


# ---- 018 §3.1·§4: HTTP 는 Redis 키를 그대로 답한다 — 메모리를 읽지 않는다 ----

# 게시기가 넣는 것과 같은 모양의 표 한 장 — 바이트 비교용이라 공백 하나도 의미가 있다
TABLE = (
    '{"rate":1400.0,"notional":1000.0,"rows":[],"warnings":[],'
    '"dataReceivedAt":1787000000,"fetchedAt":1787000000000}'
)


def make_serving_client() -> tuple[object, fakeredis.FakeRedis]:
    """GET /spreads 클라이언트 + 같은 fakeredis 서버를 보는 동기 클라이언트(키 시드·TTL 확인용)."""
    bus, server = make_bus()
    return make_client(LiveStore(), bus=bus), fakeredis.FakeRedis(server=server)


def test_get_returns_latest_key_bytes_verbatim_and_writes_want_for_15s() -> None:
    client, redis = make_serving_client()
    redis.set("spreads:latest", TABLE, ex=10)
    resp = client.get("/spreads")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert (
        resp.content == TABLE.encode()
    )  # 파싱·재직렬화 없음 — 수집이 만든 바이트가 곧 응답
    assert redis.ttl("spreads:want") == 15


def test_missing_key_is_404_and_still_writes_want() -> None:
    client, redis = make_serving_client()
    resp = client.get("/spreads")
    assert resp.status_code == 404
    body = resp.json()["error"]
    assert body["code"] == "market_data_not_found"
    assert body["detail"] == {"key": "spreads:latest"}
    # 첫 폴링은 404 여도 want 는 쓴다 — 수집이 5초 안에 표를 만들기 시작하게 (§3.1)
    assert redis.ttl("spreads:want") == 15


def test_memory_rows_do_not_matter_when_key_is_missing() -> None:
    store = LiveStore()
    seed_basic(store)
    assert spreads_json(store)["rows"]  # 메모리엔 표를 만들 재료가 있지만
    assert (
        make_client(store).get("/spreads").status_code == 404
    )  # HTTP 는 Redis 만 본다


def test_redis_down_is_503_and_want_is_not_written() -> None:
    bus, server = make_bus()
    client = make_client(LiveStore(), bus=bus)
    server.connected = False
    resp = client.get("/spreads")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "redis_unavailable"
    server.connected = True
    assert (
        fakeredis.FakeRedis(server=server).ttl("spreads:want") == -2
    )  # 키 자체가 없다


@pytest.mark.parametrize("value", ["1000", "500000", "abc", ""])
def test_any_notional_query_is_400_notional_fixed(value: str) -> None:
    client, redis = make_serving_client()
    redis.set("spreads:latest", TABLE, ex=10)
    resp = client.get(f"/spreads?notional={value}")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "notional_fixed"


def test_other_queries_are_ignored() -> None:
    client, redis = make_serving_client()
    redis.set("spreads:latest", TABLE, ex=10)
    assert client.get("/spreads?foo=1").status_code == 200


def test_gzip_is_applied_to_the_verbatim_body() -> None:
    bus, server = make_bus()
    client = make_client(
        LiveStore(), bus=RedisBus(fakeredis.aioredis.FakeRedis(server=server))
    )
    big = TABLE.replace('"rows":[]', '"rows":[' + ",".join(["{}"] * 300) + "]")
    fakeredis.FakeRedis(server=server).set("spreads:latest", big, ex=10)
    resp = client.get("/spreads", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.content == big.encode()  # httpx 가 풀어 준 본문은 키의 바이트 그대로


# ---- 003 §3.2·§4: 표 계산 규칙 — 게시기가 만드는 표(= GET /spreads 본문)의 내용 ----


def test_domestic_exchange_without_rate_is_dropped_entirely() -> None:
    store = LiveStore()
    seed_basic(store)
    # bithumb 은 스냅샷은 있지만 환율이 없다 → bithumb 행 전체가 빠진다
    seed_rows(store, [make_row("bithumb", "BTC")], _now())
    rows = spreads_json(store)["rows"]
    assert {r["dom"] for r in rows} == {"upbit"}
    assert [r["sym"] for r in rows] == ["BTC"]


def test_one_side_listing_makes_no_row() -> None:
    store = LiveStore()
    seed_basic(store)
    # ETH 는 국내에만, SOL 은 해외에만 상장 → 둘 다 행 없음
    seed_rows(
        store,
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
            ),
            make_row("upbit", "ETH"),
        ],
        _now(),
    )
    seed_rows(
        store,
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
        _now(),
    )
    rows = spreads_json(store)["rows"]
    assert [r["sym"] for r in rows] == ["BTC"]


def test_fwd_rev_use_directional_quotes_and_rates() -> None:
    store = LiveStore()
    seed_basic(store)
    body = spreads_json(store)
    [row] = body["rows"]
    # 규모가 1단계 안에서 끝나는 시드라 순값 = 원값이다 — 원값 수식이 그대로 드러난다.
    # fwd: 해외 ask 에 사서(환율 ask) 국내 bid 에 판다 / rev: 국내 ask 에 사서 해외 bid 에 판다(환율 bid)
    assert row["fwd"] == pytest.approx((100_000_000.0 / (71_500.0 * 1400.0) - 1) * 100)
    assert row["rev"] == pytest.approx((71_450.0 * 1390.0 / 100_100_000.0 - 1) * 100)
    assert row["slipFwd"] == 0.0
    assert row["slipRev"] == 0.0
    assert row["usd"] == 71_480.0
    assert row["status"] == "ok"
    # 최상위 값: rate 는 기준 거래소 환율 ask, dataReceivedAt 은 ms
    assert set(body) == TOP_KEYS
    assert body["rate"] == 1400.0
    assert body["notional"] == 1000.0
    assert body["dataReceivedAt"] == 1_787_000_000_000
    assert body["fetchedAt"] > 1_700_000_000_000


def test_equal_ask_bid_rate_matches_single_rate_formula() -> None:
    store = LiveStore()
    seed_basic(store)
    store.set_rate("upbit", 1400.0, 1400.0, _now())
    [row] = spreads_json(store)["rows"]
    assert row["fwd"] == pytest.approx((100_000_000.0 / (71_500.0 * 1400.0) - 1) * 100)
    assert row["rev"] == pytest.approx((71_450.0 * 1400.0 / 100_100_000.0 - 1) * 100)


def test_each_domestic_exchange_uses_its_own_rate() -> None:
    store = LiveStore()
    seed_basic(store)
    seed_rows(store, [make_row("bithumb", "BTC")], _now())
    store.set_rate("bithumb", 1410.0, 1405.0, _now())
    rows = spreads_json(store)["rows"]
    by_dom = {r["dom"]: r for r in rows}
    # 환율은 응답에 없다 — 각 행의 krw(그 거래소 최우선 매수호가)와 순값으로 확인한다
    assert by_dom["upbit"]["krw"] == 100_000_000.0
    assert by_dom["bithumb"]["krw"] == 99.0
    # 1단계 안에서 끝나는 시드라 순값 = 원값이고, 각 행이 자기 거래소 환율만 쓴다
    assert by_dom["upbit"]["fwd"] == pytest.approx(
        (100_000_000.0 / (71_500.0 * 1400.0) - 1) * 100
    )
    assert by_dom["bithumb"]["fwd"] == pytest.approx(
        (99.0 / (71_500.0 * 1410.0) - 1) * 100
    )


def test_empty_orderbook_is_fail_with_zero_numbers_and_kept_io() -> None:
    store = LiveStore()
    seed_basic(store)
    store.remove_row("binance", "BTC")  # 교체하면 입출금 3필드를 물려받는다 (001 §3.3)
    seed_rows(
        store,
        [
            make_row(
                "binance", "BTC", asks=[], bids=[[71_450.0, 1.5]], dep=True, wd=False
            )
        ],
        _now(),
    )
    [row] = spreads_json(store)["rows"]
    assert row["status"] == "fail"
    for key in ("fwd", "rev", "usd", "slipFwd", "slipRev", "krw"):
        assert row[key] == 0
    # fail 이어도 입출금 값과 age 는 싣는다
    assert row["depFx"] is True
    assert row["wdFx"] is False
    assert row["age"] >= 0


def test_zero_size_best_quote_is_fail() -> None:
    # 잔량 0 은 걷어도 체결 수량이 0 이라 평균가가 0 이 된다 — 가격 0 과 같이 fail (§3.2-4)
    store = LiveStore()
    seed_basic(store)
    store.remove_row("binance", "BTC")  # 교체하면 입출금 3필드를 물려받는다 (001 §3.3)
    seed_rows(
        store,
        [
            make_row(
                "binance",
                "BTC",
                price=71_480.0,
                bids=[[71_450.0, 1.5]],
                asks=[[71_500.0, 0.0]],
                dep=True,
            )
        ],
        _now(),
    )
    [row] = spreads_json(store)["rows"]
    assert row["status"] == "fail"
    assert row["fwd"] == 0 and row["slipFwd"] == 0 and row["krw"] == 0
    assert row["depFx"] is True  # fail 이어도 입출금 값은 싣는다


def test_stale_ok_and_age_follow_older_stream_not_row() -> None:
    store = LiveStore()
    now = datetime.now(UTC)
    # 행 자체는 299초 전 — 조용한 코인. 행 자체 규칙(300초)의 바로 아래라 스트림 기준 그대로다
    old = now - timedelta(seconds=299)
    seed_rows(
        store,
        [
            make_row(
                "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
            )
        ],
        old,
    )
    seed_rows(
        store,
        [
            make_row(
                "binance",
                "BTC",
                price=71_480.0,
                bids=[[71_450.0, 1.5]],
                asks=[[71_500.0, 2.0]],
            )
        ],
        old,
    )
    store.set_rate("upbit", 1400.0, 1390.0, now)
    ms = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731
    store.stream("upbit").last_message_at = ms(now - timedelta(seconds=6))
    store.stream("binance").last_message_at = ms(now - timedelta(seconds=0.5))
    [row] = spreads_json(store)["rows"]
    # age 는 양측 스트림 중 오래된 쪽(6초) 기준 → stale
    assert row["status"] == "stale"
    assert 5.9 <= row["age"] <= 8.0

    # 스트림이 둘 다 0.5초 전이면 행 갱신 시각이 299초 전이어도 ok
    store.stream("upbit").last_message_at = ms(
        datetime.now(UTC) - timedelta(seconds=0.5)
    )
    [row] = spreads_json(store)["rows"]
    assert row["status"] == "ok"
    assert 0.4 <= row["age"] <= 2.0


@pytest.mark.parametrize("silent_exchange", ["upbit", "binance"])
def test_row_unchanged_for_300s_is_stale_even_with_live_stream(
    silent_exchange: str,
) -> None:
    """행 자체 미갱신 300초 — 거래 정지·심볼 장애 (§3.2-4 예외, §4).

    스트림은 살아 있는데(0.5초 전 수신) 그 코인 행만 301초 전이면 age 는 그 행의
    실제 경과 초(≥300)라 stale 이다. 국내 행·해외 행 어느 쪽이든 같고, 다른 코인 행은 ok.
    """
    store = LiveStore()
    now = datetime.now(UTC)
    silent_at = now - timedelta(seconds=301)
    upbit_rows = {
        "BTC": make_row(
            "upbit", "BTC", bids=[[100_000_000.0, 0.5]], asks=[[100_100_000.0, 0.4]]
        ),
        "ETH": make_row(
            "upbit", "ETH", bids=[[5_000_000.0, 1.0]], asks=[[5_010_000.0, 1.0]]
        ),
    }
    binance_rows = {
        "BTC": make_row(
            "binance",
            "BTC",
            price=71_480.0,
            bids=[[71_450.0, 1.5]],
            asks=[[71_500.0, 2.0]],
        ),
        "ETH": make_row(
            "binance",
            "ETH",
            price=3_550.0,
            bids=[[3_549.0, 1.0]],
            asks=[[3_551.0, 1.0]],
        ),
    }
    for exchange, table in (("upbit", upbit_rows), ("binance", binance_rows)):
        for base, row in table.items():
            # 조용한 거래소의 BTC 행만 301초 전, 나머지는 지금
            seeded_at = (
                silent_at if (exchange, base) == (silent_exchange, "BTC") else now
            )
            seed_rows(store, [row], seeded_at)
    store.set_rate("upbit", 1400.0, 1390.0, now)
    ms = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731
    for exchange in ("upbit", "binance"):
        store.stream(exchange).last_message_at = ms(now - timedelta(seconds=0.5))

    rows = {r["sym"]: r for r in spreads_json(store)["rows"]}
    assert rows["BTC"]["status"] == "stale"
    assert 300.0 <= rows["BTC"]["age"] <= 303.0
    # 같은 스트림의 다른 코인 행은 스트림 기준 그대로 ok
    assert rows["ETH"]["status"] == "ok"
    assert 0.4 <= rows["ETH"]["age"] <= 2.0


def test_row_and_stream_both_stale_report_the_older() -> None:
    """행 자체 301초 + 스트림 400초 → age 는 둘 중 오래된 쪽(≈400) (§3.2-4, §4)."""
    store = LiveStore()
    now = datetime.now(UTC)
    seed_basic(store, now=now - timedelta(seconds=301))
    store.set_rate("upbit", 1400.0, 1390.0, now)
    ms = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731
    store.stream("upbit").last_message_at = ms(now - timedelta(seconds=400))
    store.stream("binance").last_message_at = ms(now - timedelta(seconds=0.5))
    [row] = spreads_json(store)["rows"]
    assert row["status"] == "stale"
    assert 399.0 <= row["age"] <= 403.0


def test_krw_is_domestic_best_bid_price() -> None:
    # krw 는 그 행 국내 거래소의 최우선 매수호가 — 환율·슬리피지와 무관하다 (§3.2-4)
    store = LiveStore()
    seed_basic(store)
    [row] = spreads_json(store)["rows"]
    assert row["krw"] == 100_000_000.0
    # 규모를 키워 슬리피지가 생겨도 국내 시세 자체는 그대로다
    [big] = spreads_json(store, notional=500_000)["rows"]
    assert big["krw"] == 100_000_000.0


def test_rows_sorted_by_sym_dom_fx() -> None:
    store = LiveStore()
    seed_rows(
        store,
        [make_row("upbit", "ETH"), make_row("upbit", "BTC"), make_row("upbit", "ADA")],
        _now(),
    )
    seed_rows(store, [make_row("bithumb", "BTC"), make_row("bithumb", "ADA")], _now())
    seed_rows(
        store,
        [
            make_row("binance", "ADA"),
            make_row("binance", "BTC"),
            make_row("binance", "ETH"),
        ],
        _now(),
    )
    store.set_rate("upbit", 1400.0, 1390.0, _now())
    store.set_rate("bithumb", 1410.0, 1405.0, _now())
    rows = spreads_json(store)["rows"]
    keys = [(r["sym"], r["dom"], r["fx"]) for r in rows]
    assert keys == sorted(keys)
    assert keys == [
        ("ADA", "bithumb", "binance"),
        ("ADA", "upbit", "binance"),
        ("BTC", "bithumb", "binance"),
        ("BTC", "upbit", "binance"),
        ("ETH", "upbit", "binance"),
    ]


def test_row_keys_are_exactly_the_17_camel_case_keys() -> None:
    store = LiveStore()
    seed_basic(store)
    [row] = spreads_json(store)["rows"]
    assert set(row) == ROW_KEYS
    assert row["spark"] == []
    assert row["netDom"] is None
    # 입출금 4개 값은 수집기가 준 코인 단위 값 그대로 — 001 은 전부 null 을 준다
    assert row["depDom"] is None and row["wdDom"] is None
    assert row["depFx"] is None and row["wdFx"] is None


def test_spark_is_taken_from_the_published_map_including_fail_rows() -> None:
    """009 가 게시한 (dom, fx, base) 맵이 행의 `spark` 로 실린다 — 키 17개·타입은 불변."""
    store = LiveStore()
    seed_basic(store)
    seed_rows(store, [make_row("upbit", "ETH", bids=[], asks=[[3_000.0, 1.0]])], _now())
    seed_rows(store, [make_row("binance", "ETH")], _now())
    store.set_spark(
        {("upbit", "binance", "BTC"): [1.5, 2.0], ("upbit", "binance", "ETH"): [0.3]}
    )
    rows = {r["sym"]: r for r in spreads_json(store)["rows"]}
    assert set(rows["BTC"]) == ROW_KEYS
    assert rows["BTC"]["spark"] == [1.5, 2.0]
    assert rows["ETH"]["status"] == "fail" and rows["ETH"]["spark"] == [0.3]


def test_spark_values_are_rounded_to_three_decimals() -> None:
    """490행 × 30개를 매초 보내므로 배정밀도 그대로면 응답이 몇 배가 된다 (§3.2)."""
    store = LiveStore()
    seed_basic(store)
    store.set_spark({("upbit", "binance", "BTC"): [-1.4799569337290985, 2.0]})
    [row] = spreads_json(store)["rows"]
    assert row["spark"] == [-1.48, 2.0]


# ---- 리뷰 결함 회귀: 국내 호가 가격 0 은 500 이 아니라 그 행 fail (003 §3.2-4 방어) ----


def test_zero_domestic_ask_price_fails_row_not_500():
    store = LiveStore()
    seed_rows(
        store,
        [
            make_row("upbit", "BTC", asks=[[0.0, 1.0]], bids=[[99_000_000.0, 1.0]]),
            make_row(
                "upbit", "ETH", bids=[[5_000_000.0, 1.0]], asks=[[5_010_000.0, 1.0]]
            ),
        ],
        _now(),
    )
    seed_rows(
        store,
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
        _now(),
    )
    store.set_rate("upbit", 1400.0, 1390.0, _now())
    store.mark_received(1_787_000_000)
    rows = {r["sym"]: r for r in spreads_json(store)["rows"]}
    assert rows["BTC"]["status"] == "fail" and rows["BTC"]["rev"] == 0
    assert rows["ETH"]["status"] in ("ok", "stale")
