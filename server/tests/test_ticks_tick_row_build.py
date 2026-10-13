"""틱 행 만들기 — tuple.__new__ 로 만든 TickRow 와 표 나누기가 바꾸기 전 구현과 같다 (스펙 001 §3.6-2, 2026-10-09).

build_tick 은 TickRow 를 namedtuple 생성자 대신 tuple.__new__(TickRow, 값 14개)로 만들고, 거래소 표를 setdefault 없이
나눈다. 아래 `_reference_build_tick` 은 바꾸기 전(9b13b2c) build_tick 을 그대로 옮긴 것이다 — 무작위 저장소(소문자 base·
KRW/USDT 밖 호가·빈 호가·0·NaN·inf 가격·시세 없음·망 목록)에서 행 순서·타입·길이·값(repr)이 같아야 한다.
"""

import math
import random
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.live_store import LiveStore
from app.core.models import Row, Tick, TickRow
from app.core.networks import Network, WalletMemo, wallet_fields
from app.core.premium import premium_percent
from app.core.ticks import build_tick
from tests.conftest import make_row

NOW = datetime.now(UTC)
T0 = 1_787_000_000
_EXCHANGES = ("upbit", "bithumb", "binance", "bybit", "bitget", "okx")
_BASES = ("BTC", "ETH", "XRP", "SOL", "doge", "Ada", "TRX", "LINK", "PEPE", "ß")


def _reference_build_tick(
    store: LiveStore, ts: int, dw_failed: Sequence[str], memo: WalletMemo | None = None
) -> Tick:
    """바꾸기 전 build_tick(9b13b2c) 그대로 — 생성자 TickRow(...)·setdefault 표 나누기."""
    rates = {ex: r for ex, r in store.rates().items() if r.ask > 0 and r.bid > 0}
    domestic: dict[str, dict[str, Row]] = {}
    foreign: dict[str, dict[str, Row]] = {}
    for row in store.get_all():
        if row.quote == "KRW":
            domestic.setdefault(row.exchange, {})[row.base.upper()] = row
        elif row.quote == "USDT":
            foreign.setdefault(row.exchange, {})[row.base.upper()] = row
    if memo is not None:
        memo.rotate()
    rows: list[TickRow] = []
    for dom_ex, dom_table in sorted(domestic.items()):
        rate = rates.get(dom_ex)
        if rate is None:
            continue
        rate_ask = rate.ask
        rate_bid = rate.bid
        mid = (rate_ask + rate_bid) / 2
        for fx_ex, fx_table in sorted(foreign.items()):
            if fx_ex == dom_ex:
                continue
            for base in sorted(dom_table.keys() & fx_table.keys()):
                dom_row = dom_table[base]
                fx_row = fx_table[base]
                if not (dom_row.bids and dom_row.asks and fx_row.bids and fx_row.asks):
                    continue
                dom_bid = dom_row.bids[0][0]
                dom_ask = dom_row.asks[0][0]
                fx_bid = fx_row.bids[0][0]
                fx_ask = fx_row.asks[0][0]
                if dom_bid <= 0 or dom_ask <= 0 or fx_bid <= 0 or fx_ask <= 0:
                    continue
                wf = (
                    wallet_fields(dom_row, fx_row)
                    if memo is None
                    else memo.fields((dom_ex, fx_ex, base), dom_row, fx_row)
                )
                rows.append(
                    TickRow(
                        dom=dom_ex,
                        fx=fx_ex,
                        base=base,
                        fwd=premium_percent(buy_krw=fx_ask * rate_ask, sell_krw=dom_bid),
                        rev=premium_percent(buy_krw=dom_ask, sell_krw=fx_bid * rate_bid),
                        dom_price=dom_row.price,
                        fx_price=fx_row.price,
                        rate=mid,
                        dom_dep=wf.dep_dom,
                        dom_wd=wf.wd_dom,
                        fx_dep=wf.dep_fx,
                        fx_wd=wf.wd_fx,
                        net_dom=wf.net_dom,
                        net_fx=wf.net_fx,
                    )
                )
    return Tick(ts=ts, rows=tuple(rows), dw_failed=tuple(dw_failed))


