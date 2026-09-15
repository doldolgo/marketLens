"""표 바이트 계약 — `build_table` 의 dict 가 `SpreadRow`/`SpreadsResponse` 모델을 거친 옛 경로와 같은 바이트인지.

뜨거운 경로(017 게시기)는 모델을 만들지 않고 camelCase dict 를 바로 json 으로 만든다. 키 이름·순서·
값의 형(int→float 강제)이 모델과 어긋나면 브라우저가 받는 바이트가 바뀌므로 여기서 잡는다.
"""

import json
from datetime import UTC, datetime, timedelta

from app.core.live_store import LiveStore
from app.core.networks import Network
from app.core.serialization import camelize_json
from app.features.spreads.models import SpreadsResponse
from app.features.spreads.push import encode_table
from app.features.spreads.service import build_table
from app.features.spreads.tests.helpers import make_row, seed_rows

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


def _store() -> LiveStore:
    store = LiveStore()
    seed_rows(
        store,
        [
            # 정수 가격 — 거래소가 int 로 준 값은 모델이 float 로 강제하던 것과 같아야 한다
            make_row(
                "upbit",
                "BTC",
                price=150_000_000,
                asks=[[150_000_100, 1]],
                bids=[[150_000_000, 2]],
            ),
            make_row(
                "binance",
                "BTC",
                price=100_000,
                asks=[[100_001, 1.5]],
                bids=[[99_999, 1.5]],
            ),
            # 아주 작은 가격 — 지수 표기(1.25e-05) 가 그대로 나가는지
            make_row(
                "upbit",
                "SHIB",
                price=0.0187,
                asks=[[0.0188, 5e8]],
                bids=[[0.0187, 5e8]],
                dep=True,
                wd=None,
            ),
            make_row(
                "binance",
                "SHIB",
                price=1.25e-05,
                asks=[[1.26e-05, 5e8]],
                bids=[[1.25e-05, 5e8]],
                dep=False,
                wd=True,
                networks=[
                    Network(code="BSC", name="BNB Smart Chain", dep=True, wd=False)
                ],
            ),
            # fail 행 — 호가 없음
            make_row(
                "bithumb",
                "XRP",
                quote="KRW",
                price=800.0,
                asks=[],
                bids=[[799.0, 10.0]],
            ),
            make_row(
                "binance",
                "XRP",
                price=0.55,
                asks=[[0.551, 100.0]],
                bids=[[0.549, 100.0]],
            ),
        ],
        NOW,
    )
    store.stream("bithumb").last_message_at = int(
        (NOW - timedelta(seconds=7)).timestamp() * 1000
    )
    store.set_rate("upbit", 1450, 1449, NOW)
    # 빗썸 환율은 61초 낡음 → 한글 경고 1줄 (008)
    store.set_rate("bithumb", 1451.0, 1450.0, NOW - timedelta(seconds=61))
    store.mark_received(int(NOW.timestamp()))
    store.set_spark(
        {
            ("upbit", "binance", "BTC"): [1.23456789, -0.0004, 2.0],
            ("upbit", "binance", "SHIB"): [0.1],
        }
    )
    return store


def test_table_bytes_match_model_path() -> None:
    table = build_table(_store(), now=NOW)
    fast = encode_table(table)
    # 옛 경로 — 모델 → model_dump(snake_case) → camelize_json → json.dumps
    model = SpreadsResponse.model_validate(table)
    slow = json.dumps(
        camelize_json(model.model_dump()),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    assert fast == slow
    # 표가 실제로 까다로운 값들을 담았는지 — 빈 표로 통과하는 테스트가 아니다
    parsed = json.loads(fast)
    syms = {(r["sym"], r["dom"], r["status"]) for r in parsed["rows"]}
    assert ("BTC", "upbit", "ok") in syms
    assert ("XRP", "bithumb", "fail") in syms
    assert any(r["usd"] == 1.25e-05 for r in parsed["rows"])
    assert '"krw":150000000.0' in fast  # int 가격이 float 로
    assert parsed["warnings"] and "bithumb" in parsed["warnings"][0]


def test_row_keys_follow_spread_row_model() -> None:
    table = build_table(_store(), now=NOW)
    expected = [
        "sym", "dom", "fx", "fwd", "rev", "usd", "spark", "status", "age",
        "slipFwd", "slipRev", "krw", "netDom", "depDom", "wdDom", "depFx", "wdFx",
    ]  # fmt: skip
    assert all(list(row.keys()) == expected for row in table["rows"])
    assert list(table.keys()) == [
        "rate",
        "notional",
        "rows",
        "warnings",
        "dataReceivedAt",
        "fetchedAt",
    ]
