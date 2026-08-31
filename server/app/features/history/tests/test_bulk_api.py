"""GET /history/streaks/bulk — 전 코인 통계·빈 coins·오류 (스펙 005 §3.4, §4)."""

from app.features.history.tests.helpers import FakeInfluxReader, make_client

T0 = 1_700_000_000


def seeded_reader() -> FakeInfluxReader:
    reader = FakeInfluxReader()
    reader.seed("upbit", "binance", "ETH", [(T0, 2.0, -1.0), (T0 + 60, 3.0, -1.5)])
    reader.seed("upbit", "binance", "BTC", [(T0, 1.0, 0.5)])
    reader.seed("bithumb", "binance", "XRP", [(T0, 9.0, 9.0)])  # dom 이 달라 빠진다
    return reader


def test_empty_records_returns_empty_coins() -> None:
    # 기록 없으면 404 가 아니라 빈 coins (§3.4)
    res = make_client(FakeInfluxReader()).get("/history/streaks/bulk")
    assert res.status_code == 200
    body = res.json()
    assert body["coins"] == []
    assert body["coinCount"] == 0


def test_coin_count_and_shapes() -> None:
    res = make_client(seeded_reader()).get(
        "/history/streaks/bulk", params={"threshold": 0, "maxGap": 123}
    )
    assert res.status_code == 200
    body = res.json()
    # coin_count == len(coins), dom 기본 upbit 의 코인만 (§4)
    assert body["coinCount"] == len(body["coins"]) == 2
    assert [c["base"] for c in body["coins"]] == ["BTC", "ETH"]
    eth = body["coins"][1]
    assert set(eth.keys()) == {
        "base",
        "scanned",
        "lastTs",
        "kimp",
        "reverse",
        "overall",
    }
    assert eth["scanned"] == 2
    assert eth["lastTs"] == T0 + 60
    assert eth["kimp"]["count"] == 1
    assert eth["kimp"]["segments"][0]["samples"] == 2
    # start 기본 0 (§3.4)
    assert body["startTs"] == 0
    assert body["maxGapSeconds"] == 123
    assert set(body.keys()) == {
        "dom",
        "fx",
        "thresholdPercent",
        "maxGapSeconds",
        "startTs",
        "endTs",
        "coinCount",
        "coins",
        "fetchedAt",
    }


def test_dom_filter() -> None:
    res = make_client(seeded_reader()).get(
        "/history/streaks/bulk", params={"dom": "bithumb"}
    )
    body = res.json()
    assert [c["base"] for c in body["coins"]] == ["XRP"]


def test_end_before_start_400() -> None:
    res = make_client(seeded_reader()).get(
        "/history/streaks/bulk", params={"start": 100, "end": 100}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"


def test_negative_threshold_422() -> None:
    res = make_client(seeded_reader()).get(
        "/history/streaks/bulk", params={"threshold": -0.5}
    )
    assert res.status_code == 422


def test_storage_unavailable_503() -> None:
    res = make_client(None).get("/history/streaks/bulk")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "storage_unavailable"
