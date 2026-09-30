"""실데이터 표 3장 위에서 틱·표·표 인코딩이 기준 구현과 같은 결과인지 (2026-09-28 성능 개선).

`data/spreads_prod_3.json.xz` 는 운영 `GET /spreads` 표 3장(연속 3초, 1,458행)에서 행마다 코인·국내·해외
거래소·국내가·해외가·spark·입출금 6필드·dayChg 만 남긴 것이다(행 순서 그대로). 호가창은 그 가격에 시드를
고정해 합성한다 — 단계당 금액이 로그정규라 $1,000 걷기가 평균 2~3단계를 먹는다. 기준 구현은 틱 자격·원값
(001 §3.6-2)과 행 하나의 규칙(003 §3.2-4)을 조합마다 그대로 계산한다 — 거래소·시장 단위 값을 행마다
다시 구하고, 걷기는 core 의 공개 걷기 함수로, 망 판정은 행마다 공유 함수로 한다.
"""

import json
import lzma
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.influx import SparkBucketRow
from app.core.live_store import LiveStore
from app.core.models import Row, TickRow
from app.core.networks import Network, WalletMemo, wallet_fields
from app.core.orderbook import (
    average_price,
    walk_amount,
    walk_levels,
    walk_quantity,
)
from app.core.premium import premium_percent
from app.core.spark import SparkBuffer
from app.core.tick_store import TickRelay
from app.core.ticks import build_tick
from app.features.spreads.push import encode_table, encode_table_with_spark_json
from app.features.spreads.service import build_table

DATA = Path(__file__).parent / "data" / "spreads_prod_3.json.xz"
# 조각의 열 — 행마다 이 순서의 값 목록이다
FIELDS = (
    "sym", "dom", "fx", "krw", "usd", "spark", "netDom", "depDom", "wdDom", "depFx", "wdFx", "netFx", "dayChg",
)  # fmt: skip
DEPTH = {"upbit": 30, "bithumb": 15, "binance": 20, "bybit": 20, "bitget": 15}
RATES = {"upbit": (1361.0, 1360.0), "bithumb": (1362.0, 1361.0)}
OTHER_NETS = (
    ("BSC", "BNB Smart Chain (BEP20)"),
    ("TRX", "Tron (TRC20)"),
    ("SOL", "Solana"),
    ("ARBITRUM", "Arbitrum One"),
    ("MATIC", "Polygon POS"),
    ("BASE", "Base"),
)
NOW = datetime(
    2026, 9, 28, 3, 0, 58, 250_000, tzinfo=UTC
)  # 초 = 58 — 두 틱 뒤에 분이 바뀐다
Table = dict[str, list]


@pytest.fixture(scope="module")
def tables() -> list[Table]:
    return json.loads(lzma.decompress(DATA.read_bytes()))


# ---- 합성 저장소 ----


def _levels(
    best: float, n: int, side: str, rng: random.Random, unit: float
) -> list[list[float]]:
    step = best * rng.choice((0.0005, 0.001, 0.002))
    price = best
    out: list[list[float]] = []
    for _ in range(n):
        usd = rng.lognormvariate(6.4, 1.3)  # 단계당 금액 — 중앙 약 $600
        out.append([round(price, 8), round(usd * unit / price, 6)])
        price = price + step if side == "asks" else price - step
        if price <= 0:
            break
    return out


def _row(
    exchange: str,
    base: str,
    quote: str,
    price: float,
    asks: list[list[float]],
    bids: list[list[float]],
    dep: bool | None = None,
    wd: bool | None = None,
    networks: list[Network] | None = None,
) -> Row:
    return Row(
        exchange=exchange,
        base=base,
        quote=quote,
        native_symbol=f"{base}{quote}",
        price=price,
        asks=asks,
        bids=bids,
        price_timestamp=0,
        deposit_enabled=dep,
        withdrawal_enabled=wd,
        networks=networks if networks is not None else [],
    )


def _stamp(store: LiveStore, rng: random.Random, now: datetime) -> None:
    """다섯 거래소 스트림의 마지막 수신 시각과 틱 시각 — 짝 없는 거래소(mexc)는 스트림 상태가 없다."""
    now_ms = int(now.timestamp() * 1000)
    for ex in DEPTH:
        store.stream(ex).last_message_at = now_ms - rng.randint(50, 900)
    store.mark_received(int(now.timestamp()))


