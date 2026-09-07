"""틱 루프·판정 — 자격·원값 수식·인계·received_at·동기성 (스펙 001 §3.6·§3.8, §4)."""

import asyncio
import inspect
from datetime import UTC, datetime

import httpx
import pytest

from app.core.live_store import LiveStore
from app.core.models import Rate, Row, StreamError, StreamState, Tick
from app.core.premium import premium_percent
from app.core.ticks import STALE_AFTER_MS, TickLoop, build_tick, judge_state
from tests.conftest import FakeStream, make_row

NOW = datetime.now(UTC)
URL = "wss://api.upbit.com/websocket/v1"
T0 = 1_787_000_000


def _client() -> httpx.AsyncClient:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"테스트에서 네트워크 호출 발생: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(fail))


def seeded() -> LiveStore:
    store = LiveStore()
    store.put_rows(
        [
            make_row("upbit", "BTC", bids=[[100_000.0, 1.0]], asks=[[100_100.0, 1.0]]),
            make_row("upbit", "ETH"),
            make_row(
                "bithumb", "BTC", bids=[[100_050.0, 1.0]], asks=[[100_150.0, 1.0]]
            ),
            make_row("bithumb", "XRP"),
            make_row("binance", "BTC", bids=[[70.0, 1.0]], asks=[[71.0, 1.0]]),
            make_row("binance", "XRP"),
            make_row("binance", "SOL"),
        ],
        NOW,
    )
    store.set_rate("upbit", 1400.0, 1390.0, NOW)
    return store


def test_build_tick_eligibility_and_raw_formulas() -> None:
    store = seeded()
    tick = build_tick(store, T0, ["upbit"])
    assert tick.ts == T0 and tick.dw_failed == ("upbit",)
    # 빗썸은 자기 시세가 없어 전부 빠진다. ETH·SOL 은 한쪽 상장. → upbit×binance BTC 하나
    assert [(r.dom, r.fx, r.base) for r in tick.rows] == [("upbit", "binance", "BTC")]
    [row] = tick.rows
    assert row.fwd == pytest.approx(
        premium_percent(buy_krw=71.0 * 1400.0, sell_krw=100_000.0)
    )
    assert row.rev == pytest.approx(
        premium_percent(buy_krw=100_100.0, sell_krw=70.0 * 1390.0)
    )
    # 빗썸 시세가 생기면 빗썸×바이낸스 BTC·XRP 가 더해진다 (정렬: dom, fx, base)
    store.set_rate("bithumb", 1410.0, 1405.0, NOW)
    rows = build_tick(store, T0, []).rows
    assert [(r.dom, r.base) for r in rows] == [
        ("bithumb", "BTC"),
        ("bithumb", "XRP"),
        ("upbit", "BTC"),
    ]


def test_build_tick_row_carries_prices_and_wallet_states_for_candles() -> None:
    """014 §3.2 — 가격 3개·입출금 4상태가 그 순간의 행에서 실리고, rate 는 (ask+bid)/2."""
    store = seeded()
    dom = make_row(
        "upbit",
        "BTC",
        price=100_050.0,
        bids=[[100_000.0, 1.0]],
        asks=[[100_100.0, 1.0]],
    )
    fx = make_row("binance", "BTC", price=70.5, bids=[[70.0, 1.0]], asks=[[71.0, 1.0]])
    store.put_rows([dom, fx], NOW)
    # 입출금 3필드는 교체 시 직전 행에서 물려받으므로(001 §3.5) 006 조회기처럼 저장된 행에 직접 얹는다
    dom.deposit_enabled, dom.withdrawal_enabled = True, False
    fx.deposit_enabled, fx.withdrawal_enabled = None, True
    [row] = build_tick(store, T0, []).rows
    assert (row.dom_price, row.fx_price, row.rate) == (100_050.0, 70.5, 1395.0)
    assert (row.dom_dep, row.dom_wd, row.fx_dep, row.fx_wd) == (True, False, None, True)


