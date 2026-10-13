"""PerpSink 의 매 메시지 경로가 2026-10-09 이전 구현과 같은 결과를 내는지 (스펙 046 §3.2·§3.4).

2026-10-09 에 바뀐 것: 원천 행 표를 붙잡아 바로 찾기(LiveStore.perp_table), 같은 수신 ms 의 datetime 재사용, 펀딩에서
행이 있으면 필드 사전 없이 바로 대입, 넘겨받은 우주가 지금 우주와 같으면 set_universe 무동작.
`_SinkBefore` 는 그 전 PerpSink 를 그대로 옮긴 대조용이다 — 무작위 연산열을 두 싱크(저장소 따로)에 똑같이 먹이고
매 단계 행(원천 순서·행 순서·전 필드)·보류(키·필드 순서)·주기·우주·돌려준 값·예외 종류를 비교한다.
나머지는 고정 기대값으로 계약을 못 박는다.
"""

import dataclasses
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.live_store import LiveStore
from app.core.models import PerpRow
from app.core.perp import PerpSink

T0 = 1_787_727_947_000
AT0 = datetime(2026, 8, 26, 7, 5, 47, tzinfo=UTC)  # T0 의 수신 시각(UTC)
_INF = math.inf
SOURCES = ["binance_perp", "bybit_perp", "bitget_perp", "hyperliquid_perp"]


def _valid(v: float | None) -> bool:
    return v is not None and 0.0 < v < _INF


class _SinkBefore:
    """2026-10-09 이전 PerpSink 를 그대로 옮긴 대조용 — 매번 perp_row 로 찾고, datetime 을 새로 만들고, 펀딩은 사전을 돈다."""

    def __init__(self, store: LiveStore) -> None:
        self._store = store
        self._universe: set[str] = set()
        self._pending: dict[tuple[str, str], dict[str, object]] = {}
        self._interval: dict[tuple[str, str], int] = {}

    def set_universe(self, bases: set[str]) -> int:
        self._universe = {b.upper() for b in bases}
        for key in [k for k in self._pending if k[1] not in self._universe]:
            del self._pending[key]
        return self._store.retain_perp_bases(self._universe)

    @property
    def universe(self) -> set[str]:
        return set(self._universe)

    def set_interval(self, source: str, base: str, hours: int | None) -> None:
        key = base.upper()
        if hours is None:
            self._interval.pop((source, key), None)
        else:
            self._interval[(source, key)] = hours
        row = self._store.perp_row(source, key)
        if row is not None:
            row.funding_interval_h = hours

    def quote(
        self,
        *,
        source: str,
        base: str,
        native_symbol: str,
        multiplier: int,
        bid: float | None,
        ask: float | None,
        bid_size: float | None,
        ask_size: float | None,
        quote_ts: int,
        received_at_ms: int,
    ) -> None:
        key = base.upper()
        if key not in self._universe:
            return
        row = self._store.perp_row(source, key)
        if row is not None and quote_ts < row.quote_ts:
            return
        if _valid(bid):
            n_bid = bid / multiplier  # type: ignore[operator]
        elif bid is None and row is not None:
            n_bid = row.bid
        else:
            n_bid = None
        if _valid(ask):
            n_ask = ask / multiplier  # type: ignore[operator]
        elif ask is None and row is not None:
            n_ask = row.ask
        else:
            n_ask = None
        if _valid(bid_size):
            n_bid_size = bid_size * multiplier  # type: ignore[operator]
        elif bid_size is None and row is not None:
            n_bid_size = row.bid_size
        else:
            n_bid_size = None
        if _valid(ask_size):
            n_ask_size = ask_size * multiplier  # type: ignore[operator]
        elif ask_size is None and row is not None:
            n_ask_size = row.ask_size
        else:
            n_ask_size = None
        now = datetime.fromtimestamp(received_at_ms / 1000, tz=UTC)
        if n_bid is None or n_ask is None or n_bid_size is None or n_ask_size is None:
            if row is not None:
                row.updated_at = now
            return
        if row is None:
            row = PerpRow(
                source=source,
                base=key,
                native_symbol=native_symbol,
                multiplier=multiplier,
                bid=n_bid,
                ask=n_ask,
                bid_size=n_bid_size,
                ask_size=n_ask_size,
                quote_ts=quote_ts,
                funding_interval_h=self._interval.get((source, key)),
                updated_at=now,
            )
            held = self._pending.pop((source, key), None)
            if held is not None:
                for name, value in held.items():
                    setattr(row, name, value)
            self._store.put_perp_row(row)
            return
        row.bid = n_bid
        row.ask = n_ask
        row.bid_size = n_bid_size
        row.ask_size = n_ask_size
        row.quote_ts = quote_ts
        row.updated_at = now

    def funding(
        self,
        *,
        source: str,
        base: str,
        multiplier: int,
        received_at_ms: int,
        funding_rate: float | None = None,
        next_funding_ms: int | None = None,
        mark: float | None = None,
        funding_interval_h: int | None = None,
    ) -> None:
        key = base.upper()
        if key not in self._universe:
            return
        fields: dict[str, object] = {}
        if funding_rate is not None:
            fields["funding_rate"] = funding_rate
        if next_funding_ms is not None:
            fields["next_funding_ms"] = next_funding_ms
        if mark is not None:
            fields["mark"] = mark / multiplier
        if funding_interval_h is not None:
            fields["funding_interval_h"] = funding_interval_h
            self._interval[(source, key)] = funding_interval_h
        if not fields:
            return
        row = self._store.perp_row(source, key)
        if row is None:
            held = self._pending.get((source, key))
            if held is None:
                self._pending[(source, key)] = fields
            else:
                held.update(fields)
            return
        for name, value in fields.items():
            setattr(row, name, value)
        row.updated_at = datetime.fromtimestamp(received_at_ms / 1000, tz=UTC)