def _add_edges(store: LiveStore, rng: random.Random) -> None:
    """fail(호가 없음·최우선 잔량 0)·얇은 호가(걷기 소진)·300초 멈춘 행·짝 없는 해외 거래소."""
    for row in rng.sample(store.get_all(), 60):
        kind = rng.choice(("empty", "zero", "thin", "old", "thin_sell"))
        asks = [list(level) for level in row.asks]
        bids = [list(level) for level in row.bids]
        if kind == "empty":
            asks = []
        elif kind == "zero":
            bids[0][1] = 0.0
        elif kind == "thin":
            asks = [[asks[0][0], 1e-6]]
            bids = [[bids[0][0], 1e-6]]
        elif kind == "thin_sell":
            bids = [[price, size * 0.01] for price, size in bids[:2]]
        age = 400 if kind == "old" else 1
        store.put_row(
            _row(row.exchange, row.base, row.quote, row.price, asks, bids),
            NOW - timedelta(seconds=age),
        )
    store.put_row(
        _row("mexc", "NOPAIR", "USDT", 1.0, [[1.01, 5.0]], [[0.99, 5.0]]), NOW
    )


def build_world(
    table: Table, seed: int
) -> tuple[LiveStore, dict[tuple[str, str], float], SparkBuffer]:
    """표 한 장 → 저장소(합성 호가·망 목록·스트림·환율)·기준가 장부·spark 버퍼(표의 spark 로 복원해 게시)."""
    rng = random.Random(seed)
    store = LiveStore()
    rows: dict[tuple[str, str], Row] = {}
    day_open: dict[tuple[str, str], float] = {}
    sparks: list[SparkBucketRow] = []
    minute = int(NOW.timestamp()) // 60
    for values in table["rows"]:
        r = dict(zip(FIELDS, values, strict=True))
        sym, dom, fx = r["sym"], r["dom"], r["fx"]
        krw = r["krw"] or 1.0
        usd = r["usd"] or krw / 1361.0
        if (dom, sym) not in rows:
            dom_nets: list[Network] = []
            if r["netDom"] is not None:
                code = sym if rng.random() < 0.6 else r["netDom"].upper()[:10]
                dom_nets.append(
                    Network(code, r["netDom"], bool(r["depDom"]), bool(r["wdDom"]))
                )
            ask = krw * (1 + rng.choice((0.0005, 0.001, 0.002)))
            row = _row(
                dom,
                sym,
                "KRW",
                krw * (1 + rng.uniform(-0.001, 0.001)),
                _levels(ask, DEPTH[dom], "asks", rng, 1361.0),
                _levels(krw, DEPTH[dom], "bids", rng, 1361.0),
                r["depDom"],
                r["wdDom"],
                dom_nets,
            )
            rows[(dom, sym)] = row
            if r["dayChg"] is not None:
                day_open[(dom, sym)] = row.price / (1 + r["dayChg"] / 100)
        if (fx, sym) not in rows:
            fx_nets = [
                Network(code, name, True, rng.random() < 0.9)
                for code, name in rng.sample(OTHER_NETS, rng.randint(0, 2))
            ]
            if r["netFx"] is not None:
                mine = rows[(dom, sym)].networks
                if mine and rng.random() < 0.6:
                    code = mine[0].code
                else:
                    code = r["netFx"].upper()[:10] + "X"
                fx_nets.append(
                    Network(code, r["netFx"], bool(r["depFx"]), bool(r["wdFx"]))
                )
            elif r["depFx"] is not None:
                fx_nets.append(
                    Network(
                        sym + "Z", f"{sym} Other", bool(r["depFx"]), bool(r["wdFx"])
                    )
                )
            bid = usd * (1 - rng.choice((0.0002, 0.0005, 0.001)))
            ask = usd * (1 + rng.choice((0.0002, 0.0005, 0.001)))
            rows[(fx, sym)] = _row(
                fx,
                sym,
                "USDT",
                usd,
                _levels(ask, DEPTH[fx], "asks", rng, 1.0),
                _levels(bid, DEPTH[fx], "bids", rng, 1.0),
                r["depFx"],
                r["wdFx"],
                fx_nets,
            )
        n = len(r["spark"])
        sparks.extend(
            SparkBucketRow(dom, fx, sym, (minute - n + 1 + i) * 60, v)
            for i, v in enumerate(r["spark"])
        )
    for row in rows.values():
        # 대부분 최근 갱신, 약 0.5% 는 300초 넘게 멈춘 행(거래 정지)
        age = rng.uniform(0, 10) if rng.random() > 0.005 else rng.uniform(301, 900)
        store.put_row(row, NOW - timedelta(seconds=age))
    _add_edges(store, rng)
    _stamp(store, rng, NOW)
    for ex, (ask, bid) in RATES.items():
        store.set_rate(ex, ask, bid, NOW - timedelta(seconds=1))
    buffer = SparkBuffer()
    buffer.seed(sparks)
    buffer.publish(store)
    return store, day_open, buffer


