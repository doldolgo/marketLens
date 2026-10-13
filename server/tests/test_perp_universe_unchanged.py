"""perp 우주 확정이 입력이 같을 때 다시 계산하지 않아도 매초 처음부터 계산하던 때와 결과가 같은지 (스펙 046 §3.2·047 §3.3).

- PerpUniverse.apply: (김프 우주, 원천별 base 집합)이 직전과 같으면 perp 우주 집계만 건너뛴다. 기대값은 식
  `{2곳 이상} ∪ (김프 ∩ {1곳 이상})` 을 테스트 안에서 처음부터 센 것이다. 싱크·원천에는 매 회차 넘긴다.
- 원천 4곳(binance_perp·bybit_perp·bitget_perp·hyperliquid_perp): 심볼 맵이 같은 객체이고 우주가 같으면 반영 계산을
  건너뛴다. 기대값은 '우주 base 중 맵에 있는 것의 심볼'(Hyperliquid 는 + crc32 % 3 샤드 배정)을 처음부터 계산한 것과
  '바뀐 것이 없으면 행을 지우지도 깨우지도 않는다'는 계약이다.
"""

import json
import random
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import httpx
import pytest

from app.core import perp_universe as perp_universe_mod
from app.core.live_store import LiveStore
from app.core.perp import PerpSink
from app.core.perp_universe import PerpUniverse
from app.core.streams import binance_perp as binance_perp_mod
from app.core.streams import bitget_perp as bitget_perp_mod
from app.core.streams import bybit_perp as bybit_perp_mod
from app.core.streams import hyperliquid_perp as hyperliquid_perp_mod
from tests.conftest import RawLog
from tests.stream_fakes import Clock

T0 = 1_787_727_947_000
SHARDS = 3


# --- PerpUniverse.apply (046 §3.2) ---


class ListSource:
    """회차마다 `lists` 의 다음 base 집합을 주는 perp 원천 fake. `same_object` 면 같은 집합 객체를 제자리에서 바꿔 준다."""

    def __init__(
        self, id_: str, lists: list[set[str]], same_object: bool = False
    ) -> None:
        self.id = id_
        self._lists = lists
        self._same_object = same_object
        self._held: set[str] = set()
        self.round = 0
        self.universes: list[set[str]] = []

    async def refresh(self, client: httpx.AsyncClient) -> int:
        return 1

    def bases(self) -> set[str]:
        current = self._lists[min(self.round, len(self._lists) - 1)]
        self.round += 1
        if self._same_object:
            self._held.clear()
            self._held |= current  # 받은 쪽이 들고 있는 집합이 제자리에서 바뀐다
            return self._held
        return set(current)

    def set_universe(self, bases: set[str]) -> None:
        self.universes.append(set(bases))


def _reference(kimp: set[str], per_source: list[set[str]]) -> set[str]:
    """046 §3.2 식을 처음부터 — 원천 몇 곳에 있는지 세고, 2곳 이상 ∪ (김프 ∩ 1곳 이상)."""
    seen: dict[str, int] = {}
    for bases in per_source:
        for b in bases:
            seen[b] = seen.get(b, 0) + 1
    return {b for b, n in seen.items() if n >= 2 or b in kimp}


def _perp_universe(
    sources: list[ListSource],
) -> tuple[PerpUniverse, PerpSink, LiveStore]:
    store = LiveStore()
    sink = PerpSink(store)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    return PerpUniverse(sink=sink, sources=sources, client=client), sink, store