# --- 무작위 연산열 대조 ---


def _state(store: LiveStore, sink: Any) -> tuple[Any, ...]:
    rows = [(r.source, r.base, dataclasses.asdict(r)) for r in store.perp_rows()]
    pending = [(k, list(v.items())) for k, v in sink._pending.items()]
    return rows, pending, dict(sink._interval), sorted(sink.universe)


def _call(fn: Any, kwargs: dict[str, Any]) -> tuple[str, Any]:
    try:
        return "ok", fn(**kwargs)
    except Exception as exc:  # 예외 종류까지 같아야 한다
        return type(exc).__name__, None


_BASES = ["BTC", "btc", "Eth", "PEPE", "sol", "DOGE", "XRP"]
_PRICES: list[Any] = [None, None, 1.5, 2.25, 0.0, -1.0, _INF, -_INF, math.nan, 7, 1e-9]


def _universe(rng: random.Random) -> set[str]:
    out = set(
        rng.sample(["BTC", "ETH", "PEPE", "SOL", "DOGE", "XRP"], rng.randint(0, 6))
    )
    if rng.random() < 0.15:
        out.add("eth")  # 소문자가 섞인 우주 — 지금 우주와 같아도 다시 계산하는 쪽
    return out


def _ms(rng: random.Random, last: int) -> int:
    roll = rng.random()
    if roll < 0.6:
        return last  # 같은 ms — 한 회차·연속 프레임
    if roll < 0.75:
        return last + 1
    if roll < 0.85:
        return last - rng.randint(1, 5_000)  # 뒤로 가는 ms
    if roll < 0.97:
        return last + rng.randint(2, 3_600_000)
    return T0


def _ops(seed: int, steps: int) -> list[tuple[str, dict[str, Any]]]:
    rng = random.Random(seed)
    ops: list[tuple[str, dict[str, Any]]] = []
    last_ms = T0
    last_universe: set[str] = set()
    for _ in range(steps):
        roll = rng.random()
        source = rng.choice(SOURCES)
        base = rng.choice(_BASES)
        if roll < 0.06:
            # 같은 우주를 다시 넘기는 회차가 대부분이다 — 운영처럼
            universe = set(last_universe) if rng.random() < 0.6 else _universe(rng)
            last_universe = universe
            ops.append(("set_universe", {"bases": universe}))
        elif roll < 0.09:
            ops.append(("retain", {"bases": _universe(rng)}))
        elif roll < 0.12:
            ops.append(("remove", {"source": source, "base": base}))
        elif roll < 0.16:
            hours = rng.choice([None, 1, 4, 8])
            ops.append(
                ("set_interval", {"source": source, "base": base, "hours": hours})
            )
        elif roll < 0.6:
            last_ms = _ms(rng, last_ms)
            at: Any = last_ms
            if rng.random() < 0.01:
                # datetime 범위 밖 — 만들다 예외가 나도 기억이 오염되지 않는지
                at = 10**20
            fields = {
                name: rng.choice(_PRICES)
                for name in ("bid", "ask", "bid_size", "ask_size")
            }
            if rng.random() < 0.01:
                fields["bid"] = "x"  # 숫자 아님 — 비교에서 TypeError
            ops.append(
                (
                    "quote",
                    {
                        "source": source,
                        "base": base,
                        "native_symbol": f"{base.upper()}USDT",
                        "multiplier": rng.choice([1, 1, 1000]),
                        "quote_ts": T0 + rng.randint(-3, 3),
                        "received_at_ms": at,
                        **fields,
                    },
                )
            )
        else:
            last_ms = _ms(rng, last_ms)
            at = last_ms
            if rng.random() < 0.01:
                at = 10**20
            mark: Any = rng.choice([None, 0.5, 2.0, 1e6])
            if rng.random() < 0.01:
                mark = "x"  # 나눗셈에서 TypeError
            ops.append(
                (
                    "funding",
                    {
                        "source": source,
                        "base": base,
                        # 0 — 나눗셈에서 ZeroDivisionError
                        "multiplier": rng.choice([1, 1, 1000, 0]),
                        "received_at_ms": at,
                        "funding_rate": rng.choice([None, 0.0001, -0.0002]),
                        "next_funding_ms": rng.choice([None, T0 + 3_600_000]),
                        "mark": mark,
                        "funding_interval_h": rng.choice([None, None, 1, 8]),
                    },
                )
            )
    return ops


