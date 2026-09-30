"""무거운 조회 게이트 — premium·streaks·bulk 동시 1개, 돌고 있으면 곧바로 429 `busy` (005 §3.4, 2026-09-28).

가벼운 조회(봉·사건)는 게이트와 무관하다. 게이트는 요청이 아니라 스레드 조회가 끝날 때 열린다.
"""

import asyncio
import threading
from collections.abc import Iterator

import httpx

from app.features.history.tests.helpers import FakeInfluxReader, make_client

T0 = 1_700_000_000


class _Blocking(FakeInfluxReader):
    """streaks 조회가 스레드 안에서 `gate` 가 열릴 때까지 멈춘다 — 느린 Influx 흉내. `done` 은 끝난 조회 수."""

    def __init__(self) -> None:
        super().__init__()
        self.seed("upbit", "binance", "BTC", [(T0 + i, 1.0, -1.0) for i in range(10)])
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.done = 0

    def stream_premium(self, **kw: object) -> Iterator[tuple[str, str, int, float]]:  # type: ignore[override]
        self.entered.set()
        self.gate.wait(timeout=5)
        try:
            yield from super().stream_premium(**kw)  # type: ignore[arg-type]
        finally:
            self.done += 1


STREAKS = ("/history/streaks", {"base": "BTC", "start": T0, "end": T0 + 60})
HEAVY = [
    STREAKS,
    ("/history/streaks/bulk", {"start": T0, "end": T0 + 60}),
    ("/history/premium", {"base": "BTC", "unit": "week", "date": "2023-11-15"}),
]


async def _until(check, what: str) -> None:  # noqa: ANN001
    for _ in range(500):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(what)


def _client(reader: FakeInfluxReader) -> tuple[httpx.AsyncClient, object]:
    app = make_client(reader).app
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t"), app


async def test_heavy_routes_answer_429_busy_while_one_runs_and_light_routes_still_answer() -> (
    None
):
    reader = _Blocking()
    reader.seed_candle("candles_1m", T0 - T0 % 60)
    client, _ = _client(reader)
    async with client:
        first = asyncio.create_task(client.get(STREAKS[0], params=STREAKS[1]))
        await _until(reader.entered.is_set, "첫 조회가 시작되지 않았다")
        for path, params in HEAVY:
            res = await client.get(path, params=params)
            assert res.status_code == 429, path
            # 다시 부를 때를 머리로도 알린다 — 조회 하나가 끝나기를 기다리면 된다
            assert res.headers["retry-after"] == "1", path
            assert res.json() == {
                "error": {
                    "code": "busy",
                    "message": "다른 긴 기록 조회가 돌고 있습니다 — 잠시 뒤 다시 요청하세요.",
                    "detail": None,
                }
            }
        # 가벼운 조회는 게이트를 거치지 않는다
        res = await client.get(
            "/history/candles",
            params={"base": "BTC", "start": T0 - T0 % 60, "end": T0 - T0 % 60 + 3_600},
        )
        assert res.status_code == 200 and res.json()["count"] == 1
        res = await client.get("/history/events", params={"start": T0, "end": T0 + 60})
        assert res.status_code == 200
        reader.gate.set()
        done = await first
        assert done.status_code == 200 and "retry-after" not in done.headers
        # 끝나면 다음 무거운 조회를 받는다
        res = await client.get(STREAKS[0], params=STREAKS[1])
        assert res.status_code == 200


async def test_the_gate_stays_closed_until_the_query_thread_ends_even_if_the_request_is_cancelled() -> (
    None
):
    reader = _Blocking()
    client, app = _client(reader)
    async with client:
        first = asyncio.create_task(client.get(STREAKS[0], params=STREAKS[1]))
        await _until(reader.entered.is_set, "첫 조회가 시작되지 않았다")
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        # 요청은 끊겼지만 조회 스레드는 아직 돈다 — 문은 닫혀 있다
        res = await client.get(STREAKS[0], params=STREAKS[1])
        assert res.status_code == 429
        reader.gate.set()
        await _until(lambda: not app.state.history_heavy.busy, "문이 열리지 않았다")  # type: ignore[attr-defined]
        assert reader.done == 1
        res = await client.get(STREAKS[0], params=STREAKS[1])
        assert res.status_code == 200


async def test_a_failing_heavy_query_opens_the_gate() -> None:
    reader = _Blocking()
    reader.gate.set()
    reader.fail = True
    client, _ = _client(reader)
    async with client:
        res = await client.get(STREAKS[0], params=STREAKS[1])
        assert res.status_code == 503
        reader.fail = False
        res = await client.get(STREAKS[0], params=STREAKS[1])
        assert res.status_code == 200