def _counting_formula(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    real = perp_universe_mod.perp_universe

    def counting(kimp: set[str], per_source: Any) -> set[str]:
        calls[0] += 1
        return real(kimp, per_source)

    monkeypatch.setattr(perp_universe_mod, "perp_universe", counting)
    return calls


def test_same_inputs_skip_only_the_count_and_still_reach_the_sink_and_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _counting_formula(monkeypatch)
    a = ListSource("binance_perp", [{"BTC", "PEPE", "MOG"}])
    b = ListSource("bybit_perp", [{"BTC", "PEPE", "ETH"}])
    universe, sink, _ = _perp_universe([a, b])
    for _ in range(3):
        universe.apply({"BTC", "ETH"})
    assert calls[0] == 1  # 같은 입력 — 집계는 한 번
    assert universe.universe == {"BTC", "PEPE", "ETH"} == sink.universe
    # 넘기기는 매 회차
    assert a.universes == b.universes == [{"BTC", "PEPE", "ETH"}] * 3
    universe.apply({"BTC"})  # 김프 우주가 바뀌면 다시 센다
    assert calls[0] == 2 and universe.universe == {"BTC", "PEPE"} == sink.universe


def test_kimp_set_changed_in_place_by_the_caller_is_counted_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _counting_formula(monkeypatch)
    a = ListSource("binance_perp", [{"BTC", "XRP"}])
    b = ListSource("bybit_perp", [{"BTC"}])
    universe, _, _ = _perp_universe([a, b])
    kimp = {"XRP"}
    universe.apply(kimp)
    assert universe.universe == {"BTC", "XRP"}
    kimp.discard("XRP")  # 넘긴 집합을 부른 쪽이 고친다
    universe.apply(kimp)
    assert calls[0] == 2 and universe.universe == {"BTC"}


def test_source_set_changed_in_place_is_not_served_from_the_previous_round() -> None:
    lists = [{"BTC", "ETH"}, {"BTC"}, {"BTC", "SOL"}]
    a = ListSource("binance_perp", lists, same_object=True)
    b = ListSource("bybit_perp", [{"BTC", "ETH", "SOL"}])
    universe, sink, _ = _perp_universe([a, b])
    seen = []
    for _ in range(3):
        universe.apply(set())
        seen.append(set(universe.universe))
    assert seen == [{"BTC", "ETH"}, {"BTC"}, {"BTC", "SOL"}]
    assert sink.universe == {"BTC", "SOL"}


def test_random_list_sequences_match_the_from_scratch_formula() -> None:
    rng = random.Random(20261009)
    pool = [f"C{i:02d}" for i in range(30)]

    def pick() -> set[str]:
        return set(rng.sample(pool, rng.randint(0, len(pool))))

    rounds = 60
    lists: list[list[set[str]]] = [[], [], [], []]
    kimps: list[set[str]] = []
    for _ in range(rounds):
        # 대부분의 회차는 직전과 같은 목록·같은 김프 우주 — 운영처럼 가끔만 바뀐다
        for per in lists:
            per.append(per[-1] if per and rng.random() < 0.8 else pick())
        kimps.append(kimps[-1] if kimps and rng.random() < 0.8 else pick())
    sources = [
        ListSource(id_, per, same_object=(i == 1))
        for i, (id_, per) in enumerate(
            zip(
                ["binance_perp", "bybit_perp", "bitget_perp", "hyperliquid_perp"],
                lists,
                strict=True,
            )
        )
    ]
    universe, sink, _ = _perp_universe(sources)
    for i in range(rounds):
        universe.apply(set(kimps[i]))
        expected = _reference(kimps[i], [per[i] for per in lists])
        assert universe.universe == expected, i
        assert sink.universe == expected, i
        assert all(s.universes[-1] == expected for s in sources), i


# --- 원천 4곳의 set_universe (046 §3.5~3.7·047 §3.3) ---


def _binance_body(symbols: list[str], t: int, extra: bool) -> bytes:
    rows: list[dict[str, Any]] = [
        {
            "symbol": s,
            "baseAsset": s[:-4],
            "quoteAsset": "USDT",
            "contractType": "PERPETUAL",
            "status": "TRADING",
        }
        for s in symbols
    ]
    if extra:  # 맵에 들지 않는 행 — 본문 바이트만 바뀌고 맵 내용은 같다
        rows.append(
            {
                "symbol": "T999USDT_261225",
                "baseAsset": "T999",
                "quoteAsset": "USDT",
                "contractType": "CURRENT_QUARTER",
                "status": "TRADING",
            }
        )
    body = {"timezone": "UTC", "serverTime": t, "symbols": rows}
    return json.dumps(body, separators=(",", ":")).encode()


def _bybit_body(symbols: list[str], t: int, extra: bool) -> bytes:
    rows: list[dict[str, Any]] = [
        {
            "symbol": s,
            "contractType": "LinearPerpetual",
            "status": "Trading",
            "baseCoin": s[:-4],
            "quoteCoin": "USDT",
            "fundingInterval": 480,
        }
        for s in symbols
    ]
    if extra:
        rows.append(
            {
                "symbol": "T999USDC",
                "contractType": "LinearPerpetual",
                "status": "Trading",
                "baseCoin": "T999",
                "quoteCoin": "USDC",
                "fundingInterval": 480,
            }
        )
    body = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {"category": "linear", "list": rows, "nextPageCursor": ""},
        "retExtInfo": {},
        "time": t,
    }
    return json.dumps(body, separators=(",", ":")).encode()


