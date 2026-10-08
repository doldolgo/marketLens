"""`chain_flow` 줄·나노초 정밀도 — `write_lines` 의 정밀도 인자와 기존 호출 불변 (스펙 050 §3.5·§4)."""

import dataclasses
from types import SimpleNamespace

import pytest
from influxdb_client.domain.write_precision import WritePrecision

from app.core import influx as influx_mod
from app.core.influx import ChainFlowRow, InfluxClient, chain_flow_line, chain_flow_ns
from tests.eth_flow_fakes import parse_flow_line

T0 = 1_759_924_631


def _client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[InfluxClient, list[tuple[str, bytes, object]]]:
    writes: list[tuple[str, bytes, object]] = []

    class _WriteApi:
        def __enter__(self) -> "_WriteApi":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def write(self, *, bucket: str, record: bytes, write_precision: object) -> None:
            writes.append((bucket, record, write_precision))

    def make(**kw: object) -> SimpleNamespace:
        return SimpleNamespace(write_api=lambda write_options: _WriteApi())

    monkeypatch.setattr(influx_mod, "InfluxDBClient", make)
    return InfluxClient(url="http://influx.test", token="t"), writes


def test_write_lines_defaults_to_seconds_and_takes_nanoseconds_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, writes = _client(monkeypatch)
    client.write_lines(["x v=1.0 1"])  # 기존 호출 모양(009·014) — 초
    client.write_lines(["x v=1.0 1"], "candles_1m")
    client.write_lines(["x v=1.0 1000000000"], precision="ns")
    assert [(b, p) for b, _, p in writes] == [
        ("marketlens", WritePrecision.S),
        ("candles_1m", WritePrecision.S),
        ("marketlens", WritePrecision.NS),
    ]


def test_chain_flow_line_has_four_tags_seven_fields_and_nanosecond_time() -> None:
    row = ChainFlowRow(
        dir="in",
        symbol="SAND",
        ts=T0,
        log_index=42,
        amount=323679.07,
        counterparty="0x" + "21" * 20,
        addr="0x" + "4b" * 20,
        tx_hash="0x" + "7d" * 32,
        block=26_147_676,
    )
    line = chain_flow_line(row)
    assert line.startswith("chain_flow,dir=in,exchange=upbit,network=eth,symbol=SAND ")
    assert line.endswith(f" {T0 * 1_000_000_000 + 42}")
    p = parse_flow_line(line)
    assert p == {
        "measurement": "chain_flow",
        "dir": "in",
        "exchange": "upbit",
        "network": "eth",
        "symbol": "SAND",
        "addr": row.addr,
        "amount": 323679.07,
        "block": 26_147_676,
        "counterparty": row.counterparty,
        "log_index": 42,
        "removed": False,
        "tx_hash": row.tx_hash,
        "ts_ns": chain_flow_ns(T0, 42),
    }
    # 되돌림은 같은 머리·같은 시각에 removed 만 참 — Influx 가 같은 점을 덮는다
    removed = chain_flow_line(dataclasses.replace(row, removed=True))
    assert removed.replace("removed=true", "removed=false") == line
