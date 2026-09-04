"""012 깊이 스트림이 004 의 걷기에 반영되는지 — 스펙 004 §3.1·§4 "깊이 반영".

걷는 목록은 전부 `core/orderbook.py` 의 `walk_levels` 가 고른다: `depth_*` 가 있으면 그것,
없으면 REST `asks`/`bids`. 표면값(1단계)은 그 반대로 REST 최우선을 직접 읽는다 — 한 응답 안에서
출처가 갈리는 것이 의도된 설계다(§3.1).
"""

import pytest

from app.core.live_store import LiveStore
from app.features.analysis.tests.helpers import (
    FIXED_DT,
    FIXED_MS,
    FIXED_SEC,
    SEED_RATE,
    make_client,
    make_row,
    seed_levels,
    standard_store,
)

# 바이낸스 REST 는 1단계뿐이다(001 계약) — 깊이가 붙기 전의 모습
REST_ASKS = [[71_000.0, 1.0]]
REST_BIDS = [[70_900.0, 1.0]]
# 스트림이 채운 3단계. 최우선가가 REST 와 다르다 — 출처가 다르기 때문이다(§3.2 orderbook)
DEPTH_ASKS = [[71_100.0, 0.1], [71_200.0, 0.1], [71_300.0, 0.1]]
DEPTH_BIDS = [[70_800.0, 0.1], [70_700.0, 0.1], [70_600.0, 0.1]]

# 표면값 검증용 — REST 최우선(71,035.5)과 확연히 다른 깊이
SKEWED_ASKS = [[80_000.0, 1.0], [90_000.0, 1.0]]
SKEWED_BIDS = [[60_000.0, 1.0], [50_000.0, 1.0]]

# 1단계가 얕은 깊이 — 200만원을 걸으면 2단계까지 먹는다(1단계 = 71,000×1,400×0.01 = 99.4만원)
SHALLOW_ASKS = [[71_000.0, 0.01], [72_000.0, 10.0]]
SHALLOW_BIDS = [[70_900.0, 10.0]]

# REST 최우선(ask 71,035.5)보다 싼 매도호가 — 매수가 유리해져 실효 수익률이 표면 김프를 넘는다.
# 어떤 규모도 1단계 안에서 끝나게 깊게 둬서 걷기 자체의 슬리피지를 0 으로 만든다.
BETTER_ASKS = [[70_000.0, 100.0]]
BETTER_BIDS = [[69_950.0, 100.0]]


def _attach_depth(
    store: LiveStore,
    exchange: str,
    base: str,
    asks: list[list[float]],
    bids: list[list[float]],
) -> LiveStore:
    """저장소가 들고 있는 행에 012 스트림 깊이를 얹는다."""
    row = store.get(exchange, base)
    assert row is not None
    row.depth_asks, row.depth_bids = asks, bids
    row.depth_at = FIXED_MS
    return store


def _binance_only_store(*, depth: bool) -> LiveStore:
    """REST 1단계짜리 바이낸스 행 하나. depth=True 면 3단계 스트림을 얹는다."""
    store = LiveStore()
    row = make_row("binance", "BTC", price=71_000.0, asks=REST_ASKS, bids=REST_BIDS)
    if depth:
        row.depth_asks, row.depth_bids = DEPTH_ASKS, DEPTH_BIDS
        row.depth_at = FIXED_MS
    store.replace_exchange("binance", [row], FIXED_DT)
    store.mark_received(FIXED_SEC)
    return store


def test_orderbook_returns_stream_depth_levels():
    """depth_asks 가 3단계인 바이낸스 행 → /orderbook/binance 의 asks 가 3개 (§4)."""
    client = make_client(_binance_only_store(depth=True))
    body = client.get(
        "/orderbook/binance", params={"symbol": "BTC/USDT", "depth": 10}
    ).json()
    assert [lv["price"] for lv in body["asks"]] == [lv[0] for lv in DEPTH_ASKS]
    assert [lv["size"] for lv in body["asks"]] == [lv[1] for lv in DEPTH_ASKS]
    assert [lv["price"] for lv in body["bids"]] == [lv[0] for lv in DEPTH_BIDS]
    assert len(body["asks"]) == 3


def test_orderbook_falls_back_to_rest_when_stream_absent():
    """depth_* 가 비면 asks 는 REST 1단계 (§4)."""
    client = make_client(_binance_only_store(depth=False))
    body = client.get(
        "/orderbook/binance", params={"symbol": "BTC/USDT", "depth": 10}
    ).json()
    assert [lv["price"] for lv in body["asks"]] == [lv[0] for lv in REST_ASKS]
    assert [lv["price"] for lv in body["bids"]] == [lv[0] for lv in REST_BIDS]
    assert len(body["asks"]) == 1


