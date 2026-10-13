"""우주 확정이 입력이 같을 때 다시 계산하지 않아도 결과가 매초 다시 계산하던 때와 같은지 (스펙 001 §3.2·012 §3.3·020 §3.3).

- 국내: 업비트·빗썸 코드 목록이 직전과 같으면 base 집합을 다시 만들지 않는다. 목록이 바뀌거나 제자리에서 고쳐지면
  새로 만든다 — 우주는 매 회차 `(국내 base 합집합) ∩ (해외 합집합)` 을 처음부터 계산한 값과 같아야 한다.
- 해외(바이낸스·비트겟): 심볼 맵이 같은 객체이고 우주가 같으면 샤드 배정 계산을 건너뛴다. 기대값은 배정 규칙
  (crc32 % 3)과 '배정이 바뀐 샤드가 있을 때만 깨운다'는 계약으로 테스트 안에서 처음부터 계산한다.
"""

import json
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import httpx
import pytest

from app.core.live_store import LiveStore
from app.core.quotes import QuoteSink
from app.core.streams import binance as binance_mod
from app.core.streams import bitget as bitget_mod
from app.core.universe import UniverseRefresher
from tests.conftest import RawLog, make_row
from tests.stream_fakes import store_with_universe

T0 = 1_787_727_947_000
NOW = datetime.fromtimestamp(T0 / 1000, tz=UTC)
SHARDS = 3


# --- 국내 base 집합 (001 §3.2) ---


class ListDomestic:
    """회차마다 `lists` 의 다음 목록을 돌려주는 국내 스트림 fake. `same_object` 면 같은 목록 객체를 제자리에서 바꿔 준다."""

    def __init__(
        self, id_: str, lists: list[list[str]], same_object: bool = False
    ) -> None:
        self.id = id_
        self._lists = lists
        self._same_object = same_object
        self._held: list[str] = []
        self.round = 0
        self.markets: list[list[str]] = []

    async def fetch_markets(self, client: httpx.AsyncClient) -> list[str]:
        codes = self._lists[min(self.round, len(self._lists) - 1)]
        self.round += 1
        if self._same_object:
            self._held[:] = codes  # 받은 쪽이 들고 있는 목록이 제자리에서 바뀐다
            return self._held
        return list(codes)

    def set_markets(self, codes: list[str]) -> None:
        self.markets.append(list(codes))


class FixedForeign:
    def __init__(self, bases: set[str]) -> None:
        self.id = "binance"
        self._bases = bases
        self.universes: list[set[str]] = []

    async def refresh(self, client: httpx.AsyncClient) -> int:
        return 1

    def bases(self) -> set[str]:
        return set(self._bases)

    def set_universe(self, bases: set[str]) -> None:
        self.universes.append(set(bases))


def _refresher(
    up: ListDomestic, bt: ListDomestic, foreign: set[str]
) -> tuple[UniverseRefresher, FixedForeign]:
    fx = FixedForeign(foreign)
    refresher = UniverseRefresher(
        sink=QuoteSink(LiveStore()),
        streams=[up, bt],
        foreigns=[fx],
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ),
    )
    return refresher, fx


def _reference_universe(lists: list[list[str]], foreign: set[str]) -> set[str]:
    """매초 처음부터 계산하던 식 그대로 — 코드마다 '-' 뒤를 대문자로, 국내 합집합 ∩ 해외."""
    domestic: set[str] = set()
    for codes in lists:
        domestic |= {c.split("-", 1)[1].upper() for c in codes if "-" in c}
    return domestic & foreign


async def test_universe_follows_domestic_list_changes_round_by_round() -> None:
    a = ["KRW-BTC", "KRW-XRP", "KRW-USDT", "KRW-ONLYKR"]
    b = ["KRW-BTC", "KRW-USDT", "KRW-ONLYKR"]  # XRP 상폐
    c = ["KRW-BTC", "KRW-XRP", "KRW-sol", "KRW-USDT"]  # XRP 재상장·소문자 코드
    up = ListDomestic("upbit", [a, a, b, b, c, c, a])
    bt = ListDomestic("bithumb", [["KRW-ETH"]])
    refresher, fx = _refresher(up, bt, {"BTC", "XRP", "ETH", "SOL"})
    seen = []
    for _ in range(7):
        await refresher.refresh()
        seen.append(set(refresher.universe))
    assert seen == [
        {"BTC", "XRP", "ETH"},
        {"BTC", "XRP", "ETH"},
        {"BTC", "ETH"},
        {"BTC", "ETH"},
        {"BTC", "XRP", "SOL", "ETH"},
        {"BTC", "XRP", "SOL", "ETH"},
        {"BTC", "XRP", "ETH"},
    ]
    assert fx.universes == seen  # 해외 커넥터에도 매 회차 같은 우주가 간다
    assert up.markets == [a, a, b, b, c, c, a]  # 국내 구독 목록도 매 회차 그대로 넘긴다