def _next_second(store: LiveStore, rng: random.Random, now: datetime) -> None:
    """다음 초 흉내 — 행 절반이 새 메시지로 교체되고, 입출금 재조회처럼 일부 행의 망 목록·코인 값이 바뀐다."""
    for row in store.get_all():
        if rng.random() < 0.5:
            f = 1 + rng.uniform(-0.001, 0.001)
            store.put_row(
                _row(
                    row.exchange,
                    row.base,
                    row.quote,
                    row.price * f,
                    [[p * f, s * rng.uniform(0.7, 1.3)] for p, s in row.asks],
                    [[p * f, s * rng.uniform(0.7, 1.3)] for p, s in row.bids],
                ),
                now,
            )
    for row in rng.sample(store.get_all(), 40):
        if row.networks and rng.random() < 0.5:
            row.networks = [
                Network(n.code, n.name, n.dep, not n.wd) for n in row.networks
            ]
        else:
            row.deposit_enabled = rng.choice((True, False, None))
    _stamp(store, rng, now)


# ---- 기준 구현 ----


def reference_tick_rows(store: LiveStore) -> list[TickRow]:
    """001 §3.6-2 를 조합마다 그대로 — 여섯 값을 모아 검사하고, 망 판정은 행마다 공유 함수로."""
    rates = {ex: r for ex, r in store.rates().items() if r.ask > 0 and r.bid > 0}
    domestic: dict[str, dict[str, Row]] = {}
    foreign: dict[str, dict[str, Row]] = {}
    for row in store.get_all():
        if row.quote == "KRW":
            domestic.setdefault(row.exchange, {})[row.base.upper()] = row
        elif row.quote == "USDT":
            foreign.setdefault(row.exchange, {})[row.base.upper()] = row
    out: list[TickRow] = []
    for dom_ex, dom_table in sorted(domestic.items()):
        rate = rates.get(dom_ex)
        if rate is None:
            continue
        for fx_ex, fx_table in sorted(foreign.items()):
            if fx_ex == dom_ex:
                continue
            for base in sorted(dom_table.keys() & fx_table.keys()):
                dom_row, fx_row = dom_table[base], fx_table[base]
                if not (dom_row.bids and dom_row.asks and fx_row.bids and fx_row.asks):
                    continue
                dom_bid, dom_ask = dom_row.bids[0][0], dom_row.asks[0][0]
                fx_bid, fx_ask = fx_row.bids[0][0], fx_row.asks[0][0]
                six = (dom_bid, dom_ask, fx_bid, fx_ask, rate.ask, rate.bid)
                if any(v <= 0 for v in six):
                    continue
                wf = wallet_fields(dom_row, fx_row)
                out.append(
                    TickRow(
                        dom=dom_ex,
                        fx=fx_ex,
                        base=base,
                        fwd=premium_percent(
                            buy_krw=fx_ask * rate.ask, sell_krw=dom_bid
                        ),
                        rev=premium_percent(
                            buy_krw=dom_ask, sell_krw=fx_bid * rate.bid
                        ),
                        dom_price=dom_row.price,
                        fx_price=fx_row.price,
                        rate=(rate.ask + rate.bid) / 2,
                        dom_dep=wf.dep_dom,
                        dom_wd=wf.wd_dom,
                        fx_dep=wf.dep_fx,
                        fx_wd=wf.wd_fx,
                        net_dom=wf.net_dom,
                        net_fx=wf.net_fx,
                    )
                )
    return out


def _age(row: Row, store: LiveStore, now: datetime) -> float:
    state = store.stream_state(row.exchange)
    assert state is not None and state.last_message_at is not None
    assert row.updated_at is not None
    stream_age = (now.timestamp() * 1000 - state.last_message_at) / 1000
    row_age = (now - row.updated_at).total_seconds()
    return max(stream_age, row_age) if row_age >= 300.0 else stream_age