def _apply(
    store: LiveStore, sink: Any, name: str, kwargs: dict[str, Any]
) -> tuple[str, Any]:
    if name == "retain":
        return _call(store.retain_perp_bases, kwargs)
    if name == "remove":
        return _call(store.remove_perp_row, kwargs)
    return _call(getattr(sink, name), kwargs)


@pytest.mark.parametrize("seed", range(40))
def test_random_message_sequences_match_the_previous_sink(seed: int) -> None:
    before_store, now_store = LiveStore(), LiveStore()
    before, now = _SinkBefore(before_store), PerpSink(now_store)
    for step, (name, kwargs) in enumerate(_ops(seed, 600)):
        got_before = _apply(before_store, before, name, kwargs)
        got_now = _apply(now_store, now, name, kwargs)
        assert got_now == got_before, (seed, step, name, kwargs)
        assert _state(now_store, now) == _state(before_store, before), (
            seed,
            step,
            name,
            kwargs,
        )


# --- 고정 기대값 ---


def _quote(sink: PerpSink, base: str = "BTC", **over: Any) -> None:
    kwargs: dict[str, Any] = {
        "source": "bybit_perp",
        "base": base,
        "native_symbol": f"{base}USDT",
        "multiplier": 1,
        "bid": 100.0,
        "ask": 101.0,
        "bid_size": 2.0,
        "ask_size": 3.0,
        "quote_ts": T0,
        "received_at_ms": T0,
    }
    kwargs.update(over)
    sink.quote(**kwargs)


def _row(source: str, base: str) -> PerpRow:
    return PerpRow(
        source=source,
        base=base,
        native_symbol=f"{base}USDT",
        multiplier=1,
        bid=1.0,
        ask=1.1,
        bid_size=1.0,
        ask_size=1.0,
        quote_ts=T0,
    )


def test_perp_table_is_one_object_across_put_remove_and_retain_and_is_not_created() -> (
    None
):
    store = LiveStore()
    assert store.perp_table("bybit_perp") is None
    store.put_perp_row(_row("binance_perp", "BTC"))
    store.put_perp_row(_row("bybit_perp", "BTC"))
    # 조회가 원천 표를 만들지 않으므로 원천 순서는 첫 행이 생긴 순서 그대로다
    assert [r.source for r in store.perp_rows()] == ["binance_perp", "bybit_perp"]
    table = store.perp_table("bybit_perp")
    assert table is not None and list(table) == ["BTC"]
    store.put_perp_row(_row("bybit_perp", "ETH"))
    store.remove_perp_row("bybit_perp", "btc")
    assert store.perp_table("bybit_perp") is table and list(table) == ["ETH"]
    assert store.retain_perp_bases(set()) == 2
    assert store.perp_table("bybit_perp") is table and table == {}
    store.put_perp_row(_row("bybit_perp", "SOL"))
    assert store.perp_table("bybit_perp") is table and list(table) == ["SOL"]