async def test_list_changed_in_place_is_not_served_from_the_previous_round() -> None:
    # 같은 목록 객체를 제자리에서 고쳐 돌려줘도 직전 회차의 base 집합을 쓰면 안 된다
    lists = [["KRW-BTC", "KRW-XRP"], ["KRW-BTC"], ["KRW-BTC", "KRW-ETH"]]
    up = ListDomestic("upbit", lists, same_object=True)
    bt = ListDomestic("bithumb", [[]])
    refresher, _ = _refresher(up, bt, {"BTC", "XRP", "ETH"})
    seen = []
    for _ in range(3):
        await refresher.refresh()
        seen.append(set(refresher.universe))
    assert seen == [{"BTC", "XRP"}, {"BTC"}, {"BTC", "ETH"}]


class CountingCode(str):
    """split 호출 수를 세는 코드 문자열 — 같은 목록이면 base 집합을 다시 만들지 않는지 본다."""

    calls = 0

    def split(self, *args: Any, **kwargs: Any) -> list[str]:  # type: ignore[override]
        CountingCode.calls += 1
        return super().split(*args, **kwargs)


async def test_same_domestic_list_does_not_split_codes_again() -> None:
    first = [CountingCode(c) for c in ("KRW-BTC", "KRW-XRP", "KRW-ETH")]
    changed = [CountingCode(c) for c in ("KRW-BTC", "KRW-ETH")]
    up = ListDomestic("upbit", [first, first, first, changed, changed])
    bt = ListDomestic("bithumb", [[]])
    refresher, _ = _refresher(up, bt, {"BTC", "XRP", "ETH"})
    CountingCode.calls = 0
    per_round = []
    for _ in range(5):
        before = CountingCode.calls
        await refresher.refresh()
        per_round.append(CountingCode.calls - before)
    assert per_round == [3, 0, 0, 2, 0]
    assert refresher.universe == {"BTC", "ETH"}


async def test_random_list_sequences_match_the_from_scratch_formula() -> None:
    rng = random.Random(20261009)
    pool = [f"KRW-C{i:02d}" for i in range(40)] + ["KRW-USDT", "BTC-ETH", "NODASH"]
    foreign = {f"C{i:02d}" for i in range(0, 40, 3)} | {"ETH"}

    def pick() -> list[str]:
        return rng.sample(pool, rng.randint(0, len(pool)))

    up_lists: list[list[str]] = []
    bt_lists: list[list[str]] = []
    for _ in range(40):
        # 대부분의 회차는 직전과 같은 목록 — 운영처럼 가끔만 바뀐다
        up_lists.append(up_lists[-1] if up_lists and rng.random() < 0.7 else pick())
        bt_lists.append(bt_lists[-1] if bt_lists and rng.random() < 0.7 else pick())
    up = ListDomestic("upbit", up_lists)
    bt = ListDomestic("bithumb", bt_lists)
    refresher, _ = _refresher(up, bt, foreign)
    for i in range(40):
        await refresher.refresh()
        assert refresher.universe == _reference_universe(
            [up_lists[i], bt_lists[i]], foreign
        ), i


# --- 해외 커넥터 샤드 배정 (012 §3.3·020 §3.3) ---


def _shard(symbol: str) -> int:
    return zlib.crc32(symbol.upper().encode()) % SHARDS


@dataclass(frozen=True)
class Kind:
    id: str
    module: ModuleType
    make: Callable[..., Any]
    body: Callable[[list[str], int, bool], bytes]


def _binance_body(symbols: list[str], server_time: int, other_quote: bool) -> bytes:
    rows = [
        {"symbol": s, "status": "TRADING", "baseAsset": s[:-4], "quoteAsset": "USDT"}
        for s in symbols
    ]
    if other_quote:  # 맵에 들지 않는 행 — 본문 바이트만 바뀌고 맵 내용은 같다
        rows.append(
            {
                "symbol": "ETHBTC",
                "status": "TRADING",
                "baseAsset": "ETH",
                "quoteAsset": "BTC",
            }
        )
    body = {"timezone": "UTC", "serverTime": server_time, "symbols": rows}
    return json.dumps(body, separators=(",", ":")).encode()


def _bitget_body(symbols: list[str], request_time: int, other_quote: bool) -> bytes:
    rows = [
        {"symbol": s, "baseCoin": s[:-4], "quoteCoin": "USDT", "status": "online"}
        for s in symbols
    ]
    if other_quote:
        rows.append(
            {
                "symbol": "ETHBTC",
                "baseCoin": "ETH",
                "quoteCoin": "BTC",
                "status": "online",
            }
        )
    body = {
        "code": "00000",
        "msg": "success",
        "requestTime": request_time,
        "data": rows,
    }
    return json.dumps(body, separators=(",", ":")).encode()


