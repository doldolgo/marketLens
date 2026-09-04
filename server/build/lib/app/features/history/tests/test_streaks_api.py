"""GET /history/streaks — 구간 판정·가중 평균·maxGap·오류 (스펙 005 §3.4, §4)."""

import time

from app.features.history.tests.helpers import FakeInfluxReader, make_client

T0 = 1_700_000_000

# 스펙 §3.4 예시: 값 0 1 3 6 29 4 31 (60초 간격) — fwd 에 싣고 rev 는 전부 음수로 둔다
EXAMPLE = [0.0, 1.0, 3.0, 6.0, 29.0, 4.0, 31.0]


def example_reader() -> FakeInfluxReader:
    reader = FakeInfluxReader()
    reader.seed(
        "upbit",
        "binance",
        "BTC",
        [(T0 + i * 60, v, -1.0) for i, v in enumerate(EXAMPLE)],
    )
    return reader


def get(client, **params):
    return client.get("/history/streaks", params={"base": "BTC", **params})


def test_threshold_4_single_segment() -> None:
    # 예시: threshold 4 → 구간 1개(samples 4, max 31) (§3.4)
    res = get(make_client(example_reader()), threshold=4)
    assert res.status_code == 200
    body = res.json()
    kimp = body["kimp"]
    assert kimp["count"] == 1
    seg = kimp["segments"][0]
    assert seg["samples"] == 4
    assert seg["maxPercent"] == 31.0
    assert seg["startTs"] == T0 + 3 * 60
    assert seg["endTs"] == T0 + 6 * 60
    assert seg["durationSeconds"] == 180
    # rev 는 전부 음수 — reverse 방향은 구간이 없다 (절댓값 없이 각각 계산, §3.4-3)
    assert body["reverse"]["count"] == 0
    assert body["reverse"]["segments"] == []


def test_threshold_5_two_segments_weighted_avg() -> None:
    # 예시: threshold 5 → 2개, 방향 avg 는 샘플 수 가중 (§4)
    res = get(make_client(example_reader()), threshold=5)
    body = res.json()
    kimp = body["kimp"]
    assert kimp["count"] == 2
    assert [s["samples"] for s in kimp["segments"]] == [2, 1]
    # 구간 평균 17.5(2표본)·31(1표본) → 가중 평균 (6+29+31)/3 = 22.0
    assert kimp["avgPercent"] == 22.0
    assert kimp["maxPercent"] == 31.0
    assert kimp["maxDurationSeconds"] == 60
    assert kimp["avgDurationSeconds"] == 30.0
    # 기록 1개짜리 구간은 duration 0 (§3.4-4)
    assert kimp["segments"][1]["durationSeconds"] == 0


def test_max_gap_splits_segment() -> None:
    # maxGap 초과 간격에서 구간이 끊긴다 (§4)
    reader = FakeInfluxReader()
    reader.seed(
        "upbit",
        "binance",
        "BTC",
        [(T0, 5.0, 0.0), (T0 + 60, 6.0, 0.0), (T0 + 60 + 700, 7.0, 0.0)],
    )
    body = get(make_client(reader), threshold=1).json()  # 기본 maxGap 600 < 700
    assert body["kimp"]["count"] == 2
    body = get(make_client(reader), threshold=1, maxGap=1000).json()
    assert body["kimp"]["count"] == 1


def test_top_level_14_keys_and_kst() -> None:
    res = get(make_client(example_reader()), threshold=4)
    body = res.json()
    assert set(body.keys()) == {
        "base",
        "dom",
        "fx",
        "thresholdPercent",
        "maxGapSeconds",
        "startTs",
        "endTs",
        "kimp",
        "reverse",
        "overall",
        "scanned",
        "lastUpdatedTs",
        "lastUpdated",
        "fetchedAt",
    }
    # last_updated 가 +09:00 으로 끝난다 (§4)
    assert body["lastUpdated"].endswith("+09:00")
    assert body["lastUpdatedTs"] == T0 + 6 * 60
    assert body["scanned"] == len(EXAMPLE)
    # start/end 미지정 — 첫 ts / 지금+1초 (§3.4)
    assert body["startTs"] == T0
    assert body["endTs"] > int(time.time())
    # 구간 start/end 도 KST 표기 (§3.4-4)
    assert body["kimp"]["segments"][0]["start"].endswith("+09:00")


def test_overall_ignores_threshold_and_unions_segments() -> None:
    body = get(make_client(example_reader()), threshold=5).json()
    overall = body["overall"]
    # 기준치 무관 전체 행 기준 (§3.4-6)
    assert overall["maxKimpPercent"] == 31.0
    assert overall["avgKimpPercent"] == sum(EXAMPLE) / len(EXAMPLE)
    assert overall["maxReversePercent"] == -1.0
    assert overall["avgReversePercent"] == -1.0
    # 두 방향 구간 합집합 — reverse 구간은 없다
    assert overall["segmentCount"] == 2
    assert overall["maxDurationSeconds"] == 60
    assert overall["avgDurationSeconds"] == 30.0


def test_explicit_range_filters_rows() -> None:
    body = get(
        make_client(example_reader()), threshold=0, start=T0 + 60, end=T0 + 121
    ).json()
    assert body["scanned"] == 2
    assert body["startTs"] == T0 + 60
    assert body["endTs"] == T0 + 121


def test_end_before_start_400() -> None:
    res = get(make_client(example_reader()), start=T0 + 100, end=T0 + 100)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"


def test_unknown_coin_404() -> None:
    res = get(make_client(example_reader()), threshold=0)
    assert res.status_code == 200
    res = make_client(example_reader()).get(
        "/history/streaks", params={"base": "NOPE", "threshold": 0}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "market_data_not_found"


def test_negative_threshold_422() -> None:
    assert get(make_client(example_reader()), threshold=-1).status_code == 422


def test_storage_unavailable_503() -> None:
    assert get(make_client(None)).status_code == 503


# ---- 리뷰 확정 결함 회귀: 경계 밖 start/end 는 422, end<=0 은 400 (005 §3.4) ----


def test_out_of_range_epoch_params_are_422_not_500():
    client = make_client(FakeInfluxReader())
    assert client.get("/history/streaks?base=BTC&end=253402300800").status_code == 422
    assert client.get("/history/streaks?base=BTC&start=-1").status_code == 422
    assert client.get("/history/streaks/bulk?end=999999999999999").status_code == 422
    assert client.get("/history/streaks/bulk?start=-99999999999999").status_code == 422


def test_nonpositive_end_is_400():
    client = make_client(FakeInfluxReader())
    res = client.get("/history/streaks?base=BTC&end=0")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"
