"""GET /history/events — 기본 창·필터·진행 중·고아 점·정렬·빈 200·오류 (스펙 013 §3.4, §4)."""

import time

from app.core.models import Tick, TickRow
from app.core.premium_events import PremiumEventDetector
from app.features.history.tests.helpers import FakeInfluxReader, make_client

T0 = 1_700_000_000
WEEK = 7 * 86_400


def get(client, **params):  # noqa: ANN001, ANN201 — 테스트 편의
    return client.get(
        "/history/events", params={k: v for k, v in params.items() if v is not None}
    )


def detector_with_open(
    start_ts: int, *, base: str = "SOPH", now: int
) -> PremiumEventDetector:
    """start_ts 에 열려 now 까지 이어진 진행 중 사건 하나를 든 감지기."""
    det = PremiumEventDetector()
    row = TickRow(dom="upbit", fx="binance", base=base, fwd=1.5, rev=-1.0)
    det.observe(Tick(ts=start_ts, rows=(row,), dw_failed=()))
    det.observe(Tick(ts=now, rows=(row,), dw_failed=()))
    return det


def test_default_window_is_seven_days_and_events_empty_is_200() -> None:
    res = get(make_client(FakeInfluxReader()))
    assert res.status_code == 200
    body = res.json()
    assert body["events"] == [] and body["count"] == 0
    assert body["startTs"] == body["endTs"] - WEEK
    assert body["endTs"] >= int(time.time())


def test_closed_event_shape_and_camel_case() -> None:
    reader = FakeInfluxReader()
    reader.seed_event(
        "BONK", T0, T0 + 900, dom="bithumb", dir="reverse", max_percent=1.42
    )
    res = get(make_client(reader), start=T0, end=T0 + 1000)
    body = res.json()
    assert body["count"] == 1
    ev = body["events"][0]
    assert ev == {
        "base": "BONK",
        "dom": "bithumb",
        "fx": "binance",
        "dir": "reverse",
        "startTs": T0,
        "endTs": T0 + 900,
        "durationSeconds": 900,
        "ongoing": False,
        "maxPercent": 1.42,
        "maxTs": T0,
        "samples": 10,
    }


def test_filters_dom_dir_base_and_uppercases_base() -> None:
    reader = FakeInfluxReader()
    reader.seed_event("BTC", T0, T0 + 100)
    reader.seed_event("BTC", T0 + 10, T0 + 100, dir="reverse")
    reader.seed_event("BTC", T0 + 20, T0 + 100, dom="bithumb")
    reader.seed_event("ETH", T0 + 30, T0 + 100)
    client = make_client(reader)
    assert get(client, start=T0, end=T0 + 1000).json()["count"] == 4
    assert get(client, start=T0, end=T0 + 1000, dom="upbit").json()["count"] == 3
    assert get(client, start=T0, end=T0 + 1000, dir="kimp").json()["count"] == 3
    body = get(client, start=T0, end=T0 + 1000, base="btc").json()
    assert body["count"] == 3 and {e["base"] for e in body["events"]} == {"BTC"}


def test_window_is_judged_by_start_ts_only() -> None:
    reader = FakeInfluxReader()
    reader.seed_event("BTC", T0 - 10, T0 + 100)  # 구간 전에 시작 → 빠진다
    reader.seed_event("ETH", T0, T0 + 5000)  # 구간 안에 시작, 구간 밖에서 끝남 → 실린다
    body = get(make_client(reader), start=T0, end=T0 + 1000).json()
    assert [e["base"] for e in body["events"]] == ["ETH"]


def test_sorted_start_desc_then_base_asc() -> None:
    reader = FakeInfluxReader()
    reader.seed_event("ZRX", T0 + 100, T0 + 200)
    reader.seed_event("AAVE", T0 + 100, T0 + 200)
    reader.seed_event("BTC", T0 + 300, T0 + 400)
    body = get(make_client(reader), start=T0, end=T0 + 1000).json()
    assert [e["base"] for e in body["events"]] == ["BTC", "AAVE", "ZRX"]


def test_ongoing_event_from_memory_and_short_one_is_hidden() -> None:
    now = int(time.time())
    det = detector_with_open(now - 4800, now=now)
    det.observe(
        Tick(
            ts=now,
            rows=(TickRow(dom="upbit", fx="binance", base="NEW", fwd=2.0, rev=-1.0),),
            dw_failed=(),
        )
    )  # 방금 열림 — 60초 전이라 안 실린다
    body = get(make_client(FakeInfluxReader(), events=det)).json()
    assert body["count"] == 1
    ev = body["events"][0]
    assert (ev["base"], ev["endTs"], ev["ongoing"]) == ("SOPH", None, True)
    assert abs(ev["durationSeconds"] - 4800) <= 2


def test_memory_wins_over_influx_for_same_key() -> None:
    now = int(time.time())
    reader = FakeInfluxReader()
    reader.seed_event(
        "SOPH", now - 4800, 0, last_ts=now - 100, samples=3
    )  # 60초 갱신 전의 옛 점
    det = detector_with_open(now - 4800, now=now)
    body = get(make_client(reader, events=det)).json()
    assert body["count"] == 1
    assert body["events"][0]["ongoing"] is True and body["events"][0]["samples"] == 2


def test_orphan_open_point_is_shown_closed_at_last_ts() -> None:
    reader = FakeInfluxReader()
    reader.seed_event("SOPH", T0, 0, last_ts=T0 + 700)  # end_ts 0 인데 메모리엔 없다
    body = get(make_client(reader), start=T0, end=T0 + 1000).json()
    ev = body["events"][0]
    assert (ev["endTs"], ev["durationSeconds"], ev["ongoing"]) == (T0 + 700, 700, False)


def test_end_before_start_400() -> None:
    res = get(make_client(FakeInfluxReader()), start=T0, end=T0)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"


def test_bad_params_422() -> None:
    client = make_client(FakeInfluxReader())
    assert get(client, dir="up").status_code == 422
    assert get(client, dom="binance").status_code == 422
    assert get(client, base="BTC/KRW").status_code == 422
    assert get(client, start=-1).status_code == 422


def test_storage_unavailable_503_even_with_ongoing_in_memory() -> None:
    now = int(time.time())
    det = detector_with_open(now - 4800, now=now)
    res = get(make_client(None, events=det))  # INFLUX_TOKEN 없음
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "storage_unavailable"
    reader = FakeInfluxReader()
    reader.fail = True
    res = get(make_client(reader, events=det))
    assert res.status_code == 503
