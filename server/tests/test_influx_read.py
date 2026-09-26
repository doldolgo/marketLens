"""Influx 읽기 규칙 — 봉·사건의 망 문자열 2필드는 선택이고 없음·빈 문자열은 None (스펙 024 §3.4·§3.5).

influxdb-client 자리에 가짜 결과를 꽂아 `query_candles`·`query_premium_events` 의 변환만 본다 — 네트워크 없음.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.core.influx as influx_mod
from app.core.influx import InfluxClient

T0 = 1_700_000_000


class _Record:
    """FluxRecord 중 이 모듈이 읽는 부분 — `values` 와 `get_time`."""

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def get_time(self) -> object:
        return self.values["_time"]


def _client(monkeypatch: pytest.MonkeyPatch, records: list[_Record]) -> InfluxClient:
    tables = [SimpleNamespace(records=records)]
    query_api = SimpleNamespace(query=lambda flux: tables)
    fake = SimpleNamespace(query_api=lambda: query_api)
    monkeypatch.setattr(influx_mod, "InfluxDBClient", lambda **kw: fake)
    return InfluxClient(url="http://influx.test", token="t")


def _candle_values(ts: int, **extra: object) -> dict[str, object]:
    v: dict[str, object] = {
        "_time": datetime.fromtimestamp(ts, tz=UTC),
        "dom": "upbit",
        "fx": "binance",
        "base": "BTC",
        "krw": 100.0,
        "usdt": 70.0,
        "rate": 1400.0,
        "dom_dep": 1,
        "dom_wd": 1,
        "fx_dep": 1,
        "fx_wd": 0,
        "blocked_fwd_sec": 60,
        "blocked_rev_sec": 0,
        "samples": 60,
    }
    for k in ("fwd_o", "fwd_h", "fwd_l", "fwd_c", "rev_o", "rev_h", "rev_l", "rev_c"):
        v[k] = 0.5
    v.update(extra)
    return v


def test_old_eighteen_field_candle_reads_with_null_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch,
        [
            _Record(_candle_values(T0)),  # 배포 전 점 — 망 필드 없음
            _Record(_candle_values(T0 + 60, net_dom="Ethereum", net_fx="")),
            _Record(_candle_values(T0 + 120, net_dom="", net_fx="")),
        ],
    )
    rows = client.query_candles("candles_1m", start=T0, stop=T0 + 180)
    assert [(r.ts, r.net_dom, r.net_fx) for r in rows] == [
        (T0, None, None),
        (T0 + 60, "Ethereum", None),
        (T0 + 120, None, None),
    ]
    assert rows[0].fx_wd == 0  # 수치 18 필드는 그대로 읽힌다 — 버리지 않는다


def test_candle_missing_numeric_field_is_still_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _candle_values(T0, net_dom="Ethereum")
    del values["samples"]
    client = _client(monkeypatch, [_Record(values)])
    assert client.query_candles("candles_1m", start=T0, stop=T0 + 60) == []


def test_old_event_point_reads_with_null_names(monkeypatch: pytest.MonkeyPatch) -> None:
    base = {
        "dom": "upbit",
        "fx": "binance",
        "base": "SOPH",
        "dir": "kimp",
        "end_ts": 0,
        "duration_seconds": 0,
        "max_percent": 1.5,
        "max_ts": T0,
        "last_ts": T0 + 100,
        "samples": 10,
        "enter_percent": 1.0,
        "exit_percent": 0.5,
    }
    client = _client(
        monkeypatch,
        [
            _Record({**base, "_time": datetime.fromtimestamp(T0, tz=UTC)}),
            _Record(
                {
                    **base,
                    "_time": datetime.fromtimestamp(T0 + 1_000, tz=UTC),
                    "net_dom": "Ethereum",
                    "net_fx": "",
                }
            ),
        ],
    )
    rows = client.query_premium_events(start=T0, stop=T0 + 2_000)
    assert [(r.start_ts, r.net_dom, r.net_fx) for r in rows] == [
        (T0, None, None),
        (T0 + 1_000, "Ethereum", None),
    ]
