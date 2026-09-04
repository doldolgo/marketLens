"""실패 이력 추적기 — 구간 열기·세기·닫기·kind 전환·보관·Influx 쓰기/복원 (스펙 011 §3.3~3.4, §4)."""

import asyncio
import time

from app.core.collector import Collector
from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.influx import CollectFailRow, InfluxPoint, InfluxUnavailableError
from app.core.live_store import LiveStore
from app.core.outages import RETENTION_MS, OutageTracker
from tests.conftest import FakeConnector, make_row

T0 = 1_700_000_000_000  # epoch ms
SEC = 1_000
URL = "https://api.upbit.com/v1/orderbook"


class FakeInflux:
    """(measurement, tags, ts) 유일키로 필드를 합치는 메모리 저장소 — Influx 의 덮어쓰기 규칙."""

    def __init__(self) -> None:
        self.data: dict[
            tuple[str, frozenset[tuple[str, str]], int], dict[str, object]
        ] = {}
        self.write_calls = 0
        self.fail = False
        self.rows: list[CollectFailRow] = []
        self.query_fail = False
        self.query_delay = 0.0

    def write(self, points: list[InfluxPoint]) -> None:
        self.write_calls += 1
        if self.fail:
            raise InfluxUnavailableError("연결 실패 (테스트)")
        for p in points:
            key = (p.measurement, frozenset(p.tags.items()), p.ts)
            self.data.setdefault(key, {}).update(p.fields)

    def query_collect_fail(self, *, start: int) -> list[CollectFailRow]:
        if self.query_delay:
            time.sleep(self.query_delay)
        if self.query_fail:
            raise InfluxUnavailableError("조회 실패 (테스트)")
        return [r for r in self.rows if r.started_ts >= start]

    def only(self) -> dict[str, object]:
        assert len(self.data) == 1
        return next(iter(self.data.values()))


def fail(
    tracker: OutageTracker,
    ex: str,
    at: int,
    kind: str = "timeout",
    message: str = "m",
    status: int | None = None,
) -> None:
    tracker.record_failure(
        ex,
        at,
        kind=kind,
        message=message,
        status_code=status,
        url=URL,
        retry_after_sec=None,
    )


def test_first_failure_opens_outage_and_repeats_only_count_up() -> None:
    t = OutageTracker()
    fail(t, "upbit", T0, message="first")
    o = t.open_outage("upbit")
    assert o is not None and (o.count, o.started_at, o.ended_at) == (1, T0, None)
    fail(t, "upbit", T0 + SEC, message="second", status=503)
    fail(t, "upbit", T0 + 2 * SEC, message="third", status=502)
    assert (o.count, o.last_failed_at, o.message, o.status_code) == (
        3,
        T0 + 2 * SEC,
        "third",
        502,
    )
    assert len(t.outages()) == 1


def test_two_successes_then_failure_keeps_same_outage_three_closes() -> None:
    t = OutageTracker()
    fail(t, "upbit", T0)
    t.record_success("upbit", T0 + SEC)
    t.record_success("upbit", T0 + 2 * SEC)
    fail(t, "upbit", T0 + 3 * SEC)  # 플래핑 — 같은 구간
    assert len(t.outages()) == 1 and t.open_outage("upbit") is not None
    assert t.open_outage("upbit").count == 2
    t.record_success("upbit", T0 + 4 * SEC)
    t.record_success("upbit", T0 + 5 * SEC)
    assert t.open_outage("upbit") is not None
    t.record_success("upbit", T0 + 6 * SEC)
    assert t.open_outage("upbit") is None
    assert t.outages()[0].ended_at == T0 + 4 * SEC  # 연속 성공의 첫 성공 시각
    assert t.last_success_at("upbit") == T0 + 6 * SEC


def test_kind_change_closes_and_opens_new_outage() -> None:
    t = OutageTracker()
    fail(t, "binance", T0, kind="timeout")
    fail(t, "binance", T0 + SEC, kind="rate_limit", status=429)
    outs = t.outages()  # started_at 내림차순
    assert [o.kind for o in outs] == ["rate_limit", "timeout"]
    assert outs[1].ended_at == T0 + SEC and outs[0].ended_at is None
    assert t.open_outage("binance") is outs[0]


def test_closed_outages_older_than_24h_are_dropped_but_open_stay() -> None:
    t = OutageTracker()
    fail(t, "upbit", T0)
    for i in range(1, 4):
        t.record_success("upbit", T0 + i * SEC)  # T0+1s 에 닫힘
    fail(t, "bithumb", T0 + 2 * SEC)  # 진행 중
    t.record_success("binance", T0 + 2 * SEC + RETENTION_MS)  # 24h 뒤 사이클
    assert [o.exchange for o in t.outages()] == ["bithumb"]


def test_message_is_capped_at_300_chars() -> None:
    t = OutageTracker()
    fail(t, "upbit", T0, message="x" * 400)
    assert len(t.open_outage("upbit").message) == 300


