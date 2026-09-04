"""`GET /spreads` 의 서버 슬리피지 — 스펙 003 §3.2-4·§4 "슬리피지 (이 스펙의 핵심)".

네트워크 없음, 저장소에 직접 시드. 시드는 손으로 검산되게 라운드 숫자로 골랐다.
"""

from datetime import UTC, datetime

import pytest

from app.core.live_store import LiveStore
from app.core.models import Row
from app.features.spreads.tests.helpers import make_client, make_row

NOW = datetime.now(UTC)

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
    depth: tuple[list[list[float]], list[list[float]]] | None = None,
) -> LiveStore:
    """upbit × binance 한 페어. depth 를 주면 해외 행에 012 스트림 깊이를 얹는다."""
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit",
                "BTC",
                asks=dom_asks if dom_asks is not None else DOM_ASKS,
                bids=dom_bids if dom_bids is not None else DOM_BIDS,
            )
        ],
        NOW,
    )
    fx_row: Row = make_row(
        "binance",
        "BTC",
        price=100.0,
        asks=fx_asks if fx_asks is not None else FX_ASKS,
        bids=fx_bids if fx_bids is not None else FX_BIDS,
    )
    if depth is not None:
        fx_row.depth_asks, fx_row.depth_bids = depth
        fx_row.depth_at = 1_757_000_000_000
    store.replace_exchange("binance", [fx_row], NOW)
    store.set_rate("upbit", RATE, RATE, NOW)
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


def test_depth_levels_are_used_when_present() -> None:
    # 해외에 depth_* 가 있으면 그것을, 없으면 1단계 asks/bids 를 쓴다 (012 스트림 유무, §4)
    shallow = seed(LiveStore(), fx_asks=[FX_ASKS[0]], fx_bids=[FX_BIDS[0]])
    streamed = seed(
        LiveStore(),
        fx_asks=[FX_ASKS[0]],
        fx_bids=[FX_BIDS[0]],
        depth=(FX_ASKS, FX_BIDS),
    )
    # 1단계만 있으면 $5,000 어치(50개)밖에 못 사고 그 평균은 최우선가 그대로다
    assert only_row(shallow)["slipFwd"] == 0.0
    # 깊이가 실리면 2단계까지 먹어 평균이 나빠진다 — 2단계 시드와 같은 값
    assert only_row(streamed)["fwd"] == pytest.approx(25.0)
    assert only_row(streamed)["slipFwd"] == pytest.approx(75.0)
    # 깊이는 응답에 노출되지 않는다 (001 §3.3 — 저장도 안 한다)
    assert all("depth" not in key for key in only_row(streamed))


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
