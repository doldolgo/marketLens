"""QuoteSink.set_universe 같은 우주 가드 (스펙 001 §3.2) — 같은 우주로 다시 부르면 바로 0 을 돌려줘도 결과가 같은지.

우주 확정은 매초 거의 늘 같은 우주로 싱크를 부른다. 싱크는 본문을 한 번 탄 뒤로는 넘겨받은 우주가 지금 우주와
같으면 아무것도 훑지 않고 0 을 돌려준다. 여기서는 가드 없는 이전 구현을 그대로 옮긴 대조용과 나란히, 호가·체결·
우주 호출을 무작위로 섞어 매 단계 반환값·우주·보류 체결가·행이 같은지 본다(운영처럼 행은 싱크로만 들어온다).
"""

import random
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.live_store import LiveStore
from app.core.quotes import QuoteSink
from tests.conftest import make_row

T0 = 1_700_000_000_000
NOW = datetime.fromtimestamp(T0 / 1000, tz=UTC)


class _SinkBefore(QuoteSink):
    """가드 없는 이전 set_universe 를 그대로 옮긴 대조용 — 부를 때마다 대문자 집합을 만들고 보류·행을 훑는다."""

    def set_universe(self, bases: set[str]) -> int:
        self._universe = {b.upper() for b in bases}
        for key in [k for k in self._trades if k[1] not in self._universe]:
            del self._trades[key]
        return self._store.retain_bases(self._universe)


BASES = ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "USDT"]
EXCHANGES = [("upbit", "KRW"), ("bithumb", "KRW"), ("binance", "USDT"), ("okx", "USDT")]


def _state(sink: QuoteSink, store: LiveStore) -> tuple[Any, ...]:
    rows = sorted(
        (r.exchange, r.base, r.price, r.price_timestamp, str(r.asks), str(r.bids))
        for r in store.get_all()
    )
    return (sorted(sink.universe), sorted(sink._trades.items()), rows)


def _book(sink: QuoteSink, rng: random.Random, at: int) -> None:
    exchange, quote = rng.choice(EXCHANGES)
    base = rng.choice(BASES)
    if rng.random() < 0.2:
        base = base.lower()
    mid = rng.choice([100.0, 101.0, 1400.0])
    asks = [[mid + 1, 1.0], [mid + 2, 2.0]] if rng.random() < 0.9 else []
    sink.orderbook(
        exchange=exchange,
        base=base,
        quote=quote,
        native_symbol=f"{base}-{quote}",
        asks=asks,
        bids=[[mid - 1, 1.0]],
        timestamp_ms=at - 5,
        received_at_ms=at,
    )


def _trade(sink: QuoteSink, rng: random.Random, at: int) -> None:
    exchange, _ = rng.choice(EXCHANGES)
    sink.trade(
        exchange=exchange,
        base=rng.choice(BASES),
        price=rng.choice([99.5, 100.5, 0.0, float("nan")]),
        price_timestamp=at,
    )


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_random_rounds_match_the_unguarded_sink_step_by_step(seed: int) -> None:
    rng = random.Random(seed)
    new_store, ref_store = LiveStore(), LiveStore()
    new, ref = QuoteSink(new_store), _SinkBefore(ref_store)
    # 같은 집합 객체를 제자리에서 고쳐 다시 넘기는 호출자도 흉내 낸다
    shared: set[str] = {"BTC", "ETH"}
    for step in range(3_000):
        at = T0 + step
        roll = rng.random()
        if roll < 0.55:
            state = rng.getstate()
            _book(new, rng, at)
            rng.setstate(state)
            _book(ref, rng, at)
        elif roll < 0.8:
            state = rng.getstate()
            _trade(new, rng, at)
            rng.setstate(state)
            _trade(ref, rng, at)
        else:
            kind = rng.random()
            if kind < 0.5:
                bases: Any = set(new.universe)  # 같은 우주 — 운영에서 가장 흔한 호출
            elif kind < 0.6:
                bases = frozenset(new.universe)
            elif kind < 0.7:
                bases = {b.lower() for b in new.universe}  # 대소문자만 다른 같은 우주
            elif kind < 0.8:
                if rng.random() < 0.5:
                    shared.add(rng.choice(BASES))
                else:
                    shared.discard(rng.choice(BASES))
                bases = shared
            elif kind < 0.85:
                bases = set()
            else:
                bases = {b for b in BASES if rng.random() < 0.6}
            assert new.set_universe(bases) == ref.set_universe(bases), (seed, step)
        assert _state(new, new_store) == _state(ref, ref_store), (seed, step)


def test_first_call_always_runs_even_with_the_initial_empty_universe() -> None:
    """빈 우주로 시작한 싱크도 첫 호출은 본문을 탄다 — 먼저 들어 있던 행은 그 호출에서 지워진다."""
    store = LiveStore()
    store.put_rows([make_row("upbit", "BTC"), make_row("binance", "ETH")], NOW)
    sink = QuoteSink(store)
    assert sink.set_universe(set()) == 2
    assert store.get_all() == []
    assert sink.set_universe(set()) == 0


def test_same_universe_returns_zero_and_a_changed_one_still_cleans_up() -> None:
    store = LiveStore()
    sink = QuoteSink(store)
    assert sink.set_universe({"BTC", "ETH"}) == 0
    for base in ("BTC", "ETH"):
        sink.orderbook(
            exchange="upbit",
            base=base,
            quote="KRW",
            native_symbol=f"KRW-{base}",
            asks=[[101.0, 1.0]],
            bids=[[99.0, 1.0]],
            timestamp_ms=T0,
            received_at_ms=T0,
        )
    sink.trade(exchange="binance", base="ETH", price=50.0, price_timestamp=T0)
    universe = {"BTC", "ETH"}
    assert sink.set_universe(universe) == 0
    assert sink.set_universe(frozenset(universe)) == 0  # type: ignore[arg-type]
    assert {r.base for r in store.get_all()} == {"BTC", "ETH"}
    universe.discard("ETH")  # 같은 객체를 고쳐 다시 넘겨도 바뀐 우주로 본다
    assert sink.set_universe(universe) == 1
    assert {r.base for r in store.get_all()} == {"BTC"}
    assert sink._trades == {}  # ETH 보류 체결가도 함께 지워진다
    assert sink.universe == {"BTC"}