async def test_influx_written_once_on_open_and_once_on_close() -> None:
    influx = FakeInflux()
    t = OutageTracker(writer=influx)
    fail(t, "upbit", T0, kind="rate_limit", status=429, message='{"error":1}')
    await t.flush()
    assert influx.write_calls == 1
    fail(t, "upbit", T0 + SEC, kind="rate_limit", status=429, message="again")
    fail(t, "upbit", T0 + 2 * SEC, kind="rate_limit", status=429, message="again")
    await t.flush()
    assert influx.write_calls == 1  # 연속 실패 중에는 쓰지 않는다
    opened = dict(influx.only())
    assert "ended_ts" not in opened and opened["count"] == 1
    for i in range(3, 6):
        t.record_success("upbit", T0 + i * SEC)
    await t.flush()
    assert influx.write_calls == 2
    closed = influx.only()
    assert closed["ended_ts"] == T0 // 1000 + 3
    assert closed["last_failed_ts"] == T0 // 1000 + 2
    assert closed["count"] == 3
    assert closed["message"] == "again"
    key = next(iter(influx.data))
    assert key == (
        "collect_fail",
        frozenset({("exchange", "upbit"), ("kind", "rate_limit")}),
        T0 // 1000,
    )


async def test_write_failure_is_swallowed() -> None:
    influx = FakeInflux()
    influx.fail = True
    t = OutageTracker(writer=influx)
    fail(t, "upbit", T0)
    await t.flush()  # 예외가 새어 나오지 않는다
    assert influx.write_calls == 1 and t.open_outage("upbit") is not None


def _row(
    ex: str, started: int, *, ended: int | None, kind: str = "timeout", count: int = 5
) -> CollectFailRow:
    return CollectFailRow(
        exchange=ex,
        kind=kind,
        started_ts=started,
        count=count,
        last_failed_ts=started + count - 1,
        status_code=None,
        message="m",
        url=URL,
        retry_after_sec=None,
        ended_ts=ended,
    )


async def test_restore_fills_memory_and_open_points_continue() -> None:
    influx = FakeInflux()
    s0 = T0 // 1000
    influx.rows = [
        _row("upbit", s0 - 3600, ended=s0 - 3500),
        _row("binance", s0 - 60, ended=None, kind="rate_limit"),
        _row(
            "bithumb", s0 - 30 * 3600, ended=s0 - 29 * 3600
        ),  # 24시간 밖 — 조회 범위 밖
    ]
    t = OutageTracker(writer=influx)
    await t.restore(influx, T0)
    assert [(o.exchange, o.ended_at is None) for o in t.outages()] == [
        ("binance", True),
        ("upbit", False),
    ]
    open_ = t.open_outage("binance")
    assert open_ is not None and open_.count == 5
    fail(t, "binance", T0, kind="rate_limit")  # 이어서 센다
    assert open_.count == 6 and open_.started_at == (s0 - 60) * 1000
    # 첫 사이클부터 성공 3회면 닫힘 규칙대로 닫힌다
    t2 = OutageTracker(writer=influx)
    await t2.restore(influx, T0)
    for i in range(3):
        t2.record_success("binance", T0 + i * SEC)
    assert t2.open_outage("binance") is None and t2.outages()[0].ended_at == T0


async def test_restore_without_influx_or_on_error_or_timeout_starts_empty(
    monkeypatch,
) -> None:
    t = OutageTracker()
    await t.restore(None, T0)
    assert t.outages() == []

    influx = FakeInflux()
    influx.query_fail = True
    influx.rows = [_row("upbit", T0 // 1000 - 10, ended=None)]
    t = OutageTracker(writer=influx)
    await t.restore(influx, T0)
    assert t.outages() == []

    monkeypatch.setattr("app.core.outages.RESTORE_TIMEOUT_SEC", 0.05)
    influx = FakeInflux()
    influx.query_delay = 0.3
    influx.rows = [_row("upbit", T0 // 1000 - 10, ended=None)]
    t = OutageTracker(writer=influx)
    await t.restore(influx, T0)
    assert t.outages() == []


# ── 수집 사이클 → 추적기 연결 (011 §2 바꾸는 것 2) ─────────────────────────────


async def test_cycle_feeds_tracker_with_connector_kind_and_body(unused_client) -> None:
    store = LiveStore()
    upbit = FakeConnector("upbit", [[make_row("upbit", "BTC")]])
    bithumb = FakeConnector(
        "bithumb",
        [
            ExchangeApiError(
                "bithumb",
                URL,
                "비-200",
                status_code=429,
                body='{"error":429}',
                kind="rate_limit",
                retry_after_sec=2,
            )
        ],
    )
    binance = FakeConnector("binance", [ExchangeTimeoutError("binance", URL, "느림")])
    t = OutageTracker()
    collector = Collector(
        store=store,
        domestic=[upbit, bithumb],
        foreign=binance,
        client=unused_client,
        outages=t,
    )
    result = await collector.run_cycle()
    assert t.last_success_at("upbit") == result.fetched_at
    b = t.open_outage("bithumb")
    assert b is not None and (b.kind, b.status_code, b.message, b.retry_after_sec) == (
        "rate_limit",
        429,
        '{"error":429}',
        2,
    )
    n = t.open_outage("binance")
    assert n is not None and (n.kind, n.message, n.status_code) == (
        "timeout",
        "느림",
        None,
    )
    # /refresh 가 쓰는 failures 모양은 불변
    assert [sorted(f) for f in result.failures] == [
        ["error_code", "exchange", "message"]
    ] * 2


async def test_writer_loop_drains_queue_in_order() -> None:
    influx = FakeInflux()
    t = OutageTracker(writer=influx)
    task = asyncio.create_task(t.run_writer_loop())
    fail(t, "upbit", T0)
    for i in range(1, 4):
        t.record_success("upbit", T0 + i * SEC)
    for _ in range(50):
        if influx.write_calls == 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert influx.write_calls == 2 and influx.only()["ended_ts"] == T0 // 1000 + 1