def test_slippage_reports_stream_depth_as_available_and_best():
    """depthAvailable 이 depth_asks 길이와 같고 bestPrice 는 depth_asks[0][0] (§4)."""
    client = make_client(_binance_only_store(depth=True))
    body = client.get(
        "/slippage/binance", params={"symbol": "BTC/USDT", "amount": 5_000}
    ).json()
    assert body["depthAvailable"] == len(DEPTH_ASKS)
    assert body["bestPrice"] == DEPTH_ASKS[0][0]  # REST 최우선 71,000 이 아니다
    # 스트림이 없으면 같은 두 값이 REST 로 돌아간다
    flat = make_client(_binance_only_store(depth=False))
    plain = flat.get(
        "/slippage/binance", params={"symbol": "BTC/USDT", "amount": 5_000}
    ).json()
    assert plain["depthAvailable"] == 1
    assert plain["bestPrice"] == REST_ASKS[0][0]


def test_larger_size_turns_slippage_positive_only_with_depth():
    """깊이가 있으면 규모를 키울 때 slippagePercent 가 0 → 양수 (§4).

    1단계뿐이면 평균가가 곧 최우선가라 어떤 규모에도 0 이다 — 이 항목이 회귀를 잡는다.
    """
    deep = make_client(_binance_only_store(depth=True))
    small = deep.get(
        "/slippage/binance", params={"symbol": "BTC/USDT", "amount": 5_000}
    ).json()
    assert small["levelsConsumed"] == 1  # 1단계 = 71,100×0.1 = 7,110
    assert small["slippagePercent"] == 0.0

    big = deep.get(
        "/slippage/binance", params={"symbol": "BTC/USDT", "amount": 10_000}
    ).json()
    quantity = 0.1 + (10_000 - 71_100 * 0.1) / 71_200
    average = 10_000 / quantity
    assert big["levelsConsumed"] == 2
    assert big["quantity"] == pytest.approx(quantity)
    assert big["slippagePercent"] == pytest.approx((average - 71_100) / 71_100 * 100)
    assert big["slippagePercent"] > 0

    # 같은 규모라도 1단계짜리 REST 행은 0 이다 — 깊이를 안 걷으면 이 값이 0 으로 남는다
    flat = (
        make_client(_binance_only_store(depth=False))
        .get("/slippage/binance", params={"symbol": "BTC/USDT", "amount": 10_000})
        .json()
    )
    assert flat["levelsConsumed"] == 1
    assert flat["slippagePercent"] == 0.0


def test_domestic_row_walks_its_own_rest_levels():
    """국내 행은 depth_* 가 비어 있어 asks/bids 를 걷는다 — 단계 수가 REST 그대로 (§4)."""
    client = make_client(standard_store())
    asks, bids = seed_levels(100_000_000, "KRW")
    book = client.get(
        "/orderbook/upbit", params={"symbol": "BTC/KRW", "depth": 99}
    ).json()
    assert [lv["price"] for lv in book["asks"]] == [lv[0] for lv in asks]
    assert [lv["price"] for lv in book["bids"]] == [lv[0] for lv in bids]

    slip = client.get(
        "/slippage/upbit", params={"symbol": "BTC/KRW", "amount": 1_000_000}
    ).json()
    assert slip["depthAvailable"] == len(asks) == 5
    assert slip["bestPrice"] == asks[0][0]


def test_surface_premium_uses_rest_best_not_stream_depth():
    """depth_asks[0] 을 REST asks[0] 과 다르게 심어도 표면 김프는 REST 최우선 기준 (§4)."""
    store = _attach_depth(standard_store(), "binance", "BTC", SKEWED_ASKS, SKEWED_BIDS)
    client = make_client(store)

    prem = client.get("/premium", params={"sym": "BTC"}).json()
    assert prem["fwd"]["usd"] == pytest.approx(71_000 * (1 + 0.0005))
    assert prem["fwd"]["premiumPercent"] == pytest.approx(0.503, abs=1e-3)
    assert prem["rev"]["usd"] == pytest.approx(71_000 * (1 - 0.0005))
    assert prem["rev"]["premiumPercent"] == pytest.approx(-0.699, abs=1e-3)

    btc = next(c for c in client.get("/matrix").json()["coins"] if c["sym"] == "BTC")
    assert btc["fwd"]["buyExchange"] == "binance"
    assert btc["fwd"]["sellExchange"] == "bithumb"
    assert btc["fwd"]["premiumPercent"] == pytest.approx(0.603, abs=1e-3)


