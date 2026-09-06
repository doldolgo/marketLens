"""`GET /spreads` 의 서버 슬리피지 — 스펙 003 §3.2-4·§4 "슬리피지 (이 스펙의 핵심)".

네트워크 없음, 저장소에 직접 시드. 시드는 손으로 검산되게 라운드 숫자로 골랐다.
"""

from datetime import UTC, datetime

import pytest

from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.ticks import build_tick
from app.features.spreads.tests.helpers import make_client, make_row, seed_rows


def _now() -> datetime:
    """호출 시점의 시계 — import 시각을 상수로 잡으면 느린 CI 에서 수집 뒤 실행까지 STALE_SEC 를 넘겨 행이 낡은 것으로 판정된다."""
    return datetime.now(UTC)


# 환율 ask=bid=1000 — 슬리피지만 남기려고 테더 프리미엄을 없앤 시드
RATE = 1000.0

# 해외 매수(asks)·매도(bids), 국내 매수(asks)·매도(bids) 각 2단계.
# notional $10,000 이면 두 방향 모두 2단계째까지 먹는다.
FX_ASKS = [[100.0, 50.0], [200.0, 100.0]]  # 1단계 $5,000 → 나머지 $5,000 은 2단계
FX_BIDS = [[80.0, 20.0], [40.0, 100.0]]
DOM_BIDS = [[200_000.0, 50.0], [100_000.0, 100.0]]
DOM_ASKS = [[250_000.0, 20.0], [500_000.0, 100.0]]  # 1단계 ₩5,000,000


def seed(
    store: LiveStore,
    *,
    fx_asks: list[list[float]] | None = None,
    fx_bids: list[list[float]] | None = None,
    dom_bids: list[list[float]] | None = None,
    dom_asks: list[list[float]] | None = None,
) -> LiveStore:
    """upbit × binance 한 페어."""
    seed_rows(
        store,
        [
            make_row(
                "upbit",
                "BTC",
                asks=dom_asks if dom_asks is not None else DOM_ASKS,
                bids=dom_bids if dom_bids is not None else DOM_BIDS,
            )
        ],
        _now(),
    )
    fx_row: Row = make_row(
        "binance",
        "BTC",
        price=100.0,
        asks=fx_asks if fx_asks is not None else FX_ASKS,
        bids=fx_bids if fx_bids is not None else FX_BIDS,
    )
    seed_rows(store, [fx_row], _now())
    store.set_rate("upbit", RATE, RATE, _now())
    store.mark_received(1_787_000_000)
    return store


def only_row(store: LiveStore, notional: float | None = None) -> dict:
    query = "/spreads" if notional is None else f"/spreads?notional={notional}"
    resp = make_client(store).get(query)
    assert resp.status_code == 200, resp.text
    [row] = resp.json()["rows"]
    return row


# 원값(최우선 1단계 기준) — 응답에는 없고 fwd + slipFwd 로 복원된다
FWD_RAW = (200_000.0 / (100.0 * RATE) - 1) * 100  # +100.0 %
REV_RAW = (80.0 * RATE / 250_000.0 - 1) * 100  # −68.0 %


def test_single_level_fill_has_zero_slippage() -> None:
    # 규모가 1단계 안에서 끝나면 slip 이 0 이고 fwd·rev 가 원값과 같다 (§4)
    row = only_row(seed(LiveStore()), notional=1000)
    assert row["slipFwd"] == 0.0
    assert row["slipRev"] == 0.0
    assert row["fwd"] == pytest.approx(FWD_RAW)
    assert row["rev"] == pytest.approx(REV_RAW)


def test_two_levels_deduct_and_raw_is_restored_both_directions() -> None:
    # 2단계 이상을 먹으면 slip > 0 이고 fwd + slipFwd 가 원값이다 (양방향, §4)
    row = only_row(seed(LiveStore()))
    # fwd: 해외에서 $10,000 → 50 + 25 = 75개, 평균 $133.33…
    #      국내에서 75개를 팔면 50@₩200,000 + 25@₩100,000 → 평균 ₩166,666.67
    assert row["fwd"] == pytest.approx(25.0)
    assert row["slipFwd"] == pytest.approx(75.0)
    # rev: 국내에서 ₩10,000,000 → 20 + 10 = 30개, 평균 ₩333,333.33
    #      해외에서 30개를 팔면 20@$80 + 10@$40 → 평균 $66.67
    assert row["rev"] == pytest.approx(-80.0)
    assert row["slipRev"] == pytest.approx(12.0)

    assert row["fwd"] + row["slipFwd"] == pytest.approx(FWD_RAW)
    assert row["rev"] + row["slipRev"] == pytest.approx(REV_RAW)


