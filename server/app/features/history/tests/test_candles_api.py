"""GET /history/candles — 기본 창·상한·계층 버킷·방향 매핑·3상태·정렬·빈 200·오류 (스펙 014 §3.6, §4)."""

import time

from app.features.history.tests.helpers import FakeInfluxReader, make_client

T0 = 1_788_739_200  # 2026-09-06 00:00 KST (스펙 예시 값)
DAY = 86_400


def get(client, **params):  # noqa: ANN001, ANN201 — 테스트 편의
    return client.get(
        "/history/candles", params={k: v for k, v in params.items() if v is not None}
    )


def test_default_window_is_limit_and_empty_candles_is_200() -> None:
    res = get(make_client(FakeInfluxReader()), base="BTC")
    assert res.status_code == 200
    body = res.json()
    assert body["candles"] == [] and body["count"] == 0
    assert body["startTs"] == body["endTs"] - DAY  # 1m 상한 = 1,440분 = 하루
    assert abs(body["endTs"] - int(time.time())) <= 2
    assert (body["res"], body["dom"], body["fx"], body["dir"]) == (
        "1m",
        "upbit",
        "binance",
        "kimp",
    )


def test_candle_shape_matches_spec_example_and_camel_case() -> None:
    reader = FakeInfluxReader()
    reader.seed_candle(
        "candles_1m", T0, fwd=(0.62, 0.71, 0.58, 0.66), dw=(1, 0, -1, 1), blocked=(0, 7)
    )
    body = get(make_client(reader), base="BTC", start=T0, end=T0 + DAY).json()
    assert body["count"] == 1 and "fetchedAt" in body
    assert body["candles"][0] == {
        "ts": T0,
        "open": 0.62,
        "high": 0.71,
        "low": 0.58,
        "close": 0.66,
        "krw": 168_450_000.0,
        "usdt": 112_010.5,
        "fxRate": 1502.5,
        "depositOk": True,  # 김프 = dom_dep
        "withdrawOk": True,  # 김프 = fx_wd
        "domDepositOk": True,  # 거래소별 4상태 — dw=(dom_dep, dom_wd, fx_dep, fx_wd)
        "domWithdrawOk": False,
        "fxDepositOk": None,  # −1 → null
        "fxWithdrawOk": True,
        "blockedSec": 0,
        "samples": 60,
    }


def test_window_limit_per_res_is_1440_windows() -> None:
    client = make_client(FakeInfluxReader())
    assert (
        get(client, base="BTC", res="5m", start=T0, end=T0 + 432_000).status_code == 200
    )
    res = get(client, base="BTC", res="5m", start=T0, end=T0 + 432_001)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"
    assert "window exceeds limit" in res.json()["error"]["message"]
    assert (
        get(client, base="BTC", res="1d", start=T0, end=T0 + 1_440 * DAY).status_code
        == 200
    )
    # start 만 주면 end = 지금 — 상한을 넘기면 400
    assert get(client, base="BTC", start=0).status_code == 400
    # end 만 주면 start = end − 상한
    body = get(client, base="BTC", res="1h", end=T0).json()
    assert body["startTs"] == T0 - 1_440 * 3_600


def test_end_not_after_start_is_400() -> None:
    client = make_client(FakeInfluxReader())
    assert get(client, base="BTC", start=T0, end=T0).status_code == 400
    assert get(client, base="BTC", start=T0 + 1, end=T0).status_code == 400


def test_each_res_reads_its_own_bucket() -> None:
    reader = FakeInfluxReader()
    reader.seed_candle("candles_1m", T0)
    reader.seed_candle("candles_5m", T0, samples=300)
    reader.seed_candle("candles_1d", T0, samples=86_400)
    client = make_client(reader)
    assert (
        get(client, base="BTC", start=T0, end=T0 + DAY).json()["candles"][0]["samples"]
        == 60
    )
    assert (
        get(client, base="BTC", res="5m", start=T0, end=T0 + DAY).json()["candles"][0][
            "samples"
        ]
        == 300
    )
    assert (
        get(client, base="BTC", res="1h", start=T0, end=T0 + DAY).json()["candles"]
        == []
    )
    assert (
        get(client, base="BTC", res="1d", start=T0, end=T0 + DAY).json()["candles"][0][
            "samples"
        ]
        == 86_400
    )


def test_reverse_uses_rev_fields_and_swaps_wallet_path() -> None:
    reader = FakeInfluxReader()
    reader.seed_candle(
        "candles_1m", T0, rev=(-0.2, 0.1, -0.5, 0.05), dw=(1, 0, -1, 1), blocked=(3, 9)
    )
    c = get(
        make_client(reader), base="BTC", dir="reverse", start=T0, end=T0 + DAY
    ).json()["candles"][0]
    assert (c["open"], c["high"], c["low"], c["close"]) == (-0.2, 0.1, -0.5, 0.05)
    assert c["withdrawOk"] is False  # 역프 = dom_wd (0)
    assert c["depositOk"] is None  # 역프 = fx_dep (−1 → null)
    # 거래소별 4상태는 방향과 무관하게 그대로
    assert (
        c["domDepositOk"],
        c["domWithdrawOk"],
        c["fxDepositOk"],
        c["fxWithdrawOk"],
    ) == (True, False, None, True)
    assert c["blockedSec"] == 9


def test_filters_by_dom_and_uppercases_base() -> None:
    reader = FakeInfluxReader()
    reader.seed_candle("candles_1m", T0, dom="upbit")
    reader.seed_candle("candles_1m", T0, dom="bithumb", samples=7)
    reader.seed_candle("candles_1m", T0, base="ETH")
    client = make_client(reader)
    body = get(client, base="btc", start=T0, end=T0 + DAY).json()
    assert (
        body["base"] == "BTC"
        and body["count"] == 1
        and body["candles"][0]["samples"] == 60
    )
    body = get(client, base="BTC", dom="bithumb", start=T0, end=T0 + DAY).json()
    assert body["count"] == 1 and body["candles"][0]["samples"] == 7


def test_window_judged_by_window_start_and_sorted_ascending() -> None:
    reader = FakeInfluxReader()
    reader.seed_candle("candles_1m", T0 + 120)
    reader.seed_candle("candles_1m", T0)
    reader.seed_candle("candles_1m", T0 - 60)  # start 전
    reader.seed_candle("candles_1m", T0 + 180)  # end 와 같다 → 빠진다
    body = get(make_client(reader), base="BTC", start=T0, end=T0 + 180).json()
    assert [c["ts"] for c in body["candles"]] == [T0, T0 + 120]


def test_bad_params_422() -> None:
    client = make_client(FakeInfluxReader())
    assert get(client, base="BTC", fx="bybit").status_code == 422
    assert get(client, base="BTC", res="3m").status_code == 422
    assert get(client, base="BTC", dir="up").status_code == 422
    assert get(client, base="BTC", dom="binance").status_code == 422
    assert get(client, base="BTC/KRW").status_code == 422
    assert get(client).status_code == 422  # base 필수
    assert get(client, base="BTC", start=-1).status_code == 422


def test_storage_unavailable_503() -> None:
    res = get(make_client(None), base="BTC")  # INFLUX_TOKEN 없음
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "storage_unavailable"
    reader = FakeInfluxReader()
    reader.fail = True
    assert get(make_client(reader), base="BTC").status_code == 503