def test_build_tick_skips_non_positive_values_and_empty_books() -> None:
    store = seeded()
    store.put_rows([make_row("binance", "BTC", bids=[[0.0, 1.0]])], NOW)
    assert build_tick(store, T0, []).rows == ()
    store.put_rows([make_row("binance", "BTC", bids=[], asks=[[71.0, 1.0]])], NOW)
    assert build_tick(store, T0, []).rows == ()
    store.set_rate("upbit", 0.0, 1390.0, NOW)
    store.put_rows([make_row("binance", "BTC")], NOW)
    assert build_tick(store, T0, []).rows == ()


def test_tick_is_synchronous_and_hands_off_previous_tick() -> None:
    handed: list[Tick] = []
    store = seeded()
    loop = TickLoop(store=store, streams=[], client=_client(), handoff=handed.append)
    assert not inspect.iscoroutinefunction(TickLoop.tick)  # await 없는 동기 계산
    first = loop.tick(T0)
    assert isinstance(first, Tick) and handed == []  # 첫 틱은 인계 없음
    assert store.received_at == T0 and store.tick is first
    second = loop.tick(T0 + 1)
    assert handed == [first]  # 두 번째 틱에서 첫 틱이 인계된다
    assert store.received_at == T0 + 1 and store.tick is second


class ReentrancyStore(LiveStore):
    """저장소 호출마다 이벤트 루프 회전 수를 적는다 — 틱 도중 다른 태스크가 돌면 값이 달라진다."""

    def __init__(self) -> None:
        super().__init__()
        self.turns = 0
        self.seen: list[int] = []

    def get_all(
        self, exchange: str | None = None, base: str | None = None
    ) -> list[Row]:
        self.seen.append(self.turns)
        return super().get_all(exchange, base)

    def rates(self) -> dict[str, Rate]:
        self.seen.append(self.turns)
        return super().rates()

    def push_tick(self, tick: Tick) -> Tick | None:
        self.seen.append(self.turns)
        return super().push_tick(tick)

    def mark_received(self, ts: int) -> None:
        self.seen.append(self.turns)
        super().mark_received(ts)


async def test_tick_does_not_yield_to_other_tasks_while_building() -> None:
    store = ReentrancyStore()
    store.put_rows(seeded().get_all(), NOW)
    store.set_rate("upbit", 1400.0, 1390.0, NOW)

    async def spin() -> None:
        while True:
            store.turns += 1
            await asyncio.sleep(0)

    spinner = asyncio.create_task(spin())
    await asyncio.sleep(0.01)  # 스피너가 돌고 있다
    loop = TickLoop(store=store, streams=[], client=_client())
    tick = loop.tick(T0)
    spinner.cancel()
    assert tick.rows and len(store.seen) >= 4
    # 읽기·슬롯·received_at 사이에 이벤트 루프가 한 번도 돌지 않았다
    assert len(set(store.seen)) == 1


async def test_wallet_refresh_task_is_not_duplicated_while_pending() -> None:
    release = asyncio.Event()

    class Wallet:
        calls = 0

        async def refresh_if_due(
            self, client: httpx.AsyncClient, *, force: bool = False
        ) -> dict[str, int] | None:
            self.calls += 1
            await release.wait()
            return None

        def apply(self, rows: list[object], exchange: str) -> None:
            pass

        def availability(self) -> dict[str, bool]:
            return {}

        def warnings(self) -> list[str]:
            return []

        def failed(self) -> list[str]:
            return []

    wallet = Wallet()
    times = iter([T0 + 0.5, T0 + 1.5, T0 + 2.5])
    slept = 0
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        nonlocal slept
        slept += 1
        if slept == 3:
            stop.set()
            await asyncio.Event().wait()
        await asyncio.sleep(0)  # 조회 태스크가 첫 await(release 대기)까지 돈다

    loop = TickLoop(
        store=seeded(),
        streams=[],
        client=_client(),
        wallet=wallet,  # type: ignore[arg-type]
        clock=lambda: next(times),
        sleep=sleep,
    )
    loop.start()
    await asyncio.wait_for(stop.wait(), 1.0)
    # 두 틱이 지났지만 직전 조회 태스크가 안 끝나 새 태스크를 만들지 않았다
    assert wallet.calls == 1
    release.set()
    await loop.aclose()