_NETS = (
    [],
    [Network("ETH", "Ethereum", True, True)],
    [Network("SOL", "Solana", True, False), Network("ETH", "Ethereum", False, True)],
    [Network("TRX", "Tron", False, False)],
)


def _price(rng: random.Random) -> float:
    roll = rng.random()
    if roll < 0.03:
        return 0.0
    if roll < 0.05:
        return math.nan
    if roll < 0.06:
        return math.inf
    if roll < 0.07:
        return -1.0
    return rng.uniform(0.5, 120_000.0)


def _random_store(rng: random.Random) -> LiveStore:
    store = LiveStore()
    for ex in _EXCHANGES:
        for base in _BASES:
            if rng.random() < 0.25:
                continue
            quote = rng.choice(("KRW", "KRW", "BTC")) if ex in ("upbit", "bithumb") else rng.choice(("USDT", "USDT", "USDC"))
            asks = [] if rng.random() < 0.05 else [[_price(rng), 1.0]]
            bids = [] if rng.random() < 0.05 else [[_price(rng), 1.0]]
            row = make_row(ex, base, quote=quote, price=_price(rng), asks=asks, bids=bids)
            store.put_row(row, NOW)
            row.deposit_enabled = rng.choice((True, False, None))
            row.withdrawal_enabled = rng.choice((True, False, None))
            row.networks = rng.choice(_NETS)
    for ex in ("upbit", "bithumb", "okx"):
        roll = rng.random()
        if roll < 0.15:
            continue  # 시세 없음
        ask = 0.0 if roll < 0.25 else rng.uniform(1300.0, 1500.0)
        store.set_rate(ex, ask, rng.uniform(1300.0, 1500.0), NOW)
    return store


def test_tick_rows_match_the_reference_build_type_length_and_values() -> None:
    rng = random.Random(20261009)
    seen = 0
    for _ in range(400):
        store = _random_store(rng)
        for memo_pair in ((None, None), (WalletMemo(), WalletMemo())):
            got = build_tick(store, T0, ["upbit"], memo_pair[0])
            want = _reference_build_tick(store, T0, ["upbit"], memo_pair[1])
            assert (got.ts, got.dw_failed) == (want.ts, want.dw_failed)
            assert len(got.rows) == len(want.rows)
            for g, w in zip(got.rows, want.rows, strict=True):
                assert type(g) is TickRow  # namedtuple 하위형 그대로 — 필드 이름으로 읽힌다
                assert len(g) == len(TickRow._fields)  # 필드가 늘었는데 tuple.__new__ 자리를 빠뜨리면 여기서 걸린다
                assert repr(g) == repr(w)  # NaN 이 끼어도 비교되게 repr 로
                assert g._asdict().keys() == w._asdict().keys()
            seen += len(got.rows)
    assert seen > 5_000  # 무작위 저장소가 실제로 자격 통과 행을 많이 만든다


def test_tick_row_built_from_tuple_new_behaves_like_the_constructor() -> None:
    store = LiveStore()
    store.put_rows(
        [
            make_row("upbit", "btc", price=100_050.0, bids=[[100_000.0, 1.0]], asks=[[100_100.0, 1.0]]),
            make_row("binance", "BTC", price=70.5, bids=[[70.0, 1.0]], asks=[[71.0, 1.0]]),
        ],
        NOW,
    )
    store.set_rate("upbit", 1400.0, 1390.0, NOW)
    [row] = build_tick(store, T0, []).rows
    assert row == TickRow(*row)
    assert hash(row) == hash(TickRow(*row))
    assert (row.dom, row.fx, row.base, row.rate) == ("upbit", "binance", "BTC", 1395.0)
    assert row._replace(fwd=1.0).fwd == 1.0  # namedtuple 메서드도 그대로 쓴다