KINDS = [
    Kind("binance", binance_mod, binance_mod.BinanceStream, _binance_body),
    Kind("bitget", bitget_mod, bitget_mod.BitgetStream, _bitget_body),
]

SYMBOLS = [f"T{i:03d}USDT" for i in range(24)]  # 세 샤드에 고루 떨어진다
UNIVERSE = {s[:-4] for s in SYMBOLS[:18]} | {
    "ONLYOTHER"
}  # 다른 해외에만 있는 base 도 섞인다


def _expected(symbols: list[str], universe: set[str]) -> list[set[str]]:
    """배정 규칙을 처음부터 — 우주 base 중 맵에 있는 것의 심볼을 crc32 % 3 샤드로."""
    by_base = {s[:-4]: s for s in symbols}
    out: list[set[str]] = [set() for _ in range(SHARDS)]
    for b in universe:
        sym = by_base.get(b.upper())
        if sym is not None:
            out[_shard(sym)].add(sym)
    return out


class Probe:
    """커넥터 하나와 그 주변 — refresh 본문을 바꿔 가며 넣고, crc32·행 삭제 호출 수를 센다."""

    def __init__(self, kind: Kind, monkeypatch: pytest.MonkeyPatch) -> None:
        self.kind = kind
        self.store, sink = store_with_universe(set())
        self.stream = kind.make(store=self.store, sink=sink, record=RawLog())
        self.t = T0
        self.shard_calls = 0
        self.removed: list[str] = []
        real_shard_of = kind.module.shard_of

        def counting_shard_of(symbol: str) -> int:
            self.shard_calls += 1
            return real_shard_of(symbol)

        monkeypatch.setattr(kind.module, "shard_of", counting_shard_of)
        real_remove = self.store.remove_row

        def counting_remove(exchange: str, base: str) -> None:
            self.removed.append(base)
            real_remove(exchange, base)

        monkeypatch.setattr(self.store, "remove_row", counting_remove)

    async def refresh(self, symbols: list[str], other_quote: bool = False) -> None:
        self.t += (
            1_000  # 매 응답 바뀌는 시각 필드 — 이것만 다르면 맵을 다시 만들지 않는다
        )
        content = self.kind.body(symbols, self.t, other_quote)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=content)
            )
        )
        await self.stream.refresh(client)

    def assigned(self) -> list[set[str]]:
        return [set(s.assigned) for s in self.stream._shards]

    def has_work(self) -> list[bool]:
        return [s.has_work.is_set() for s in self.stream._shards]

    def take_wake(self) -> bool:
        """재조정 루프가 하듯 깨움을 소비하고, 깨웠었는지 돌려준다."""
        woke = self.stream._wake.is_set()
        self.stream._wake.clear()
        return woke

    def reset_counts(self) -> None:
        self.shard_calls = 0
        self.removed.clear()


@pytest.fixture(params=KINDS, ids=[k.id for k in KINDS])
def kind(request: pytest.FixtureRequest) -> Kind:
    return request.param