async def test_aclose_hands_off_last_tick_and_run_ticks_on_second_boundary() -> None:
    handed: list[Tick] = []
    store = seeded()
    times = iter([T0 + 0.25, T0 + 1.3])
    slept: list[float] = []
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 2:
            stop.set()
            await asyncio.Event().wait()

    loop = TickLoop(
        store=store,
        streams=[],
        client=_client(),
        handoff=handed.append,
        clock=lambda: next(times),
        sleep=sleep,
    )
    loop.start()
    await asyncio.wait_for(stop.wait(), 1.0)
    assert slept[0] == pytest.approx(0.75)  # 다음 초 경계까지
    assert store.tick is not None and store.tick.ts == T0 + 1
    await loop.aclose()
    assert handed == [store.tick]  # 종료 시 슬롯의 틱 인계


def test_handoff_receives_immutable_tick_rows() -> None:
    store = seeded()
    tick = build_tick(store, T0, [])
    assert isinstance(tick.rows, tuple)
    with pytest.raises(AttributeError):
        tick.ts = 1  # type: ignore[misc]


def _state(**kw: object) -> StreamState:
    return StreamState(**kw)  # type: ignore[arg-type]


def test_judge_state_rules() -> None:
    now = T0 * 1000
    assert judge_state(_state(), now, URL) is None  # 연결 시도 전
    err = StreamError("network", "dns", None, URL)
    v = judge_state(_state(last_error=err), now, URL)
    assert v is not None and not v.ok and v.error is err
    v = judge_state(
        _state(last_error=StreamError("rate_limit", "429", 429, URL, 5)), now, URL
    )
    assert v is not None and v.error is not None and v.error.kind == "rate_limit"
    ok = judge_state(_state(connected=True, last_message_at=now - 1000), now, URL)
    assert ok is not None and ok.ok
    stale = judge_state(
        _state(connected=True, last_message_at=now - STALE_AFTER_MS), now, URL
    )
    assert stale is not None and stale.error is not None
    assert (stale.error.kind, stale.error.url, stale.error.status_code) == (
        "stale_stream",
        URL,
        None,
    )
    # 연결됐고 아직 시세가 없으면 구독 시각부터 센다
    fresh = judge_state(_state(connected=True, connected_since=now - 100), now, URL)
    assert fresh is not None and fresh.ok
    quiet = judge_state(
        _state(connected=True, connected_since=now - STALE_AFTER_MS), now, URL
    )
    assert quiet is not None and not quiet.ok
    # 오래 끊겼다가 재연결한 직후(직전 수신 60초 전·구독 1초 전) 첫 프레임 전은 성공
    reconnected = judge_state(
        _state(
            connected=True, last_message_at=now - 60_000, connected_since=now - 1_000
        ),
        now,
        URL,
    )
    assert reconnected is not None and reconnected.ok
    # 구독이 오래됐어도 최근에 시세를 받았으면 성공 — 둘 중 최신이 기준
    active = judge_state(
        _state(
            connected=True, last_message_at=now - 1_000, connected_since=now - 60_000
        ),
        now,
        URL,
    )
    assert active is not None and active.ok
    # 시세를 받았다가 오류 기록 없이 닫히면 network
    closed = judge_state(_state(last_message_at=now - 5000), now, URL)
    assert closed is not None and closed.error is not None
    assert closed.error.kind == "network"


def test_judgement_recovers_when_messages_return() -> None:
    class Sink:
        def __init__(self) -> None:
            self.log: list[tuple[str, str, str | None]] = []

        def record_success(self, exchange: str, at_ms: int) -> None:
            self.log.append((exchange, "ok", None))

        def record_failure(self, exchange: str, at_ms: int, **kw: object) -> None:
            self.log.append((exchange, "fail", str(kw["kind"])))

    sink = Sink()
    upbit = FakeStream("upbit")
    loop = TickLoop(store=LiveStore(), streams=[upbit], client=_client(), outages=sink)
    loop.tick(T0)  # 판정 대상 아님 — 기록 없음
    upbit.fail("network")
    loop.tick(T0 + 1)
    upbit.succeed()
    loop.tick(T0 + 2)
    assert sink.log == [("upbit", "fail", "network"), ("upbit", "ok", None)]