def test_legs_are_linked_by_quantity_not_walked_separately() -> None:
    # 한 다리에서 산 수량을 다른 다리에서 판다 — 다리를 따로 걸은 값과 다르다 (§4).
    # 국내 매도 1단계를 얇게 해 "산 수량 75개"가 2단계로 넘어가게 만든 시드.
    thin = [[200_000.0, 10.0], [100_000.0, 100.0]]
    row = only_row(seed(LiveStore(), dom_bids=thin))
    # 연결: 75개를 판다 → 10@₩200,000 + 65@₩100,000 = ₩8,500,000, 평균 ₩113,333.33
    #       fwd = (113,333.33 / 133,333.33 − 1) × 100 = −15.0
    assert row["fwd"] == pytest.approx(-15.0)
    # 다리를 따로 걸었다면: 국내 bids 를 금액 ₩10,000,000 로 걸어 평균 ₩125,000
    #                      → fwd = (125,000 / 133,333.33 − 1) × 100 = −6.25
    separate = (125_000.0 / (10_000.0 / 75.0 * RATE) - 1) * 100
    assert row["fwd"] != pytest.approx(separate)


@pytest.mark.parametrize("direction", ["slipFwd", "slipRev"])
def test_slippage_is_monotonic_in_notional(direction: str) -> None:
    # 같은 호가에서 규모를 키우면 slip 이 줄지 않는다 — 호가가 소진돼도 마찬가지 (§4)
    store = seed(LiveStore())
    slips = [only_row(store, notional=n)[direction] for n in (1000, 10_000, 100_000)]
    assert slips == sorted(slips)
    assert slips[0] < slips[-1]

    # 저장 단계를 전부 넘기는 규모(양쪽 호가 소진)에서도 단조성이 유지된다
    over = [only_row(store, notional=n)[direction] for n in (1_000_000, 10_000_000)]
    assert over == sorted(over)
    assert over[0] >= slips[-1]


def test_exhausted_book_uses_actually_filled_average_and_keeps_status() -> None:
    # 호가가 소진되면 실제 체결된 만큼의 평균가를 쓰고 status 는 바뀌지 않는다 (§4).
    # 국내 매도측을 얇게 해 못 판 수량이 생기게 한다 → 판 수량만큼 매수측을 되맞춘다.
    row = only_row(
        seed(LiveStore(), dom_bids=[[200_000.0, 50.0], [100_000.0, 60.0]]),
        notional=100_000,
    )
    # 해외 asks 전부(150개)를 사도 국내에서는 110개만 팔린다 → 매수측을 110개로 되맞춤
    #   매수: 50@$100 + 60@$200 = $17,000, 평균 $154.5454…
    #   매도: 50@₩200,000 + 60@₩100,000 = ₩16,000,000, 평균 ₩145,454.5454…
    #   fwd = (145,454.5454 / 154,545.4545 − 1) × 100
    assert row["fwd"] == pytest.approx((16_000_000.0 / 17_000.0 / RATE - 1) * 100)
    assert row["status"] == "ok"
    # 못 판 코인을 0원으로 치지 않았으므로 −50% 대 쓰레기 값이 아니다
    assert row["fwd"] > -50.0


def _expected_fwd(fx_asks: list[list[float]], notional: float) -> float:
    """시드로 손계산한 fwd — 해외 asks 를 규모로 걸어 산 수량을 국내 DOM_BIDS 에 판 값 (§3.2-4)."""
    bought_qty = bought_amt = 0.0
    for price, size in fx_asks:
        take = min(size, (notional - bought_amt) / price)
        bought_qty += take
        bought_amt += take * price
        if bought_amt >= notional:
            break
    sold_qty = sold_amt = 0.0
    for price, size in DOM_BIDS:
        take = min(size, bought_qty - sold_qty)
        sold_qty += take
        sold_amt += take * price
    return ((sold_amt / sold_qty) / ((bought_amt / bought_qty) * RATE) - 1) * 100