async def test_same_map_and_same_universe_does_nothing(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(SYMBOLS)
    p.stream.set_universe(set(UNIVERSE))
    assert p.assigned() == _expected(SYMBOLS, UNIVERSE)
    assert p.take_wake() is True
    first = [s.assigned for s in p.stream._shards]

    await p.refresh(SYMBOLS)  # 시각 필드만 다른 본문 — 맵은 그대로다
    p.reset_counts()
    p.stream.set_universe(set(UNIVERSE))  # 내용이 같은 새 집합
    assert p.shard_calls == 0  # 배정 계산을 건너뛴다
    assert p.removed == []
    assert p.take_wake() is False
    assert [s.assigned for s in p.stream._shards] == first
    # 배정 집합 객체도 그대로다 — 바뀐 샤드가 없으면 교체하지 않는다
    after = [s.assigned for s in p.stream._shards]
    assert all(a is b for a, b in zip(first, after, strict=True))


async def test_equal_map_as_a_new_object_recomputes_to_the_same_assignment(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(SYMBOLS)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()
    before_map = p.stream._symbol_of
    before = p.assigned()

    await p.refresh(SYMBOLS, other_quote=True)  # 본문은 다르고 맵 내용은 같다
    assert p.stream._symbol_of is not before_map
    assert p.stream._symbol_of == before_map
    p.reset_counts()
    p.stream.set_universe(set(UNIVERSE))
    assert p.shard_calls > 0  # 새 맵이라 다시 계산한다
    assert p.assigned() == before == _expected(SYMBOLS, UNIVERSE)
    assert p.removed == []
    assert p.take_wake() is False  # 배정이 같으니 깨우지 않는다


async def test_list_change_reassigns_and_wakes_with_the_same_universe(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(SYMBOLS)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()

    dropped = SYMBOLS[3]  # 우주 안의 심볼이 목록에서 빠진다(상폐)
    remaining = [s for s in SYMBOLS if s != dropped]
    await p.refresh(remaining)
    p.stream.set_universe(set(UNIVERSE))
    assert p.assigned() == _expected(remaining, UNIVERSE)
    assert dropped not in set().union(*p.assigned())
    assert p.take_wake() is True

    await p.refresh(SYMBOLS)  # 재상장
    p.stream.set_universe(set(UNIVERSE))
    assert p.assigned() == _expected(SYMBOLS, UNIVERSE)
    assert p.take_wake() is True


async def test_universe_change_with_the_same_map_removes_rows_and_wakes(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(SYMBOLS)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()
    gone = SYMBOLS[5][:-4]
    p.store.put_row(make_row(kind.id, gone, quote="USDT"), NOW)
    smaller = UNIVERSE - {gone}

    p.stream.set_universe(set(smaller))
    assert p.assigned() == _expected(SYMBOLS, smaller)
    assert p.store.get(kind.id, gone) is None  # 빠진 base 의 행은 그 자리에서 지운다
    assert p.removed == [gone]
    assert p.take_wake() is True

    p.stream.set_universe(
        set(UNIVERSE)
    )  # 직전과 다른 우주 — 기억한 것과 비교해 다시 계산한다
    assert p.assigned() == _expected(SYMBOLS, UNIVERSE)
    assert p.take_wake() is True


async def test_caller_changing_the_passed_set_is_seen_on_the_next_call(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(SYMBOLS)
    universe = set(UNIVERSE)
    p.stream.set_universe(universe)
    p.take_wake()
    universe.discard(SYMBOLS[0][:-4])  # 넘긴 집합을 부른 쪽이 고친다

    p.stream.set_universe(universe)
    assert p.assigned() == _expected(SYMBOLS, universe)
    assert p.take_wake() is True


async def test_call_that_failed_midway_does_not_leave_a_skip_behind(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 샤드 0 배정을 바꾼 뒤 뒤쪽 샤드의 행 삭제가 예외로 끝난 호출 — 다음에 원래 우주가 오면 샤드 0 을 되돌려야 한다
    p = Probe(kind, monkeypatch)
    await p.refresh(SYMBOLS)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()
    add0 = next(
        s for s in SYMBOLS[18:] if _shard(s) == 0
    )  # 우주 밖 → 넣으면 샤드 0 이 바뀐다
    drop_late = next(
        s for s in SYMBOLS[:18] if _shard(s) > 0
    )  # 우주 안 → 빼면 뒤 샤드에서 행 삭제
    changed = (UNIVERSE | {add0[:-4]}) - {drop_late[:-4]}

    def boom(exchange: str, base: str) -> None:
        raise RuntimeError("행 삭제 실패")

    monkeypatch.setattr(p.store, "remove_row", boom)
    with pytest.raises(RuntimeError):
        p.stream.set_universe(set(changed))
    assert add0 in p.stream._shards[0].assigned  # 샤드 0 은 이미 바뀌었다

    monkeypatch.setattr(p.store, "remove_row", lambda exchange, base: None)
    p.stream.set_universe(set(UNIVERSE))
    assert p.assigned() == _expected(SYMBOLS, UNIVERSE)


async def test_random_sequence_matches_the_from_scratch_assignment(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    """목록·우주를 섞어 바꾸는 60회 — 매 호출 뒤 배정·has_work·깨움이 처음부터 계산한 값과 같다."""
    rng = random.Random(f"uni-{kind.id}")
    p = Probe(kind, monkeypatch)
    symbols = list(SYMBOLS)
    universe = set(UNIVERSE)
    await p.refresh(symbols)
    previous: list[set[str]] = [set() for _ in range(SHARDS)]
    for step in range(60):
        roll = rng.random()
        if roll < 0.15:
            symbols = rng.sample(SYMBOLS, rng.randint(10, len(SYMBOLS)))
            await p.refresh(symbols, other_quote=rng.random() < 0.5)
        elif roll < 0.3:
            await p.refresh(symbols, other_quote=rng.random() < 0.5)  # 맵 내용은 같다
        elif roll < 0.45:
            universe = {s[:-4] for s in rng.sample(SYMBOLS, rng.randint(0, 20))}
        # 나머지 회차는 목록·우주 그대로 — 운영의 대부분 회차
        p.stream.set_universe(set(universe))
        expected = _expected(symbols, universe)
        assert p.assigned() == expected, step
        assert p.has_work() == [bool(x) for x in expected], step
        assert p.take_wake() is (expected != previous), step
        previous = expected
