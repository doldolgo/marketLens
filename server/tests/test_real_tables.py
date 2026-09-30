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
