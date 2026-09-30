"""Influx 클라이언트의 줄 쓰기·복원 조회·망 이름 표식 — 스펙 009 §3.5·§3.6, 013 §3.3, 014 §3.4, 024 §3.4 (2026-09-28).

influxdb-client 자리에 가짜를 꽂아 요청 본문·Flux·CSV 변환만 본다 — 네트워크 없음.
"""

import random
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.core.influx as influx_mod
from app.core.influx import (
    CandleRow,
    InfluxClient,
    PremiumEventRow,
    SparkBucketRow,
    candle_head,
    candle_line,
    candle_point,
    dw_fail_point,
    premium_event_point,
    premium_head,
    premium_line,
    premium_point,
    to_line,
)

T0 = 1_787_000_000


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Fake:
    """InfluxDBClient 자리 — 생성 인자, 쓰기 본문, query_raw 로 받은 Flux 를 남기고 정해 둔 CSV 를 차례로 준다."""

    def __init__(self, log: dict[str, list], csv_texts: list[str]) -> None:
        self._log = log
        self._csv = csv_texts

    def write_api(self, write_options: object) -> "_Fake":
        return self

    def __enter__(self) -> "_Fake":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, *, bucket: str, record: object, write_precision: object) -> None:
        self._log["writes"].append((bucket, record))

    def query_api(self) -> SimpleNamespace:
        def query_raw(flux: str, dialect: object) -> SimpleNamespace:
            self._log["flux"].append(flux)
            text = self._csv.pop(0)
            return SimpleNamespace(data=text.encode(), release_conn=lambda: None)

        return SimpleNamespace(query_raw=query_raw)


def _client(
    monkeypatch: pytest.MonkeyPatch, csv_texts: list[str] | None = None
) -> tuple[InfluxClient, dict[str, list]]:
    log: dict[str, list] = {"created": [], "writes": [], "flux": []}
    texts = list(csv_texts or [])

    def make(**kw: object) -> _Fake:
        log["created"].append(kw["timeout"])
        return _Fake(log, texts)

    monkeypatch.setattr(influx_mod, "InfluxDBClient", make)
    return InfluxClient(url="http://influx.test", token="t"), log


# ── 줄 쓰기 (009 §3.5·014 §3.4) ────────────────────────────────────────────────


def test_write_lines_posts_the_same_body_as_writing_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points = [
        premium_point(dom="upbit", fx="binance", base="btc", ts=T0, fwd=1.5, rev=-0.25),
        dw_fail_point(exchange="upbit", ts=T0),
    ]
    client, log = _client(monkeypatch)
    client.write(points)
    client.write_lines([to_line(p) for p in points], "candles_1m")
    client.write_lines([])  # 빈 목록은 요청하지 않는다
    # 본문 = 줄마다 UTF-8 로 바꿔 개행으로 이은 바이트(influxdb-client 에 줄 목록을 넘기던 때와 같다)
    body = b"\n".join(to_line(p).encode() for p in points)
    assert log["writes"] == [("marketlens", body), ("candles_1m", body)]


