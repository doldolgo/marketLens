"""GET /history/premium — 구간 판정·events/dt·summary·오류 4종 (스펙 005 §3.4, §4)."""

from datetime import UTC, datetime

from app.features.history.tests.helpers import FakeInfluxReader, make_client


def ts_of(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


# 2025-03-03 은 월요일 — date=2025-03-05(수) 의 ISO 주는 [03-03, 03-10)
MON = ts_of(2025, 3, 3)


def seeded_reader() -> FakeInfluxReader:
    reader = FakeInfluxReader()
    reader.seed(
        "upbit",
        "binance",
        "BTC",
        [
            (ts_of(2025, 3, 2, 23, 59, 59), 9.0, -9.0),  # 주 구간 밖 (이전 일요일)
            (MON, 1.0, -1.0),
            (MON + 60, 2.5, -0.5),
            (ts_of(2025, 3, 9, 23, 59, 59), 0.5, -2.0),
            (ts_of(2025, 3, 10), 8.0, -8.0),  # end exclusive — 다음 월요일
        ],
    )
    return reader


def test_week_window_and_events() -> None:
    client = make_client(seeded_reader())
    res = client.get(
        "/history/premium", params={"base": "BTC", "unit": "week", "date": "2025-03-05"}
    )
    assert res.status_code == 200
    body = res.json()
    # 구간 밖 기록은 안 잡힘, count == len(events), events[0].dt == 0 (§4)
    assert body["count"] == 3
    assert len(body["events"]) == 3
    assert body["events"][0] == {"dt": 0, "fwd": 1.0, "rev": -1.0}
    assert body["events"][1]["dt"] == 60
    assert body["events"][2]["dt"] == ts_of(2025, 3, 9, 23, 59, 59) - (MON + 60)
    assert body["start"] == "2025-03-03T00:00:00Z"
    assert body["end"] == "2025-03-10T00:00:00Z"
    assert body["first_ts"] == MON
    # summary 는 구간 전체 기준 (§4)
    assert body["summary"] == {
        "first_fwd": 1.0,
        "last_fwd": 0.5,
        "min_fwd": 0.5,
        "max_fwd": 2.5,
    }
    assert (body["dom"], body["fx"], body["base"], body["unit"]) == (
        "upbit",
        "binance",
        "BTC",
        "week",
    )


def test_month_window_calendar_boundaries() -> None:
    # 주/월 구간 경계가 ISO 주·달력 월과 일치 (§4)
    reader = FakeInfluxReader()
    reader.seed(
        "upbit",
        "binance",
        "BTC",
        [
            (ts_of(2025, 2, 28, 23, 59, 59), 1.0, 0.1),  # 2월 — 밖
            (ts_of(2025, 3, 1), 2.0, 0.2),
            (ts_of(2025, 3, 31, 23, 59, 59), 3.0, 0.3),
            (ts_of(2025, 4, 1), 4.0, 0.4),  # 4월 — 밖
        ],
    )
    client = make_client(reader)
    res = client.get(
        "/history/premium",
        params={"base": "BTC", "unit": "month", "date": "2025-03-15"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 2
    assert body["start"] == "2025-03-01T00:00:00Z"
    assert body["end"] == "2025-04-01T00:00:00Z"


def test_december_month_rolls_to_next_year() -> None:
    reader = FakeInfluxReader()
    reader.seed("upbit", "binance", "BTC", [(ts_of(2025, 12, 31, 12), 1.0, 0.0)])
    client = make_client(reader)
    res = client.get(
        "/history/premium",
        params={"base": "BTC", "unit": "month", "date": "2025-12-05"},
    )
    assert res.status_code == 200
    assert res.json()["end"] == "2026-01-01T00:00:00Z"


def test_default_date_is_today_utc() -> None:
    reader = FakeInfluxReader()
    now = int(datetime.now(UTC).timestamp())
    reader.seed("upbit", "binance", "BTC", [(now - 1, 1.5, -0.5)])
    client = make_client(reader)
    res = client.get("/history/premium", params={"base": "BTC", "unit": "week"})
    assert res.status_code == 200
    assert res.json()["count"] == 1


def test_empty_window_404() -> None:
    client = make_client(seeded_reader())
    res = client.get(
        "/history/premium", params={"base": "BTC", "unit": "week", "date": "2024-01-03"}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "market_data_not_found"


def test_bad_date_400() -> None:
    client = make_client(seeded_reader())
    res = client.get(
        "/history/premium", params={"base": "BTC", "unit": "week", "date": "abc"}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"


def test_invalid_unit_and_dom_422() -> None:
    client = make_client(seeded_reader())
    assert (
        client.get(
            "/history/premium", params={"base": "BTC", "unit": "day"}
        ).status_code
        == 422
    )
    # dom=binance 등 파라미터 검증 실패는 FastAPI 기본 422 (§3.4)
    assert (
        client.get(
            "/history/premium", params={"base": "BTC", "unit": "week", "dom": "binance"}
        ).status_code
        == 422
    )


def test_storage_unavailable_503() -> None:
    # INFLUX_TOKEN 없음(클라이언트 없음) — 503 storage_unavailable (§3.1)
    client = make_client(None)
    res = client.get("/history/premium", params={"base": "BTC", "unit": "week"})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "storage_unavailable"

    # 연결 실패도 같은 503 (§3.4)
    reader = seeded_reader()
    reader.fail = True
    res = make_client(reader).get(
        "/history/premium", params={"base": "BTC", "unit": "week", "date": "2025-03-05"}
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "storage_unavailable"


# ---- 리뷰 확정 결함 회귀: 극단 날짜·비표준 형식은 500 이 아니라 400 (005 §3.4) ----


def test_extreme_date_returns_400_not_500():
    client = make_client(FakeInfluxReader())
    for unit in ("month", "week"):
        res = client.get(f"/history/premium?base=BTC&unit={unit}&date=9999-12-31")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "invalid_request"


def test_non_dashed_date_formats_are_rejected():
    client = make_client(FakeInfluxReader())
    for bad in ("20260828", "2026-W35-4", "2026-8-28"):
        res = client.get(f"/history/premium?base=BTC&unit=week&date={bad}")
        assert res.status_code == 400, bad