def _bitget_body(symbols: list[str], t: int, extra: bool) -> bytes:
    rows: list[dict[str, Any]] = [
        {
            "symbol": s,
            "baseCoin": s[:-4],
            "quoteCoin": "USDT",
            "symbolType": "perpetual",
            "symbolStatus": "normal",
            "fundInterval": "8",
        }
        for s in symbols
    ]
    if extra:
        rows.append(
            {
                "symbol": "T999USDT",
                "baseCoin": "T999",
                "quoteCoin": "USDT",
                "symbolType": "delivery",
                "symbolStatus": "normal",
                "fundInterval": "8",
            }
        )
    body = {"code": "00000", "msg": "success", "requestTime": t, "data": rows}
    return json.dumps(body, separators=(",", ":")).encode()


def _hyperliquid_body(names: list[str], t: int, extra: bool) -> bytes:
    rows: list[dict[str, Any]] = [
        {"szDecimals": 0, "name": n, "maxLeverage": 10, "marginTableId": 1}
        for n in names
    ]
    if extra:  # 상장폐지 코인은 맵에 들지 않는다
        rows.append(
            {"szDecimals": 0, "name": "T999", "maxLeverage": 3, "isDelisted": True}
        )
    return json.dumps({"universe": rows, "marginTables": []}).encode()


@dataclass(frozen=True)
class Kind:
    id: str
    module: ModuleType
    make: Callable[..., Any]
    body: Callable[[list[str], int, bool], bytes]
    suffix: str  # 심볼 = base + suffix (Hyperliquid 는 코인 이름이 곧 base)


KINDS = [
    Kind(
        "binance_perp",
        binance_perp_mod,
        binance_perp_mod.BinancePerpStream,
        _binance_body,
        "USDT",
    ),
    Kind(
        "bybit_perp",
        bybit_perp_mod,
        bybit_perp_mod.BybitPerpStream,
        _bybit_body,
        "USDT",
    ),
    Kind(
        "bitget_perp",
        bitget_perp_mod,
        bitget_perp_mod.BitgetPerpStream,
        _bitget_body,
        "USDT",
    ),
    Kind(
        "hyperliquid_perp",
        hyperliquid_perp_mod,
        hyperliquid_perp_mod.HyperliquidPerpStream,
        _hyperliquid_body,
        "",
    ),
]

BASES = [f"T{i:03d}" for i in range(24)]  # Hyperliquid 세 샤드에 고루 떨어진다
UNIVERSE = set(BASES[:18]) | {"ONLYOTHER"}  # 다른 원천에만 있는 base 도 섞인다


class CountingSet(set[str]):
    """원소를 하나씩 훑은 횟수를 센다 — 반영 계산(우주를 훑어 맵에서 찾기)을 건너뛰었는지 본다."""

    iterations = 0

    def __iter__(self) -> Iterator[str]:
        CountingSet.iterations += 1
        return super().__iter__()