def test_rows_come_back_after_the_source_table_was_emptied() -> None:
    store = LiveStore()
    sink = PerpSink(store)
    sink.set_universe({"BTC"})
    _quote(sink)
    assert store.perp_row("bybit_perp", "BTC") is not None
    # 우주에서 빠져 그 원천 표가 빈다(표 객체는 그대로)
    assert sink.set_universe(set()) == 1
    assert store.perp_rows() == []
    sink.set_universe({"BTC"})
    _quote(sink, bid=200.0, ask=201.0, quote_ts=T0 + 5, received_at_ms=T0 + 5)
    sink.funding(
        source="bybit_perp",
        base="btc",
        multiplier=1,
        received_at_ms=T0 + 6,
        funding_rate=0.0003,
    )
    row = store.perp_row("bybit_perp", "BTC")
    assert row is not None and store.perp_rows() == [row]
    assert (row.bid, row.ask, row.quote_ts, row.funding_rate) == (
        200.0,
        201.0,
        T0 + 5,
        0.0003,
    )
    assert row.updated_at == AT0 + timedelta(milliseconds=6)


def test_receive_time_is_shared_within_one_ms_and_follows_alternating_ms() -> None:
    store = LiveStore()
    sink = PerpSink(store)
    sink.set_universe({"BTC", "ETH"})
    _quote(sink, "BTC")
    _quote(sink, "ETH")
    btc, eth = store.perp_row("bybit_perp", "BTC"), store.perp_row("bybit_perp", "ETH")
    assert btc is not None and eth is not None
    assert btc.updated_at == eth.updated_at == AT0
    assert btc.updated_at is not None and btc.updated_at.utcoffset() == timedelta(0)
    for at in (T0 + 1, T0, T0 + 1, T0 + 1, T0 + 1500, T0):
        _quote(sink, "BTC", received_at_ms=at)
        assert btc.updated_at == AT0 + timedelta(milliseconds=at - T0)
        sink.funding(
            source="bybit_perp", base="ETH", multiplier=1, received_at_ms=at, mark=5.0
        )
        assert eth.updated_at == AT0 + timedelta(milliseconds=at - T0)
    # 무효 호가는 호가를 두고 수신 시각만 센다 — 직전과 다른 ms 여도 그 ms
    _quote(sink, "BTC", bid=math.nan, received_at_ms=T0 + 9)
    assert btc.bid == 100.0 and btc.updated_at == AT0 + timedelta(milliseconds=9)


def test_receive_time_out_of_range_raises_and_the_next_call_still_uses_its_own_ms() -> (
    None
):
    store = LiveStore()
    sink = PerpSink(store)
    sink.set_universe({"BTC"})
    _quote(sink)
    with pytest.raises((OverflowError, OSError, ValueError)):
        _quote(sink, received_at_ms=10**20)
    _quote(sink, received_at_ms=T0)
    row = store.perp_row("bybit_perp", "BTC")
    assert row is not None and row.updated_at == AT0


def test_funding_failing_on_the_mark_division_changes_nothing() -> None:
    store = LiveStore()
    sink = PerpSink(store)
    sink.set_universe({"BTC", "ETH"})
    _quote(sink, "BTC")
    row = store.perp_row("bybit_perp", "BTC")
    assert row is not None
    before = dataclasses.asdict(row)
    for base in ("BTC", "ETH"):  # 행이 있을 때와 없을 때(보류 길) 둘 다
        with pytest.raises(ZeroDivisionError):
            sink.funding(
                source="bybit_perp",
                base=base,
                multiplier=0,
                received_at_ms=T0 + 1,
                funding_rate=0.5,
                mark=1.0,
                funding_interval_h=4,
            )
    assert dataclasses.asdict(row) == before
    assert sink._interval == {} and sink._pending == {}
    # 주기도 기록되지 않았으므로 새 행에는 주기가 없다
    _quote(sink, "ETH")
    eth = store.perp_row("bybit_perp", "ETH")
    assert eth is not None and eth.funding_interval_h is None