def _cross(
    buy_levels: list[list[float]], sell_levels: list[list[float]], amount: float
) -> tuple[float, float]:
    buy = walk_amount(buy_levels, amount)
    sell = walk_quantity(sell_levels, buy.quantity)
    if sell.exhausted and sell.quantity < buy.quantity:
        buy = walk_quantity(buy_levels, sell.quantity)
    return average_price(buy), average_price(sell)


def _reference_row(
    base: str,
    dom_row: Row,
    fx_row: Row,
    rate_ask: float,
    rate_bid: float,
    store: LiveStore,
    now: datetime,
    day_open: dict[tuple[str, str], float],
) -> dict[str, object]:
    best = (
        dom_row.bids[0] if dom_row.bids else None,
        dom_row.asks[0] if dom_row.asks else None,
        fx_row.bids[0] if fx_row.bids else None,
        fx_row.asks[0] if fx_row.asks else None,
    )
    age = max(0.0, _age(dom_row, store, now), _age(fx_row, store, now))
    dom_bid, dom_ask, fx_bid, fx_ask = best
    if (
        dom_bid is None
        or dom_ask is None
        or fx_bid is None
        or fx_ask is None
        or any(
            level[0] <= 0 or level[1] <= 0
            for level in (dom_bid, dom_ask, fx_bid, fx_ask)
        )
    ):
        fwd = rev = usd = krw = slip_fwd = slip_rev = 0.0
        status = "fail"
    else:
        fwd_raw = premium_percent(buy_krw=fx_ask[0] * rate_ask, sell_krw=dom_bid[0])
        rev_raw = premium_percent(buy_krw=dom_ask[0], sell_krw=fx_bid[0] * rate_bid)
        fx_ask_avg, dom_bid_avg = _cross(
            walk_levels(fx_row, "asks"), walk_levels(dom_row, "bids"), 1000.0
        )
        dom_ask_avg, fx_bid_avg = _cross(
            walk_levels(dom_row, "asks"), walk_levels(fx_row, "bids"), 1000.0 * rate_ask
        )
        fwd = premium_percent(buy_krw=fx_ask_avg * rate_ask, sell_krw=dom_bid_avg)
        rev = premium_percent(buy_krw=dom_ask_avg, sell_krw=fx_bid_avg * rate_bid)
        slip_fwd = max(0.0, fwd_raw - fwd)
        slip_rev = max(0.0, rev_raw - rev)
        krw = dom_bid[0]
        usd = fx_row.price
        status = "stale" if age >= 5.0 else "ok"
    wf = wallet_fields(dom_row, fx_row)
    ref = day_open.get((dom_row.exchange, base))
    day_chg = None
    if ref is not None and ref > 0 and dom_row.price > 0:
        day_chg = (dom_row.price / ref - 1) * 100
    return {
        "sym": base,
        "dom": dom_row.exchange,
        "fx": fx_row.exchange,
        "fwd": float(fwd),
        "rev": float(rev),
        "usd": float(usd),
        "spark": store.spark(dom_row.exchange, fx_row.exchange, base),
        "status": status,
        "age": float(age),
        "slipFwd": float(slip_fwd),
        "slipRev": float(slip_rev),
        "krw": float(krw),
        "netDom": wf.net_dom,
        "depDom": wf.dep_dom,
        "wdDom": wf.wd_dom,
        "depFx": wf.dep_fx,
        "wdFx": wf.wd_fx,
        "netFx": wf.net_fx,
        "dayChg": day_chg,
    }


def reference_rows(
    store: LiveStore,
    *,
    now: datetime,
    day_open: dict[tuple[str, str], float],
    excluded: set[str],
) -> list[dict[str, object]]:
    """003 §3.2-4 행 하나의 규칙을 행마다 그대로 — 거래소·시장 값도 행마다 다시 구하고 끝에 정렬한다."""
    domestic: dict[str, dict[str, Row]] = {}
    foreign: dict[str, dict[str, Row]] = {}
    for row in store.get_all():
        if row.quote == "KRW":
            domestic.setdefault(row.exchange, {})[row.base.upper()] = row
        elif row.quote == "USDT":
            foreign.setdefault(row.exchange, {})[row.base.upper()] = row
    out: list[dict[str, object]] = []
    for dom_ex, dom_table in domestic.items():
        rate = store.get_rate(dom_ex)
        if rate is None or rate.ask <= 0 or rate.bid <= 0:
            continue
        for fx_ex, fx_table in foreign.items():
            if fx_ex == dom_ex:
                continue
            for base in dom_table.keys() & fx_table.keys():
                if base in excluded:
                    continue
                out.append(
                    _reference_row(
                        base,
                        dom_table[base],
                        fx_table[base],
                        rate.ask,
                        rate.bid,
                        store,
                        now,
                        day_open,
                    )
                )
    out.sort(key=lambda r: (str(r["sym"]), str(r["dom"]), str(r["fx"])))
    return out