class Probe:
    """원천 하나와 그 주변 — 목록 본문을 바꿔 가며 넣고, 행 삭제·Hyperliquid crc32 호출 수를 센다."""

    def __init__(self, kind: Kind, monkeypatch: pytest.MonkeyPatch) -> None:
        self.kind = kind
        self.store = LiveStore()
        self.sink = PerpSink(self.store)
        self.sink.set_universe(set(UNIVERSE))
        self.clock = Clock(T0)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        )
        kwargs: dict[str, Any] = {
            "store": self.store,
            "sink": self.sink,
            "record": RawLog(),
            "clock": self.clock,
        }
        if kind.id != "hyperliquid_perp":
            kwargs["client"] = client
        self.stream = kind.make(**kwargs)
        self.t = T0
        self.shard_calls = 0
        self.removed: list[str] = []
        if kind.id == "hyperliquid_perp":
            real_shard_of = kind.module.shard_of

            def counting_shard_of(name: str) -> int:
                self.shard_calls += 1
                return real_shard_of(name)

            monkeypatch.setattr(kind.module, "shard_of", counting_shard_of)
        real_remove = self.store.remove_perp_row

        def counting_remove(source: str, base: str) -> None:
            self.removed.append(base)
            real_remove(source, base)

        monkeypatch.setattr(self.store, "remove_perp_row", counting_remove)

    def sym(self, base: str) -> str:
        return base + self.kind.suffix

    async def refresh(
        self, bases: list[str], extra: bool = False, symbols: list[str] | None = None
    ) -> None:
        # 매 응답 바뀌는 시각 필드 — 이것만 다르면 맵을 다시 만들지 않는다
        self.t += 1_000
        content = self.kind.body(
            symbols if symbols is not None else [self.sym(b) for b in bases],
            self.t,
            extra,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/fapi/v1/fundingInfo":
                return httpx.Response(200, json=[])
            return httpx.Response(200, content=content)

        await self.stream.refresh(
            httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    async def refresh_fails(self) -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(429, text="slow down")
            )
        )
        with pytest.raises(Exception):  # noqa: B017 — 원천마다 분류는 각 스트림 테스트가 본다
            await self.stream.refresh(client)

    def put_row(self, base: str) -> None:
        self.sink.quote(
            source=self.kind.id,
            base=base,
            native_symbol=self.sym(base),
            multiplier=1,
            bid=1.0,
            ask=1.1,
            bid_size=1.0,
            ask_size=1.0,
            quote_ts=T0,
            received_at_ms=T0,
        )

    def wanted(self) -> set[str]:
        if self.kind.id == "hyperliquid_perp":
            return set().union(*(s.assigned for s in self.stream._shards))
        return set(self.stream._wanted)

    def assigned(self) -> list[set[str]] | None:
        if self.kind.id != "hyperliquid_perp":
            return None
        return [set(s.assigned) for s in self.stream._shards]

    def take_wake(self) -> bool | None:
        """재조정 루프가 하듯 깨움을 소비하고 깨웠었는지 — Hyperliquid 만 소켓 재조정이 있다."""
        if self.kind.id != "hyperliquid_perp":
            return None
        woke = self.stream._wake.is_set()
        self.stream._wake.clear()
        return woke

    def subscribed(self) -> int | None:
        """폴링 원천의 subscribed = 반영 대상 심볼 수(set_universe 가 쓴다). Hyperliquid 는 소켓 루프가 쓰므로 None."""
        if self.kind.id == "hyperliquid_perp":
            return None
        state = self.store.stream_state(self.kind.id)
        return None if state is None else state.subscribed

    def reset_counts(self) -> None:
        self.shard_calls = 0
        self.removed.clear()
        CountingSet.iterations = 0


def _expected_wanted(kind: Kind, bases: list[str], universe: set[str]) -> set[str]:
    """반영 대상을 처음부터 — 우주 base 중 맵에 있는 것의 심볼."""
    listed = set(bases)
    return {b + kind.suffix for b in universe if b.upper() in listed}


def _expected_assigned(
    kind: Kind, bases: list[str], universe: set[str]
) -> list[set[str]] | None:
    if kind.id != "hyperliquid_perp":
        return None
    out: list[set[str]] = [set() for _ in range(SHARDS)]
    for name in _expected_wanted(kind, bases, universe):
        out[zlib.crc32(name.encode()) % SHARDS].add(name)
    return out


@pytest.fixture(params=KINDS, ids=[k.id for k in KINDS])
def kind(request: pytest.FixtureRequest) -> Kind:
    return request.param