def test_hourly_ctx_is_held_before_the_first_quote_then_updates_in_place() -> None:
    """Hyperliquid activeAssetCtx 처럼 매번 주기 1 이 같이 오는 펀딩 — 행 전엔 보류, 뒤엔 제자리 대입."""
    store = LiveStore()
    sink = PerpSink(store)
    sink.set_universe({"PEPE"})
    common = {"source": "hyperliquid_perp", "base": "PEPE", "multiplier": 1000}
    sink.funding(
        **common,
        received_at_ms=T0,
        funding_rate=-0.00004,
        next_funding_ms=T0 + 1,
        mark=3.749,
        funding_interval_h=1,
    )
    sink.funding(
        **common, received_at_ms=T0, funding_rate=-0.00005, funding_interval_h=1
    )
    assert sink._pending == {
        ("hyperliquid_perp", "PEPE"): {
            "funding_rate": -0.00005,
            "next_funding_ms": T0 + 1,
            "mark": 3.749 / 1000,
            "funding_interval_h": 1,
        }
    }
    sink.quote(
        **common,
        native_symbol="kPEPE",
        bid=3.748,
        ask=3.749,
        bid_size=745.0,
        ask_size=1337.0,
        quote_ts=T0,
        received_at_ms=T0 + 2,
    )
    row = store.perp_row("hyperliquid_perp", "PEPE")
    assert row is not None and sink._pending == {}
    assert (
        row.funding_rate,
        row.next_funding_ms,
        row.mark,
        row.funding_interval_h,
    ) == (
        -0.00005,
        T0 + 1,
        3.749 / 1000,
        1,
    )
    assert (row.bid, row.ask_size) == (3.748 / 1000, 1337.0 * 1000)
    assert row.updated_at == AT0 + timedelta(milliseconds=2)
    sink.funding(
        **common,
        received_at_ms=T0 + 3,
        funding_rate=0.0001,
        next_funding_ms=T0 + 4,
        mark=4.0,
        funding_interval_h=1,
    )
    assert store.perp_row("hyperliquid_perp", "PEPE") is row  # 새 객체 없이 제자리
    assert (
        row.funding_rate,
        row.next_funding_ms,
        row.mark,
        row.funding_interval_h,
    ) == (
        0.0001,
        T0 + 4,
        4.0 / 1000,
        1,
    )
    assert row.updated_at == AT0 + timedelta(milliseconds=3)
    # 온 필드가 하나도 없으면 수신 시각도 그대로다
    sink.funding(**common, received_at_ms=T0 + 50)
    assert row.updated_at == AT0 + timedelta(milliseconds=3)


def test_same_universe_again_skips_the_row_scan_and_an_outside_change_is_undone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LiveStore()
    sink = PerpSink(store)
    scans: list[set[str]] = []
    real_retain = store.retain_perp_bases

    def counting_retain(bases: set[str]) -> int:
        scans.append(set(bases))
        return real_retain(bases)

    monkeypatch.setattr(store, "retain_perp_bases", counting_retain)
    # 첫 호출은 우주가 처음의 빈 집합과 같아도 끝까지 돈다 — 싱크를 거치지 않고 넣은 행도 지운다
    store.put_perp_row(_row("binance_perp", "SEED"))
    assert sink.set_universe(set()) == 1 and len(scans) == 1
    assert sink.set_universe({"BTC", "DOGE"}) == 0 and len(scans) == 2
    _quote(sink, "DOGE")
    _quote(sink, "BTC")
    sink.funding(
        source="bitget_perp", base="DOGE", multiplier=1, received_at_ms=T0, mark=1.0
    )
    # 같은 우주 — 훑지 않는다
    assert sink.set_universe({"BTC", "DOGE"}) == 0 and len(scans) == 2
    # 소문자 — 다시 계산(결과는 같다)
    assert sink.set_universe({"BTC", "doge"}) == 0 and len(scans) == 3
    assert sink.universe == {"BTC", "DOGE"}
    # 바깥에서 넓혀 둔 우주가 다음 회차에 기준대로 되돌아오고 그 밖 행·보류가 지워진다
    assert sink.set_universe({"BTC"}) == 1 and len(scans) == 4
    assert store.perp_row("bybit_perp", "DOGE") is None and sink._pending == {}
    assert sink.set_universe({"BTC"}) == 0 and len(scans) == 4
    assert [r.base for r in store.perp_rows()] == ["BTC"]


def test_set_universe_failing_midway_does_not_leave_a_skip_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LiveStore()
    sink = PerpSink(store)
    sink.set_universe({"BTC", "ETH"})
    _quote(sink, "ETH")
    real_retain = store.retain_perp_bases

    def boom(bases: set[str]) -> int:
        raise RuntimeError("행 정리 실패")

    monkeypatch.setattr(store, "retain_perp_bases", boom)
    with pytest.raises(RuntimeError):
        sink.set_universe({"BTC"})
    monkeypatch.setattr(store, "retain_perp_bases", real_retain)
    # 우주는 이미 바뀌었지만 끝까지 돌지 못했다 — 같은 우주가 다시 오면 행 정리를 다시 한다
    assert sink.set_universe({"BTC"}) == 1
    assert store.perp_row("bybit_perp", "ETH") is None