def test_all_stored_levels_are_walked_even_at_twenty() -> None:
    # 바이낸스 행의 asks 가 20단계면 그 전부를 걷는다 (§4) — 20단계를 모두 소진하는 규모에서
    # fwd 가 손계산과 같고, 같은 시드를 19단계로 자르면 값이 달라진다(앞 N단계만 걷는 회귀를 잡는다).
    deep_asks = [[100.0 + i, 5.0] for i in range(20)]  # 단계당 ≈$500, 전체 $10,950
    notional = 11_000  # 20단계 전부(100개, $10,950)를 먹고도 남는다 → 실제 체결분 평균
    deep = only_row(seed(LiveStore(), fx_asks=deep_asks), notional=notional)
    assert deep["fwd"] == pytest.approx(_expected_fwd(deep_asks, notional))
    # 손계산 그대로: 평균 $109.5 에 100개 → 국내 50@₩200,000 + 50@₩100,000 → 평균 ₩150,000
    assert deep["fwd"] == pytest.approx((150_000.0 / (109.5 * RATE) - 1) * 100)
    assert deep["slipFwd"] > 0.0

    truncated = only_row(seed(LiveStore(), fx_asks=deep_asks[:19]), notional=notional)
    assert truncated["fwd"] == pytest.approx(_expected_fwd(deep_asks[:19], notional))
    assert truncated["fwd"] != pytest.approx(deep["fwd"])
    # 깊이 전용 필드는 응답에 없다 (001 §3.3 — 행의 asks/bids 가 전부다)
    assert all("depth" not in key for key in deep)


def test_default_notional_is_10000_and_echoed_at_top_level() -> None:
    # notional 미지정이면 10000 이 쓰이고 응답 최상위에 그 값이 실린다 (§4)
    store = seed(LiveStore())
    body = make_client(store).get("/spreads").json()
    assert body["notional"] == 10_000.0
    explicit = make_client(store).get("/spreads?notional=10000").json()
    assert explicit["notional"] == 10_000.0
    assert explicit["rows"][0]["fwd"] == body["rows"][0]["fwd"]
    # 실수도 허용된다
    assert make_client(store).get("/spreads?notional=12345.5").json()["notional"] == (
        12_345.5
    )


@pytest.mark.parametrize("value", ["0", "-1", "10000001", "abc", ""])
def test_out_of_range_or_non_numeric_notional_is_422(value: str) -> None:
    # 0·음수·상한 초과·문자열은 FastAPI 기본 422 다 — error 포장이 아니다 (§3.2-0)
    resp = make_client(seed(LiveStore())).get(f"/spreads?notional={value}")
    assert resp.status_code == 422
    assert "detail" in resp.json()
    assert "error" not in resp.json()


def test_boundary_notional_values_are_accepted() -> None:
    # 허용 범위는 1 ≤ notional ≤ 10,000,000 이고 양 끝은 통과한다 (§3.2-0)
    store = seed(LiveStore())
    for value in (1, 10_000_000):
        resp = make_client(store).get(f"/spreads?notional={value}")
        assert resp.status_code == 200
        assert resp.json()["notional"] == float(value)


def test_net_values_differ_from_stored_raw_by_the_deducted_width() -> None:
    # 응답 fwd·rev 는 저장 계층(009 틱 → 005 premium)이 쓰는 원값과 다르다 —
    # 같은 저장소로 만든 틱의 fwd 는 응답의 fwd + slipFwd 와 같다 (§4)
    store = seed(LiveStore())
    row = only_row(store)
    [point] = build_tick(store, ts=1_787_000_000, dw_failed=()).rows
    assert (point.dom, point.fx, point.base) == ("upbit", "binance", "BTC")
    # 차감이 0 이 아닌 시드라 두 값이 실제로 다르다
    assert row["slipFwd"] > 0 and row["slipRev"] > 0
    assert point.fwd != pytest.approx(row["fwd"])
    assert point.fwd == pytest.approx(row["fwd"] + row["slipFwd"])
    assert point.rev == pytest.approx(row["rev"] + row["slipRev"])