async def test_same_map_and_same_universe_does_nothing(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(BASES)
    p.stream.set_universe(set(UNIVERSE))
    assert p.wanted() == _expected_wanted(kind, BASES, UNIVERSE)
    assert p.assigned() == _expected_assigned(kind, BASES, UNIVERSE)
    assert p.subscribed() in (18, None)
    p.take_wake()
    for b in BASES[:3]:
        p.put_row(b)
    rows = [(r.base, r.bid) for r in p.store.perp_rows()]

    await p.refresh(BASES)  # 시각 필드만 다른 본문 — 맵은 그대로다
    p.reset_counts()
    p.stream.set_universe(CountingSet(UNIVERSE))  # 내용이 같은 새 집합
    assert CountingSet.iterations == 0  # 우주를 훑어 맵에서 찾는 일을 건너뛴다
    assert p.shard_calls == 0 and p.removed == []
    assert p.take_wake() in (False, None)
    assert p.wanted() == _expected_wanted(kind, BASES, UNIVERSE)
    assert p.assigned() == _expected_assigned(kind, BASES, UNIVERSE)
    assert p.subscribed() in (18, None)
    assert [(r.base, r.bid) for r in p.store.perp_rows()] == rows


async def test_equal_map_as_a_new_object_recomputes_to_the_same_result(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(BASES)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()
    before_map = p.stream._symbol_of

    await p.refresh(BASES, extra=True)  # 본문은 다르고 맵 내용은 같다
    assert p.stream._symbol_of is not before_map and p.stream._symbol_of == before_map
    p.reset_counts()
    p.stream.set_universe(CountingSet(UNIVERSE))
    assert CountingSet.iterations > 0  # 새 맵이라 다시 계산한다
    assert p.wanted() == _expected_wanted(kind, BASES, UNIVERSE)
    assert p.assigned() == _expected_assigned(kind, BASES, UNIVERSE)
    assert p.removed == [] and p.take_wake() in (False, None)
    # 다시 계산한 뒤로는 같은 맵·같은 우주라 다시 건너뛴다
    CountingSet.iterations = 0
    p.stream.set_universe(CountingSet(UNIVERSE))
    assert CountingSet.iterations == 0


async def test_list_change_with_the_same_universe_recomputes_what_is_wanted(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(BASES)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()
    dropped = BASES[3]  # 우주 안의 심볼이 목록에서 빠진다(상폐)
    p.put_row(dropped)
    remaining = [b for b in BASES if b != dropped]
    await p.refresh(remaining)
    p.reset_counts()
    p.stream.set_universe(set(UNIVERSE))
    assert p.wanted() == _expected_wanted(kind, remaining, UNIVERSE)
    assert p.assigned() == _expected_assigned(kind, remaining, UNIVERSE)
    assert p.subscribed() in (17, None)
    # 빠진 심볼의 행 삭제는 그 전과 같은 인자로 부른다 — 새 맵에 그 심볼이 없어 base 대신 심볼 문자열로 부르므로
    # 폴링 원천(심볼 ≠ base)에서는 행이 지워지지 않는다. 기준 코드부터 그렇다(이 변경과 별개의 결함 — 고치면 이 줄도 바꾼다)
    assert p.removed == [p.sym(dropped)]
    assert p.take_wake() in (True, None)

    await p.refresh(BASES)  # 재상장
    p.stream.set_universe(set(UNIVERSE))
    assert p.wanted() == _expected_wanted(kind, BASES, UNIVERSE)
    assert p.subscribed() in (18, None)
    assert p.take_wake() in (True, None)


async def test_universe_change_with_the_same_map_removes_rows_and_comes_back(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(BASES)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()
    gone = BASES[5]
    p.put_row(gone)
    smaller = UNIVERSE - {gone}

    p.stream.set_universe(set(smaller))
    assert p.wanted() == _expected_wanted(kind, BASES, smaller)
    assert p.assigned() == _expected_assigned(kind, BASES, smaller)
    # 빠진 base 의 행은 그 자리에서 지운다
    assert p.store.perp_row(kind.id, gone) is None
    assert p.removed == [gone] and p.subscribed() in (17, None)
    assert p.take_wake() in (True, None)

    # 직전과 다른 우주 — 기억한 것과 비교해 다시 계산한다
    p.stream.set_universe(set(UNIVERSE))
    assert p.wanted() == _expected_wanted(kind, BASES, UNIVERSE)
    assert p.subscribed() in (18, None)
    assert p.take_wake() in (True, None)


async def test_caller_changing_the_passed_set_is_seen_on_the_next_call(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(BASES)
    universe = set(UNIVERSE)
    p.stream.set_universe(universe)
    p.take_wake()
    universe.discard(BASES[0])  # 넘긴 집합을 부른 쪽이 고친다

    p.stream.set_universe(universe)
    assert p.wanted() == _expected_wanted(kind, BASES, universe)
    assert p.assigned() == _expected_assigned(kind, BASES, universe)
    assert p.subscribed() in (17, None)


async def test_call_that_failed_midway_does_not_leave_a_skip_behind(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(BASES)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()
    gone = BASES[7]
    p.put_row(gone)
    smaller = UNIVERSE - {gone}
    real_remove = p.store.remove_perp_row

    def boom(source: str, base: str) -> None:
        raise RuntimeError("행 삭제 실패")

    monkeypatch.setattr(p.store, "remove_perp_row", boom)
    with pytest.raises(RuntimeError):
        p.stream.set_universe(set(smaller))
    monkeypatch.setattr(p.store, "remove_perp_row", real_remove)
    # 같은 우주가 다시 오면 끝까지 다시 계산한다 — 그 사이 실패를 기억으로 덮지 않는다
    p.stream.set_universe(set(smaller))
    assert p.store.perp_row(kind.id, gone) is None
    assert p.wanted() == _expected_wanted(kind, BASES, smaller)
    assert p.assigned() == _expected_assigned(kind, BASES, smaller)
    assert p.subscribed() in (17, None)


async def test_failed_list_refresh_keeps_the_skip(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = Probe(kind, monkeypatch)
    await p.refresh(BASES)
    p.stream.set_universe(set(UNIVERSE))
    p.take_wake()
    await p.refresh_fails()  # 429 — 직전 맵 유지
    p.reset_counts()
    p.stream.set_universe(CountingSet(UNIVERSE))
    assert CountingSet.iterations == 0 and p.removed == [] and p.shard_calls == 0
    assert p.wanted() == _expected_wanted(kind, BASES, UNIVERSE)


async def test_symbol_swap_with_the_same_base_set_changes_what_is_wanted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """비트겟 PEPEUSDT → 1000PEPEUSDT — base 집합은 같고 심볼만 바뀌면 맵이 새 객체라 반영 대상이 바뀐다."""
    kind = next(k for k in KINDS if k.id == "bitget_perp")
    p = Probe(kind, monkeypatch)
    p.sink.set_universe({"PEPE", "T000"})
    await p.refresh([], symbols=["PEPEUSDT", "T000USDT"])
    p.stream.set_universe({"PEPE", "T000"})
    assert p.wanted() == {"PEPEUSDT", "T000USDT"}
    await p.refresh([], symbols=["1000PEPEUSDT", "T000USDT"])
    assert p.stream.bases() == {"PEPE", "T000"}
    p.stream.set_universe({"PEPE", "T000"})
    assert p.wanted() == {"1000PEPEUSDT", "T000USDT"} and p.subscribed() == 2


async def test_hyperliquid_limit_warning_stays_once_for_the_same_universe(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    kind = next(k for k in KINDS if k.id == "hyperliquid_perp")
    p = Probe(kind, monkeypatch)
    limit = (
        hyperliquid_perp_mod.SUBSCRIPTION_LIMIT // hyperliquid_perp_mod.SUBS_PER_COIN
    )
    names = [f"C{i:04d}" for i in range(limit + 10)]
    p.sink.set_universe(set(names))
    await p.refresh(names)
    with caplog.at_level("WARNING", logger="marketlens.stream.hyperliquid_perp"):
        for _ in range(3):
            p.stream.set_universe(set(names))
        # 맵이 새 객체여도 제외 수가 같으면 경고는 그대로 한 번
        await p.refresh(names, extra=True)
        p.stream.set_universe(set(names))
    warnings = [r.message for r in caplog.records if "구독 한도" in r.message]
    assert len(warnings) == 1 and "10코인 제외" in warnings[0]
    assert p.wanted() == set(names[:limit])
