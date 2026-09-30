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


def seed_events(reader: FakeInfluxReader, n: int) -> None:
    for i in range(n):
        reader.seed_event(
            f"C{i % 97}",
            T0 + i * 13,
            T0 + i * 13 + 90,
            dom=("upbit", "bithumb")[i % 2],
            dir=("kimp", "reverse")[i % 3 == 0],
            max_percent=1.0 + (i % 17) * 0.137,
            net_dom=("Ethereum", None)[i % 4 == 0],
            net_fx=("ERC20", None, "")[i % 3] or None,
        )
    # 고아 점 — last_ts 로 닫힌다
    reader.seed_event("ORPHAN", T0 + 5, 0, last_ts=T0 + 400)


def test_events_bytes_match_the_model_path() -> None:
    now = T0 + 50_000
    det = detector_with_open("SOPH", "C3", start_ts=T0 + 1_000, now=now)
    for n in (0, ENCODE_CHUNK - 1, ENCODE_CHUNK, 4_321):
        reader = FakeInfluxReader()
        seed_events(reader, n)
        for filters in (
            {},
            {"dom": "upbit"},
            {"dir": "kimp"},
            {"dir": "reverse", "base": "c3"},
        ):
            kw = {
                "start": T0,
                "end": T0 + 60_000,
                "dom": None,
                "dir": None,
                "base": None,
            }
            kw.update(filters)
            opened = det.open_events()
            want = legacy(build_events(reader, opened, now_sec=now, **kw))  # type: ignore[arg-type]
            got = encode_events(reader, opened, now_sec=now, **kw)  # type: ignore[arg-type]
            assert masked(got) == want, (n, filters)


def test_events_route_serves_the_encoded_bytes() -> None:
    reader = FakeInfluxReader()
    seed_events(reader, 2_500)
    want = legacy(
        build_events(
            reader, [], start=T0, end=T0 + 60_000, dom=None, dir="kimp", base=None
        )
    )
    res = make_client(reader).get(
        "/history/events", params={"start": T0, "end": T0 + 60_000, "dir": "kimp"}
    )
    assert masked(res.content) == want


def seed_candles(reader: FakeInfluxReader, n: int) -> None:
    # 저장값이 −1·0·1 밖이어도(−2·2) 모델 경로의 3상태 변환과 같아야 한다
    tri = (-2, -1, 0, 1, 2)
    for i in range(n):
        reader.seed_candle(
            "candles_1m",
            T0 - T0 % 60 + (n - i) * 60,  # 역순으로 넣어도 ts 오름차순
            fwd=(0.5 + i * 0.01, 0.9, 0.4, -0.0),
            rev=(-0.5, -0.4, -0.9 - i * 1e-9, -0.7),
            dw=(tri[i % 5], tri[(i + 1) % 5], tri[(i + 2) % 5], tri[(i + 3) % 5]),
            blocked=(i % 61, (i * 7) % 61),
            samples=60 - i % 3,
            net_dom=("Ethereum", None)[i % 2],
            net_fx=(None, "ERC20")[i % 3 == 0],
        )


def test_candles_bytes_match_the_model_path_both_directions() -> None:
    start = T0 - T0 % 60
    for n in (0, 360, 1_440):
        reader = FakeInfluxReader()
        seed_candles(reader, n)
        for direction in ("kimp", "reverse"):
            kw = {
                "base": "btc",
                "res": "1m",
                "dom": "upbit",
                "fx": "binance",
                "dir": direction,
                "start": start,
                "end": start + 86_400,
            }
            want = legacy(build_candles(reader, now_sec=T0, **kw))  # type: ignore[arg-type]
            assert masked(encode_candles(reader, now_sec=T0, **kw)) == want  # type: ignore[arg-type]
            res = make_client(reader).get(
                "/history/candles",
                params={k: v for k, v in kw.items() if k != "base"} | {"base": "btc"},
            )
            assert masked(res.content) == want, (n, direction)


def test_streaks_and_bulk_routes_serve_the_model_bytes() -> None:
    reader = FakeInfluxReader()
    for base in ("BTC", "ETH", "XRP"):
        reader.seed(
            "upbit",
            "binance",
            base,
            [
                (T0 + i * 60, [0, 1, 3, 6, 29, 4, 31][i % 7] * 0.5, -0.1 * i)
                for i in range(500)
            ],
        )
    client = make_client(reader)
    streaks = build_streaks(
        reader,
        dom="upbit",
        fx="binance",
        base="BTC",
        threshold=1.0,
        start=T0,
        end=T0 + 40_000,
        max_gap=600,
    )
    res = client.get(
        "/history/streaks",
        params={"base": "BTC", "threshold": 1.0, "start": T0, "end": T0 + 40_000},
    )
    assert masked(res.content) == legacy(streaks)
    bulk = build_bulk(
        reader,
        dom="upbit",
        fx="binance",
        threshold=1.0,
        start=T0,
        end=T0 + 3_600,
        max_gap=600,
    )
    res = client.get(
        "/history/streaks/bulk",
        params={"threshold": 1.0, "start": T0, "end": T0 + 3_600},
    )
    assert masked(res.content) == legacy(bulk)


# --- 같은 오류 ---


