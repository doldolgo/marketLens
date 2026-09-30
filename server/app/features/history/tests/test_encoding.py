"""응답 인코딩 경로 — 모델 경로와 같은 바이트·같은 오류·루프 밖 인코딩 (005 §3.4·013 §3.4·014 §3.6, 2026-09-28).

`encode_*`(라우터가 스레드에서 부르는 dict 경로)가 `build_*` → model_dump → camelize → `JSONResponse` 와 같은
바이트인지 본다. `fetchedAt` 은 만든 시각이라 두 쪽 모두 0 으로 바꿔 비교한다.
"""

import re
import threading
from collections.abc import Iterator

import httpx
import pytest
from fastapi.responses import JSONResponse

from app.core.influx import InfluxUnavailableError, PremiumRow
from app.core.models import Tick, TickRow
from app.core.premium_events import PremiumEventDetector
from app.core.serialization import camelize_json
from app.features.history import service
from app.features.history.service import (
    ENCODE_CHUNK,
    HistoryApiError,
    build_bulk,
    build_candles,
    build_events,
    build_premium_history,
    build_streaks,
    encode_candles,
    encode_events,
    encode_premium_history,
)
from app.features.history.tests.helpers import FakeInfluxReader, make_client

WEEK_START = 1_699_833_600  # 2023-11-13 월 00:00 UTC
T0 = 1_700_000_000


def masked(body: bytes) -> bytes:
    return re.sub(rb'"fetchedAt":\d+', b'"fetchedAt":0', body)


def legacy(model) -> bytes:  # noqa: ANN001
    """이 변경 전 라우터가 내던 바이트 — 모델 → camelCase → JSONResponse."""
    return masked(JSONResponse(content=camelize_json(model.model_dump())).body)


# --- 같은 바이트 ---


def seed_premium(reader: FakeInfluxReader, n: int) -> None:
    rows = []
    for i in range(n):
        # 부호·지수 표기·음의 0 이 섞이게 — 부동소수 표기까지 같아야 한다
        fwd = [-0.0, 1e-7, 0.1234567890123, -2.5, 3.0][i % 5] + i * 1e-4
        rows.append((WEEK_START + 30 + i * 7, fwd, -fwd / 3))
    reader.seed("upbit", "binance", "BTC", rows)


def test_premium_bytes_match_the_model_path_across_chunk_boundaries() -> None:
    for n in (1, ENCODE_CHUNK - 1, ENCODE_CHUNK, ENCODE_CHUNK + 1, 4_500):
        reader = FakeInfluxReader()
        seed_premium(reader, n)
        kw = {"dom": "upbit", "fx": "binance", "base": "btc", "unit": "week"}
        want = legacy(build_premium_history(reader, date_str="2023-11-15", **kw))
        got = encode_premium_history(reader, date_str="2023-11-15", **kw)  # type: ignore[arg-type]
        assert masked(got) == want, n
        res = make_client(reader).get(
            "/history/premium",
            params={"base": "btc", "unit": "week", "date": "2023-11-15"},
        )
        assert masked(res.content) == want, n


class SeriesReader:
    """방향마다 따로 든 시리즈 — 한 방향에만 있는 시각(반쪽 점)을 만들 수 있다.

    `query_premium` 은 실물의 pivot 처럼 두 방향이 다 있는 시각만 행으로(반쪽 행은 버린다), `stream_premium` 은
    실물처럼 방향 줄기마다 시각 오름차순으로 흘려보낸다 — rev 줄기가 먼저다(줄기 순서에 기대지 않게).
    """

    def __init__(self, fwd: dict[int, float], rev: dict[int, float]) -> None:
        self.fwd = fwd
        self.rev = rev

    def query_premium(
        self, *, dom: str, fx: str, base: str | None, start: int, stop: int
    ) -> list[PremiumRow]:
        return [
            PremiumRow(base=str(base), ts=ts, fwd=self.fwd[ts], rev=self.rev[ts])
            for ts in sorted(self.fwd)
            if ts in self.rev and start <= ts < stop
        ]

    def stream_premium(
        self, *, dom: str, fx: str, base: str | None, start: int, stop: int
    ) -> Iterator[tuple[str, str, int, float]]:
        for field, series in (("rev", self.rev), ("fwd", self.fwd)):
            for ts in sorted(series):
                if start <= ts < stop:
                    yield str(base), field, ts, series[ts]


def test_premium_streamed_bytes_match_the_list_path_with_half_points() -> None:
    """흘려 읽기 경로가 점 목록 경로와 같은 바이트 — 반쪽 점은 두 경로 모두 건너뛴다(구간 앞·가운데·끝)."""
    fwd: dict[int, float] = {}
    rev: dict[int, float] = {}
    n = 2 * ENCODE_CHUNK + 777
    for i in range(n):
        ts = WEEK_START + 5 + i * 3
        value = [0.0, -0.0, 1e-7, 2.5, -2.5, 0.0, 1e21][i % 7] + (i % 11) * 1e-4
        if i % 13 != 4:
            fwd[ts] = value
        if i % 17 != 9:
            rev[ts] = -value / 3
    fwd[WEEK_START + 1] = 9.0  # 구간 첫 시각은 fwd 만 — 첫 기록은 그다음이다
    rev[WEEK_START + 604_799] = -9.0  # 끝 시각은 rev 만
    fwd[WEEK_START - 1] = rev[WEEK_START - 1] = 5.0  # 구간 밖
    fwd[WEEK_START + 604_800] = rev[WEEK_START + 604_800] = 5.0
    reader = SeriesReader(fwd, rev)
    kw = {"dom": "upbit", "fx": "binance", "base": "btc", "unit": "week"}
    want = legacy(build_premium_history(reader, date_str="2023-11-15", **kw))  # type: ignore[arg-type]

    def no_list(**_: object) -> list[PremiumRow]:
        raise AssertionError("응답 경로는 점 목록을 읽지 않는다")

    reader.query_premium = no_list  # type: ignore[method-assign]
    got = encode_premium_history(reader, date_str="2023-11-15", **kw)  # type: ignore[arg-type]
    assert masked(got) == want
    assert b'"count":' + str(len(fwd.keys() & rev.keys()) - 2).encode() in got
    res = make_client(reader).get(  # type: ignore[arg-type]
        "/history/premium", params={"base": "btc", "unit": "week", "date": "2023-11-15"}
    )
    assert masked(res.content) == want


def test_premium_with_only_half_points_is_404_on_both_paths() -> None:
    reader = SeriesReader({WEEK_START + 10: 1.0}, {WEEK_START + 20: -1.0})
    kw = {"dom": "upbit", "fx": "binance", "base": "BTC", "unit": "week"}
    errors = []
    for build in (build_premium_history, encode_premium_history):
        with pytest.raises(HistoryApiError) as exc:
            build(reader, date_str="2023-11-15", **kw)  # type: ignore[arg-type,operator]
        errors.append((exc.value.http_status, exc.value.message, exc.value.detail))
    assert errors[0] == errors[1] and errors[0][0] == 404


def detector_with_open(*bases: str, start_ts: int, now: int) -> PremiumEventDetector:
    det = PremiumEventDetector()
    rows = tuple(
        TickRow(dom="upbit", fx="binance", base=b, fwd=1.5, rev=-1.0) for b in bases
    )
    det.observe(Tick(ts=start_ts, rows=rows, dw_failed=()))
    det.observe(Tick(ts=now, rows=rows, dw_failed=()))
    return det