def test_write_lines_body_matches_the_library_serializing_a_line_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실물 influxdb-client 가 줄 목록(고치기 전 `write` 가 넘기던 모양)으로 만드는 본문과 `write_lines` 본문이 같다.

    요청 직전(`_post_write`)에서 본문만 가로챈다 — 네트워크 없음.
    """
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS, WriteApi

    bodies: list[bytes] = []

    def capture(
        self: WriteApi,
        _async_req: bool,
        bucket: str,
        org: str,
        body: bytes,
        precision: object,
        **kw: object,
    ) -> None:
        bodies.append(body)

    monkeypatch.setattr(WriteApi, "_post_write", capture)
    lines = [
        premium_line(premium_head("upbit", "binance", "A,B C"), 1.5, -0.0, T0),
        to_line(dw_fail_point(exchange="upbit", ts=T0)),
        candle_line(
            candle_head("bithumb", "bybit", "BTC"),
            T0,
            **{
                k: 1.0
                for k in (
                    "fwd_o",
                    "fwd_h",
                    "fwd_l",
                    "fwd_c",
                    "rev_o",
                    "rev_h",
                    "rev_l",
                    "rev_c",
                    "krw",
                    "usdt",
                    "rate",
                )
            },
            **{
                k: 1
                for k in (
                    "dom_dep",
                    "dom_wd",
                    "fx_dep",
                    "fx_wd",
                    "blocked_fwd_sec",
                    "blocked_rev_sec",
                    "samples",
                )
            },
            net_dom='망 "이름"',
            net_fx=None,
        ),
    ]
    client = InfluxClient(url="http://influx.test", token="t")
    client.write_lines(lines)
    client.close()
    with InfluxDBClient(url="http://influx.test", token="t", org="marketlens") as lib:
        with lib.write_api(write_options=SYNCHRONOUS) as api:
            api.write(bucket="marketlens", record=lines, write_precision="s")
    assert len(bodies) == 2 and bodies[0] == bodies[1]


def test_premium_line_matches_point_serialization() -> None:
    rng = random.Random(11)
    names = ["BTC", "eth", "A,B", "C D", "E=F", "G\\H"]
    for i in range(500):
        dom, fx, base = ("upbit", "bybit", names[i % len(names)])
        fwd = rng.choice([0.0, -0.0, 1e-9, rng.uniform(-9, 9)])
        rev = rng.uniform(-9, 9)
        line = premium_line(premium_head(dom, fx, base), fwd, rev, T0 + i)
        assert line == to_line(
            premium_point(dom=dom, fx=fx, base=base, ts=T0 + i, fwd=fwd, rev=rev)
        )


def test_candle_line_matches_candle_point_serialization() -> None:
    """누적에서 바로 만든 봉 줄이 봉 행 → 점 → `to_line` 과 바이트가 같다(망 없음·따옴표·역슬래시 포함)."""
    rng = random.Random(13)
    nets = [None, "", "Ethereum", "BNB Smart Chain (BEP20)", 'Q"uote', "Back\\slash"]
    for i in range(500):
        row = CandleRow(
            dom=("upbit", "bithumb")[i % 2],
            fx=("binance", "bybit", "bitget")[i % 3],
            base=("BTC", "A,B", "C D", "E=F")[i % 4],
            ts=T0 + i * 60,
            fwd_o=rng.uniform(-3, 3),
            fwd_h=rng.choice([0.0, -0.0, 3.5]),
            fwd_l=rng.uniform(-3, 3),
            fwd_c=1e-12,
            rev_o=rng.uniform(-3, 3),
            rev_h=rng.uniform(-3, 3),
            rev_l=-0.0,
            rev_c=rng.uniform(-3, 3),
            krw=rng.choice([100_000.0, 12]),  # 정수 가격도 float 로 적힌다
            usdt=rng.uniform(0, 1e5),
            rate=1_400.5,
            dom_dep=rng.choice([1, 0, -1]),
            dom_wd=rng.choice([1, 0, -1]),
            fx_dep=rng.choice([1, 0, -1]),
            fx_wd=rng.choice([1, 0, -1]),
            blocked_fwd_sec=rng.randrange(61),
            blocked_rev_sec=rng.randrange(61),
            samples=rng.randrange(1, 61),
            net_dom=rng.choice(nets),
            net_fx=rng.choice(nets),
        )
        fields = {
            k: v
            for k, v in row.__dict__.items()
            if k not in ("dom", "fx", "base", "ts")
        }
        line = candle_line(candle_head(row.dom, row.fx, row.base), row.ts, **fields)
        assert line == to_line(candle_point(row))


# ── 망 이름 "없음" = `-` (024 §3.4) ─────────────────────────────────────────────
