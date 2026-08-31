"""POST /refresh 의 공개 동작 — 토큰 검사와 응답 모양 (스펙 003 §3.3·§4)."""

from datetime import UTC, datetime

from app.core.live_store import LiveStore
from app.features.spreads.tests.helpers import (
    FakeCollector,
    make_client,
    make_cycle_result,
)


def test_refresh_without_token_config_needs_no_header() -> None:
    collector = FakeCollector(make_cycle_result())
    resp = make_client(collector=collector).post("/refresh")
    assert resp.status_code == 200
    assert collector.cycles == 1


def test_refresh_with_token_set_and_no_header_is_401_plain_detail() -> None:
    collector = FakeCollector(make_cycle_result())
    resp = make_client(refresh_token="s3cret", collector=collector).post("/refresh")
    assert resp.status_code == 401
    # FastAPI 기본 형식 — error 포장 없음
    assert resp.json() == {"detail": "X-Refresh-Token 헤더가 없거나 올바르지 않습니다."}
    assert collector.cycles == 0


def test_refresh_with_wrong_header_is_401() -> None:
    collector = FakeCollector(make_cycle_result())
    client = make_client(refresh_token="s3cret", collector=collector)
    resp = client.post("/refresh", headers={"X-Refresh-Token": "nope"})
    assert resp.status_code == 401


def test_refresh_with_correct_header_is_200() -> None:
    collector = FakeCollector(make_cycle_result())
    client = make_client(refresh_token="s3cret", collector=collector)
    resp = client.post("/refresh", headers={"X-Refresh-Token": "s3cret"})
    assert resp.status_code == 200
    assert collector.cycles == 1


def test_refresh_response_shape_maps_cycle_result() -> None:
    store = LiveStore()
    store.set_rate("upbit", 1400.0, 1390.0, datetime.now(UTC))
    collector = FakeCollector(
        make_cycle_result(
            saved={"upbit": 132, "bithumb": 0, "binance": 210},
            rates_observed=["upbit"],
            failures=[
                {
                    "exchange": "bithumb",
                    "error_code": "exchange_timeout",
                    "message": "응답 시간 초과",
                }
            ],
            warnings=["경고 한 줄"],
            calls={"upbit": 2, "binance": 2},
            wallet_status_available={"binance": True},
        )
    )
    body = make_client(store, collector=collector).post("/refresh").json()
    assert set(body) == {
        "snapshots", "usdkrw", "total_saved", "failures", "warnings", "duration_ms", "fetched_at",
    }  # fmt: skip
    # snapshots[] 는 거래소당 1항목 — 실패 거래소(bithumb)는 saved·calls 0.
    # wallet_status_available 은 006 — 입출금 조회 결과가 없으면 false
    assert body["snapshots"] == [
        {
            "exchange": "upbit",
            "saved": 132,
            "calls": 2,
            "wallet_status_available": False,
        },
        {
            "exchange": "bithumb",
            "saved": 0,
            "calls": 0,
            "wallet_status_available": False,
        },
        {
            "exchange": "binance",
            "saved": 210,
            "calls": 2,
            "wallet_status_available": True,
        },
    ]
    assert body["usdkrw"] == [{"exchange": "upbit", "ask": 1400.0, "bid": 1390.0}]
    assert body["total_saved"] == 342
    assert body["failures"] == [
        {
            "exchange": "bithumb",
            "error_code": "exchange_timeout",
            "message": "응답 시간 초과",
        }
    ]
    assert body["warnings"] == ["경고 한 줄"]
    assert body["duration_ms"] == 12.5
    assert body["fetched_at"] == 1_787_139_510_000
