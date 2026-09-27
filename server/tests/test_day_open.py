"""일중 기준가 장부 + 행 `dayChg` — 스펙 026 §3.1·§3.2·§4. Redis 는 fakeredis, 네트워크 없음."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

import fakeredis
import pytest

from app.core.day_open import DayOpenBook, kst_date
from app.core.live_store import LiveStore
from app.core.models import Tick, TickRow
from app.core.redis_bus import RedisBus
from app.features.spreads.tests.helpers import make_row, seed_rows, spreads_json

KST = timezone(timedelta(hours=9))


def at(y: int, m: int, d: int, hh: int, mm: int, ss: int) -> int:
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=KST).timestamp())


def tick(ts: int, price: float, *, dom: str = "upbit", base: str = "BTC") -> Tick:
    row = TickRow(dom=dom, fx="binance", base=base, fwd=0.0, rev=0.0, dom_price=price)
    return Tick(ts=ts, rows=(row,), dw_failed=())


async def settle() -> None:
    """읽기·쓰기 태스크가 돌 틈 — fakeredis 는 왕복이 없어 몇 회전이면 끝난다."""
    for _ in range(4):
        await asyncio.sleep(0)


def make_bus() -> tuple[RedisBus, fakeredis.FakeRedis]:
    server = fakeredis.FakeServer()
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server)), fakeredis.FakeRedis(
        server=server
    )


T = at(2026, 9, 27, 10, 0, 0)  # 오늘 낮


def test_kst_date_boundary_is_kst_midnight() -> None:
    assert kst_date(at(2026, 9, 26, 23, 59, 59)) == "2026-09-26"
    assert kst_date(at(2026, 9, 27, 0, 0, 0)) == "2026-09-27"
    # UTC 로는 아직 26일이지만 KST 로는 27일이다
    assert datetime.fromtimestamp(at(2026, 9, 27, 0, 0, 0), tz=UTC).day == 26


async def test_first_tick_after_load_pins_price_and_later_ticks_do_not_overwrite() -> (
    None
):
    bus, raw = make_bus()
    book = DayOpenBook(bus=bus)
    book.observe(tick(T, 100.0))  # 기동 — 읽기 시작, 아직 못 박지 않는다
    assert book.prices == {}
    await settle()
    book.observe(tick(T + 1, 100.0))
    book.observe(tick(T + 2, 110.0))
    assert book.prices == {("upbit", "BTC"): 100.0}
    await settle()
    assert raw.hgetall("dayopen:2026-09-27") == {b"upbit:BTC": b"100.0"}
    assert 172_000 < raw.ttl("dayopen:2026-09-27") <= 172_800
    await book.aclose()


async def test_prices_mapping_reference_survives_startup_and_midnight() -> None:
    """게시기는 기동 때 받은 `prices` 참조를 매초 읽는다 — 장부가 비워져도 같은 객체여야 한다 (배포 후 dayChg 전부 null 이던 버그)."""
    bus, _ = make_bus()
    book = DayOpenBook(bus=bus)
    view = book.prices  # 게시기가 들고 있는 참조
    book.observe(tick(T, 100.0))
    await settle()
    book.observe(tick(T + 1, 100.0))
    assert view == {("upbit", "BTC"): 100.0}
    late = at(2026, 9, 27, 23, 59, 59)
    book.observe(tick(late + 1, 120.0))  # 자정 — 비운다
    assert view == {}
    await settle()
    book.observe(tick(late + 2, 120.0))
    assert view == {("upbit", "BTC"): 120.0}
    await book.aclose()


async def test_midnight_rollover_clears_book_and_pins_new_price() -> None:
    bus, raw = make_bus()
    book = DayOpenBook(bus=bus)
    late = at(2026, 9, 26, 23, 59, 58)
    book.observe(tick(late, 100.0))
    await settle()
    book.observe(tick(late + 1, 100.0))
    assert book.prices == {("upbit", "BTC"): 100.0} and book.date == "2026-09-26"
    book.observe(tick(late + 2, 120.0))  # 00:00:00 KST — 장부를 비운다
    assert book.prices == {} and book.date == "2026-09-27"
    await settle()
    book.observe(tick(late + 3, 120.0))
    assert book.prices == {("upbit", "BTC"): 120.0}
    await settle()
    assert raw.hget("dayopen:2026-09-26", "upbit:BTC") == b"100.0"
    assert raw.hget("dayopen:2026-09-27", "upbit:BTC") == b"120.0"
    await book.aclose()


async def test_startup_uses_stored_midnight_price_not_current() -> None:
    bus, raw = make_bus()
    raw.hset("dayopen:2026-09-27", "upbit:BTC", "90.0")
    book = DayOpenBook(bus=bus)
    book.observe(tick(T, 100.0))
    await settle()
    book.observe(tick(T + 1, 100.0))
    assert book.prices == {("upbit", "BTC"): 90.0}
    await settle()
    assert raw.hget("dayopen:2026-09-27", "upbit:BTC") == b"90.0"  # 덮어쓰지 않는다
    await book.aclose()


async def test_redis_down_warns_and_continues_in_memory(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken:
        async def day_open_load(self, date: str) -> dict[str, float]:
            raise RuntimeError("down")

        async def day_open_save(self, date: str, prices: dict[str, float]) -> None:
            raise RuntimeError("down")

    book = DayOpenBook(bus=Broken())  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="marketlens.day_open"):
        book.observe(tick(T, 100.0))
        await settle()
        book.observe(tick(T + 1, 100.0))
        await settle()
    assert book.prices == {("upbit", "BTC"): 100.0}
    assert any("기준가 읽기 실패" in r.message for r in caplog.records)
    await book.aclose()


async def test_only_domestic_fields_and_zero_price_skipped() -> None:
    bus, raw = make_bus()
    book = DayOpenBook(bus=bus)
    book.observe(tick(T, 100.0))
    await settle()
    rows = (
        TickRow(
            dom="upbit", fx="binance", base="BTC", fwd=0.0, rev=0.0, dom_price=100.0
        ),
        TickRow(dom="upbit", fx="bybit", base="BTC", fwd=0.0, rev=0.0, dom_price=101.0),
        TickRow(
            dom="bithumb", fx="binance", base="ETH", fwd=0.0, rev=0.0, dom_price=0.0
        ),
    )
    book.observe(Tick(ts=T + 1, rows=rows, dw_failed=()))
    await settle()
    assert raw.hgetall("dayopen:2026-09-27") == {b"upbit:BTC": b"100.0"}
    await book.aclose()


def test_day_chg_in_spreads_rows() -> None:
    now = datetime.now(UTC)
    store = LiveStore()
    store.set_rate("upbit", 1400.0, 1390.0, now)
    seed_rows(
        store,
        [
            make_row("upbit", "BTC", price=110.0),
            make_row(
                "upbit", "ETH", price=50.0, asks=[], bids=[[49.0, 1.0]]
            ),  # fail 행
            make_row("upbit", "XRP", price=3.0),
        ],
        now,
    )
    seed_rows(
        store,
        [
            make_row("binance", "BTC"),
            make_row("binance", "ETH"),
            make_row("binance", "XRP"),
        ],
        now,
    )
    day_open = {("upbit", "BTC"): 100.0, ("upbit", "ETH"): 40.0}
    rows = {r["sym"]: r for r in spreads_json(store, day_open=day_open)["rows"]}
    assert rows["BTC"]["dayChg"] == pytest.approx(10.0)
    assert rows["ETH"]["status"] == "fail" and rows["ETH"]["dayChg"] == pytest.approx(
        25.0
    )
    assert rows["XRP"]["dayChg"] is None  # 기준가 없음 = null, 0 이 아니다
    assert all(r["dayChg"] is None for r in spreads_json(store)["rows"])