def legacy_error(build) -> tuple[int, bytes]:  # noqa: ANN001
    """이 변경 전 라우터의 오류 응답 — 같은 빌드 함수가 낸 예외를 같은 형식으로."""
    try:
        build()
    except HistoryApiError as exc:
        status, code, message, detail = (
            exc.http_status,
            exc.code,
            exc.message,
            exc.detail,
        )
    except InfluxUnavailableError as exc:
        status, code = 503, "storage_unavailable"
        message, detail = f"저장소 조회에 실패했습니다: {exc}", None
    else:
        raise AssertionError("오류가 나야 하는 입력")
    body = {"error": {"code": code, "message": message, "detail": detail}}
    return status, JSONResponse(status_code=status, content=camelize_json(body)).body


def test_error_responses_are_the_same_as_the_model_path() -> None:
    empty = FakeInfluxReader()
    failing = FakeInfluxReader()
    failing.fail = True
    week = {"dom": "upbit", "fx": "binance", "base": "BTC", "unit": "week"}
    candle = {"base": "BTC", "dom": "upbit", "fx": "binance", "dir": "kimp"}
    cases = [
        (empty, "/history/premium", {"base": "BTC", "unit": "week", "date": "2023-02-30"},
         lambda: build_premium_history(empty, date_str="2023-02-30", **week)),
        (empty, "/history/premium", {"base": "BTC", "unit": "week", "date": "2023-11-15"},
         lambda: build_premium_history(empty, date_str="2023-11-15", **week)),
        (failing, "/history/premium", {"base": "BTC", "unit": "week", "date": "2023-11-15"},
         lambda: build_premium_history(failing, date_str="2023-11-15", **week)),
        (empty, "/history/streaks", {"base": "BTC", "start": T0, "end": T0 + 10},
         lambda: build_streaks(empty, dom="upbit", fx="binance", base="BTC", threshold=0,
                               start=T0, end=T0 + 10, max_gap=600)),
        (empty, "/history/streaks", {"base": "BTC", "start": T0, "end": T0},
         lambda: build_streaks(empty, dom="upbit", fx="binance", base="BTC", threshold=0,
                               start=T0, end=T0, max_gap=600)),
        (empty, "/history/streaks/bulk", {"start": T0, "end": T0 - 1},
         lambda: build_bulk(empty, dom="upbit", fx="binance", threshold=0, start=T0,
                            end=T0 - 1, max_gap=600)),
        (empty, "/history/events", {"start": T0, "end": T0},
         lambda: build_events(empty, [], start=T0, end=T0, dom=None, dir=None, base=None)),
        (failing, "/history/events", {"start": T0, "end": T0 + 60},
         lambda: build_events(failing, [], start=T0, end=T0 + 60, dom=None, dir=None, base=None)),
        (empty, "/history/candles", {**candle, "res": "1m", "start": T0, "end": T0 + 86_401},
         lambda: build_candles(empty, res="1m", start=T0, end=T0 + 86_401, **candle)),
        (empty, "/history/candles", {**candle, "res": "5m", "start": T0, "end": T0},
         lambda: build_candles(empty, res="5m", start=T0, end=T0, **candle)),
        (failing, "/history/candles", {**candle, "res": "1h", "start": T0, "end": T0 + 3_600},
         lambda: build_candles(failing, res="1h", start=T0, end=T0 + 3_600, **candle)),
    ]  # fmt: skip
    for reader, path, params, build in cases:
        status, body = legacy_error(build)
        res = make_client(reader).get(path, params=params)
        assert (res.status_code, res.content) == (status, body), (path, params)


def test_no_storage_is_the_same_503_on_every_route() -> None:
    body = JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "storage_unavailable",
                "message": "저장소를 쓸 수 없습니다 — INFLUX_TOKEN 이 설정되지 않았습니다.",
                "detail": None,
            }
        },
    ).body
    client = make_client(None)
    for path, params in (
        ("/history/premium", {"base": "BTC", "unit": "week"}),
        ("/history/streaks", {"base": "BTC"}),
        ("/history/streaks/bulk", {}),
        ("/history/events", {}),
        ("/history/candles", {"base": "BTC"}),
    ):
        res = client.get(path, params=params)
        assert (res.status_code, res.content) == (503, body), path


# --- 루프 밖에서 ---


async def test_json_encoding_runs_off_the_event_loop(monkeypatch) -> None:  # noqa: ANN001
    """조회·빌드·인코딩은 스레드에서 — 이벤트 루프가 도는 스레드(여기선 테스트 스레드)에서는 한 번도 없다."""
    threads: list[threading.Thread] = []
    real = service.render_json

    def recording(content: object) -> bytes:
        threads.append(threading.current_thread())
        return real(content)

    monkeypatch.setattr(service, "render_json", recording)
    reader = FakeInfluxReader()
    seed_premium(reader, 3_000)
    seed_events(reader, 3_000)
    seed_candles(reader, 360)
    app = make_client(reader).app
    loop_thread = threading.current_thread()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        for path, params in (
            ("/history/premium", {"base": "BTC", "unit": "week", "date": "2023-11-15"}),
            (
                "/history/streaks",
                {"base": "BTC", "start": WEEK_START, "end": T0 + 60_000},
            ),
            ("/history/streaks/bulk", {"start": T0, "end": T0 + 3_600}),
            ("/history/events", {"start": T0, "end": T0 + 60_000}),
            (
                "/history/candles",
                {"base": "BTC", "start": T0 - T0 % 60, "end": T0 - T0 % 60 + 86_400},
            ),
        ):
            before = len(threads)
            res = await client.get(path, params=params)
            assert res.status_code == 200, path
            assert len(threads) > before, path
    assert threads and all(t is not loop_thread for t in threads)