def test_arbitrage_foreign_leg_walks_stream_depth():
    """해외 다리가 depth_* 를 걷는다 — 깊이를 준 시드와 안 준 시드의 실효 수익률이 다르다 (§4)."""
    params = {"sym": "BTC", "amount": 2_000_000}
    plain = make_client(standard_store()).get("/arbitrage", params=params).json()
    deep_store = _attach_depth(
        standard_store(), "binance", "BTC", SHALLOW_ASKS, SHALLOW_BIDS
    )
    deep = make_client(deep_store).get("/arbitrage", params=params).json()

    assert plain["buy"]["exchange"] == deep["buy"]["exchange"] == "binance"
    # REST 는 1단계가 300만원이라 200만원이 안에서 끝난다
    assert plain["buy"]["levelsConsumed"] == 1
    assert plain["buy"]["slippagePercent"] == 0.0
    # 얕은 깊이는 1단계가 99.4만원뿐이라 2단계까지 먹는다
    assert deep["buy"]["levelsConsumed"] == 2
    assert deep["buy"]["slippagePercent"] > 0
    assert deep["profitPercent"] < plain["profitPercent"]
    # 후보 최우선가도 걷는 목록 기준이다(표면 김프를 REST 로 고정하는 곳은 premium·scan·matrix 뿐)
    assert deep["candidates"][0]["bestAskKrw"] == pytest.approx(
        SHALLOW_ASKS[0][0] * SEED_RATE
    )


def test_matrix_foreign_leg_walks_stream_depth():
    """matrix 해외 다리도 depth_* 를 걷는다 — 표면은 그대로, 실효 수익률만 나빠진다 (§4)."""
    params = {"amountKrw": 2_000_000}
    plain_body = make_client(standard_store()).get("/matrix", params=params).json()
    deep_store = _attach_depth(
        standard_store(), "binance", "BTC", SHALLOW_ASKS, SHALLOW_BIDS
    )
    deep_body = make_client(deep_store).get("/matrix", params=params).json()
    plain = next(c for c in plain_body["coins"] if c["sym"] == "BTC")["fwd"]
    deep = next(c for c in deep_body["coins"] if c["sym"] == "BTC")["fwd"]

    assert deep["premiumPercent"] == pytest.approx(plain["premiumPercent"])
    assert plain["totalSlippagePercent"] == pytest.approx(0.0, abs=1e-9)
    assert deep["totalSlippagePercent"] > plain["totalSlippagePercent"]


def test_matrix_slippage_floors_at_zero_when_stream_beats_rest():
    """스트림 최우선가가 REST 보다 유리해도 totalSlippagePercent 는 음수가 아니라 0 (§3.1·§4).

    표면 김프는 REST 최우선, 실효 수익률은 스트림 깊이라 출처가 달라 차가 음수로 갈 수 있다.
    같은 응답의 표면 김프는 그 시드에서도 REST 값 그대로여야 한다.
    """
    amount_krw = 2_000_000
    store = _attach_depth(standard_store(), "binance", "BTC", BETTER_ASKS, BETTER_BIDS)
    body = make_client(store).get("/matrix", params={"amountKrw": amount_krw}).json()
    fwd = next(c for c in body["coins"] if c["sym"] == "BTC")["fwd"]

    # 표면 김프는 REST 최우선 그대로 — binance ask[0] 에 사서 bithumb bid[0] 에 판다
    rest_buy_krw = 71_000 * (1 + 0.0005) * SEED_RATE
    rest_sell_krw = 100_100_000 * (1 - 0.0005)
    surface = (rest_sell_krw / rest_buy_krw - 1) * 100
    assert fwd["buyExchange"] == "binance"
    assert fwd["sellExchange"] == "bithumb"
    assert fwd["premiumPercent"] == pytest.approx(surface)

    # 실효 수익률은 싼 스트림 호가로 걸어 표면 김프를 넘는다 — 자르기 전 값이 실제로 음수다
    quantity = amount_krw / (BETTER_ASKS[0][0] * SEED_RATE)  # 매수 1단계 안에서 끝난다
    effective = (quantity * rest_sell_krw / amount_krw - 1) * 100  # 매도도 1단계 안
    assert surface - effective < 0

    assert fwd["totalSlippagePercent"] == 0.0