# ---- 시험 ----


@pytest.mark.parametrize("index", [0, 1, 2])
def test_table_bytes_match_the_row_by_row_reference(
    tables: list[Table], index: int
) -> None:
    """003 §3.2 표 계산 경로 — 거래소·시장 값을 한 번씩만 구하고 정렬 없이 만들어도 결과 바이트가 같다."""
    store, day_open, _ = build_world(tables[index], seed=index)
    rng = random.Random(100 + index)
    memo = WalletMemo()
    now = NOW
    for second in range(3):
        build_tick(
            store, int(now.timestamp()), [], memo
        )  # 틱이 회차를 열고 망 판정 메모를 채운다
        excluded = {"BTC", "eth"} if second == 2 else set()
        table = build_table(
            store, now=now, day_open=day_open, excluded=excluded, wallet_memo=memo
        )
        expected = dict(
            table,
            rows=reference_rows(
                store,
                now=now,
                day_open=day_open,
                excluded={c.upper() for c in excluded},
            ),
        )
        assert encode_table(table) == encode_table(expected)
        # 메모 없이 행마다 판정해도 같다
        plain = build_table(store, now=now, day_open=day_open, excluded=excluded)
        assert encode_table({"rows": plain["rows"]}) == encode_table(
            {"rows": table["rows"]}
        )
        # 빈 표로 통과하는 시험이 아니다 — fail·stale 행과 전 조합이 들어 있다
        rows = table["rows"]
        assert isinstance(rows, list)
        statuses = {row["status"] for row in rows}
        assert {"ok", "fail"} <= statuses and len(rows) > 1_400
        if second == 0:
            assert "stale" in statuses
        now += timedelta(seconds=1)
        _next_second(store, rng, now)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_tick_rows_match_the_reference_to_the_bit(
    tables: list[Table], index: int
) -> None:
    """001 §3.6-2 한 번 훑기 — 행 순서와 14필드가 비트까지 같다(망 판정 메모가 있어도 없어도)."""
    store, _, _ = build_world(tables[index], seed=10 + index)
    rng = random.Random(200 + index)
    memo = WalletMemo()
    now = NOW
    for _ in range(3):
        ts = int(now.timestamp())
        expected = [tuple(map(repr, row)) for row in reference_tick_rows(store)]
        assert len(expected) > 1_300
        for tick in (
            build_tick(store, ts, ["upbit"], memo),
            build_tick(store, ts, ["upbit"]),
        ):
            assert [tuple(map(repr, row)) for row in tick.rows] == expected
            assert tick.ts == ts and tick.dw_failed == ("upbit",)
        now += timedelta(seconds=1)
        _next_second(store, rng, now)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_spark_fragments_encode_the_same_bytes_as_encode_table(
    tables: list[Table], index: int
) -> None:
    """017 §3.1·009 §3.6 — 조각을 끼운 표 JSON 이 `encode_table` 과 같다(분이 바뀌는 틱 포함)."""
    store, day_open, buffer = build_world(tables[index], seed=20 + index)
    rng = random.Random(300 + index)
    relay = TickRelay(stream=None, store=store, spark=buffer)
    now = NOW
    for step in range(4):
        payload = build_table(store, now=now, day_open=day_open)
        rows = payload["rows"]
        assert isinstance(rows, list)
        sparks = [row["spark"] for row in rows]
        encoded = encode_table_with_spark_json(payload, store.spark_json())
        assert encoded == encode_table(payload)
        # 인코딩 뒤 payload 는 그대로다 — 같은 목록 객체
        assert all(
            row["spark"] is spark for row, spark in zip(rows, sparks, strict=True)
        )
        if step == 0:
            assert (
                "-0.0," in encoded or "-0.0]" in encoded
            )  # 실데이터의 −0.0 이 그대로 나간다
        now += timedelta(seconds=1)
        _next_second(store, rng, now)
        relay(build_tick(store, int(now.timestamp()), []))
